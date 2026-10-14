"""Pure contracts for staged AutoDiscovery and its linked task boundary."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlmodel import Session, select

from app import models
from app.api.llm4ad import paper_runtime
from app.core.db import engine
from app.schemas import paper as schemas
from app.services import paper_task_control, project_service, task_service
from app.tasks.paper_agent import (
    DiscoveryBaselineArtifact,
    DiscoveryCandidatesArtifact,
    DiscoveryEvaluationArtifact,
)
from tests.utils.user import create_random_user


def test_baseline_requires_versioned_grounded_summary() -> None:
    """Keep a baseline useful without fabricating evaluation measurements."""
    artifact = DiscoveryBaselineArtifact.model_validate(
        {
            "schema_version": "discovery-baseline-v1",
            "summary": "Paper proposes a search heuristic.",
            "algorithm_summary": "Optimize the bounded candidate generator.",
            "evidence": [{"claim": "Uses local search", "source_path": "paper.md"}],
            "unknowns": ["No evaluator dataset was uploaded"],
        }
    )
    assert artifact.unknowns == ["No evaluator dataset was uploaded"]
    with pytest.raises(ValidationError):
        DiscoveryBaselineArtifact.model_validate({"schema_version": "unknown", "summary": "x", "algorithm_summary": "y"})


def test_candidates_allow_comparison_before_selection() -> None:
    """Do not force a fabricated choice when no direction is defensible."""
    unresolved = DiscoveryCandidatesArtifact.model_validate(
        {"schema_version": "discovery-candidates-v1", "summary": "More input needed", "candidates": []}
    )
    assert unresolved.selected_candidate_id is None
    with pytest.raises(ValidationError):
        DiscoveryCandidatesArtifact.model_validate(
            {
                "schema_version": "discovery-candidates-v1",
                "summary": "Wrong selection",
                "candidates": [],
                "selected_candidate_id": "missing",
            }
        )


def test_evaluation_ready_requires_executable_contract() -> None:
    """Separate a published open question from a ready evaluator design."""
    with pytest.raises(ValidationError):
        DiscoveryEvaluationArtifact.model_validate(
            {
                "schema_version": "discovery-evaluation-v1",
                "summary": "No data protocol",
                "candidate_id": "a",
                "ready_for_build": True,
            }
        )


def _family_fixture(monkeypatch: pytest.MonkeyPatch):
    """Construct a minimal owner-linked root and unrelated child."""
    owner_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    root_id = uuid.uuid4()
    project_id = uuid.uuid4()
    user = SimpleNamespace(id=owner_id)
    workspace = SimpleNamespace(id=workspace_id, user_id=owner_id, mode="algorithm")
    root = SimpleNamespace(id=root_id, parent_id=None, project_id=project_id, active_child_id=None)
    project = SimpleNamespace(id=project_id, user_id=owner_id)
    link = SimpleNamespace(workspace_id=workspace_id, proposal_id=proposal_id, task_id=root_id)
    db = MagicMock()
    db.exec.return_value.first.return_value = link
    monkeypatch.setattr(paper_task_control.paper_service, "_owned_workspace", lambda *_args: workspace)
    return db, user, workspace_id, proposal_id, root, project


def test_linked_family_rejects_unrelated_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model-supplied task ID cannot escape its linked root family."""
    db, user, workspace_id, proposal_id, root, project = _family_fixture(monkeypatch)
    unrelated = SimpleNamespace(id=uuid.uuid4(), project_id=project.id, group_id=uuid.uuid4())
    db.get.side_effect = lambda model, identifier: {
        (models.Task, root.id): root,
        (models.Project, project.id): project,
        (models.Task, unrelated.id): unrelated,
    }.get((model, identifier))

    with pytest.raises(HTTPException) as exc:
        paper_task_control._linked_family(db, user, workspace_id, proposal_id, unrelated.id)

    assert exc.value.status_code == 404


def test_linked_family_rejects_project_owner_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a superuser-owned task cannot be smuggled into another workspace."""
    db, user, workspace_id, proposal_id, root, project = _family_fixture(monkeypatch)
    project.user_id = uuid.uuid4()
    db.get.side_effect = lambda model, identifier: {
        (models.Task, root.id): root,
        (models.Project, project.id): project,
    }.get((model, identifier))

    with pytest.raises(HTTPException) as exc:
        paper_task_control._linked_family(db, user, workspace_id, proposal_id)

    assert exc.value.status_code == 403


def test_numeric_parameter_patch_validates_existing_evolution_fields() -> None:
    """Permit a typed tuning diff but reject unrelated configuration writes."""
    task = SimpleNamespace(input_args={"evolution": {"type": "island_ga", "max_generations": 10}})
    updated, changes = paper_task_control._parameter_update(task, {"max_generations": 20})
    assert updated["evolution"]["max_generations"] == 20
    assert changes["max_generations"] == {"before": 10, "after": 20}
    with pytest.raises(HTTPException) as exc:
        paper_task_control._parameter_update(task, {"provider": 1})
    assert exc.value.status_code == 422


def test_child_copy_does_not_activate_new_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scoped copy path opts out of the legacy automatic selection."""
    version = datetime.now(UTC)
    parent = SimpleNamespace(id=uuid.uuid4(), version_time=version, input_args={"evolution": {"type": "island_ga"}})
    copied = SimpleNamespace(id=uuid.uuid4())
    calls: list[dict] = []
    monkeypatch.setattr(paper_task_control, "_linked_family", lambda *_args: (parent, parent))

    def copy_task(_db, _task_id, _user, **kwargs):
        calls.append(kwargs)
        return copied

    monkeypatch.setattr(paper_task_control.task_service, "copy_task", copy_task)
    result, changes = paper_task_control.create_child(
        MagicMock(),
        SimpleNamespace(id=uuid.uuid4()),
        uuid.uuid4(),
        uuid.uuid4(),
        parent.id,
        schemas.PaperLinkedTaskChildCreate(expected_version_time=version),
    )
    assert result is copied
    assert changes == {}
    assert calls == [{"is_child": True, "activate_child": False, "copy_logs": False, "inherit_reports": False}]


