"""Read-only browser projections of the local SDK journal and immutable evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import stat
import time
from contextlib import suppress
from pathlib import Path, PurePosixPath

from .journal import Journal

_TERMINAL = {"completed", "failed", "cancelled"}
_PRIVATE_KEYS = {
    "identity",
    "secret",
    "secrets",
    "apikey",
    "authorization",
    "password",
    "token",
    "accesstoken",
    "refreshtoken",
    "credentials",
    "saved",
    "savedstate",
    "recovery",
    "recoverystate",
    "recoveryhandle",
    "environment",
    "fullenvironment",
    "env",
    "environ",
    "projectroot",
    "artifactdir",
    "path",
    "paths",
    "localpath",
    "localpaths",
    "spoolpath",
    "objectkey",
    "objectkeys",
    "evaluationmessages",
}
_VIEW = """
SELECT j.*,
    o.metadata AS observed_metadata,
    CASE
        WHEN j.state='completed' THEN CASE WHEN json_extract(j.report,'$.success')=0 THEN 'failed' ELSE 'success' END
        WHEN j.state IN ('failed','cancelled') THEN j.state
        WHEN j.state='cancel_requested' OR c.stop_requested='cancel' THEN 'cancelling'
        WHEN j.state IN ('accepted','queued','pending') THEN 'pending'
        ELSE j.state
    END AS display_state,
    (j.state IN ('completed','failed','cancelled') AND (j.seq>j.ack OR EXISTS (
        SELECT 1 FROM artifacts a WHERE a.job_id=j.id
        AND a.attempt_id=json_extract(j.job,'$.attempt_id') AND COALESCE(a.object_key,'')=''
    ))) AS pending_upload
