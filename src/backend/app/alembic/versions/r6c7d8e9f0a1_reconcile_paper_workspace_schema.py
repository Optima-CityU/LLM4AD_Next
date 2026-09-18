"""Reconcile paper workspace schemas created by an earlier draft.

Revision ID: r6c7d8e9f0a1
Revises: q5b6c7d8e9f0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "r6c7d8e9f0a1"
down_revision: str | None = "q5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    """Bring draft paper tables in line with the current application models."""
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    source_columns = {
        column["name"] for column in inspector.get_columns("paper_source_version")
    }
    if "parent_version_id" not in source_columns:
        op.add_column(
            "paper_source_version",
            sa.Column(
                "parent_version_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        )
        op.create_foreign_key(
            "fk_paper_source_version_parent_version_id",
            "paper_source_version",
            "paper_source_version",
            ["parent_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_paper_source_version_parent_version_id",
            "paper_source_version",
            ["parent_version_id"],
        )
    if "change_summary" not in source_columns:
        op.add_column(
            "paper_source_version",
            sa.Column("change_summary", sa.String(length=500), nullable=True),
        )

    review_columns = {
        column["name"] for column in inspector.get_columns("paper_review")
    }
    if "source_system" not in review_columns:
        op.add_column(
            "paper_review",
            sa.Column("source_system", sa.String(length=64), nullable=True),
        )
        if "review_kind" in review_columns:
            op.execute(
                sa.text(
                    "UPDATE paper_review "
                    "SET source_system = COALESCE(NULLIF(review_kind, ''), 'manual')"
                )
            )
        else:
            op.execute(sa.text("UPDATE paper_review SET source_system = 'manual'"))
        op.alter_column(
            "paper_review",
            "source_system",
            existing_type=sa.String(length=64),
            nullable=False,
        )
    if "reviewer_label" not in review_columns:
        op.add_column(
            "paper_review",
            sa.Column("reviewer_label", sa.String(length=128), nullable=True),
        )
        op.execute(sa.text("UPDATE paper_review SET reviewer_label = 'Reviewer'"))
        op.alter_column(
            "paper_review",
            "reviewer_label",
            existing_type=sa.String(length=128),
            nullable=False,
        )
    if "title" not in review_columns:
        op.add_column(
            "paper_review",
            sa.Column("title", sa.String(length=255), nullable=True),
        )
    if "review_kind" in review_columns:
        op.alter_column(
            "paper_review",
            "review_kind",
            existing_type=sa.String(length=32),
            nullable=True,
        )
    if "content_type" in review_columns:
        op.alter_column(
            "paper_review",
            "content_type",
            existing_type=sa.String(length=16),
            nullable=True,
        )

    if "paper_boundary_snapshot" not in tables:
        op.create_table(
            "paper_boundary_snapshot",
            *_timestamps(),
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "source_version_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("content", sa.JSON(), nullable=False),
            sa.Column("user_notes", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(
                ["workspace_id"],
                ["paper_workspace.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["source_version_id"],
                ["paper_source_version.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_version_id",
                name="uq_paper_boundary_source",
            ),
        )
        op.create_index(
            "ix_paper_boundary_snapshot_workspace_id",
            "paper_boundary_snapshot",
            ["workspace_id"],
        )
        op.create_index(
            "ix_paper_boundary_snapshot_source_version_id",
            "paper_boundary_snapshot",
            ["source_version_id"],
        )
        op.create_index(
            "ix_paper_boundary_snapshot_run_id",
            "paper_boundary_snapshot",
            ["run_id"],
        )

    if "paper_optimization_target" not in tables:
        op.create_table(
            "paper_optimization_target",
            *_timestamps(),
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "source_version_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
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
            sa.ForeignKeyConstraint(
                ["workspace_id"],
                ["paper_workspace.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["source_version_id"],
                ["paper_source_version.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["boundary_id"],
                ["paper_boundary_snapshot.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("workspace_id", "source_version_id", "boundary_id"):
            op.create_index(
                f"ix_paper_optimization_target_{column}",
                "paper_optimization_target",
                [column],
            )
        op.create_index(
            "ix_paper_target_workspace_status",
            "paper_optimization_target",
            ["workspace_id", "status"],
        )

    proposal_columns = {
        column["name"] for column in inspector.get_columns("paper_algorithm_proposal")
    }
    if "target_id" not in proposal_columns:
        op.add_column(
            "paper_algorithm_proposal",
            sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_paper_algorithm_proposal_target_id",
            "paper_algorithm_proposal",
            "paper_optimization_target",
            ["target_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_paper_algorithm_proposal_target_id",
            "paper_algorithm_proposal",
            ["target_id"],
        )

    task_link_columns = {
        column["name"] for column in inspector.get_columns("paper_task_link")
    }
    if "target_id" not in task_link_columns:
        op.add_column(
            "paper_task_link",
            sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_paper_task_link_target_id",
            "paper_task_link",
            "paper_optimization_target",
            ["target_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_unique_constraint(
            "uq_paper_task_link_target",
            "paper_task_link",
            ["target_id"],
        )
        op.create_index(
            "ix_paper_task_link_target_id",
            "paper_task_link",
            ["target_id"],
        )

    revision_columns = {
        column["name"] for column in inspector.get_columns("paper_revision_candidate")
    }
    if "target_id" not in revision_columns:
        op.add_column(
            "paper_revision_candidate",
            sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_paper_revision_candidate_target_id",
            "paper_revision_candidate",
            "paper_optimization_target",
            ["target_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_paper_revision_candidate_target_id",
            "paper_revision_candidate",
            ["target_id"],
        )


def downgrade() -> None:
    """Keep reconciled columns because the base revision may already own them."""
