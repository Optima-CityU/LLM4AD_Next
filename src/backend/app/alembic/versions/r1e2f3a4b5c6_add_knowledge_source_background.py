"""add saved background to knowledge sources

Revision ID: r1e2f3a4b5c6
Revises: q0d1e2f3a4b5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r1e2f3a4b5c6"
down_revision: str | None = "q0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_source",
        sa.Column("background", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_source", "background")
