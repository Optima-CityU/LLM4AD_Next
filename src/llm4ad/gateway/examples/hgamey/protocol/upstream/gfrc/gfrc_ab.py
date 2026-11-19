#!/usr/bin/env python3
"""gfrc_ab.py — 整机插帧+超分 (高通 GPP G-FRC+) 开/关的同局 A/B。不改游戏任何设置。

    python3 -I gfrc/gfrc_ab.py --serial <S> [--pkg com.miHoYo.Yuanshen] [--rounds 6] --out <目录>

做什么:
  1. 记下 settings global game_gfrc_mode 原值; 无论怎么退出都写回原值并回读核对。
  2. 冷启动游戏、进大世界 (phonefarm onboard --enter-only), 之后全程不碰触屏, 镜头不动。
  3. rounds 对 ABBA 交替 (关/开/开/关 ...)。每臂: 写本游戏的 GFRC 位 (关=000, 开=--on 位),
     等 --settle 秒, 核对 vendor.gpp.gfrc.* 属性确实跟着变了 (没变 = 这臂作废, 如实记下),
     然后同一个 --window 秒窗口里并行采:
       - SurfaceFlinger timestats: 游戏图层实际上屏帧数 → 显示帧率; 屏幕刷新率; 丢帧
       - ftrace (loop_v1/tools/ftrace_capture.sh): 游戏线程自己的 GPU 提交 → 游戏渲染帧率/帧时
     窗口后: 游戏图层的 HWC 源尺寸 (超分输出分辨率), 一张截图 (锐度: 全画面与固定区域的拉普拉斯方差)。
  4. 每个指标按「对」做配对差, 精确符号翻转检验 (2^n 种翻转全列举) 给双侧 p 与 95% 区间 (bootstrap)。
     「画面不变」在这里的含义: 游戏自己的渲染帧率、渲染分辨率不变 (游戏没被改), 变的只是整机后处理。

产物 (--out 下): arms.jsonl (每臂原始数), report.json, report.md, shots/*.png, 原值与还原核对。
"""
import argparse, itertools, json, os, random, re, shlex, signal, statistics, subprocess, sys, time

# phonefarm 树：作为平台的实验任务运行时由节点给 PF_ROOT；在 phonefarm/loop_v1 老位置直接跑时回退到上两级。
REPO = os.environ.get("PF_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY = "game_gfrc_mode"
DEV_TRACE = "/data/local/tmp/gfrc_ftrace_capture.sh"


def lock_fds():
    """Keep the enclosing phonefarm lease alive in task children."""
    return tuple(int(fd) for fd in os.environ.get("PF_LOCK_FDS", "").split(",") if fd)


def sh(cmd, timeout=120, check=False):
    """Bound the entire owned transport tree without claiming remote cleanup."""
    process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, pass_fds=lock_fds(), start_new_session=os.name == "posix")
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        # Killing only the shell leaves ADB/proxy children holding the device lease.
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.communicate(timeout=2)
        raise
    if check and process.returncode != 0:
        raise RuntimeError(f"Command failed ({process.returncode}): {cmd}\n{stderr.strip()}")
    return stdout


class Dev:
    def __init__(self, serial):
        self.s = serial

    def adb(self, args, timeout=120, check=False):
        return sh(f"adb -s {shlex.quote(self.s)} {args}", timeout, check)

    def shell(self, cmd, timeout=120):
        return self.adb(f"shell {shlex.quote(cmd)}", timeout)

    def su(self, cmd, timeout=120):
        return self.adb(f"shell su -c {shlex.quote(shlex.quote(cmd))}", timeout)

    def prop(self, name):
        return self.shell(f"getprop {name}").strip()


def set_pkg_bits(value, pkg, bits):
    """game_gfrc_mode 形如 'a+000,b+111'; 改 pkg 那一项, 没有就追加。"""
    items = [x for x in value.split(",") if x]
    out, hit = [], False
    for it in items:
        name, _, _ = it.partition("+")
        if name == pkg:
            out.append(f"{pkg}+{bits}"); hit = True
        else:
            out.append(it)
    if not hit:
        out.append(f"{pkg}+{bits}")
    return ",".join(out)


