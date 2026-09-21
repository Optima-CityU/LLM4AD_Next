"""Validation and persistence for native research stage artifacts."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field, model_validator
from sqlmodel import select

from app import models
from app.core.db import get_db_session
from app.core.storage import storage
from app.schemas import paper as paper_schemas
from app.services import paper_service, paper_workflow


class DiscoveryArtifact(BaseModel):
    """Validated output from an algorithm discovery run."""

    proposals: list[paper_schemas.PaperAlgorithmProposalCreate] = Field(min_length=1, max_length=20)


class ProposalFormattingArtifact(BaseModel):
    """Validated Typst result and its durable project-level foundation."""

    source_path: str = Field(min_length=1, max_length=1_024)
    summary: str = Field(min_length=1, max_length=20_000)
    context_patch: paper_schemas.ProposalContextPatch


class ProposalSectionArtifact(BaseModel):
    """Validated result produced by one proposal writing stage."""

    source_paths: list[str] = Field(min_length=1, max_length=10)
    summary: str = Field(min_length=1, max_length=20_000)
    citations: list[str] = Field(default_factory=list, max_length=500)
    context_patch: paper_schemas.ProposalContextPatch


class ProposalFinalReviewArtifact(BaseModel):
    """Read-only whole-proposal review produced before PDF export."""

    entry_path: str = Field(min_length=1, max_length=1_024)
    summary: str = Field(min_length=1, max_length=20_000)
    findings: list[str] = Field(default_factory=list, max_length=200)
    ready_for_export: bool
    context_patch: paper_schemas.ProposalContextPatch

    @model_validator(mode="after")
    def require_blocking_findings(self) -> ProposalFinalReviewArtifact:
        """Require an actionable explanation when export remains blocked."""
        if not self.ready_for_export and not self.findings:
            raise ValueError("A review that requires revision must include at least one finding")
        return self


class RebuttalConstraintSummary(BaseModel):
    """Submission constraints that govern response structure and budget."""

    venue: str | None = Field(default=None, max_length=255)
    venue_year: int | None = Field(default=None, ge=2000, le=2200)
    response_mode: str = Field(default="per_reviewer", pattern=r"^(per_reviewer|shared_global)$")
    output_format: str = Field(default="markdown", pattern=r"^(markdown|text)$")
    per_reviewer_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    total_limit: int | None = Field(default=None, ge=1, le=5_000_000)
    author_notes: list[str] = Field(default_factory=list, max_length=100)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=100)


class RebuttalReviewerSource(BaseModel):
    """One reviewer document included in the rebuttal workflow."""

    review_id: uuid.UUID
    reviewer_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=255)


class RebuttalIntakeArtifact(BaseModel):
    """Normalized paper, reviewer, and venue inputs."""

    summary: str = Field(min_length=1, max_length=20_000)
    paper_summary: str = Field(min_length=1, max_length=50_000)
    constraints: RebuttalConstraintSummary
    reviewer_sources: list[RebuttalReviewerSource] = Field(min_length=1, max_length=100)
    open_questions: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_unique_reviewers(self) -> RebuttalIntakeArtifact:
        """Reject duplicate review documents and reviewer identifiers."""
        review_ids = [item.review_id for item in self.reviewer_sources]
        reviewer_ids = [item.reviewer_id for item in self.reviewer_sources]
        if len(review_ids) != len(set(review_ids)):
            raise ValueError("Rebuttal intake contains duplicate review IDs")
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("Rebuttal intake contains duplicate reviewer IDs")
        return self


class RebuttalConcern(BaseModel):
    """One atomic reviewer concern with an explicit response move."""

    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    reviewer_id: str = Field(min_length=1, max_length=128)
    label: str = Field(pattern=r"^(W|Q|M)$")
    concern: str = Field(min_length=1, max_length=20_000)
    concern_type: str = Field(min_length=1, max_length=128)
    severity: str = Field(pattern=r"^(low|medium|high)$")
    answer_source: str = Field(min_length=1, max_length=20_000)
    draft_move: str = Field(min_length=1, max_length=20_000)
    source_refs: list[str] = Field(default_factory=list, max_length=200)


class RebuttalReviewerCard(BaseModel):
    """Reviewer-level attitude and movability assessment."""

    reviewer_id: str = Field(min_length=1, max_length=128)
    sentiment: str = Field(min_length=1, max_length=128)
    movability: str = Field(min_length=1, max_length=128)
    attitude: str = Field(min_length=1, max_length=10_000)
    primary_concerns: list[str] = Field(default_factory=list, max_length=100)
    concern_ids: list[str] = Field(default_factory=list, max_length=100)


class RebuttalAnalysisArtifact(BaseModel):
    """Atomic concern inventory and reviewer models."""

    summary: str = Field(min_length=1, max_length=20_000)
    concerns: list[RebuttalConcern] = Field(min_length=1, max_length=500)
    reviewer_cards: list[RebuttalReviewerCard] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_concern_links(self) -> RebuttalAnalysisArtifact:
        """Ensure concern IDs are unique and reviewer cards only reference them."""
        concern_ids = [item.id for item in self.concerns]
        if len(concern_ids) != len(set(concern_ids)):
            raise ValueError("Rebuttal analysis contains duplicate concern IDs")
        known = set(concern_ids)
        linked = {item for card in self.reviewer_cards for item in card.concern_ids}
        if not linked.issubset(known):
            raise ValueError("Reviewer cards reference unknown concerns")
        return self


class RebuttalBaselineArtifact(BaseModel):
    """Author-confirmed paper, review, and concern baseline."""

    summary: str = Field(min_length=1, max_length=20_000)
    intake: RebuttalIntakeArtifact
    analysis: RebuttalAnalysisArtifact
    ready_for_generation: bool
    findings: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_reviewer_coverage(self) -> RebuttalBaselineArtifact:
        """Require cards and concerns to stay aligned with every input review."""
        expected_reviewers = {item.reviewer_id for item in self.intake.reviewer_sources}
        cards_by_reviewer = {item.reviewer_id: item for item in self.analysis.reviewer_cards}
        if len(cards_by_reviewer) != len(self.analysis.reviewer_cards):
            raise ValueError("Rebuttal baseline contains duplicate reviewer cards")
        if set(cards_by_reviewer) != expected_reviewers:
            raise ValueError("Rebuttal baseline must contain one card for every reviewer")

        concerns_by_id = {item.id: item for item in self.analysis.concerns}
        linked_ids: list[str] = []
        for reviewer_id, card in cards_by_reviewer.items():
            linked_ids.extend(card.concern_ids)
            mismatched = [
                concern_id for concern_id in card.concern_ids if concerns_by_id[concern_id].reviewer_id != reviewer_id
            ]
            if mismatched:
                raise ValueError(f"Reviewer card {reviewer_id} links concerns from another reviewer")
        if len(linked_ids) != len(set(linked_ids)) or set(linked_ids) != set(concerns_by_id):
            raise ValueError("Every baseline concern must belong to exactly one reviewer card")
        if not self.ready_for_generation and not (self.findings or self.intake.open_questions):
            raise ValueError("A blocked rebuttal baseline must describe an author action")
        return self


class RebuttalFormatPlan(BaseModel):
    """Resolved response structure for the active submission constraints."""

    response_mode: str = Field(pattern=r"^(per_reviewer|shared_global)$")
    output_format: str = Field(pattern=r"^(markdown|text)$")
    global_summary: bool
    assumptions: list[str] = Field(default_factory=list, max_length=100)


class RebuttalReviewerBudget(BaseModel):
    """Planned response-character allocation for one reviewer."""

    reviewer_id: str = Field(min_length=1, max_length=128)
    target_characters: int = Field(ge=1, le=1_000_000)
    limit: int | None = Field(default=None, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def keep_target_within_limit(self) -> RebuttalReviewerBudget:
        """Reject a plan that knowingly exceeds its reviewer limit."""
        if self.limit is not None and self.target_characters > self.limit:
            raise ValueError("Reviewer target characters exceed the declared limit")
        return self


class RebuttalBudgetPlan(BaseModel):
    """Deterministic character budget used for generation and compliance."""

    unit: str = Field(default="response_characters", pattern=r"^response_characters$")
    per_reviewer_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    total_limit: int | None = Field(default=None, ge=1, le=5_000_000)
    safety_margin: int = Field(default=0, ge=0, le=1_000_000)
    reviewer_budgets: list[RebuttalReviewerBudget] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_reviewers(self) -> RebuttalBudgetPlan:
        """Require exactly one allocation per listed reviewer."""
        reviewer_ids = [item.reviewer_id for item in self.reviewer_budgets]
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("Rebuttal budget contains duplicate reviewer allocations")
        if self.total_limit is not None:
            planned = sum(item.target_characters for item in self.reviewer_budgets)
            if planned + self.safety_margin > self.total_limit:
                raise ValueError("Planned rebuttal characters exceed the total limit")
        return self


class RebuttalStrategyArtifact(BaseModel):
    """Cross-reviewer response strategy, format, and character budget."""

    summary: str = Field(min_length=1, max_length=20_000)
    shared_issues: list[str] = Field(default_factory=list, max_length=100)
    priority_reviewers: list[str] = Field(default_factory=list, max_length=100)
    global_strategy: list[str] = Field(min_length=1, max_length=100)
    format_plan: RebuttalFormatPlan
    budget_plan: RebuttalBudgetPlan


class RebuttalDraftArtifact(BaseModel):
    """Reviewer-addressed rebuttal entries ready for author inspection."""

    summary: str = Field(min_length=1, max_length=20_000)
    entries: list[paper_schemas.RebuttalEntry] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_unique_entries(self) -> RebuttalDraftArtifact:
        """Require stable unique entry identifiers."""
        entry_ids = [entry.id for entry in self.entries]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("Rebuttal draft contains duplicate entry IDs")
        return self


class RebuttalComplianceArtifact(RebuttalDraftArtifact):
    """Final checked rebuttal and any remaining author actions."""

    findings: list[str] = Field(default_factory=list, max_length=200)
    ready_for_submission: bool
    open_placeholders: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def require_blocking_findings(self) -> RebuttalComplianceArtifact:
        """Explain why a final response remains unready."""
        if not self.ready_for_submission and not (self.findings or self.open_placeholders):
            raise ValueError("An unready rebuttal must describe a finding or placeholder")
        unresolved = [entry.id for entry in self.entries if entry.evidence_status in {"placeholder", "needs_author"}]
        if self.ready_for_submission and (self.open_placeholders or unresolved):
            raise ValueError("A submission-ready rebuttal cannot contain unresolved author evidence")
        return self


class AutoRebuttalArtifact(BaseModel):
    """Strategy, draft, and final compliance result from one automatic phase."""

    summary: str = Field(min_length=1, max_length=20_000)
    strategy: RebuttalStrategyArtifact
    draft: RebuttalDraftArtifact
    compliance: RebuttalComplianceArtifact

    @model_validator(mode="after")
    def preserve_draft_coverage(self) -> AutoRebuttalArtifact:
        """Allow polishing while preserving the concerns covered by the draft."""
        draft_concerns = {item for entry in self.draft.entries for item in entry.concern_ids}
        final_concerns = {item for entry in self.compliance.entries for item in entry.concern_ids}
        if draft_concerns != final_concerns:
            raise ValueError("Compliance review must preserve the draft concern coverage")
        return self


class MetricArtifact(BaseModel):
    """Validated optional metric suggestions."""

    metrics: list[paper_schemas.PaperMetricDraft] = Field(min_length=1, max_length=50)


class RevisionCandidateArtifact(BaseModel):
    """One exact, reviewable source replacement emitted by the paper agent."""

    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=20_000)
    source_path: str = Field(min_length=1, max_length=1_024)
    original_text: str = Field(min_length=1, max_length=500_000)
    replacement_text: str = Field(min_length=1, max_length=500_000)
    provenance: list[str] = Field(default_factory=list, max_length=200)


class RevisionArtifact(BaseModel):
    """Validated collection of independent paper revision candidates."""

    candidates: list[RevisionCandidateArtifact] = Field(min_length=1, max_length=10)


class BoundaryArtifact(paper_schemas.PaperBoundaryDraft):
    """Validated paper boundary returned by the analysis agent."""


class TargetArtifact(BaseModel):
    """Validated collection of source-grounded optimization targets."""

    targets: list[paper_schemas.PaperOptimizationTargetDraft] = Field(min_length=1, max_length=100)


def _merge_proposal_context(
    current: dict[str, object] | None,
    patch: paper_schemas.ProposalContextPatch,
) -> paper_schemas.ProposalFoundation:
    """Apply model-defined context changes while preserving untouched blocks.

    Args:
        current: Latest persisted proposal context.
        patch: Explicit model-requested block updates and removals.

    Returns:
        The validated complete proposal context after the patch.

    Raises:
        ValueError: If the patch would leave the proposal without context.
    """
    blocks = paper_schemas.ProposalFoundation.model_validate(current).blocks if current is not None else []
    removed = set(patch.remove_keys)
    merged = {block.key: block for block in blocks if block.key not in removed}
    for block in patch.upsert:
        merged[block.key] = block
    if not merged:
        raise ValueError("Proposal context must contain at least one block")
    return paper_schemas.ProposalFoundation(blocks=list(merged.values()))


def _canonical_source_reference(raw_path: str, manifest: Iterable[str]) -> str:
    """Map a trusted paper-runtime path to its stored manifest-relative path."""
    available = set(manifest)
    normalized = raw_path.strip().replace("\\", "/")
    if normalized in available:
        return normalized
    for prefix in (
        "/workspace/source/",
        "source/",
        "/workspace/input/source/",
        "input/source/",
    ):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break
    candidate = PurePosixPath(normalized)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or any(part in {"", "."} for part in candidate.parts)
        or candidate.as_posix() not in available
    ):
        raise ValueError(f"Source path is not in the manifest: {raw_path}")
    return candidate.as_posix()


def _canonical_boundary_payload(
    artifact: BoundaryArtifact,
    manifest: Iterable[str],
) -> dict:
    """Normalize all boundary provenance to stored source-relative paths."""
    payload = artifact.model_dump(mode="json")
    for section in payload["sections"]:
        for source_ref in section.get("source_refs", []):
            source_ref["path"] = _canonical_source_reference(source_ref["path"], manifest)
    for source_entry in payload.get("source_map", []):
        source_path = source_entry.get("path")
        if isinstance(source_path, str) and source_path:
            source_entry["path"] = _canonical_source_reference(source_path, manifest)
    return payload


def _source_path_allowed(path: str, allowed_paths: Iterable[str]) -> bool:
    """Return whether a source-relative path belongs to the current stage."""
    return any(path == allowed or (allowed.endswith("/") and path.startswith(allowed)) for allowed in allowed_paths)


def _validate_proposal_source_changes(
    previous_entries: Iterable[tuple[str, bytes]],
    current_entries: Iterable[tuple[str, bytes]],
    allowed_paths: list[str],
) -> set[str]:
    """Reject source edits outside the current proposal stage boundary."""
    previous = dict(previous_entries)
    current = dict(current_entries)
    changed = {path for path in previous.keys() | current.keys() if previous.get(path) != current.get(path)}
    forbidden = sorted(path for path in changed if not _source_path_allowed(path, allowed_paths))
    if forbidden:
        raise ValueError(f"Proposal stage modified source outside its boundary: {', '.join(forbidden[:10])}")
    return changed


def _update_proposal_stage_state(
    workspace: models.PaperWorkspace,
    run: models.PaperAgentRun,
    source_version_id: uuid.UUID,
    *,
    status: str = "ready",
    summary: str | None = None,
    findings: list[str] | None = None,
) -> None:
    """Record one workflow stage outcome and invalidate completed dependents.

    Args:
        workspace: Research workspace whose stage state should change.
        run: Native agent run publishing the stage result.
        source_version_id: Source version containing the published result.
        status: Durable stage outcome exposed to the author.
        summary: Optional concise result summary.
        findings: Optional review findings that require follow-up.

    Raises:
        ValueError: If the supplied status is not a supported stage outcome.
    """
    if status not in {"ready", "needs_revision"}:
        raise ValueError(f"Unsupported research stage outcome: {status}")
    workflow = paper_workflow.get_research_workflow(workspace.mode)
    stage = workflow.stage_for_run_kind(run.run_kind)
    states = dict(workspace.proposal_stage_states or {})
    previous_stage = states.get(stage)
    previous_iteration = (
        previous_stage.get("iteration", 0)
        if isinstance(previous_stage, dict) and isinstance(previous_stage.get("iteration", 0), int)
        else 0
    )
    for dependent_stage in workflow.dependent_stages(stage):
        previous = states.get(dependent_stage)
        if isinstance(previous, dict) and previous.get("status") in {
            "ready",
            "needs_revision",
        }:
            states[dependent_stage] = {**previous, "status": "stale"}
    states[stage] = {
        "status": status,
        "run_id": str(run.id),
        "source_version_id": str(source_version_id),
        "updated_time": datetime.now(UTC).isoformat(),
        "iteration": previous_iteration + 1,
        "summary": summary,
        "findings": findings or [],
    }
    workspace.proposal_stage_states = states


def _supersede_boundary_targets(
    targets: Iterable[models.PaperOptimizationTarget],
) -> None:
    """Mark artifacts derived from an older boundary as non-actionable."""
    for target in targets:
        target.status = "superseded"


def _workspace_source_entries(source_root: Path) -> list[tuple[str, bytes]]:
    """Collect a validated, symlink-free project tree after an agent edit."""
    entries: list[tuple[str, bytes]] = []
    for path in sorted(source_root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Paper workspace source cannot contain symbolic links")
        if not path.is_file():
            continue
        relative = path.relative_to(source_root).as_posix()
        paper_service._validate_zip_path(relative)
        entries.append((relative, path.read_bytes()))
    paper_service.validate_paper_source_batch(entries)
    return entries


def _validate_rebuttal_entry_coverage(
    workspace: models.PaperWorkspace,
    entries: list[paper_schemas.RebuttalEntry],
) -> None:
    """Require draft entries to cover exactly the analyzed reviewer concerns."""
    analysis = (workspace.rebuttal_context or {}).get("analysis")
    concerns = analysis.get("concerns") if isinstance(analysis, dict) else None
    if not isinstance(concerns, list) or not concerns:
        raise ValueError("Rebuttal review analysis is missing")
    reviewer_by_concern = {
        str(item.get("id")): str(item.get("reviewer_id"))
        for item in concerns
        if isinstance(item, dict) and item.get("id") and item.get("reviewer_id")
    }
    covered = {concern_id for entry in entries for concern_id in entry.concern_ids}
    expected = set(reviewer_by_concern)
    if covered != expected:
        missing = sorted(expected - covered)
        unknown = sorted(covered - expected)
        raise ValueError(f"Rebuttal entries do not match analyzed concerns; missing={missing}, unknown={unknown}")
    for entry in entries:
        mismatched = [
            concern_id for concern_id in entry.concern_ids if reviewer_by_concern.get(concern_id) != entry.reviewer_id
        ]
        if mismatched:
            raise ValueError(f"Rebuttal entry {entry.id} mixes concerns from another reviewer")


def _validate_autorebuttal_constraints(
    workspace: models.PaperWorkspace,
    artifact: AutoRebuttalArtifact,
) -> None:
    """Validate strategy identity and final character limits against the baseline."""
    intake = (workspace.rebuttal_context or {}).get("intake")
    if not isinstance(intake, dict):
        raise ValueError("Rebuttal intake baseline is missing")
    constraints = RebuttalConstraintSummary.model_validate(intake.get("constraints"))
    reviewer_sources = [RebuttalReviewerSource.model_validate(item) for item in intake.get("reviewer_sources", [])]
    expected_reviewers = {item.reviewer_id for item in reviewer_sources}
    format_plan = artifact.strategy.format_plan
    budget_plan = artifact.strategy.budget_plan
    if format_plan.response_mode != constraints.response_mode:
        raise ValueError("AutoRebuttal response mode does not match the confirmed baseline")
    if format_plan.output_format != constraints.output_format:
        raise ValueError("AutoRebuttal output format does not match the confirmed baseline")
    if budget_plan.per_reviewer_limit != constraints.per_reviewer_limit:
        raise ValueError("AutoRebuttal per-reviewer limit does not match the confirmed baseline")
    if budget_plan.total_limit != constraints.total_limit:
        raise ValueError("AutoRebuttal total limit does not match the confirmed baseline")
    budgets_by_reviewer = {item.reviewer_id: item for item in budget_plan.reviewer_budgets}
    if set(budgets_by_reviewer) != expected_reviewers:
        raise ValueError("AutoRebuttal budget must allocate every confirmed reviewer exactly once")
    if constraints.per_reviewer_limit is not None and any(
        item.limit != constraints.per_reviewer_limit for item in budget_plan.reviewer_budgets
    ):
        raise ValueError("Reviewer budget limits do not match the confirmed per-reviewer limit")

    if not artifact.compliance.ready_for_submission:
        return
    counts_by_reviewer = dict.fromkeys(expected_reviewers, 0)
    for entry in artifact.compliance.entries:
        counts_by_reviewer[entry.reviewer_id] = counts_by_reviewer.get(entry.reviewer_id, 0) + entry.character_count
    if constraints.per_reviewer_limit is not None:
        exceeded = sorted(
            reviewer_id for reviewer_id, count in counts_by_reviewer.items() if count > constraints.per_reviewer_limit
        )
        if exceeded:
            raise ValueError(f"Submission-ready rebuttal exceeds reviewer limits: {exceeded}")
    if constraints.total_limit is not None and sum(counts_by_reviewer.values()) > constraints.total_limit:
        raise ValueError("Submission-ready rebuttal exceeds the confirmed total limit")


def _persist_output(run_id: uuid.UUID, user_id: uuid.UUID, work_dir: Path) -> bool:
    """Persist a validated artifact and report whether project source was committed."""
    result_path = work_dir / "output" / "result.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    source_committed = False
    with get_db_session() as db:
        run = db.get(models.PaperAgentRun, run_id)
        if run is None or run.status == models.PaperAgentRunStatus.CANCELLED.value:
            return False
        workspace = db.get(models.PaperWorkspace, run.workspace_id)
        if workspace is None or workspace.user_id != user_id:
            raise RuntimeError("Paper run owner changed")
        artifact_key = f"paper/{user_id}/{workspace.id}/runs/{run.id}/result.json"
        storage.upload(artifact_key, result_path.read_bytes(), content_type="application/json")

        if run.run_kind == models.PaperAgentRunKind.PROPOSAL_FORMATTING.value:
            artifact = ProposalFormattingArtifact.model_validate(payload)
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if source is None:
                raise RuntimeError("Paper source is missing")
            requested_path = paper_service._validate_zip_path(
                str((run.request_payload or {}).get("source_path") or "")
            ).as_posix()
            if PurePosixPath(requested_path).suffix.lower() != ".typ":
                raise ValueError("Proposal formatting target must be a Typst source")
            source_root = paper_service.paper_workspace_source_dir(
                user_id,
                workspace.id,
            )
            entries = _workspace_source_entries(source_root)
            current_manifest = [path for path, _data in entries]
            source_path = _canonical_source_reference(artifact.source_path, current_manifest)
            if source_path != requested_path or PurePosixPath(source_path).suffix.lower() != ".typ":
                raise ValueError("Proposal formatting result does not match the requested Typst source")
            allowed_paths = list((run.request_payload or {}).get("writable_paths") or [])
            _validate_proposal_source_changes(paper_service._source_entries(source), entries, allowed_paths)
            entries = paper_service.ensure_proposal_assembly(entries, source_path)
            entry_map = dict(entries)
            formatted = entry_map.get(source_path, b"")
            if not formatted.strip() or len(formatted) > 5 * 1024 * 1024:
                raise ValueError("Formatted Typst source is empty or too large")
            workspace.proposal_foundation = _merge_proposal_context(
                workspace.proposal_foundation,
                artifact.context_patch,
            ).model_dump(mode="json")
            workspace.proposal_entry_path = source_path
            paper_service._persist_source_entries(
                db,
                workspace,
                source,
                entries,
                change_summary=artifact.summary,
            )
            _update_proposal_stage_state(
                workspace,
                run,
                source.id,
                summary=artifact.summary,
            )
            source_committed = True
        elif run.run_kind in {
            models.PaperAgentRunKind.PROPOSAL_LITERATURE.value,
            models.PaperAgentRunKind.PROPOSAL_RATIONALE.value,
            models.PaperAgentRunKind.PROPOSAL_OBJECTIVES.value,
            models.PaperAgentRunKind.PROPOSAL_METHODS.value,
            models.PaperAgentRunKind.PROPOSAL_INNOVATION_PLAN.value,
            models.PaperAgentRunKind.PROPOSAL_FOUNDATION_FEASIBILITY.value,
        }:
            artifact = ProposalSectionArtifact.model_validate(payload)
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if source is None:
                raise RuntimeError("Paper source is missing")
            source_root = paper_service.paper_workspace_source_dir(user_id, workspace.id)
            entries = _workspace_source_entries(source_root)
            allowed_paths = list((run.request_payload or {}).get("writable_paths") or [])
            changed_paths = _validate_proposal_source_changes(
                paper_service._source_entries(source),
                entries,
                allowed_paths,
            )
            if not workspace.proposal_entry_path:
                raise ValueError("Proposal entry document is missing")
            entries = paper_service.ensure_proposal_assembly(
                entries,
                workspace.proposal_entry_path,
            )
            current_manifest = [path for path, _data in entries]
            reported_paths = {_canonical_source_reference(path, current_manifest) for path in artifact.source_paths}
            if not changed_paths or not changed_paths.issubset(reported_paths):
                raise ValueError("Proposal stage result does not describe every modified source file")
            if any(not _source_path_allowed(path, allowed_paths) for path in reported_paths):
                raise ValueError("Proposal stage result contains a source path owned by another stage")
            paper_service._persist_source_entries(
                db,
                workspace,
                source,
                entries,
                change_summary=artifact.summary,
            )
            workspace.proposal_foundation = _merge_proposal_context(
                workspace.proposal_foundation,
                artifact.context_patch,
            ).model_dump(mode="json")
            _update_proposal_stage_state(
                workspace,
                run,
                source.id,
                summary=artifact.summary,
            )
            source_committed = True
        elif run.run_kind == models.PaperAgentRunKind.PROPOSAL_FINAL_REVIEW.value:
            artifact = ProposalFinalReviewArtifact.model_validate(payload)
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if source is None:
                raise RuntimeError("Paper source is missing")
            source_root = paper_service.paper_workspace_source_dir(user_id, workspace.id)
            entries = _workspace_source_entries(source_root)
            allowed_paths = list((run.request_payload or {}).get("writable_paths") or [])
            changed_paths = _validate_proposal_source_changes(
                paper_service._source_entries(source),
                entries,
                allowed_paths,
            )
            current_manifest = [path for path, _data in entries]
            entry_path = _canonical_source_reference(artifact.entry_path, current_manifest)
            if entry_path != workspace.proposal_entry_path or changed_paths.difference({entry_path}):
                raise ValueError("Final review may only update the proposal entry document")
            entries = paper_service.ensure_proposal_assembly(entries, entry_path)
            paper_service._persist_source_entries(
                db,
                workspace,
                source,
                entries,
                change_summary=artifact.summary,
            )
            workspace.proposal_foundation = _merge_proposal_context(
                workspace.proposal_foundation,
                artifact.context_patch,
            ).model_dump(mode="json")
            _update_proposal_stage_state(
                workspace,
                run,
                source.id,
                status=("ready" if artifact.ready_for_export else "needs_revision"),
                summary=artifact.summary,
                findings=artifact.findings,
            )
            source_committed = True
        elif run.run_kind == models.PaperAgentRunKind.REBUTTAL_BASELINE.value:
            artifact = RebuttalBaselineArtifact.model_validate(payload)
            available_reviews = {
                item.id
                for item in db.exec(
                    select(models.PaperReview).where(
                        models.PaperReview.workspace_id == workspace.id,
                        models.PaperReview.source_version_id == run.source_version_id,
                    )
                ).all()
            }
            reported_reviews = {item.review_id for item in artifact.intake.reviewer_sources}
            if reported_reviews != available_reviews:
                raise ValueError("Rebuttal baseline must cover every review attached to the active paper")
            context = dict(workspace.rebuttal_context or {})
            context["intake"] = artifact.intake.model_dump(mode="json")
            context["analysis"] = artifact.analysis.model_dump(mode="json")
            context["baseline"] = {
                "summary": artifact.summary,
                "ready_for_generation": artifact.ready_for_generation,
                "findings": artifact.findings,
            }
            workspace.rebuttal_context = context
            _update_proposal_stage_state(
                workspace,
                run,
                run.source_version_id,
                status=("ready" if artifact.ready_for_generation else "needs_revision"),
                summary=artifact.summary,
                findings=[*artifact.findings, *artifact.intake.open_questions],
            )
        elif run.run_kind == models.PaperAgentRunKind.AUTOREBUTTAL.value:
            artifact = AutoRebuttalArtifact.model_validate(payload)
            _validate_rebuttal_entry_coverage(workspace, artifact.draft.entries)
            _validate_rebuttal_entry_coverage(workspace, artifact.compliance.entries)
            _validate_autorebuttal_constraints(workspace, artifact)
            workspace.rebuttal_entries = [entry.model_dump(mode="json") for entry in artifact.compliance.entries]
            context = dict(workspace.rebuttal_context or {})
            context["strategy"] = artifact.strategy.model_dump(mode="json")
            context["draft"] = {
                "summary": artifact.draft.summary,
                "entries": [entry.model_dump(mode="json") for entry in artifact.draft.entries],
            }
            context["compliance"] = {
                "summary": artifact.compliance.summary,
                "findings": artifact.compliance.findings,
                "ready_for_submission": artifact.compliance.ready_for_submission,
                "open_placeholders": artifact.compliance.open_placeholders,
            }
            workspace.rebuttal_context = context
            _update_proposal_stage_state(
                workspace,
                run,
                run.source_version_id,
                status=("ready" if artifact.compliance.ready_for_submission else "needs_revision"),
                summary=artifact.summary,
                findings=[
                    *artifact.compliance.findings,
                    *artifact.compliance.open_placeholders,
                ],
            )
        elif run.run_kind == models.PaperAgentRunKind.BOUNDARY_ANALYSIS.value:
            artifact = BoundaryArtifact.model_validate(payload)
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if source is None:
                raise RuntimeError("Paper source is missing")
            boundary_payload = _canonical_boundary_payload(artifact, source.manifest)
            boundary = db.exec(
                select(models.PaperBoundarySnapshot).where(
                    models.PaperBoundarySnapshot.source_version_id == run.source_version_id
                )
            ).first()
            if boundary is None:
                boundary = models.PaperBoundarySnapshot(
                    workspace_id=workspace.id,
                    source_version_id=run.source_version_id,
                )
            else:
                derived_targets = list(
                    db.exec(
                        select(models.PaperOptimizationTarget).where(
                            models.PaperOptimizationTarget.boundary_id == boundary.id,
                            models.PaperOptimizationTarget.status != "superseded",
                        )
                    ).all()
                )
                _supersede_boundary_targets(derived_targets)
                for target in derived_targets:
                    db.add(target)
            boundary.run_id = run.id
            boundary.status = "confirmed"
            boundary.content = boundary_payload
            boundary.updated_time = datetime.now(UTC)
            db.add(boundary)
        elif run.run_kind == models.PaperAgentRunKind.ISSUE_EXTRACTION.value:
            artifact = TargetArtifact.model_validate(payload)
            boundary = db.exec(
                select(models.PaperBoundarySnapshot).where(
                    models.PaperBoundarySnapshot.source_version_id == run.source_version_id,
                )
            ).first()
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if boundary is None or source is None:
                raise RuntimeError("Confirmed paper boundary is missing")
            source_entries = dict(paper_service._source_entries(source))
            replaceable_targets = list(
                db.exec(
                    select(models.PaperOptimizationTarget).where(
                        models.PaperOptimizationTarget.boundary_id == boundary.id,
                        models.PaperOptimizationTarget.status.in_(["draft", "ignored"]),
                    )
                ).all()
            )
            for existing_target in replaceable_targets:
                db.delete(existing_target)
            for item in artifact.targets:
                source_path = _canonical_source_reference(
                    item.source_path,
                    source_entries,
                )
                if item.source_quote:
                    try:
                        source_text = source_entries[source_path].decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise ValueError("Optimization target points to a non-text source") from exc
                    quote_count = source_text.count(item.source_quote)
                    if quote_count == 0:
                        raise ValueError(f"Target quote was not found in source: {source_path}")
                    if item.target_type == "manuscript" and quote_count != 1:
                        raise ValueError(f"Manuscript target quote is not unique in source: {source_path}")
                target_payload = item.model_dump(exclude={"review_ids"})
                target_payload["source_path"] = source_path
                db.add(
                    models.PaperOptimizationTarget(
                        workspace_id=workspace.id,
                        source_version_id=run.source_version_id,
                        boundary_id=boundary.id,
                        review_ids=[str(value) for value in item.review_ids],
                        **target_payload,
                    )
                )
        elif run.run_kind == models.PaperAgentRunKind.ALGORITHM_DISCOVERY.value:
            artifact = DiscoveryArtifact.model_validate(payload)
            for proposal in artifact.proposals:
                db.add(
                    models.PaperAlgorithmProposal(
                        workspace_id=workspace.id,
                        source_version_id=run.source_version_id,
                        run_id=run.id,
                        target_id=(
                            uuid.UUID(str((run.request_payload or {}).get("target_id")))
                            if (run.request_payload or {}).get("target_id")
                            else None
                        ),
                        **proposal.model_dump(),
                    )
                )
        elif run.run_kind == models.PaperAgentRunKind.METRIC_SUGGESTION.value:
            artifact = MetricArtifact.model_validate(payload)
            review = db.get(models.PaperReview, run.review_id)
            if review is None:
                raise RuntimeError("Reviewer baseline is missing")
            existing = {
                item.key
                for item in db.exec(
                    select(models.PaperEvaluationMetric).where(
                        models.PaperEvaluationMetric.source_version_id == run.source_version_id
                    )
                ).all()
            }
            for metric in artifact.metrics:
                if metric.source != "model_suggested" or metric.key in existing:
                    raise ValueError("Model metric suggestions cannot replace reviewer baseline metrics")
                db.add(
                    models.PaperEvaluationMetric(
                        workspace_id=workspace.id,
                        source_version_id=run.source_version_id,
                        review_id=review.id,
                        **metric.model_dump(),
                    )
                )
                existing.add(metric.key)
        elif run.run_kind == models.PaperAgentRunKind.PAPER_REVISION.value:
            artifact = RevisionArtifact.model_validate(payload)
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if source is None:
                raise RuntimeError("Paper source is missing")
            for item in artifact.candidates:
                candidate_id = uuid.uuid4()
                patch_key = f"paper/{user_id}/{workspace.id}/runs/{run.id}/candidates/{candidate_id}.json"
                replacement_payload = item.model_dump(mode="json")
                replacement_payload["source_path"] = _canonical_source_reference(
                    item.source_path,
                    source.manifest,
                )
                storage.upload(
                    patch_key,
                    json.dumps(replacement_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    content_type="application/json",
                )
                db.add(
                    models.PaperRevisionCandidate(
                        id=candidate_id,
                        workspace_id=workspace.id,
                        source_version_id=run.source_version_id,
                        run_id=run.id,
                        target_id=(
                            uuid.UUID(str((run.request_payload or {}).get("target_id")))
                            if (run.request_payload or {}).get("target_id")
                            else None
                        ),
                        title=item.title,
                        summary=item.summary,
                        patch_object_key=patch_key,
                    )
                )
        elif run.run_kind == models.PaperAgentRunKind.JUDGE.value:
            artifact = paper_schemas.PaperJudgeScorePayload.model_validate(payload)
            candidate_id = uuid.UUID(str((run.request_payload or {}).get("candidate_id")))
            candidate = db.get(models.PaperRevisionCandidate, candidate_id)
            if candidate is None or candidate.workspace_id != workspace.id:
                raise RuntimeError("Pinned revision candidate is missing")
            metrics = list(
                db.exec(
                    select(models.PaperEvaluationMetric).where(
                        models.PaperEvaluationMetric.source_version_id == run.source_version_id,
                        models.PaperEvaluationMetric.selected.is_(True),
                    )
                ).all()
            )
            baseline_keys = {item.key for item in metrics if item.locked}
            optional_keys = {item.key for item in metrics if not item.locked}
            if set(artifact.baseline) != baseline_keys or set(artifact.optional) != optional_keys:
                raise ValueError("Judge output does not cover the selected evaluation metrics")
            reviewer_role = str((run.request_payload or {}).get("reviewer_role") or "")
            judge_index = 0 if reviewer_role == "A" else 1
            judge_result = db.exec(
                select(models.PaperJudgeResult).where(
                    models.PaperJudgeResult.candidate_id == candidate.id,
                    models.PaperJudgeResult.judge_index == judge_index,
                )
            ).first()
            if judge_result is None:
                judge_result = models.PaperJudgeResult(
                    candidate_id=candidate.id,
                    judge_index=judge_index,
                )
            judge_result.provider_id = run.provider_id
            judge_result.model_name = run.model_name or ""
            judge_result.baseline_scores = artifact.baseline
            judge_result.optional_scores = artifact.optional
            judge_result.rationale = artifact.rationale
            judge_result.citations = artifact.citations
            db.add(judge_result)
        run.status = models.PaperAgentRunStatus.READY.value
        run.progress = 100
        run.stage = "completed"
        if (
            run.run_kind == models.PaperAgentRunKind.PROPOSAL_FINAL_REVIEW.value
            and isinstance(artifact, ProposalFinalReviewArtifact)
            and not artifact.ready_for_export
        ):
            run.message = "Proposal review requires revision"
        elif (
            run.run_kind == models.PaperAgentRunKind.REBUTTAL_BASELINE.value
            and isinstance(artifact, RebuttalBaselineArtifact)
            and not artifact.ready_for_generation
        ):
            run.message = "Rebuttal baseline requires author confirmation"
        elif (
            run.run_kind == models.PaperAgentRunKind.AUTOREBUTTAL.value
            and isinstance(artifact, AutoRebuttalArtifact)
            and not artifact.compliance.ready_for_submission
        ):
            run.message = "Rebuttal review requires author action"
        else:
            run.message = "Research stage result is ready"
        run.artifact_object_key = artifact_key
        run.error_code = None
        run.error = None
        run.updated_time = datetime.now(UTC)
        db.add(run)
        db.commit()
    return source_committed
