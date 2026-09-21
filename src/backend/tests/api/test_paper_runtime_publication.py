"""End-to-end contracts for native research runtime publication."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlmodel import Session, select

from app import models
from app.api.llm4ad import paper_runtime
from app.core.db import engine
from app.schemas import paper as paper_schemas
from app.services import paper_service
from app.tasks import paper_agent
from tests.utils.user import create_random_user


class _AsyncRedis:
    """Minimal async Redis implementation for publication idempotency."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, **kwargs) -> bool:
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


class _ObjectStorage:
    """Capture durable artifacts written through the RustFS abstraction."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload(self, key: str, data: bytes, **_kwargs) -> None:
        self.objects[key] = data.read() if hasattr(data, "read") else data

    def download(self, key: str, **_kwargs) -> bytes:
        return self.objects[key]

    def delete(self, key: str, **_kwargs) -> None:
        self.objects.pop(key, None)


def _publication_request(token: str) -> Request:
    """Build the internal authenticated request used by the MCP bridge."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/runtime/stage-results",
            "headers": [(b"x-llm4ad-runtime-token", token.encode())],
        }
    )


def test_stage_publication_advances_current_stage_and_stales_dependents() -> None:
    """Make the durable workflow state follow a newly published stage result."""
    source_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        run_kind=models.PaperAgentRunKind.PROPOSAL_FORMATTING.value,
    )
    workspace = SimpleNamespace(
        mode=models.ResearchWorkspaceMode.PROPOSAL.value,
        proposal_stage_states={
            "literature": {
                "status": "ready",
                "run_id": str(uuid.uuid4()),
                "source_version_id": str(source_id),
            }
        },
    )

    paper_agent._update_proposal_stage_state(workspace, run, source_id)

    assert workspace.proposal_stage_states["formatting"]["status"] == "ready"
    assert workspace.proposal_stage_states["formatting"]["run_id"] == str(run.id)
    assert workspace.proposal_stage_states["formatting"]["iteration"] == 1
    assert workspace.proposal_stage_states["literature"]["status"] == "stale"

    paper_agent._update_proposal_stage_state(workspace, run, source_id)

    assert workspace.proposal_stage_states["formatting"]["iteration"] == 2


def test_parallel_proposal_stage_only_stales_real_dependents() -> None:
    """Keep a sibling stage ready when another methods-dependent stage changes."""
    source_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        run_kind=models.PaperAgentRunKind.PROPOSAL_INNOVATION_PLAN.value,
    )
    workspace = SimpleNamespace(
        mode=models.ResearchWorkspaceMode.PROPOSAL.value,
        proposal_stage_states={
            "foundation_feasibility": {"status": "ready"},
            "final_review": {"status": "needs_revision"},
        },
    )

    paper_agent._update_proposal_stage_state(workspace, run, source_id)

    assert workspace.proposal_stage_states["innovation_plan"]["status"] == "ready"
    assert workspace.proposal_stage_states["foundation_feasibility"]["status"] == "ready"
    assert workspace.proposal_stage_states["final_review"]["status"] == "stale"


def test_blocked_final_review_requires_an_actionable_finding() -> None:
    """Reject an unexplained state that would leave the author unable to revise."""
    with pytest.raises(ValidationError, match="must include at least one finding"):
        paper_agent.ProposalFinalReviewArtifact.model_validate(
            {
                "entry_path": "proposal.typ",
                "summary": "Review blocked without a reason.",
                "findings": [],
                "ready_for_export": False,
                "context_patch": {"upsert": [], "remove_keys": []},
            }
        )


def test_rebuttal_compliance_preserves_placeholders_and_computes_counts() -> None:
    """Keep unsupported evidence visible and derive counts from final prose."""
    response = "We will report [AUTHOR: measured latency] after verification."
    artifact = paper_agent.RebuttalComplianceArtifact.model_validate(
        {
            "summary": "One author measurement is still required.",
            "entries": [
                {
                    "id": "r1-q1",
                    "reviewer_id": "R1",
                    "label": "Q",
                    "title": "Runtime measurement",
                    "response": response,
                    "concern_ids": ["r1-q1"],
                    "evidence_status": "placeholder",
                    "source_refs": ["paper.md#experiments"],
                    "character_count": 999,
                }
            ],
            "findings": [],
            "ready_for_submission": False,
            "open_placeholders": ["Measure and insert inference latency."],
        }
    )

    assert artifact.entries[0].character_count == len(response)
    assert artifact.entries[0].evidence_status == "placeholder"


