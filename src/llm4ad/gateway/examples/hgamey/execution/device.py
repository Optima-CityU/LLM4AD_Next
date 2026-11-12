"""Execute one leased device job and confirm restoration receipts."""

import json
import os
import re
import sys

from .adb import adb, assert_identity, foreground_component
from .process import command, inherited_fds
from .storage import read_json, write_json


def game_cleanup_confirmed(folder):
    """Prevent foreground writes while a game wrapper leaves ownership uncertain."""
    try:
        report = read_json(folder / "result.json")
        if report.get("conditions_observed", {}).get("restored") is not True:
            return False
        records = [report]
        for path in (folder / "game-restoration.json", folder / "evidence/restore.json"):
            if path.exists():
                receipt = read_json(path)
                if path.name == "game-restoration.json" and receipt.get("restored") is not True:
                    return False
                records.append(receipt)
        for record in records:
            if (
                record.get("recovery_unknown", False) is not False
                or record.get("restoration_deferred", False) is not False
                or record.get("layer_absent", True) is not True
            ):
                return False
        return True
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def execute_on_device(folder):
    """Run a single already-leased job and restore the previous foreground."""
    if not inherited_fds():
        raise RuntimeError("device worker requires an inherited phonefarm lease")
    frozen = read_json(folder / "input.json")
    device, body = frozen["device"], frozen["submission"]
    endpoint = device["endpoint"]
    connected = adb("connect", endpoint, timeout=20)
    (folder / "adb-connect.txt").write_text(connected)
    if adb("-s", endpoint, "get-state").strip() != "device":
        raise RuntimeError("wireless ADB is not authorized/online")
    observed = adb("-s", endpoint, "shell", "getprop", "ro.serialno")
    assert_identity(device, observed)
    connection = {"endpoint": endpoint, "physical_serial": observed.strip(), "transport": "wireless_adb", "usb_fallback": False}
    write_json(folder / "connection.json", connection)
    if body["operation"] == "probe":
        facts = adb("-s", endpoint, "shell", "getprop ro.product.model; getprop ro.build.version.release; su -c id")
        result = {
            "schema": "hgamey/executor-probe@1",
            "status": "PASS",
            "connection": connection,
            "facts": facts,
            "root_verified": "uid=0(" in facts,
        }
        write_json(folder / "result.json", result)
        return 0
    foreground = adb("-s", endpoint, "shell", "dumpsys window")
    with (folder / "foreground-window.txt").open("x") as raw:
        raw.write(foreground)
    previous = foreground_component(foreground, ("mCurrentFocus",))
    foreground_source = "window" if previous else "unknown"
    if previous is None:
        activity = adb("-s", endpoint, "shell", "dumpsys activity activities")
        with (folder / "foreground-activity.txt").open("x") as raw:
            raw.write(activity)
        previous = foreground_component(activity, ("topResumedActivity", "mResumedActivity", "ResumedActivity", "Resumed"))
        if previous:
            foreground_source = "activity"
    target = body.get("request", {}).get("package", "io.github.hgamey.refbench")
    if body["operation"] == "game-ab":
        target = body["options"]["package"]
    # Pause only the explicitly requested foreground workload, and restore its activity.
    paused = False
    try:
        if body.get("pause_foreground") and previous:
            pkg = previous.split("/")[0]
            if pkg != target and not pkg.startswith(("com.android.", "com.oplus.", "android")):
                adb("-s", endpoint, "shell", "am", "force-stop", pkg)
                paused = True
        if body["operation"] == "eval":
            argv = [
                "phonefarm",
                "eval",
                "--request",
                str(folder / "eval-request.json"),
                "--serial",
                endpoint,
                "--out",
                str(folder / "evidence"),
                "--json",
            ]
        elif body["operation"] == "game-ab":
            argv = [sys.executable, "-m", "llm4ad.gateway.examples.hgamey.protocol.game_ab", "--job", str(folder)]
        else:
            o = body["options"]
            argv = [
                "phonefarm",
                "gray-layer",
                "refbench-loadop",
                "--serial",
                endpoint,
                "--frames",
                str(o["frames"]),
                "--intensity",
                str(o["intensity"]),
                "--timeout-seconds",
                str(o["timeout_seconds"]),
                "--so",
                os.environ.get("HGAMEY_LAYER_PATH", "/opt/executor/assets/libVkLayer_refknobs.so"),
                "--out",
                str(folder / "evidence"),
                "--json",
            ]
        write_json(folder / "backend-command.json", argv)
        with (folder / "backend.stdout").open("x") as out, (folder / "backend.stderr").open("x") as err:
            done = command(argv, pf=True, stdout=out, stderr=err)
        if body["operation"] == "game-ab":
            # The wrapper owns the report; stdout retains human-readable evidence.
            report = read_json(folder / "result.json")
            if not isinstance(report, dict):
                raise ValueError("game-ab wrapper result must be a JSON object")
            return done.returncode
        raw = (folder / "backend.stdout").read_text()
        try:
            report = json.loads(raw)
        except ValueError:
            report = {"status": "ERROR", "error": "backend did not return valid JSON; see backend.stdout/backend.stderr"}
            write_json(folder / "result.json", report)
            return 2
        write_json(folder / "result.json", report)
        return done.returncode
    finally:
        required = previous.split("/")[0] != target if previous else None
        request = body.get("request", {})
        headless = request.get("kind") == "shader" and request.get("workload", {}).get("carrier") == "headless"
        if headless and not paused:
            required = False
        receipt = {"previous": previous, "paused": paused, "headless": headless, "restore_required": required, "restored": None}
        receipt["source"] = foreground_source
        if body["operation"] == "game-ab" and not game_cleanup_confirmed(folder):
            receipt["restore_blocked"] = "game-ab cleanup is unconfirmed"
        write_json(folder / "foreground.json", receipt)
        if required and not receipt.get("restore_blocked"):
            restored = command(
                [os.environ.get("ADB_BIN", "adb"), "-s", endpoint, "shell", "am", "start", "-W", "-n", previous],
                capture_output=True,
                text=True,
                timeout=30,
            )
            (folder / "foreground-restore.txt").write_text(restored.stdout + restored.stderr)
            receipt.update(
                restore_exit_code=restored.returncode,
                restored=restored.returncode == 0 and bool(re.search(r"^Status: ok\s*$", restored.stdout, re.MULTILINE)),
            )
            write_json(folder / "foreground.json", receipt)


