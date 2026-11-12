"""Run the upstream real-game A/B protocol through the serialized executor.

This operation qualifies the device or validates an identified candidate against
a fixed baseline. It retains real-game evidence without assigning fitness; the
existing operator benchmark remains the source of the evolutionary score.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

UPSTREAM = Path(__file__).with_name("upstream")
SCRIPT = UPSTREAM / "selfsr/selfsr_ab.py"
SCHEMA = "llm4ad/hgamey-game-ab@1"


def write_json(path, value):
    """Atomically retain an experiment record."""
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def validate_options(options):
    """Validate a fixed operator-owned experiment, without arbitrary commands."""
    defaults = {
        "package": "com.miHoYo.Yuanshen",
        "a": "native",
        "b": "layer",
        "rounds": 2,
        "window": 10,
        "settle": 20,
        "enter_script": None,
        "layer_path": "/opt/executor/assets/libVkLayer_refknobs.so",
        "baseline_layer_path": None,
        "candidate_id": None,
        "candidate_source_sha256": None,
        "baseline_source_sha256": None,
        "timeout_seconds": 1800,
        "pan": True,
        "power_profile": "disabled",
        "perfetto": True,
        "cool_max": 0,
    }
    if not isinstance(options, dict) or options.keys() - defaults.keys():
        raise ValueError("Unsupported game-ab options")
    result = defaults | options
    if result["package"] != "com.miHoYo.Yuanshen":
        raise ValueError("This game qualification supports Genshin only")
    for key in ("a", "b"):
        arm = result[key]
        if not isinstance(arm, str) or not re.fullmatch(
            r"native|layer|sr:(?:1|0\.[1-9][0-9]*):[1-9][0-9]{1,3}x[1-9][0-9]{1,3}:(?:0|1|0\.[0-9]+)", arm
        ):
            raise ValueError(f"{key} must be native, layer or sr:scale:WIDTHxHEIGHT:sharpness")
        if arm.startswith("sr:") and any(not 64 <= int(value) <= 4096 for value in arm.split(":")[2].split("x")):
            raise ValueError("SR output extent must be within 64..4096 pixels")
    for key, low, high in (("rounds", 1, 12), ("window", 1, 120), ("timeout_seconds", 30, 14400)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"{key} must be an integer within {low}..{high}")
    for key, high in (("settle", 300), ("cool_max", 60)):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= high:
            raise ValueError(f"Invalid {key}")
    if result["power_profile"] not in ("disabled", "linux_micro_negative", "oplus_milli_positive"):
        raise ValueError("Unsupported battery power profile")
    if any(type(result[key]) is not bool for key in ("pan", "perfetto")):
        raise ValueError("pan and perfetto must be boolean")
    entry = result["enter_script"]
    if entry is not None:
        if not isinstance(entry, str) or not entry or len(entry) > 4096:
            raise ValueError("enter_script must be a nonempty upstream action script")
        for step in entry.split(";"):
            if re.fullmatch(r"s:[0-9]+(?:\.[0-9]+)?", step):
                if not 0 <= float(step[2:]) <= 120:
                    raise ValueError("Each entry wait must be within 0..120 seconds")
            elif re.fullmatch(r"t:(?:0|1|0\.[0-9]+),(?:0|1|0\.[0-9]+)", step):
                pass
            else:
                raise ValueError("Entry actions support only waits and normalized taps")
    if not isinstance(result["layer_path"], str) or not Path(result["layer_path"]).is_absolute():
        raise ValueError("layer_path must be an absolute local path")
    if result["baseline_layer_path"] is not None and (
        not isinstance(result["baseline_layer_path"], str) or not Path(result["baseline_layer_path"]).is_absolute()
    ):
        raise ValueError("baseline_layer_path must be an absolute local path")
    if result["candidate_id"] is not None and (
        not isinstance(result["candidate_id"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", result["candidate_id"])
        or not result["baseline_layer_path"]
        or result["a"] != result["b"]
        or not result["a"].startswith("sr:")
    ):
        raise ValueError("Candidate game checks require paired SR arms with identical settings and a fixed baseline layer")
    if result["candidate_id"] is not None:
        for key in ("candidate_source_sha256", "baseline_source_sha256"):
            if not isinstance(result[key], str) or not re.fullmatch(r"[a-f0-9]{64}", result[key]):
                raise ValueError(f"Candidate game checks require {key}")
    return result


def freeze_inputs(folder, frozen):
    """Freeze the operator's layer before queue publication."""
    options = frozen["submission"]["options"] = validate_options(frozen["submission"].get("options", {}))
    if options["a"] == options["b"] == "native":
        return
    for key in ("layer_path", "baseline_layer_path"):
        if options[key] is not None:
            freeze_layer(folder, frozen, key)


