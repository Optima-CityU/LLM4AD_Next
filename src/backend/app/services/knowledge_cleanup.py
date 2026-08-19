"""Lifecycle cleanup helpers for user-owned knowledge-library resources."""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.core.config import settings
from app.core.storage import storage
from app.models.knowledge import (
    KnowledgeCleanupJob,
    KnowledgeDocument,
    KnowledgeParsePlan,
    KnowledgeParseRun,
    KnowledgeParseStatus,
    KnowledgeSource,
)
from app.models.user import User

logger = logging.getLogger(__name__)


def build_cleanup_payload(
    *,
    user_id: uuid.UUID,
    source_id: uuid.UUID | None = None,
    object_prefixes: list[str] | None = None,
    plans: Iterable[Any] = (),
    runs: Iterable[Any] = (),
    all_user_workspaces: bool = False,
) -> dict[str, Any]:
    """Build a serializable, idempotent cleanup description before DB rows disappear."""
    plan_rows = list(plans)
    run_rows = list(runs)
    parser_jobs = [
        {"id": str(item.id), "container_id": getattr(item, "container_id", None)}
        for item in [*plan_rows, *run_rows]
    ]
    workspaces: list[dict[str, str]] = []
    seen_workspaces: set[tuple[str, uuid.UUID]] = set()

    def add_workspace(kind: str, raw_id: object) -> None:
        workspace_id = uuid.UUID(str(raw_id))
        marker = (kind, workspace_id)
        if marker in seen_workspaces:
            return
        seen_workspaces.add(marker)
        workspaces.append({"kind": kind, "id": str(workspace_id)})

    for plan in plan_rows:
        add_workspace("plan", plan.id)
    for run in run_rows:
        owner_kind = getattr(run, "session_owner_kind", None) or "run"
        owner_id = getattr(run, "session_owner_id", None) or run.id
        add_workspace("plan" if owner_kind == "plan" else "run", owner_id)

    prefixes = list(object_prefixes or [])
    if source_id is not None and not prefixes:
        prefixes.append(f"knowledge/{user_id}/{source_id}/")
    return {
        "user_id": str(user_id),
        "object_prefixes": prefixes,
        "parser_jobs": parser_jobs,
        "workspaces": workspaces,
        "all_user_workspaces": all_user_workspaces,
    }


def stop_parser_job(job: dict[str, Any]) -> None:
    """Idempotently stop one queued/running parser and remove its ephemeral state."""
    from app.core.celery import celery_app
    from app.core.redis import delete_knowledge_parse_context, delete_knowledge_parse_stream
    from app.services import container_service, credential_broker

    job_id = uuid.UUID(str(job.get("id")))
    celery_app.control.revoke(str(job_id), terminate=False)
    container_id = str(job.get("container_id") or f"llm4ad-knowledge-{job_id.hex[:16]}")
    container_service.kill_container_by_name(container_id, raise_on_error=True)
    delete_knowledge_parse_context(job_id)
    delete_knowledge_parse_stream(job_id)
    credential_broker.revoke_task_tokens(job_id)


def _remove_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def cleanup_payload(payload: dict[str, Any]) -> None:
    """Execute a validated cleanup payload; every operation is safe to retry."""
    user_id = uuid.UUID(str(payload.get("user_id")))
    allowed_prefix = f"knowledge/{user_id}/"
    prefixes = payload.get("object_prefixes") or []
    if not isinstance(prefixes, list):
        raise ValueError("invalid knowledge object prefixes")
    for prefix in prefixes:
        if not isinstance(prefix, str) or not prefix.startswith(allowed_prefix):
            raise ValueError("knowledge object prefix is outside the owning user")

    parser_jobs = payload.get("parser_jobs") or []
    if not isinstance(parser_jobs, list):
        raise ValueError("invalid parser jobs")
    for job in parser_jobs:
        if not isinstance(job, dict):
            raise ValueError("invalid parser job")
        stop_parser_job(job)

    if payload.get("revoke_user_tokens") is True:
        from app.services import credential_broker

        credential_broker.revoke_user_tokens(user_id, raise_on_error=True)

    for prefix in prefixes:
        storage.delete_many(storage.list_objects(prefix))
        if remaining := storage.list_objects(prefix):
            raise RuntimeError(f"knowledge object cleanup incomplete: {len(remaining)} objects remain")

    user_root = Path(settings.DOCKER_PROJECT_HOME) / f"code_user-{user_id}"
    if payload.get("all_user_workspaces") is True:
        _remove_path(user_root / "knowledge_plan")
        _remove_path(user_root / "knowledge_parse")
        _remove_path(user_root / ".task_runtime")
        return

    workspaces = payload.get("workspaces") or []
    if not isinstance(workspaces, list):
        raise ValueError("invalid parser workspaces")
    for workspace in workspaces:
        if not isinstance(workspace, dict):
            raise ValueError("invalid parser workspace")
        kind = workspace.get("kind")
        if kind not in {"plan", "run"}:
            raise ValueError("invalid parser workspace kind")
        workspace_id = uuid.UUID(str(workspace.get("id")))
        directory = "knowledge_plan" if kind == "plan" else "knowledge_parse"
        _remove_path(user_root / directory / str(workspace_id))


