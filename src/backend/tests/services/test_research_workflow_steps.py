"""Verify durable, explicit workflow steps independently of raw tool progress."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app import models
from app.services import paper_workflow
from app.services import paper_workflow_coordinator as coordinator


@pytest.fixture
def workflow_db():
    """Provide real SQLite persistence without application storage or workers."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            model.__table__
            for model in (
                models.User,
                models.LLMProvider,
                models.PaperWorkspace,
                models.PaperSourceVersion,
                models.PaperReview,
                models.PaperAgentRun,
            )
        ],
    )
    try:
        with Session(engine) as db:
            workspace = models.PaperWorkspace(user_id=uuid.uuid4(), title="Workflow steps", mode="proposal")
            run = models.PaperAgentRun(
                id=uuid.uuid5(workspace.id, "cloudcli:workflow"),
                workspace_id=workspace.id,
                run_kind="research_workflow",
                session_id="persisted-native-conversation",
                request_payload={"auto_workflow": True, "native_runtime": "cloudcli"},
            )
            db.add(workspace)
            db.add(run)
            db.commit()
            yield db, workspace, run
    finally:
        engine.dispose()


def step_payload(step_id="compare-routes", **updates):
    """Build the public camelCase report sent by the coordinator MCP tool."""
    return {
        "id": step_id,
        "title": "Compare research routes",
        "status": "running",
        "goal": "Identify the evidence needed to choose between two methods.",
        "approach": "Compare assumptions and check the available source material.",
        "summary": "The comparison has started.",
        "outputPaths": [],
        **updates,
    }


def raw_snapshot(run, *, tool_name="Read", agents=None, status="running", **updates):
    """Build an actual transcript snapshot rather than an explicit work report."""
    return {
        "sessionId": run.session_id,
        "status": status,
        "currentTool": tool_name if status == "running" else None,
        "agents": (
            agents
            if agents is not None
            else [
                {
                    "id": "tool-call-1",
                    "parentId": "main",
                    "stage": "methods",
                    "description": "Read author evidence",
                    "status": "running",
                    "toolCount": 1,
                    "currentTool": tool_name,
                    "tools": [{"id": "read-1", "name": tool_name, "done": False, "failed": False}],
                }
            ]
        ),
        **updates,
    }


def restored_steps(db, workspace):
    """Restore the public logical-step list through the production read path."""
    snapshot = coordinator.load_progress_snapshot(db, workspace)
    assert snapshot is not None
    assert isinstance(snapshot["workflowSteps"], list)
    return snapshot["workflowSteps"]


def test_workflow_report_persists_without_raw_tool_snapshots(workflow_db):
    """A reported work unit survives a fresh session with no transcript events."""
    db, workspace, run = workflow_db
    report = step_payload(outputPaths=[".research/drafts/routes.md"])
    returned = coordinator.record_workflow_step(db, workspace, run, report)
    assert all(returned[key] == value for key, value in report.items())
    assert returned["runId"] == str(run.id)
    assert returned["revision"] >= 1
    assert datetime.fromisoformat(returned["startedAt"])
    assert datetime.fromisoformat(returned["updatedAt"]) >= datetime.fromisoformat(returned["startedAt"])
    assert run.request_payload["research_workflow_steps"]
    assert run.request_payload["research_workflow_sequence"] == 1
    assert not run.request_payload.get("research_progress")
    assert not run.request_payload.get("research_progress_events")
    workspace_id, run_id = workspace.id, run.id
    db.commit()

    with Session(db.get_bind()) as fresh:
        saved_workspace = fresh.get(models.PaperWorkspace, workspace_id)
        saved_run = fresh.get(models.PaperAgentRun, run_id)
        assert saved_run.request_payload["research_workflow_sequence"] == 1
        assert restored_steps(fresh, saved_workspace) == [returned]


def test_repeated_report_is_idempotent_even_after_database_reload(workflow_db):
    """Delivery retries cannot create steps or advance revisions and sequence."""
    db, workspace, run = workflow_db
    report = step_payload()
    first = deepcopy(coordinator.record_workflow_step(db, workspace, run, report))
    before = deepcopy(run.request_payload)
    db.commit()
    db.expire_all()
    duplicate = coordinator.record_workflow_step(db, workspace, run, deepcopy(report))
    assert duplicate == first
    assert run.request_payload == before
    assert restored_steps(db, workspace) == [first]


