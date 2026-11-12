"""Validate a queued request and launch its leased device worker."""

import json
import sys

from .contracts import BackendNotStartedError
from .process import command
from .storage import read_json, verify_frozen_inputs, write_json


def sync_registry(device, folder):
    """Register the selected device with revision-checked phonefarm state."""
    current = command(["phonefarm", "lease", "devices", "--json"], capture_output=True, text=True, timeout=15)
    if current.returncode:
        raise RuntimeError(current.stdout + current.stderr)
    registry = json.loads(current.stdout)
    desired = {
        "device_id": device["id"],
        "label": device["label"],
        "aliases": [device["expected_serial"], device["endpoint"]],
        "enabled": True,
    }
    existing = next((d for d in registry["devices"] if d["device_id"] == device["id"]), None)
    if existing == desired:
        return
    file = folder / "registry-request.json"
    write_json(file, {"expected_revision": registry["revision"], "device": desired})
    result = command(
        ["phonefarm", "lease", "devices-set", "--request", str(file), "--json"], capture_output=True, text=True, timeout=30
    )
    (folder / "registry-receipt.json").write_text(result.stdout)
    if result.returncode:
        raise RuntimeError("device registration failed: " + result.stdout + result.stderr)


def run_backend(folder):
    """Validate frozen inputs and launch a child under the phonefarm lease."""
    frozen = read_json(folder / "input.json")
    body = frozen["submission"]
    try:
        verify_frozen_inputs(folder, frozen)
        if body["operation"] == "eval":
            write_json(folder / "eval-request.json", body["request"])
            check = command(
                ["phonefarm", "eval", "--request", str(folder / "eval-request.json"), "--validate-only", "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            (folder / "validation.stdout").write_text(check.stdout)
            (folder / "validation.stderr").write_text(check.stderr)
            if check.returncode:
                raise ValueError("phonefarm rejected the request before connecting to phone; see validation logs")
        elif body["operation"] == "game-ab":
            from llm4ad.gateway.examples.hgamey.protocol import game_ab

            game_ab.validate_options(body["options"])
        sync_registry(frozen["device"], folder)
    except Exception as error:
        raise BackendNotStartedError(str(error)) from error
    # Global PF lease enforces the one-phone-at-a-time rule even across restarts
    # and other local PF clients sharing /work. Its FDs survive in task children.
    argv = [
        "phonefarm",
        "lease",
        "--label",
        "executor:" + folder.name,
        "--timeout-ms",
        "30000",
        "--",
        sys.executable,
        "-m",
        "llm4ad.gateway.examples.hgamey.execution.executor",
        "_device",
        str(folder),
    ]
    with (folder / "worker.stdout").open("x") as out, (folder / "worker.stderr").open("x") as err:
        result = command(argv, stdout=out, stderr=err)
    if not (folder / "result.json").exists():
        raise RuntimeError("device worker returned no report; see worker.stderr")
    return result.returncode
