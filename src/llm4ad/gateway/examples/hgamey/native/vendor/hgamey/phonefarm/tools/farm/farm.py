#!/usr/bin/env python3
"""farm: 设备健康读取与任务文件协议。

  farm health <serial>              在线 + 温度
  farm task new|msg|note|result|check|handoff|ls …
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import shutil
import sys
from pathlib import Path

EXIT_OFFLINE = 10
EXIT_HOT = 11
HOT_C_DEFAULT = 70.0  # 用户定的: 只有离线或 >70°C 才中止


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def safe_name(serial: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", serial)


def write_new(path: Path, text: str) -> None:
    """只新建: 已有同名文件先改名留底 (<名>.prev-<时间>), 绝不覆盖。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path.rename(path.with_name(f"{path.name}.prev-{stamp}"))
    path.write_text(text)


def append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(text)


def real_tool(tool: str) -> str:
    path = os.environ.get(f"FARM_REAL_{tool.upper()}") or shutil.which(tool)
    if path:
        return path
    raise SystemExit(f"farm: 找不到 {tool} (设 FARM_REAL_{tool.upper()})")


def tool_of(serial: str) -> tuple[str, str]:
    """serial 写成 hdc:<key> 表示鸿蒙设备, 否则 adb。"""
    return ("hdc", serial[4:]) if serial.startswith("hdc:") else ("adb", serial)


