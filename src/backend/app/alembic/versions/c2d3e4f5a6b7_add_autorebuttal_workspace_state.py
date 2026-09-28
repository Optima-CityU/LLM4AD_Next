"""Add structured AutoRebuttal workspace state.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add durable workflow context and generated rebuttal entries."""
    op.add_column(
        "paper_workspace",
        sa.Column(
            "rebuttal_context",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "paper_workspace",
        sa.Column(
            "rebuttal_entries",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove durable AutoRebuttal state."""
    op.drop_column("paper_workspace", "rebuttal_entries")
    op.drop_column("paper_workspace", "rebuttal_context")