def cleanup_completed(folder, operation):
    """Check owned-resource and foreground receipts after the backend exits.

    Args:
        folder: Directory retaining the original backend evidence.
        operation: Frozen executor operation.

    Returns:
        Whether all applicable restoration receipts confirm completion.
    """
    if (folder / "worker-error.json").exists():
        return False
    if operation == "probe":
        return True
    try:
        report = read_json(folder / "result.json")
        observed = report.get("conditions_observed", {})
        if not isinstance(observed, dict):
            return False
        confirmed = False
        if "restored" in observed:
            if observed["restored"] is not True:
                return False
            confirmed = True
        if "recovery" in report:
            recovery = report["recovery"]
            if not isinstance(recovery, dict) or recovery.get("status") != "reported_complete":
                return False
            confirmed = True
        gpu_lock = observed.get("gpu_level_lock")
        if gpu_lock is not None and (not isinstance(gpu_lock, dict) or gpu_lock.get("restored") is not True):
            return False
        foreground = read_json(folder / "foreground.json")
        if foreground.get("restore_blocked"):
            return False
        if foreground.get("restore_required") is False:
            foreground_ok = foreground.get("paused") is False and (
                bool(foreground.get("previous")) or foreground.get("headless") is True
            )
        else:
            foreground_ok = (
                foreground.get("restore_required") is True
                and foreground.get("restored") is True
                and foreground.get("restore_exit_code") == 0
            )
        return confirmed and foreground_ok
    except (OSError, ValueError, AttributeError, TypeError):
        return False
