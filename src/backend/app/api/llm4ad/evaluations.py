"""Authenticated evaluation details shared by local and remote tasks."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from llm4ad.evaluator.models import EvaluationRecord
from sqlmodel import select

from app.api.deps import CurrentUser, SessionDep
from app.core.storage import storage
from app.models import RemoteEvaluationJob
from app.schemas.evaluation import EvaluationDetail, EvaluationList, EvaluationSummary
from app.services import evaluation_data as service
from app.services.task_service.auth import get_task_with_auth

router = APIRouter(prefix="/tasks/{task_id}/evaluations", tags=["llm4ad.evaluations"])
PREVIEW_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TEXT_SUFFIXES = {".json", ".txt", ".md", ".markdown", ".csv", ".log", ".yaml", ".yml", ".xml"}
STORAGE_ERRORS = (BotoCoreError, ClientError, OSError)


def _load(task_id, evaluation_id) -> dict | None:
    """Read a committed manifest if its key exists in the task namespace."""
    key = service.record_key(task_id, str(evaluation_id))
    if key not in storage.list_objects(prefix=key):
        return None
    data = json.loads(storage.download(key))
    record = EvaluationRecord.model_validate(data["record"])
    if record.task_id != str(task_id) or record.id != str(evaluation_id):
        raise HTTPException(409, "Evaluation identity mismatch")
    return data


@router.get("", response_model=EvaluationList)
def list_evaluations(
    task_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    algorithm_id: str | None = Query(None, max_length=128),
    evaluation_profile: str | None = None,
    execution_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List task-version evaluations, including running and pending records."""
    task = get_task_with_auth(db, task_id, current_user)
    indexes = {}
    descriptors = {}
    cached = {}
    storage_available = True
    try:
        keys = storage.list_objects(prefix=service.prefix(task_id) + "index/")
    except STORAGE_ERRORS:
        keys, storage_available = [], False
    for key in keys:
        try:
            stamp, profile, remainder = Path(key).stem.split("__", 2)
            candidate, identity = remainder.rsplit("__", 1)
        except ValueError:
            continue
        if algorithm_id is not None and candidate != algorithm_id:
            continue
        if evaluation_profile is not None and profile != evaluation_profile:
            continue
        indexes[identity] = key
        descriptors[identity] = stamp
        if execution_id is not None:
            try:
                summary = EvaluationSummary.model_validate_json(storage.download(key))
            except STORAGE_ERRORS:
                indexes.clear()
                descriptors.clear()
                cached.clear()
                storage_available = False
                break
            if summary.execution_id != execution_id:
                descriptors.pop(identity)
            else:
                cached[identity] = summary
    pending = {
        record.id: service.summary_for(record, pending=record.storage_state != "saved")
        for record in service.pending_records(service.task_outbox(db, task), include_saved=not storage_available)
    }
    jobs = {
        str(job.attempt_id): job
        for job in db.exec(select(RemoteEvaluationJob).where(RemoteEvaluationJob.task_id == task_id))
    }
    legacy = service.legacy_identities(db, task_id, list(jobs.values()))
    live = {identity: service.remote_summary(db, job, legacy.get(str(job.id))) for identity, job in jobs.items()}
    for identity, summary in {**pending, **live}.items():
        if identity in indexes:
            continue
        if algorithm_id is not None and summary.algorithm_id != algorithm_id:
            continue
        if evaluation_profile is not None and summary.evaluation_profile != evaluation_profile:
            continue
        if execution_id is not None and summary.execution_id != execution_id:
            continue
        descriptors[identity] = "".join(character for character in summary.created_at if character.isdigit())
    identities = sorted(descriptors, key=lambda identity: (descriptors[identity], identity), reverse=True)
    offset = (page - 1) * page_size
    values = []
    for identity in identities[offset : offset + page_size]:
        if identity in indexes:
            try:
                summary = cached.get(identity) or EvaluationSummary.model_validate_json(
                    storage.download(indexes[identity])
                )
            except STORAGE_ERRORS:
                storage_available = False
                local = {
                    record.id: service.summary_for(record)
                    for record in service.pending_records(service.task_outbox(db, task), include_saved=True)
                }
                summary = local.get(identity) or live.get(identity)
                if summary is None:
                    continue
            if identity in live:
                progress = live[identity]
                summary.gateway_name, summary.gateway_online = progress.gateway_name, progress.gateway_online
                summary.progress = progress.progress
                if not summary.algorithm_id:
                    summary.algorithm_id, summary.algorithm_name = progress.algorithm_id, progress.algorithm_name
                    summary.source_fingerprint = progress.source_fingerprint
        elif identity in live:
            summary = live[identity]
            job = jobs[identity]
            if job.state in {"completed", "failed", "cancelled"}:
                try:
                    result = service.legacy_result(job)
                except STORAGE_ERRORS:
                    result, storage_available = None, False
                record, _ = service.remote_record(job, result, legacy.get(str(job.id)))
                summary.artifact_count = len(record.artifacts)
                summary.state = record.state
                summary.storage_state = record.storage_state
        else:
            summary = pending[identity]
        values.append(summary)
    return EvaluationList(
        data=values, count=len(identities), page=page, page_size=page_size, storage_available=storage_available
    )


