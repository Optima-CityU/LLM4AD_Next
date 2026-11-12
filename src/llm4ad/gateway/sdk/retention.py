"""Shared acknowledgement fences, cleanup receipts and guarded file retention."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from .journal import Journal
from .lifecycle import worker_active, worker_directory
from .storage import persist


@dataclass
class CleanupTarget:
    """Declare one backend-owned directory and compact files to retain."""

    path: Path
    keep: frozenset[str] = frozenset()
    after_cleanup: Callable | None = None


@dataclass
class CleanupPlan:
    """Hold validated backend cleanup targets while the backend guard is locked."""

    targets: list[CleanupTarget] = field(default_factory=list)
    external_jobs: list[str] = field(default_factory=list)


class EvidencePolicy:
    """Supply optional external ownership checks without duplicating SDK collection."""

    terminal_states = frozenset({"completed", "failed", "cancelled"})
    include_startup = False
    clear_worktree = False
    requires_result = False

    def lock(self):
        """Hold any backend resource guard for planning and deletion."""
        return nullcontext()

    def plan(self, gateway, job: dict, saved: dict, metadata: dict) -> CleanupPlan:
        """Validate and declare external evidence; ordinary evaluators need none."""
        return CleanupPlan()

    def restoration_plan(self, gateway, job: dict, saved: dict, metadata: dict) -> CleanupPlan:
        """Reuse ownership checks for log expiry independently of bulk cleanup eligibility."""
        return self.plan(gateway, job, saved, metadata)


def owned_path(path: Path, root: Path) -> Path:
    """Require a strict descendant of the canonical root without symlink parents."""
    path, root = path.absolute(), root.absolute()
    if (
        root.resolve() != root
        or path == root
        or not path.is_relative_to(root)
        or ".." in path.parts
    ):
        raise ValueError("Evidence cleanup path is outside its owned root")
    current = root
    for component in path.relative_to(root).parts:
        current /= component
        if current.is_symlink():
            raise ValueError("Evidence cleanup path contains a symlink")
    return path


def prune_directory(path: Path, keep: frozenset[str]) -> int:
    """Remove direct children except small receipts, unlinking rather than following links.

    Args:
        path: Previously verified attempt-owned directory.
        keep: Names of compact top-level receipts to retain.

    Returns:
        Number of regular-file bytes removed.
    """
    if not path.exists():
        return 0
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Evidence cleanup requires an owned directory")
    removed = 0
    for child in path.iterdir():
        if child.name in keep:
            if child.is_symlink():
                raise ValueError("Evidence receipt cannot be a symlink")
            continue
        if child.is_symlink():
            child.unlink()
        elif child.is_dir():
            size = sum(
                item.stat().st_size
                for item in child.rglob("*")
                if not item.is_symlink() and item.is_file()
            )
            shutil.rmtree(child)
            removed += size
        else:
            size = child.stat().st_size
            child.unlink()
            removed += size
    return removed


def collect_journal(
    gateway,
    journal: Journal,
    policy: EvidencePolicy,
    *,
    startup: bool,
    keep_seconds: float,
    now: float,
) -> dict:
    """Prune acknowledged attempts with a SQLite fence against replacement jobs."""
    totals = {"cleaned": 0, "removed_bytes": 0, "errors": 0}
    snapshots = [(row, False) for row in journal.rows()]
    with journal.connect() as db:
        snapshots.extend(
            (json.loads(row[0]), True) for row in db.execute("SELECT snapshot FROM retired_attempts")
        )
    for snapshot, retired in snapshots:
        if snapshot["state"] not in policy.terminal_states or (
            not startup and snapshot["ack"] < snapshot["seq"]
        ):
            continue
        job = json.loads(snapshot["job"])
        for key in ("id", "attempt_id"):
            uuid.UUID(job[key])
            if Path(job[key]).name != job[key]:
                raise ValueError("Invalid SDK attempt path")
        identity = hashlib.sha256(f"{job['id']}|{job['attempt_id']}".encode()).hexdigest()
        receipt = owned_path(
            gateway.root / "retention" / Path(journal.path).name / f"{identity}.json", gateway.root
        )
        try:
            prior = json.loads(receipt.read_text()) if receipt.exists() else {}
            if prior.get("state") == "cleaned":
                if retired:
                    from .local_logs import LocalLogStore

                    catalog = LocalLogStore(journal).directory(job["id"], job["attempt_id"]) / "manifest.json"
                    with journal.connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        if catalog.is_file():
                            db.execute(
                                "INSERT OR IGNORE INTO local_log_attempts(job_id,attempt_id,snapshot) VALUES(?,?,?)",
                                (job["id"], job["attempt_id"], json.dumps(snapshot)),
                            )
                        db.execute(
                            "DELETE FROM retired_attempts WHERE job_id=? AND attempt_id=?",
                            (job["id"], job["attempt_id"]),
                        )
                continue
            process = getattr(gateway, "processes", {}).get(job["id"])
            if process and process.is_alive():
                continue
            if worker_active(journal, job["id"], job["attempt_id"]):
                continue
            with journal.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.row_factory = sqlite3.Row
                current = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
                if retired:
                    if current and json.loads(current["job"])["attempt_id"] == job["attempt_id"]:
                        continue
                    context = db.execute(
                        "SELECT snapshot FROM retired_attempts WHERE job_id=? AND attempt_id=?",
                        (job["id"], job["attempt_id"]),
                    ).fetchone()
                    row = json.loads(context[0]) if context else None
                else:
                    row = dict(current) if current else None
                if (
                    row is None
                    or json.loads(row["job"])["attempt_id"] != job["attempt_id"]
                    or row["state"] not in policy.terminal_states
                    or (not startup and row["ack"] < row["seq"])
                ):
                    continue
                saved, report = json.loads(row["saved"]), json.loads(row["report"])
                if policy.requires_result and type(report.get("success")) is not bool:
                    continue
                if startup and (
                    report["success"] is not True
                    or saved.get("purpose") != "startup_readiness_only"
                    or report.get("metadata", {}).get("purpose") != "startup_readiness_only"
                ):
                    continue
                artifacts = journal.artifacts(job["id"], job["attempt_id"])
                if not startup and any(not item["object_key"] for item in artifacts):
                    continue
                spool = owned_path(gateway.root / "artifacts" / identity, gateway.root)
                for item in artifacts:
                    if not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
                        raise ValueError("Invalid evidence outbox identity")
                    if Path(item["path"]).absolute() != owned_path(
                        spool / item["sha256"], gateway.root
                    ):
                        raise ValueError("Evidence outbox belongs to another attempt")
                work = owned_path(
                    gateway.root / "work" / job["id"] / job["attempt_id"], gateway.root
                )
                if Path(job["project_root"]).absolute() != work:
                    if (
                        not startup
                        or Path(job["project_root"]).absolute() != gateway.root / "startup-work"
                    ):
                        raise ValueError("SDK worktree belongs to another attempt")
                    work = None
                with policy.lock():
                    plan = policy.plan(gateway, job, saved, report.get("metadata", {}))
                    acknowledged_at = prior.get("acknowledged_at", now)
                    base = {
                        "job_id": job["id"],
                        "attempt_id": job["attempt_id"],
                        "report_sequence": row["seq"],
                        "startup_readiness_only": startup,
                        "acknowledged_at": acknowledged_at,
                    }
                    if now - acknowledged_at < keep_seconds:
                        persist(receipt, {**base, "state": "waiting"})
                        continue
                    from .local_logs import LocalLogStore

                    logs = LocalLogStore(journal)
                    hook = getattr(gateway, "local_log_sources", None)
                    sources = hook(job, saved) if hook else {}
                    if (
                        sources
                        or (logs.directory(job["id"], job["attempt_id"]) / "manifest.json").exists()
                    ):
                        if retired:
                            db.execute(
                                "INSERT OR IGNORE INTO local_log_attempts(job_id,attempt_id,snapshot) VALUES(?,?,?)",
                                (job["id"], job["attempt_id"], json.dumps(row)),
                            )
                        logs.archive(job["id"], job["attempt_id"], sources, retired=retired)
                    removed = 0
                    for target in plan.targets:
                        removed += prune_directory(target.path, target.keep)
                        if target.after_cleanup:
                            target.after_cleanup(base, now)
                    if work is not None:
                        paths = (
                            [work]
                            if policy.clear_worktree
                            else [
                                owned_path(work / name, gateway.root)
                                for name in (".evaluation-artifacts", ".behavior-artifacts")
                            ]
                        )
                        for path in paths:
                            removed += prune_directory(path, frozenset())
                            if path.exists():
                                path.rmdir()
                    removed += prune_directory(spool, frozenset())
                    if spool.exists():
                        spool.rmdir()
                    lease = owned_path(
                        worker_directory(journal, job["id"], job["attempt_id"]), gateway.root
                    )
                    removed += prune_directory(lease, frozenset())
                    if lease.exists():
                        lease.rmdir()
                    persist(
                        receipt,
                        {
                            **base,
                            "state": "cleaned",
                            "cleaned_at": now,
                            "removed_bytes": removed,
                            "external_jobs": plan.external_jobs,
                        },
                    )
                    if retired:
                        db.execute(
                            "DELETE FROM retired_attempts WHERE job_id=? AND attempt_id=?",
                            (job["id"], job["attempt_id"]),
                        )
                    totals["cleaned"] += 1
                    totals["removed_bytes"] += removed
        except (OSError, ValueError, KeyError, TypeError):
            # A failed partial cleanup is retried; no evaluation or report is replayed.
            totals["errors"] += 1
    return totals


def collect_finished(
    gateway,
    *,
    policy: EvidencePolicy | None = None,
    keep_seconds: float = 0,
    now: float | None = None,
) -> dict:
    """Collect uploaded attempts while preserving durable recovery and file ownership.

    Args:
        gateway: SDK gateway owning its journal, workspaces and artifact outbox.
        policy: Optional backend restoration and external-evidence checks.
        keep_seconds: Debugging delay after final upload acknowledgement.
        now: Optional deterministic collection time.

    Returns:
        Counts of cleaned attempts, removed file bytes and deferred errors.
    """
    totals = {"cleaned": 0, "removed_bytes": 0, "errors": 0}
    if policy is None and getattr(gateway.adapter, "manages_evidence_retention", False):
        return totals
    policy = EvidencePolicy() if policy is None else policy
    filenames = ("jobs.sqlite3", "startup.sqlite3") if policy.include_startup else ("jobs.sqlite3",)
    for filename in filenames:
        path = gateway.root / filename
        if path.is_file():
            values = collect_journal(
                gateway,
                Journal(path),
                policy,
                startup=filename.startswith("startup"),
                keep_seconds=keep_seconds,
                now=time.time() if now is None else now,
            )
            for key, value in values.items():
                totals[key] += value
    return totals


def collect_local_logs(
    gateway,
    journal: Journal,
    *,
    policy: EvidencePolicy | None = None,
    keep_seconds: float = 604800,
    now: float | None = None,
) -> dict:
    """Expire private logs only after optional backend restoration is confirmed.

    Args:
        gateway: Owner of accepted tasks and optional backend resources.
        journal: Main task journal or isolated startup journal.
        policy: Existing resource restoration policy, reused without deleting evidence.
        keep_seconds: Independent local-log retention, normally seven days.
        now: Optional deterministic collection time.

    Returns:
        Local-log expiration counts; no artifacts or upload acknowledgements change.
    """
    from .local_logs import LocalLogStore

    if policy is None and getattr(gateway.adapter, "manages_evidence_retention", False):
        return {"cleaned": 0, "errors": 0, "removed_bytes": 0}

    @contextmanager
    def restored(row):
        if policy is None:
            uncertain = (
                row.get("superseded_at") is not None
                and row["state"] not in {"completed", "failed", "cancelled"}
                and bool(json.loads(row["saved"]))
            )
            yield not uncertain
            return
        with policy.lock():
            getattr(policy, "restoration_plan", policy.plan)(
                gateway,
                json.loads(row["job"]),
                json.loads(row["saved"]),
                json.loads(row["report"]).get("metadata", {}),
            )
            yield True

    return LocalLogStore(journal).collect(keep_seconds=keep_seconds, now=now, guard=restored)
