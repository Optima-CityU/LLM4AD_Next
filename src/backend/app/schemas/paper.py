"""Request and response contracts for paper optimization workspaces."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PaperSourceKind = Literal["markdown", "latex_zip", "source_bundle"]
PaperRunKind = Literal[
    "proposal_formatting",
    "proposal_literature",
    "proposal_rationale",
    "proposal_objectives",
    "proposal_methods",
    "proposal_innovation_plan",
    "proposal_foundation_feasibility",
    "proposal_final_review",
    "boundary_analysis",
    "issue_extraction",
    "algorithm_discovery",
    "metric_suggestion",
    "paper_revision",
    "judge",
]
PaperRunStatus = Literal["pending", "running", "ready", "failed", "cancelled"]
PaperMetricSource = Literal["reviewer_baseline", "model_suggested", "user_defined"]
ResearchWorkspaceMode = Literal["proposal", "manuscript", "algorithm"]
ResearchWorkflowStage = Literal[
    "formatting",
    "literature",
    "rationale",
    "objectives",
    "methods",
    "innovation_plan",
    "foundation_feasibility",
    "final_review",
    "reviews",
    "boundary",
    "targets",
    "branch",
]


class PaperWorkspaceCreate(BaseModel):
    """Create an independent research workspace."""

    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4_000)
    mode: Literal["proposal", "manuscript", "algorithm"]


class PaperWorkspaceUpdate(BaseModel):
    """Update user-editable paper workspace metadata."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4_000)


class ProposalContextBlock(BaseModel):
    """One model-defined block in the durable proposal context."""

    key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=200_000)
    source_refs: list[str] = Field(default_factory=list, max_length=200)


class ProposalFoundation(BaseModel):
    """Ordered model-defined context shared by every proposal stage."""

    model_config = ConfigDict(extra="forbid")

    blocks: list[ProposalContextBlock] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_unique_keys(self) -> ProposalFoundation:
        """Require stable unique keys so later stages can update blocks safely."""
        keys = [block.key for block in self.blocks]
        if len(keys) != len(set(keys)):
            raise ValueError("Proposal context block keys must be unique")
        return self


class ProposalContextPatch(BaseModel):
    """Model-requested changes to the shared proposal context."""

    upsert: list[ProposalContextBlock] = Field(default_factory=list, max_length=100)
    remove_keys: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_operations(self) -> ProposalContextPatch:
        """Reject ambiguous updates before they reach persistent state."""
        upsert_keys = [block.key for block in self.upsert]
        if len(upsert_keys) != len(set(upsert_keys)):
            raise ValueError("Proposal context patch contains duplicate upsert keys")
        if len(self.remove_keys) != len(set(self.remove_keys)):
            raise ValueError("Proposal context patch contains duplicate remove keys")
        if set(upsert_keys).intersection(self.remove_keys):
            raise ValueError("Proposal context patch cannot update and remove the same key")
        return self


class ProposalStageState(BaseModel):
    """Latest durable state for one proposal workflow stage."""

    status: Literal["ready", "stale", "needs_revision"]
    run_id: uuid.UUID
    source_version_id: uuid.UUID
    updated_time: datetime
    iteration: int = Field(default=1, ge=1)
    summary: str | None = None
    findings: list[str] = Field(default_factory=list, max_length=200)


class PaperWorkspaceSummary(BaseModel):
    """Paper workspace list item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    mode: ResearchWorkspaceMode
    description: str | None
    active_source_version_id: uuid.UUID | None
    proposal_foundation: ProposalFoundation | None
    proposal_entry_path: str | None
    proposal_stage_states: dict[str, ProposalStageState] = Field(default_factory=dict)
    analysis_provider_id: uuid.UUID | None
    analysis_model_name: str | None
    analysis_context_window_tokens: int
    analysis_max_output_tokens: int
    reviewer_a_provider_id: uuid.UUID | None
    reviewer_a_model_name: str | None
    reviewer_b_provider_id: uuid.UUID | None
    reviewer_b_model_name: str | None
    created_time: datetime
    updated_time: datetime


class PaperWorkspaceList(BaseModel):
    """Paginated paper workspace response."""

    items: list[PaperWorkspaceSummary]
    total: int
    skip: int
    limit: int


class PaperSourceVersionResponse(BaseModel):
    """Metadata for the stored working paper source."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    version: int
    source_kind: PaperSourceKind
    filename: str
    object_key: str
    content_hash: str
    content_size: int
    manifest: list[str]
    parent_version_id: uuid.UUID | None
    change_summary: str | None
    created_time: datetime


