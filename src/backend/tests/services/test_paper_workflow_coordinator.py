"""Contracts for optional helpers, revision context and legacy publication."""

import uuid
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request
from sqlmodel import Session, SQLModel, create_engine

from app import models
from app.api.llm4ad import paper_runtime
from app.services import paper_workflow
from app.services import paper_workflow_coordinator as coordinator
from app.tasks import paper_agent


def test_optional_helpers_load_domain_skills_and_inherit_the_bound_model():
    """Helpers receive explicit methods and context without stage assignment."""
    workspace = models.PaperWorkspace(
        user_id=uuid.uuid4(), title="Proposal", mode="proposal", proposal_entry_path="main.typ"
    )
    agents = coordinator.coordinator_agents(workspace, "\nExplain findings in Chinese.")
    workflow = paper_workflow.get_research_workflow("proposal")
    assert set(agents) == {"analysis", "reference-check"}
    for helper in agents.values():
        assert helper["skills"] == ["openair-proposal"]
        assert helper["model"] == "inherit"
        assert "main.typ" in helper["prompt"]
        assert "Explain findings in Chinese" in helper["prompt"]
        assert "final text response" in helper["prompt"]
        assert not {"Write", "Edit", "Bash", "Agent", "Task"}.intersection(helper["tools"])
        assert not any("publish" in tool or "propose_region_change" in tool for tool in helper["tools"])
    assert coordinator.COORDINATOR_SKILL in coordinator.coordinator_skills(workflow)


def test_rebuttal_helpers_keep_sources_read_only_and_search_available():
    """Both helper roles can check references without receiving mutating tools."""
    workspace = models.PaperWorkspace(user_id=uuid.uuid4(), title="Rebuttal", mode="manuscript")
    references = ["mcp__arxiv__search_papers", "mcp__research_search__search", "mcp__research_search__read_webpage"]
    helpers = coordinator.coordinator_agents(
        workspace, "", [*references, "Write", "Edit", "Bash", "mcp__llm4ad_stage__publish_research_revision"]
    )
    for helper in helpers.values():
        assert helper["skills"] == ["autorebuttal-workflow"]
        assert set(references).issubset(helper["tools"])
        assert set(helper["tools"]) == {
            "Read",
            "Glob",
            "Grep",
            "mcp__llm4ad_stage__get_workflow_context",
            "mcp__llm4ad_stage__get_region_context",
            *references,
        }
        assert "Shared documents under /workspace/source are read-only" in helper["prompt"]
        assert "author approves the targeted diff" in helper["prompt"]


@pytest.mark.parametrize("entry", [None, "proposal.md", "目录/申请书.MD"])
def test_markdown_proposals_do_not_load_typst_authoring(entry):
    """The default proposal never injects legacy authoring or fixed Typst files."""
    workflow = paper_workflow.get_research_workflow("proposal")
    assert "typst-author" not in coordinator.coordinator_skills(workflow, entry)
    paths = coordinator.workflow_paths(workflow, entry or "proposal.md")
    assert (entry or "proposal.md") in paths
    assert "sections/" in paths and "references.bib" in paths
    assert not any(path.endswith(".typ") for path in paths)
    assert all(coordinator.stage_paths(workflow, stage, entry or "proposal.md") == paths for stage in workflow.stages)


def test_legacy_typst_entry_can_retain_authoring_skill():
    """An explicit existing Typst entry keeps compatibility without changing defaults."""
    workflow = paper_workflow.get_research_workflow("proposal")
    assert "typst-author" in coordinator.coordinator_skills(workflow, "legacy/Main.TYP")


@pytest.mark.parametrize("mode", ["algorithm", "autodesign"])
def test_nonunified_workflows_retain_original_stage_workers(mode):
    """Discovery and design keep their stage-specific methods and capabilities."""
    workspace = models.PaperWorkspace(user_id=uuid.uuid4(), title="Existing workflow", mode=mode)
    workflow = paper_workflow.get_research_workflow(mode)
    workers = coordinator.coordinator_agents(workspace, "Preserve these author preferences.")
    assert list(workers) == [stage.replace("_", "-") for stage in workflow.stages]
    for stage in workflow.stages:
        worker = workers[stage.replace("_", "-")]
        paths = coordinator.stage_paths(workflow, stage, "proposal.typ")
        assert worker["model"] == "inherit"
        assert worker["skills"] == list(workflow.skills_by_run_kind[workflow.run_kind_by_stage[stage]])
        expected_tools = ["Read", "Glob", "Grep", "mcp__llm4ad_stage__get_workflow_context"]
        if paths:
            expected_tools.extend(["Write", "Edit", "mcp__llm4ad_stage__check_typst"])
        assert worker["tools"] == expected_tools
        assert f"You are the {stage} stage worker" in worker["prompt"]
        assert f"Edit only these stage-owned paths: {paths}" in worker["prompt"]
        assert "publish_stage_result" in worker["prompt"]
        assert "publish_research_revision" not in worker["prompt"]
        assert "Preserve these author preferences" in worker["prompt"]


