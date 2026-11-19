"""Focused contracts for actual progress and unified research assistants."""

import copy
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from app import models
from app.services import paper_workflow
from app.services import paper_workflow_coordinator as coordinator


@pytest.fixture
def research_db():
    """Provide an isolated coordinator database without external services."""
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
    with Session(engine) as db:
        workspace = models.PaperWorkspace(user_id=uuid.uuid4(), title="Research", mode="proposal")
        run = models.PaperAgentRun(
            workspace_id=workspace.id,
            run_kind="research_workflow",
            session_id="conversation",
            request_payload={"auto_workflow": True, "native_runtime": "cloudcli"},
        )
        db.add(workspace)
        db.add(run)
        db.commit()
        yield db, workspace, run


def progress(step_id="task-1", status="running", session_id="conversation"):
    """Build a snapshot of an observed invocation."""
    return {
        "sessionId": session_id,
        "status": status,
        "currentTool": "Read",
        "agents": [
            {
                "id": step_id,
                "parentId": "main",
                "stage": "methods",
                "description": "Assess method",
                "status": status,
                "toolCount": 1,
                "currentTool": "Read",
                "tools": [{"id": "read-1", "name": "Read", "done": status == "completed", "failed": False}],
            }
        ],
    }


def test_reconnect_restores_actual_attempts_and_deduplicates_events(research_db):
    """New sessions can restore history without inventing pending stages."""
    db, workspace, run = research_db
    assert coordinator.load_progress_snapshot(db, workspace) is None
    first = coordinator.record_progress_snapshot(db, workspace, run, progress())
    assert coordinator.record_progress_snapshot(db, workspace, run, progress()) == first
    assert len(run.request_payload["research_progress_events"]) == 1
    coordinator.record_progress_snapshot(db, workspace, run, progress(status="completed"))
    coordinator.record_progress_snapshot(db, workspace, run, progress("task-2", "waiting"))
    db.commit()
    db.expire_all()
    restored = coordinator.load_progress_snapshot(db, workspace)
    assert [(item["id"], item["status"]) for item in restored["agents"]] == [
        ("task-1", "completed"),
        ("task-2", "waiting"),
    ]
    assert restored["sequence"] == 3
    assert all(item["runId"] == str(run.id) for item in restored["agents"])
    assert len(run.request_payload["research_progress_events"]) == 3


def test_stale_replay_cannot_revive_finished_work_or_erase_history(research_db):
    """Truncated reconnect snapshots retain completed outcomes and tool evidence."""
    db, workspace, run = research_db
    done = coordinator.record_progress_snapshot(db, workspace, run, progress(status="completed"))
    replay = coordinator.record_progress_snapshot(db, workspace, run, progress())
    assert replay["agents"] == done["agents"]
    snapshot = progress("task-2")
    snapshot["sequence"] = 0
    assert coordinator.record_progress_snapshot(db, workspace, run, snapshot) == replay
    assert len(run.request_payload["research_progress_events"]) == 2


def test_reused_tool_ids_across_runs_remain_distinct(research_db):
    """Run identity qualifies actual tool-call identity in restored history."""
    db, workspace, first = research_db
    coordinator.record_progress_snapshot(db, workspace, first, progress(status="completed"))
    second = models.PaperAgentRun(workspace_id=workspace.id, run_kind="research_workflow", session_id="second")
    db.add(second)
    coordinator.record_progress_snapshot(db, workspace, second, progress(session_id="second"))
    db.commit()
    restored = coordinator.load_progress_snapshot(db, workspace)
    assert len(restored["agents"]) == 2
    assert {step["runId"] for step in restored["agents"]} == {str(first.id), str(second.id)}


def test_progress_is_scoped_and_future_statuses_are_rejected(research_db):
    """Reject cross-session progress and fabricated pending execution rows."""
    db, workspace, run = research_db
    with pytest.raises(HTTPException):
        coordinator.record_progress_snapshot(db, workspace, run, progress(session_id="other"))
    with pytest.raises(ValidationError):
        coordinator.record_progress_snapshot(db, workspace, run, progress(status="pending"))
    assert not run.request_payload.get("research_progress_events")