def test_rebuttal_baseline_requires_exact_reviewer_concern_ownership() -> None:
    """Reject a baseline whose reviewer card claims another reviewer's concern."""
    with pytest.raises(ValidationError, match="links concerns from another reviewer"):
        paper_agent.RebuttalBaselineArtifact.model_validate(
            {
                "summary": "Two reports were normalized.",
                "intake": {
                    "summary": "Inputs ready.",
                    "paper_summary": "A paper summary.",
                    "constraints": {},
                    "reviewer_sources": [
                        {"review_id": str(uuid.uuid4()), "reviewer_id": "R1"},
                        {"review_id": str(uuid.uuid4()), "reviewer_id": "R2"},
                    ],
                    "open_questions": [],
                },
                "analysis": {
                    "summary": "Concerns mapped.",
                    "concerns": [
                        {
                            "id": "r1-w1",
                            "reviewer_id": "R1",
                            "label": "W",
                            "concern": "Missing comparison.",
                            "concern_type": "baseline_comparison",
                            "severity": "high",
                            "answer_source": "Experiments",
                            "draft_move": "Point to the existing table.",
                            "source_refs": ["paper.md#experiments"],
                        }
                    ],
                    "reviewer_cards": [
                        {
                            "reviewer_id": "R1",
                            "sentiment": "mixed",
                            "movability": "swing",
                            "attitude": "skeptical",
                            "primary_concerns": ["baseline_comparison"],
                            "concern_ids": [],
                        },
                        {
                            "reviewer_id": "R2",
                            "sentiment": "mixed",
                            "movability": "swing",
                            "attitude": "neutral",
                            "primary_concerns": [],
                            "concern_ids": ["r1-w1"],
                        },
                    ],
                },
                "ready_for_generation": True,
                "findings": [],
            }
        )


def test_rebuttal_baseline_publication_stales_automatic_result() -> None:
    """Invalidate the generated rebuttal when its confirmed baseline changes."""
    source_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        run_kind=models.PaperAgentRunKind.REBUTTAL_BASELINE.value,
    )
    workspace = SimpleNamespace(
        mode=models.ResearchWorkspaceMode.MANUSCRIPT.value,
        proposal_stage_states={
            "autorebuttal": {"status": "needs_revision"},
        },
    )

    paper_agent._update_proposal_stage_state(workspace, run, source_id)

    assert workspace.proposal_stage_states["rebuttal_baseline"]["status"] == "ready"
    assert workspace.proposal_stage_states["autorebuttal"]["status"] == "stale"


