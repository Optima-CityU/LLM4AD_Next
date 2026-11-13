"""HGamey device-restoration and attempt ownership policy for SDK cleanup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import threading
from contextlib import closing
from pathlib import Path, PurePosixPath

from llm4ad.gateway.sdk.contracts import RemoteJob
from llm4ad.gateway.sdk.retention import CleanupPlan, CleanupTarget, EvidencePolicy, owned_path
from llm4ad.gateway.sdk.storage import persist

EXECUTOR_RECEIPTS = frozenset(
    {
        "job.json",
        "input.json",
        "result.json",
        "foreground.json",
        "game-original.json",
        "worker-error.json",
        "evidence-cleanup.json",
    }
)

ADAPTER_RECEIPTS = frozenset(
    {
        "owner.json",
        "candidate.comp",
        "baseline.comp",
        "submission.json",
        "report.json",
        "game-report.json",
        "executor-job.json",
        "game-executor-job.json",
    }
)

_SCREENSHOT_LOCK = threading.Lock()
_SCREENSHOT_DIRECTORY = "screenshot-export"
_SCREENSHOT_SCHEMA = "llm4ad/hgamey-screenshots@1"
_SCREENSHOT_NAME = re.compile(r"shots/[AB][12]\.png")
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


class EvidenceChangedError(ValueError):
    """A declared artifact no longer matches its original checksum-bound bytes."""


def _read_owned(path: Path, root: Path, limit: int = _MAX_ARTIFACT_BYTES) -> bytes:
    """Read bounded, stable regular-file bytes without following ownership aliases."""
    path = owned_path(path, root)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise ValueError("Invalid evidence file")
        content = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    owned_path(path, root)
    if (
        len(content) != before.st_size
        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    ):
        raise EvidenceChangedError("Evidence changed while being read")
    return content


def _valid_artifacts(artifacts) -> bool:
    """Validate optional manifest structure before using file names or identities."""
    if not isinstance(artifacts, list):
        return False
    names = set()
    for item in artifacts:
        if not isinstance(item, dict):
            return False
        name, checksum, size = item.get("name"), item.get("sha256"), item.get("size")
        if (
            not isinstance(name, str)
            or not name
            or "\x00" in name
            or "\\" in name
            or PurePosixPath(name).is_absolute()
            or ".." in PurePosixPath(name).parts
            or name == "."
            or name != PurePosixPath(name).as_posix()
            or name in names
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[a-f0-9]{64}", checksum)
            or type(size) is not int
            or not 0 < size <= _MAX_ARTIFACT_BYTES
            or not isinstance(item.get("media_type", ""), str)
            or not isinstance(item.get("description", ""), str)
        ):
            return False
        names.add(name)
    return True


def _verify_artifact(content: bytes, item: dict) -> None:
    """Require the bytes served or snapshotted to match their declared identity."""
    if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise EvidenceChangedError("Evidence changed")


def measurement_screenshots(evidence: Path, options: dict, arms: list[dict]) -> list[dict]:
    """Declare original game screenshots from the first two measurement pairs.

    Args:
        evidence: This executor job's private evidence directory.
        options: Frozen game protocol and optional candidate identity.
        arms: Recorded A/B measurement windows, including screenshot paths.

    Returns:
        At most four PNG declarations with A/B roles and capture timing.
        Missing or invalid optional screenshots do not affect measurement scores.
    """
    if (
        not isinstance(options, dict)
        or type(options.get("rounds")) is not int
        or not 1 <= options["rounds"] <= 12
        or not isinstance(arms, list)
    ):
        return []
    try:
        evidence = owned_path(evidence, evidence.parent)
        shots = owned_path(evidence / "shots", evidence)
    except (OSError, RuntimeError, ValueError):
        return []
    artifacts = []
    for pair in range(1, min(options["rounds"], 2) + 1):
        for side in ("A", "B"):
            label = f"{side}{pair}"
            records = [
                arm for arm in arms
                if isinstance(arm, dict) and arm.get("arm") == label and arm.get("side") == side
                and type(arm.get("pair")) is int and arm["pair"] == pair
            ]
            if len(records) != 1 or records[0].get("in_game") is not True:
                continue
            path = shots / f"{label}.png"
            source = records[0].get("shot")
            try:
                if not isinstance(source, str) or Path(source).resolve() != path.resolve():
                    continue
                content = _read_owned(path, evidence)
            except (OSError, RuntimeError, ValueError):
                continue
            if len(content) <= 8 or not content.startswith(b"\x89PNG\r\n\x1a\n"):
                continue
            role = "Baseline" if side == "A" else "Candidate" if options.get("candidate_id") else "Comparison"
            artifacts.append({
                "name": path.relative_to(evidence).as_posix(),
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "media_type": "image/png",
                "description": (
                    f"{role} ({label}, round {pair}); captured after the measurement window. "
                    "Visual evidence only; not a same-frame PSNR comparison."
                ),
            })
    return artifacts


def _snapshot_manifest(folder: Path) -> list[dict]:
    """Read only owned, checksum-verified PNGs from a complete published snapshot."""
    snapshot = owned_path(folder / _SCREENSHOT_DIRECTORY, folder)
    manifest = json.loads(_read_owned(snapshot / "manifest.json", folder, 65536))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != _SCREENSHOT_SCHEMA
        or manifest.get("job_id") != folder.name
        or not _valid_artifacts(manifest.get("artifacts"))
        or not 1 <= len(manifest["artifacts"]) <= 4
    ):
        raise ValueError("Invalid screenshot snapshot manifest")
    for item in manifest["artifacts"]:
        if not _SCREENSHOT_NAME.fullmatch(item["name"]) or item.get("media_type") != "image/png":
            raise ValueError("Invalid screenshot snapshot entry")
        content = _read_owned(snapshot / item["sha256"], folder)
        _verify_artifact(content, item)
        if len(content) <= 8 or not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Invalid screenshot snapshot PNG")
    return manifest["artifacts"]


def _sync_directory(path: Path) -> None:
    """Persist snapshot directory entries on the executor's POSIX filesystem."""
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _screenshot_snapshot(folder: Path, artifacts: list[dict]) -> list[dict]:
    """Publish the first complete snapshot once for concurrent executor HTTP readers."""
    # One executor owns the jobs directory; serialize its ThreadingHTTPServer readers.
    with _SCREENSHOT_LOCK:
        folder = owned_path(folder, folder.parent)
        snapshot = owned_path(folder / _SCREENSHOT_DIRECTORY, folder)
        if snapshot.exists():
            return _snapshot_manifest(folder)
        frozen = json.loads(_read_owned(folder / "input.json", folder))
        body = frozen.get("submission") if isinstance(frozen, dict) else None
        if not isinstance(body, dict) or body.get("operation") != "game-ab":
            return []
        evidence = owned_path(folder / "evidence", folder)
        arms = [json.loads(line) for line in _read_owned(evidence / "arms.jsonl", folder).decode().splitlines()]
        names = {item["name"] for item in artifacts}
        images = [item for item in measurement_screenshots(evidence, body.get("options"), arms) if item["name"] not in names]
        if not images:
            return []
        with tempfile.TemporaryDirectory(prefix=".screenshot-export-", dir=folder) as temporary:
            staging = Path(temporary)
            for item in images:
                content = _read_owned(evidence / item["name"], folder)
                _verify_artifact(content, item)
                target = staging / item["sha256"]
                if not target.exists():
                    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
            persist(staging / "manifest.json", {"schema": _SCREENSHOT_SCHEMA, "job_id": folder.name, "artifacts": images})
            _sync_directory(staging)
            owned_path(snapshot, folder)
            staging.rename(snapshot)
            _sync_directory(folder)
        return _snapshot_manifest(folder)