def test_attempt_keys_make_retries_idempotent_and_revisions_distinct(research_db):
    """Artifact keys remain stable while each real publication has its own run."""
    db, workspace, run = research_db
    first = coordinator.publication_run(db, workspace, run, "methods", "first")
    first.artifact_object_key = "history/first-result.json"
    first.status = "completed"
    db.add(first)
    retry = coordinator.publication_run(db, workspace, run, "methods", "first")
    revised = coordinator.publication_run(db, workspace, run, "methods", "second")
    assert first.id == retry.id and first.id != revised.id
    assert db.get(models.PaperAgentRun, first.id).artifact_object_key == "history/first-result.json"
    assert first.status == "completed"
    assert coordinator.publication_run(db, workspace, run, "methods").id != revised.id
    assert first.run_kind == revised.run_kind == "proposal_methods"
    assert first.request_payload["auto_workflow"] is True
    assert first.request_payload["writable_paths"] == coordinator.workflow_paths(
        paper_workflow.get_research_workflow("proposal"),
        "proposal.md",
    )
    assert "references.bib" in first.request_payload["writable_paths"]
    with pytest.raises(HTTPException):
        coordinator.publication_run(db, workspace, run, "ac_summary", "foreign")


@pytest.mark.parametrize("mode", ["proposal", "manuscript"])
def test_optional_helper_methods_resolve_without_fixed_worker_bindings(mode):
    """Domain guidance stays available without requiring one worker per adapter."""
    root = Path(__file__).resolve().parents[4] / "skills"
    workflow = paper_workflow.get_research_workflow(mode)
    workspace = models.PaperWorkspace(user_id=uuid.uuid4(), title="Research", mode=mode)
    helpers = coordinator.coordinator_agents(workspace, "Use Chinese explanations.")
    skill_roots = {
        "openair-proposal": root / "openair-proposal",
        "autorebuttal-workflow": root / "autorebuttal",
        "research-stage-publication": root / "research-stage-publication",
        "research-workflow-coordinator": root / "research-workflow-coordinator",
        "typst-author": root / "typst-author",
    }
    for skill in coordinator.coordinator_skills(workflow):
        assert (skill_roots[skill] / "SKILL.md").is_file()
    for stage in workflow.stages:
        assert (
            skill_roots[coordinator.UNIFIED_SKILLS[mode]] / "references" / f"{coordinator.METHOD_BY_STAGE[stage]}.md"
        ).is_file()
    assert set(helpers) == {"analysis", "reference-check"}
    for helper in helpers.values():
        assert helper["model"] == "inherit"
        assert "isolated scratch drafts" not in helper["prompt"]
        assert "Return findings, proposed text diffs" in helper["prompt"]
        assert "Shared documents under /workspace/source are read-only" in helper["prompt"]
        assert "Use Chinese explanations" in helper["prompt"]
        assert "mcp__llm4ad_stage__get_region_context" in helper["tools"]
        assert (
            "Before analysis, read get_workflow_context and the read-only get_region_context together"
            in helper["prompt"]
        )
        assert "get_region_context as the authoritative current text, including manual author edits" in helper["prompt"]
        assert "published workflow artifacts and file mirrors may contain older text" in helper["prompt"]
        assert "revision before analysis" in helper["prompt"]
        assert "base_revision" in helper["prompt"]
        assert not any("publish" in tool for tool in helper["tools"])
        assert not {"Write", "Edit", "Bash"}.intersection(helper["tools"])


def test_persisting_progress_does_not_mutate_previously_returned_snapshots(research_db):
    """Event history is durable evidence, not references to mutable live state."""
    db, workspace, run = research_db
    first = coordinator.record_progress_snapshot(db, workspace, run, progress())
    saved = copy.deepcopy(first)
    coordinator.record_progress_snapshot(db, workspace, run, progress(status="completed"))
    assert first == saved
    assert run.request_payload["research_progress_events"][0]["agents"][0]["status"] == "running"


def test_context_capabilities_do_not_start_future_work(research_db):
    """Internal method metadata never becomes a fabricated execution event."""
    db, workspace, _run = research_db
    context = coordinator.workflow_context(db, workspace)
    assert context["progress"] is None
    assert all(stage["prerequisites_satisfied"] for stage in context["stages"])
    assert all(stage["dependencies_advisory"] for stage in context["stages"])
    assert context["stage_keys_are_artifact_adapters"] is True
    assert all(not stage["prerequisites"] and "agent" not in stage for stage in context["stages"])
    assert context["proposal_entry_path"] == "proposal.md"
    assert context["authoring_format"] == "markdown"
    assert context["source_version_id"] is None
    assert context["source_content_hash"] is None


