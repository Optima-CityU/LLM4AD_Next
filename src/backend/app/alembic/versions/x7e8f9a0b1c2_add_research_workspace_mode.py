"""Add the creation-time research workspace mode.

Revision ID: x7e8f9a0b1c2
Revises: r6c7d8e9f0a1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "x7e8f9a0b1c2"
down_revision: str | None = "r6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist the explicitly selected research workspace mode."""
    op.add_column(
        "paper_workspace",
        sa.Column(
            "mode",
            sa.String(length=24),
            nullable=False,
            server_default="manuscript",
        ),
    )
    op.alter_column("paper_workspace", "mode", server_default=None)


def downgrade() -> None:
    """Remove the research workspace mode."""
    op.drop_column("paper_workspace", "mode")
