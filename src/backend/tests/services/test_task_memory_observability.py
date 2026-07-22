import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from app import models
from app.core.db import engine
from app.services import task_service
from tests.utils.user import create_random_user


@pytest.fixture(scope="module")
def db():
    with Session(engine) as session:
        yield session


def _create_task_for_user(
    db: Session,
    user_id: uuid.UUID,
    *,
    memory: dict | None,
) -> models.Task:
    project = models.Project(name="Memory Observability Project", description="", user_id=user_id)
    db.add(project)
    db.commit()
    db.refresh(project)

    task = models.Task(
        name="Memory Observability Task",
        project_id=project.id,
        input_args={"memory": memory} if memory is not None else {},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _add_log(db: Session, owner_task_id: uuid.UUID, entry_type: str, **data):
    log = models.TaskLog(
        task_id=owner_task_id,
        type=entry_type,
        level=data.pop("level", "INFO"),
        timestamp=data.pop("timestamp", datetime.now(UTC)),
        message=data.pop("message", None),
        data=data or None,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def test_memory_observability_summarizes_injection_events(db: Session):
    user = create_random_user(db)
    task = _create_task_for_user(
        db,
        user.id,
        memory={"enabled": True, "type": "mindmemos_cloud"},
    )

    _add_log(
        db,
        task.id,
        "mindmemos_memory_injected",
        sampler="init",
        strategy="fast",
        scope_hits={"task": 1, "project": 2, "user": 0},
        deduped_hits=2,
        injected_chars=1200,
        elapsed_ms=150,
        used_memories=[
            {
                "memory_id": "m-task",
                "scope": "task",
                "type": "good_algorithm",
                "title": "Task sort memory",
                "score": -1.5,
                "generation": 2,
                "algorithm_id": "algo-1",
            },
            {
                "memory_id": "m-project",
                "scope": "project",
                "type": "domain_knowledge",
                "title": "Project memory",
            },
        ],
    )
    _add_log(
        db,
        task.id,
        "mindmemos_memory_injected",
        sampler="mutation",
        strategy="fast",
        scope_hits={"task": 1, "project": 0, "user": 1},
        deduped_hits=2,
        injected_chars=800,
        elapsed_ms=250,
        used_memories=[
            {
                "memory_id": "m-task",
                "scope": "task",
                "type": "good_algorithm",
                "title": "Task sort memory",
                "score": -1.5,
                "generation": 2,
                "algorithm_id": "algo-1",
            }
        ],
    )
    _add_log(
        db,
        task.id,
        "memory_card_created",
        scope="task",
        task_id=str(task.id),
        memory_id="created-1",
    )

    summary = task_service.get_task_memory_observability(db, task.id, user)

    assert summary.enabled is True
    assert summary.injection_calls == 2
    assert summary.scope_hits_total == {"task": 2, "project": 2, "user": 1}
    assert summary.deduped_hits_total == 4
    assert summary.injected_chars_total == 2000
    assert summary.elapsed_ms_total == 400
    assert summary.elapsed_ms_avg == 200
    assert summary.sampler_counts == {"init": 1, "mutation": 1}
    assert summary.created_task_memory_count == 1
    assert summary.latest_injection is not None
    assert summary.latest_injection.sampler == "mutation"
    assert not hasattr(summary, "used_memories")


def test_memory_observability_estimates_contribution_from_generated_scores(db: Session):
    user = create_random_user(db)
    task = _create_task_for_user(
        db,
        user.id,
        memory={"enabled": True, "type": "mindmemos_cloud"},
    )

    _add_log(
        db,
        task.id,
        "mindmemos_memory_injected",
        timestamp=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        sampler="mutation",
        strategy="fast",
        scope_hits={"task": 2, "project": 1, "user": 0},
        deduped_hits=3,
        injected_chars=900,
        elapsed_ms=100,
    )
    _add_log(
        db,
        task.id,
        "generated",
        timestamp=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        data={
            "id": "algo-positive",
            "evaluation": {"score": 1.4},
            "generation_meta": {"operation_params": {"parent_score": 1.0}},
        },
    )
    _add_log(
        db,
        task.id,
        "mindmemos_memory_injected",
        timestamp=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        sampler="crossover",
        strategy="fast",
        scope_hits={"task": 0, "project": 0, "user": 2},
        deduped_hits=2,
        injected_chars=600,
        elapsed_ms=120,
    )
    _add_log(
        db,
        task.id,
        "generated",
        timestamp=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
        data={
            "id": "algo-negative",
            "evaluation": {"score": 0.8},
            "generation_meta": {
                "operation_params": {
                    "parent_1_score": 1.0,
                    "parent_2_score": 0.9,
                }
            },
        },
    )

    summary = task_service.get_task_memory_observability(db, task.id, user)

    assert summary.contribution.associated_generations == 2
    assert summary.contribution.scored_generations == 2
    assert summary.contribution.positive_results == 1
    assert summary.contribution.best_delta == pytest.approx(0.4)
    assert summary.contribution.average_delta == pytest.approx(0.1)
    assert summary.contribution.by_scope["task"].calls == 1
    assert summary.contribution.by_scope["task"].positive_results == 1
    assert summary.contribution.by_scope["task"].best_delta == pytest.approx(0.4)
    assert summary.contribution.by_scope["project"].calls == 1
    assert summary.contribution.by_scope["project"].positive_results == 1
    assert summary.contribution.by_scope["user"].calls == 1
    assert summary.contribution.by_scope["user"].positive_results == 0
    assert summary.contribution.by_scope["user"].best_delta == pytest.approx(-0.2)


def test_memory_observability_is_disabled_for_local_memory_tasks(db: Session):
    user = create_random_user(db)
    task = _create_task_for_user(
        db,
        user.id,
        memory={"enabled": True, "type": "local_yaml"},
    )
    _add_log(
        db,
        task.id,
        "mindmemos_memory_injected",
        scope_hits={"task": 1},
        deduped_hits=1,
        injected_chars=500,
        elapsed_ms=50,
    )

    summary = task_service.get_task_memory_observability(db, task.id, user)

    assert summary.enabled is False
    assert summary.injection_calls == 0
    assert not hasattr(summary, "used_memories")