def test_same_id_updates_one_step_without_changing_first_report_order(workflow_db):
    """Progress updates revise an existing logical unit without reordering it."""
    db, workspace, run = workflow_db
    first = deepcopy(coordinator.record_workflow_step(db, workspace, run, step_payload("first")))
    coordinator.record_workflow_step(db, workspace, run, step_payload("second", title="Check the literature"))
    changed = coordinator.record_workflow_step(
        db,
        workspace,
        run,
        step_payload("first", summary="The assumptions have been compared.", outputPaths=["notes/routes.md"]),
    )
    assert changed["revision"] == first["revision"] + 1
    assert changed["startedAt"] == first["startedAt"]
    assert run.request_payload["research_workflow_sequence"] == 3
    db.commit()
    db.expire_all()
    steps = restored_steps(db, workspace)
    assert [item["id"] for item in steps] == ["first", "second"]
    assert steps[0] == changed


def test_completed_step_can_be_explicitly_reopened_and_completed_again(workflow_db):
    """Explicit work reports can reopen a unit even though raw replay cannot."""
    db, workspace, run = workflow_db
    completed = deepcopy(
        coordinator.record_workflow_step(
            db,
            workspace,
            run,
            step_payload(status="completed", summary="Comparison finished; evidence is inconclusive."),
        )
    )
    reopened = deepcopy(
        coordinator.record_workflow_step(
            db,
            workspace,
            run,
            step_payload(status="running", summary="Rechecking newly supplied author evidence."),
        )
    )
    assert reopened["revision"] == completed["revision"] + 1
    assert datetime.fromisoformat(reopened["startedAt"]) >= datetime.fromisoformat(completed["startedAt"])
    assert reopened["status"] == "running"
    retry = coordinator.record_workflow_step(
        db,
        workspace,
        run,
        step_payload(status="running", summary="Rechecking newly supplied author evidence."),
    )
    assert retry == reopened
    assert run.request_payload["research_workflow_sequence"] == 2
    final = coordinator.record_workflow_step(
        db,
        workspace,
        run,
        step_payload(status="completed", summary="The new evidence has been assessed."),
    )
    assert final["revision"] == reopened["revision"] + 1
    assert run.request_payload["research_workflow_sequence"] == 3
    assert restored_steps(db, workspace) == [final]


@pytest.mark.parametrize("status", ["ready", "running", "completed", "interrupted"])
def test_raw_snapshots_cannot_erase_or_rewrite_explicit_steps(workflow_db, status):
    """Empty, truncated, and completed transcript updates leave reports intact."""
    db, workspace, run = workflow_db
    reported = deepcopy(coordinator.record_workflow_step(db, workspace, run, step_payload()))
    stored_steps = deepcopy(run.request_payload["research_workflow_steps"])
    sequence = run.request_payload["research_workflow_sequence"]
    coordinator.record_progress_snapshot(db, workspace, run, raw_snapshot(run, tool_name="Grep"))
    coordinator.record_progress_snapshot(
        db,
        workspace,
        run,
        raw_snapshot(run, agents=[], status=status, workflowSteps=[], research_workflow_steps=[]),
    )
    db.commit()
    db.expire_all()
    assert run.request_payload["research_workflow_steps"] == stored_steps
    assert run.request_payload["research_workflow_sequence"] == sequence
    assert restored_steps(db, workspace) == [reported]
    assert run.request_payload["research_progress_events"]


def test_workflow_updates_preserve_existing_raw_progress_and_events(workflow_db):
    """The two durable ledgers can advance independently in either order."""
    db, workspace, run = workflow_db
    coordinator.record_progress_snapshot(db, workspace, run, raw_snapshot(run))
    progress = deepcopy(run.request_payload["research_progress"])
    events = deepcopy(run.request_payload["research_progress_events"])
    coordinator.record_workflow_step(db, workspace, run, step_payload())
    coordinator.record_workflow_step(db, workspace, run, step_payload(summary="A useful evidence gap was found."))
    assert run.request_payload["research_progress"] == progress
    assert run.request_payload["research_progress_events"] == events


