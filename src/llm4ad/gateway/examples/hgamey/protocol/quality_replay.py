"""Export independent GPU invocations of one frozen game frame for local IQ judging."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import struct
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .game_quality import _image, _reject_constant

CLEANUP_GRACE_SECONDS = 120.0
_WIDTH, _HEIGHT = 1920, 1080
_ROLES = {"baseline": "A", "candidate": "B"}


def _save(path: Path, value: dict) -> None:
    """Sync an owned receipt before acknowledging native execution or cleanup."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _file(path: Path) -> bytes:
    """Read a regular immutable input without following symbolic links."""
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError(f"Replay input is not a regular owned file: {path}")
    return path.read_bytes()


def _digest(data: bytes) -> str:
    """Identify exact input or artifact bytes."""
    return hashlib.sha256(data).hexdigest()


def _manifest(path: Path, folder: Path, declared: dict | None = None) -> dict:
    """Bind an artifact within this frame folder to its current original bytes."""
    relative = path.relative_to(folder).as_posix()
    data = _file(path)
    digest = _digest(data)
    if declared and (declared.get("sha256") != digest or declared.get("size") != len(data)):
        raise ValueError(f"Native artifact checksum/size mismatch: {relative}")
    return {**(declared or {}), "name": relative, "path": str(path), "sha256": digest, "size": len(data)}


def _restored(report: dict) -> bool:
    """Require actual arm restoration and honor every supplied aggregate lock receipt."""
    observed = report.get("conditions_observed", {})
    if not isinstance(observed, dict) or observed.get("restored") is not True:
        return False
    arms = observed.get("arms")
    if not isinstance(arms, list) or not arms or any(not isinstance(a, dict) or a.get("restored") is not True for a in arms):
        return False
    lock = observed.get("gpu_level_lock")
    return lock is None or isinstance(lock, dict) and lock.get("restored") is True


def _request(shaders: dict, reference: Path, serial: str) -> tuple[dict, dict]:
    """Validate the frozen shader contract and construct a bounded native v2 request."""
    if shaders.get("protocol") != "same-input-gpu-replay@1":
        raise ValueError("Unsupported frozen replay protocol")
    files = {}
    for role in ("candidate_comp", "baseline_comp", "candidate_spv", "baseline_spv"):
        path = Path(shaders[f"{role}_path"]).absolute()
        digest = _digest(_file(path))
        if digest != shaders[f"{role}_sha256"]:
            raise ValueError(f"Frozen {role} checksum mismatch")
        files[role] = {"path": str(path), "sha256": digest}
    if Path(files["baseline_spv"]["path"]) != Path(files["candidate_spv"]["path"]).with_name("baseline.spv"):
        raise ValueError("Native replay requires baseline.spv adjacent to candidate SPIR-V")
    conditions = {
        "require_root": True, "require_discharging": False, "allow_missing_power": True,
        "charging_suspend": {"enabled": False}, "cool_c": 50, "quality_reference": str(reference),
    }
    level = os.environ.get("HGAMEY_GPU_LEVEL")
    if level is not None:
        if not level.isdecimal() or int(level) > 2**32 - 1:
            raise ValueError("HGAMEY_GPU_LEVEL must be a nonnegative u32")
        conditions["gpu_level"] = int(level)
    return {
        "version": 2, "kind": "shader", "candidate_id": files["candidate_spv"]["sha256"],
        "serial": serial, "package": "com.miHoYo.Yuanshen",
        "workload": {"carrier": "headless", "seconds": 1}, "conditions": conditions,
        "protocol": {"rounds": 3, "alternating": True, "p_threshold": 0.01, "stage": "full", "image_policy": "report"},
        "payload": {
            "track": "sr", "shader_path": files["candidate_comp"]["path"],
            "spirv_path": files["candidate_spv"]["path"], "budget_ms": 10, "quality_baseline_db": 30,
        },
    }, files


