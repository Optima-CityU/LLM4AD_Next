#!/usr/bin/env python3
"""Run one explicit host regression lane, retaining the first result without retries."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=("fast", "driver", "integration"),
                        help="fast: binary unit tests; driver: real-clock confirmation; integration: one named target")
    parser.add_argument("--target", help="src/tests target name; required only for integration")
    parser.add_argument("--filter", action="append", default=[], help="test name filter; repeat to select several")
    parser.add_argument("--exact", action="store_true", help="match filters exactly")
    parser.add_argument("--out", type=Path, required=True, help="new evidence directory, never overwritten")
    parser.add_argument("--cargo", default=shutil.which("cargo") or str(Path.home() / ".cargo/bin/cargo"),
                        help="cargo executable; defaults to PATH or the standard rustup install")
    parser.add_argument("--plan", action="store_true", help="print commands without building, running or creating files")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "src"
    if args.lane == "integration":
        if not args.target or not args.target.isidentifier() or not (source / "tests" / (args.target + ".rs")).is_file():
            parser.error("integration requires an existing src/tests/<target>.rs name")
    elif args.target:
        parser.error("--target is only valid for integration")
    if args.exact and not args.filter:
        parser.error("--exact requires at least one --filter")
    target = "phonefarm" if args.lane == "fast" else (args.target or "confirmation_driver_cli")
    selector = "--bin" if args.lane == "fast" else "--test"
    build = [args.cargo, "test", selector, target, "--no-run", "--message-format=json"]
    # Process/heartbeat integration is not an unbudgeted host stress test. Tests
    # still exercise their own explicitly constructed concurrent owners.
    selection_args = list(args.filter) + (["--exact"] if args.exact else [])
    test_args = list(selection_args)
    if args.lane != "fast":
        test_args += ["--test-threads=1"]
    test_args += ["--nocapture"]
    out = args.out.expanduser().resolve()
    plan = {"lane": args.lane, "target": target, "cwd": str(source), "build": build,
            "test_args": test_args, "out": str(out), "retry": False,
            "coverage": "one selected target only; fast excludes integration targets"}
    if args.plan:
        print(json.dumps(plan, indent=2))
        return 0
    out.mkdir(parents=True, exist_ok=False)
    record = {**plan, "started_at": now(), "state": "building"}

    def save():
        (out / "run.json").write_text(json.dumps(record, indent=2) + "\n")

    def run(label, command, env=None):
        print(f"{label}: {' '.join(map(str, command))}", flush=True)
        start = time.monotonic()
        with (out / (label + ".stdout")).open("wb") as stdout, (out / (label + ".stderr")).open("wb") as stderr:
            result = subprocess.run(command, cwd=source, env=env, stdout=stdout, stderr=stderr)
        record[label] = {"argv": list(map(str, command)), "exit": result.returncode,
                         "elapsed_seconds": time.monotonic() - start}
        save()
        return result.returncode

    save()
    try:
        code = run("build", build)
        if not code:
            executables = set()
            test_executable = None
            for line in (out / "build.stdout").read_text().splitlines():
                item = json.loads(line)
                if item.get("reason") == "compiler-artifact" and item.get("executable"):
                    executables.add(item["executable"])
                    if item["target"]["name"] == target and item["profile"]["test"]:
                        test_executable = item["executable"]
            if not test_executable:
                raise RuntimeError("cargo did not report the selected test executable")
            record["executables"] = sorted(executables)
            if platform.system() == "Darwin":
                for i, executable in enumerate(sorted(executables)):
                    code = run(f"codesign-{i}", ["codesign", "--force", "--sign", "-", executable])
                    if code:
                        break
            if not code:
                code = run("selection", [test_executable, *selection_args, "--list"])
                selected = [line.removesuffix(": test") for line in (out / "selection.stdout").read_text().splitlines()
                            if line.endswith(": test")]
                record["selected_tests"] = selected
                if not code and not selected:
                    raise RuntimeError("the selected filters match zero tests")
            if not code:
                record["state"] = "testing"
                save()
                env = os.environ.copy()
                if target == "confirmation_driver_cli":
                    env["PHONEFARM_DRIVER_TEST_ARTIFACT_DIR"] = str(out / "fixtures")
                code = run("tests", [test_executable, *test_args], env)
        record["state"] = "passed" if code == 0 else "failed"
        record["exit"] = code
    except (OSError, ValueError, RuntimeError) as error:
        record.update(state="runner_failed", error=str(error), exit=1)
        code = 1
    except KeyboardInterrupt:
        record.update(state="interrupted", exit=130)
        code = 130
    record["finished_at"] = now()
    save()
    print(f"{record['state']}: {out / 'run.json'}", flush=True)
    return code if code >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