@pytest.mark.parametrize("mode", ["algorithm", "autodesign"])
def test_nonunified_context_requires_no_revision_or_reviewer_tables(mode, monkeypatch):
    """Legacy context stays usable without proposal/rebuttal publication services."""
    from app.schemas import paper_publication
    from app.services import paper_revision_history

    state_hash = Mock(side_effect=AssertionError("Unified revision service must not run"))
    schemas = Mock(side_effect=AssertionError("Unified publication schemas must not load"))
    monkeypatch.setattr(paper_revision_history, "current_state_hash", state_hash)
    monkeypatch.setattr(paper_publication, "publication_schemas", schemas)
    engine = create_engine("sqlite://")
    models.PaperAgentRun.__table__.create(engine)
    workspace = models.PaperWorkspace(user_id=uuid.uuid4(), title="Existing workflow", mode=mode)
    workflow = paper_workflow.get_research_workflow(mode)
    with Session(engine) as db:
        context = coordinator.workflow_context(db, workspace)
        assert context["publication_tool"] == "publish_stage_result"
        assert not {"revision", "publication_schemas", "optional_helpers", "source_writes"}.intersection(context)
        assert context["stage_keys_are_artifact_adapters"] is False
        assert context["progress"] is None
        for item, stage in zip(context["stages"], workflow.stages, strict=True):
            kind = workflow.run_kind_by_stage[stage]
            assert item["agent"] == stage.replace("_", "-")
            assert item["skills"] == list(workflow.skills_by_run_kind[kind])
            assert item["prerequisites"] == list(workflow.prerequisites_by_run_kind.get(kind, ()))
            assert item["writable_paths"] == coordinator.stage_paths(workflow, stage, "proposal.typ")
        if mode == "algorithm":
            assert context["stages"][0]["prerequisites_satisfied"] is True
            assert all(not item["prerequisites_satisfied"] for item in context["stages"][1:])
            workspace.proposal_stage_states = {"discovery_baseline": {"status": "ready"}}
            refreshed = coordinator.workflow_context(db, workspace)
            assert refreshed["stages"][1]["prerequisites_satisfied"] is True
            assert refreshed["stages"][2]["prerequisites_satisfied"] is False
    state_hash.assert_not_called()
    schemas.assert_not_called()


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
                models.PaperReview,
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
    stage = coordinator.publication_run(db, workspace, main, "autorebuttal", "revision-1")
    assert stage.id == uuid.uuid5(main.id, "publication:autorebuttal:revision-1")
    assert stage.run_kind == "autorebuttal" and stage.id != main.id
    assert coordinator.publication_run(db, workspace, main, "autorebuttal", "revision-1").id == stage.id
    assert coordinator.publication_run(db, workspace, main, "autorebuttal", "revision-2").id != stage.id
    with pytest.raises(HTTPException) as error:
        coordinator.publication_run(db, workspace, main, "methods")
    assert error.value.status_code == 422


def test_workflow_context_restores_current_state_without_guessing(coordinator_db):
    """Empty stages stay pending, so opening a conversation does not fabricate work."""
    db, workspace, _main = coordinator_db
    result = coordinator.workflow_context(db, workspace)
    assert [stage["stage"] for stage in result["stages"]] == ["rebuttal_baseline", "autorebuttal", "ac_summary"]
    assert all(stage["status"] == "pending" for stage in result["stages"])
    assert all(stage["prerequisites"] == [] for stage in result["stages"])
    assert all("agent" not in stage and "writable_paths" not in stage for stage in result["stages"])


def test_context_discloses_exact_publication_schema_and_fresh_state_hash(coordinator_db, monkeypatch):
    """A context refresh reads the authoritative state rather than a cached source hash."""
    from app.schemas.paper_publication import publication_schemas
    from app.services import paper_revision_history

    db, workspace, _main = coordinator_db
    revision_api = Mock(wraps=paper_revision_history.current_state_hash)
    monkeypatch.setattr(paper_revision_history, "current_state_hash", revision_api)
    first = coordinator.workflow_context(db, workspace)
    revision_api.assert_called_once_with(db, workspace)
    assert len(first["revision"]) == 64
    assert first["publication_tool"] == "publish_research_revision"
    assert first["publication_schemas"] == publication_schemas()
    assert first["publication_schemas"]["properties"]["base_revision"]["type"] == "string"
    assert first["regional_edits_authoritative"] is True
    assert "worker_draft_root" not in first
    original_revision = first["revision"]
    workspace.rebuttal_entries = [{"id": "response-1", "response": "Current author correction."}]
    second = coordinator.workflow_context(db, workspace)
    assert second["revision"] != original_revision
    assert first["revision"] == original_revision
    assert second["source_content_hash"] == first["source_content_hash"] == "source"


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
        assert response["run_id"] == str(uuid.uuid5(main.id, f"publication:{stage}:same-key"))
    assert called == ["rebuttal_baseline", "autorebuttal"]


def test_proposal_publication_preserves_the_requested_entry_and_stage_paths(coordinator_db):
    """A coordinator cannot accidentally lose the formatting entry contract."""
    db, workspace, main = coordinator_db
    workspace.mode = "proposal"
    workspace.proposal_entry_path = "main.typ"
    run = coordinator.publication_run(db, workspace, main, "formatting")
    assert run.request_payload["source_path"] == "main.typ"
    assert run.request_payload["writable_paths"] == coordinator.workflow_paths(
        paper_workflow.get_research_workflow("proposal"), "main.typ"
    )
    assert run.request_payload["auto_workflow"] is True


def test_chair_stage_can_follow_a_published_draft_with_author_actions(coordinator_db):
    """Expose the existing chair rule instead of introducing an extra blocker."""
    db, workspace, _main = coordinator_db
    workspace.proposal_stage_states = {"autorebuttal": {"status": "needs_revision"}}
    context = coordinator.workflow_context(db, workspace)
    assert context["stages"][-1]["stage"] == "ac_summary"
    assert context["stages"][-1]["prerequisites_satisfied"] is True
