"""Gateway discovery and outbound-client evaluation transport."""

import asyncio
import base64
import binascii
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response
from llm4ad.evaluator.base import EvaluationResult
from llm4ad.remote.contracts import EvaluationContract
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.deps import CurrentUser, SessionDep
from app.api.llm4ad.sse_utils import sse_response
from app.core.db import engine
from app.core.storage import storage
from app.models import EvaluationGateway, RemoteEvaluationJob, Task, TaskStatus
from app.services import remote_evaluation as service

router = APIRouter(prefix="/evaluation-gateways", tags=["llm4ad.evaluation-gateways"])
_NOTIFICATION_INTERVAL = 10


class GatewayView(BaseModel):
    """Public metadata visible only to allowed users."""

    id: uuid.UUID
    name: str
    contract: dict
    contract_key: str
    capacity: int
    online: bool
    last_seen: datetime
    running: int
    queued: int
    protocol_version: int = 1
    ready: bool = True


class JobView(BaseModel):
    """Recent evaluation progress, without runtime channels or storage keys."""

    id: uuid.UUID
    state: str
    gateway_id: uuid.UUID | None
    gateway_name: str | None
    gateway_online: bool
    report: dict
    updated_at: datetime
    attempt_count: int = 0
    max_attempts: int = 1
    failure_category: str | None = None
    attempt_history: list[dict] = Field(default_factory=list)


class Registration(BaseModel):
    """Retry-safe anonymous registration with a CLI-generated recovery identity."""

    id: uuid.UUID
    secret: str = Field(min_length=32, repr=False)
    name: str = Field(min_length=1, max_length=255)
    allowed_user_ids: list[uuid.UUID]
    capacity: int = Field(ge=1)
    contract: EvaluationContract
    protocol_version: Literal[1, 2] = 1
    ready: bool = True


class Heartbeat(BaseModel):
    """Renew authority while advertising actual resource admission readiness."""

    ready: bool = True


class JobSubmission(BaseModel):
    """One candidate submitted through its task-scoped runtime channel."""

    id: uuid.UUID
    gateway_ids: list[uuid.UUID] = Field(default_factory=list)
    dispatch_version: Literal[1, 2] = 1
    selection_mode: Literal["auto", "selected"] = "selected"
    max_attempts: int = Field(default=3, ge=1)
    lease_timeout: float = Field(default=60, gt=0, allow_inf_nan=False)
    contract: EvaluationContract
    timeout: float = Field(gt=0)
    evaluation_profile: Literal["standard", "elite"] = "standard"
    behavior_storage: Literal["rendered", "raw", "none"] = "none"
    parameters: dict = Field(default_factory=dict)
    bundle: str
    algorithm_id: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9_-]*$")
    algorithm_name: str = ""
    source_fingerprint: str = ""
    execution_id: str = ""
    instance_id: str = ""


class JobReport(BaseModel):
    """Monotonic reports fenced to a single execution attempt."""

    attempt_id: uuid.UUID
    sequence: int = Field(ge=0)
    state: str
    report: dict = Field(default_factory=dict)


def token(authorization: str) -> str:
    """Read the opaque bearer token without putting it in a URL."""
    return authorization.removeprefix("Bearer ")


@router.get("", response_model=list[GatewayView])
def list_gateways(db: SessionDep, current_user: CurrentUser):
    """List only gateways explicitly shared with the signed-in user."""
    result = []
    jobs = db.exec(select(RemoteEvaluationJob).where(RemoteEvaluationJob.state.not_in(service.TERMINAL))).all()
    for gateway in db.exec(select(EvaluationGateway)):
        if str(current_user.id) not in gateway.allowed_user_ids:
            continue
        result.append(
            GatewayView(
                id=gateway.id,
                name=gateway.name,
                contract=gateway.contract,
                contract_key=gateway.contract_key,
                capacity=gateway.capacity,
                online=(datetime.now(UTC) - gateway.last_seen.replace(tzinfo=UTC)).total_seconds() <= 30,
                last_seen=gateway.last_seen,
                running=sum(j.gateway_id == gateway.id for j in jobs),
                queued=sum(service.eligible(j, gateway, online=False) and j.state == "queued" for j in jobs),
                protocol_version=gateway.protocol_version,
                ready=gateway.ready,
            )
        )
    return result