def _detail(task_id, evaluation_id, db, current_user) -> tuple[EvaluationDetail, dict]:
    """Resolve committed, pending or historical records after task authorization."""
    task = get_task_with_auth(db, task_id, current_user)
    offline = False
    try:
        data = _load(task_id, evaluation_id)
    except STORAGE_ERRORS:
        data, offline = None, True
    if data:
        record = EvaluationRecord.model_validate(data["record"])
        if record.remote_job_id and not record.algorithm_id:
            job = db.get(RemoteEvaluationJob, uuid.UUID(record.remote_job_id))
            if job is not None:
                identity = service.legacy_identities(db, task_id, [job]).get(str(job.id), {})
                for name, value in identity.items():
                    setattr(record, name, value)
        return EvaluationDetail(
            record=record, summary=service.summary_for(record), available_artifact_ids=list(data.get("objects", {}))
        ), data.get("objects", {})
    for record in service.pending_records(service.task_outbox(db, task), include_saved=offline):
        if record.id == str(evaluation_id):
            if offline:
                record.storage_state = "offline"
            return EvaluationDetail(record=record, summary=service.summary_for(record, pending=True)), {}
    job = db.exec(
        select(RemoteEvaluationJob).where(
            RemoteEvaluationJob.task_id == task_id,
            RemoteEvaluationJob.attempt_id == evaluation_id,
        )
    ).first()
    if job:
        legacy = service.legacy_identities(db, task_id, [job])
        try:
            result = service.legacy_result(job)
        except STORAGE_ERRORS:
            result, offline = None, True
        record, objects = service.remote_record(job, result, legacy.get(str(job.id)))
        if offline:
            record.storage_state = "offline"
        return EvaluationDetail(
            record=record,
            summary=service.remote_summary(db, job, legacy.get(str(job.id))),
            available_artifact_ids=list(objects),
        ), objects
    raise HTTPException(404, "Evaluation not found")


@router.get("/{evaluation_id}", response_model=EvaluationDetail)
def evaluation_detail(task_id: uuid.UUID, evaluation_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    """Return details without granting arbitrary workspace or object access."""
    return _detail(task_id, evaluation_id, db, current_user)[0]


@router.get(
    "/{evaluation_id}/artifacts/{artifact_id}",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
def evaluation_artifact(
    task_id: uuid.UUID,
    evaluation_id: uuid.UUID,
    artifact_id: str,
    db: SessionDep,
    current_user: CurrentUser,
    download: bool = True,
):
    """Verify saved evidence before streaming a download or safe inline preview."""
    detail, objects = _detail(task_id, evaluation_id, db, current_user)
    artifact = next((item for item in detail.record.artifacts if item.id == artifact_id), None)
    key = objects.get(artifact_id)
    if artifact is None or not key:
        raise HTTPException(404, "Artifact unavailable or not yet saved")
    expected = service.prefix(task_id) + f"files/{evaluation_id}/{artifact.sha256}"
    legacy = (
        f"remote-evaluation/{task_id}/{detail.record.remote_job_id}/artifacts/"
        f"{detail.record.remote_attempt_id}/{artifact.sha256}"
    )
    if key not in {expected, legacy}:
        raise HTTPException(409, "Artifact storage identity mismatch")
    media_type = "application/octet-stream"
    if not download:
        from app.core.config import settings

        if artifact.size > settings.EVALUATION_PREVIEW_MAX_BYTES:
            raise HTTPException(413, "File is too large to preview; download the original instead")
        if artifact.media_type in PREVIEW_TYPES:
            media_type = artifact.media_type
        elif (
            Path(artifact.name).suffix.lower() in TEXT_SUFFIXES
            or artifact.media_type.startswith("text/")
            or artifact.media_type == "application/json"
        ):
            media_type = "text/plain; charset=utf-8"
        else:
            raise HTTPException(415, "Preview unavailable; download the original instead")
    temporary = tempfile.TemporaryFile()
    hasher, size = hashlib.sha256(), 0
    try:
        for chunk in storage.iter_download(key):
            temporary.write(chunk)
            hasher.update(chunk)
            size += len(chunk)
        if size != artifact.size or hasher.hexdigest() != artifact.sha256:
            raise HTTPException(409, "Stored artifact checksum mismatch")
        temporary.seek(0)
    except (KeyError, FileNotFoundError) as exc:
        temporary.close()
        raise HTTPException(404, "Saved artifact is unavailable") from exc
    except ClientError as exc:
        temporary.close()
        status = 404 if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"} else 503
        raise HTTPException(status, "Artifact storage is unavailable") from exc
    except (BotoCoreError, OSError) as exc:
        temporary.close()
        raise HTTPException(503, "Artifact storage is temporarily unavailable") from exc
    except BaseException:
        temporary.close()
        raise

    def chunks():
        try:
            while chunk := temporary.read(1024 * 1024):
                yield chunk
        finally:
            temporary.close()

    name = artifact.name.replace("\\", "/").rsplit("/", 1)[-1]
    disposition = "attachment" if download else "inline"
    return StreamingResponse(
        chunks(),
        media_type=media_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name, safe='')}",
            "Content-Length": str(size),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )
