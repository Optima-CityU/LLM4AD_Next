"""Automatic same-device restoration after an SDK worker has been terminated."""

from __future__ import annotations

import fcntl
import json
import shlex
import sys
import time
import uuid
from pathlib import Path

from ..configuration import NATIVE_RESTORATION_TIMEOUT
from .adb import adb, assert_identity
from .collectors import restore_collectors
from .device import cleanup_completed, preparation_not_started
from .process import command, inherited_fds
from .retry import retry_read
from .storage import read_json, write_json


def restore_device_scripts(endpoint, request):
    """Restore native script-owned controls rather than only the GPU lock.

    Args:
        endpoint: Identity-verified device under the original physical lease.
        request: Frozen native evaluation request.

    Returns:
        A separate cleanup receipt after each applicable native script verifies
        its own readbacks and removes its original device-side state.
    """
    kind = request.get("kind")
    scripts = ["knob_sysparam"] if kind == "sysparam" else []
    if kind in {"sysparam", "whitebox"}:
        scripts.append("charge_suspend")
    if not scripts:
        return None
    lines = ['set -eu', '[ "$(id -u)" = 0 ]']
    for script in scripts:
        state = "loop_v1_sysparam" if script == "knob_sysparam" else "loop_v1_charge"
        path = f"/data/local/tmp/{state}.state"
        lines.extend([
            f"if [ -e {path} ]; then",
            f"  sh /data/local/tmp/{script}.sh restore",
            f"  [ ! -e {path} ]",
            "fi",
        ])
    lines.append("echo HGAMEY_NATIVE_SCRIPTS_RESTORED")
    output = adb("-s", endpoint, "shell", "su -c " + shlex.quote("\n".join(lines)), timeout=120)
    if output.splitlines()[-1:] != ["HGAMEY_NATIVE_SCRIPTS_RESTORED"]:
        raise RuntimeError("Native script restoration remains unconfirmed")
    return {"scope": "native_script_owned_controls", "restored": True, "scripts": scripts, "readback": output}


def restore_job(folder):
    """Verify completed cleanup or run the original native recovery owners.

    Args:
        folder: Original native job directory, never a replacement submission.

    Returns:
        Whether all owned restoration obligations have been verified.
    """
    folder = Path(folder)
    with (folder / "execution.lock").open("a+") as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        job = read_json(folder / "job.json")
        already_restored = cleanup_completed(folder, job["operation"])
        if already_restored and job.get("cleanup_status") == "completed":
            return True
        untouched = job["state"] in {"queued", "cancel_requested"} and not job.get("started_at") and not any(
            (folder / name).exists() for name in ("device-preparation.json", "backend-command.json", "foreground.json")
        )
        confirmed = already_restored or untouched or preparation_not_started(folder)
        error, native_proof = None, None
        if not confirmed:
            from llm4ad.gateway.sdk.processes import cleanup_owner

            frozen = read_json(folder / "input.json")
            owner = folder / "recovery-processes" / uuid.uuid4().hex
            argv = ["phonefarm", "lease", "--serial", frozen["device"]["expected_serial"],
                    "--label", "sdk-recovery:" + folder.name, "--timeout-ms", "30000", "--",
                    sys.executable, "-m", "llm4ad.gateway.examples.hgamey.execution.executor", "_restore", str(folder)]
            try:
                result = command(argv, capture_output=True, text=True, timeout=NATIVE_RESTORATION_TIMEOUT, owner_directory=owner)
                proof = json.loads(result.stdout)
                confirmed = result.returncode == 0 and proof.get("job_id") == folder.name and proof.get("restored") is True
                native_proof = proof
                error = proof.get("error") or (result.stderr if not confirmed else None)
            except Exception as caught:
                error = str(caught)
            finally:
                if (owner / "owner.json").is_file():
                    try:
                        cleanup_owner(owner, timeout=3)
                    except Exception as caught:
                        confirmed, error = False, str(caught)
        receipt = {"schema": "hgamey/sdk-restoration@1", "job_id": folder.name, "restored": confirmed,
                   "device_id": job["device_id"], "error": error, "verified_at": time.time()}
        if native_proof is not None:
            receipt["native_proof"] = native_proof
        write_json(folder / "sdk-restoration.json", receipt)
        job.update(state="cancelled" if confirmed else "interrupted", cleanup_status="completed" if confirmed else "uncertain",
                   finished_at=time.time(), cleanup_error=error)
        write_json(folder / "job.json", job)
        return confirmed