@router.post("/register", include_in_schema=False)
def register(body: Registration, db: SessionDep):
    """Register or reconnect the same local identity without an account login."""
    existing = db.exec(select(EvaluationGateway).where(EvaluationGateway.id == body.id).with_for_update()).first()
    if existing is not None:
        service.authorized_gateway(db, body.id, body.secret)
    gateway = existing or EvaluationGateway(
        id=body.id,
        name=body.name,
        secret_hash=service.digest(body.secret),
        allowed_user_ids=[],
        contract={},
        contract_key="",
    )
    new_key = service.contract_key(body.contract.model_dump(mode="json"))
    if existing and existing.contract_key != new_key:
        active = db.exec(
            select(RemoteEvaluationJob).where(
                RemoteEvaluationJob.gateway_id == body.id, RemoteEvaluationJob.state.in_(service.ACTIVE)
            )
        ).first()
        if active:
            raise HTTPException(409, "Finish or reconcile accepted jobs before changing the evaluator contract")
    gateway.name, gateway.capacity = body.name, body.capacity
    gateway.allowed_user_ids = [str(value) for value in body.allowed_user_ids]
    gateway.contract = body.contract.model_dump(mode="json")
    gateway.contract_key = new_key
    gateway.protocol_version, gateway.ready = body.protocol_version, body.ready
    gateway.last_seen = datetime.now(UTC)
    db.add(gateway)
    db.commit()
    return {"id": str(gateway.id)}


@router.post("/{gateway_id}/heartbeat", include_in_schema=False)
def heartbeat(gateway_id: uuid.UUID, db: SessionDep, body: Heartbeat | None = None, authorization: str = Header()):
    """Refresh liveness and reconcile accepted work after any disconnection."""
    gateway = service.authorized_gateway(db, gateway_id, token(authorization))
    service.expire_leases(db, gateway_id=gateway_id)
    jobs = db.exec(
        select(RemoteEvaluationJob).where(
            RemoteEvaluationJob.gateway_id == gateway_id, RemoteEvaluationJob.state.not_in(service.TERMINAL)
        ).with_for_update().execution_options(populate_existing=True)
    ).all()
    renewed = []
    for job in jobs:
        if service.expire_attempt(db, job):
            continue
        task = db.get(Task, job.task_id)
        if task is None or task.status not in {"running", "pending"}:
            job.state = "cancel_requested"
            db.add(job)
        elif job.request.get("managed_attempt"):
            job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=job.request["lease_timeout"])
            db.add(job)
        renewed.append(job)
    # Match claim's job-before-gateway lock order; autoflush must not take the
    # gateway lock before this request has acquired its current attempt rows.
    gateway.last_seen = datetime.now(UTC)
    if body is not None:
        gateway.ready = body.ready
    db.add(gateway)
    db.commit()
    return {"jobs": [service.job_payload(j) for j in renewed]}


@router.post("/{gateway_id}/claim", include_in_schema=False)
def claim(gateway_id: uuid.UUID, db: SessionDep, authorization: str = Header()):
    """Claim one eligible candidate after reconciling expired attempt authority."""
    service.authorized_gateway(db, gateway_id, token(authorization))
    job = service.claim(db, gateway_id)
    return service.job_payload(job) if job else None