def artifact_bytes(folder: Path, item: dict) -> bytes:
    """Read declared artifact bytes from an owned snapshot or original evidence.

    Args:
        folder: Executor job under its canonical jobs directory.
        item: Checksum-bound declaration returned in the exported result.

    Returns:
        Original bytes matching the declaration, without reopening after verification.

    Raises:
        ValueError: The manifest, ownership or checksum is invalid.
        OSError: The declared evidence is unavailable.
    """
    folder = owned_path(folder, folder.parent)
    if not _valid_artifacts([item]):
        raise ValueError("Invalid evidence manifest")
    path = folder / "evidence" / item["name"]
    if _SCREENSHOT_NAME.fullmatch(item["name"]):
        snapshot = owned_path(folder / _SCREENSHOT_DIRECTORY, folder)
        if snapshot.exists():
            if item not in _snapshot_manifest(folder):
                raise ValueError("Evidence does not match its screenshot snapshot")
            path = snapshot / item["sha256"]
    content = _read_owned(path, folder)
    _verify_artifact(content, item)
    return content


def exported_result(folder: Path) -> dict:
    """Add frozen screenshot declarations without rewriting the measured result.

    Args:
        folder: Owned executor job containing its frozen input and result.

    Returns:
        Original measurement fields with optional game screenshot attachments.
    """
    folder = owned_path(folder, folder.parent)
    report = json.loads(owned_path(folder / "result.json", folder).read_text())
    if not isinstance(report, dict) or not _valid_artifacts(report.get("artifacts", [])):
        return report
    try:
        images = _screenshot_snapshot(folder, report.get("artifacts", []))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return report
    if not images:
        return report
    artifacts = report.get("artifacts", [])
    names = {item["name"] for item in artifacts}
    return report | {"artifacts": [*artifacts, *(item for item in images if item["name"] not in names)]}


