"""Keep research conversation preferences separate from generated artifacts.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the per-workspace conversation preferences."""
    op.add_column(
        "paper_workspace",
        sa.Column(
            "conversation_preferences",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove the per-workspace conversation preferences."""
    op.drop_column("paper_workspace", "conversation_preferences")