@router.get("/{gateway_id}/jobs/{job_id}/bundle", include_in_schema=False)
def bundle(gateway_id: uuid.UUID, job_id: uuid.UUID, db: SessionDep, authorization: str = Header()):
    """Serve only the candidate assigned to this authenticated gateway."""
    gateway = service.authorized_gateway(db, gateway_id, token(authorization))
    job = service.lock_job(db, job_id)
    if job is not None and service.expire_attempt(db, job):
        db.commit()
    if job is None or job.gateway_id != gateway.id or job.state not in {"accepted", "running", "recovery_required"}:
        raise HTTPException(403, "Candidate is not assigned to this gateway")
    task = db.get(Task, job.task_id)
    if task is None or task.status not in {TaskStatus.RUNNING, TaskStatus.PENDING}:
        raise HTTPException(409, "Task is no longer active")
    object_key = job.object_key
    db.commit()
    return Response(storage.download(object_key), media_type="application/zip")


@router.post("/{gateway_id}/jobs/{job_id}/report", include_in_schema=False)
def report(gateway_id: uuid.UUID, job_id: uuid.UUID, body: JobReport, db: SessionDep, authorization: str = Header()):
    """Accept monotonic progress or a validated, idempotent final result."""
    service.authorized_gateway(db, gateway_id, token(authorization))
    job = service.lock_job(db, job_id)
    if job is not None and service.expire_attempt(db, job):
        db.commit()
    if job is None or job.gateway_id != gateway_id or job.attempt_id != body.attempt_id:
        raise HTTPException(409, "This execution attempt is no longer authoritative")
    if job.state in service.TERMINAL or body.sequence <= job.sequence:
        return {"ack": body.sequence, "state": job.state}
    if body.state not in service.ACTIVE | service.TERMINAL:
        raise HTTPException(422, "Unknown evaluation state")
    if job.state == "cancel_requested" and body.state != "cancelled":
        return {"ack": body.sequence, "state": "cancel_requested"}
    if job.request.get("managed_attempt") and body.state == "recovery_required":
        service.finish_attempt(db, job, {
            "metrics": {}, **body.report,
            "error_message": body.report.get("error_message") or body.report.get("message")
            or "Device cleanup is unconfirmed; its resource remains quarantined",
        }, "infrastructure")
        db.commit()
        return {"ack": body.sequence, "state": job.state}
    if not job.request.get("managed_attempt") and body.state == "recovery_required" and body.report.get("retry_confirmed") is True:
        job.attempt_id = uuid.uuid4()
        job.gateway_id = None
        job.sequence = -1
        job.report = {}
        job.state = "queued"
        db.add(job)
        db.commit()
        return {"ack": body.sequence, "state": "queued"}
    payload = body.report
    final_state = body.state
    result = None
    if body.state in {"completed", "failed", "cancelled"}:
        from app.services.evaluation_data import publish, remote_record

        try:
            if body.state != "completed":
                payload = {
                    "metrics": {},
                    **payload,
                    "success": False,
                    "error_message": payload.get("error_message") or payload.get("message") or body.state,
                }
            evaluation = EvaluationResult.model_validate(payload)
            if body.state != "completed":
                evaluation.metadata.setdefault("failure_type", payload.get("error_type", "RemoteExecutionError"))
            result = evaluation.model_dump(mode="json")
            if job.request.get("managed_attempt"):
                unsuccessful = body.state == "failed" or body.state == "completed" and result["success"] is False
                category = service.failure_category(result) if unsuccessful else None
                history = [*job.attempt_history, service.attempt_entry(
                    db, job, "failed" if unsuccessful else body.state, category,
                    result.get("error_message") or "",
                )]
                result["metadata"].update(
                    attempt_history=history, attempt_count=job.attempt_count, max_attempts=job.max_attempts,
                )
                if category:
                    result["metadata"].update(
                        failure_category=category,
                        infrastructure_failure=category == "infrastructure",
                        candidate_failure=category == "candidate",
                        attempts_exhausted=category in {"infrastructure", "evaluator"} and job.attempt_count >= job.max_attempts,
                    )
                    result.pop("score", None)
                    if category != "candidate":
                        result["metrics"] = {}
            result.setdefault("metadata", {})["evaluation_records"] = [{
                "id": str(job.attempt_id), "algorithm_id": job.request.get("algorithm_id", ""), "evaluation_profile": job.request.get("evaluation_profile", "standard"),
                "instance_id": job.request.get("instance_id", ""),
                "source_fingerprint": job.request.get("source_fingerprint", ""),
            }]
            record, objects = remote_record(job, result)
            if any(key not in storage.list_objects(prefix=key) for key in objects.values()):
                raise ValueError("Remote artifact has not been uploaded")
            record.state = "failed" if body.state == "completed" and result.get("success") is False else body.state
            encoded = json.dumps(result, allow_nan=False).encode()
        except (ValidationError, ValueError) as exc:
            raise HTTPException(422, "Invalid evaluation result") from exc
        result_key = f"remote-evaluation/{job.task_id}/{job.id}/result-{job.attempt_id}.json"
        storage.upload(result_key, encoded, content_type="application/json")
        publish(record, objects)
        payload = {key: result[key] for key in ("score", "metrics", "success", "duration_ms") if key in result}
        if body.state != "completed":
            payload.update({key: body.report[key] for key in ("message", "error_type") if key in body.report})
        if result["success"] is False and result.get("error_message"):
            payload["error_message"] = result["error_message"]
            payload.setdefault("message", result["error_message"])
        payload["result_key"] = result_key
    if job.request.get("managed_attempt") and result is not None:
        unsuccessful = body.state == "failed" or body.state == "completed" and result.get("success") is False
        if unsuccessful:
            category = service.failure_category(result)
            retry = service.finish_attempt(db, job, {**payload, "metadata": result.get("metadata", {})}, category)
            if retry:
                db.commit()
                return {"ack": body.sequence, "state": "queued"}
            payload = job.report
            final_state = job.state
        else:
            job.attempt_history = result["metadata"]["attempt_history"]
        job.lease_expires_at = None
    job.state, job.sequence, job.report = final_state, body.sequence, payload
    job.updated_at = datetime.now(UTC)
    db.add(job)
    db.commit()
    return {"ack": body.sequence, "state": job.state}


