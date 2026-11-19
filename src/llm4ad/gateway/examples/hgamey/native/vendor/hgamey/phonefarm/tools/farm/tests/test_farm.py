"""farm 的离线测试: 假 adb, 不碰任何真设备。

  python3 -m unittest discover -s tools/farm/tests -v      (或 pytest tools/farm/tests)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FARM = HERE.parent / "farm.py"
sys.path.insert(0, str(HERE.parent))
import farm  # noqa: E402

RM = "91253241019A"
HONOR = "AVXB6R5C19000481"


class FarmCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="farmtest-"))
        self.state = self.tmp / "state.json"
        real = self.tmp / "realbin"
        real.mkdir()
        for t in ("adb", "hdc"):
            (real / t).write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fake_adb.py'} \"$@\"\n")
            (real / t).chmod(0o755)
        self.dev = {"settings": {"system:screen_brightness": "120", "global:stay_on_while_plugged_in": "0"},
                    "props": {"debug.knobs.loadop": "0"},
                    "files": {"/sys/class/qcom-battery/charging_enabled": "1"},
                    "dirs": {"/data/app/*/com.miHoYo.Yuanshen*/lib/arm64": ["libil2cpp.so", "libunity.so"]}}
        self.write_state({"online": [RM, HONOR], "dev": {RM: self.dev, HONOR: json.loads(json.dumps(self.dev))}})
        self.env_backup = dict(os.environ)
        os.environ.update({
            "FAKE_STATE": str(self.state),
            "FARM_REAL_ADB": str(real / "adb"),
            "FARM_REAL_HDC": str(real / "hdc"),
        })
        for k in ("FARM_ROLE", "FARM_AGENT", "ANDROID_SERIAL", "DEVLOCK_DIR", "DEVLOCK_LABEL"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 工具
    def write_state(self, s):
        self.state.write_text(json.dumps(s))

    def read_state(self):
        return json.loads(self.state.read_text())

    def run_farm(self, *args, env=None, stdin=None):
        e = dict(os.environ, **(env or {}))
        return subprocess.run([sys.executable, str(FARM), *args], capture_output=True, text=True, env=e, input=stdin)

class HealthTest(FarmCase):
    def test_ok_hot_offline(self):
        self.assertEqual(self.run_farm("health", RM).returncode, 0)
        st = self.read_state(); st["dev"][RM]["temp10"] = 755; self.write_state(st)
        self.assertEqual(self.run_farm("health", RM).returncode, farm.EXIT_HOT)
        st["online"] = [HONOR]; self.write_state(st)
        self.assertEqual(self.run_farm("health", RM).returncode, farm.EXIT_OFFLINE)


class TaskTest(FarmCase):
    def git_repo(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()
        g("init", "-q", "-b", "harness/x")
        (repo / "f").write_text("x")
        g("add", "f")
        g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c")
        return repo, g("rev-parse", "HEAD")

    def test_impl_task_lifecycle(self):
        d = self.tmp / "_tmp" / "N9"
        self.assertEqual(self.run_farm("task", "new", str(d), "--id", "N9", "--role", "impl").returncode, 0)
        self.run_farm("task", "msg", str(d), "先读 BRIEF")
        self.run_farm("task", "note", str(d), "开始改代码")
        r = self.run_farm("task", "check", str(d))
        self.assertEqual(r.returncode, 1)
        self.assertIn("REPORT.md", r.stdout)
        repo, sha = self.git_repo()
        (d / "REPORT.md").write_text("# 报告\n实测: …\n")
        self.run_farm("task", "result", str(d), "--status", "done", "--commit", f"{repo}:harness/x:{sha}", "--tests", "cargo test --release|10|0")
        r = self.run_farm("task", "check", str(d))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.run_farm("task", "result", str(d), "--status", "done", "--commit", f"{repo}:harness/x:deadbeef", "--tests", "x|1|2")
        r = self.run_farm("task", "check", str(d))
        self.assertIn("提交不存在", r.stdout)
        self.assertIn("测试有失败却标 done", r.stdout)
        self.assertTrue(any(p.name.startswith("RESULT.json.prev-") for p in d.iterdir()))
        h = self.run_farm("task", "handoff", str(d)).stdout
        self.assertIn("先读 BRIEF", h)
        self.assertIn("开始改代码", h)
        self.assertIn("N9", self.run_farm("task", "ls", str(self.tmp / "_tmp")).stdout)

    def test_runner_task_requires_clean_restore(self):
        d = self.tmp / "_tmp" / "R1"
        self.run_farm("task", "new", str(d), "--id", "R1", "--role", "runner", "--device", RM)
        (d / "REPORT.md").write_text("报告")
        self.run_farm("task", "result", str(d), "--status", "done")
        self.assertIn("restore-", self.run_farm("task", "check", str(d)).stdout)
        report = d / f"restore-{farm.safe_name(RM)}.json"
        report.write_text(json.dumps({"ok": False, "unresolved": ["charging"]}))
        self.assertIn("没恢复干净", self.run_farm("task", "check", str(d)).stdout)
        report.write_text(json.dumps({"ok": True}))
        r = self.run_farm("task", "check", str(d))
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_role_device_rules(self):
        self.assertNotEqual(self.run_farm("task", "new", str(self.tmp / "a"), "--id", "a", "--role", "research", "--device", RM).returncode, 0)
        self.assertNotEqual(self.run_farm("task", "new", str(self.tmp / "b"), "--id", "b", "--role", "runner").returncode, 0)


if __name__ == "__main__":
    unittest.main()
