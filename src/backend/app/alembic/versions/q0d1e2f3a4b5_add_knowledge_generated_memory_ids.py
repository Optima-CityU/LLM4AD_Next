"""add generated memory ids to knowledge parse runs

Revision ID: q0d1e2f3a4b5
Revises: p9c0d1e2f3a4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q0d1e2f3a4b5"
down_revision: str | None = "p9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_parse_run",
        sa.Column("generated_memory_ids", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column("knowledge_parse_run", "generated_memory_ids", server_default=None)


def downgrade() -> None:
    op.drop_column("knowledge_parse_run", "generated_memory_ids")