def stage_for(gateway, job: dict, saved: dict, metadata: dict) -> Path | None:
    """Resolve one attempt's staging directory without touching shared reference data."""
    benchmark = gateway.adapter.benchmark
    if saved.get("evidence_directory"):
        if saved.get("remote_job_id") != job["id"] or saved.get("remote_attempt_id") != job["attempt_id"]:
            raise ValueError("Evidence state belongs to a different SDK attempt")
        path, key = Path(saved["evidence_directory"]), saved.get("idempotency_key", "")
    else:
        path = (
            Path(metadata["evidence_directory"])
            if metadata.get("evidence_directory")
            else (benchmark.profile / "jobs" / benchmark.attempt_key(RemoteJob.model_validate(job)))
        )
        key = path.name
        if not (path / "owner.json").is_file():
            # Old compilation failures have no durable ownership record.
            return None
    root = benchmark.inputs / "llm4ad-hgamey"
    path = owned_path(path, root)
    parts = path.relative_to(root).parts
    if (
        len(parts) != 3
        or parts[1] != "jobs"
        or parts[2] != key
        or not re.fullmatch(r"[a-f0-9]{64}", parts[0])
        or not re.fullmatch(r"[a-f0-9]{64}", key)
    ):
        raise ValueError("Invalid attempt staging identity")
    owner = path / "owner.json"
    if owner.exists():
        if owner.is_symlink() or owner.stat().st_size > 65536:
            raise ValueError("Invalid staging ownership receipt")
        record = json.loads(owner.read_text())
        if any(record.get(key) != job[key] for key in ("attempt_id", "task_id")):
            raise ValueError("Staging ownership receipt differs from the SDK attempt")
        if record.get("job_id") != job["id"]:
            raise ValueError("Staging ownership receipt differs from the SDK job")
    return path


