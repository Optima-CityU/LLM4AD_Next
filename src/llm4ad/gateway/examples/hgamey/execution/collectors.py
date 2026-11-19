"""Reconcile persisted collectors without changing historical game measurements."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .process import inherited_fds

_LIMIT = 8 * 1024 * 1024


def _safe(path: Path) -> None:
    """Reject aliases before reading intents or allowing upstream receipt writes."""
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise RuntimeError(f"Collector symlink is not allowed: {path.name}")


def _read(path: Path) -> str:
    """Read bounded regular evidence without accepting filesystem aliases."""
    _safe(path)
    if not path.is_file() or path.stat().st_size > _LIMIT:
        raise RuntimeError(f"Collector evidence is missing or oversized: {path.name}")
    with path.open("rb") as stream:
        data = stream.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise RuntimeError(f"Collector evidence is oversized: {path.name}")
    return data.decode("utf-8")


def _object(path: Path) -> dict:
    """Require an object rather than treating broken intent as no submission."""
    value = json.loads(_read(path))
    if not isinstance(value, dict):
        raise RuntimeError(f"Collector intent must be an object: {path.name}")
    return value


def _inventory(folder: Path) -> list[tuple[str, Path, dict | None]]:
    """Discover pre-arm intents, checking arm references only for missing owners."""
    traces = folder / "evidence/traces"
    _safe(traces)
    declared = set()
    arms = folder / "evidence/arms.jsonl"
    if arms.exists() or arms.is_symlink():
        for line in _read(arms).splitlines():
            arm = json.loads(line)
            if not isinstance(arm, dict):
                raise RuntimeError("Collector arm declaration is invalid")
            for field, collector in (("perfetto", "perfetto"), ("mali_telemetry", "mali")):
                if arm.get(field) is not None:
                    label = arm.get("arm")
                    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", label):
                        raise RuntimeError("Collector arm identity is invalid")
                    declared.add(traces / f"{label}_{collector}")
    found = []
    for directory in sorted(traces.iterdir()) if traces.exists() else []:
        kind = next(
            (kind for kind in ("perfetto", "mali") if directory.name.endswith("_" + kind)), None
        )
        if kind is None:
            continue
        _safe(directory)
        if not directory.is_dir():
            raise RuntimeError(f"Collector directory is invalid: {directory.name}")
        for path in directory.rglob("*"):
            _safe(path)
            if not path.is_file() and not path.is_dir():
                raise RuntimeError(f"Collector evidence is not regular: {path.name}")
        receipt = directory / f"{kind}-cleanup.json"
        if not receipt.exists():
            # Both pinned producers mkdir before writing intent, and cannot
            # submit any remote mutation until that intent has been written.
            if any(directory.iterdir()) or directory in declared:
                raise RuntimeError(f"Collector intent is missing: {directory.name}")
            state = None
        else:
            state = _object(receipt)
        found.append((kind, directory, state))
    if declared - {directory for _, directory, _ in found}:
        raise RuntimeError("Declared collector directory is missing; cleanup unconfirmed")
    return found


def _runtime(endpoint: str) -> tuple[Any, Callable[[Any, Path], dict]]:
    """Reuse the pinned upstream transport and identity-bound Perfetto API."""
    from ..protocol.game_ab import load_upstream

    upstream = load_upstream()
    capture = importlib.import_module(upstream.PerfettoCapture.__module__)
    return upstream.Dev(endpoint), capture.reconcile


def _perfetto_intent(state: dict, directory: Path, device: dict, boot: str, package: str) -> bool:
    """Validate fields interpolated by upstream before it can touch the device."""
    remote = state.get("remote")
    token = re.fullmatch(r"/data/local/tmp/llm4ad_perfetto_([a-f0-9]{32})", remote or "")
    if (
        state.get("schema") != "llm4ad/perfetto-capture@1"
        or token is None
        or state.get("session") != "llm4ad_perfetto_" + token[1]
        or state.get("marker") != "llm4ad_window_" + token[1]
        or state.get("arm") != directory.name.removesuffix("_perfetto")
        or state.get("package") != package
        or any(
            type(state.get(key)) is not bool
            for key in ("submitted", "cleanup_confirmed", "recovery_unknown")
        )
    ):
        raise RuntimeError(f"Invalid Perfetto collector intent: {directory.name}")
    if state["submitted"] is False:
        if (
            state["cleanup_confirmed"] is not True
            or state["recovery_unknown"] is not False
            or state.get("status") not in {"pending", "unavailable"}
            or state.get("pid") is not None
            or state.get("recorder_stopped")
            or state.get("staging_removed")
        ):
            raise RuntimeError("Conflicting pre-submission Perfetto intent")
        return False
    caps = state.get("capabilities")
    if (
        not isinstance(caps, dict)
        or caps.get("physical_serial") != device["expected_serial"]
        or caps.get("boot_id") != boot
        or state.get("boot_id") != boot
    ):
        raise RuntimeError("Perfetto collector intent identity differs from the original device")
    return True


def _mali_intent(
    state: dict, directory: Path, endpoint: str, boot: str, package: str
) -> dict | None:
    """Accept only pre-submit reservations or the original tagged cleanup proof.

    The current producer does not journal its collector/sampler PID and start
    identity or an independently owned SurfaceFlinger control. An unfinished
    capture therefore cannot authorize signals or global timestats writes.
    """
    reserved = (
        set(state) == {"instance", "temporary_directory", "submitted"}
        and state["submitted"] is False
    )
    origin = state if reserved else state.get("provenance", {})
    if not isinstance(origin, dict):
        raise RuntimeError("Mali collector ownership is unconfirmed")
    instance = origin.get("instance")
    token = re.fullmatch(
        r"/sys/kernel/tracing/instances/llm4ad_mali_([a-f0-9]{32})", instance or ""
    )
    if (
        token is None
        or origin.get("temporary_directory") != "/data/local/tmp/llm4ad_mali_" + token[1]
    ):
        raise RuntimeError("Mali collector intent has no exact private resource binding")
    if reserved:
        if (directory / "mali-capture.txt").exists():
            raise RuntimeError("Mali reservation conflicts with submitted capture evidence")
        return None
    if (
        origin.get("source") != "owned_private_ftrace"
        or origin.get("serial") != endpoint
        or origin.get("package") != package
    ):
        raise RuntimeError("Mali collector intent identity differs from the original device")
    raw_path = directory / "mali-capture.txt"
    if not raw_path.is_file():
        raise RuntimeError(
            "Mali cleanup unconfirmed: original marker/PID/control ownership is unavailable"
        )
    raw = _read(raw_path)
    tag = "LLM4AD_MALI_" + token[1]
    parts = re.split(rf"(?m)^{tag}:([a-z_]+)\r?$", raw)
    labels = parts[1::2]
    if (
        any(labels.count(label) != 1 for label in ("identity_before", "cleanup", "done"))
        or labels[-1] != "done"
    ):
        raise RuntimeError("Mali cleanup unconfirmed: original tagged completion is missing")
    sections = dict(zip(labels, parts[2::2], strict=True))
    if re.findall(r"(?m)^boot_id=(.*)$", sections["identity_before"]) != [boot]:
        raise RuntimeError("Mali collector boot identity differs from the original device")
    fields = re.findall(r"(?m)^([a-z_]+)=(.*)$", sections["cleanup"])
    receipt = dict(fields)
    if (
        len(fields) != len(receipt)
        or receipt.get("instance") != instance
        or receipt.get("owned") not in {"0", "1"}
        or any(
            receipt.get(key) != "1"
            for key in ("removed", "sf_disabled", "sampler_stopped", "temp_removed")
        )
        or origin.get("cleanup_receipt", receipt) != receipt
    ):
        raise RuntimeError(
            "Mali cleanup unconfirmed: original control or sampler closure is missing"
        )
    return {
        "instance": instance,
        "temporary_directory": origin["temporary_directory"],
        "receipt": receipt,
        "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
    }


def restore_collectors(folder: str | Path, endpoint: str) -> list[dict]:
    """Restore independently journaled collectors under the caller's device lease.

    Args:
        folder: Original executor job, after its original host workers have stopped.
        endpoint: Exact frozen ADB endpoint; this function never acquires a lease.

    Returns:
        Separate ``hgamey/collector-restoration@1`` proofs, one per discovered
        directory, with collector, job-relative directory, method, cleanup flags,
        original receipt digest and recovery details. Empty inventory returns an
        empty list: both producers create their directory and persist intent
        before remote mutation. Empty directories and explicit pre-submit intents
        return no-mutation proofs without device access. Perfetto may finalize its
        cleanup receipt and resume trace transfer through the upstream API. Trace
        download failures retain remote data; this helper never requests discard.
        Mali only verifies original tagged closure plus fresh private-path absence.
        Historical arm reports, failed measurements and Mali receipts are unchanged.

    Raises:
        RuntimeError: Lease, inventory, identity, ownership or cleanup is unconfirmed.
            Earlier collectors may already have completed; retry the same job.
    """
    try:
        fds = inherited_fds()
        if not fds:
            raise RuntimeError("Collector recovery requires the already-held device lease")
        for fd in fds:
            os.fstat(fd)
        folder = Path(folder).absolute()
        frozen = _object(folder / "input.json")
        device = frozen["device"]
        if endpoint != device["endpoint"] or not device.get("expected_serial"):
            raise RuntimeError("Collector recovery endpoint differs from the frozen device")
        submission = frozen["submission"]
        if submission["operation"] not in {"game-ab", "eval", "gray-loadop", "probe"}:
            raise RuntimeError("Unknown collector lifecycle; cleanup unconfirmed")
        inventory = _inventory(folder)
        if not inventory:
            return []
        if submission["operation"] != "game-ab":
            raise RuntimeError("Collector intents do not belong to a declared game-ab lifecycle")
        boot = _object(folder / "connection.json").get("boot_id")
        if not isinstance(boot, str) or not boot:
            raise RuntimeError("Original collector boot identity is missing")
        package = submission.get("options", {}).get("package", "com.miHoYo.Yuanshen")
        plans = []
        for kind, directory, state in inventory:
            action = None
            if state is not None:
                action = (
                    _perfetto_intent(state, directory, device, boot, package)
                    if kind == "perfetto"
                    else _mali_intent(state, directory, endpoint, boot, package)
                )
            digest = (
                hashlib.sha256(_read(directory / f"{kind}-cleanup.json").encode()).hexdigest()
                if state is not None
                else None
            )
            plans.append((kind, directory, action, digest))
        runtime = None
        if any(action for _, _, action, _ in plans):
            runtime = _runtime(endpoint)
            dev, _ = runtime
            if dev.shell("getprop ro.serialno").strip() != device["expected_serial"]:
                raise RuntimeError("Collector device identity changed; cleanup unconfirmed")
            if dev.shell("cat /proc/sys/kernel/random/boot_id").strip() != boot:
                raise RuntimeError("Collector device boot changed; cleanup unconfirmed")
        proofs = []
        for kind, directory, action, digest in plans:
            recovery, method = {}, "no_device_mutations"
            if action is True:
                assert runtime is not None
                dev, reconcile = runtime
                recovery = reconcile(dev, directory)
                if (
                    recovery.get("cleanup_confirmed") is not True
                    or recovery.get("recovery_unknown") is not False
                    or recovery.get("staging_removed") is not True
                ):
                    detail = recovery.get("cleanup_error") or recovery.get("status")
                    raise RuntimeError(
                        f"Perfetto cleanup unconfirmed for {directory.name}: {detail}"
                    )
                method = "native_reconcile"
            elif isinstance(action, dict):
                assert runtime is not None
                dev, _ = runtime
                script = (
                    'set -eu\n[ "$(id -u)" = 0 ]\n'
                    "for p in /sys/kernel/tracing/instances /data/local/tmp; do\n"
                    '[ -d "$p" ] && [ -r "$p" ] && [ -x "$p" ] || exit 1\ndone\n'
                    f"[ ! -e {action['instance']} ]\n"
                    f"[ ! -e {action['temporary_directory']} ]\necho LLM4AD_MALI_ABSENT"
                )
                readback = dev.su("sh -c " + shlex.quote(script), timeout=15)
                if readback.strip() != "LLM4AD_MALI_ABSENT":
                    raise RuntimeError(
                        f"Mali cleanup unconfirmed: private resources remain for {directory.name}"
                    )
                recovery, method = dict(action, readback=readback), "recorded_cleanup"
            proofs.append(
                {
                    "schema": "hgamey/collector-restoration@1",
                    "collector": kind,
                    "directory": directory.relative_to(folder).as_posix(),
                    "method": method,
                    "cleanup_confirmed": True,
                    "recovery_unknown": False,
                    "endpoint": endpoint,
                    "physical_serial": device["expected_serial"],
                    "boot_id": boot,
                    "original_receipt_sha256": digest,
                    "recovery": recovery,
                    "verified_at": time.time(),
                }
            )
        return proofs
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        ImportError,
        subprocess.SubprocessError,
    ) as error:
        raise RuntimeError(f"Collector recovery unconfirmed: {error}") from error
