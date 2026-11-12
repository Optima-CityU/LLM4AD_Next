"""Persist local and remote evaluation evidence using task-scoped manifests."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from botocore.exceptions import ClientError
from llm4ad.evaluator.models import EvaluationFile, EvaluationRecord, artifact_media_type
from llm4ad.evaluator.records import (
    atomic_json,
    json_safe,
    open_owned_file,
    prune_owned_bulk,
    read_owned_file,
    regular_file,
)
from loguru import logger
from sqlmodel import Session, select

from app.core.config import settings
from app.core.env_detect import get_project_home
from app.core.storage import storage
from app.models import EvaluationGateway, Project, RemoteEvaluationJob, Task, TaskLog, TaskStatus
from app.schemas.evaluation import EvaluationSummary


def prefix(task_id) -> str:
    """Return the exclusive storage namespace of a task version."""
    return f"evaluation-data/{uuid.UUID(str(task_id))}/"


def outbox_root(task_id, user_id) -> Path:
    """Locate durable pending data outside the disposable task run directory."""
    home = get_project_home(settings.HOST_PROJECT_HOME, settings.DOCKER_PROJECT_HOME)
    return Path(home) / f"code_user-{uuid.UUID(str(user_id))}" / ".evaluation-outbox" / str(uuid.UUID(str(task_id)))


def summary_for(record: EvaluationRecord, *, pending: bool = False) -> EvaluationSummary:
    """Build a small public index while keeping full details in object storage."""
    return EvaluationSummary(
        **record.model_dump(exclude={"result", "artifacts"}),
        score=record.result.get("score") if record.result.get("success", True) else None,
        artifact_count=len(record.artifacts),
        transfer_pending=pending,
    )


def record_key(task_id, record_id: str) -> str:
    """Derive a manifest key from validated identities."""
    return prefix(task_id) + f"records/{uuid.UUID(record_id)}.json"


def index_key(task_id, record: EvaluationRecord) -> str:
    """Encode filterable attributes into the lightweight index filename."""
    timestamp = re.sub(r"[^0-9]", "", record.created_at)
    algorithm_id = record.algorithm_id or "none"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", algorithm_id):
        raise ValueError("Invalid candidate identity")
    profile = record.evaluation_profile if record.evaluation_profile in {"standard", "elite"} else "standard"
    return prefix(task_id) + f"index/{timestamp}__{profile}__{algorithm_id}__{uuid.UUID(record.id)}.json"


def publish(record: EvaluationRecord, objects: dict[str, str]) -> None:
    """Commit the manifest before making a record discoverable or acknowledging it."""
    record = record.model_copy(deep=True)
    record.result = json_safe(record.result)
    record.storage_state = "error" if record.storage_errors else "saved"
    envelope = {"version": 1, "record": record.model_dump(mode="json"), "objects": objects}
    storage.upload(
        record_key(record.task_id, record.id),
        json.dumps(envelope, ensure_ascii=False).encode(),
        content_type="application/json",
    )
    storage.upload(
        index_key(record.task_id, record),
        summary_for(record).model_dump_json().encode(),
        content_type="application/json",
    )


def upload_pending(root: Path, task_id: str, *, task_status: TaskStatus | None = None) -> bool:
    """Upload stable snapshots idempotently; retain unacknowledged files on failure.

    Args:
        root: Platform-owned outbox for exactly one task version.
        task_id: Task identity supplied by the worker, not by evaluator JSON.
        task_status: Authoritative database state, if supplied by task recovery.

    Returns:
        Whether every currently published terminal record was acknowledged.
    """
    complete = True
    if root.is_symlink():
        raise ValueError("Outbox must not be a symlink")
    for path in root.glob("*/record.json"):
        try:
            regular_file(path, root)
            raw = read_owned_file(path, root)
            digest = hashlib.sha256(raw).hexdigest()
            receipt = path.parent / "ack.json"
            if receipt.exists() and json.loads(read_owned_file(receipt, root)).get("sha256") == digest:
                continue
            record = EvaluationRecord.model_validate_json(raw)
            if str(uuid.UUID(record.id)) != path.parent.name:
                raise ValueError("Outbox identity mismatch")
            record.task_id = str(uuid.UUID(task_id))
            if record.state == "running":
                if task_status not in {TaskStatus.COMPLETED, TaskStatus.FAILED} or record.origin != "local":
                    # A live task or remote gateway retains ownership of its unfinished record.
                    continue
                metadata = record.result.get("metadata")
                record.result = {
                    "metrics": {},
                    **record.result,
                    "success": False,
                    "error_message": "Evaluation interrupted before a terminal result was recorded",
                    "metadata": {
                        **(metadata if isinstance(metadata, dict) else {}),
                        "failure_type": "EvaluationInterrupted",
                    },
                }
                record.state = "failed"
                record.storage_state = "error" if record.storage_errors else "pending"
                atomic_json(path, record.model_dump(mode="json"), owned_root=root)
                digest = hashlib.sha256(read_owned_file(path, root)).hexdigest()
            objects = {}
            for item in record.artifacts:
                if not re.fullmatch(r"[a-f0-9]{64}", item.sha256):
                    raise ValueError("Invalid artifact digest")
                filename = regular_file(path.parent / "files" / item.sha256, root)
                content_hash = hashlib.sha256()
                with open_owned_file(filename, root) as stream:
                    while chunk := stream.read(1024 * 1024):
                        content_hash.update(chunk)
                if filename.stat().st_size != item.size or content_hash.hexdigest() != item.sha256:
                    raise ValueError("Artifact checksum mismatch")
                key = prefix(task_id) + f"files/{record.id}/{item.sha256}"
                with open_owned_file(filename, root) as stream:
                    storage.upload(key, stream, content_type="application/octet-stream")
                objects[item.id] = key
            publish(record, objects)
            atomic_json(receipt, {"sha256": digest}, owned_root=root)
            # Keep the small record and ACK; only immutable uploaded bulk is removed.
            if not record.storage_errors:
                prune_owned_bulk(path.parent, root)
        except Exception:
            complete = False
            try:
                atomic_json(
                    path.parent / "transfer-error.json",
                    {"message": "File transfer failed; retry scheduled"},
                    owned_root=root,
                )
            except (OSError, ValueError):
                pass
            logger.exception("Evaluation data remains pending for task {}", task_id)
    return complete


def remote_record(
    job: RemoteEvaluationJob,
    result: dict | None = None,
    identity: dict | None = None,
) -> tuple[EvaluationRecord, dict[str, str]]:
    """Adapt current and legacy remote results without copying stored file bytes."""
    request = {**(job.request or {}), **(identity or {})}
    record = EvaluationRecord(
        id=str(job.attempt_id),
        task_id=str(job.task_id),
        algorithm_id=request.get("algorithm_id", ""),
        algorithm_name=request.get("algorithm_name", ""),
        source_fingerprint=request.get("source_fingerprint", ""),
        execution_id=request.get("execution_id", ""),
        evaluation_profile=request.get("evaluation_profile", "standard"),
        instance_id=request.get("instance_id", ""),
        origin="remote",
        state=job.state,
        created_at=job.created_at.replace(tzinfo=UTC).isoformat(),
        storage_state="saved"
        if result
        else ("unavailable" if job.state in {"completed", "failed", "cancelled"} else "pending"),
        remote_job_id=str(job.id),
        remote_attempt_id=str(job.attempt_id),
    )
    objects = {}
    if result is None:
        record.result = {
            name: job.report[name]
            for name in ("score", "metrics", "success", "duration_ms", "error_message")
            if name in job.report
        }
        if record.state == "completed" and record.result.get("success") is False:
            record.state = "failed"
        if (
            record.state in {"failed", "cancelled"}
            and not record.result.get("error_message")
            and job.report.get("message")
        ):
            record.result["error_message"] = job.report["message"]
    if result is not None:
        record.result = json_safe(result.copy())
        if record.state == "completed" and result.get("success") is False:
            record.state = "failed"
        record.result.pop("artifacts", None)
        record.result["metadata"] = dict(record.result.get("metadata", {}))
        manifests = record.result["metadata"].pop("artifacts", [])
        descriptions = record.result["metadata"].pop("artifact_descriptions", {})
        record.storage_errors = [str(error) for error in record.result["metadata"].pop("artifact_errors", [])]
        if record.storage_errors:
            record.storage_state = "error"
        record.result["metadata"].pop("all_behavior", None)
        record.result.pop("behavior", None)
        for item in manifests:
            digest = item.get("sha256", "")
            expected = f"remote-evaluation/{job.task_id}/{job.id}/artifacts/{job.attempt_id}/{digest}"
            if not re.fullmatch(r"[a-f0-9]{64}", digest) or item.get("object_key") != expected:
                raise ValueError("Remote evidence is outside the assigned execution attempt")
            identity = hashlib.sha256(f"{item['name']}|{digest}".encode()).hexdigest()
            record.artifacts.append(
                EvaluationFile(
                    id=identity,
                    name=item["name"],
                    sha256=digest,
                    size=item["size"],
                    media_type=artifact_media_type(item["name"], item.get("media_type", "application/octet-stream")),
                    description=item.get("description", descriptions.get(item["name"], "")),
                )
            )
            objects[identity] = expected
    return record, objects


def legacy_result(job: RemoteEvaluationJob) -> dict | None:
    """Read historical full results only from the expected remote object key."""
    key = (job.report or {}).get("result_key")
    expected = f"remote-evaluation/{job.task_id}/{job.id}/result-{job.attempt_id}.json"
    if key == expected:
        try:
            return json.loads(storage.download(key))
        except (KeyError, FileNotFoundError):
            return None
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"NoSuchKey", "404", "NotFound"}:
                raise
    return None


def remote_summary(db: Session, job: RemoteEvaluationJob, identity: dict | None = None) -> EvaluationSummary:
    """Preserve existing gateway connectivity and progress in the common list."""
    record, _ = remote_record(job, identity=identity)
    summary = summary_for(record)
    gateway = db.get(EvaluationGateway, job.gateway_id) if job.gateway_id else None
    summary.gateway_name = gateway.name if gateway else None
    summary.gateway_online = (
        (datetime.now(UTC) - gateway.last_seen.replace(tzinfo=UTC)).total_seconds() <= 30 if gateway else None
    )
    summary.progress = job.report or {}
    summary.progress = {key: value for key, value in summary.progress.items() if key != "result_key"}
    if summary.gateway_online is False and job.state not in {"completed", "failed", "cancelled"}:
        summary.state = "reconnecting"
    summary.score = None if (job.report or {}).get("success") is False else (job.report or {}).get("score")
    return summary


def legacy_identities(db: Session, task_id, jobs: list[RemoteEvaluationJob]) -> dict[str, dict]:
    """Recover old candidate links from saved results without rewriting history."""
    wanted = {str(job.id) for job in jobs if not (job.request or {}).get("algorithm_id")}
    if not wanted:
        return {}
    links, ambiguous = {}, set()
    for payload in db.exec(select(TaskLog.data).where(TaskLog.task_id == task_id, TaskLog.type == "generated")):
        if not isinstance(payload, dict) or not payload.get("id"):
            continue
        metadata = payload.get("custom_metadata") or {}
        if not isinstance(metadata, dict):
            continue
        profiles = metadata.get("evaluation_profiles") or {}
        results = list(profiles.values()) if isinstance(profiles, dict) else []
        full = metadata.get("evaluation_result")
        if isinstance(full, dict):
            results.append({"evidence": full.get("metadata", {})})
        for result in results:
            if not isinstance(result, dict):
                continue
            evidence = result.get("evidence") or {}
            job_id = evidence.get("remote_job_id") if isinstance(evidence, dict) else None
            if job_id not in wanted:
                continue
            identity = {
                "algorithm_id": str(payload["id"]),
                "algorithm_name": str(payload.get("name", "")),
                "source_fingerprint": str(result.get("source_fingerprint", "")),
            }
            if job_id in links and links[job_id]["algorithm_id"] != identity["algorithm_id"]:
                ambiguous.add(job_id)
            links[job_id] = identity
    return {job_id: identity for job_id, identity in links.items() if job_id not in ambiguous}


def pending_records(root: Path, *, include_saved: bool = False) -> list[EvaluationRecord]:
    """Read small local records for running evaluations and pending uploads."""
    records = []
    for path in root.glob("*/record.json"):
        try:
            record = EvaluationRecord.model_validate_json(read_owned_file(path, root))
            receipt = path.parent / "ack.json"
            acknowledged = (
                receipt.exists()
                and json.loads(read_owned_file(receipt, root)).get("sha256")
                == hashlib.sha256(read_owned_file(path, root)).hexdigest()
            )
            if acknowledged and not include_saved:
                continue
            if acknowledged:
                record.storage_state = "saved"
            elif (path.parent / "transfer-error.json").is_file():
                record.storage_state = "error"
                record.storage_errors.append("File transfer failed; retry scheduled")
            records.append(record)
        except (OSError, ValueError):
            logger.warning("Ignoring unreadable pending evaluation record")
    return records


def task_outbox(db: Session, task: Task) -> Path:
    """Resolve a task's owned spool using its project owner."""
    project = db.get(Project, task.project_id)
    return outbox_root(task.id, project.user_id)


