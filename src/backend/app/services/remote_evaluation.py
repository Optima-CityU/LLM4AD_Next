"""Authorized durable dispatch for outbound remote evaluation gateways."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from llm4ad.remote.contracts import EvaluationContract
from pydantic import ValidationError
from sqlalchemy import update
from sqlmodel import Session, select

from app.core.config import settings
from app.models import EvaluationGateway, Project, RemoteEvaluationJob, Task, TaskStatus

TERMINAL = {"completed", "failed", "cancelled"}
ACTIVE = {"accepted", "running", "recovery_required", "cancel_requested"}


def digest(value: str) -> str:
    """Hash an opaque locally generated recovery credential."""
    return hashlib.sha256(value.encode()).hexdigest()


def contract_key(contract: dict) -> str:
    """Compare declared evaluator/data versions and metric semantics."""
    normalized = EvaluationContract.model_validate(contract).model_dump(mode="json")
    normalized["metrics"].sort(key=lambda item: item["name"])
    return digest(json.dumps(normalized, sort_keys=True))


def runtime_token(task_id: uuid.UUID | str) -> str:
    """Issue a task-only evaluation channel without exposing model credentials."""
    return hmac.new(settings.SECRET_KEY.encode(), f"remote-evaluation:{task_id}".encode(), hashlib.sha256).hexdigest()


def authorized_gateway(db: Session, gateway_id: uuid.UUID, token: str) -> EvaluationGateway:
    """Verify the gateway's durable recovery identity."""
    gateway = db.get(EvaluationGateway, gateway_id)
    if gateway is None or not hmac.compare_digest(gateway.secret_hash, digest(token)):
        raise HTTPException(401, "Invalid gateway identity")
    return gateway


def runtime_task(db: Session, task_id: uuid.UUID, token: str) -> Task:
    """Limit runtime operations to an active platform task."""
    task = db.get(Task, task_id)
    if task is None or not hmac.compare_digest(runtime_token(task_id), token):
        raise HTTPException(401, "Invalid remote evaluation channel")
    if task.status not in {TaskStatus.RUNNING, TaskStatus.PENDING}:
        raise HTTPException(409, "Task is no longer active")
    return task


def validate_pool(db: Session, user_id: uuid.UUID, evaluator: dict) -> dict:
    """Validate selected identities, visibility and comparable scoring."""
    try:
        ids = list(dict.fromkeys(str(uuid.UUID(value)) for value in evaluator.get("gateway_ids", [])))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(422, "Gateway IDs must be UUIDs") from exc
    if not ids:
        raise HTTPException(422, "Select at least one remote evaluation gateway")
    gateways = [db.get(EvaluationGateway, uuid.UUID(value)) for value in ids]
    if any(g is None or str(user_id) not in g.allowed_user_ids for g in gateways):
        raise HTTPException(403, "A selected gateway is not available to this user")
    keys = {g.contract_key for g in gateways}
    if len(keys) != 1:
        raise HTTPException(422, "Selected gateways use incompatible evaluation contracts")
    try:
        if evaluator.get("contract") and contract_key(evaluator["contract"]) not in keys:
            raise HTTPException(422, "The selected evaluator contract changed; review the task configuration")
    except ValidationError as exc:
        raise HTTPException(422, "Invalid evaluation contract") from exc
    return gateways[0].contract


def is_online(gateway: EvaluationGateway) -> bool:
    """Interpret a stale heartbeat as offline without changing job ownership."""
    return (datetime.now(UTC) - gateway.last_seen.replace(tzinfo=UTC)).total_seconds() <= 30


def job_payload(job: RemoteEvaluationJob, include_result: bool = False) -> dict:
    """Return runtime metadata, excluding storage internals and credentials."""
    report = {key: value for key, value in job.report.items() if key != "result_key"}
    if include_result and job.state in {"completed", "failed", "cancelled"} and job.report.get("result_key"):
        from app.core.storage import storage

        report = json.loads(storage.download(job.report["result_key"]))
        if job.state in {"failed", "cancelled"}:
            report.update({name: job.report[name] for name in ("message", "error_type") if name in job.report})
    return {
        **job.request,
        "id": str(job.id),
        "attempt_id": str(job.attempt_id),
        "task_id": str(job.task_id),
        "state": job.state,
        "report": report,
        "sequence": job.sequence,
    }


def claim(db: Session, gateway_id: uuid.UUID) -> RemoteEvaluationJob | None:
    """Atomically reserve capacity and claim one eligible queued candidate."""
    gateway = db.exec(select(EvaluationGateway).where(EvaluationGateway.id == gateway_id).with_for_update()).one()
    db.refresh(gateway, with_for_update=True)
    active = db.exec(
        select(RemoteEvaluationJob).where(
            RemoteEvaluationJob.gateway_id == gateway.id, RemoteEvaluationJob.state.in_(ACTIVE)
        )
    ).all()
    if len(active) >= gateway.capacity:
        return None
    for job in db.exec(
        select(RemoteEvaluationJob)
        .where(RemoteEvaluationJob.state == "queued", RemoteEvaluationJob.contract_key == gateway.contract_key)
        .order_by(RemoteEvaluationJob.created_at)
    ):
        if str(gateway.id) not in job.selected_gateway_ids or str(job.user_id) not in gateway.allowed_user_ids:
            continue
        task = db.get(Task, job.task_id)
        if task is None or task.status not in {TaskStatus.RUNNING, TaskStatus.PENDING}:
            continue
        result = db.execute(
            update(RemoteEvaluationJob)
            .where(RemoteEvaluationJob.id == job.id, RemoteEvaluationJob.state == "queued")
            .values(state="accepted", gateway_id=gateway.id, updated_at=datetime.now(UTC))
        )
        if result.rowcount:
            db.commit()
            db.refresh(job)
            return job
    db.commit()
    return None


def cancel_task_jobs(db: Session, task_id: uuid.UUID) -> None:
    """Cancel pending work and request cancellation without inventing confirmation."""
    for job in db.exec(
        select(RemoteEvaluationJob).where(
            RemoteEvaluationJob.task_id == task_id, RemoteEvaluationJob.state.not_in(TERMINAL)
        )
    ):
        job.state = "cancelled" if job.state == "queued" else "cancel_requested"
        db.add(job)
    db.commit()


def task_owner(db: Session, task: Task) -> uuid.UUID:
    """Resolve task ownership from the canonical project relationship."""
    return db.get(Project, task.project_id).user_id