@pytest.mark.parametrize("tool_name", ["Read", "Grep", "Task", "mcp__research_search__search"])
def test_unrelated_tool_calls_are_not_promoted_to_workflow_steps(workflow_db, tool_name):
    """Only explicit coordinator reports create logical work units."""
    db, workspace, run = workflow_db
    coordinator.record_progress_snapshot(db, workspace, run, raw_snapshot(run, tool_name=tool_name))
    db.commit()
    db.expire_all()
    snapshot = coordinator.load_progress_snapshot(db, workspace)
    assert len(snapshot["agents"]) == 1
    assert snapshot.get("workflowSteps", []) == []
    assert not run.request_payload.get("research_workflow_steps")


def test_reconnect_merges_same_logical_id_across_runs_without_duplicates(workflow_db):
    """A newer coordinator report replaces the old unit while retaining order."""
    db, workspace, first_run = workflow_db
    coordinator.record_workflow_step(db, workspace, first_run, step_payload("first"))
    coordinator.record_workflow_step(db, workspace, first_run, step_payload("second"))
    coordinator.record_progress_snapshot(db, workspace, first_run, raw_snapshot(first_run))
    second_run = models.PaperAgentRun(
        workspace_id=workspace.id,
        run_kind="research_workflow",
        session_id=first_run.session_id,
        created_time=first_run.created_time + timedelta(seconds=1),
        request_payload={"auto_workflow": True, "native_runtime": "cloudcli"},
    )
    db.add(second_run)
    db.flush()
    newest = coordinator.record_workflow_step(
        db,
        workspace,
        second_run,
        step_payload("first", status="completed", summary="Updated after reconnect."),
    )
    third = coordinator.record_workflow_step(db, workspace, second_run, step_payload("third"))
    db.commit()
    db.expire_all()
    restored = restored_steps(db, workspace)
    assert [item["id"] for item in restored] == ["first", "second", "third"]
    assert restored[0] == newest
    assert restored[0]["runId"] == str(second_run.id)
    assert restored[2] == third


@pytest.mark.parametrize(
    "scope", ["foreign_workspace", "missing_session", "empty_session", "not_auto", "wrong_run_kind", "child_run"]
)
def test_unauthorized_scope_is_rejected_without_mutating_either_ledger(workflow_db, scope):
    """An auto-workflow flag alone cannot authorize a foreign or child run."""
    db, workspace, run = workflow_db
    target = run
    if scope == "foreign_workspace":
        workspace = models.PaperWorkspace(user_id=uuid.uuid4(), title="Other workspace", mode="proposal")
        db.add(workspace)
    elif scope == "missing_session":
        run.session_id = None
    elif scope == "empty_session":
        run.session_id = ""
    elif scope == "not_auto":
        run.request_payload = {**run.request_payload, "auto_workflow": False}
    elif scope == "wrong_run_kind":
        run.run_kind = "proposal_methods"
    else:
        target = models.PaperAgentRun(
            workspace_id=workspace.id,
            run_kind="research_workflow",
            session_id=run.session_id,
            request_payload={
                "auto_workflow": True,
                "native_runtime": "cloudcli",
                "coordinator_run_id": str(run.id),
            },
        )
        db.add(target)
    db.flush()
    before = deepcopy(target.request_payload)
    with pytest.raises(HTTPException):
        coordinator.record_workflow_step(db, workspace, target, step_payload())
    assert target.request_payload == before
    assert not target.request_payload.get("research_workflow_sequence")


@pytest.mark.parametrize("mode", ["proposal", "manuscript"])
def test_child_workers_are_not_given_the_workflow_report_tool(workflow_db, mode):
    """Named workers can read context but cannot publish parent progress reports."""
    _db, workspace, _run = workflow_db
    workspace.mode = mode
    workers = coordinator.coordinator_agents(workspace, "Use the author's language.")
    assert workers
    for worker in workers.values():
        assert not any(tool.endswith("report_workflow_step") for tool in worker["tools"])


