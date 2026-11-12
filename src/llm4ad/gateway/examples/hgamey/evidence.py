"""HGamey device-restoration and attempt ownership policy for SDK cleanup."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

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