def artifact_key(job: RemoteEvaluationJob, digest: str) -> str:
    """Derive an attempt-scoped object key without accepting arbitrary storage paths."""
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise HTTPException(422, "Invalid artifact SHA-256")
    return f"remote-evaluation/{job.task_id}/{job.id}/artifacts/{job.attempt_id}/{digest}"


@router.put("/{gateway_id}/jobs/{job_id}/artifacts/{digest}", include_in_schema=False)
async def upload_artifact(
    gateway_id: uuid.UUID,
    job_id: uuid.UUID,
    digest: str,
    request: Request,
    db: SessionDep,
    authorization: str = Header(),
    x_attempt_id: uuid.UUID = Header(),
):
    """Accept retry-safe binary evidence only from the authoritative gateway."""
    service.authorized_gateway(db, gateway_id, token(authorization))
    job = service.lock_job(db, job_id)
    if job is not None and service.expire_attempt(db, job):
        db.commit()
    if job is None or job.gateway_id != gateway_id or job.attempt_id != x_attempt_id:
        raise HTTPException(409, "This execution attempt is no longer authoritative")
    if job.state not in service.ACTIVE | {"completed"}:
        raise HTTPException(409, "Execution no longer accepts evidence")
    key = artifact_key(job, digest)
    # Immutable attempt-scoped objects do not require holding a database lock
    # while receiving bytes; heartbeats must remain able to renew the lease.
    db.commit()
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 64 * 1024 * 1024:
            raise HTTPException(413, "Evidence exceeds 64 MiB")
    if not content or hashlib.sha256(content).hexdigest() != digest:
        raise HTTPException(422, "Evidence checksum mismatch")
    job = service.lock_job(db, job_id)
    if job is not None and service.expire_attempt(db, job):
        db.commit()
    if job is None or job.gateway_id != gateway_id or job.attempt_id != x_attempt_id:
        raise HTTPException(409, "This execution attempt is no longer authoritative")
    if job.state not in service.ACTIVE | {"completed"}:
        raise HTTPException(409, "Execution no longer accepts evidence")
    db.commit()
    storage.upload(key, bytes(content), content_type="application/octet-stream")
    return {"object_key": key, "sha256": digest, "size": len(content)}