@pytest.mark.parametrize(
    "mode,stage", [("proposal", None), ("proposal", "methods"), ("manuscript", None), ("manuscript", "autorebuttal")]
)
def test_stage_is_optional_and_completion_has_no_artifact_quality_gate(workflow_db, mode, stage):
    """Finishing useful work does not require a source, predecessor, or artifact."""
    db, workspace, run = workflow_db
    workspace.mode = mode
    workspace.proposal_stage_states = {}
    run.artifact_object_key = None
    report = step_payload(status="completed", summary="Analysis finished; remaining evidence gaps are documented.")
    if stage is not None:
        report["stage"] = stage
    saved = coordinator.record_workflow_step(db, workspace, run, report)
    assert saved["status"] == "completed"
    assert saved.get("stage", "") == (stage or "")
    assert saved["outputPaths"] == []
    assert workspace.proposal_stage_states == {}
    assert run.artifact_object_key is None


@pytest.mark.parametrize("mode", ["proposal", "manuscript"])
def test_unknown_stage_is_preserved_as_metadata_without_business_gating(workflow_db, mode):
    """Custom work units can use stage labels outside the fixed artifact adapters."""
    db, workspace, run = workflow_db
    workspace.mode = mode
    report = step_payload(stage="author-defined-evidence-comparison", status="completed")
    saved = coordinator.record_workflow_step(db, workspace, run, report)
    assert saved["stage"] == report["stage"]
    assert saved["status"] == "completed"
    db.commit()
    db.expire_all()
    assert restored_steps(db, workspace) == [saved]


def test_persisted_session_names_have_no_fixed_workspace_naming_requirement(workflow_db):
    """The authorized run's stored conversation identity is trusted as opaque."""
    db, workspace, run = workflow_db
    run.session_id = "another-valid-legacy-conversation"
    saved = coordinator.record_workflow_step(db, workspace, run, step_payload())
    db.commit()
    db.expire_all()
    progress = coordinator.load_progress_snapshot(db, workspace)
    assert progress["sessionId"] == run.session_id
    assert progress["workflowSteps"] == [saved]


def test_returns_are_stable_snapshots_not_mutated_by_later_revisions(workflow_db):
    """A later report must not rewrite an already-returned API response object."""
    db, workspace, run = workflow_db
    returned = coordinator.record_workflow_step(db, workspace, run, step_payload())
    before = deepcopy(returned)
    coordinator.record_workflow_step(db, workspace, run, step_payload(summary="New revision."))
    assert returned == before


def test_workflow_read_does_not_invent_steps_from_available_stage_metadata(workflow_db):
    """A fresh workflow exposes capabilities without manufacturing future steps."""
    db, workspace, _run = workflow_db
    context = coordinator.workflow_context(db, workspace)
    assert paper_workflow.get_research_workflow(workspace.mode).stages
    assert context["progress"] is None or context["progress"].get("workflowSteps", []) == []


def test_partial_step_update_preserves_existing_design_and_output_paths(workflow_db):
    """A status-only report cannot silently discard the work unit's details."""
    db, workspace, run = workflow_db
    first = coordinator.record_workflow_step(
        db, workspace, run, step_payload(outputPaths=["drafts/evidence.md"], stage="methods")
    )
    update = {"id": first["id"], "title": first["title"], "status": "completed"}
    changed = coordinator.record_workflow_step(db, workspace, run, update)
    assert changed["status"] == "completed"
    for key in ("goal", "approach", "summary", "stage", "outputPaths"):
        assert changed[key] == first[key]
    sequence = run.request_payload["research_workflow_sequence"]
    assert coordinator.record_workflow_step(db, workspace, run, update) == changed
    assert run.request_payload["research_workflow_sequence"] == sequence


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (("running", "completed"), "running"),
        (("waiting", "completed"), "waiting"),
        (("running", "waiting"), "running"),
        (("completed", "failed"), "failed"),
    ],
)
def test_logical_only_progress_uses_active_work_before_last_completed_step(workflow_db, statuses, expected):
    """A later completed report cannot hide another step still running or waiting."""
    db, workspace, run = workflow_db
    for index, status in enumerate(statuses):
        coordinator.record_workflow_step(db, workspace, run, step_payload(f"step-{index}", status=status))
    db.commit()
    db.expire_all()
    progress = coordinator.load_progress_snapshot(db, workspace)
    assert progress["status"] == expected
    assert progress["agents"] == []
    assert [step["status"] for step in progress["workflowSteps"]] == list(statuses)


