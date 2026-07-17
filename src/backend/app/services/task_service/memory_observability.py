"""MindMemOS task memory observability aggregation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app import models
from app.core.redis import read_all_logs
from app.schemas import task as schemas
from app.utils.log_persist import task_log_to_dict

from .auth import get_task_with_auth


def get_task_memory_observability(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
) -> schemas.TaskMemoryObservabilityResponse:
    """Aggregate MindMemOS usage events from task logs."""
    task = get_task_with_auth(db, task_id, current_user)
    enabled = _is_mindmemos_task(task)
    response = schemas.TaskMemoryObservabilityResponse(task_id=task.id, enabled=enabled)
    if not enabled:
        return response

    logs = _load_raw_logs(db, task)
    latest: schemas.MemoryInjectionSummary | None = None
    associated_algorithm_ids: set[str] = set()
    total_deltas: list[float] = []
    scope_deltas: dict[str, list[float]] = {"task": [], "project": [], "user": []}

    for entry in logs:
        entry_type = str(entry.get("type") or "")
        if entry_type == "mindmemos_memory_injected":
            injection = _injection_summary(entry)
            response.injection_calls += 1
            response.deduped_hits_total += injection.deduped_hits
            response.injected_chars_total += injection.injected_chars
            response.elapsed_ms_total += injection.elapsed_ms
            response.sampler_counts[injection.sampler] = (
                response.sampler_counts.get(injection.sampler, 0) + 1
            )
            for scope, count in injection.scope_hits.items():
                response.scope_hits_total[scope] = response.scope_hits_total.get(scope, 0) + count
            latest = injection
        elif entry_type == "memory_card_created" and str(entry.get("scope") or "") == "task":
            response.created_task_memory_count += 1
        elif entry_type == "generated" and latest is not None:
            _aggregate_generated_contribution(
                response.contribution,
                latest,
                entry,
                associated_algorithm_ids,
                total_deltas,
                scope_deltas,
            )

    if response.injection_calls:
        response.elapsed_ms_avg = round(response.elapsed_ms_total / response.injection_calls)
    response.latest_injection = latest
    _finalize_contribution(response.contribution, total_deltas, scope_deltas)
    return response


def _load_raw_logs(db: Session, task: models.Task) -> list[dict[str, Any]]:
    from app.models import TaskLog

    rows = list(
        db.exec(
            select(TaskLog)
            .where(TaskLog.task_id == task.id)
            .order_by(TaskLog.timestamp.asc(), TaskLog.id.asc())
        ).all()
    )
    if rows:
        return [task_log_to_dict(row) for row in rows]
    return read_all_logs(task.id)


def _is_mindmemos_task(task: models.Task) -> bool:
    memory = task.input_args.get("memory") if isinstance(task.input_args, dict) else None
    if not isinstance(memory, dict):
        return False
    return memory.get("enabled") is not False and memory.get("type") == "mindmemos_cloud"


def _injection_summary(entry: dict[str, Any]) -> schemas.MemoryInjectionSummary:
    scope_hits = {"task": 0, "project": 0, "user": 0}
    raw_scope_hits = entry.get("scope_hits")
    if isinstance(raw_scope_hits, dict):
        for key, value in raw_scope_hits.items():
            scope = str(key)
            if scope not in scope_hits:
                continue
            scope_hits[scope] = _safe_int(value)
    return schemas.MemoryInjectionSummary(
        sampler=str(entry.get("sampler") or "unknown"),
        strategy=str(entry.get("strategy") or ""),
        scope_hits=scope_hits,
        deduped_hits=_safe_int(entry.get("deduped_hits")),
        injected_chars=_safe_int(entry.get("injected_chars")),
        elapsed_ms=_safe_int(entry.get("elapsed_ms")),
        timestamp=_parse_timestamp(entry.get("timestamp")),
    )


def _aggregate_generated_contribution(
    contribution: schemas.MemoryContributionSummary,
    injection: schemas.MemoryInjectionSummary,
    entry: dict[str, Any],
    associated_algorithm_ids: set[str],
    total_deltas: list[float],
    scope_deltas: dict[str, list[float]],
) -> None:
    data = entry.get("data")
    if not isinstance(data, dict):
        return
    score = _safe_float(_nested_get(data, ("evaluation", "score")))
    if score is None:
        return

    algorithm_key = str(data.get("id") or entry.get("file_name") or "")
    if algorithm_key:
        if algorithm_key in associated_algorithm_ids:
            return
        associated_algorithm_ids.add(algorithm_key)

    active_scopes = [
        scope
        for scope, count in injection.scope_hits.items()
        if scope in contribution.by_scope and _safe_int(count) > 0
    ]
    contribution.associated_generations += 1
    for scope in active_scopes:
        contribution.by_scope[scope].calls += 1

    parent_score = _extract_parent_score(data)
    if parent_score is None:
        return

    delta = round(score - parent_score, 6)
    total_deltas.append(delta)
    contribution.scored_generations += 1
    if delta > 0:
        contribution.positive_results += 1

    for scope in active_scopes:
        scope_summary = contribution.by_scope[scope]
        scope_deltas[scope].append(delta)
        if delta > 0:
            scope_summary.positive_results += 1


def _extract_parent_score(data: dict[str, Any]) -> float | None:
    operation_params = _nested_get(data, ("generation_meta", "operation_params"))
    if not isinstance(operation_params, dict):
        return None

    direct_score = _safe_float(operation_params.get("parent_score"))
    if direct_score is not None:
        return direct_score

    parent_scores = [
        score
        for key, value in operation_params.items()
        if key.startswith("parent") and key.endswith("_score")
        for score in [_safe_float(value)]
        if score is not None
    ]
    if not parent_scores:
        return None
    return max(parent_scores)


def _finalize_contribution(
    contribution: schemas.MemoryContributionSummary,
    total_deltas: list[float],
    scope_deltas: dict[str, list[float]],
) -> None:
    if total_deltas:
        contribution.best_delta = max(total_deltas)
        contribution.average_delta = round(sum(total_deltas) / len(total_deltas), 6)
    for scope, deltas in scope_deltas.items():
        if scope not in contribution.by_scope or not deltas:
            continue
        summary = contribution.by_scope[scope]
        summary.best_delta = max(deltas)
        summary.average_delta = round(sum(deltas) / len(deltas), 6)


def _nested_get(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