@router.get("/tasks/{task_id}/jobs/{job_id}/artifacts/{digest}", include_in_schema=False)
def download_artifact(task_id: uuid.UUID, job_id: uuid.UUID, digest: str, db: SessionDep, current_user: CurrentUser):
    """Return completed evidence only to the task owner and only from its manifest."""
    task = db.get(Task, task_id)
    if task is None or service.task_owner(db, task) != current_user.id:
        raise HTTPException(404, "Task not found")
    job = db.get(RemoteEvaluationJob, job_id)
    if job is None or job.task_id != task_id or not job.report.get("result_key"):
        raise HTTPException(404, "Evidence not found")
    key = artifact_key(job, digest)
    result = json.loads(storage.download(job.report["result_key"]))
    item = next(
        (
            item
            for item in result.get("metadata", {}).get("artifacts", [])
            if item.get("object_key") == key and item.get("sha256") == digest
        ),
        None,
    )
    if item is None:
        raise HTTPException(404, "Evidence not found")
    data = storage.download(key)
    if len(data) != item.get("size") or hashlib.sha256(data).hexdigest() != digest:
        raise HTTPException(409, "Stored evidence checksum mismatch")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", str(item.get("name", "evidence")).rsplit("/", 1)[-1])
    return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/tasks/{task_id}/jobs", include_in_schema=False)