def test_context_retains_logical_work_when_raw_progress_has_no_workers(research_db):
    """A single main agent can report research independently of transcript tools."""
    db, workspace, run = research_db
    report = {
        "id": "design-comparison",
        "title": "Choose a comparison design",
        "goal": "Separate the effect of the proposed method from the data change.",
        "approach": "Hold data fixed and compare the two variants with the same metrics.",
        "stage": "methods",
        "status": "running",
    }
    started = coordinator.record_workflow_step(db, workspace, run, report)
    initial = coordinator.workflow_context(db, workspace)["progress"]
    assert initial["workflowSteps"] == [started]
    assert initial["workflowSequence"] == 1
    assert initial["agents"] == []
    raw = {"sessionId": run.session_id, "status": "running", "currentTool": "Read", "agents": []}
    coordinator.record_progress_snapshot(db, workspace, run, raw)
    outcome = coordinator.record_workflow_step(
        db,
        workspace,
        run,
        {"id": report["id"], "title": report["title"], "status": "completed", "summary": "Selected paired variants."},
    )
    context = coordinator.workflow_context(db, workspace)
    assert context["progress"]["workflowSteps"] == [outcome]
    assert context["progress"]["workflowSequence"] == 2
    assert context["progress"]["sequence"] == 1
    assert outcome["goal"] == report["goal"] and outcome["approach"] == report["approach"]
    assert outcome["revision"] == 2


def test_logical_identity_survives_revisits_without_collapsing_shared_adapters(research_db):
    """Distinct goals can use one adapter and the same goal can resume in a new run."""
    db, workspace, first = research_db
    for step_id in ("identify-confounders", "choose-controls"):
        coordinator.record_workflow_step(
            db,
            workspace,
            first,
            {
                "id": step_id,
                "title": step_id,
                "stage": "methods",
                "status": "completed",
            },
        )
    second = models.PaperAgentRun(
        workspace_id=workspace.id,
        run_kind="research_workflow",
        session_id="revisit",
        request_payload={"auto_workflow": True},
    )
    db.add(second)
    revised = coordinator.record_workflow_step(
        db,
        workspace,
        second,
        {
            "id": "choose-controls",
            "title": "Revise the control design",
            "stage": "methods",
            "goal": "Account for the newly confirmed limitation.",
            "status": "running",
        },
    )
    db.commit()
    context = coordinator.workflow_context(db, workspace)
    steps = {item["id"]: item for item in context["progress"]["workflowSteps"]}
    assert set(steps) == {"identify-confounders", "choose-controls"}
    assert steps["choose-controls"] == revised
    assert steps["identify-confounders"]["status"] == "completed"


def test_source_context_and_publication_keep_the_actual_draft_base(research_db):
    """A newer manual source revision cannot silently replace a draft's base."""
    db, workspace, run = research_db
    versions = [
        models.PaperSourceVersion(
            workspace_id=workspace.id,
            version=index,
            source_kind="typst",
            filename="proposal.typ",
            object_key=f"test/source-{index}.typ",
            content_hash=f"hash-{index}",
            content_size=index,
        )
        for index in (1, 2)
    ]
    db.add_all(versions)
    db.flush()
    run.source_version_id = versions[0].id
    workspace.active_source_version_id = versions[1].id
    db.add(run)
    db.add(workspace)
    context = coordinator.workflow_context(db, workspace)
    assert context["source_version_id"] == str(versions[1].id)
    assert context["source_content_hash"] == "hash-2"
    original = coordinator.publication_run(db, workspace, run, "methods", "old-draft")
    assert original.source_version_id == versions[0].id
    assert original.request_payload["base_content_hash"] == "hash-1"
    fresh = coordinator.publication_run(
        db,
        workspace,
        run,
        "methods",
        "new-draft",
        base_source_version_id=versions[1].id,
        base_content_hash="hash-2",
    )
    assert fresh.source_version_id == versions[1].id
    assert fresh.request_payload["base_content_hash"] == "hash-2"
    with pytest.raises(HTTPException) as error:
        coordinator.publication_run(
            db,
            workspace,
            run,
            "methods",
            "old-draft",
            base_source_version_id=versions[1].id,
            base_content_hash="hash-2",
        )
    assert error.value.status_code == 409
    with pytest.raises(HTTPException):
        coordinator.publication_run(
            db,
            workspace,
            run,
            "methods",
            "new-draft",
            base_source_version_id=versions[1].id,
            base_content_hash="changed-hash",
        )
    assert original.source_version_id == versions[0].id