def restore_on_device(folder):
    """Restore native owners and foreground under the reacquired physical lease.

    Args:
        folder: Original operation with immutable input and ownership receipts.

    Returns:
        A separate cleanup proof, leaving measured results unchanged.
    """
    folder = Path(folder)
    if not inherited_fds():
        raise RuntimeError("Automatic restoration requires the original device lease")
    frozen = read_json(folder / "input.json")
    device = frozen["device"]
    endpoint = device["endpoint"]
    connection = read_json(folder / "connection.json")
    original = read_json(folder / "game-original.json") if (folder / "game-original.json").is_file() else None
    expected_boot = connection.get("boot_id") or (original or {}).get("boot_id")
    if not expected_boot:
        raise RuntimeError("Original boot identity is unavailable; automatic writes refused")
    adb("connect", endpoint, timeout=15)
    retry_read(lambda remaining: adb("-s", endpoint, "wait-for-device", timeout=remaining), 15)
    physical = retry_read(lambda remaining: adb("-s", endpoint, "shell", "getprop", "ro.serialno", timeout=remaining), 15)
    assert_identity(device, physical)
    boot = retry_read(
        lambda remaining: adb("-s", endpoint, "shell", "cat", "/proc/sys/kernel/random/boot_id", timeout=remaining), 15
    ).strip()
    if boot != expected_boot:
        raise RuntimeError("Device boot changed; automatic restoration refused")
    checks = []
    from ..protocol.game_ab import capture_reports

    try:
        completed_captures = {
            Path(report["session"]): report["recovery"]
            for _, report in capture_reports(folder / "evidence")
        }
    except (OSError, ValueError, TypeError):
        completed_captures = {}
    for path in sorted((folder / "evidence").rglob("session.json")):
        if path.is_symlink():
            raise ValueError("Native recovery receipt cannot be a symlink")
        state = read_json(path)
        if state.get("serial") not in {endpoint, device["expected_serial"]}:
            raise ValueError("Native recovery belongs to another device")
        owner = state.get("completion_owner", "standalone")
        if state.get("schema") == "phonefarm/capture-session@1":
            completed = completed_captures.get(path.parent)
            if completed is not None:
                checks.append({"session": str(path.parent), "recovery": completed})
                continue
            argv = ["phonefarm", "capture", "--recover", str(path.parent), "--serial", endpoint, "--json"]
        else:
            prefix = {
                "gray_layer_control": ["gray-layer", "session-recover"],
                "gray_eval_launch": ["eval", "recover"], "gray_eval_script": ["eval", "recover"],
                "gray_ab_loadop": ["gray-ab", "recover"],
                "gray_refbench_loadop": ["gray-layer", "refbench-recover"],
                "whitebox_refbench_native": ["whitebox-refbench-recover"],
                "standalone": ["frame-audit", "recover"],
            }.get(owner)
            if prefix is None:
                raise ValueError("Unknown native restoration owner")
            argv = ["phonefarm", *prefix, "--session", str(path.parent), "--serial", endpoint, "--json"]
        result = command(argv,
                         capture_output=True, text=True, timeout=90, pf=True)
        proof = json.loads(result.stdout)
        recovery = proof.get("recovery", proof)
        if result.returncode or recovery.get("status") != "reported_complete":
            detail = recovery.get("error") or proof.get("error") or result.stderr.strip() or "No completion receipt"
            raise RuntimeError(
                f"Native ownership restoration remains unconfirmed for {path.parent.name} "
                f"(exit={result.returncode}, status={recovery.get('status')}): {detail}"
            )
        checks.append({"session": str(path.parent), "recovery": recovery})
    # Same-input game quality replay uses the static GPU evaluator and therefore
    # can leave its own frequency/charging record even after game arms closed.
    if frozen["submission"]["operation"] == "eval":
        scripts = restore_device_scripts(endpoint, frozen["submission"].get("request", {}))
        if scripts is not None:
            checks.append(scripts)
    replay_intents = list((folder / "evidence/quality/replay").glob("frame-*/replay-intent.json"))
    if frozen["submission"]["operation"] == "eval" or replay_intents:
        from .static_measurement import recovery_directory

        evidence = recovery_directory(folder)
        result = command(["phonefarm", "gpu-op", "--serial", endpoint, "--unlock", "--out", str(evidence), "--json"],
                         capture_output=True, text=True, timeout=60, pf=True)
        proof = json.loads(result.stdout)
        if result.returncode or proof.get("restored") is not True:
            raise RuntimeError("Native frequency or charging restoration failed")
        checks.append(proof)
    checks.extend(restore_collectors(folder, endpoint))
    if original is not None:
        from ..protocol.game_ab import restore_settings

        # Native owners and collectors above have produced fresh cleanup proof;
        # failed measurement reports remain immutable historical evidence.
        if not restore_settings(folder):
            raise RuntimeError("Game settings restoration remains unconfirmed")
    foreground = read_json(folder / "foreground.json")
    if foreground.get("restore_required") is True:
        previous = foreground.get("previous")
        if not isinstance(previous, str) or not previous:
            raise RuntimeError("Original foreground identity is missing")
        output = adb("-s", endpoint, "shell", "am", "start", "-W", "-n", previous, timeout=30)
        if "Status: ok" not in output:
            raise RuntimeError("Original foreground restoration was not acknowledged")
        foreground.update(restored=True, restore_exit_code=0)
        foreground.pop("restore_blocked", None)
        write_json(folder / "foreground.json", foreground)
    elif foreground.get("restore_required") is not False:
        raise RuntimeError("Original foreground restoration obligation is unknown")
    if adb("-s", endpoint, "shell", "cat", "/proc/sys/kernel/random/boot_id", timeout=15).strip() != boot:
        raise RuntimeError("Device boot changed during automatic restoration")
    return {"schema": "hgamey/sdk-native-restoration@1", "job_id": folder.name, "device_id": device["id"],
            "boot_id": boot, "restored": True, "checks": checks}
