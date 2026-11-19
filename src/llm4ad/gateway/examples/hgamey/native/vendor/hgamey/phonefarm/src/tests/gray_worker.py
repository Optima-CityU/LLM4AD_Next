#!/usr/bin/env python3
"""Gray worker host fixtures: real process groups, no Android/device access.

Reuse the existing bench harness's explicit Android identity substitutions.
The trace fixture substitutes tracefs paths and its selected-clock readback;
ordinary files cannot validate Android tracefs or actual GPU event delivery.
"""
import os
import json
from pathlib import Path
import signal
import subprocess
import unittest

import bench_worker


class GrayWorker(unittest.TestCase):
    def setUp(self):
        self.host = bench_worker.BenchWorker(methodName="runTest")
        self.host.setUp()
        self.instances = self.host.root / "instances"
        self.instances.mkdir()
        self.instance = self.instances / "phonefarm_gray_fixture-token"
        self.instance.mkdir()
        self.host.worker.write_text(self.host.worker.read_text().replace(
            "/sys/kernel/tracing/instances/", str(self.instances) + "/"))

    def tearDown(self):
        self.host.tearDown()

    def sample(self, run, **extra_env):
        host = self.host
        argv = [str(host.bin / "setsid"), "/bin/sh", str(host.worker), str(host.d),
                "fixture-token", "fixture-boot", "gray_sample", run, "1", str(self.instance)]
        child = subprocess.Popen(argv, env={**host.env, **extra_env}, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = child.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=2)
            raise
        self.assertFalse((host.d / "forbidden-restore").exists(), stderr)
        return child.returncode, stdout, stderr

    def children(self, workload_exit=0, sampler_exit=0):
        (self.host.d / "workload.sh").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@"\nsleep .1\nexit ' + str(workload_exit) + '\n')
        (self.host.d / "sample_env.sh").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@"\nsleep .2\necho sampler-finished\nexit ' + str(sampler_exit) + '\n')

    def test_two_children_report_success_or_failure_then_reconcile_and_verify(self):
        for run, workload_exit, sampler_exit in [("normal", 0, 0), ("failed", 7, 9)]:
            with self.subTest(run=run):
                self.children(workload_exit, sampler_exit)
                code, stdout, stderr = self.sample(run)
                self.assertEqual((code, stdout.strip()), (0, "GRAY_SAMPLE_COMPLETE"), stderr)
                r = self.host.d / run
                self.assertEqual((r / "workload_exit.txt").read_text().strip(), str(workload_exit))
                self.assertEqual((r / "sampler_exit.txt").read_text().strip(), str(sampler_exit))
                self.assertEqual((r / "workload.log").read_text().splitlines(),
                                 [str(self.instance), "1", str(r)])
                self.assertEqual((r / "sampler.log").read_text().splitlines(), ["5", "2", "sampler-finished"])
                self.assertEqual((r / "quiescence.txt").read_text().strip(), "OWNED_GROUPS_ABSENT")
                self.assertEqual({line.split("\t")[0] for line in (r / "children.tsv").read_text().splitlines()},
                                 {"sampler", "workload"})
                self.assertFalse((self.host.d / "worker.lock").exists())
                for line in (r / "children.tsv").read_text().splitlines():
                    with self.assertRaises(ProcessLookupError):
                        os.killpg(int(line.split("\t")[1]), 0)
                before = self.host.files()
                self.assertEqual(self.host.call("verify_gray_idle", "verify")[:2],
                                 (0, "GRAY_IDLE_QUIESCENT\n"))
                self.assertEqual(self.host.files(), before)
        self.assertEqual(self.host.call("reconcile_bench", "reconcile")[:2],
                         (0, "BENCH_RECONCILE_COMPLETE\n"))
        before = self.host.files()
        self.assertEqual(self.host.call("verify_bench", "verify")[:2], (0, "BENCH_CLOSED_QUIESCENT\n"))
        self.assertEqual(self.host.files(), before)
        self.assertEqual(self.host.call("verify_gray_idle", "verify")[0], 86)
        self.assertEqual(self.host.files(), before)
        # Gray uses the same strict two-tag ledger when checked after closure.
        with (self.host.d / "normal/children.tsv").open("a") as ledger:
            ledger.write("thermal_sampler\t2147483647\t123\t" + str(self.host.d / "thermal_sampler.sh") + "\n")
        before = self.host.files()
        self.assertEqual(self.host.call("verify_bench", "verify")[0], 77)
        self.assertEqual(self.host.files(), before)

    def test_unknown_group_retains_master_without_trace_or_control_cleanup(self):
        self.children()
        code, stdout, stderr = self.sample("unknown", HOST_KILL_EPERM="1")
        self.assertEqual(code, 76, stderr)
        self.assertNotIn("GRAY_SAMPLE_COMPLETE", stdout)
        self.assertTrue((self.host.d / "worker.lock/owner").is_file())
        self.assertFalse((self.host.d / "unknown/quiescence.txt").exists())

    def test_script_three_children_natural_completion_and_independent_deadline(self):
        host = self.host
        guard = (bench_worker.REPO / "loop_v1/tools/gray_child_guard.sh").read_text()
        (host.d / "gray_child_guard.sh").write_text(guard.replace("/proc/", str(host.proc) + "/"))
        host.executable("cut", r"""
import os, subprocess, sys, time
if sys.argv[-1] == os.environ["HOST_PROC"] + "/uptime":
    print("%.6f" % time.monotonic())
else:
    sys.exit(subprocess.call(["/usr/bin/cut", *sys.argv[1:]]))
""")
        (host.d / "sample_env.sh").write_text("""#!/bin/sh
set -eu
R=$3; TOKEN=$4; BOOT=$5; D=${R%/*}
. "$D/gray_child_guard.sh"
gray_child_begin sampler 5
sleep .6
gray_child_finish 0
""")
        (host.d / "trace.sh").write_text("""#!/bin/sh
set -eu
R=$3; D=${R%/*}; TOKEN=fixture-token; BOOT=fixture-boot
. "$D/gray_child_guard.sh"
gray_child_begin trace 5
echo TRACE_READY > "$R/trace-ready.txt"
while [ ! -f "$R/workload-start.txt" ]; do sleep .05; done
sleep .2
gray_child_finish 0
""")
        def sample(run, interrupted, control=False):
            # Production guard, real PGID. Only the finite command/budget is a fixture.
            workload = (bench_worker.REPO / "loop_v1/tools/gray_input_workload.sh").read_text()
            budget = 1 if interrupted else 5
            workload = workload.replace("# GRAY_PLAN_CONSTANTS", f"GRAY_BUDGET={budget}\nSTEPS=1; DECLARED_MS=10000")
            workload = workload.replace("# GRAY_PLAN_ACTIONS", "sleep 30" if interrupted else "sleep .3")
            workload_name = f"workload-{run}.sh" if control else "workload.sh"
            (host.d / workload_name).write_text(workload)
            argv = [str(host.bin / "setsid"), "/bin/sh", str(host.worker), str(host.d),
                    "fixture-token", "fixture-boot", "gray_control_sample" if control else "gray_script_sample", run, "1", str(self.instance), "0"]
            child = subprocess.Popen(argv, env=host.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = child.communicate(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate(timeout=2)
                raise
            self.assertEqual((child.returncode, stdout.strip()), (0, "GRAY_SCRIPT_SAMPLE_COMPLETE"), stderr)
            r = host.d / run
            self.assertEqual({line.split("\t")[0] for line in (r / "children.tsv").read_text().splitlines()},
                             {"workload", "trace", "sampler"})
            workload_row = next(line.split("\t") for line in (r / "children.tsv").read_text().splitlines() if line.startswith("workload\t"))
            self.assertEqual(workload_row[3], str(host.d / workload_name))
            self.assertEqual((r / "quiescence.txt").read_text().strip(), "OWNED_GROUPS_ABSENT")
            for line in (r / "children.tsv").read_text().splitlines():
                with self.assertRaises(ProcessLookupError):
                    os.killpg(int(line.split("\t")[1]), 0)
            for tag in ["trace", "sampler"] + ([] if interrupted else ["workload"]):
                timing = json.loads((r / f"{tag}-time.json").read_text())
                self.assertGreaterEqual(timing["end"], timing["start"])
                self.assertEqual(timing["exit_code"], 0)
            return r

        r = sample("script_normal", False)
        self.assertEqual((r / "workload_exit.txt").read_text().strip(), "0")
        self.assertEqual(json.loads((r / "input-complete.json").read_text())["run"], "script_normal")
        control = sample("control_normal", False, control=True)
        self.assertEqual((control / "round-kind.txt").read_text().strip(), "gray_control_v1")
        before = host.files()
        self.assertEqual(host.call("verify_gray_idle", "verify")[:2], (0, "GRAY_IDLE_QUIESCENT\n"))
        self.assertEqual(host.call("verify_gray_groups", "verify")[:2], (0, "GRAY_GROUPS_QUIESCENT\n"))
        self.assertEqual(host.files(), before)
        normal = r
        r = sample("script_deadline", True)
        self.assertNotEqual((r / "workload_exit.txt").read_text().strip(), "0")
        self.assertFalse((r / "input-complete.json").exists())
        (normal / "input-complete.json").unlink()
        before = host.files()
        self.assertEqual(host.call("verify_gray_groups", "verify")[:2], (0, "GRAY_GROUPS_QUIESCENT\n"))
        self.assertEqual(host.call("verify_gray_idle", "verify")[0], 77)
        self.assertEqual(host.files(), before)
        # The read-only receiver also accepts a valid closed-owner fixture, but
        # this weaker proof must not satisfy the normal completion verifier.
        closed = host.d / "session.closed"
        closed.mkdir()
        (closed / "owner").write_text("fixture-token fixture-boot reconcile\n")
        before = host.files()
        self.assertEqual(host.call("verify_gray_groups", "verify")[:2], (0, "GRAY_GROUPS_QUIESCENT\n"))
        self.assertEqual(host.call("verify_bench", "verify")[0], 77)
        self.assertEqual(host.files(), before)
        (closed / "owner").write_text("another-token fixture-boot reconcile\n")
        self.assertEqual(host.call("verify_gray_groups", "verify")[0], 86)
        (closed / "owner").unlink()
        closed.rmdir()
        ledger = normal / "children.tsv"
        original_ledger = ledger.read_text()
        with ledger.open("a") as output:
            output.write("thermal_sampler\t2147483647\t123\t" + str(host.d / "thermal_sampler.sh") + "\n")
        before = host.files()
        self.assertEqual(host.call("verify_gray_groups", "verify")[0], 77)
        self.assertEqual(host.files(), before)
        ledger.write_text(original_ledger)
        self.assertEqual(host.call("reconcile_bench", "reconcile")[0], 77)
        self.assertFalse((host.d / "session.closed").exists())
        self.assertTrue((host.d / "worker.lock/owner").exists())
        before = host.files()
        self.assertEqual(host.call("verify_gray_groups", "verify")[0], 85)
        self.assertEqual(host.files(), before)
        self.assertFalse((host.d / "forbidden-restore").exists())

    def test_script_trace_qualification_png_and_capture_window(self):
        host = self.host
        source = (bench_worker.REPO / "loop_v1/tools/gray_trace_workload.sh").read_text()
        source = source.replace("/sys/kernel/tracing/instances/", str(self.instances) + "/")
        source = source.replace("/data/local/tmp/", str(host.root) + "/")
        source = source.replace("/proc/", str(host.proc) + "/")
        script = host.d / "trace.sh"
        script.write_text(source)
        guard = (bench_worker.REPO / "loop_v1/tools/gray_child_guard.sh").read_text()
        (host.d / "gray_child_guard.sh").write_text(guard.replace("/proc/", str(host.proc) + "/"))
        (host.proc / "uptime").write_text("10.0 20.0\n")
        host.executable("sed", r"""
import os, subprocess, sys
from pathlib import Path
if sys.argv[-1] == os.environ.get("HOST_TRACE_CLOCK") and Path(sys.argv[-1]).read_text().strip() == "boot":
    print("boot")
else:
    sys.exit(subprocess.call(["/usr/bin/sed", *sys.argv[1:]]))
""")
        host.executable("screencap", 'import sys\nsys.stdout.buffer.write(b"\\x89PNG\\r\\n\\x1a\\nfixture")\n')
        for name, value in {"tracing_on": "0", "current_tracer": "nop", "buffer_size_kb": "16",
                            "trace_clock": "[local] boot", "trace": ""}.items():
            (self.instance / name).write_text(value + "\n")
        for event in ["adreno_cmdbatch_submitted", "adreno_cmdbatch_retired"]:
            enable = self.instance / "events/kgsl" / event / "enable"
            enable.parent.mkdir(parents=True)
            enable.write_text("0\n")
        out = host.d / "script_trace"
        out.mkdir()
        # Clock substitution skips the six-second lead, not the trace duration.
        (out / "workload-start.txt").write_text("4.0\n")
        child = subprocess.Popen([str(host.bin / "setsid"), "/bin/sh", str(script), str(self.instance),
                                  "1", str(out), "6", "1"],
                                 env={**host.env, "HOST_TRACE_CLOCK": str(self.instance / "trace_clock")},
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = child.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=2)
            raise
        self.assertEqual((child.returncode, stdout.strip()), (0, "GRAY_TRACE_COMPLETE"), stderr)
        for frame in ["frame_a.png", "frame_b.png"]:
            self.assertEqual((out / frame).read_bytes(), b"\x89PNG\r\n\x1a\nfixture")
        timing = json.loads((out / "trace-time.json").read_text())
        self.assertEqual((timing["start"], timing["end"], timing["exit_code"]), (10.0, 10.0, 0))
        self.assertIn("capture_start_uptime=10.0 capture_end_uptime=10.0", (out / "trace.txt").read_text())
        self.assertEqual((self.instance / "tracing_on").read_text().strip(), "0")

    def test_trace_workload_uses_only_owned_instance_and_rejects_missing_capability(self):
        source = (bench_worker.REPO / "loop_v1/tools/gray_trace_workload.sh").read_text()
        source = source.replace("/sys/kernel/tracing/instances/", str(self.instances) + "/")
        source = source.replace("/data/local/tmp/", str(self.host.root) + "/")
        uptime = self.host.root / "uptime"
        uptime.write_text("10.0 20.0\n")
        source = source.replace("/proc/uptime", str(uptime))
        script = self.host.root / "trace-workload.sh"
        script.write_text(source)
        self.host.executable("sed", r'''
import os, subprocess, sys
from pathlib import Path
if sys.argv[-1] == os.environ.get("HOST_TRACE_CLOCK") and Path(sys.argv[-1]).read_text().strip() == "boot":
    print("boot")  # tracefs reports the selected clock as [boot].
else:
    sys.exit(subprocess.call(["/usr/bin/sed", *sys.argv[1:]]))
''')
        for name, value in {"tracing_on": "0", "current_tracer": "nop", "buffer_size_kb": "16",
                            "trace_clock": "[local] boot", "trace": ""}.items():
            (self.instance / name).write_text(value + "\n")
        for event in ["adreno_cmdbatch_submitted", "adreno_cmdbatch_retired"]:
            enable = self.instance / "events/kgsl" / event / "enable"
            enable.parent.mkdir(parents=True)
            enable.write_text("0\n")
        stats = self.instance / "per_cpu/cpu0/stats"
        stats.parent.mkdir(parents=True)
        stats.write_text("overrun: 0\n")
        global_trace = self.host.root / "global-tracing"
        global_trace.mkdir()
        (global_trace / "tracing_on").write_text("1\n")

        def run(name, instance):
            out = self.host.d / name
            out.mkdir()
            return subprocess.run(["/bin/sh", str(script), str(instance), "1", str(out)],
                                  env={**self.host.env, "HOST_TRACE_CLOCK": str(self.instance / "trace_clock")},
                                  capture_output=True, text=True, timeout=5), out

        result, out = run("trace-normal", self.instance)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "GRAY_TRACE_COMPLETE")
        trace = (out / "trace.txt").read_text()
        self.assertIn("#loop_v1_meta capture_start_uptime=10.0 capture_end_uptime=10.0 duration_req=1", trace)
        self.assertIn("trace_mode=instance buffer_size_kb=65536 trace_clock=boot", trace)
        self.assertIn("trace_event=kgsl_buslevel status=unavailable", trace)
        self.assertIn("# phonefarm cpu0 overrun: 0", trace)
        self.assertEqual((self.instance / "tracing_on").read_text().strip(), "0")
        self.assertTrue(self.instance.is_dir())
        self.assertEqual(run("global-denied", global_trace)[0].returncode, 70)
        self.assertEqual((global_trace / "tracing_on").read_text(), "1\n")
        (self.instance / "trace_clock").write_text("[local]\n")
        result, out = run("clock-denied", self.instance)
        self.assertEqual(result.returncode, 74, result.stderr)
        self.assertFalse((out / "trace.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
