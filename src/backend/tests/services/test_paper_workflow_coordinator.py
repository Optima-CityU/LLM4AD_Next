"""Contracts for coordinator delegation and stage-scoped publication."""

import uuid

import pytest
from fastapi import HTTPException, Request
from sqlmodel import Session, SQLModel, create_engine

from app import models
from app.api.llm4ad import paper_runtime
from app.services import paper_workflow
from app.services import paper_workflow_coordinator as coordinator
from app.tasks import paper_agent


def test_workers_load_their_own_skills_and_inherit_the_bound_model():
    """Stage methods and author preferences reach workers explicitly."""
    workspace = models.PaperWorkspace(
        user_id=uuid.uuid4(), title="Proposal", mode="proposal", proposal_entry_path="main.typ"
    )
    agents = coordinator.coordinator_agents(workspace, "\nExplain findings in Chinese.")
    workflow = paper_workflow.get_research_workflow("proposal")
    assert list(agents) == [stage.replace("_", "-") for stage in workflow.stages]
    assert agents["formatting"]["skills"] == list(workflow.skills_by_run_kind["proposal_formatting"])
    assert agents["formatting"]["model"] == "inherit"
    assert "main.typ" in agents["formatting"]["prompt"]
    assert "Explain findings in Chinese" in agents["methods"]["prompt"]
    assert "mcp__llm4ad_stage__publish_stage_result" not in agents["methods"]["tools"]
    assert coordinator.COORDINATOR_SKILL in coordinator.coordinator_skills(workflow)


def test_rebuttal_workers_keep_existing_read_only_methods():
    """Do not give paper-writing tools to the rebuttal stage workers."""
    workspace = models.PaperWorkspace(user_id=uuid.uuid4(), title="Rebuttal", mode="manuscript")
    workers = coordinator.coordinator_agents(workspace, "")
    assert list(workers) == ["rebuttal-baseline", "autorebuttal", "ac-summary"]
    assert workers["autorebuttal"]["skills"] == ["autorebuttal", "research-stage-publication"]
    assert not set(workers["autorebuttal"]["tools"]) & {"Write", "Edit", "Bash"}


@pytest.fixture
def coordinator_db(monkeypatch):
    """Provide isolated tables for native coordinator routing tests."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            m.__table__
            for m in (
                models.User,
                models.LLMProvider,
                models.PaperWorkspace,
                models.PaperSourceVersion,
                models.PaperAgentRun,
            )
        ],
    )
    monkeypatch.setattr(paper_runtime, "engine", engine)
    with Session(engine) as db:
        workspace = models.PaperWorkspace(user_id=uuid.uuid4(), title="Rebuttal", mode="manuscript")
        db.add(workspace)
        db.flush()
        source = models.PaperSourceVersion(
            workspace_id=workspace.id,
            version=1,
            source_kind="markdown",
            filename="paper.md",
            object_key="paper/test/paper.md",
            content_hash="source",
            content_size=1,
        )
        db.add(source)
        db.flush()
        workspace.active_source_version_id = source.id
        run = models.PaperAgentRun(
            workspace_id=workspace.id,
            source_version_id=source.id,
            run_kind="research_workflow",
            session_id=f"{workspace.id.hex}-workflow",
            request_payload={"native_runtime": "cloudcli", "auto_workflow": True},
        )
        db.add(workspace)
        db.add(run)
        db.commit()
        yield db, workspace, run


def test_publication_uses_existing_stage_identity_and_rejects_foreign_stages(coordinator_db):
    """The coordinator delegates persistence without changing artifact contracts."""
    db, workspace, main = coordinator_db
    stage = coordinator.publication_run(db, workspace, main, "autorebuttal")
    assert stage.id == paper_runtime._native_run_id(workspace.id, "autorebuttal")
    assert stage.run_kind == "autorebuttal" and stage.id != main.id
    assert coordinator.publication_run(db, workspace, main, "autorebuttal").id == stage.id
    with pytest.raises(HTTPException) as error:
        coordinator.publication_run(db, workspace, main, "methods")
    assert error.value.status_code == 422


def test_workflow_context_restores_current_state_without_guessing(coordinator_db):
    """Empty stages stay pending, so opening a conversation does not fabricate work."""
    db, workspace, _main = coordinator_db
    result = coordinator.workflow_context(db, workspace)
    assert [stage["stage"] for stage in result["stages"]] == ["rebuttal_baseline", "autorebuttal", "ac_summary"]
    assert all(stage["status"] == "pending" for stage in result["stages"])
    assert result["stages"][1]["prerequisites"] == ["rebuttal_baseline"]


@pytest.mark.asyncio
async def test_coordinator_routes_each_publication_to_the_real_stage(coordinator_db, monkeypatch):
    """Two coordinator publications reach separate original stage pipelines."""
    db, workspace, main = coordinator_db
    token = paper_runtime.paper_workspace_runtime_token(workspace.id)
    request = Request({"type": "http", "headers": [(b"x-llm4ad-runtime-token", token.encode())]})
    values = {}

    class Redis:
        async def get(self, key):
            return values.get(key)

        async def set(self, key, value, **kwargs):
            if kwargs.get("nx") and key in values:
                return False
            values[key] = value
            return True

        async def delete(self, key):
            values.pop(key, None)

    async def redis():
        return Redis()

    called = []
    monkeypatch.setattr(paper_runtime, "get_async_redis", redis)
    monkeypatch.setattr(paper_runtime, "touch_paper_workspace_active", lambda *_: None)

    def persist(run_id, _user_id, _path):
        db.expire_all()
        stage = db.get(models.PaperAgentRun, run_id)
        called.append(stage.run_kind)
        return False

    monkeypatch.setattr(paper_agent, "_persist_output", persist)
    for stage in ["rebuttal_baseline", "autorebuttal"]:
        response = await paper_runtime.publish_runtime_stage_result(
            paper_runtime._StagePublicationRequest(
                workspace_id=workspace.id, run_id=main.id, workflow_stage=stage, artifact={}, idempotency_key="same-key"
            ),
            request,
        )
        assert response["run_id"] == str(paper_runtime._native_run_id(workspace.id, stage))
    assert called == ["rebuttal_baseline", "autorebuttal"]


def test_proposal_publication_preserves_the_requested_entry_and_stage_paths(coordinator_db):
    """A coordinator cannot accidentally lose the formatting entry contract."""
    db, workspace, main = coordinator_db
    workspace.mode = "proposal"
    workspace.proposal_entry_path = "main.typ"
    run = coordinator.publication_run(db, workspace, main, "formatting")
    assert run.request_payload["source_path"] == "main.typ"
    assert run.request_payload["writable_paths"] == ["main.typ", "styles/", "assets/"]


def test_chair_stage_can_follow_a_published_draft_with_author_actions(coordinator_db):
    """Expose the existing chair rule instead of introducing an extra blocker."""
    db, workspace, _main = coordinator_db
    workspace.proposal_stage_states = {"autorebuttal": {"status": "needs_revision"}}
    context = coordinator.workflow_context(db, workspace)
    assert context["stages"][-1]["stage"] == "ac_summary"
    assert context["stages"][-1]["prerequisites_satisfied"] is True