def submit(task_id: uuid.UUID, body: JobSubmission, db: SessionDep, authorization: str = Header()):
    """Persist one retry-safe candidate without exposing platform credentials."""
    task = service.runtime_task(db, task_id, token(authorization))
    job = db.get(RemoteEvaluationJob, body.id)
    if job:
        if job.task_id != task_id:
            raise HTTPException(403, "Job belongs to another task")
        return service.job_payload(job, include_result=True)
    configured = (task.input_args or {}).get("evaluator", {})
    mode = service.selection_mode(configured)
    if body.dispatch_version == 1 and any(key in configured for key in ("selection_mode", "max_attempts", "lease_timeout")):
        raise HTTPException(422, "The task's device-pool policy requires dispatch protocol v2")
    if (configured.get("type") != "remote" or body.selection_mode != mode
        or mode == "selected" and set(configured.get("gateway_ids", [])) != {str(g) for g in body.gateway_ids}):
        raise HTTPException(422, "Gateway selection differs from the active task configuration")
    owner = service.task_owner(db, task)
    contract = service.validate_pool(
        db,
        owner,
        {"gateway_ids": [str(g) for g in body.gateway_ids], "contract": body.contract.model_dump(mode="json"),
         "selection_mode": mode},
    )
    if configured.get("contract") and service.contract_key(configured["contract"]) != service.contract_key(contract):
        raise HTTPException(422, "Evaluation benchmark differs from the active task configuration")
    if body.dispatch_version >= 2:
        if body.max_attempts != configured.get("max_attempts", 3) or body.lease_timeout != configured.get("lease_timeout", 60):
            raise HTTPException(422, "Attempt policy differs from the active task configuration")
        if mode == "selected" and any(db.get(EvaluationGateway, g).protocol_version < 2 for g in body.gateway_ids):
            raise HTTPException(422, "Upgrade selected gateways to protocol v2 for leased device attempts")
    try:
        candidate = base64.b64decode(body.bundle, validate=True)
    except binascii.Error as exc:
        raise HTTPException(422, "Invalid candidate archive encoding") from exc
    key = f"remote-evaluation/{task_id}/{body.id}/candidate-{service.digest(body.bundle)}.zip"
    storage.upload(key, candidate, content_type="application/zip")
    job = RemoteEvaluationJob(
        id=body.id,
        task_id=task_id,
        user_id=owner,
        selected_gateway_ids=[str(g) for g in body.gateway_ids],
        selection_mode=mode,
        max_attempts=body.max_attempts if body.dispatch_version >= 2 else 1,
        contract_key=service.contract_key(contract),
        object_key=key,
        request={
            "timeout": body.timeout,
            "evaluation_profile": body.evaluation_profile,
            "behavior_storage": body.behavior_storage,
            "parameters": body.parameters,
            "algorithm_id": str(body.algorithm_id) if body.algorithm_id else "",
            "algorithm_name": body.algorithm_name,
            "source_fingerprint": body.source_fingerprint,
            "execution_id": body.execution_id,
            "instance_id": body.instance_id,
            **({"managed_attempt": True, "lease_timeout": body.lease_timeout} if body.dispatch_version >= 2 else {}),
        },
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        job = db.get(RemoteEvaluationJob, body.id)
        if job is None or job.task_id != task_id:
            raise HTTPException(409, "Conflicting evaluation job identity") from None
    return service.job_payload(job)


@router.get("/tasks/{task_id}/jobs/{job_id}", include_in_schema=False)
def status(task_id: uuid.UUID, job_id: uuid.UUID, db: SessionDep, authorization: str = Header()):
    """Wait durably while refreshing credentials for a still-active task."""
    task = service.runtime_task(db, task_id, token(authorization))
    job = db.get(RemoteEvaluationJob, job_id)
    if job is None or job.task_id != task.id:
        raise HTTPException(404, "Evaluation job not found")
    service.expire_leases(db, task_id=task.id)
    db.commit()
    db.refresh(job)
    from app.services.credential_broker import extend_task_tokens

    extend_task_tokens(task.id, settings_ttl(), user_id=service.task_owner(db, task))
    return service.job_payload(job, include_result=True)


def settings_ttl():
    """Keep active task credentials alive through long offline waits."""
    from app.core.config import settings

    return settings.TASK_TIME_LIMIT + 3600


@router.get("/tasks/{task_id}", response_model=list[JobView])
def task_jobs(task_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    """Expose remote progress in the existing task workbench."""
    from app.services.task_service.auth import get_task_with_auth

    get_task_with_auth(db, task_id, current_user)
    service.expire_leases(db, task_id=task_id)
    db.commit()
    gateways = {gateway.id: gateway for gateway in db.exec(select(EvaluationGateway))}
    result = []
    jobs = db.exec(
        select(RemoteEvaluationJob)
        .where(RemoteEvaluationJob.task_id == task_id)
        .order_by(RemoteEvaluationJob.created_at.desc())
        .limit(100)
    ).all()
    for job in jobs:
        gateway = gateways.get(job.gateway_id)
        online = gateway is not None and service.is_online(gateway)
        waiting = job.state == "queued" and not any(
            service.eligible(job, g)
            for g in gateways.values()
        )
        result.append(
            JobView(
                id=job.id,
                state="waiting_gateway" if waiting else job.state,
                gateway_id=job.gateway_id,
                gateway_name=gateway.name if gateway else None,
                gateway_online=online,
                report=service.job_payload(job)["report"],
                updated_at=job.updated_at,
                attempt_count=job.attempt_count,
                max_attempts=job.max_attempts,
                failure_category=job.report.get("metadata", {}).get("failure_category"),
                attempt_history=job.attempt_history,
            )
        )
    return result


@router.get("/{gateway_id}/events", include_in_schema=False)
def notifications(gateway_id: uuid.UUID, authorization: str = Header()):
    """Authenticate once and release the database before streaming SSE hints."""
    with Session(engine) as db:
        service.authorized_gateway(db, gateway_id, token(authorization))

    async def events():
        """Keep the HTTP stream active without holding database resources."""
        while True:
            yield 'event: poll\ndata: {"type":"poll"}\n\n'
            await asyncio.sleep(_NOTIFICATION_INTERVAL)

    return sse_response(events())
