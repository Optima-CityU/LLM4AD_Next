"""track structured knowledge document insertion

Revision ID: s2f3a4b5c6d7
Revises: r1e2f3a4b5c6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s2f3a4b5c6d7"
down_revision: str | None = "r1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_parse_run",
        sa.Column(
            "inserted_document_ids",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.alter_column("knowledge_parse_run", "inserted_document_ids", server_default=None)


def downgrade() -> None:
    op.drop_column("knowledge_parse_run", "inserted_document_ids")