def prepare_source_cleanup_job(
    db: Session,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
) -> KnowledgeCleanupJob:
    """Collect source-owned resources while their metadata is still queryable."""
    plans = list(
        db.exec(
            select(KnowledgeParsePlan).where(KnowledgeParsePlan.source_id == source_id)
        ).all()
    )
    runs = list(
        db.exec(
            select(KnowledgeParseRun).where(KnowledgeParseRun.source_id == source_id)
        ).all()
    )
    return KnowledgeCleanupJob(
        user_id=user_id,
        payload=build_cleanup_payload(
            user_id=user_id,
            source_id=source_id,
            plans=plans,
            runs=runs,
        ),
    )


def prepare_file_cleanup_job(
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    file_id: uuid.UUID,
) -> KnowledgeCleanupJob:
    return KnowledgeCleanupJob(
        user_id=user_id,
        payload=build_cleanup_payload(
            user_id=user_id,
            object_prefixes=[f"knowledge/{user_id}/{source_id}/sources/{file_id}/"],
        ),
    )


def prepare_user_cleanup_job(db: Session, user_id: uuid.UUID) -> KnowledgeCleanupJob:
    """Cancel a user's parser jobs and retain everything needed after DB cascade."""
    # Lock both the owner and every source so concurrent source/plan/run inserts
    # cannot slip between resource collection and the account-delete commit.
    db.exec(select(User).where(User.id == user_id).with_for_update()).first()
    list(
        db.exec(
            select(KnowledgeSource)
            .where(KnowledgeSource.user_id == user_id)
            .with_for_update()
        ).all()
    )
    plans = list(
        db.exec(
            select(KnowledgeParsePlan)
            .join(KnowledgeSource, col(KnowledgeSource.id) == col(KnowledgeParsePlan.source_id))
            .where(KnowledgeSource.user_id == user_id)
        ).all()
    )
    runs = list(
        db.exec(
            select(KnowledgeParseRun)
            .join(KnowledgeSource, col(KnowledgeSource.id) == col(KnowledgeParseRun.source_id))
            .where(KnowledgeSource.user_id == user_id)
        ).all()
    )
    active_statuses = {
        KnowledgeParseStatus.PENDING.value,
        KnowledgeParseStatus.RUNNING.value,
    }
    active_jobs: list[Any] = []
    parser_jobs: list[KnowledgeParsePlan | KnowledgeParseRun] = [*plans, *runs]
    now = datetime.now(UTC)
    for item in parser_jobs:
        if item.status not in active_statuses:
            continue
        item.status = KnowledgeParseStatus.CANCELLED.value
        item.stage = "cancelled"
        item.message = "用户已删除，解析任务已停止"
        item.error_code = None
        item.error = None
        item.updated_time = now
        db.add(item)
        active_jobs.append(item)
    for item in active_jobs:
        try:
            stop_parser_job({"id": str(item.id), "container_id": item.container_id})
        except Exception:
            logger.warning("Failed to stop parser during user cleanup: job_id=%s", item.id, exc_info=True)

    payload = build_cleanup_payload(
        user_id=user_id,
        object_prefixes=[f"knowledge/{user_id}/"],
        plans=plans,
        runs=runs,
        all_user_workspaces=True,
    )
    payload["revoke_user_tokens"] = True
    return KnowledgeCleanupJob(user_id=user_id, payload=payload)


def run_cleanup_job(db: Session, job_id: uuid.UUID) -> None:
    """Run one durable outbox job and retain it only when a retry is needed."""
    job = db.get(KnowledgeCleanupJob, job_id)
    if job is None:
        return
    job.status = "running"
    job.attempts += 1
    job.error = None
    job.updated_time = datetime.now(UTC)
    db.add(job)
    db.commit()
    try:
        cleanup_payload(job.payload)
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)[:4000]
        job.updated_time = datetime.now(UTC)
        db.add(job)
        db.commit()
        raise
    db.delete(job)
    db.commit()


def run_or_schedule_cleanup(job_id: uuid.UUID) -> None:
    """Prefer immediate cleanup, then fall back to the Celery retry queue."""
    from app.core.db import get_db_session

    try:
        with get_db_session() as db:
            run_cleanup_job(db, job_id)
        return
    except Exception:
        logger.warning("Immediate knowledge cleanup failed: job_id=%s", job_id, exc_info=True)
    try:
        from app.tasks.knowledge_parser import run_knowledge_cleanup

        run_knowledge_cleanup.apply_async(args=[str(job_id)], task_id=str(job_id))
    except Exception:
        logger.exception("Failed to enqueue knowledge cleanup retry: job_id=%s", job_id)


def recover_pending_cleanup_jobs() -> int:
    """Requeue outbox rows left behind by a process, broker, or storage outage."""
    from app.core.db import get_db_session
    from app.tasks.knowledge_parser import run_knowledge_cleanup

    with get_db_session() as db:
        jobs = list(db.exec(select(KnowledgeCleanupJob)).all())
    for job in jobs:
        try:
            run_knowledge_cleanup.apply_async([str(job.id)], task_id=str(job.id))
        except Exception:
            logger.exception("Failed to recover knowledge cleanup: job_id=%s", job.id)
    return len(jobs)
