"""Workspace-scoped access to an AutoDiscovery proposal's evolution task family."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from llm4ad.config.app import AppConfig
from pydantic import ValidationError
from sqlmodel import Session, select

from app import models
from app.core.config import settings
from app.core.security import create_access_token
from app.schemas import paper as schemas
from app.schemas import task as task_schemas
from app.services import paper_service, task_service


def _linked_family(
    db: Session,
    current_user: models.User,
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    task_id: uuid.UUID | None = None,
) -> tuple[models.Task, models.Task]:
    """Resolve a root or descendant without trusting a supplied project ID."""
    workspace = paper_service._owned_workspace(db, current_user.id, workspace_id)
    if workspace.mode != models.ResearchWorkspaceMode.ALGORITHM.value:
        raise HTTPException(status_code=404, detail="AutoDiscovery workspace not found")
    link = db.exec(
        select(models.PaperTaskLink).where(
            models.PaperTaskLink.workspace_id == workspace_id,
            models.PaperTaskLink.proposal_id == proposal_id,
        )
    ).first()
    if link is None:
        raise HTTPException(status_code=404, detail="Linked task not found")
    root = db.get(models.Task, link.task_id)
    if root is None or root.parent_id is not None:
        raise HTTPException(status_code=404, detail="Linked root task not found")
    project = db.get(models.Project, root.project_id)
    if project is None or project.user_id != workspace.user_id:
        raise HTTPException(status_code=403, detail="Linked project owner does not match workspace")
    selected = db.get(models.Task, task_id or root.active_child_id or root.id)
    if selected is None or selected.project_id != root.project_id or (
        selected.id != root.id and selected.group_id != root.id
    ):
        raise HTTPException(status_code=404, detail="Task is outside the linked family")
    return root, selected


def _version_matches(actual: datetime, expected: datetime) -> bool:
    """Compare timestamps consistently across PostgreSQL and SQLite drivers."""
    actual_utc = actual.replace(tzinfo=actual.tzinfo or UTC).astimezone(UTC)
    expected_utc = expected.replace(tzinfo=expected.tzinfo or UTC).astimezone(UTC)
    return actual_utc == expected_utc


def _parameter_update(task: models.Task, parameters: dict[str, int | float | bool]) -> tuple[dict, dict]:
    """Validate a narrow numeric evolution patch and return config plus diff."""
    evolution = task.input_args.get("evolution") if isinstance(task.input_args, dict) else None
    if not isinstance(evolution, dict):
        raise HTTPException(status_code=409, detail="Task has no evolution configuration")
    if not parameters:
        raise HTTPException(status_code=422, detail="At least one parameter is required")
    updated = dict(task.input_args)
    updated_evolution = dict(evolution)
    changes: dict[str, dict[str, int | float | bool]] = {}
    for key, value in parameters.items():
        previous = evolution.get(key)
        if key == "type" or key not in evolution or not isinstance(previous, (int, float, bool)):
            raise HTTPException(status_code=422, detail=f"Unsupported evolution parameter: {key}")
        if isinstance(previous, bool) != isinstance(value, bool):
            raise HTTPException(status_code=422, detail=f"Parameter type mismatch: {key}")
        if isinstance(value, float) and not math.isfinite(value):
            raise HTTPException(status_code=422, detail=f"Parameter must be finite: {key}")
        updated_evolution[key] = value
        changes[key] = {"before": previous, "after": value}
    updated["evolution"] = updated_evolution
    try:
        AppConfig.model_validate(updated)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid task configuration: {exc.errors()[0]['msg']}") from exc
    return updated, changes


def linked_task_snapshot(
    db: Session,
    current_user: models.User,
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    task_id: uuid.UUID | None = None,
) -> schemas.PaperLinkedTaskSnapshot:
    """Return live task-family state without exposing provider credentials."""
    root, selected = _linked_family(db, current_user, workspace_id, proposal_id, task_id)
    children = db.exec(
        select(models.Task).where(models.Task.group_id == root.id).order_by(models.Task.created_time.asc())
    ).all()
    logs = task_service.get_task_logs(db, selected.id, current_user, limit=20).entries
    interruption = any(
        entry.get("error_type") == "TaskInterrupted"
        or (isinstance(entry.get("data"), dict) and entry["data"].get("error_type") == "TaskInterrupted")
        for entry in logs
    )
    stats = task_service.get_task_stats(db, selected.id, current_user)
    parent = db.get(models.Task, selected.parent_id) if selected.parent_id else None
    parent_evolution = (
        parent.input_args.get("evolution", {})
        if parent is not None and parent.project_id == root.project_id and isinstance(parent.input_args, dict)
        else {}
    )
    current_evolution = selected.input_args.get("evolution", {}) if isinstance(selected.input_args, dict) else {}
    if not isinstance(parent_evolution, dict):
        parent_evolution = {}
    if not isinstance(current_evolution, dict):
        current_evolution = {}
    parameter_diff = {
        key: {"before": previous, "after": current_evolution[key]}
        for key, previous in parent_evolution.items()
        if key in current_evolution
        and previous != current_evolution[key]
        and isinstance(previous, (int, float, bool))
        and isinstance(current_evolution[key], (int, float, bool))
    }
    result: dict[str, Any] | None = None
    if selected.status == models.TaskStatus.COMPLETED:
        task_result = task_service.get_task_result(db, selected.id, current_user)
        if isinstance(task_result.result, dict):
            result = {
                key: task_result.result[key]
                for key in ("score", "best_score", "metrics")
                if key in task_result.result
            } or None
    return schemas.PaperLinkedTaskSnapshot(
        proposal_id=proposal_id,
        project_id=root.project_id,
        root_task_id=root.id,
        active_task_id=root.active_child_id or root.id,
        tasks=[
            {
                "id": str(item.id),
                "name": item.name,
                "status": item.status.value,
                "parent_id": str(item.parent_id) if item.parent_id else None,
                "version_time": item.version_time.isoformat(),
            }
            for item in (root, *children)
        ],
        selected_task_id=selected.id,
        status=selected.status.value,
        stopped_by_user=selected.status == models.TaskStatus.FAILED and interruption,
        solution_count=stats.solution_count,
        avg_score=stats.avg_score,
        max_score=stats.max_score,
        recent_logs=[
            {
                "type": str(entry.get("type") or ""),
                "timestamp": str(entry.get("timestamp") or ""),
                "message": str(entry.get("message") or ""),
            }
            for entry in logs
        ],
        result=result,
        evolution_parameters={
            key: value
            for key, value in current_evolution.items()
            if isinstance(value, (int, float, bool))
        },
        parameter_diff=parameter_diff,
    )


def create_child(
    db: Session,
    current_user: models.User,
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    parent_id: uuid.UUID,
    request: schemas.PaperLinkedTaskChildCreate,
) -> tuple[models.Task, dict]:
    """Copy a linked task without selecting the new version or touching its parent."""
    _root, parent = _linked_family(db, current_user, workspace_id, proposal_id, parent_id)
    db.refresh(parent, with_for_update=True)
    if not _version_matches(parent.version_time, request.expected_version_time):
        raise HTTPException(status_code=409, detail="Task configuration changed; refresh before tuning")
    updated, changes = _parameter_update(parent, request.parameters) if request.parameters else (None, {})
    child = task_service.copy_task(
        db,
        parent.id,
        current_user,
        is_child=True,
        activate_child=False,
        copy_logs=False,
        inherit_reports=False,
    )
    if updated is not None:
        child = task_service.update_task(
            db, child.id, current_user, task_schemas.TaskUpdate(input_args=updated)
        )
    return child, changes


def patch_child(
    db: Session,
    current_user: models.User,
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    task_id: uuid.UUID,
    request: schemas.PaperLinkedTaskPatch,
) -> tuple[models.Task, dict]:
    """Apply a validated evolution patch only to an unstarted child version."""
    root, child = _linked_family(db, current_user, workspace_id, proposal_id, task_id)
    db.refresh(child, with_for_update=True)
    if child.id == root.id or child.status != models.TaskStatus.UNINITIALIZED:
        raise HTTPException(status_code=409, detail="Only an unstarted child can be tuned")
    if not _version_matches(child.version_time, request.expected_version_time):
        raise HTTPException(status_code=409, detail="Task configuration changed; refresh before tuning")
    updated, changes = _parameter_update(child, request.parameters)
    task = task_service.update_task(db, child.id, current_user, task_schemas.TaskUpdate(input_args=updated))
    return task, changes


def start_linked_task(
    db: Session,
    current_user: models.User,
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    task_id: uuid.UUID,
) -> task_schemas.TaskRunResponse:
    """Start a linked task through the platform's existing execution service."""
    _root, task = _linked_family(db, current_user, workspace_id, proposal_id, task_id)
    db.refresh(task, with_for_update=True)
    if task.status in {models.TaskStatus.PENDING, models.TaskStatus.RUNNING}:
        raise HTTPException(status_code=409, detail="Task is already running")
    token = create_access_token(
        current_user.id,
        expires_delta=timedelta(seconds=settings.TASK_TIME_LIMIT + 3600),
    )
    return task_service.run_task(db, task.id, current_user, token)


def stop_linked_task(
    db: Session,
    current_user: models.User,
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    task_id: uuid.UUID,
) -> models.Task:
    """Stop a pending or running linked task through the existing service."""
    _root, task = _linked_family(db, current_user, workspace_id, proposal_id, task_id)
    db.refresh(task, with_for_update=True)
    if task.status not in {models.TaskStatus.PENDING, models.TaskStatus.RUNNING}:
        raise HTTPException(status_code=409, detail="Only a pending or running task can be stopped")
    return task_service.stop_task(db, task.id, current_user)
