"""Unit contracts for the AutoRebuttal persistence boundary."""

from app import models
from app.tasks.paper_agent import AutoRebuttalArtifact


def _entry(**overrides) -> dict:
    payload = {
        "id": "entry-1",
        "reviewer_id": "review-1",
        "label": "W",
        "title": "Missing baseline",
        "response": "The comparison is reported in Section 4.",
        "concern_ids": ["review-1-w1"],
        "evidence_status": "source_grounded",
        "source_refs": ["paper.md#section-4"],
    }
    payload.update(overrides)
    return payload


def test_compliance_carries_agent_rendered_canonical_text() -> None:
    """Keep canonical prose owned by the agent instead of backend rendering."""
    rendered_text = "Response to all reviewers\n\nThe protocol is defined in Section 4."
    artifact = AutoRebuttalArtifact.model_validate(
        {
            "summary": "One shared answer covers the concern.",
            "strategy": {
                "summary": "Answer once globally.",
                "shared_issues": ["evaluation protocol"],
                "priority_reviewers": ["review-1"],
                "global_strategy": ["Use one evidence-grounded shared response."],
                "format_plan": {
                    "response_mode": "shared_global",
                    "output_format": "text",
                    "global_summary": True,
                    "assumptions": [],
                },
            },
            "draft": {
                "summary": "Shared draft.",
                "global_response": {
                    "title": "Response to all reviewers",
                    "response": "The protocol is defined in Section 4.",
                    "concern_ids": ["review-1-w1"],
                    "evidence_status": "source_grounded",
                    "source_refs": ["paper.md#section-4"],
                },
                "entries": [],
            },
            "compliance": {
                "summary": "Checked by the AutoRebuttal skill.",
                "global_response": {
                    "title": "Response to all reviewers",
                    "response": "The protocol is defined in Section 4.",
                    "concern_ids": ["review-1-w1"],
                    "evidence_status": "source_grounded",
                    "source_refs": ["paper.md#section-4"],
                },
                "entries": [],
                "rendered_text": rendered_text,
                "findings": [],
                "ready_for_submission": True,
                "open_placeholders": [],
            },
        }
    )

    assert artifact.compliance.rendered_text == rendered_text
    assert artifact.compliance.entries == []


def test_backend_does_not_duplicate_skill_semantic_checks() -> None:
    """Accept structured output after semantic checks have run in the skill."""
    artifact = AutoRebuttalArtifact.model_validate(
        {
            "summary": "Agent-owned validation result.",
            "strategy": {
                "summary": "Use reviewer blocks.",
                "shared_issues": [],
                "priority_reviewers": ["review-1"],
                "global_strategy": ["Answer directly."],
                "format_plan": {
                    "response_mode": "per_reviewer",
                    "output_format": "markdown",
                    "global_summary": False,
                    "assumptions": [],
                },
            },
            "draft": {
                "summary": "Draft.",
                "entries": [_entry(source_refs=[])],
            },
            "compliance": {
                "summary": "Final.",
                "entries": [
                    _entry(source_refs=[]),
                    _entry(id="entry-2", source_refs=[]),
                ],
                "rendered_text": "## Reviewer 1\n\nFinal response.",
                "findings": [],
                "ready_for_submission": True,
                "open_placeholders": [],
            },
        }
    )

    assert len(artifact.compliance.entries) == 2


def test_stale_workspace_never_exposes_rebuttal_as_submission_ready() -> None:
    """Derive effective readiness from the current workflow state."""
    workspace = models.PaperWorkspace(
        user_id="11111111-1111-1111-1111-111111111111",
        title="Stale rebuttal",
        mode=models.ResearchWorkspaceMode.MANUSCRIPT.value,
        proposal_stage_states={"autorebuttal": {"status": "stale"}},
        rebuttal_context={
            "rendered": {
                "output_format": "markdown",
                "text": "## Reviewer 1\n\n### W1: Concern\n\nResponse.",
                "ready_for_submission": True,
            }
        },
    )

    assert workspace.rebuttal_output is not None
    assert workspace.rebuttal_output["ready_for_submission"] is False
