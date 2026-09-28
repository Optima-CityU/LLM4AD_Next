"""Add durable research proposal foundation.

Revision ID: y8f9a0b1c2d3
Revises: x7e8f9a0b1c2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "y8f9a0b1c2d3"
down_revision: str | None = "x7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist proposal-wide constraints for every later workflow stage."""
    op.add_column(
        "paper_workspace",
        sa.Column("proposal_foundation", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Remove the proposal foundation metadata."""
    op.drop_column("paper_workspace", "proposal_foundation")
