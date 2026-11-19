"""Validate a queued request and launch its leased device worker."""

import json
import sys

from .contracts import BackendNotStartedError
from .process import command
from .storage import read_json, verify_frozen_inputs, write_json


def start_runtime(data, config):
    """Prepare shared registry and daemon ownership before any SDK slot runs.

    Args:
        data: Shared executor data directory.
        config: Operator-owned device configuration.
    """
    from pathlib import Path

    from .adb import adb
    from .devices import load_devices

    for device in load_devices(config):
        if device["enabled"]:
            directory = Path(data) / "registry" / device["id"]
            directory.mkdir(parents=True, exist_ok=True)
            sync_registry(device, directory)
    adb("start-server", timeout=30)


def sync_registry(device, folder):
    """Serialize local registry transactions and retry bounded external revision conflicts.

    Args:
        device: Frozen device with both physical and wireless aliases.
        folder: Owned job directory below the shared executor jobs directory.
    """
    import fcntl

    desired = {
        "device_id": device["id"],
        "label": device["label"],
        "aliases": [device["expected_serial"], device["endpoint"]],
        "enabled": True,
    }
    with (folder.parent.parent / "phonefarm-registry.lock").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        for attempt in range(3):
            current = command(["phonefarm", "lease", "devices", "--json"], capture_output=True, text=True, timeout=15)
            if current.returncode:
                raise RuntimeError(current.stdout + current.stderr)
            registry = json.loads(current.stdout)
            existing = next((d for d in registry["devices"] if d["device_id"] == device["id"]), None)
            if existing == desired:
                return
            file = folder / "registry-request.json"
            write_json(file, {"expected_revision": registry["revision"], "device": desired})
            result = command(
                ["phonefarm", "lease", "devices-set", "--request", str(file), "--json"],
                capture_output=True, text=True, timeout=30,
            )
            (folder / "registry-receipt.json").write_text(result.stdout)
            if not result.returncode:
                return
            try:
                failure = json.loads(result.stdout)
            except ValueError:
                failure = {}
            if (
                attempt < 2
                and isinstance(failure, dict)
                and failure.get("code") == "conflict"
                and str(failure.get("error", "")).startswith("expected_revision does not match current revision")
            ):
                continue
            raise RuntimeError("device registration failed: " + result.stdout + result.stderr)


def run_backend(folder):
    """Validate frozen inputs and launch a child under the phonefarm lease."""
    frozen = read_json(folder / "input.json")
    body = frozen["submission"]
    try:
        verify_frozen_inputs(folder, frozen)
        if body["operation"] == "eval":
            request = body["request"]
            if pairs := request.get("payload", {}).get("static_pairs"):
                from .static_measurement import pair_request

                request = pair_request(request, pairs[0])
            write_json(folder / "eval-request.json", request)
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
    # Physical and wireless aliases share the native registry's canonical device
    # lease. Its FDs survive in task children without blocking other phones.
    argv = [
        "phonefarm",
        "lease",
        "--serial",
        frozen["device"]["expected_serial"],
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
