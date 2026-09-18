"""Paper optimization workspace metadata models.

Large source and generated bodies live in RustFS. These tables preserve
ownership, source pointers, review dimensions, agent state, and links to
explicitly created evolution tasks.
"""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import Index, Text, UniqueConstraint
from sqlmodel import JSON, Column, Field, SQLModel

from app.models.base import TimeMixin


class PaperSourceKind(StrEnum):
    MARKDOWN = "markdown"
    LATEX_ZIP = "latex_zip"
    SOURCE_BUNDLE = "source_bundle"


class ResearchWorkspaceMode(StrEnum):
    """Stable research workflow selected when a workspace is created."""

    PROPOSAL = "proposal"
    MANUSCRIPT = "manuscript"
    ALGORITHM = "algorithm"


class PaperAgentRunKind(StrEnum):
    PROPOSAL_FORMATTING = "proposal_formatting"
    PROPOSAL_LITERATURE = "proposal_literature"
    PROPOSAL_RATIONALE = "proposal_rationale"
    PROPOSAL_OBJECTIVES = "proposal_objectives"
    PROPOSAL_METHODS = "proposal_methods"
    PROPOSAL_INNOVATION_PLAN = "proposal_innovation_plan"
    PROPOSAL_FOUNDATION_FEASIBILITY = "proposal_foundation_feasibility"
    PROPOSAL_FINAL_REVIEW = "proposal_final_review"
    BOUNDARY_ANALYSIS = "boundary_analysis"
    ISSUE_EXTRACTION = "issue_extraction"
    ALGORITHM_DISCOVERY = "algorithm_discovery"
    METRIC_SUGGESTION = "metric_suggestion"
    PAPER_REVISION = "paper_revision"
    JUDGE = "judge"


class PaperAgentRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaperWorkspace(SQLModel, TimeMixin, table=True):
    """User-owned paper optimization workspace."""

    __tablename__ = "paper_workspace"
    __table_args__ = (Index("ix_paper_workspace_user_updated", "user_id", "updated_time"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    title: str = Field(max_length=255)
    mode: str = Field(max_length=24)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    active_source_version_id: uuid.UUID | None = Field(default=None, index=True)
    proposal_foundation: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    proposal_entry_path: str | None = Field(default=None, max_length=1_024)
    proposal_stage_states: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    analysis_provider_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="llmprovider.id",
        ondelete="SET NULL",
        index=True,
    )
    analysis_model_name: str | None = Field(default=None, max_length=255)
    analysis_context_window_tokens: int = Field(default=128_000, ge=4_096)
    analysis_max_output_tokens: int = Field(default=16_384, ge=256)
    reviewer_a_provider_id: uuid.UUID | None = Field(default=None, foreign_key="llmprovider.id", ondelete="SET NULL")
    reviewer_a_model_name: str | None = Field(default=None, max_length=255)
    reviewer_b_provider_id: uuid.UUID | None = Field(default=None, foreign_key="llmprovider.id", ondelete="SET NULL")
    reviewer_b_model_name: str | None = Field(default=None, max_length=255)


class PaperSourceVersion(SQLModel, TimeMixin, table=True):
    """Stored Markdown or LaTeX working source."""

    __tablename__ = "paper_source_version"
    __table_args__ = (
        UniqueConstraint("workspace_id", "version", name="uq_paper_source_workspace_version"),
        Index("ix_paper_source_workspace_created", "workspace_id", "created_time"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workspace_id: uuid.UUID = Field(
        foreign_key="paper_workspace.id",
        ondelete="CASCADE",
        index=True,
    )
    version: int = Field(ge=1)
    source_kind: str = Field(max_length=16)
    filename: str = Field(max_length=255)
    object_key: str = Field(max_length=1024, unique=True)
    content_hash: str = Field(max_length=64)
    content_size: int = Field(ge=1)
    manifest: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    parent_version_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="paper_source_version.id",
        ondelete="SET NULL",
        index=True,
    )
    change_summary: str | None = Field(default=None, max_length=500)


class PaperReview(SQLModel, TimeMixin, table=True):
    """Versioned reviewer feedback attached to a paper source."""

    __tablename__ = "paper_review"
    __table_args__ = (Index("ix_paper_review_source_created", "source_version_id", "created_time"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workspace_id: uuid.UUID = Field(
        foreign_key="paper_workspace.id",
        ondelete="CASCADE",
        index=True,
    )
    source_version_id: uuid.UUID = Field(
        foreign_key="paper_source_version.id",
        ondelete="CASCADE",
        index=True,
    )
    source_system: str = Field(default="manual", max_length=64)
    reviewer_label: str = Field(default="Reviewer", max_length=128)
    title: str | None = Field(default=None, max_length=255)
    object_key: str = Field(max_length=1024, unique=True)
    content_hash: str = Field(max_length=64)
    baseline_dimensions: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )


class PaperBoundarySnapshot(SQLModel, TimeMixin, table=True):
    """User-reviewable boundary extracted from a working paper source."""

    __tablename__ = "paper_boundary_snapshot"
    __table_args__ = (UniqueConstraint("source_version_id", name="uq_paper_boundary_source"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workspace_id: uuid.UUID = Field(foreign_key="paper_workspace.id", ondelete="CASCADE", index=True)
    source_version_id: uuid.UUID = Field(foreign_key="paper_source_version.id", ondelete="CASCADE", index=True)
    run_id: uuid.UUID | None = Field(default=None, index=True)
    status: str = Field(default="draft", max_length=16)
    content: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    user_notes: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class PaperOptimizationTarget(SQLModel, TimeMixin, table=True):
    """A reviewer issue mapped to an actionable manuscript or algorithm target."""

    __tablename__ = "paper_optimization_target"
    __table_args__ = (Index("ix_paper_target_workspace_status", "workspace_id", "status"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workspace_id: uuid.UUID = Field(foreign_key="paper_workspace.id", ondelete="CASCADE", index=True)
    source_version_id: uuid.UUID = Field(foreign_key="paper_source_version.id", ondelete="CASCADE", index=True)
    boundary_id: uuid.UUID = Field(foreign_key="paper_boundary_snapshot.id", ondelete="CASCADE", index=True)
    review_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    target_type: str = Field(max_length=16)
    title: str = Field(max_length=255)
    problem: str = Field(sa_column=Column(Text, nullable=False))
    recommendation: str = Field(sa_column=Column(Text, nullable=False))
    severity: str = Field(default="medium", max_length=16)
    source_path: str = Field(max_length=1024)
    section_title: str | None = Field(default=None, max_length=500)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    source_quote: str = Field(default="", sa_column=Column(Text, nullable=False))
    status: str = Field(default="draft", max_length=16)


class PaperAgentRun(SQLModel, TimeMixin, table=True):
    """Durable paper agent attempt for progress recovery and resume."""

    __tablename__ = "paper_agent_run"
    __table_args__ = (Index("ix_paper_run_workspace_created", "workspace_id", "created_time"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workspace_id: uuid.UUID = Field(
        foreign_key="paper_workspace.id",
        ondelete="CASCADE",
        index=True,
    )
    source_version_id: uuid.UUID = Field(
        foreign_key="paper_source_version.id",
        ondelete="CASCADE",
        index=True,
    )
    review_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="paper_review.id",
        ondelete="SET NULL",
        index=True,
    )
    run_kind: str = Field(max_length=32)
    status: str = Field(default=PaperAgentRunStatus.PENDING.value, max_length=16, index=True)
    progress: int = Field(default=0, ge=0, le=100)
    stage: str = Field(default="queued", max_length=64)
    message: str = Field(default="", max_length=500)
    provider_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="llmprovider.id",
        ondelete="SET NULL",
        index=True,
    )
    model_name: str | None = Field(default=None, max_length=255)
    session_id: str | None = Field(default=None, max_length=255)
    celery_task_id: str | None = Field(default=None, max_length=255, index=True)
    request_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    artifact_object_key: str | None = Field(default=None, max_length=1024)
    container_id: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class PaperAlgorithmProposal(SQLModel, TimeMixin, table=True):
    """Structured, user-reviewable algorithm proposal."""

    __tablename__ = "paper_algorithm_proposal"
    __table_args__ = (Index("ix_paper_proposal_workspace_created", "workspace_id", "created_time"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workspace_id: uuid.UUID = Field(
        foreign_key="paper_workspace.id",
        ondelete="CASCADE",
        index=True,
    )
    source_version_id: uuid.UUID = Field(
        foreign_key="paper_source_version.id",
        ondelete="CASCADE",
        index=True,
    )
    run_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="paper_agent_run.id",
        ondelete="SET NULL",
        index=True,
    )
    target_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="paper_optimization_target.id",
        ondelete="SET NULL",
        index=True,
    )
    title: str = Field(max_length=255)
    problem_statement: str = Field(sa_column=Column(Text, nullable=False))
    algorithm_design: str = Field(sa_column=Column(Text, nullable=False))
    evaluator_requirements: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    assumptions: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    provenance: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    suggested_task_config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    status: str = Field(default="draft", max_length=16)


class PaperTaskLink(SQLModel, TimeMixin, table=True):
    """Idempotent relation from one proposal to one root evolution task."""

    __tablename__ = "paper_task_link"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_paper_task_link_proposal"),
        UniqueConstraint("target_id", name="uq_paper_task_link_target"),
        UniqueConstraint("task_id", name="uq_paper_task_link_task"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workspace_id: uuid.UUID = Field(
        foreign_key="paper_workspace.id",
        ondelete="CASCADE",
        index=True,
    )
    proposal_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="paper_algorithm_proposal.id",
        ondelete="CASCADE",
        index=True,
    )
    target_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="paper_optimization_target.id",
        ondelete="CASCADE",
        index=True,
    )
    task_id: uuid.UUID = Field(foreign_key="task.id", ondelete="CASCADE", index=True)


class PaperEvaluationMetric(SQLModel, TimeMixin, table=True):
    """Baseline or optional paper revision evaluation metric."""

    __tablename__ = "paper_evaluation_metric"
    __table_args__ = (
        UniqueConstraint("source_version_id", "key", name="uq_paper_metric_source_key"),
        Index("ix_paper_metric_workspace_source", "workspace_id", "source_version_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workspace_id: uuid.UUID = Field(
        foreign_key="paper_workspace.id",
        ondelete="CASCADE",
        index=True,
    )
    source_version_id: uuid.UUID = Field(
        foreign_key="paper_source_version.id",
        ondelete="CASCADE",
        index=True,
    )
    review_id: uuid.UUID = Field(foreign_key="paper_review.id", ondelete="CASCADE", index=True)
    key: str = Field(max_length=64)
    title: str = Field(max_length=255)
    description: str = Field(sa_column=Column(Text, nullable=False))
    source: str = Field(max_length=32)
    selected: bool = Field(default=True)
    locked: bool = Field(default=False)
    weight: float = Field(default=1, gt=0)
    provenance: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))


class PaperRevisionCandidate(SQLModel, TimeMixin, table=True):
    """Reviewable patch created from a working paper source."""

    __tablename__ = "paper_revision_candidate"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workspace_id: uuid.UUID = Field(
        foreign_key="paper_workspace.id",
        ondelete="CASCADE",
        index=True,
    )
    source_version_id: uuid.UUID = Field(
        foreign_key="paper_source_version.id",
        ondelete="CASCADE",
        index=True,
    )
    run_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="paper_agent_run.id",
        ondelete="SET NULL",
        index=True,
    )
    target_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="paper_optimization_target.id",
        ondelete="SET NULL",
        index=True,
    )
    title: str = Field(max_length=255)
    summary: str = Field(sa_column=Column(Text, nullable=False))
    patch_object_key: str = Field(max_length=1024, unique=True)
    status: str = Field(default="draft", max_length=16)
    accepted_source_version_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="paper_source_version.id",
        ondelete="SET NULL",
    )


class PaperJudgeResult(SQLModel, TimeMixin, table=True):
    """Raw independent Judge scores for a revision candidate."""

    __tablename__ = "paper_judge_result"
    __table_args__ = (UniqueConstraint("candidate_id", "judge_index", name="uq_paper_judge_candidate_index"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    candidate_id: uuid.UUID = Field(
        foreign_key="paper_revision_candidate.id",
        ondelete="CASCADE",
        index=True,
    )
    provider_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="llmprovider.id",
        ondelete="SET NULL",
    )
    model_name: str = Field(max_length=255)
    judge_index: int = Field(ge=0)
    baseline_scores: dict[str, float] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    optional_scores: dict[str, float] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    rationale: str = Field(sa_column=Column(Text, nullable=False))
    citations: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))


class PaperCleanupJob(SQLModel, TimeMixin, table=True):
    """Durable cleanup outbox entry for paper RustFS objects."""

    __tablename__ = "paper_cleanup_job"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    status: str = Field(default="pending", max_length=16, index=True)
    attempts: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