class PaperReviewCreate(BaseModel):
    """Attach generic reviewer feedback to one source version."""

    content: str = Field(min_length=1, max_length=2_000_000)
    source_system: str = Field(default="manual", min_length=1, max_length=64)
    reviewer_label: str = Field(default="Reviewer", min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=255)


class PaperReviewUpdate(BaseModel):
    """Replace editable reviewer feedback and its derived baseline."""

    content: str = Field(min_length=1, max_length=2_000_000)
    reviewer_label: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=255)


class PaperReviewResponse(BaseModel):
    """Stored generic reviewer feedback metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_version_id: uuid.UUID
    source_system: str
    reviewer_label: str
    title: str | None
    object_key: str
    content_hash: str
    baseline_dimensions: list[dict[str, Any]]
    created_time: datetime


class PaperReviewContentResponse(BaseModel):
    """Owned reviewer Markdown content."""

    review_id: uuid.UUID
    content: str


class PaperMetricDraft(BaseModel):
    """Editable evaluation dimension suggested from paper context."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=4_000)
    source: PaperMetricSource
    selected: bool = True
    locked: bool = False
    weight: float = Field(default=1, gt=0, le=100)
    provenance: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def lock_reviewer_baseline(self):
        """Ensure baseline metrics cannot be represented as optional."""
        if self.source == "reviewer_baseline":
            self.locked = True
            self.selected = True
        return self


class PaperMetricSelection(BaseModel):
    """User selection for an existing metric key."""

    key: str
    selected: bool
    weight: float = Field(gt=0, le=100)


class PaperMetricSelectionRequest(BaseModel):
    """Replace editable selection while retaining locked baseline metrics."""

    metrics: list[PaperMetricSelection]


class PaperMetricSuggestionRequest(BaseModel):
    """Validated suggestions produced by a model run."""

    metrics: list[PaperMetricDraft] = Field(min_length=1, max_length=50)


class PaperMetricResponse(PaperMetricDraft):
    """Persisted metric."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    source_version_id: uuid.UUID
    review_id: uuid.UUID
    created_time: datetime
    updated_time: datetime


class PaperAlgorithmProposalCreate(BaseModel):
    """Structured algorithm proposal emitted by an exploration agent."""

    title: str = Field(min_length=1, max_length=255)
    problem_statement: str = Field(min_length=1, max_length=20_000)
    algorithm_design: str = Field(min_length=1, max_length=100_000)
    evaluator_requirements: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    provenance: list[str] = Field(default_factory=list, max_length=200)
    suggested_task_config: dict[str, Any] = Field(default_factory=dict)


class PaperAlgorithmProposalUpdate(BaseModel):
    """User edits applied before a proposal is confirmed."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    problem_statement: str | None = Field(default=None, min_length=1, max_length=20_000)
    algorithm_design: str | None = Field(default=None, min_length=1, max_length=100_000)
    evaluator_requirements: list[str] | None = Field(default=None, max_length=100)
    assumptions: list[str] | None = Field(default=None, max_length=100)
    provenance: list[str] | None = Field(default=None, max_length=200)
    suggested_task_config: dict[str, Any] | None = None