def parse_timestats(text, pkg):
    """取游戏图层 (SurfaceView[pkg/...](BLAST)) 的 totalFrames, 以及全局 displayOnTime/missedFrames。"""
    g = {}
    for k in ("missedFrames", "displayOnTime"):
        m = re.search(rf"^{k} = (\d+)", text, re.M)
        g[k] = int(m.group(1)) if m else None
    rr = re.search(r"^displayRefreshRate = (\d+)", text, re.M)
    g["display_hz"] = int(rr.group(1)) if rr else None
    layer = None
    for blk in text.split("layerName = ")[1:]:
        name = blk.splitlines()[0]
        if f"SurfaceView[{pkg}/" in name and "(BLAST)" in name and "selfsr_interp" not in name:   # 改了名的插帧子图层也带这个前缀
            m = re.search(r"^totalFrames = (\d+)", blk, re.M)
            layer = (name, int(m.group(1)) if m else None)
    g["layer"] = layer[0] if layer else None
    g["layer_frames"] = layer[1] if layer else None
    return g


def layer_src(sf_dump, pkg):
    """HWC 层表里游戏 BLAST 层的 Source Crop 宽高 (横屏: 取大者为宽)。"""
    lines = sf_dump.splitlines()
    for i, l in enumerate(lines):
        if "SurfaceView[" in l and "(BLAST)" in l and pkg[:12] in l and "selfsr_interp" not in l:
            for nxt in lines[i + 1:i + 3]:
                cells = [c.strip() for c in nxt.split("|")]
                # Source Crop: 4 个带小数点的数 (荣耀的表多一列 [*], 不能按固定列号取)
                for c in cells[5:]:
                    toks = c.split()
                    if len(toks) == 4 and all(re.fullmatch(r"-?\d+\.\d+", t) for t in toks):
                        nums = [float(t) for t in toks]
                        w, h = nums[2] - nums[0], nums[3] - nums[1]
                        return [int(max(w, h)), int(min(w, h))]
    return None


def sharpness(png, box=None):
    from PIL import Image
    import numpy as np
    im = Image.open(png).convert("L")
    if box:
        im = im.crop(box)
    g = np.asarray(im, dtype=float)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def side_by_side(out, valid, box=(1180, 560, 1480, 1060)):
    """第一对完整的关/开截图, 取角色区域 1:1 放大 2 倍, 左关右开拼一张。"""
    from PIL import Image
    first = min((r["pair"] for r in valid), default=None)
    pics = {r["state"]: os.path.join(out, "shots", f"{r['arm']}.png") for r in valid if r["pair"] == first}
    if len(pics) != 2:
        return None
    w, h = (box[2] - box[0]) * 2, (box[3] - box[1]) * 2
    canvas = Image.new("RGB", (w * 2 + 20, h), "white")
    for i, st in enumerate(("off", "on")):
        canvas.paste(Image.open(pics[st]).convert("RGB").crop(box).resize((w, h), Image.NEAREST), (i * (w + 20), 0))
    path = os.path.join(out, "side_by_side.png")
    canvas.save(path)
    return path


def signflip(diffs):
    """配对差的精确符号翻转检验, 双侧。"""
    n = len(diffs)
    if n == 0:
        return None
    obs = abs(statistics.mean(diffs))
    hits = total = 0
    for signs in itertools.product((1, -1), repeat=n):
        total += 1
        if abs(statistics.mean(d * s for d, s in zip(diffs, signs))) >= obs - 1e-12:
            hits += 1
    return hits / total