@pytest.fixture
def workflow_api(tmp_path, monkeypatch):
    """Exercise the real hidden route against isolated, persistent SQL sessions."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.llm4ad import paper_runtime

    engine = create_engine(f"sqlite:///{tmp_path / 'workflow-api.sqlite'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(
        engine,
        tables=[
            model.__table__
            for model in (
                models.User,
                models.LLMProvider,
                models.PaperWorkspace,
                models.PaperSourceVersion,
                models.PaperAgentRun,
            )
        ],
    )
    monkeypatch.setattr(paper_runtime, "engine", engine)
    user = models.User(email="workflow-api@example.com", hashed_password="unused")
    with Session(engine) as db:
        workspace = models.PaperWorkspace(user_id=user.id, title="Workflow API", mode="proposal")
        foreign = models.PaperWorkspace(user_id=uuid.uuid4(), title="Foreign", mode="proposal")
        run = models.PaperAgentRun(
            id=paper_runtime._native_run_id(workspace.id, "workflow"),
            workspace_id=workspace.id,
            run_kind="research_workflow",
            session_id="legacy-native-session",
            request_payload={"auto_workflow": True, "native_runtime": "cloudcli"},
        )
        foreign_run = models.PaperAgentRun(
            workspace_id=foreign.id,
            run_kind="research_workflow",
            session_id=f"{foreign.id.hex}-workflow",
            request_payload={"auto_workflow": True, "native_runtime": "cloudcli"},
        )
        non_coordinator = models.PaperAgentRun(
            workspace_id=workspace.id,
            run_kind="proposal_methods",
            session_id=f"{workspace.id.hex}-methods",
            request_payload={"auto_workflow": False, "native_runtime": "cloudcli"},
        )
        child = models.PaperAgentRun(
            workspace_id=workspace.id,
            run_kind="research_workflow",
            session_id=run.session_id,
            request_payload={
                "auto_workflow": True,
                "native_runtime": "cloudcli",
                "coordinator_run_id": str(run.id),
            },
        )
        db.add_all([workspace, foreign, run, foreign_run, non_coordinator, child])
        db.commit()
        ids = {
            "workspace": workspace.id,
            "run": run.id,
            "foreign_run": foreign_run.id,
            "non_coordinator": non_coordinator.id,
            "child": child.id,
            "session": run.session_id,
        }
    auth_calls = []

    def get_db():
        with Session(engine) as db:
            yield db

    def get_current_user():
        auth_calls.append("browser-auth")
        return user

    app = FastAPI()
    app.include_router(paper_runtime.router)
    app.dependency_overrides[deps.get_db] = get_db
    app.dependency_overrides[deps.get_current_user] = get_current_user
    headers = {"x-llm4ad-runtime-token": paper_runtime.paper_workspace_runtime_token(ids["workspace"])}
    try:
        with TestClient(app) as client:
            yield client, engine, ids, headers, auth_calls
    finally:
        engine.dispose()


def post_step(api, report, *, run_key="run", headers=None):
    """Post the public report directly without generated SDK request types."""
    client, _engine, ids, expected_headers, _auth_calls = api
    return client.post(
        "/papers/runtime/workflow-step",
        params={"workspace_id": str(ids["workspace"]), "run_id": str(ids[run_key])},
        headers=expected_headers if headers is None else headers,
        json=report,
    )


def test_hidden_route_persists_with_runtime_token_and_without_browser_auth(workflow_api):
    """The MCP report endpoint is hidden and needs only its runtime capability."""
    client, engine, ids, _headers, auth_calls = workflow_api
    response = post_step(
        workflow_api, step_payload(outputPaths=["drafts/routes.md"], stage="author-defined-comparison")
    )
    assert response.status_code == 200
    assert set(response.json()) == {"step"}
    step = response.json()["step"]
    assert step["outputPaths"] == ["drafts/routes.md"]
    assert step["stage"] == "author-defined-comparison"
    assert step["runId"] == str(ids["run"])
    assert auth_calls == []
    assert "/papers/runtime/workflow-step" not in client.get("/openapi.json").json()["paths"]
    with Session(engine) as db:
        workspace = db.get(models.PaperWorkspace, ids["workspace"])
        assert restored_steps(db, workspace) == [step]


@pytest.mark.parametrize("headers", [{}, {"x-llm4ad-runtime-token": "incorrect-runtime-token"}])
def test_hidden_route_rejects_missing_or_invalid_runtime_tokens(workflow_api, headers):
    """A browser session or anonymous caller cannot replace the runtime token."""
    _client, engine, ids, _expected, auth_calls = workflow_api
    assert post_step(workflow_api, step_payload(), headers=headers).status_code == 401
    assert auth_calls == []
    with Session(engine) as db:
        assert not db.get(models.PaperAgentRun, ids["run"]).request_payload.get("research_workflow_steps")


@pytest.mark.parametrize("run_key,status", [("foreign_run", 404), ("non_coordinator", 403), ("child", 403)])
def test_hidden_route_checks_workspace_and_parent_coordinator_scope(workflow_api, run_key, status):
    """A valid workspace token does not authorize foreign or child run reports."""
    _client, engine, ids, _headers, _calls = workflow_api
    response = post_step(workflow_api, step_payload(), run_key=run_key)
    assert response.status_code == status
    with Session(engine) as db:
        assert not db.get(models.PaperAgentRun, ids[run_key]).request_payload.get("research_workflow_steps")


@pytest.mark.parametrize(
    "updates", [{"id": ""}, {"title": ""}, {"status": "pending"}, {"outputPaths": "drafts/routes.md"}]
)
def test_hidden_route_returns_422_for_invalid_reports_without_persisting(workflow_api, updates):
    """Malformed logical reports fail validation before the database is changed."""
    _client, engine, ids, _headers, _calls = workflow_api
    assert post_step(workflow_api, step_payload(**updates)).status_code == 422
    with Session(engine) as db:
        assert not db.get(models.PaperAgentRun, ids["run"]).request_payload.get("research_workflow_steps")


def test_old_raw_posts_return_enriched_progress_without_erasing_workflow(workflow_api):
    """Legacy transcript updates round-trip alongside newer explicit work units."""
    client, engine, ids, _headers, _calls = workflow_api
    reported = post_step(workflow_api, step_payload(status="completed")).json()["step"]
    progress_url = f"/papers/workspaces/{ids['workspace']}/runtime-progress"
    raw = {
        "sessionId": ids["session"],
        "status": "ready",
        "currentTool": None,
        "agents": [],
        "workflowSteps": [],
        "workflowSequence": 0,
    }
    for _ in range(2):
        response = client.post(progress_url, json=raw)
        assert response.status_code == 200
        assert response.json()["progress"]["workflowSteps"] == [reported]
        assert client.get(progress_url).json() == response.json()
    with Session(engine) as db:
        run = db.get(models.PaperAgentRun, ids["run"])
        assert run.request_payload["research_workflow_sequence"] == 1
        assert run.request_payload["research_progress"]["agents"] == []


def test_hidden_route_reopen_retry_is_idempotent_after_previous_completion(workflow_api):
    """Reopening advances one logical revision while network retries do not."""
    _client, engine, ids, _headers, _calls = workflow_api
    done = post_step(workflow_api, step_payload(status="completed")).json()["step"]
    report = step_payload(status="running", summary="Reopened for the author's new evidence.")
    reopened = post_step(workflow_api, report).json()["step"]
    retry = post_step(workflow_api, report).json()["step"]
    assert retry == reopened
    assert reopened["revision"] == done["revision"] + 1
    with Session(engine) as db:
        run = db.get(models.PaperAgentRun, ids["run"])
        workspace = db.get(models.PaperWorkspace, ids["workspace"])
        assert run.request_payload["research_workflow_sequence"] == 2
        assert restored_steps(db, workspace) == [reopened]