class PaperAlgorithmProposalResponse(PaperAlgorithmProposalCreate):
    """Persisted proposal and linked task state."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    source_version_id: uuid.UUID
    run_id: uuid.UUID | None
    target_id: uuid.UUID | None
    target_type: Literal["manuscript", "algorithm"] | None = None
    status: str
    task_id: uuid.UUID | None = None
    task_project_id: uuid.UUID | None = None
    created_time: datetime
    updated_time: datetime


class PaperProposalTaskCreateRequest(BaseModel):
    """Confirm proposals and create one root task per selected proposal."""

    proposal_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    language: Literal["zh", "en"] = "zh"


class PaperProposalTaskLinkResponse(BaseModel):
    """Idempotent link between a proposal and its explicit evolution task."""

    model_config = ConfigDict(from_attributes=True)

    proposal_id: uuid.UUID
    task_id: uuid.UUID
    project_id: uuid.UUID


class PaperSourceDownloadResponse(BaseModel):
    """Short-lived download URL for an owned source version."""

    url: str
    filename: str


class PaperSourceFileResponse(BaseModel):
    """UTF-8 source file content from an owned immutable version."""

    source_version_id: uuid.UUID
    path: str
    content: str


class PaperSourcePathDeleteRequest(BaseModel):
    """Remove one file or directory prefix through version derivation."""

    path: str = Field(min_length=1, max_length=1_024)
    workflow_stage: ResearchWorkflowStage | None = None


class PaperSourceFileUpdateRequest(BaseModel):
    """Save one UTF-8 file in the working paper source."""

    path: str = Field(min_length=1, max_length=1_024)
    content: str = Field(max_length=10_000_000)
    workflow_stage: ResearchWorkflowStage | None = None


class PaperExportResponse(BaseModel):
    """Owned short-lived export URL."""

    url: str
    filename: str


class PaperModelBindingUpdate(BaseModel):
    """Persist the analysis model used by non-Judge paper runs."""

    provider_id: uuid.UUID
    model_name: str = Field(min_length=1, max_length=255)
    context_window_tokens: int = Field(default=128_000, ge=4_096, le=2_000_000)
    max_output_tokens: int = Field(default=16_384, ge=256, le=256_000)


class PaperModelBindingResponse(PaperModelBindingUpdate):
    """Saved workspace analysis model binding."""

    configured: bool = True


class PaperJudgeBinding(BaseModel):
    """One anonymous reviewer model binding."""

    provider_id: uuid.UUID
    model_name: str = Field(min_length=1, max_length=255)


class PaperJudgeBindingsUpdate(BaseModel):
    """Reviewer A and Reviewer B model bindings."""

    reviewer_a: PaperJudgeBinding
    reviewer_b: PaperJudgeBinding


class PaperJudgeBindingsResponse(PaperJudgeBindingsUpdate):
    """Saved manuscript evaluation bindings."""

    configured: bool = True


class PaperBoundarySourceRef(BaseModel):
    """One source location supporting a model-defined boundary section."""

    path: str = Field(min_length=1, max_length=1_024)
    sections: list[str] = Field(default_factory=list, max_length=50)


class PaperBoundarySection(BaseModel):
    """One progressively published, model-defined boundary section."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=20_000)
    details: list[str] = Field(default_factory=list, max_length=100)
    source_refs: list[PaperBoundarySourceRef] = Field(default_factory=list, max_length=100)


class PaperBoundaryDraft(BaseModel):
    """Ordered paper scope in the requested interface language."""

    sections: list[PaperBoundarySection] = Field(min_length=1, max_length=30)
    source_map: list[dict[str, Any]] = Field(default_factory=list, max_length=2_000)
    unknowns: list[str] = Field(default_factory=list, max_length=100)


class PaperBoundaryResponse(BaseModel):
    """Persisted paper boundary snapshot."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    source_version_id: uuid.UUID
    run_id: uuid.UUID | None
    status: str
    content: dict[str, Any]
    user_notes: str | None
    created_time: datetime
    updated_time: datetime


class PaperOptimizationTargetDraft(BaseModel):
    """One source-grounded paper optimization target."""

    target_type: Literal["manuscript", "algorithm"]
    title: str = Field(min_length=1, max_length=255)
    problem: str = Field(min_length=1, max_length=20_000)
    recommendation: str = Field(min_length=1, max_length=20_000)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    source_path: str = Field(min_length=1, max_length=1_024)
    section_title: str | None = Field(default=None, max_length=500)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    source_quote: str = Field(default="", max_length=100_000)
    review_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class PaperOptimizationTargetUpdate(BaseModel):
    """Editable target fields and confirmation state."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    problem: str | None = Field(default=None, min_length=1, max_length=20_000)
    recommendation: str | None = Field(default=None, min_length=1, max_length=20_000)
    severity: Literal["low", "medium", "high", "critical"] | None = None
    selected: bool | None = None