def _exports(report: dict, folder: Path, reference: Path, digest: str, dimensions: tuple[int, int]) -> dict:
    """Verify six distinct native GPU invocations against the exact prepared source frame."""
    observed = report.get("conditions_observed", {})
    arms = observed.get("arms", [])
    if observed.get("root_verified") is not True or len(arms) != 6:
        raise ValueError("Replay requires six actual root-verified native arm observations")
    for index, arm in enumerate(arms):
        if (arm.get("arm") != ("A" if index % 2 == 0 else "B") or type(arm.get("index")) is not int or arm["index"] != index
                or type(arm.get("runner_exit_status")) is not int or arm["runner_exit_status"] != 0):
            raise ValueError("Replay lacks a completed alternating GPU invocation")
    provenance = report.get("evidence_provenance")
    if not isinstance(provenance, list) or any(not isinstance(item, dict) for item in provenance):
        raise ValueError("Native evidence_provenance is missing")
    references = [p for p in provenance if p.get("role") == "reference"]
    if len(references) != 1:
        raise ValueError("Replay requires exactly one prepared reference frame")
    ref = references[0]
    source = ref.get("source", {})
    width, height = dimensions
    if (ref.get("frame_id") != "frame-00" or source.get("kind") != "prepared_reference"
            or source.get("sha256") != digest or source.get("path") != str(reference)
            or source.get("transform") != "center_crop_without_resampling"
            or (source.get("width"), source.get("height")) != dimensions
            or (source.get("crop_x"), source.get("crop_y")) != ((width - _WIDTH) // 2, (height - _HEIGHT) // 2)
            or not re.fullmatch(r"[a-f0-9]{64}", str(ref.get("rgba_sha256", "")))):
        raise ValueError("Prepared reference does not attest the original captured PNG and exact crop")
    native = folder / "native"
    artifacts = {}
    for item in report.get("artifacts", []):
        name = item.get("name")
        if not isinstance(name, str) or Path(name).is_absolute() or any(p in {".", ".."} for p in Path(name).parts):
            raise ValueError("Unsafe native artifact path")
        if name in artifacts:
            raise ValueError("Duplicate native artifact name")
        artifact = _manifest(native / name, folder, item)
        if item.get("media_type") != "image/png":
            raise ValueError("GPU replay image artifact is not a PNG")
        data = _image(native / name)
        if struct.unpack_from(">II", data, 16) != (_WIDTH, _HEIGHT):
            raise ValueError("Native image does not match the prepared reference dimensions")
        artifacts[name] = artifact
    if ref.get("artifact") not in artifacts:
        raise ValueError("Prepared reference PNG artifact is missing")
    result = {"baseline": [], "candidate": [], "artifacts": list(artifacts.values())}
    remotes, inodes, names = set(), set(), set()
    for role in _ROLES:
        outputs = [p for p in provenance if p.get("role") == role]
        if (len(outputs) != 3 or any(type(p.get("round")) is not int for p in outputs)
                or {p.get("round") for p in outputs} != {0, 1, 2}):
            raise ValueError(f"Replay requires three actual {role} GPU outputs")
        for item in sorted(outputs, key=lambda p: p["round"]):
            src, name = item.get("source", {}), item.get("artifact")
            if (item.get("frame_id") != ref["frame_id"] or item.get("reference_rgba_sha256") != ref["rgba_sha256"]
                    or item.get("reference_source_sha256") != digest or item.get("reference_source") != str(reference)
                    or src.get("kind") != "vulkan_gpu_readback" or type(src.get("runner_exit_status")) is not int
                    or src["runner_exit_status"] != 0 or src.get("outside_gpu_timing") is not True
                    or src.get("size") != _WIDTH * _HEIGHT * 4 or src.get("sha256") != item.get("rgba_sha256")
                    or not re.fullmatch(r"[a-f0-9]{64}", str(src.get("sha256", "")))
                    or Path(str(src.get("shader", ""))).name != role + ".spv"
                    or name not in artifacts or name in names):
                raise ValueError(f"Invalid {role} invocation/reference provenance")
            remote = src.get("remote_path")
            inode = (native / name).stat()
            identity = (inode.st_dev, inode.st_ino)
            if not isinstance(remote, str) or not remote or remote in remotes or identity in inodes:
                raise ValueError("GPU output paths must identify separate actual invocations")
            remotes.add(remote)
            inodes.add(identity)
            names.add(name)
            artifact = artifacts[name]
            result[role].append({
                "artifact": artifact["name"], "sha256": artifact["sha256"], "size": artifact["size"],
                "frame_id": folder.name, "source_sha256": digest,
                "invocation": {**src, "round": item["round"], "frame_id": item["frame_id"],
                               "reference_rgba_sha256": item["reference_rgba_sha256"],
                               "rgba_sha256": item["rgba_sha256"]},
            })
    if report.get("artifact_errors"):
        raise ValueError("Native GPU readback export reported errors")
    comparisons = []
    for item in report.get("evidence_comparisons", []):
        images = [{**image, "artifact": artifacts[image["artifact"]]["name"]} for image in item["images"]]
        comparisons.append({**item, "id": folder.name + "-" + item["id"], "images": images})
    result["evidence_comparisons"] = comparisons
    return result


def replay_frame(
    folder: Path, source_png: Path, shaders: dict, serial: str, cancelled: Callable[[], bool], timeout: float,
) -> dict:
    """Drain one same-input native GPU replay and export its verified original images.

    Args:
        folder: Dedicated frame-XX directory under the caller's replay evidence root.
        source_png: Actual game screenshot, copied losslessly into this owned folder.
        shaders: Frozen same-input-gpu-replay@1 contract with candidate_comp,
            baseline_comp, candidate_spv and baseline_spv path/sha256 fields.
        serial: Explicit existing leased device selector.
        cancelled: Admission/cancellation observation; a running native frame drains.
        timeout: Native execution budget, followed by bounded cleanup grace.

    Returns:
        Frame ID, frozen source, original native report, manifests with names relative
        to folder, actual baseline/candidate outputs, comparisons and cleanup status.
        Native SLOW/POOR_QUALITY are completed exports when all evidence is valid.
        Deadline expiry never kills or repeats native work; a still-running child
        retains inherited lease FDs and an unconfirmed receipt for quarantine.
    """
    folder = Path(folder).absolute()
    result: dict[str, Any] = {
        "frame_id": folder.name, "source": {}, "baseline": [], "candidate": [], "artifacts": [],
        "evidence_comparisons": [], "report": {}, "cleanup_confirmed": True, "status": "not_measured", "error": None,
    }
    handlers, child, intent = {}, None, {}
    owns_intent = False
    interrupted = threading.Event()
    try:
        if not re.fullmatch(r"frame-[0-9]{2,}", folder.name):
            raise ValueError("Replay folder must be named frame-XX")
        if not serial or any(c.isspace() for c in serial) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Replay requires an explicit serial and positive finite timeout")
        fds = tuple(int(fd) for fd in os.environ.get("PF_LOCK_FDS", "").split(",") if fd)
        if not fds:
            raise ValueError("Replay requires the existing physical device lease")
        for fd in fds:
            os.fstat(fd)
        if cancelled():
            result["status"] = "cancelled"
            return result
        data = _image(Path(source_png).absolute())
        dimensions = struct.unpack_from(">II", data, 16)
        if dimensions[0] < _WIDTH or dimensions[1] < _HEIGHT:
            raise ValueError("Captured PNG is too small for native 1920x1080 crop; no resampling allowed")
        if any(p.is_symlink() for p in (folder, *folder.parents)):
            raise ValueError("Replay evidence path contains a symbolic link")
        folder.mkdir(parents=True, exist_ok=True)
        if (folder / "replay-intent.json").exists():
            result.update(cleanup_confirmed=False, status="recovery_required")
            raise ValueError("Replay intent already exists; refusing to repeat native GPU work")
        reference = folder / "source.png"
        request, files = _request(shaders, reference, serial)
        with reference.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        source = _manifest(reference, folder)
        source["media_type"] = "image/png"
        result["source"] = {"artifact": source["name"], "sha256": source["sha256"], "size": source["size"]}
        result["artifacts"] = [source]
        _save(folder / "request.json", request)
        native = folder / "native"
        native.mkdir(exist_ok=False)
        command = [
            "phonefarm", "eval", "--request", str(folder / "request.json"),
            "--serial", serial, "--out", str(native), "--json",
        ]
        intent = {"command": command, "source": result["source"], "shaders": files, "submitted": True, "pid": None}
        with (folder / "replay-intent.json").open("x") as stream:
            owns_intent = True
            json.dump(intent, stream)
            stream.flush()
            os.fsync(stream.fileno())
        result["cleanup_confirmed"] = False
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, lambda *_: interrupted.set())
        with (folder / "native.stdout").open("x") as stdout, (folder / "native.stderr").open("x") as stderr:
            try:
                child = subprocess.Popen(command, stdout=stdout, stderr=stderr, pass_fds=fds)
            except OSError:
                result["cleanup_confirmed"] = True
                raise
            intent["pid"] = child.pid
            _save(folder / "replay-intent.json", intent)
            deadline = time.monotonic() + timeout
            stop = False
            while child.poll() is None:
                stop |= interrupted.is_set() or bool(cancelled())
                remaining = deadline + CLEANUP_GRACE_SECONDS - time.monotonic()
                if remaining <= 0:
                    result.update(status="recovery_required", pid=child.pid)
                    raise TimeoutError("Native replay is still draining; original child and lease retained, cleanup unconfirmed")
                with suppress(subprocess.TimeoutExpired):
                    child.wait(timeout=min(0.2, remaining))
            stop |= interrupted.is_set() or bool(cancelled())
            timed_out = time.monotonic() > deadline
        raw = json.loads((folder / "native.stdout").read_text(), parse_constant=_reject_constant)
        if not isinstance(raw, dict):
            raise ValueError("Native replay returned no report object")
        result["report"] = raw
        _save(folder / "native-report.json", raw)
        resolved = raw.get("resolved_config", {})
        if (raw.get("version") != 2 or raw.get("kind") != "shader" or raw.get("source") != "phonefarm"
                or not isinstance(resolved, dict) or resolved.get("serial") != serial
                or resolved.get("carrier") != "headless" or resolved.get("seconds") != 1):
            raise ValueError("Native replay device/carrier differs from the frozen request")
        result["cleanup_confirmed"] = _restored(raw)
        if not result["cleanup_confirmed"]:
            raise ValueError("Native device restoration is unconfirmed; quarantine this physical device")
        if raw.get("status") == "ABORT":
            verdict = raw.get("verdict", {})
            reason = verdict.get("reason") if isinstance(verdict, dict) else None
            result["error"] = f"Native replay aborted with verified cleanup: {reason or 'measurement incomplete'}"
            result["status"] = "cancelled" if stop else "timeout" if timed_out else "not_measured"
            return result
        if child.returncode not in (0, 1):
            raise ValueError(f"Native replay failed with exit {child.returncode}")
        if "gpu_level" in request["conditions"] and resolved.get("gpu_level") != request["conditions"]["gpu_level"]:
            raise ValueError("Native replay GPU level differs from the operator setting")
        for item in files.values():
            if _digest(_file(Path(item["path"]))) != item["sha256"]:
                raise ValueError("Frozen shader changed during replay")
        if _digest(_image(reference)) != source["sha256"]:
            raise ValueError("Frozen game frame changed during replay")
        exports = _exports(raw, folder, reference, source["sha256"], dimensions)
        result.update(exports)
        result["artifacts"].insert(0, source)
        result["status"] = "cancelled" if stop else "timeout" if timed_out else "completed"
        if timed_out:
            result["error"] = "Native replay exceeded its budget but completed verified cleanup during grace"
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
        if not result["cleanup_confirmed"]:
            result["status"] = "recovery_required"
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if owns_intent:
            metadata = [("request.json", "application/json"), ("replay-intent.json", "application/json"),
                        ("native-report.json", "application/json")]
            if child is None or child.poll() is not None:
                metadata += [("native.stdout", "text/plain"), ("native.stderr", "text/plain")]
            for name, media_type in metadata:
                path = folder / name
                if path.is_file() and path.stat().st_size:
                    result["artifacts"].append({**_manifest(path, folder), "media_type": media_type})
            result["recovery_unknown"] = not result["cleanup_confirmed"]
            _save(folder / "replay-result.json", result)
    return result