def native_jobs(engine, saved: dict) -> list[tuple[Path, dict]]:
    """Validate every native job before removing evidence from any of them."""
    plans = []
    for intent in [saved, *([saved["game"]] if "game" in saved else [])]:
        key = intent.get("external_job_id")
        if not key:
            if (intent is saved and intent.get("request")) or (
                intent is not saved and intent.get("submission_started") is not False
            ):
                raise ValueError("External submission identity remains unknown")
            continue
        if not re.fullmatch(r"[a-f0-9]{32}", key):
            raise ValueError("Invalid native job identity")
        job = engine.get(key)
        expected_device = intent.get("request", {}).get("device_id")
        if (
            engine.active == key
            or key in engine.blocked_jobs
            or job.get("state") not in {"completed", "failed", "cancelled"}
            or job.get("cleanup_status") != "completed"
            or job.get("id") != key
            or not re.fullmatch(r"[a-f0-9]{64}", intent.get("idempotency_key", ""))
            or job.get("idempotency_key") != intent.get("idempotency_key")
            or not expected_device
            or job.get("device_id") != expected_device
        ):
            raise ValueError("Native job is active, unowned or restoration is unconfirmed")
        plans.append((owned_path(engine.jobs / key, engine.jobs), job))
    if saved.get("game_required") and "game" not in saved:
        raise ValueError("Game restoration obligation is missing")
    return plans


class HGameyEvidencePolicy(EvidencePolicy):
    """Retain native receipts and require confirmed device restoration before deletion."""

    terminal_states = frozenset({"completed", "failed"})
    include_startup = True
    clear_worktree = True
    requires_result = True

    def __init__(self, engine, gateway):
        """Bind the live serialized executor and the owning SDK gateway."""
        self.engine, self.gateway = engine, gateway

    def lock(self):
        """Prevent device jobs from being replaced while their files are pruned."""
        return self.engine.mutex

    def plan(self, gateway, job: dict, saved: dict, metadata: dict) -> CleanupPlan:
        """Confirm device ownership and restoration, then declare case-specific files."""
        startup = (
            saved.get("purpose") == "startup_readiness_only"
            or metadata.get("purpose") == "startup_readiness_only"
            or Path(job["project_root"]).absolute() == gateway.root / "startup-work"
        )
        path = gateway.root / ("startup.sqlite3" if startup else "jobs.sqlite3")
        # The collector already holds the writer fence; read without schema initialization.
        with closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute(
                "SELECT state,report FROM jobs WHERE id=? AND json_extract(job,'$.attempt_id')=?",
                (job["id"], job["attempt_id"]),
            ).fetchone()
        if row and row[0] == "failed":
            report = json.loads(row[1])
            failure = report.get("metadata", {})
            if (
                report.get("success") is not False
                or failure.get("candidate_failure") is not True
                or "CandidateCompilationError" not in (failure.get("error_type"), failure.get("failure_type"))
            ):
                raise ValueError("Only known candidate compilation failures permit failed-attempt cleanup")
        return self.restoration_plan(gateway, job, saved, metadata)

    def restoration_plan(self, gateway, job: dict, saved: dict, metadata: dict) -> CleanupPlan:
        """Check ownership and restoration independently of bulk-result cleanup eligibility.

        The SDK private-log expiry guard can reuse these checks without extending
        evidence deletion to infrastructure failures or cancellations.
        """
        stage = stage_for(gateway, job, saved, metadata)
        native = native_jobs(self.engine, saved)
        targets = []
        for path, record in native:

            def receipt(base, now, path=path, record=record):
                persist(path / "evidence-cleanup.json", {**base, "cleaned_at": now, "state": "cleaned"})
                record["evidence_cleanup"] = {"state": "cleaned", "at": now}
                persist(path / "job.json", record)

            targets.append(CleanupTarget(path, EXECUTOR_RECEIPTS, receipt))
        if stage is not None:
            targets.append(CleanupTarget(stage, ADAPTER_RECEIPTS))
        return CleanupPlan(targets, [record["id"] for _, record in native])