def retry_outboxes() -> bool:
    """Recover pending transfers after worker restart without rerunning experiments."""
    from app.core.db import get_db_session

    complete = True
    with get_db_session() as db:
        identities = db.exec(select(Task.id)).all()
    for task_id in identities:
        complete = upload_task_pending(task_id) and complete
    return complete


def upload_task_pending(task_id) -> bool:
    """Serialize transfer with deletion without locking task execution state."""
    from app.core.db import get_db_session

    with get_db_session() as db:
        task = db.get(Task, uuid.UUID(str(task_id)))
        if task is None:
            return True
        root = task_outbox(db, task)
    with evidence_lease(root), get_db_session() as db:
        task = db.get(Task, uuid.UUID(str(task_id)))
        if task is None:
            return True
        return upload_pending(root, str(task_id), task_status=task.status)


@contextmanager
def evidence_lease(root: Path):
    """Hold an OS-released transfer lock outside the evaluator's mounted directory."""
    locks = root.parent / ".locks"
    locks.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(locks / f"{uuid.UUID(root.name)}.sqlite3", timeout=0)
    try:
        deadline = time.monotonic() + 300
        while True:
            try:
                connection.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                # A SQLite busy timeout would block the gevent worker's event loop.
                time.sleep(0.05)
        yield
    finally:
        connection.close()


def cleanup_task_evidence(task_id, root: Path) -> None:
    """Reuse the same cleanup for task-version and containing-project deletion."""
    with evidence_lease(root):
        for namespace in (prefix(task_id), f"remote-evaluation/{uuid.UUID(str(task_id))}/"):
            try:
                for key in storage.list_objects(prefix=namespace):
                    storage.delete(key)
            except Exception:
                logger.exception("Could not remove evaluation objects for deleted task {}", task_id)
        if not root.is_symlink():
            shutil.rmtree(root, ignore_errors=True)