def boot_ci(diffs, reps=4000, seed=7):
    if len(diffs) < 2:
        return None
    rnd = random.Random(seed)
    ms = sorted(statistics.mean(rnd.choice(diffs) for _ in diffs) for _ in range(reps))
    return [ms[int(0.025 * reps)], ms[int(0.975 * reps) - 1]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--pkg", default="com.miHoYo.Yuanshen")
    ap.add_argument("--on", default="111", help="开臂写的三位: 画质档/帧率档/总开关 (游戏助手的编码)")
    ap.add_argument("--rounds", type=int, default=6, help="配对数 (每对一关一开)")
    ap.add_argument("--settle", type=float, default=8)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-launch", action="store_true", help="游戏已在大世界, 不重新进")
    ap.add_argument("--phonefarm", default=os.environ.get("PHONEFARM_BIN") or os.path.join(REPO, "phonefarm"))
    a = ap.parse_args()

    out = os.path.abspath(a.out)
    os.makedirs(os.path.join(out, "shots"), exist_ok=True)
    dev = Dev(a.serial)
    log = open(os.path.join(out, "run.log"), "a")

    def note(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True); log.write(line + "\n"); log.flush()

    orig = dev.shell(f"settings get global {KEY}").strip()
    if orig in ("", "null"):
        orig = None
    open(os.path.join(out, "orig_game_gfrc_mode.txt"), "w").write(f"{orig}\n")
    orig_timeout = dev.shell("settings get system screen_off_timeout").strip()

    def put_mode(bits):
        dev.shell(f"settings put global {KEY} {shlex.quote(set_pkg_bits(orig or '', a.pkg, bits))}")

    def restore(*_):
        if orig is None:
            dev.shell(f"settings delete global {KEY}")
        else:
            dev.shell(f"settings put global {KEY} {shlex.quote(orig)}")
        if orig_timeout and orig_timeout != "null":
            dev.shell(f"settings put system screen_off_timeout {orig_timeout}")
        now = dev.shell(f"settings get global {KEY}").strip()
        ok = (now == orig) or (orig is None and now in ("", "null"))
        rec = {"restored": ok, "want": orig, "got": now}
        json.dump(rec, open(os.path.join(out, "restore.json"), "w"), ensure_ascii=False, indent=1)
        note(f"还原 {KEY}: {'OK' if ok else '失败'} ({now})")
        return ok

    def on_signal(sig, _f):
        restore(); sys.exit(128 + sig)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    arms = []
    try:
        dev.shell("settings put system screen_off_timeout 1800000")
        dev.adb(f"push {shlex.quote(os.path.join(REPO, 'loop_v1/tools/ftrace_capture.sh'))} {DEV_TRACE}", check=True)
        put_mode("000")
        if not a.no_launch:
            note("冷启动并进入大世界 (GFRC 关)")
            dev.shell(f"am force-stop {a.pkg}")
            r = subprocess.run([a.phonefarm, "onboard", "--package", a.pkg, "--serial", a.serial, "--enter-only",
                                "--no-guest", "--out", os.path.join(out, "onboard")],
                               capture_output=True, text=True, timeout=900, pass_fds=lock_fds())
            if r.returncode != 0:
                raise RuntimeError(f"进大世界失败: {r.stderr[-400:]}")
        order = []
        for i in range(a.rounds):
            order += [("off", i), ("on", i)] if i % 2 == 0 else [("on", i), ("off", i)]
        for k, (state, pair) in enumerate(order):
            name = f"{state}{pair + 1}"
            dev.shell("input keyevent KEYCODE_WAKEUP")
            put_mode(a.on if state == "on" else "000")
            time.sleep(a.settle)
            fg = dev.shell("dumpsys window | grep mCurrentFocus")
            props = {p: dev.prop(f"vendor.gpp.{p}") for p in ("gfrc.interp.rate", "gfrc.upscale.ratio", "frc.enable")}
            # 三位: 画质档 (→ 超分 upscale.ratio) / 帧率档 (→ 插帧 interp.rate) / 总开关; 两个属性分别核对
            bits = a.on if state == "on" else "000"
            want_up = bits[2] == "1" and bits[0] != "0"
            want_interp = bits[2] == "1" and bits[1] != "0"
            applied = ((props["gfrc.upscale.ratio"] not in ("", "0")) == want_up
                       and (props["gfrc.interp.rate"] not in ("", "0")) == want_interp)
            dev_trace = f"/data/local/tmp/gfrc_trace_{name}.txt"
            ts_cmd = ("dumpsys SurfaceFlinger --timestats -disable -clear >/dev/null; "
                      "dumpsys SurfaceFlinger --timestats -enable >/dev/null; "
                      f"sleep {a.window}; dumpsys SurfaceFlinger --timestats -dump -maxlayers 8; "
                      "dumpsys SurfaceFlinger --timestats -disable >/dev/null")
            tr = subprocess.Popen(f"adb -s {shlex.quote(a.serial)} shell su -c "
                                  + shlex.quote(shlex.quote(f"sh {DEV_TRACE} {a.window} {dev_trace} {a.pkg}")),
                                  shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=lock_fds())
            ts = parse_timestats(dev.shell(ts_cmd, timeout=a.window + 60), a.pkg)
            tr.wait(timeout=a.window + 90)
            local_trace = os.path.join(out, f"trace_{name}.txt")
            dev.adb(f"exec-out su -c {shlex.quote('cat ' + dev_trace)} > {shlex.quote(local_trace)}")
            dev.su(f"rm -f {dev_trace}")
            pt = subprocess.run([a.phonefarm, "parse-trace", local_trace], capture_output=True, text=True, pass_fds=lock_fds())
            try:
                game = json.loads(pt.stdout)
            except ValueError:
                game = {}
            src = layer_src(dev.shell("dumpsys SurfaceFlinger"), a.pkg)
            shot = os.path.join(out, "shots", f"{name}.png")
            dev.adb(f"exec-out screencap -p > {shlex.quote(shot)}")
            span = (ts.get("displayOnTime") or a.window * 1000) / 1000
            rec = {"arm": name, "state": state, "pair": pair + 1, "seq": k + 1, "applied": applied,
                   "in_game": a.pkg in fg, "props": props,
                   "display_fps": round(ts["layer_frames"] / span, 2) if ts.get("layer_frames") else None,
                   "display_hz": ts.get("display_hz"), "missed_frames": ts.get("missedFrames"),
                   "game_fps": game.get("fps_mean"), "game_frame_p95_ms": game.get("frame_p95"),
                   "out_res": src, "sharp_full": sharpness(shot), "t": time.time()}
            arms.append(rec)
            open(os.path.join(out, "arms.jsonl"), "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
            note(f"{name}: applied={applied} 显示 {rec['display_fps']} fps @{rec['display_hz']}Hz "
                 f"游戏 {rec['game_fps']} fps p95 {rec['game_frame_p95_ms']} ms 输出 {src} 锐度 {rec['sharp_full']:.1f}")
    finally:
        restored = restore()

    # ── 统计 ──
    valid = [r for r in arms if r["applied"] and r["in_game"]]
    pairs = sorted({r["pair"] for r in valid if sum(1 for x in valid if x["pair"] == r["pair"]) == 2})
    metrics = ["display_fps", "game_fps", "game_frame_p95_ms", "sharp_full", "missed_frames"]
    res = {}
    for m in metrics:
        offs = [next(x[m] for x in valid if x["pair"] == p and x["state"] == "off") for p in pairs]
        ons = [next(x[m] for x in valid if x["pair"] == p and x["state"] == "on") for p in pairs]
        if any(v is None for v in offs + ons) or not pairs:
            res[m] = {"status": "not_measured"}
            continue
        d = [b - c for b, c in zip(ons, offs)]
        mo = statistics.mean(offs)
        res[m] = {"off_mean": round(mo, 3), "on_mean": round(statistics.mean(ons), 3),
                  "diff_mean": round(statistics.mean(d), 3),
                  "diff_pct": round(100 * statistics.mean(d) / mo, 2) if mo else None,
                  "ci95": [round(x, 3) for x in boot_ci(d)] if len(d) > 1 else None,
                  "p": signflip(d), "n_pairs": len(d)}
    res_on = sorted({tuple(r["out_res"]) for r in valid if r["state"] == "on" and r["out_res"]})
    res_off = sorted({tuple(r["out_res"]) for r in valid if r["state"] == "off" and r["out_res"]})
    orig_bits = next((it.partition("+")[2] for it in (orig or "").split(",") if it.partition("+")[0] == a.pkg), None)
    report = {"pkg": a.pkg, "on_bits": a.on, "orig_bits": orig_bits, "rounds": a.rounds, "valid_pairs": len(pairs),
              "dropped_arms": [r["arm"] for r in arms if r not in valid],
              "restored": restored, "out_res_off": res_off, "out_res_on": res_on, "metrics": res,
              "min_p_possible": 2 / 2 ** len(pairs) if pairs else None}
    json.dump(report, open(os.path.join(out, "report.json"), "w"), ensure_ascii=False, indent=1)
    names = {"display_fps": "显示帧率 (上屏 fps)", "game_fps": "游戏渲染帧率 (GPU 提交)",
             "game_frame_p95_ms": "游戏渲染 p95 帧时 (ms)", "sharp_full": "画面锐度 (拉普拉斯方差)",
             "missed_frames": "SurfaceFlinger 丢帧数"}
    md = [f"# 整机插帧+超分 A/B: {a.pkg}", "",
          f"同一局、镜头不动, 关/开 ABBA 交替 {len(pairs)} 对 (精确符号翻转检验, 最小可能 p = {report['min_p_possible']})。"
          f"游戏设置未改; 关/开只改 settings global {KEY} 里本游戏的位 ({'000'} ↔ {a.on})。", "",
          f"- 输出分辨率: 关 {res_off} → 开 {res_on}", f"- 原值还原: {'已核对' if restored else '失败!'} (开跑前本游戏的位: {orig_bits})",
          f"- 作废臂: {report['dropped_arms'] or '无'}", "",
          "| 指标 | 关 | 开 | 差 | 差% | 95% 区间 | p |", "|---|---|---|---|---|---|---|"]
    for m in metrics:
        r = res[m]
        if r.get("status"):
            md.append(f"| {names[m]} | - | - | - | - | - | 未测成 |"); continue
        md.append(f"| {names[m]} | {r['off_mean']} | {r['on_mean']} | {r['diff_mean']:+} | {r['diff_pct']}% | {r['ci95']} | {r['p']:.4f} |")
    side = side_by_side(out, valid)
    if side:
        md += ["", f"第一对的局部 1:1 放大 (左关右开, 角色区域): ![]({os.path.basename(side)})"]
    open(os.path.join(out, "report.md"), "w").write("\n".join(md) + "\n")
    print("\n".join(md))
    return 0 if restored else 2


if __name__ == "__main__":
    sys.exit(main())
