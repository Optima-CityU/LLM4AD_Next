"""Authorized durable dispatch for outbound remote evaluation gateways."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from llm4ad.remote.contracts import EvaluationContract
from pydantic import ValidationError
from sqlalchemy import func, update
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
    mode = selection_mode(evaluator)
    if mode == "auto":
        contract = evaluator.get("contract")
        if not contract and ids:
            selected = db.get(EvaluationGateway, uuid.UUID(ids[0]))
            if selected and str(user_id) in selected.allowed_user_ids:
                contract = selected.contract
        try:
            normalized = EvaluationContract.model_validate(contract or {}).model_dump(mode="json")
        except (ValidationError, TypeError, ValueError) as exc:
            raise HTTPException(422, "Choose an evaluation method and benchmark for the automatic pool") from exc
        # A valid benchmark can wait for an authorized future pool member.
        # Authorization is checked again against every actual claimant.
        return normalized
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


def selection_mode(evaluator: dict) -> str:
    """Preserve legacy explicit lists while defaulting new pools to automatic."""
    mode = evaluator.get("selection_mode", "selected" if evaluator.get("gateway_ids") else "auto")
    if not isinstance(mode, str) or mode not in {"auto", "selected"}:
        raise HTTPException(422, "Unknown remote evaluation selection mode")
    return mode


def eligible(job: RemoteEvaluationJob, gateway: EvaluationGateway, *, online: bool = True) -> bool:
    """Resolve authorization and comparison semantics independently of phone IDs."""
    return (
        (job.selection_mode == "auto" or str(gateway.id) in job.selected_gateway_ids)
        and str(job.user_id) in gateway.allowed_user_ids
        and gateway.contract_key == job.contract_key
        and (not job.request.get("managed_attempt") or gateway.protocol_version >= 2)
        and gateway.ready
        and (not online or is_online(gateway))
    )


def failure_category(report: dict) -> str:
    """Distinguish invalid candidates from infrastructure and evaluator errors."""
    metadata = report.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    category = metadata.get("failure_category")
    if isinstance(category, str) and category in {"candidate", "infrastructure", "evaluator", "configuration"}:
        return category
    if metadata.get("candidate_failure") is True:
        return "candidate"
    if metadata.get("infrastructure_failure") is True:
        return "infrastructure"
    return "evaluator"


def finish_attempt(db: Session, job: RemoteEvaluationJob, report: dict, category: str, *, now=None) -> bool:
    """Fence one failed device attempt and either requeue or exhaust its budget.

    Args:
        db: Session holding the job row lock.
        job: Original logical candidate evaluation.
        report: Diagnostic failure, never a fabricated candidate score.
        category: Candidate, infrastructure, evaluator or configuration failure.
        now: Optional UTC observation time for deterministic lease checks.

    Returns:
        Whether another device attempt remains eligible.
    """
    now = now or datetime.now(UTC)
    message = report.get("error_message") or report.get("message") or category
    history = [*job.attempt_history, attempt_entry(db, job, "failed", category, str(message), now=now)]
    retry = category in {"infrastructure", "evaluator"} and job.attempt_count < job.max_attempts
    task = db.get(Task, job.task_id)
    cancelled = job.state == "cancel_requested" or task is None or task.status not in {TaskStatus.RUNNING, TaskStatus.PENDING}
    retry = retry and not cancelled
    job.attempt_history, job.lease_expires_at = history, None
    if retry:
        job.attempt_id, job.gateway_id, job.sequence = uuid.uuid4(), None, -1
        job.state = "queued"
        job.report = {"message": "Retrying evaluation on an available compatible device", "metadata": {
            "failure_category": category, "attempt_history": history,
            "attempt_count": job.attempt_count, "max_attempts": job.max_attempts,
        }}
    else:
        job.state = "cancelled" if cancelled else "failed"
        job.report = {
            "metrics": {}, **report, "success": False,
            "error_message": str(message), "message": str(message),
            "metadata": {**report.get("metadata", {}), "failure_category": category,
                         "infrastructure_failure": category == "infrastructure", "candidate_failure": category == "candidate",
                         "attempts_exhausted": not cancelled and category in {"infrastructure", "evaluator"},
                         "attempt_history": history, "attempt_count": job.attempt_count, "max_attempts": job.max_attempts},
        }
    job.updated_at = now
    db.add(job)
    return retry


def attempt_entry(db: Session, job: RemoteEvaluationJob, state: str, category=None, message="", *, now=None) -> dict:
    """Return compact durable provenance for the original attempt before fencing."""
    gateway = db.get(EvaluationGateway, job.gateway_id) if job.gateway_id else None
    return {
        "attempt_id": str(job.attempt_id), "attempt": job.attempt_count,
        "state": state,
        "gateway_id": str(job.gateway_id) if job.gateway_id else None,
        "gateway_name": gateway.name if gateway else None,
        "started_at": job.request.get("attempt_started_at"), "finished_at": (now or datetime.now(UTC)).isoformat(),
        "failure_category": category, "message": str(message),
    }


def expire_attempt(db: Session, job: RemoteEvaluationJob, *, now=None) -> bool:
    """Retire expired authority without assuming an offline device was restored."""
    now = now or datetime.now(UTC)
    if job.state not in ACTIVE or job.lease_expires_at is None or job.lease_expires_at.replace(tzinfo=UTC) > now:
        return False
    prior = job.attempt_id
    finish_attempt(db, job, {
        "message": "Device attempt lease expired before its gateway renewed authority",
        "error_type": "EvaluationLeaseExpired", "metadata": {"failure_category": "infrastructure"},
    }, "infrastructure", now=now)
    if job.attempt_id == prior:
        job.attempt_id = uuid.uuid4()
    return True


def expire_leases(db: Session, *, task_id=None, gateway_id=None) -> None:
    """Reconcile elapsed leases using existing row locks and attempt fencing."""
    now = datetime.now(UTC)
    query = select(RemoteEvaluationJob).where(
        RemoteEvaluationJob.state.in_(ACTIVE), RemoteEvaluationJob.lease_expires_at <= now,
    )
    if task_id is not None:
        query = query.where(RemoteEvaluationJob.task_id == task_id)
    if gateway_id is not None:
        query = query.where(RemoteEvaluationJob.gateway_id == gateway_id)
    for job in db.exec(query.with_for_update(skip_locked=True).execution_options(populate_existing=True)).all():
        expire_attempt(db, job, now=now)


def lock_job(db: Session, job_id: uuid.UUID) -> RemoteEvaluationJob | None:
    """Read current ownership under a lock before an expiry or publication check."""
    return db.exec(select(RemoteEvaluationJob).where(RemoteEvaluationJob.id == job_id)
                   .with_for_update().execution_options(populate_existing=True)).first()


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
    if job.request.get("managed_attempt"):
        report = {**report, "metadata": {**report.get("metadata", {}),
                  **job.report.get("metadata", {}), "attempt_history": job.attempt_history,
                  "attempt_count": job.attempt_count, "max_attempts": job.max_attempts}}
        if report.get("success") is False:
            report.pop("score", None)
    return {
        **job.request,
        "id": str(job.id),
        "attempt_id": str(job.attempt_id),
        "task_id": str(job.task_id),
        "state": job.state,
        "report": report,
        "sequence": job.sequence,
        **({
            "managed_attempt": True, "dispatch_attempt": job.attempt_count,
            "dispatch_max_attempts": job.max_attempts,
            "lease_expires_at": job.lease_expires_at.isoformat() if job.lease_expires_at else None,
            "lease_remaining": max(0, (job.lease_expires_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds())
            if job.lease_expires_at else 0,
        } if job.request.get("managed_attempt") else {}),
    }


def claim(db: Session, gateway_id: uuid.UUID) -> RemoteEvaluationJob | None:
    """Atomically reserve capacity and claim one eligible queued candidate."""
    expire_leases(db)
    gateway = db.exec(select(EvaluationGateway).where(EvaluationGateway.id == gateway_id).with_for_update()).one()
    db.refresh(gateway, with_for_update=True)
    gateway.last_seen = datetime.now(UTC)
    db.add(gateway)
    if not gateway.ready or not is_online(gateway):
        db.commit()
        return None
    active = db.exec(
        select(RemoteEvaluationJob).where(
            RemoteEvaluationJob.gateway_id == gateway.id, RemoteEvaluationJob.state.in_(ACTIVE)
        )
    ).all()
    if len(active) >= gateway.capacity:
        db.commit()
        return None
    gateways = db.exec(select(EvaluationGateway)).all()
    loads = dict(db.exec(select(RemoteEvaluationJob.gateway_id, func.count())
                         .where(RemoteEvaluationJob.state.in_(ACTIVE))
                         .group_by(RemoteEvaluationJob.gateway_id)).all())
    for job in db.exec(
        select(RemoteEvaluationJob)
        .where(RemoteEvaluationJob.state == "queued", RemoteEvaluationJob.contract_key == gateway.contract_key)
        .order_by(RemoteEvaluationJob.created_at)
    ):
        if not eligible(job, gateway):
            continue
        task = db.get(Task, job.task_id)
        if task is None or task.status not in {TaskStatus.RUNNING, TaskStatus.PENDING}:
            continue
        if job.request.get("managed_attempt"):
            counts = {str(g.id): sum(row["gateway_id"] == str(g.id) for row in job.attempt_history) for g in gateways}
            previous = job.attempt_history[-1].get("gateway_id") if job.attempt_history else None
            rank = counts[str(gateway.id)], str(gateway.id) == previous
            best = min((counts[str(g.id)], str(g.id) == previous) for g in gateways
                       if eligible(job, g) and loads.get(g.id, 0) < g.capacity)
            if rank > best:
                continue
            if job.attempt_count >= job.max_attempts:
                finish_attempt(db, job, {"message": "Evaluation attempt budget exhausted"}, "infrastructure")
                continue
        now = datetime.now(UTC)
        values = {"state": "accepted", "gateway_id": gateway.id, "updated_at": now}
        if job.request.get("managed_attempt"):
            values.update(
                attempt_count=RemoteEvaluationJob.attempt_count + 1,
                lease_expires_at=now + timedelta(seconds=job.request["lease_timeout"]),
                request={**job.request, "attempt_started_at": now.isoformat()},
            )
        result = db.execute(
            update(RemoteEvaluationJob)
            .where(RemoteEvaluationJob.id == job.id, RemoteEvaluationJob.state == "queued",
                   RemoteEvaluationJob.attempt_id == job.attempt_id)
            .values(**values)
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
        ).with_for_update()
    ):
        job.state = "cancelled" if job.state == "queued" else "cancel_requested"
        db.add(job)
    db.commit()


def task_owner(db: Session, task: Task) -> uuid.UUID:
    """Resolve task ownership from the canonical project relationship."""
    return db.get(Project, task.project_id).user_id
