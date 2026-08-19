"""add knowledge document token estimate

Revision ID: p9c0d1e2f3a4
Revises: o8b9c0d1e2f3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p9c0d1e2f3a4"
down_revision: str | None = "o8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_document",
        sa.Column("estimated_tokens", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE knowledge_document "
        "SET estimated_tokens = GREATEST(1, CEIL(content_size / 4.0)::integer)"
    )
    op.alter_column("knowledge_document", "estimated_tokens", nullable=False)


def downgrade() -> None:
    op.drop_column("knowledge_document", "estimated_tokens")