FROM jobs j LEFT JOIN execution_controls c
ON c.job_id=j.id AND c.attempt_id=json_extract(j.job,'$.attempt_id')
LEFT JOIN observations o ON o.job_id=j.id AND o.attempt_id=json_extract(j.job,'$.attempt_id')
"""


def _safe(value):
    """Remove private structured fields while retaining useful result metadata."""
    if isinstance(value, dict):
        return {
            key: _safe(item)
            for key, item in value.items()
            if re.sub(r"[^a-z0-9]", "", key.lower()) not in _PRIVATE_KEYS
            and not re.sub(r"[^a-z0-9]", "", key.lower()).endswith(("objectkey", "objectkeys", "localpath", "localpaths"))
        }
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, str) and (value.startswith(("/", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", value)):
        return "[local path]"
    return value


def _pagination(page: int, page_size: int) -> int:
    """Validate positive pagination and return its SQLite offset."""
    if type(page) is not int or type(page_size) is not int or page < 1 or page_size < 1:
        raise ValueError("Page and page_size must be positive integers")
    return (page - 1) * page_size


def _metadata(journal: Journal, row) -> dict:
    """Prefer local lifecycle observations that can outlive terminal report fencing."""
    from .local_logs import LocalLogStore

    report = json.loads(row["report"])
    metadata = {**json.loads(row["observed_metadata"] or "{}"), **report.get("metadata", {})}
    # A replaced attempt or unavailable catalog cannot alter this SQLite snapshot.
    with suppress(OSError, ValueError, LookupError):
        metadata.update(LocalLogStore(journal).lifecycle(row["id"], json.loads(row["job"])["attempt_id"]))
    return metadata


def _item(row, journal: Journal, *, metadata: dict | None = None) -> dict:
    """Project only public list fields from the authoritative current attempt."""
    job, report = json.loads(row["job"]), json.loads(row["report"])
    metadata = _metadata(journal, row) if metadata is None else metadata
    phase = metadata.get("lifecycle")
    phase = phase if isinstance(phase, dict) else {}
    stages = metadata.get("lifecycle_stages") or []
    started, finished = row["started"], row["finished_at"]
    operation_starts = [
        item["started_at"] for item in stages
        if isinstance(item, dict) and item.get("operation_id") == phase.get("operation_id")
        and item.get("stage") == "preparing" and item.get("started_at") is not None
    ]
    if started is None and operation_starts:
        started = min(operation_starts)
    phase_finished = phase.get("finished_at") if phase.get("stage") == "finalizing" else None
    finalizing = phase.get("stage") == "finalizing" and phase.get("status") == "running"
    if finalizing:
        finished = None
    if row["state"] in _TERMINAL and phase_finished is not None:
        finished = max(finished, phase_finished) if finished is not None else phase_finished
    duration = report.get("duration_ms")
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
        duration = None
    elapsed = None
    if started is not None:
        if finished is not None:
            elapsed = max(0, int((finished - started) * 1000))
        elif row["state"] not in _TERMINAL or finalizing:
            elapsed = max(0, int((time.time() - started) * 1000))
    if elapsed is None and type(phase.get("elapsed_ms")) in (int, float):
        elapsed = phase["elapsed_ms"]
        if phase.get("status") == "running" and phase.get("started_at") is not None:
            elapsed += max(0, (time.time() - phase["started_at"]) * 1000 - (phase.get("duration_ms") or 0))
    return {
        "job_id": row["id"],
        "attempt_id": job["attempt_id"],
        "task_id": job.get("task_id", ""),
        "algorithm_name": job.get("algorithm_name", ""),
        "state": row["display_state"],
        "message": _safe(report.get("message") or report.get("error_message") or ""),
        "completed": report.get("completed"),
        "total": report.get("total"),
        "score": report.get("score"),
        "duration_ms": duration,
        "elapsed_ms": elapsed,
        "started_at": started,
        "finished_at": finished,
        "phase": _safe(metadata.get("lifecycle")),
        "lifecycle_stages": _safe(metadata.get("lifecycle_stages", [])),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started": row["started"],
        "pending_upload": bool(row["pending_upload"]),
        "can_cancel": row["state"] not in _TERMINAL and row["display_state"] != "cancelling",
    }


def summary(journal: Journal) -> dict:
    """Count current attempts without touching adapters, readiness or processes.

    Args:
        journal: SDK journal to inspect.

    Returns:
        Counts by display state and total. pending_upload overlaps terminal states
        and counts results whose final report or evidence still needs delivery.
    """
    counts = dict.fromkeys(
        ("pending", "running", "recovery_required", "success", "failed", "cancelled", "cancelling", "pending_upload", "total"), 0
    )
    with journal.connect() as db:
        for state, count, pending in db.execute(
            "SELECT display_state,COUNT(*),SUM(pending_upload) FROM (" + _VIEW + ") GROUP BY display_state"
        ):
            if state in counts:
                counts[state] += count
            counts["pending_upload"] += pending
            counts["total"] += count
    return counts


def list_jobs(journal: Journal, *, page: int = 1, page_size: int = 20, state: str | None = None, search: str = "") -> dict:
    """List current attempts with stable ordering, filtering and literal search.

    Args:
        journal: SDK journal to inspect.
        page: One-based result page.
        page_size: Positive number of rows, also supporting merged-page prefixes.
        state: Display state, legacy journal state alias, or pending_upload.
        search: Case-insensitive literal substring of job, attempt, task or algorithm.

    Returns:
        Public items and the matching total before pagination.
    """
    offset = _pagination(page, page_size)
    filters, values = [], []
    if state:
        state = {"accepted": "pending", "queued": "pending", "completed": "success", "cancel_requested": "cancelling"}.get(
            state, state
        )
        filters.append("pending_upload=1" if state == "pending_upload" else "display_state=?")
        if state != "pending_upload":
            values.append(state)
    if search:
        pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        filters.append(
            "("
            + " OR ".join(
                f"COALESCE({field},'') LIKE ? ESCAPE '\\'"
                for field in (
                    "id",
                    "json_extract(job,'$.attempt_id')",
                    "json_extract(job,'$.task_id')",
                    "json_extract(job,'$.algorithm_name')",
                )
            )
            + ")"
        )
        values.extend([pattern] * 4)
    query = " FROM (" + _VIEW + ")" + (" WHERE " + " AND ".join(filters) if filters else "")
    with journal.connect() as db:
        db.execute("BEGIN")
        db.row_factory = sqlite3.Row
        total = db.execute("SELECT COUNT(*)" + query, values).fetchone()[0]
        rows = db.execute(
            "SELECT *" + query + " ORDER BY COALESCE(created_at,started,0) DESC,id LIMIT ? OFFSET ?",
            [*values, page_size, offset],
        )
        return {"items": [_item(row, journal) for row in rows], "total": total}


def _current(db, job_id, attempt_id):
    """Reject a missing or superseded attempt using the same read snapshot."""
    row = db.execute("SELECT * FROM (" + _VIEW + ") WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise LookupError("Job is not present locally")
    if json.loads(row["job"])["attempt_id"] != attempt_id:
        raise ValueError("Execution attempt is no longer current")
    return row


def _message(row) -> dict:
    """Project a durable event without exposing structured recovery fields."""
    return {
        "sequence": row["id"],
        "created_at": row["created_at"],
        "level": row["level"],
        "kind": row["kind"],
        "message": _safe(row["message"]),
        "data": _safe(json.loads(row["data"])),
    }


def job_messages(
    journal: Journal,
    job_id: str,
    attempt_id: str,
    *,
    after_sequence: int | None = None,
    before_sequence: int | None = None,
    limit: int = 100,
    level: str | None = None,
    search: str = "",
) -> dict:
    """Read bounded event windows for one current attempt using strict cursors.

    Args:
        journal: Exact locally owned task or startup journal.
        job_id: Locally recorded job identifier.
        attempt_id: Current execution attempt to fence every query.
        after_sequence: Read the first matching events strictly after this ID.
        before_sequence: Read the last matching events strictly before this ID.
        limit: Maximum window size, from 1 to 1000.
        level: Optional case-insensitive exact diagnostic level.
        search: Literal case-insensitive substring of the event message.

    Returns:
        Ascending items, filtered total, older/newer flags and first/last IDs.
        Without a cursor, items are the most recent matching events.

    Raises:
        LookupError: The local job does not exist.
        ValueError: The attempt, cursor combination or window size is invalid.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    for cursor in (after_sequence, before_sequence):
        if cursor is not None and (type(cursor) is not int or cursor < 0):
            raise ValueError("Message cursors must be nonnegative integer sequence IDs")
    if after_sequence is not None and before_sequence is not None:
        raise ValueError("Choose after_sequence or before_sequence, not both")
    where, values = ["job_id=?", "attempt_id=?"], [job_id, attempt_id]
    if level:
        where.append("LOWER(level)=LOWER(?)")
        values.append(level)
    if search:
        where.append("COALESCE(message,'') LIKE ? ESCAPE '\\'")
        values.append("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    query = " FROM messages WHERE " + " AND ".join(where)
    window, cursor_values = "", []
    if after_sequence is not None:
        window, cursor_values = " AND id>?", [after_sequence]
    elif before_sequence is not None:
        window, cursor_values = " AND id<?", [before_sequence]
    order = "ASC" if after_sequence is not None else "DESC"
    with journal.connect() as db:
        db.execute("BEGIN")
        db.row_factory = sqlite3.Row
        _current(db, job_id, attempt_id)
        total = db.execute("SELECT COUNT(*)" + query, values).fetchone()[0]
        rows = list(db.execute("SELECT *" + query + window + f" ORDER BY id {order} LIMIT ?", [*values, *cursor_values, limit]))
        if after_sequence is None:
            rows.reverse()
        first, last = (rows[0]["id"], rows[-1]["id"]) if rows else (None, None)
        older = first if first is not None else after_sequence
        newer = last if last is not None else before_sequence
        has_older = older is not None and bool(
            db.execute(
                "SELECT EXISTS(SELECT 1" + query + (" AND id<?)" if rows else " AND id<=?)"), [*values, older]
            ).fetchone()[0]
        )
        has_newer = newer is not None and bool(
            db.execute(
                "SELECT EXISTS(SELECT 1" + query + (" AND id>?)" if rows else " AND id>=?)"), [*values, newer]
            ).fetchone()[0]
        )
        return {
            "items": [_message(row) for row in rows],
            "total": total,
            "has_older": has_older,
            "has_newer": has_newer,
            "first_sequence": first,
            "last_sequence": last,
        }


def _spool_path(journal, job_id, attempt_id, artifact) -> Path:
    """Validate an exact immutable spool location without following any symlink."""
    digest = artifact["sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid artifact hash")
    root = Path(journal.path).absolute().parent
    identity = hashlib.sha256(f"{job_id}|{attempt_id}".encode()).hexdigest()
    expected = root / "artifacts" / identity / digest
    if Path(artifact["path"]) != expected:
        raise ValueError("Artifact is outside its immutable spool")
    for component in (*reversed(expected.parents), expected):
        if component.is_symlink():
            raise ValueError("Artifact path contains a symlink")
    info = expected.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size != artifact["size"]:
        raise ValueError("Artifact is not the expected regular file")
    return expected


def job_detail(
    journal: Journal,
    job_id: str,
    attempt_id: str,
    *,
    messages_page: int = 1,
    messages_page_size: int = 100,
) -> dict:
    """Return a safe job, rich result, paginated messages and evidence availability.

    Args:
        journal: SDK journal to inspect.
        job_id: Locally accepted job identifier.
        attempt_id: Exact current execution attempt.
        messages_page: One-based message page, ordered from oldest to newest.
        messages_page_size: Positive number of messages per page.

    Returns:
        List fields, safe job, report and metadata, finished_at, journal_state, messages,
        messages_total/page/page_size/pages and artifacts with available/uploaded.
        All timestamps are epoch seconds or None.

    Raises:
        LookupError: The job is not present locally.
        ValueError: The attempt is stale or pagination is invalid.
    """
    offset = _pagination(messages_page, messages_page_size)
    with journal.connect() as db:
        db.execute("BEGIN")
        db.row_factory = sqlite3.Row
        row = _current(db, job_id, attempt_id)
        metadata = _metadata(journal, row)
        item, payload, report = _item(row, journal, metadata=metadata), json.loads(row["job"]), json.loads(row["report"])
        observation = db.execute(
            "SELECT metadata FROM observations WHERE job_id=? AND attempt_id=?", (job_id, attempt_id)
        ).fetchone()
        observed = json.loads(observation[0]) if observation else {}
        descriptions = {**observed.get("artifact_descriptions", {}), **metadata.get("artifact_descriptions", {})}
        report = _safe({**report, "metadata": metadata})
        total = db.execute("SELECT COUNT(*) FROM messages WHERE job_id=? AND attempt_id=?", (job_id, attempt_id)).fetchone()[0]
        messages = [
            _message(message)
            for message in db.execute(
                "SELECT * FROM messages WHERE job_id=? AND attempt_id=? ORDER BY id LIMIT ? OFFSET ?",
                (job_id, attempt_id, messages_page_size, offset),
            )
        ]
        artifacts = []
        for artifact in db.execute("SELECT * FROM artifacts WHERE job_id=? AND attempt_id=? ORDER BY name", (job_id, attempt_id)):
            try:
                _spool_path(journal, job_id, attempt_id, artifact)
                available = True
            except (OSError, ValueError):
                available = False
            artifacts.append(
                {
                    **{key: artifact[key] for key in ("name", "sha256", "size", "media_type")},
                    "description": _safe(descriptions.get(artifact["name"], "")),
                    "available": available,
                    "uploaded": bool(artifact["object_key"]),
                }
            )
        return {
            **item,
            "journal_state": row["state"],
            "job": {
                **item,
                **{key: payload[key] for key in ("timeout", "evaluation_profile", "behavior_storage") if key in payload},
            },
            "report": report,
            "metadata": report["metadata"],
            "messages": messages,
            "messages_total": total,
            "messages_page": messages_page,
            "messages_page_size": messages_page_size,
            "messages_pages": (total + messages_page_size - 1) // messages_page_size,
            "artifacts": artifacts,
        }


def artifact_path(journal: Journal, job_id: str, attempt_id: str, name: str) -> Path:
    """Resolve only a whitelisted current-attempt artifact with verified contents.

    Args:
        journal: SDK journal owning the immutable spool.
        job_id: Locally accepted job identifier.
        attempt_id: Exact current execution attempt.
        name: Registered relative artifact name, including any subdirectories.

    Returns:
        Verified regular file under this attempt's immutable hash directory.

    Raises:
        LookupError: The job is missing.
        FileNotFoundError: The registered artifact or local bytes are missing.
        ValueError: The attempt, name, spool location or contents are invalid.
    """
    if not name or "\\" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
        raise ValueError("Artifact name must be a relative evidence path")
    with journal.connect() as db:
        db.execute("BEGIN")
        db.row_factory = sqlite3.Row
        _current(db, job_id, attempt_id)
        artifact = db.execute(
            "SELECT * FROM artifacts WHERE job_id=? AND attempt_id=? AND name=?", (job_id, attempt_id, name)
        ).fetchone()
        if artifact is None:
            raise FileNotFoundError("Artifact is not registered for this attempt")
        path = _spool_path(journal, job_id, attempt_id, artifact)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != artifact["sha256"]:
            raise ValueError("Spooled artifact contents changed")
        return path
