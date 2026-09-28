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

    def delete_many(self, keys: list[str], **_kwargs) -> None:
        for key in keys:
            self.objects.pop(key, None)

    def list_objects(self, prefix: str, **_kwargs) -> list[str]:
        return [key for key in self.objects if key.startswith(prefix)]


def test_conversation_review_source_preserves_organized_markdown() -> None:
    """Keep a conversation-supplied report available to the reviewer panel."""
    review_id = uuid.uuid4()
    source = paper_agent.RebuttalReviewerSource.model_validate(
        {
            "review_id": str(review_id),
            "reviewer_id": str(review_id),
            "display_label": "Reviewer A",
            "source_system": "conversation",
            "review_markdown": "## Reviewer A\n\n- Missing comparison.",
        }
    )

    assert source.model_dump()["review_markdown"] == "## Reviewer A\n\n- Missing comparison."


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


def test_conversation_rewind_stales_only_the_revision_it_replaced() -> None:
    """Ignore a delayed rewind after a newer stage revision was published."""
    workspace = SimpleNamespace(
        mode=models.ResearchWorkspaceMode.MANUSCRIPT.value,
        proposal_stage_states={
            "rebuttal_baseline": {
                "status": "needs_revision",
                "iteration": 2,
            },
            "autorebuttal": {
                "status": "ready",
                "iteration": 1,
            },
        },
    )

    changed = paper_service._stale_workflow_revision(
        workspace,
        "rebuttal_baseline",
        expected_iteration=2,
    )

    assert changed is True
    assert workspace.proposal_stage_states["rebuttal_baseline"]["status"] == "stale"
    assert workspace.proposal_stage_states["autorebuttal"]["status"] == "stale"

    workspace.proposal_stage_states["rebuttal_baseline"] = {
        "status": "ready",
        "iteration": 3,
    }
    changed = paper_service._stale_workflow_revision(
        workspace,
        "rebuttal_baseline",
        expected_iteration=2,
    )

    assert changed is False
    assert workspace.proposal_stage_states["rebuttal_baseline"]["status"] == "ready"


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


def test_rebuttal_compliance_preserves_agent_canonical_text() -> None:
    """Keep unsupported evidence visible in the agent-produced final text."""
    response = "We will report [AUTHOR: measured latency] after verification."
    rendered_text = f"## Reviewer 1\n\n### Q1: Runtime measurement\n\n{response}"
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
                }
            ],
            "rendered_text": rendered_text,
            "findings": [],
            "ready_for_submission": False,
            "open_placeholders": ["Measure and insert inference latency."],
        }
    )

    assert artifact.entries[0].evidence_status == "placeholder"
    assert artifact.rendered_text == rendered_text