def freeze_layer(folder, frozen, key):
    """Copy one immutable library into the executor's input manifest."""
    options = frozen["submission"]["options"]
    original = Path(options[key])
    target = Path(folder) / "inputs/game" / f"{key}.so"
    target.parent.mkdir(parents=True, exist_ok=True)
    with original.open("rb") as source, target.open("xb") as output:
        before = os.fstat(source.fileno())
        shutil.copyfileobj(source, output)
        after = os.fstat(source.fileno())
        output.flush()
        os.fsync(output.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Layer changed while being frozen")
    data = target.read_bytes()
    if not data.startswith(b"\x7fELF"):
        raise ValueError("The configured layer is not an ELF library")
    options[key] = str(target)
    frozen.setdefault("input_files", []).append(
        {"path": str(target.relative_to(folder)), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    )


def load_upstream():
    """Import the pinned upstream helpers without running their CLI."""
    spec = importlib.util.spec_from_file_location("hgamey_upstream_selfsr", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def properties(dev):
    """Read Android properties in one transport round trip."""
    values = dict(re.findall(r"^\[([^\]]+)\]: \[(.*)\]$", dev.shell("getprop"), re.MULTILINE))
    if not values.get("ro.serialno"):
        raise RuntimeError("Android property readback is unavailable")
    return values


def read_object(path):
    """Read a required JSON object without accepting malformed evidence."""
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return value


def latest_record(folder, prefix):
    """Order native receipts by their timestamp suffix, independently of PID."""
    return max(folder.glob(f"{prefix}-*.json"), key=lambda path: int(path.stem.rsplit("-", 1)[1]))


def capture_reports(evidence):
    """Verify persisted capture state, recovery checks and completion receipts."""
    reports = []
    for world in Path(evidence).glob("onboard/*_world"):
        sessions = list(world.glob("capture-*"))
        if (world / "recovery-pending.json").exists() or not sessions:
            raise ValueError(f"{world.name}: capture recovery is unconfirmed")
        for session in sessions:
            state = read_object(session / "session.json")
            recovery_path = latest_record(session, "recovery")
            recovery = read_object(recovery_path)
            report = read_object(latest_record(session, "report"))
            completed = report.get("recovery")
            checks = recovery.get("checks", {})
            if (
                state.get("schema") != "phonefarm/capture-session@1"
                or state.get("transport_uncertain") is not False
                or "write_pending" not in state
                or state["write_pending"] is not None
                or not state.get("serial")
                or report.get("serial") != state["serial"]
                or recovery.get("serial") != state["serial"]
                or recovery.get("schema") != "phonefarm/capture-recovery@1"
                or recovery.get("status") != "verified"
                or recovery.get("error") is not None
                or not isinstance(completed, dict)
                or completed != recovery | {"status": "reported_complete"}
                or report.get("session") != recovery.get("session")
                or Path(recovery.get("session", "")).name != session.name
                or Path(recovery.get("evidence", "")).name != recovery_path.name
                or not isinstance(checks, dict)
                or (
                    checks.get("scope") != "no_device_mutations"
                    and (
                        checks.get("stayon_restored") is not True
                        or type(checks.get("input_used")) is not bool
                        or (checks.get("input_used") is True and checks.get("input_absent") is not True)
                    )
                )
            ):
                raise ValueError(f"{world.name}: capture recovery lacks matching completion evidence")
            reports.append((world.name, report))
    return reports


def captures_confirmed(evidence):
    """Retain native capture uncertainty even when outer properties match."""
    try:
        if (evidence / "restore.json").exists() and read_object(evidence / "restore.json").get("recovery_unknown"):
            return False
        arms_path = evidence / "arms.jsonl"
        if arms_path.exists():
            for line in arms_path.read_text().splitlines():
                arm = json.loads(line)
                if not isinstance(arm, dict) or arm.get("recovery_unknown"):
                    return False
                if "mali_telemetry" in arm:
                    mali = arm["mali_telemetry"]
                    if (
                        not isinstance(mali, dict)
                        or mali.get("cleanup_confirmed") is not True
                        or mali.get("recovery_unknown") is not False
                    ):
                        return False
                if arm.get("in_game") is True and not (evidence / "onboard" / f"{arm.get('arm')}_world").is_dir():
                    return False
        capture_reports(evidence)
        return True
    except (OSError, ValueError, TypeError):
        return False


def upstream_cleanup_confirmed(evidence):
    """Require durable upstream and collector cleanup proof before outer writes."""
    try:
        restore = read_object(evidence / "restore.json")
        if (
            restore.get("layer_absent") is not True
            or restore.get("error") not in (None, "")
            or restore.get("recovery_unknown", False) is not False
            or restore.get("restoration_deferred", False) is not False
        ):
            return False
        # A lost arm record must not hide a collector's persisted recovery intent.
        for folder in evidence.glob("traces/*_mali"):
            cleanup = read_object(folder / "mali-cleanup.json")
            if cleanup.get("cleanup_confirmed") is not True or cleanup.get("recovery_unknown") is not False:
                return False
        for folder in evidence.glob("traces/*_perfetto"):
            cleanup = read_object(folder / "perfetto-cleanup.json")
            if cleanup.get("cleanup_confirmed") is not True or cleanup.get("recovery_unknown") is not False:
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def sessions_confirmed(evidence):
    """Require the native layer owner to close before restoring outer settings."""
    try:
        for session in (Path(evidence) / "sessions").glob("*"):
            if not session.is_dir():
                continue
            control = read_object(session / "session-control.json")
            recovery = control.get("recovery")
            if (
                control.get("phase") != "closed"
                or control.get("accepting") is not False
                or not isinstance(recovery, dict)
                or recovery.get("status") != "reported_complete"
                or recovery.get("error") is not None
            ):
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def finite_number(value):
    """Exclude booleans and nonfinite values from reported measurements."""
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def json_safe(value):
    """Keep invalid upstream numbers inspectable without emitting invalid JSON."""
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return None if isinstance(value, float) and not math.isfinite(value) else value


def command_for(folder, serial, options):
    """Build an argv for the upstream A/B implementation."""
    command = [
        sys.executable,
        "-I",
        str(SCRIPT),
        "run",
        "--serial",
        serial,
        "--pkg",
        options["package"],
        "--out",
        str(folder / "evidence"),
        "--a",
        options["a"],
        "--b",
        options["b"],
        "--rounds",
        str(options["rounds"]),
        "--window",
        str(options["window"]),
        "--settle",
        str(options["settle"]),
        "--cool-max",
        str(options["cool_max"]),
        "--so",
        options["layer_path"],
        "--phonefarm",
        shutil.which("phonefarm") or "/opt/phonefarm/phonefarm",
        "--vlm-calls",
        "0",
        "--verify-game-world",
        "--power-profile",
        options["power_profile"],
    ]
    if options["enter_script"]:
        command += ["--enter-script", options["enter_script"]]
    if options.get("baseline_layer_path"):
        command += ["--so-a", options["baseline_layer_path"], "--so-b", options["layer_path"]]
    if options["pan"]:
        command.append("--pan")
    if options["perfetto"]:
        command.append("--perfetto")
    return command


def summarize(evidence, options, restored, exit_code, identities=None):
    """Validate complete arms against frozen settings and retained native receipts."""
    errors, raw, arms = [], {}, []
    try:
        raw = read_object(evidence / "report.json")
        arms = [json.loads(line) for line in (evidence / "arms.jsonl").read_text().splitlines()]
        if any(not isinstance(arm, dict) for arm in arms):
            raise ValueError("Arm records must be objects")
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"Invalid upstream evidence: {exc}")
        arms = []
    if type(exit_code) is not int or exit_code != 0:
        errors.append(f"Upstream process exited with {exit_code}")
    if (
        type(raw.get("valid_pairs")) is not int
        or raw["valid_pairs"] != options["rounds"]
        or len(arms) != options["rounds"] * 2
        or raw.get("dropped_arms") != []
    ):
        errors.append("Incomplete baseline/candidate control pairs")
    if type(raw.get("rounds")) is not int or any(
        raw.get(key) != options[option] for key, option in (("pkg", "package"), ("a", "a"), ("b", "b"), ("rounds", "rounds"))
    ):
        errors.append("Upstream report differs from the frozen experiment")
    metrics = raw.get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    measured = metrics.get("display_fps")
    measured = measured if isinstance(measured, dict) else {}
    if (
        type(measured.get("n_pairs")) is not int
        or measured["n_pairs"] != options["rounds"]
        or any(not finite_number(measured.get(key)) or measured[key] <= 0 for key in ("a_mean", "b_mean"))
    ):
        errors.append("No valid game-layer display FPS measurement")
    try:
        captures = capture_reports(evidence)
    except (OSError, ValueError, TypeError) as exc:
        captures = []
        errors.append(f"Capture recovery is unresolved: {exc}")
    if not sessions_confirmed(evidence):
        errors.append("Native layer session recovery is unresolved")
    if identities is None:
        identities = {}
        for side in ("A", "B"):
            if options[side.lower()] != "native":
                path = options.get("baseline_layer_path") if side == "A" else None
                path = Path(path or options["layer_path"])
                try:
                    identities[side] = {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                except OSError as exc:
                    errors.append(f"Cannot verify {side} library identity: {exc}")
    order = [(side, pair) for pair in range(1, options["rounds"] + 1) for side in ("AB" if pair % 2 else "BA")]
    for seq, arm in enumerate(arms, 1):
        label = arm.get("arm", f"record {seq}")
        if seq > len(order):
            errors.append(f"{label}: unexpected extra arm")
            continue
        side, pair = order[seq - 1]
        kind = options[side.lower()]
        if (
            label != f"{side}{pair}"
            or arm.get("side") != side
            or arm.get("kind") != kind
            or type(arm.get("pair")) is not int
            or arm["pair"] != pair
            or type(arm.get("seq")) is not int
            or arm["seq"] != seq
        ):
            errors.append(f"{label}: arm membership differs from the frozen ABBA sequence")
        if arm.get("error"):
            errors.append(f"{label}: {arm['error']}")
        if arm.get("in_game") is not True:
            errors.append(f"{label}: game world was not confirmed during measurement")
        worlds = [report for name, report in captures if name == f"{side}{pair}_world"]
        if not worlds or any(
            report.get("ok") is not True
            or report.get("ready_only") is not True
            or type(report.get("frames")) is not int
            or report["frames"] != 1
            or report.get("stop_reason") != "target_reached"
            for report in worlds
        ):
            errors.append(f"{label}: no successful world capture")
        if not finite_number(arm.get("display_fps")) or arm["display_fps"] <= 0:
            errors.append(f"{label}: no finite positive display FPS")
        if kind != "native":
            if (
                arm.get("layer_loaded") is not True
                or arm.get("layer_removed") is not True
                or type(arm.get("session_exit")) is not int
                or arm["session_exit"] != 0
                or "session_stop" not in arm
                or arm.get("session_stop") not in (None, "completed")
                or not (evidence / "sessions" / f"{side}{pair}" / "session-control.json").is_file()
            ):
                errors.append(f"{label}: layer session did not load and close successfully")
            identity = identities.get(side, {})
            if not identity or arm.get("layer_path") != identity.get("path") or arm.get("layer_sha256") != identity.get("sha256"):
                errors.append(f"{label}: loaded library identity differs from the frozen input")
        selfsr = arm.get("selfsr")
        selfsr = selfsr if isinstance(selfsr, dict) else {}
        if kind.startswith("sr:"):
            count = arm.get("selfsr_upscaled_in_window")
            if type(count) is not int or count <= 0:
                errors.append(f"{label}: SR shader did not execute during the measurement window")
            requested = [int(value) for value in kind.split(":")[2].split("x")]
            if selfsr.get("swap_extent") != requested:
                errors.append(f"{label}: actual SR output extent differs from the configured extent")
        if arm.get("recovery_unknown"):
            errors.append(f"{label}: capture recovery is unresolved")
        if options["pan"] and (
            arm.get("pan_error") or type(arm.get("pan_moving_frames")) is not int or arm["pan_moving_frames"] <= 0
        ):
            errors.append(f"{label}: requested camera-motion verification was not measured")
        if options.get("candidate_id"):
            source_key = "baseline_source_sha256" if side == "A" else "candidate_source_sha256"
            if selfsr.get("candidate_source_sha256") != options[source_key]:
                errors.append(f"{label}: loaded shader source identity does not match the submitted code")
    for side in ("A", "B"):
        values = [arm.get("display_fps") for arm in arms if arm.get("side") == side]
        aggregate = measured.get(f"{side.lower()}_mean")
        if (
            len(values) == options["rounds"]
            and all(finite_number(value) for value in values)
            and finite_number(aggregate)
            and not math.isclose(sum(values) / len(values), aggregate, abs_tol=0.0001)
        ):
            errors.append(f"{side}: aggregate display FPS differs from its arm measurements")
    if restored is not True:
        errors.append("Device restoration requires confirmation")
    result = {
        "schema": SCHEMA,
        "source": "HGamey/experiments",
        "status": "PASS" if not errors else "ERROR",
        "measurement_scope": "game_baseline_qualification",
        "candidate_applied": False,
        "can_claim_game_improvement": False,
        "errors": errors,
        "conditions_observed": {"restored": restored is True},
        "upstream_report": json_safe(raw),
        "metrics": {
            f"{side}_{name}": values[f"{letter}_mean"]
            for name, values in metrics.items()
            if isinstance(values, dict)
            for side, letter in (("baseline", "a"), ("comparison", "b"))
            if finite_number(values.get(f"{letter}_mean"))
        },
        "unsupported_metrics": [
            name for name, value in metrics.items() if isinstance(value, dict) and value.get("status") == "not_measured"
        ],
        "evidence_directory": str(evidence),
        "telemetry": {
            str(arm.get("arm")): json_safe(arm["mali_telemetry"]) for arm in arms if isinstance(arm.get("mali_telemetry"), dict)
        },
        "perfetto": {str(arm.get("arm")): json_safe(arm["perfetto"]) for arm in arms if isinstance(arm.get("perfetto"), dict)},
        "artifacts": [],
    }
    for folder in sorted(evidence.glob("traces/*_perfetto")):
        for name in ("trace.pftrace", "analysis.json", "capabilities.json", "perfetto-cleanup.json"):
            path = folder / name
            if path.is_file() and not path.is_symlink() and path.stat().st_size:
                result["artifacts"].append(
                    {
                        "name": path.relative_to(evidence).as_posix(),
                        "size": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "media_type": "application/octet-stream" if name.endswith("pftrace") else "application/json",
                    }
                )
    for side, prefix in (("A", "baseline"), ("B", "comparison")):
        analyses = [arm.get("perfetto", {}).get("analysis", {}) for arm in arms if arm.get("side") == side]
        if len(analyses) == options["rounds"] and all(item.get("status") == "measured" for item in analyses):
            names = set.intersection(*(set(item.get("metrics", {})) for item in analyses))
            for name in names:
                values = [item["metrics"][name] for item in analyses]
                if all(finite_number(value) for value in values):
                    result["metrics"][f"{prefix}_perfetto_{name}"] = sum(values) / len(values)
    if options.get("candidate_id"):
        result.update(
            candidate_id=options["candidate_id"],
            measurement_scope="game_candidate_validation",
            candidate_applied=not errors,
            baseline_layer_sha256=identities.get("A", {}).get("sha256"),
            candidate_layer_sha256=identities.get("B", {}).get("sha256"),
        )
    return result


def run(folder):
    """Run a leased trial and retain failures even if cleanup transport breaks."""
    evidence = folder / "evidence"
    options = validate_options({})
    cancelled, child, restored, original, code = False, None, False, None, None
    errors = []
    identities = {}
    try:
        frozen = read_object(folder / "input.json")
        options = validate_options(frozen["submission"].get("options", {}))
        serial = frozen["device"]["endpoint"]
        fds = tuple(int(value) for value in os.environ.get("PF_LOCK_FDS", "").split(",") if value)
        if not fds:
            raise RuntimeError("Game A/B requires the executor's inherited device lease")
        for fd in fds:
            os.fstat(fd)
        os.environ.setdefault("PF_ROOT", "/opt/phonefarm")
        for side in ("A", "B"):
            if options[side.lower()] == "native":
                continue
            path = Path((options.get("baseline_layer_path") if side == "A" else None) or options["layer_path"])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = next((item for item in frozen.get("input_files", []) if folder / item["path"] == path), None)
            if manifest is None or manifest.get("sha256") != digest:
                raise ValueError(f"{side} library does not match the frozen input manifest")
            identities[side] = {"path": str(path.resolve()), "sha256": digest}
        upstream = load_upstream()
        dev = upstream.Dev(serial)
        if dev.shell("getprop ro.serialno").strip() != frozen["device"]["expected_serial"]:
            raise ValueError("Physical phone identity differs from the queued job")
        evidence.mkdir(exist_ok=False)
        props = sorted(
            {
                upstream.SR_PROP,
                upstream.UNLIMIT_PROP,
                upstream.FP_PROP,
                "debug.knobs.selfsr_live",
                "debug.vulkan.layers",
                *(f"debug.knobs.selfsr_{key}" for key in upstream.SR_OPTS if key != "unlimit"),
                *upstream.IP_PROPS,
                *(
                    f"debug.knobs.interp_{key}"
                    for key in upstream.IP_TUNE
                    if key not in ("panel", "framepacing", "unlimit", "sfovr", "sfwait")
                ),
            }
        )
        prop_values = properties(dev)
        snapshot = {
            "boot_id": dev.shell("cat /proc/sys/kernel/random/boot_id").strip(),
            "properties": {key: prop_values.get(key, "") for key in props},
            "settings": {
                key: dev.shell(f"settings get system {key}").strip() for key in ("screen_off_timeout", *upstream.PANEL_KEYS)
            },
            "mode": upstream.current_mode(dev, options["package"]),
            "game_configs": dev.shell(f"cmd game list-configs {options['package']}").strip(),
            "gfrc": dev.shell("settings get global game_gfrc_mode").strip(),
        }
        if (
            not snapshot["boot_id"]
            or snapshot["mode"] in (None, "", "unknown")
            or not snapshot["game_configs"]
            or not snapshot["gfrc"]
            or not all(snapshot["settings"].values())
        ):
            raise RuntimeError("Original device state is unreadable; refusing to start")
        write_json(folder / "game-original.json", snapshot)
        original = snapshot
        command = command_for(folder, serial, options)
        write_json(folder / "game-command.json", command)
        deadline = time.monotonic() + options["timeout_seconds"]
        cancelled = read_object(folder / "job.json").get("state") == "cancel_requested"
        if not cancelled:
            with (folder / "game.stdout").open("x") as out, (folder / "game.stderr").open("x") as err:
                child = subprocess.Popen(command, stdout=out, stderr=err, pass_fds=fds)
                stop_at = None
                while child.poll() is None:
                    job = read_object(folder / "job.json")
                    if stop_at is None and (job.get("state") == "cancel_requested" or time.monotonic() >= deadline):
                        cancelled = True
                        with contextlib.suppress(ProcessLookupError):
                            child.send_signal(signal.SIGTERM)
                        stop_at = time.monotonic()
                        write_json(
                            folder / "game-stop.json",
                            {
                                "reason": "cancel" if job.get("state") == "cancel_requested" else "deadline",
                            },
                        )
                    if stop_at is not None and time.monotonic() - stop_at > 300:
                        raise RuntimeError(
                            "Upstream cleanup still running; retain device ownership and inspect the original session"
                        )
                    time.sleep(0.5)
                code = child.returncode
                cancelled |= time.monotonic() >= deadline
    except Exception as exc:
        errors.append(str(exc))
    finally:
        try:
            # Capture, collector and layer owners must finish before any outer write.
            safe = (
                original is not None
                and (child is None or child.poll() is not None)
                and (child is None or upstream_cleanup_confirmed(evidence))
                and captures_confirmed(evidence)
                and sessions_confirmed(evidence)
            )
            if safe and dev.shell("cat /proc/sys/kernel/random/boot_id").strip() == original["boot_id"]:
                layer_absent, _ = upstream.layer_absent(dev, options["package"])
                if layer_absent:
                    current = properties(dev)
                    if child is not None:
                        changed = [
                            f"setprop {key} {shlex.quote(value)}"
                            for key, value in original["properties"].items()
                            if current.get(key, "") != value
                        ]
                        if changed:
                            dev.shell("; ".join(changed))
                        for key, value in original["settings"].items():
                            dev.shell(
                                f"settings delete system {key}"
                                if value == "null"
                                else f"settings put system {key} {shlex.quote(value)}"
                            )
                    current = properties(dev)
                    restored = (
                        all(current.get(key, "") == value for key, value in original["properties"].items())
                        and all(
                            dev.shell(f"settings get system {key}").strip() == value
                            for key, value in original["settings"].items()
                        )
                        and upstream.current_mode(dev, options["package"]) == original["mode"]
                        and dev.shell(f"cmd game list-configs {options['package']}").strip() == original["game_configs"]
                        and dev.shell("settings get global game_gfrc_mode").strip() == original["gfrc"]
                        and dev.shell("cat /proc/sys/kernel/random/boot_id").strip() == original["boot_id"]
                    )
        except Exception as exc:
            restored = False
            errors.append(f"Restoration failed: {exc}")
        try:
            # Cancellation can arrive after the last poll, including during cleanup.
            cancelled |= read_object(folder / "job.json").get("state") == "cancel_requested"
        except (OSError, ValueError, TypeError) as exc:
            errors.append(f"Cannot confirm cancellation state: {exc}")
        write_json(
            folder / "game-restoration.json",
            {
                "restored": restored,
                "upstream_exit_code": code,
                "errors": errors,
            },
        )
    report = summarize(evidence, options, restored, code, identities)
    for side, identity in identities.items():
        try:
            if hashlib.sha256(Path(identity["path"]).read_bytes()).hexdigest() != identity["sha256"]:
                raise ValueError(f"{side} library changed during the experiment")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    report["errors"].extend(errors)
    if report["errors"]:
        report["status"] = "ERROR"
    try:
        cancelled |= read_object(folder / "job.json").get("state") == "cancel_requested"
    except (OSError, ValueError, TypeError) as exc:
        report["errors"].append(f"Cannot confirm cancellation state: {exc}")
        report["status"] = "ERROR"
    if cancelled:
        report["status"] = "ABORT"
        report["errors"].append("Experiment cancelled or deadline exceeded")
    if report["status"] != "PASS":
        report["candidate_applied"] = False
    write_json(folder / "result.json", report)
    return 0 if report["status"] == "PASS" else 2


def main() -> int:
    """Run the standalone compatibility command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().job.resolve()))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
