"""add paper optimization workspace

Revision ID: p4a5b6c7d8e9
Revises: d4e5f6a7b8c9, t3a4b5c6d7e8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p4a5b6c7d8e9"
down_revision: tuple[str, str] = ("d4e5f6a7b8c9", "t3a4b5c6d7e8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "paper_workspace",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active_source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analysis_provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analysis_model_name", sa.String(length=255), nullable=True),
        sa.Column("analysis_context_window_tokens", sa.Integer(), nullable=False),
        sa.Column("analysis_max_output_tokens", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["analysis_provider_id"], ["llmprovider.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_workspace_user_id", "paper_workspace", ["user_id"])
    op.create_index(
        "ix_paper_workspace_active_source_version_id",
        "paper_workspace",
        ["active_source_version_id"],
    )
    op.create_index(
        "ix_paper_workspace_analysis_provider_id",
        "paper_workspace",
        ["analysis_provider_id"],
    )
    op.create_index(
        "ix_paper_workspace_user_updated",
        "paper_workspace",
        ["user_id", "updated_time"],
    )
    op.create_table(
        "paper_source_version",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_size", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("change_summary", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["paper_workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_version_id"], ["paper_source_version.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
        sa.UniqueConstraint("workspace_id", "version", name="uq_paper_source_workspace_version"),
    )
    op.create_index("ix_paper_source_version_workspace_id", "paper_source_version", ["workspace_id"])
    op.create_index("ix_paper_source_version_parent_version_id", "paper_source_version", ["parent_version_id"])
    op.create_index(
        "ix_paper_source_workspace_created",
        "paper_source_version",
        ["workspace_id", "created_time"],
    )

    op.create_table(
        "paper_review",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("reviewer_label", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("baseline_dimensions", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["paper_workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_version_id"], ["paper_source_version.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index("ix_paper_review_workspace_id", "paper_review", ["workspace_id"])
    op.create_index("ix_paper_review_source_version_id", "paper_review", ["source_version_id"])
    op.create_index(
        "ix_paper_review_source_created",
        "paper_review",
        ["source_version_id", "created_time"],
    )

    op.create_table(
        "paper_boundary_snapshot",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("user_notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["paper_workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_version_id"], ["paper_source_version.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_version_id", name="uq_paper_boundary_source"),
    )
    op.create_index("ix_paper_boundary_snapshot_workspace_id", "paper_boundary_snapshot", ["workspace_id"])
    op.create_index("ix_paper_boundary_snapshot_source_version_id", "paper_boundary_snapshot", ["source_version_id"])
    op.create_index("ix_paper_boundary_snapshot_run_id", "paper_boundary_snapshot", ["run_id"])

    op.create_table(
        "paper_optimization_target",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("boundary_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_ids", sa.JSON(), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("source_path", sa.String(length=1024), nullable=False),
        sa.Column("section_title", sa.String(length=500), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["paper_workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_version_id"], ["paper_source_version.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["boundary_id"], ["paper_boundary_snapshot.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("workspace_id", "source_version_id", "boundary_id"):
        op.create_index(f"ix_paper_optimization_target_{column}", "paper_optimization_target", [column])
    op.create_index("ix_paper_target_workspace_status", "paper_optimization_target", ["workspace_id", "status"])

    op.create_table(
        "paper_agent_run",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("session_id", sa.String(length=255), nullable=True),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("artifact_object_key", sa.String(length=1024), nullable=True),
        sa.Column("container_id", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["paper_workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_version_id"], ["paper_source_version.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_id"], ["paper_review.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_id"], ["llmprovider.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "workspace_id",
        "source_version_id",
        "review_id",
        "provider_id",
        "status",
        "celery_task_id",
    ):
        op.create_index(f"ix_paper_agent_run_{column}", "paper_agent_run", [column])
    op.create_index(
        "ix_paper_run_workspace_created",
        "paper_agent_run",
        ["workspace_id", "created_time"],
    )

    op.create_table(
        "paper_algorithm_proposal",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("problem_statement", sa.Text(), nullable=False),
        sa.Column("algorithm_design", sa.Text(), nullable=False),
        sa.Column("evaluator_requirements", sa.JSON(), nullable=False),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("suggested_task_config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["paper_workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_version_id"], ["paper_source_version.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["paper_agent_run.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_id"], ["paper_optimization_target.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("workspace_id", "source_version_id", "run_id", "target_id"):
        op.create_index(
            f"ix_paper_algorithm_proposal_{column}",
            "paper_algorithm_proposal",
            [column],
        )
    op.create_index(
        "ix_paper_proposal_workspace_created",
        "paper_algorithm_proposal",
        ["workspace_id", "created_time"],
    )

    op.create_table(
        "paper_task_link",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["paper_workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["proposal_id"], ["paper_algorithm_proposal.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["paper_optimization_target.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", name="uq_paper_task_link_proposal"),
        sa.UniqueConstraint("target_id", name="uq_paper_task_link_target"),
        sa.UniqueConstraint("task_id", name="uq_paper_task_link_task"),
    )
    for column in ("workspace_id", "proposal_id", "target_id", "task_id"):
        op.create_index(f"ix_paper_task_link_{column}", "paper_task_link", [column])

    op.create_table(
        "paper_evaluation_metric",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["paper_workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_version_id"], ["paper_source_version.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["review_id"], ["paper_review.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_version_id", "key", name="uq_paper_metric_source_key"),
    )
    for column in ("workspace_id", "source_version_id", "review_id"):
        op.create_index(
            f"ix_paper_evaluation_metric_{column}",
            "paper_evaluation_metric",
            [column],
        )
    op.create_index(
        "ix_paper_metric_workspace_source",
        "paper_evaluation_metric",
        ["workspace_id", "source_version_id"],
    )

    op.create_table(
        "paper_revision_candidate",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("patch_object_key", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("accepted_source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["paper_workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_version_id"], ["paper_source_version.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["paper_agent_run.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_id"], ["paper_optimization_target.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["accepted_source_version_id"], ["paper_source_version.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("patch_object_key"),
    )
    for column in ("workspace_id", "source_version_id", "run_id", "target_id"):
        op.create_index(
            f"ix_paper_revision_candidate_{column}",
            "paper_revision_candidate",
            [column],
        )

    op.create_table(
        "paper_judge_result",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("judge_index", sa.Integer(), nullable=False),
        sa.Column("baseline_scores", sa.JSON(), nullable=False),
        sa.Column("optional_scores", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["paper_revision_candidate.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["llmprovider.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", "judge_index", name="uq_paper_judge_candidate_index"),
    )
    op.create_index("ix_paper_judge_result_candidate_id", "paper_judge_result", ["candidate_id"])

    op.create_table(
        "paper_cleanup_job",
        *_timestamps(),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_cleanup_job_user_id", "paper_cleanup_job", ["user_id"])
    op.create_index("ix_paper_cleanup_job_status", "paper_cleanup_job", ["status"])


def downgrade() -> None:
    op.drop_table("paper_cleanup_job")
    op.drop_table("paper_judge_result")
    op.drop_table("paper_revision_candidate")
    op.drop_table("paper_evaluation_metric")
    op.drop_table("paper_task_link")
    op.drop_table("paper_algorithm_proposal")
    op.drop_table("paper_agent_run")
    op.drop_table("paper_optimization_target")
    op.drop_table("paper_boundary_snapshot")
    op.drop_table("paper_review")
    op.drop_table("paper_source_version")
    op.drop_table("paper_workspace")
