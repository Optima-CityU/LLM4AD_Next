"""add memory retrieval and injection modes

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-07-16 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f9a0b1c2d3e4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None

_TABLES = ("user_memory_config", "project_memory_config")


def upgrade():
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("retrieval_mode", sa.String(length=16), nullable=False, server_default="auto"),
        )
        op.add_column(
            table,
            sa.Column(
                "pinned_card_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
        op.add_column(
            table,
            sa.Column("task_injection_mode", sa.String(length=16), nullable=False, server_default="topk"),
        )


def downgrade():
    for table in _TABLES:
        op.drop_column(table, "task_injection_mode")
        op.drop_column(table, "pinned_card_ids")
        op.drop_column(table, "retrieval_mode")