def test_real_child_copy_has_no_inherited_results() -> None:
    """A new parameter experiment has no copied score, report or active selection."""
    with Session(engine) as db:
        user = create_random_user(db)
        project = project_service.create_project(db, "Discovery experiment", "Isolated copy", user.id)
        root = models.Task(
            name="Original algorithm",
            project_id=project.id,
            input_args={"evolution": {"type": "island_ga", "max_generations": 10}},
            reports={"summary": {"score": 0.8}},
            status=models.TaskStatus.RUNNING,
        )
        db.add(root)
        db.commit()
        db.refresh(root)
        db.add(models.TaskLog(task_id=root.id, type="generated", data={"evaluation": {"score": 0.8}}))
        db.commit()

        child = task_service.copy_task(
            db,
            root.id,
            user,
            is_child=True,
            activate_child=False,
            copy_logs=False,
            inherit_reports=False,
        )
        db.refresh(root)
        child_logs = db.exec(select(models.TaskLog).where(models.TaskLog.task_id == child.id)).all()

        assert root.active_child_id == root.id
        assert root.status == models.TaskStatus.RUNNING
        assert child.parent_id == root.id
        assert child.group_id == root.id
        assert child.reports is None
        assert child.status == models.TaskStatus.UNINITIALIZED
        assert child_logs == []


def test_running_child_cannot_be_patched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parameter tuning never rewrites the config of an active experiment."""
    root = SimpleNamespace(id=uuid.uuid4())
    child = SimpleNamespace(id=uuid.uuid4(), status=models.TaskStatus.RUNNING)
    monkeypatch.setattr(paper_task_control, "_linked_family", lambda *_args: (root, child))
    with pytest.raises(HTTPException) as exc:
        paper_task_control.patch_child(
            MagicMock(),
            SimpleNamespace(id=uuid.uuid4()),
            uuid.uuid4(),
            uuid.uuid4(),
            child.id,
            schemas.PaperLinkedTaskPatch(
                expected_version_time=datetime.now(UTC),
                parameters={"max_generations": 20},
            ),
        )
    assert exc.value.status_code == 409


def test_nonfinal_runtime_cannot_use_linked_task_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """A baseline Agent cannot start a project through the final-stage bridge."""
    workspace_id = uuid.uuid4()
    run_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    workspace = SimpleNamespace(id=workspace_id, mode="algorithm", user_id=owner_id)
    run = SimpleNamespace(
        id=run_id,
        workspace_id=workspace_id,
        run_kind=models.PaperAgentRunKind.DISCOVERY_BASELINE.value,
    )
    db = MagicMock()
    db.get.side_effect = lambda model, identifier: {
        (models.PaperWorkspace, workspace_id): workspace,
        (models.PaperAgentRun, run_id): run,
    }.get((model, identifier))
    session = MagicMock()
    session.__enter__.return_value = db
    monkeypatch.setattr(paper_runtime, "Session", lambda _engine: session)
    monkeypatch.setattr(paper_runtime, "paper_workspace_runtime_token", lambda _id: "test-token")
    http_request = Request({"type": "http", "headers": [(b"x-llm4ad-runtime-token", b"test-token")]})

    with pytest.raises(HTTPException) as exc:
        paper_runtime.operate_runtime_linked_task(
            paper_runtime._LinkedTaskToolRequest(workspace_id=workspace_id, run_id=run_id, operation="list"),
            http_request,
        )
    assert exc.value.status_code == 403


def test_scoped_start_and_stop_use_existing_task_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lifecycle actions delegate once after checking the persisted status."""
    root = SimpleNamespace(id=uuid.uuid4())
    task = SimpleNamespace(id=root.id, status=models.TaskStatus.UNINITIALIZED)
    monkeypatch.setattr(paper_task_control, "_linked_family", lambda *_args: (root, task))
    monkeypatch.setattr(paper_task_control, "create_access_token", lambda *_args, **_kwargs: "private-token")
    calls: list[tuple[str, str | None]] = []

    def run_task(_db, _task_id, _user, token):
        calls.append(("run", token))
        task.status = models.TaskStatus.PENDING
        return SimpleNamespace(task_id=task.id)

    def stop_task(_db, _task_id, _user):
        calls.append(("stop", None))
        task.status = models.TaskStatus.FAILED
        return task

    monkeypatch.setattr(paper_task_control.task_service, "run_task", run_task)
    monkeypatch.setattr(paper_task_control.task_service, "stop_task", stop_task)
    db = MagicMock()
    user = SimpleNamespace(id=uuid.uuid4())
    workspace_id, proposal_id = uuid.uuid4(), uuid.uuid4()

    paper_task_control.start_linked_task(db, user, workspace_id, proposal_id, task.id)
    assert calls == [("run", "private-token")]
    with pytest.raises(HTTPException):
        paper_task_control.start_linked_task(db, user, workspace_id, proposal_id, task.id)
    paper_task_control.stop_linked_task(db, user, workspace_id, proposal_id, task.id)
    assert calls == [("run", "private-token"), ("stop", None)]
    with pytest.raises(HTTPException):
        paper_task_control.stop_linked_task(db, user, workspace_id, proposal_id, task.id)
