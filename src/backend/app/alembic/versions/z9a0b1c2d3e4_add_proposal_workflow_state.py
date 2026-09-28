"""Add proposal entry path and durable stage state.

Revision ID: z9a0b1c2d3e4
Revises: y8f9a0b1c2d3
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "z9a0b1c2d3e4"
down_revision: str | None = "y8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add proposal workflow metadata columns."""
    op.add_column(
        "paper_workspace", sa.Column("proposal_entry_path", sa.String(length=1024), nullable=True)
    )
    op.add_column(
        "paper_workspace",
        sa.Column(
            "proposal_stage_states", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
    )


def downgrade() -> None:
    """Remove proposal workflow metadata columns."""
    op.drop_column("paper_workspace", "proposal_stage_states")
    op.drop_column("paper_workspace", "proposal_entry_path")