def test_rebuttal_draft_is_persisted_as_structured_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Persist generated response blocks without modifying the paper source."""
    object_storage = _ObjectStorage()
    monkeypatch.setattr(paper_agent, "storage", object_storage)

    with Session(engine) as db:
        user = create_random_user(db)
        workspace = models.PaperWorkspace(
            user_id=user.id,
            title="Structured rebuttal",
            mode=models.ResearchWorkspaceMode.MANUSCRIPT.value,
            rebuttal_context={
                "intake": {
                    "constraints": {
                        "venue": None,
                        "venue_year": None,
                        "response_mode": "per_reviewer",
                        "output_format": "markdown",
                        "per_reviewer_limit": None,
                        "total_limit": None,
                        "author_notes": [],
                        "forbidden_claims": [],
                    },
                    "reviewer_sources": [
                        {
                            "review_id": str(uuid.uuid4()),
                            "reviewer_id": "R1",
                            "title": None,
                        }
                    ],
                },
                "analysis": {
                    "concerns": [
                        {
                            "id": "r1-w1",
                            "reviewer_id": "R1",
                        }
                    ]
                },
            },
        )
        db.add(workspace)
        db.flush()
        source = models.PaperSourceVersion(
            workspace_id=workspace.id,
            version=1,
            source_kind=models.PaperSourceKind.MARKDOWN.value,
            filename="paper.md",
            object_key=f"paper/{user.id}/{workspace.id}/sources/current/paper.md",
            content_hash="source",
            content_size=8,
            manifest=["paper.md"],
        )
        db.add(source)
        db.flush()
        workspace.active_source_version_id = source.id
        run = models.PaperAgentRun(
            workspace_id=workspace.id,
            source_version_id=source.id,
            run_kind=models.PaperAgentRunKind.AUTOREBUTTAL.value,
            status=models.PaperAgentRunStatus.RUNNING.value,
        )
        db.add(workspace)
        db.add(run)
        db.commit()
        run_id = run.id
        workspace_id = workspace.id
        user_id = user.id

    work_dir = tmp_path / "rebuttal-publication"
    output_dir = work_dir / "output"
    output_dir.mkdir(parents=True)
    response = "The requested ablation is already reported in Section 4."
    entry = {
        "id": "r1-w1",
        "reviewer_id": "R1",
        "label": "W",
        "title": "Ablation coverage",
        "response": response,
        "concern_ids": ["r1-w1"],
        "evidence_status": "source_grounded",
        "source_refs": ["paper.md#section-4"],
    }
    (output_dir / "result.json").write_text(
        json.dumps(
            {
                "summary": "Generated and checked one source-grounded response.",
                "strategy": {
                    "summary": "Answer the central empirical concern directly.",
                    "shared_issues": [],
                    "priority_reviewers": ["R1"],
                    "global_strategy": ["Lead with the existing ablation."],
                    "format_plan": {
                        "response_mode": "per_reviewer",
                        "output_format": "markdown",
                        "global_summary": False,
                        "assumptions": [],
                    },
                    "budget_plan": {
                        "unit": "response_characters",
                        "per_reviewer_limit": None,
                        "total_limit": None,
                        "safety_margin": 0,
                        "reviewer_budgets": [
                            {
                                "reviewer_id": "R1",
                                "target_characters": 500,
                                "limit": None,
                            }
                        ],
                    },
                },
                "draft": {
                    "summary": "Drafted one response.",
                    "entries": [entry],
                },
                "compliance": {
                    "summary": "All concerns are covered.",
                    "entries": [entry],
                    "findings": [],
                    "ready_for_submission": True,
                    "open_placeholders": [],
                },
            }
        ),
        encoding="utf-8",
    )

    assert paper_agent._persist_output(run_id, user_id, work_dir) is False

    with Session(engine) as db:
        persisted = db.get(models.PaperWorkspace, workspace_id)
        assert persisted is not None
        assert persisted.rebuttal_entries[0]["response"] == response
        assert persisted.rebuttal_entries[0]["character_count"] == len(response)
        assert persisted.proposal_stage_states["autorebuttal"]["status"] == "ready"


def test_blocking_final_review_is_persisted_as_needs_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Preserve blocking review findings without treating them as a failed run."""
    object_storage = _ObjectStorage()
    monkeypatch.setattr(paper_service, "_storage", lambda: object_storage)
    monkeypatch.setattr(paper_agent, "storage", object_storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))

    with Session(engine) as db:
        user = create_random_user(db)
        workspace = paper_service.create_workspace(
            db,
            user,
            paper_schemas.PaperWorkspaceCreate(
                title="Proposal requiring revision",
                description="Test a durable final-review feedback loop.",
                mode="proposal",
            ),
        )
        source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
        assert source is not None
        workspace.proposal_foundation = {
            "blocks": [
                {
                    "key": "research_scope",
                    "title": "Research scope",
                    "content": "A confirmed bounded research scope.",
                    "source_refs": ["author response"],
                }
            ]
        }
        run = models.PaperAgentRun(
            workspace_id=workspace.id,
            source_version_id=source.id,
            run_kind=models.PaperAgentRunKind.PROPOSAL_FINAL_REVIEW.value,
            status=models.PaperAgentRunStatus.RUNNING.value,
            request_payload={"writable_paths": ["proposal.typ"]},
        )
        db.add(workspace)
        db.add(run)
        db.commit()
        workspace_id = workspace.id
        run_id = run.id
        user_id = user.id

    work_dir = tmp_path / "publication"
    output_dir = work_dir / "output"
    output_dir.mkdir(parents=True)
    finding = "blocking | methods | sections/05-methods.typ | Define the missing evaluation criterion."
    (output_dir / "result.json").write_text(
        json.dumps(
            {
                "entry_path": "proposal.typ",
                "summary": "The proposal is assembled but one blocking method issue remains.",
                "findings": [finding],
                "ready_for_export": False,
                "context_patch": {"upsert": [], "remove_keys": []},
            }
        ),
        encoding="utf-8",
    )

    assert paper_agent._persist_output(run_id, user_id, work_dir) is True

    with Session(engine) as db:
        persisted_workspace = db.get(models.PaperWorkspace, workspace_id)
        persisted_run = db.get(models.PaperAgentRun, run_id)
        assert persisted_workspace is not None
        assert persisted_run is not None
        final_state = persisted_workspace.proposal_stage_states["final_review"]
        assert final_state["status"] == "needs_revision"
        assert final_state["findings"] == [finding]
        assert persisted_run.status == models.PaperAgentRunStatus.READY.value
        assert persisted_run.message == "Proposal review requires revision"


