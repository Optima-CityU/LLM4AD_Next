import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
DEVLOCK = ROOT / "tools/devlock"
BINARY = Path(os.environ.get("PHONEFARM_TEST_BINARY", ROOT / "src/target/debug/phonefarm"))


@unittest.skipUnless(shutil.which("zsh") and BINARY.is_file(), "devlock requires zsh and a signed PHONEFARM_TEST_BINARY")
class DevlockBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="devlock-bridge-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = dict(os.environ, DEVLOCK_DIR=str(self.root), DEVLOCK_WAIT_SECONDS="0",
                        PHONEFARM_LEASE_DIR=str(self.root / "devices"), PHONEFARM_BIN=str(BINARY))
        self.env.pop("DEVLOCK_INSTANCE_ID", None)
        self.env.pop("DEVLOCK_LABEL", None)
        self.env.pop("PF_LOCK_FDS", None)
        self.env.pop("PF_LOCK_SCOPE", None)

    def call(self, *args):
        return subprocess.run(["zsh", str(DEVLOCK), *args], env=self.env,
                              capture_output=True, text=True, timeout=5)

    def stale_owner(self, text="outer since fixture\n"):
        owner = self.root / "devlock.d/owner"
        owner.parent.mkdir(exist_ok=True)
        owner.write_text(text)
        os.utime(owner, (1, 1))
        return owner

    def test_registered_target_reaches_transport_while_another_device_is_held(self):
        """E9: the real outer gate and Lease::target must use the same lane."""
        self.env.update(DEVLOCK_BIN=str(DEVLOCK), PHONEFARM_LEASE_TIMEOUT_MS="100",
                        DEVLOCK_WAIT_SECONDS="1")

        def pf(*args, env=None):
            return subprocess.run([str(BINARY), *args], env=env or self.env, cwd=self.root,
                                  capture_output=True, text=True, timeout=10)

        for revision, device in enumerate(("a", "b")):
            request = self.root / (device + ".json")
            request.write_text(json.dumps({"expected_revision": revision, "device": {
                "device_id": device, "label": device, "aliases": ["fake-" + device], "enabled": True}}))
            result = pf("lease", "devices-set", "--request", str(request), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
        binding = pf("lease", "resolve", "--serial", "fake-a", "--json").stdout.strip()
        adb_log = self.root / "adb-calls"
        adb = self.root / "fake-adb"
        adb.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_ADB_LOG"\nexit 1\n')
        adb.chmod(0o755)
        self.env.update(ADB_BIN=str(adb), FAKE_ADB_LOG=str(adb_log))
        request = self.root / "eval.json"
        request.write_text(json.dumps({"version": 2, "kind": "sysparam", "candidate_id": "c",
            "serial": "fake-a", "package": "refbench", "payload": {"params": {}},
            "workload": {"carrier": "game_replay", "script": None, "seconds": 30},
            "conditions": {"cool_c": 42, "require_root": True, "require_discharging": True,
                           "charging_suspend": {"enabled": True, "node": None}, "inject": False},
            "protocol": {"rounds": 6, "alternating": True, "p_threshold": 0.05}}))
        ready, finish = self.root / "ready-b", self.root / "finish-b"
        holder = subprocess.Popen([str(BINARY), "lease", "--serial", "fake-b", "--timeout-ms", "500",
            "--", sys.executable, "-c",
            "from pathlib import Path; import sys,time\nPath(sys.argv[1]).touch()\n"
            "while not Path(sys.argv[2]).exists(): time.sleep(.01)\n", str(ready), str(finish)],
            cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), "device B did not acquire its real lease")
            for wrapper in ("typed", "shell", "direct", "request"):
                for command in ("perf", "eval"):
                    if wrapper == "request" and command != "eval":
                        continue
                    with self.subTest(wrapper=wrapper, command=command):
                        adb_log.write_text("")
                        args = (["perf", "--serial", "fake-a", "--rounds", "1", "--json"] if command == "perf"
                                else ["eval", "--serial", "fake-a", "--request", str(request),
                                      "--out", str(self.root / ("out-" + wrapper)), "--json"])
                        args += ["--lock-wait", "1s"]
                        env = dict(self.env)
                        if wrapper in ("typed", "shell"):
                            env["PF_JOB_DEVICE_BINDING"] = binding
                        if wrapper == "request":
                            args.remove("--serial")
                            args.remove("fake-a")
                        argv = [str(BINARY), *args]
                        if wrapper == "shell":
                            argv = ["zsh", str(DEVLOCK), "run", "shell-a", "--", *argv]
                        result = subprocess.run(argv, env=env, cwd=self.root, capture_output=True,
                                                text=True, timeout=10)
                        self.assertTrue(adb_log.read_text(), result.stdout + result.stderr)
                        self.assertIsNone(holder.poll(), "A only reached transport after B exited")
        finally:
            finish.touch()
            stdout, stderr = holder.communicate(timeout=5)
        self.assertEqual(holder.returncode, 0, stdout + stderr)

    def test_gray_control_inherits_job_lane_through_attach_and_status(self):
        self.env.update(DEVLOCK_BIN=str(DEVLOCK), PHONEFARM_LEASE_TIMEOUT_MS="100",
                        DEVLOCK_WAIT_SECONDS="1")

        def pf(*args, env=None):
            return subprocess.run([str(BINARY), *args], env=env or self.env, cwd=self.root,
                                  capture_output=True, text=True, timeout=10)

        for revision, device in enumerate(("a", "b")):
            request = self.root / (device + ".json")
            request.write_text(json.dumps({"expected_revision": revision, "device": {
                "device_id": device, "label": device, "aliases": ["fake-" + device], "enabled": True}}))
            result = pf("lease", "devices-set", "--request", str(request), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
        binding = json.loads(pf("lease", "resolve", "--serial", "fake-a", "--json").stdout)
        adb = self.root / "fake-adb"
        shutil.copyfile(Path(__file__).with_name("fake_gray_control_adb.py"), adb)
        adb.chmod(0o755)
        so = self.root / "fixture.so"
        so.write_text("host fixture layer; never loaded by a real device")
        for busy in (False, True):
            with self.subTest(other_device_busy=busy):
                case = self.root / ("busy" if busy else "alone")
                out = case / "out"
                receipts = out / "lease-receipts"
                receipts.mkdir(parents=True)
                directories = {"/"}
                for leaf in ("/data/local/tmp", "/data/app/fixture/lib/arm64",
                             "/sdcard/Android/data/com.example.fixture/files"):
                    directory = Path(leaf)
                    directories.update(str(p) for p in [directory, *directory.parents])
                (case / "android.json").write_text(json.dumps({"files": {}, "directories": sorted(directories),
                                                              "properties": {}, "running": False}))
                jid = "111111111111" if not busy else "222222222222"
                env = dict(self.env, ADB_BIN=str(adb), FAKE_GRAY_ROOT=str(case), PF_JOB_ID=jid,
                           PF_JOB_OUT=str(out), PF_JOB_LEASE_DIR=str(receipts),
                           PF_JOB_DEVICE_BINDING=json.dumps(binding))
                ready, finish = case / "b-ready", case / "b-finish"
                holder = None
                if busy:
                    holder = subprocess.Popen([str(BINARY), "lease", "--serial", "fake-b", "--", sys.executable,
                        "-c", "from pathlib import Path; import sys,time\nPath(sys.argv[1]).touch()\n"
                        "while not Path(sys.argv[2]).exists(): time.sleep(.01)", str(ready), str(finish)],
                        env=self.env, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    deadline = time.monotonic() + 5
                    while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(ready.exists(), "B never acquired its lease")
                process = subprocess.Popen(["zsh", str(DEVLOCK), "run", "job-" + jid, "--", str(BINARY),
                    "gray-layer", "session", "--serial", "fake-a", "--package", "com.example.fixture",
                    "--mode", "probe", "--so", str(so), "--out", str(out), "--keep-prop",
                    "--max-seconds", "30", "--evidence-origin", "synthetic", "--json"],
                    env=env, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    start_new_session=True)
                try:
                    deadline = time.monotonic() + 15
                    descriptor = out / "session-control.json"
                    while time.monotonic() < deadline and process.poll() is None:
                        if descriptor.exists() and json.loads(descriptor.read_text()).get("phase") == "ready":
                            break
                        time.sleep(.02)
                    actual = json.loads(descriptor.read_text()) if descriptor.exists() else {}
                    status = pf("gray-layer", "session-status", "--out", str(out), "--json", env=env)
                    self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
                    request = case / "stop.json"
                    request.write_text(json.dumps({"schema": "phonefarm/gray-control-action@1",
                        "request_id": "stop", "session_id": actual["session_id"], "actor_id": "agent",
                        "control_epoch": 1, "action": "stop", "params": {}}))
                    stop = pf("gray-layer", "session-action", "--out", str(out),
                              "--request", str(request), "--json", env=env)
                    self.assertEqual(stop.returncode, 0, stop.stdout + stop.stderr)
                    stdout, stderr = process.communicate(timeout=20)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGINT)
                    stdout, stderr = process.communicate(timeout=20)
                    if holder:
                        overlapped = holder.poll() is None
                        finish.touch()
                        b_stdout, b_stderr = holder.communicate(timeout=5)
                        self.assertEqual(holder.returncode, 0, b_stdout + b_stderr)
                        self.assertTrue(overlapped)
                self.assertTrue((case / "attached").exists(), stdout + stderr)
                self.assertEqual(actual.get("phase"), "ready", stdout + stderr)
                recovery = [path.read_text() for path in out.rglob("*recovery-*.json")]
                self.assertEqual(process.returncode, 0, stdout + stderr + "\n".join(recovery))
                self.assertEqual(actual.get("device_binding"), binding)
                self.assertEqual(json.loads(status.stdout)["device_binding"], binding)
                self.assertEqual(actual["job_id"], jid)
                final = json.loads(descriptor.read_text())
                self.assertEqual(final["phase"], "closed", final)
                self.assertEqual(final["recovery"]["status"], "reported_complete", final)
                android = json.loads((case / "android.json").read_text())
                self.assertFalse(android["running"])
                self.assertFalse(android["files"])
                self.assertFalse(any(android["properties"].values()))
                self.assertEqual(json.loads(pf("lease", "status", "--json").stdout)["status"], "clear")

    def test_status_follows_selected_phonefarm_without_creating_storage(self):
        self.env.pop("DEVLOCK_DIR")
        target = self.root / "selected persistent locks"
        resolver = self.root / "selected-phonefarm"
        resolver.write_text("#!/bin/sh\nexport DEVLOCK_DIR=" + shlex.quote(str(target)) + "\nexec " + shlex.quote(str(BINARY)) + ' "$@"\n')
        resolver.chmod(0o755)
        self.env["PHONEFARM_BIN"] = str(resolver)
        value = json.loads(self.call("status").stdout)
        self.assertEqual(value["storage"]["devlock_dir"], str(target))
        self.assertFalse(target.exists())
        self.assertEqual(self.call("run", "outer", "--", "/usr/bin/true").returncode, 0)
        self.assertTrue((target / "phonefarm-bridge.lock").exists())
        self.assertFalse((self.root / "phonefarm-bridge.lock").exists())


    def test_quick_command_preserves_output_exit_and_has_no_heartbeat(self):
        command = """
import json, os, subprocess, sys
rows = subprocess.check_output(['ps', '-axo', 'pid=,ppid='], text=True).splitlines()
siblings = [int(p) for row in rows for p,parent in [row.split()]
            if int(parent) == os.getppid() and int(p) != os.getpid()]
print(json.dumps(siblings), flush=True)
print('host stderr', file=sys.stderr, flush=True)
sys.exit(7)
"""
        result = self.call("run", "outer", "--", sys.executable, "-c", command)
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn("host stderr", result.stderr)
        self.assertEqual(json.loads(result.stdout), [])
        self.assertFalse((self.root / "devlock.d").exists())

    def test_cancellation_releases_the_task_fd(self):
        ready = self.root / "cancel-ready"
        program = "import signal,sys,time;from pathlib import Path;signal.signal(signal.SIGINT,lambda *_:sys.exit(130));Path(sys.argv[1]).touch();time.sleep(20)"
        process = subprocess.Popen(["zsh", str(DEVLOCK), "run", "cancel", "--", sys.executable, "-c", program, str(ready)],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(ready.exists())
            os.killpg(process.pid, signal.SIGINT)
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 130)
            self.assertEqual(json.loads(self.call("status").stdout)["status"], "clear")
            self.assertEqual(self.call("run", "next", "--", "/usr/bin/true").returncode, 0)
        finally:
            if process.poll() is None: os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)

    def test_busy_bridge_waits_and_explicit_timeout_returns_without_running(self):
        script = "import fcntl,pathlib,sys,time;root=pathlib.Path(sys.argv[1]);f=open(root/'phonefarm-bridge.lock','a');fcntl.flock(f,fcntl.LOCK_EX);(root/'ready').touch();time.sleep(.4)"
        for wait, expected in (("2", 0), ("0", 2)):
            holder = subprocess.Popen([sys.executable, "-c", script, str(self.root)])
            try:
                deadline = time.monotonic() + 5
                while not (self.root / "ready").exists() and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue((self.root / "ready").exists())
                self.env["DEVLOCK_WAIT_SECONDS"] = wait
                result = self.call("run", "outer", "--", "/usr/bin/true")
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            finally:
                holder.wait(timeout=5)
                (self.root / "ready").unlink(missing_ok=True)
        self.env.pop("DEVLOCK_WAIT_SECONDS", None)

    def test_registered_serial_leases_overlap(self):
        for revision, device, serial in ((0, "A", "serial-a"), (1, "B", "serial-b")):
            request = self.root / f"register-{serial}.json"
            request.write_text(json.dumps({"expected_revision": revision,
                "device": {"device_id": device, "label": device, "aliases": [serial], "enabled": True}}))
            registered = subprocess.run([str(BINARY), "lease", "devices-set", "--request", str(request), "--json"],
                                        env=self.env, capture_output=True, text=True, timeout=5)
            self.assertEqual(registered.returncode, 0, registered.stdout + registered.stderr)
        first = subprocess.Popen([str(BINARY), "lease", "--serial", "serial-a", "--timeout-ms", "4000",
                                  "--", sys.executable, "-c", "import time; time.sleep(1)"], env=self.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen([str(BINARY), "lease", "--serial", "serial-b", "--timeout-ms", "4000",
                                   "--", sys.executable, "-c", "import time; time.sleep(.2)"], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second_out, second_err = second.communicate(timeout=5)
        self.assertEqual(second.returncode, 0, second_out + second_err)
        self.assertIsNone(first.poll(), "the other serial did not start while the first lease was held")
        alias = subprocess.run([str(BINARY), "lease", "--serial", "serial-a", "--timeout-ms", "0",
                                "--", "/usr/bin/true"], env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(alias.returncode, 0, alias.stdout + alias.stderr)
        status = json.loads(self.call("status").stdout)
        self.assertTrue(any(entry.get("scope") == "device" and entry.get("device_binding")
                            for entry in status["entries"]), status)
        first_out, first_err = first.communicate(timeout=5)
        self.assertEqual(first.returncode, 0, first_out + first_err)
        self.assertNotIn("还没有设备 lane", first_err + second_err)
        unregistered = subprocess.run([str(BINARY), "lease", "--serial", "serial-c", "--timeout-ms", "1",
                                       "--", "/usr/bin/true"], env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(unregistered.returncode, 0, unregistered.stderr)

    def test_typed_devlock_overlap_same_device_and_global_exclusion(self):
        bindings = {}
        for revision, device, serial in ((0, "A", "serial-a"), (1, "B", "serial-b")):
            request = self.root / f"typed-{serial}.json"
            request.write_text(json.dumps({"expected_revision": revision,
                "device": {"device_id": device, "label": device, "aliases": [serial], "enabled": True}}))
            registered = subprocess.run([str(BINARY), "lease", "devices-set", "--request", str(request), "--json"],
                                        env=self.env, capture_output=True, text=True, timeout=5)
            self.assertEqual(registered.returncode, 0, registered.stdout + registered.stderr)
        for serial in ("serial-a", "serial-b"):
            resolved = subprocess.run([str(BINARY), "lease", "resolve", "--serial", serial, "--json"],
                                      env=self.env, capture_output=True, text=True, timeout=5)
            self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
            value = json.loads(resolved.stdout)
            bindings[serial] = value.get("binding", value)

        def environment(serial=None, wait="5"):
            value = dict(self.env, DEVLOCK_WAIT_SECONDS=wait)
            if serial is None:
                value.pop("PF_JOB_DEVICE_BINDING", None)
            else:
                value["PF_JOB_DEVICE_BINDING"] = json.dumps(bindings[serial], separators=(",", ":"))
            return value

        def spawn(serial, label, seconds, wait="5"):
            return subprocess.Popen(["zsh", str(DEVLOCK), "run", label, "--", sys.executable, "-c",
                                     "from pathlib import Path; import sys,time; Path(sys.argv[1]).touch(); "
                                     "time.sleep(float(sys.argv[2]))", str(self.root / (label + ".started")), str(seconds)],
                                    env=environment(serial, wait),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        first = spawn("serial-a", "lane-a", 1)
        second = spawn("serial-b", "lane-b", .2)
        second_out, second_err = second.communicate(timeout=10)
        self.assertEqual(second.returncode, 0, second_out + second_err)
        overlapped = first.poll() is None
        first_out, first_err = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, first_out + first_err)
        self.assertTrue(overlapped, "different typed devices did not overlap: " + first_out + first_err)
        global_owner = self.root / "v2/global/devlock.d"
        self.assertFalse(global_owner.exists(), global_owner.read_text() if global_owner.is_file() else global_owner.exists())

        first = spawn("serial-a", "same-a-1", .8)
        deadline = time.monotonic() + 5
        while not (self.root / "same-a-1.started").exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue((self.root / "same-a-1.started").exists(), "first same-device command never acquired")
        second = spawn("serial-a", "same-a-2", .1)
        second_out, second_err = second.communicate(timeout=10)
        self.assertEqual(second.returncode, 0, second_out + second_err)
        self.assertIsNotNone(first.poll(), "same-device typed runs did not serialize")
        first.communicate(timeout=10)

        global_owner = spawn(None, "global-owner", .8)
        deadline = time.monotonic() + 5
        while not (self.root / "global-owner.started").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue((self.root / "global-owner.started").exists(), "global owner did not start")
        typed = spawn("serial-a", "typed-after-global", .1)
        typed_out, typed_err = typed.communicate(timeout=10)
        self.assertEqual(typed.returncode, 0, typed_out + typed_err)
        self.assertIsNotNone(global_owner.poll(), "typed lane bypassed the global owner")
        global_owner.communicate(timeout=10)

        (self.root / "v2/global").mkdir(parents=True)
        (self.root / "v2/global/devlock.queue").write_text("old-global-wait\n")
        result = subprocess.run(["zsh", str(DEVLOCK), "run", "typed", "--", "/usr/bin/true"],
                                env=environment("serial-a", "0"), capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "v2/global/devlock.queue").read_text(), "old-global-wait\n")

    def test_nested_task_borrows_fd_while_independent_same_label_waits(self):
        ready, finish = self.root / "ready.json", self.root / "finish"
        program = """
import json,os,pathlib,subprocess,sys,time
devlock,ready,finish=sys.argv[1:]
fds=[int(fd) for fd in os.environ['PF_LOCK_FDS'].split(',')]
r=subprocess.run(['zsh',devlock,'run','outer','--','/usr/bin/true'],pass_fds=fds,capture_output=True,text=True)
pathlib.Path(ready).write_text(json.dumps({'code':r.returncode,'err':r.stderr}))
while not pathlib.Path(finish).exists(): time.sleep(.01)
"""
        process = subprocess.Popen(["zsh", str(DEVLOCK), "run", "outer", "--", str(BINARY), "lease",
            "--serial", "fixture-device", "--", sys.executable, "-c", program, str(DEVLOCK), str(ready), str(finish)],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(ready.exists())
            self.assertEqual(json.loads(ready.read_text())["code"], 0, ready.read_text())
            contender = self.call("run", "outer", "--", "/usr/bin/true")
            self.assertEqual(contender.returncode, 2, contender.stdout + contender.stderr)
        finally:
            finish.touch()
            stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(self.call("run", "next", "--", "/usr/bin/true").returncode, 0)


if __name__ == "__main__":
    unittest.main()
