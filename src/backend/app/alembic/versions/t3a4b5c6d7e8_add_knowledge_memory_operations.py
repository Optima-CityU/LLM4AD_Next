"""persist structured knowledge memory operations

Revision ID: t3a4b5c6d7e8
Revises: s2f3a4b5c6d7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t3a4b5c6d7e8"
down_revision: str | None = "s2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_parse_run",
        sa.Column(
            "generated_memory_operations",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.alter_column(
        "knowledge_parse_run",
        "generated_memory_operations",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("knowledge_parse_run", "generated_memory_operations")
