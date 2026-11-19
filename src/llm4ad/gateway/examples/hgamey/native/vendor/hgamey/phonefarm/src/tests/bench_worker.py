#!/usr/bin/env python3
"""Run production worker modes with real host process groups, never a device.

Android /proc identity files and kill's diagnostic text are host fixtures. The
actual worker/bootstrap, durable ledgers, permit handshake, wait, TERM, closure,
and read-only verification run in /bin/sh. This is not an Android kernel test.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / "loop_v1/tools/confirmation_worker.sh").read_text()
BOOTSTRAP = (REPO / "loop_v1/tools/confirmation_bootstrap.sh").read_text()


class BenchWorker(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="phonefarm-bench-worker-")
        self.root = Path(self.temp.name).resolve()
        self.proc = self.root / "proc"
        self.proc.mkdir()
        # An unreadable unrelated /proc row must not block rootless bench modes.
        (self.proc / "2147483646").mkdir()
        (self.proc / "sys/kernel/random").mkdir(parents=True)
        (self.proc / "sys/kernel/random/boot_id").write_text("fixture-boot\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {"PATH": str(self.bin) + ":/usr/bin:/bin", "HOST_PROC": str(self.proc)}
        self.executable("setsid", r'''
import os, sys
from pathlib import Path
os.setsid()
pid = os.getpid()
p = Path(os.environ["HOST_PROC"]) / str(pid)
p.mkdir()
fields = ["S", str(os.getppid()), str(os.getpgrp())] + ["0"] * 16 + ["123"]
(p / "stat").write_text(str(pid) + " (fixture) " + " ".join(fields) + "\n")
(p / "cmdline").write_bytes(b"\0".join(a.encode() for a in sys.argv[1:]) + b"\0")
os.execvp(sys.argv[1], sys.argv[1:])
''')
        self.executable("kill-check", r'''
import os, sys
if os.environ.get("HOST_KILL_EPERM"):
    print("Operation not permitted", file=sys.stderr)
    sys.exit(1)
try:
    os.kill(int(sys.argv[-1]), 0)
except ProcessLookupError:
    print("No such process", file=sys.stderr)
    sys.exit(1)
except OSError as error:
    print(str(error), file=sys.stderr)
    sys.exit(1)
''')
        self.executable("live-pid", r'''
import os, sys
try:
    os.kill(int(sys.argv[1]), 0)
except ProcessLookupError:
    sys.exit(1)
''')
        (self.bin / "sync").write_text("#!/bin/sh\nexit 0\n")
        (self.bin / "sync").chmod(0o755)
        worker = SOURCE.replace("/data/local/tmp/phonefarm-confirmation-*", str(self.root / "phonefarm-confirmation-*"))
        # Substitute only Linux-specific process inspection. Group existence is
        # still checked against the host kernel, with EPERM kept distinct.
        worker = worker.replace('[ -d "/proc/$pid" ]', 'live-pid "$pid"')
        worker = worker.replace('[ ! -d "/proc/$oldpid" ]', '! live-pid "$oldpid"')
        worker = worker.replace("/proc/", str(self.proc) + "/")
        worker = worker.replace("/system/bin/kill", str(self.bin / "kill-check"))
        marker = "# Closed bench verification is read-only"
        forbidden = '''restore_trace() { echo trace >> "$D/forbidden-restore"; return 1; }
restore_controls() { echo controls >> "$D/forbidden-restore"; return 1; }
'''
        worker = worker.replace(marker, forbidden + marker)
        self.worker = self.root / "worker.sh"
        self.worker.write_text(worker)
        self.d = self.root / "phonefarm-confirmation-fixture"
        self.d.mkdir()
        (self.d / "owner").write_text("fixture-token fixture-boot\n")
        (self.d / "bootstrap.sh").write_text(BOOTSTRAP.replace("/proc/", str(self.proc) + "/"))
        (self.d / "sample_env.sh").write_text("#!/bin/sh\necho sampler-started\nexec sleep 30\n")

    def executable(self, name, body):
        p = self.bin / name
        p.write_text("#!" + sys.executable + "\n" + body)
        p.chmod(0o755)

    def tearDown(self):
        # Each recorded PID came from this fixture's setsid launcher; no shared
        # process group is touched, including when an assertion fails.
        for p in self.proc.iterdir():
            if not (p / "stat").exists():
                continue
            try:
                os.killpg(int(p.name), signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.temp.cleanup()

    def call(self, mode, run, **extra_env):
        command = [str(self.bin / "setsid"), "/bin/sh", str(self.worker), str(self.d),
                   "fixture-token", "fixture-boot", mode, run]
        proc = subprocess.Popen(command, env={**self.env, **extra_env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = proc.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=2)
            raise
        self.assertFalse((self.d / "forbidden-restore").exists(), stderr)
        return proc.returncode, stdout, stderr

    def files(self):
        return {str(p.relative_to(self.d)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.d.rglob("*") if p.is_file()}

    def bench(self, run, workload_exit=0):
        (self.d / "workload.sh").write_text("#!/bin/sh\necho workload-output\nexit " + str(workload_exit) + "\n")
        code, out, err = self.call("bench", run)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "BENCH_COMPLETE")
        r = self.d / run
        self.assertEqual((r / "workload_exit.txt").read_text().strip(), str(workload_exit))
        self.assertEqual((r / "quiescence.txt").read_text().strip(), "OWNED_GROUPS_ABSENT")
        self.assertEqual((r / "workload.log").read_text().strip(), "workload-output")
        self.assertIn("sampler-started", (r / "sampler.log").read_text())
        self.assertFalse((self.d / "worker.lock").exists())
        self.assertFalse(list(r.glob("launch-*.pending")))
        self.assertEqual({line.split("\t")[0] for line in (r / "children.tsv").read_text().splitlines()}, {"sampler", "workload"})
        for row in (r / "children.tsv").read_text().splitlines():
            with self.assertRaises(ProcessLookupError):
                os.killpg(int(row.split("\t")[1]), 0)

    def test_bench_stops_sampler_and_preserves_workload_exit(self):
        self.bench("normal")
        self.bench("workload_failed", 7)

    def test_reconcile_closes_then_verify_is_readonly_and_rejects_unknown(self):
        self.bench("prior")
        lock = self.d / "worker.lock"
        lock.mkdir()
        (lock / "owner").write_text("fixture-token fixture-boot 2147483647 123 prior\n")
        code, out, err = self.call("reconcile_bench", "reconcile")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "BENCH_RECONCILE_COMPLETE")
        self.assertTrue((self.d / "session.closed/owner").is_file())
        self.assertFalse(lock.exists())
        before = self.files()
        self.assertEqual(self.call("verify_bench", "verify")[:2], (0, "BENCH_CLOSED_QUIESCENT\n"))
        self.assertEqual(self.files(), before)
        self.assertNotEqual(self.call("verify_bench", "verify", HOST_KILL_EPERM="1")[0], 0)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.call("reconcile_bench", "reentry")[0], 86)
        pending = self.d / "prior/launch-sampler.pending"
        pending.write_text("sampler\t" + str(self.d / "sample_env.sh"))
        before = self.files()
        self.assertEqual(self.call("verify_bench", "verify")[0], 77)
        self.assertEqual(self.files(), before)

    def test_reconcile_preserves_unattributed_pending_without_restoring(self):
        r = self.d / "lost"
        r.mkdir()
        pending = r / "launch-workload.pending"
        pending.write_text("workload\t" + str(self.d / "workload.sh") + "\n")
        original = pending.read_bytes()
        code, out, err = self.call("reconcile_bench", "reconcile")
        self.assertEqual(code, 77, err)
        self.assertNotIn("COMPLETE", out)
        self.assertEqual(pending.read_bytes(), original)
        self.assertTrue((self.d / "worker.lock/owner").exists())
        self.assertFalse((self.d / "session.closed").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