class PaperOptimizationTargetResponse(PaperOptimizationTargetDraft):
    """Persisted optimization target and linked task state."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    source_version_id: uuid.UUID
    boundary_id: uuid.UUID
    status: str
    task_id: uuid.UUID | None = None
    task_project_id: uuid.UUID | None = None
    created_time: datetime
    updated_time: datetime


class PaperAgentRunResponse(BaseModel):
    """Durable paper run state for polling and stream recovery."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    source_version_id: uuid.UUID
    run_kind: PaperRunKind
    status: PaperRunStatus
    progress: int
    stage: str
    message: str
    provider_id: uuid.UUID | None
    model_name: str | None
    session_id: str | None
    celery_task_id: str | None
    artifact_object_key: str | None
    error_code: str | None
    error: str | None
    created_time: datetime
    updated_time: datetime


class PaperJudgeScorePayload(BaseModel):
    """One Judge's independently validated score dimensions."""

    baseline: dict[str, float]
    optional: dict[str, float] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=20_000)
    citations: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_score_ranges(self):
        """Keep every Judge dimension on the documented zero-to-one scale."""
        values = [*self.baseline.values(), *self.optional.values()]
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("Judge scores must be between 0 and 1")
        return self


class PaperJudgeAggregate(BaseModel):
    """Deterministic aggregate that preserves score namespaces."""

    baseline: dict[str, float]
    optional: dict[str, float]
    overall: float
    disagreement: float


class PaperJudgeResultResponse(BaseModel):
    """One persisted Judge response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    provider_id: uuid.UUID | None
    model_name: str
    judge_index: int
    baseline_scores: dict[str, float]
    optional_scores: dict[str, float]
    rationale: str
    citations: list[str]
    created_time: datetime


class PaperRevisionCandidateResponse(BaseModel):
    """Revision candidate with independent Judge breakdown."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    source_version_id: uuid.UUID
    run_id: uuid.UUID | None
    target_id: uuid.UUID | None
    title: str
    summary: str
    status: str
    accepted_source_version_id: uuid.UUID | None
    judge_results: list[PaperJudgeResultResponse] = Field(default_factory=list)
    aggregate: PaperJudgeAggregate | None = None
    created_time: datetime
    updated_time: datetime


class PaperRevisionPatchResponse(BaseModel):
    """User-owned revision patch content for review."""

    candidate_id: uuid.UUID
    content: str


class PaperRevisionCandidateCreate(BaseModel):
    """Import an evolved manuscript replacement for review and acceptance."""

    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(default="", max_length=20_000)
    replacement_text: str = Field(min_length=1, max_length=500_000)


class PaperRevisionApplyResponse(BaseModel):
    """Accepted candidate and updated working paper source."""

    candidate_id: uuid.UUID
    source_version: PaperSourceVersionResponse


class PaperWorkspaceDetail(PaperWorkspaceSummary):
    """Paper workspace with source, review, metric, and proposal summaries."""

    source_versions: list[PaperSourceVersionResponse] = Field(default_factory=list)
    reviews: list[PaperReviewResponse] = Field(default_factory=list)
    boundary: PaperBoundaryResponse | None = None
    targets: list[PaperOptimizationTargetResponse] = Field(default_factory=list)
    metrics: list[PaperMetricResponse] = Field(default_factory=list)
    proposals: list[PaperAlgorithmProposalResponse] = Field(default_factory=list)
    revision_candidates: list[PaperRevisionCandidateResponse] = Field(default_factory=list)
    active_run: PaperAgentRunResponse | None = None
    workflow_available: bool
    workflow_stages: list[ResearchWorkflowStage] = Field(default_factory=list)
    available_run_kinds: list[PaperRunKind] = Field(default_factory=list)


class PaperRuntimeSessionResponse(BaseModel):
    """Opaque browser session for an embedded research runtime."""

    runtime_url: str
    session_id: str


class PaperRuntimeSessionCreate(BaseModel):
    """Select the backend-owned stage profile for a native session."""

    workflow_stage: ResearchWorkflowStage