def dev_shell(serial: str, cmd: str, timeout: int = 60) -> tuple[int, str]:
    tool, key = tool_of(serial)
    flag = "-t" if tool == "hdc" else "-s"
    try:
        p = subprocess.run([real_tool(tool), flag, key, "shell", cmd], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, ""
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def health(serial: str, hot_c: float) -> int:
    tool, key = tool_of(serial)
    if tool == "adb":
        p = subprocess.run([real_tool("adb"), "-s", key, "get-state"], capture_output=True, text=True)
        online = p.returncode == 0 and p.stdout.strip() == "device"
    else:
        p = subprocess.run([real_tool("hdc"), "list", "targets"], capture_output=True, text=True)
        online = key in p.stdout.split()
    temp = None
    if online:
        cmd = "dumpsys battery" if tool == "adb" else "hidumper -s BatteryService -a -i"
        rc, out = dev_shell(serial, cmd)
        m = re.search(r"temperature\s*[:=]\s*(-?\d+)", out, re.I)
        if m:
            temp = int(m[1]) / 10.0  # 两边都是 0.1°C 单位 (鸿蒙这一侧未在真机核实)
    print(json.dumps({"serial": serial, "online": online, "battery_c": temp, "hot_c": hot_c}, ensure_ascii=False))
    if not online:
        return EXIT_OFFLINE
    if temp is not None and temp > hot_c:
        return EXIT_HOT
    return 0


# ---------------------------------------------------------------- 任务文件协议

ROLES = ("runner", "research", "impl", "review")
STATUSES = ("done", "partial", "failed", "aborted")


def task_new(d: Path, tid: str, role: str, device: str | None, repo: str | None, branch: str | None) -> int:
    if role not in ROLES:
        raise SystemExit(f"role 只能是 {ROLES}")
    if role == "runner" and not device:
        raise SystemExit("runner 任务必须带 --device")
    if role != "runner" and device:
        raise SystemExit("只有 runner 任务能带设备")
    d.mkdir(parents=True, exist_ok=True)
    task = {"id": tid, "role": role, "device": device, "repo": repo, "branch": branch, "created": now_iso()}
    write_new(d / "TASK.json", json.dumps(task, ensure_ascii=False, indent=1))
    for name in ("BRIEF.md", "MESSAGES.md", "PROGRESS.md"):
        if not (d / name).exists():
            (d / name).write_text("")
    print(d)
    return 0


def task_append(d: Path, name: str, text: str) -> int:
    append(d / name, f"--- {now_iso()}\n{text.rstrip()}\n")
    return 0


def task_result(d: Path, status: str, summary: str, commits: list[str], tests: list[str], open_items: list[str]) -> int:
    if status not in STATUSES:
        raise SystemExit(f"status 只能是 {STATUSES}")
    cs = []
    for c in commits:
        parts = c.split(":")
        if len(parts) != 3:
            raise SystemExit(f"--commit 写成 <仓库路径>:<分支>:<提交号>: {c}")
        cs.append({"repo": parts[0], "branch": parts[1], "sha": parts[2]})
    ts = []
    for t in tests:
        m = re.match(r"^(.*)\|(\d+)\|(\d+)$", t)
        if not m:
            raise SystemExit(f"--tests 写成 <命令>|<通过数>|<失败数>: {t}")
        ts.append({"cmd": m[1], "passed": int(m[2]), "failed": int(m[3])})
    res = {"status": status, "summary": summary, "commits": cs, "tests": ts, "open": open_items, "at": now_iso()}
    write_new(d / "RESULT.json", json.dumps(res, ensure_ascii=False, indent=1))
    return 0


def task_problems(d: Path) -> list[str]:
    """主控收到完成通知后跑一次: 有问题就打回, 不靠读 agent 的自述。"""
    probs = []
    try:
        task = json.loads((d / "TASK.json").read_text())
    except (OSError, ValueError) as e:
        return [f"TASK.json 读不了: {e}"]
    rp = d / "REPORT.md"
    if not rp.exists() or not rp.read_text().strip():
        probs.append("REPORT.md 缺失或为空")
    try:
        res = json.loads((d / "RESULT.json").read_text())
    except (OSError, ValueError) as e:
        return probs + [f"RESULT.json 读不了: {e}"]
    if res.get("status") not in STATUSES:
        probs.append(f"RESULT.status 非法: {res.get('status')}")
    for t in res.get("tests", []):
        if t.get("failed", 0) > 0 and res.get("status") == "done":
            probs.append(f"测试有失败却标 done: {t.get('cmd')}")
    for c in res.get("commits", []):
        r = subprocess.run(["git", "-C", c["repo"], "cat-file", "-e", f"{c['sha']}^{{commit}}"], capture_output=True)
        if r.returncode != 0:
            probs.append(f"提交不存在: {c['repo']} {c['sha']}")
            continue
        r = subprocess.run(["git", "-C", c["repo"], "merge-base", "--is-ancestor", c["sha"], c["branch"]], capture_output=True)
        if r.returncode != 0:
            probs.append(f"提交 {c['sha']} 不在分支 {c['branch']} 上")
        r = subprocess.run(["git", "-C", c["repo"], "branch", "-r", "--contains", c["sha"]], capture_output=True, text=True)
        if r.stdout.strip() and task.get("role") != "runner":
            pass  # 已在远端只说明它是旧提交; push 与否由 git 钩子/权限管, 这里不判
    if task.get("role") == "runner":
        dev = task.get("device") or ""
        rf = d / f"restore-{safe_name(dev)}.json"
        if not rf.exists():
            probs.append(f"runner 任务缺 {rf.name} (缺恢复报告)")
        else:
            rr = json.loads(rf.read_text())
            if not rr.get("ok"):
                n = len(rr.get("still_different", [])) + len(rr.get("unresolved", [])) + len(rr.get("restore_failed", []))
                probs.append(f"设备现场没恢复干净: {n} 项, 见 {rf.name}")
    return probs


def task_check(d: Path) -> int:
    probs = task_problems(d)
    print(json.dumps({"task": str(d), "ok": not probs, "problems": probs}, ensure_ascii=False, indent=1))
    return 0 if not probs else 1


def task_handoff(d: Path, tail: int) -> int:
    """重启一个 agent 时喂给它的上下文: 原始 brief + 主控的全部消息 + 最近几条进度。"""
    rd = lambda n: (d / n).read_text() if (d / n).exists() else ""
    notes = [b for b in rd("PROGRESS.md").split("--- ") if b.strip()]
    out = ["# ORIGINAL BRIEF\n", rd("BRIEF.md").strip(), "\n\n# TASK\n", rd("TASK.json").strip(),
           "\n\n# ALL MESSAGES FROM COORDINATOR (chronological)\n", rd("MESSAGES.md").strip(),
           f"\n\n# LAST {tail} PROGRESS NOTES\n", "".join("--- " + b for b in notes[-tail:]).strip()]
    print("".join(out))
    return 0


def task_ls(root: Path) -> int:
    rows = []
    for t in sorted(root.glob("*/TASK.json")) + sorted(root.glob("*/*/TASK.json")):
        d = t.parent
        try:
            task = json.loads(t.read_text())
            res = json.loads((d / "RESULT.json").read_text()) if (d / "RESULT.json").exists() else {}
        except ValueError:
            continue
        rows.append(f"{task.get('id', '?'):24} {task.get('role', '?'):8} {task.get('device') or '-':20} {res.get('status', 'running'):8} {d}")
    print("\n".join(rows))
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="farm")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("health")
    p.add_argument("serial")
    p.add_argument("--hot-c", type=float, default=HOT_C_DEFAULT)
    p = sp.add_parser("task")
    tp = p.add_subparsers(dest="op", required=True)
    q = tp.add_parser("new"); q.add_argument("dir"); q.add_argument("--id", required=True); q.add_argument("--role", required=True)
    q.add_argument("--device"); q.add_argument("--repo"); q.add_argument("--branch")
    for n in ("msg", "note"):
        q = tp.add_parser(n); q.add_argument("dir"); q.add_argument("text", nargs="?")
    q = tp.add_parser("result"); q.add_argument("dir"); q.add_argument("--status", required=True); q.add_argument("--summary", default="")
    q.add_argument("--commit", action="append", default=[]); q.add_argument("--tests", action="append", default=[]); q.add_argument("--open", action="append", default=[])
    q = tp.add_parser("check"); q.add_argument("dir")
    q = tp.add_parser("handoff"); q.add_argument("dir"); q.add_argument("--tail", type=int, default=5)
    q = tp.add_parser("ls"); q.add_argument("root")
    a = ap.parse_args(argv)
    if a.cmd == "health":
        return health(a.serial, a.hot_c)
    if a.cmd == "task":
        d = Path(a.dir) if a.op != "ls" else Path(a.root)
        if a.op == "new":
            return task_new(d, a.id, a.role, a.device, a.repo, a.branch)
        if a.op in ("msg", "note"):
            text = a.text if a.text is not None else sys.stdin.read()
            return task_append(d, "MESSAGES.md" if a.op == "msg" else "PROGRESS.md", text)
        if a.op == "result":
            return task_result(d, a.status, a.summary, a.commit, a.tests, a.open)
        if a.op == "check":
            return task_check(d)
        if a.op == "handoff":
            return task_handoff(d, a.tail)
        return task_ls(d)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
