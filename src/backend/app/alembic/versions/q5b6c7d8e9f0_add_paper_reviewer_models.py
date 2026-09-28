"""Add paper reviewer model bindings.

Revision ID: q5b6c7d8e9f0
Revises: p4a5b6c7d8e9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "q5b6c7d8e9f0"
down_revision: str | None = "p4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add independent reviewer A and reviewer B model bindings."""
    op.add_column(
        "paper_workspace",
        sa.Column(
            "reviewer_a_provider_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "paper_workspace",
        sa.Column("reviewer_a_model_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "paper_workspace",
        sa.Column(
            "reviewer_b_provider_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "paper_workspace",
        sa.Column("reviewer_b_model_name", sa.String(length=255), nullable=True),
    )
    op.create_foreign_key(
        "fk_paper_workspace_reviewer_a_provider_id_llmprovider",
        "paper_workspace",
        "llmprovider",
        ["reviewer_a_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_paper_workspace_reviewer_b_provider_id_llmprovider",
        "paper_workspace",
        "llmprovider",
        ["reviewer_b_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_paper_workspace_reviewer_a_provider_id",
        "paper_workspace",
        ["reviewer_a_provider_id"],
    )
    op.create_index(
        "ix_paper_workspace_reviewer_b_provider_id",
        "paper_workspace",
        ["reviewer_b_provider_id"],
    )


def downgrade() -> None:
    """Remove independent reviewer A and reviewer B model bindings."""
    op.drop_index(
        "ix_paper_workspace_reviewer_b_provider_id",
        table_name="paper_workspace",
    )
    op.drop_index(
        "ix_paper_workspace_reviewer_a_provider_id",
        table_name="paper_workspace",
    )
    op.drop_constraint(
        "fk_paper_workspace_reviewer_b_provider_id_llmprovider",
        "paper_workspace",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_paper_workspace_reviewer_a_provider_id_llmprovider",
        "paper_workspace",
        type_="foreignkey",
    )
    op.drop_column("paper_workspace", "reviewer_b_model_name")
    op.drop_column("paper_workspace", "reviewer_b_provider_id")
    op.drop_column("paper_workspace", "reviewer_a_model_name")
    op.drop_column("paper_workspace", "reviewer_a_provider_id")