def test_rebuttal_baseline_leaves_reviewer_ownership_checks_to_skill() -> None:
    """Keep reviewer-concern ownership at the agent skill boundary."""
    reviewer_one = str(uuid.uuid4())
    reviewer_two = str(uuid.uuid4())
    artifact = paper_agent.RebuttalBaselineArtifact.model_validate(
        {
            "summary": "Two reports were normalized.",
            "intake": {
                "summary": "Inputs ready.",
                "paper_summary": "A paper summary.",
                "constraints": {},
                "reviewer_sources": [
                    {
                        "review_id": reviewer_one,
                        "reviewer_id": reviewer_one,
                        "display_label": "Reviewer 1",
                    },
                    {
                        "review_id": reviewer_two,
                        "reviewer_id": reviewer_two,
                        "display_label": "Reviewer 2",
                    },
                ],
                "open_questions": [],
            },
            "analysis": {
                "summary": "Concerns mapped.",
                "concerns": [
                    {
                        "id": "r1-w1",
                        "reviewer_id": reviewer_one,
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
                        "reviewer_id": reviewer_one,
                        "sentiment": "mixed",
                        "movability": "swing",
                        "attitude": "skeptical",
                        "primary_concerns": ["baseline_comparison"],
                        "concern_ids": [],
                    },
                    {
                        "reviewer_id": reviewer_two,
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

    assert artifact.analysis.reviewer_cards[1].concern_ids == ["r1-w1"]


def test_rebuttal_baseline_accepts_conversation_only_review() -> None:
    """Allow a pasted report without a saved right-panel review record."""
    reviewer_id = str(uuid.uuid4())
    artifact = paper_agent.RebuttalBaselineArtifact.model_validate(
        {
            "summary": "One pasted reviewer report was analyzed.",
            "intake": {
                "summary": "Review text supplied in the conversation.",
                "paper_summary": "The paper proposes a new algorithm.",
                "constraints": {},
                "reviewer_sources": [
                    {
                        "review_id": reviewer_id,
                        "reviewer_id": reviewer_id,
                        "display_label": "Reviewer A",
                        "source_system": "conversation",
                    }
                ],
            },
            "analysis": {
                "summary": "One concern found.",
                "concerns": [
                    {
                        "id": "reviewer-a-w1",
                        "reviewer_id": reviewer_id,
                        "label": "W",
                        "concern": "The comparison is missing.",
                        "concern_type": "comparison",
                        "severity": "medium",
                        "answer_source": "Experiments section",
                        "draft_move": "Clarify the existing comparisons.",
                    }
                ],
                "reviewer_cards": [
                    {
                        "reviewer_id": reviewer_id,
                        "sentiment": "mixed",
                        "movability": "swing",
                        "attitude": "Requests a clearer comparison.",
                        "concern_ids": ["reviewer-a-w1"],
                    }
                ],
            },
            "ready_for_generation": True,
        }
    )

    assert artifact.intake.reviewer_sources[0].source_system == "conversation"
    assert artifact.analysis.concerns[0].reviewer_id == reviewer_id


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
    reviewer_id = str(uuid.uuid4())

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
                        "author_notes": [],
                        "forbidden_claims": [],
                    },
                    "reviewer_sources": [
                        {
                            "review_id": reviewer_id,
                            "reviewer_id": reviewer_id,
                            "display_label": "Reviewer 1",
                            "title": None,
                        }
                    ],
                },
                "analysis": {
                    "concerns": [
                        {
                            "id": "r1-w1",
                            "reviewer_id": reviewer_id,
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
    rendered_text = f"## Reviewer 1\n\n### W1: Ablation coverage\n\n{response}"
    entry = {
        "id": "r1-w1",
        "reviewer_id": reviewer_id,
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
                    "priority_reviewers": [reviewer_id],
                    "global_strategy": ["Lead with the existing ablation."],
                    "format_plan": {
                        "response_mode": "per_reviewer",
                        "output_format": "markdown",
                        "global_summary": False,
                        "assumptions": [],
                    },
                },
                "draft": {
                    "summary": "Drafted one response.",
                    "entries": [entry],
                },
                "compliance": {
                    "summary": "All concerns are covered.",
                    "entries": [entry],
                    "rendered_text": rendered_text,
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
        assert persisted.proposal_stage_states["autorebuttal"]["status"] == "ready"
        rendered = persisted.rebuttal_output
        assert rendered is not None
        assert rendered["text"] == rendered_text
        assert rendered["ready_for_submission"] is True
        response_model = paper_schemas.PaperWorkspaceSummary.model_validate(persisted)
        assert response_model.rebuttal_output is not None
        assert response_model.rebuttal_output.text == rendered["text"]


def test_ac_summary_publishes_separate_author_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Keep the AC message separate from the reviewer rebuttal and paper source."""
    object_storage = _ObjectStorage()
    monkeypatch.setattr(paper_agent, "storage", object_storage)
    with Session(engine) as db:
        user = create_random_user(db)
        workspace = models.PaperWorkspace(
            user_id=user.id,
            title="AC message",
            mode="manuscript",
            rebuttal_context={
                "rendered": {
                    "output_format": "markdown",
                    "text": "Reviewer response text",
                    "ready_for_submission": False,
                }
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
            run_kind=models.PaperAgentRunKind.AC_SUMMARY.value,
            status=models.PaperAgentRunStatus.RUNNING.value,
        )
        db.add(run)
        db.flush()
        workspace.proposal_stage_states = {
            "autorebuttal": {
                "status": "needs_revision",
                "run_id": str(run.id),
                "source_version_id": str(source.id),
                "updated_time": "2026-01-01T00:00:00Z",
            }
        }
        db.add(workspace)
        db.commit()
        run_id, user_id, workspace_id = run.id, user.id, workspace.id

    output_dir = tmp_path / "ac-summary" / "output"
    output_dir.mkdir(parents=True)
    (output_dir / "result.json").write_text(
        json.dumps({"summary": "Prepared an author message.", "message": "Dear AC, we clarified the shared concern."}),
        encoding="utf-8",
    )

    assert paper_agent._persist_output(run_id, user_id, output_dir.parent) is False

    with Session(engine) as db:
        persisted = db.get(models.PaperWorkspace, workspace_id)
        assert persisted is not None
        assert persisted.chair_message == "Dear AC, we clarified the shared concern."
        assert persisted.rebuttal_context["rendered"]["text"] == "Reviewer response text"
        assert persisted.proposal_stage_states["ac_summary"]["status"] == "ready"
        assert paper_schemas.PaperWorkspaceSummary.model_validate(persisted).chair_message == persisted.chair_message
        paper_service._stale_proposal_stages(persisted, "autorebuttal")
        assert persisted.proposal_stage_states["ac_summary"]["status"] == "stale"


def test_algorithm_discovery_replaces_drafts_and_marks_result_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Persist only an Agent-built and validated task package."""
    object_storage = _ObjectStorage()
    monkeypatch.setattr(paper_agent, "storage", object_storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))

    with Session(engine) as db:
        user = create_random_user(db)
        workspace = models.PaperWorkspace(
            user_id=user.id,
            title="Algorithm discovery",
            mode=models.ResearchWorkspaceMode.ALGORITHM.value,
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
            run_kind=models.PaperAgentRunKind.ALGORITHM_DISCOVERY.value,
            status=models.PaperAgentRunStatus.RUNNING.value,
        )
        db.add(run)
        db.flush()
        db.add(
            models.PaperAlgorithmProposal(
                workspace_id=workspace.id,
                source_version_id=source.id,
                run_id=run.id,
                title="Superseded draft",
                problem_statement="Old problem",
                algorithm_design="Old design",
            )
        )
        db.add(workspace)
        db.commit()
        run_id = run.id
        workspace_id = workspace.id
        user_id = user.id

    package_dir = (
        paper_service.paper_workspace_root(user_id, workspace_id)
        / ".research"
        / "autodiscovery"
        / "packages"
        / "build-1"
        / "graph-constructor"
    )
    package_dir.mkdir(parents=True)
    package_files = {
        "config.yaml": "project_name: graph-constructor\nevaluator:\n  module: evaluator.py:Evaluator\nevolution:\n  type: island_ga\n",
        "debug_run.py": "print('ok')\n",
        "test_evaluator.py": "print('ok')\n",
        "evaluator.py": "class Evaluator:\n    pass\n",
        "algorithm/solve.py": "# EVOLVE_START\npass\n# EVOLVE_END\n",
        "blueprint_meta.json": json.dumps(
            {
                "project_name": "graph-constructor",
                "evaluator_file_name": "evaluator.py",
                "algorithm_dir_name": "algorithm",
                "algorithm_file_name": "solve.py",
                "function_to_evolve": "solve",
                "metrics": [{"name": "quality", "type": "maximize"}],
                "validation_status": "passed",
                "validation_errors": [],
                "repair_attempts": 0,
            }
        ),
    }
    for relative, content in package_files.items():
        target = package_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    work_dir = tmp_path / "algorithm-publication"
    output_dir = work_dir / "output"
    output_dir.mkdir(parents=True)
    (output_dir / "result.json").write_text(
        json.dumps(
            {
                "proposals": [
                    {
                        "title": "Evolvable graph constructor",
                        "problem_statement": "Improve graph quality under a fixed budget.",
                        "algorithm_design": "Evolve the bounded construction function.",
                        "evaluator_requirements": ["Maximize validated graph quality."],
                        "assumptions": ["Training instances are available."],
                        "provenance": ["paper.md — Methods"],
                        "suggested_task_config": {"language": "python"},
                        "task_package_path": "/workspace/.research/autodiscovery/packages/build-1/graph-constructor",
                        "validation_report": {
                            "status": "passed",
                            "validator": "llm4ad.builder.TaskValidator",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert paper_agent._persist_output(run_id, user_id, work_dir) is False

    with Session(engine) as db:
        proposals = list(
            db.exec(
                select(models.PaperAlgorithmProposal).where(
                    models.PaperAlgorithmProposal.workspace_id == workspace_id
                )
            ).all()
        )
        persisted_workspace = db.get(models.PaperWorkspace, workspace_id)
        assert [proposal.title for proposal in proposals] == [
            "Evolvable graph constructor"
        ]
        assert proposals[0].validation_report["status"] == "passed"
        assert "config.yaml" in proposals[0].package_manifest
        assert proposals[0].package_object_prefix
        assert persisted_workspace is not None
        assert persisted_workspace.proposal_stage_states["discovery"]["status"] == "ready"


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
async def test_native_run_can_publish_revised_artifact_with_same_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist corrected and branch-revised output without creating a new run."""
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

    revised = corrected.model_copy(
        update={
            "artifact": {
                "sections": [
                    {
                        "key": "research_scope",
                        "title": "Revised research scope",
                        "summary": "The edited conversation produced a revised boundary.",
                        "source_refs": [{"path": "paper.md"}],
                    }
                ],
                "source_map": [],
                "unknowns": [],
            }
        }
    )
    revised_result = await paper_runtime.publish_runtime_stage_result(
        revised,
        _publication_request(runtime_token),
    )

    assert revised_result["success"] is True
    with Session(engine) as db:
        revised_boundary = db.exec(
            select(models.PaperBoundarySnapshot).where(models.PaperBoundarySnapshot.workspace_id == workspace_id)
        ).one()
        assert revised_boundary.content["sections"][0]["title"] == "Revised research scope"