@pytest.mark.asyncio
async def test_invalid_publication_can_be_repaired_in_the_same_native_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject invalid output, then persist its corrected retry without a new run."""
    redis = _AsyncRedis()
    object_storage = _ObjectStorage()
    workspace_id: uuid.UUID
    run_id: uuid.UUID

    with Session(engine) as db:
        user = create_random_user(db)
        workspace = models.PaperWorkspace(
            user_id=user.id,
            title="Native publication",
            mode=models.ResearchWorkspaceMode.MANUSCRIPT.value,
        )
        db.add(workspace)
        db.flush()
        source = models.PaperSourceVersion(
            workspace_id=workspace.id,
            version=1,
            source_kind=models.PaperSourceKind.MARKDOWN.value,
            filename="paper.md",
            object_key=f"paper/{user.id}/{workspace.id}/sources/current/paper.md",
            content_hash="source",
            content_size=8,
            manifest=["paper.md"],
        )
        db.add(source)
        db.flush()
        workspace.active_source_version_id = source.id
        run = models.PaperAgentRun(
            workspace_id=workspace.id,
            source_version_id=source.id,
            run_kind=models.PaperAgentRunKind.BOUNDARY_ANALYSIS.value,
            status=models.PaperAgentRunStatus.READY.value,
            session_id=f"{workspace.id.hex}-boundary",
            request_payload={"native_runtime": "cloudcli"},
        )
        db.add(run)
        db.commit()
        workspace_id = workspace.id
        run_id = run.id

    runtime_token = "runtime-token"

    async def get_redis() -> _AsyncRedis:
        return redis

    monkeypatch.setattr(paper_runtime, "get_async_redis", get_redis)
    monkeypatch.setattr(
        paper_runtime,
        "paper_workspace_runtime_token",
        lambda _workspace_id: runtime_token,
    )
    monkeypatch.setattr(paper_agent, "storage", object_storage)

    invalid = paper_runtime._StagePublicationRequest(
        workspace_id=workspace_id,
        run_id=run_id,
        idempotency_key="same-native-turn",
        artifact={},
    )
    with pytest.raises(HTTPException) as exc_info:
        await paper_runtime.publish_runtime_stage_result(
            invalid,
            _publication_request(runtime_token),
        )
    assert exc_info.value.status_code == 422

    with Session(engine) as db:
        failed_run = db.get(models.PaperAgentRun, run_id)
        assert failed_run is not None
        assert failed_run.status == models.PaperAgentRunStatus.FAILED.value
        assert failed_run.error_code == "invalid_stage_result"

    corrected = paper_runtime._StagePublicationRequest(
        workspace_id=workspace_id,
        run_id=run_id,
        idempotency_key="same-native-turn",
        artifact={
            "sections": [
                {
                    "key": "research_scope",
                    "title": "Research scope",
                    "summary": "A source-grounded research boundary.",
                    "source_refs": [{"path": "paper.md"}],
                }
            ],
            "source_map": [],
            "unknowns": [],
        },
    )
    result = await paper_runtime.publish_runtime_stage_result(
        corrected,
        _publication_request(runtime_token),
    )

    assert result["success"] is True
    assert result["run_id"] == str(run_id)
    with Session(engine) as db:
        ready_run = db.get(models.PaperAgentRun, run_id)
        boundary = db.exec(
            select(models.PaperBoundarySnapshot).where(models.PaperBoundarySnapshot.workspace_id == workspace_id)
        ).one()
        assert ready_run is not None
        assert ready_run.status == models.PaperAgentRunStatus.READY.value
        assert ready_run.error is None
        assert boundary.run_id == run_id
        assert boundary.content["sections"][0]["key"] == "research_scope"
        assert ready_run.artifact_object_key in object_storage.objects
