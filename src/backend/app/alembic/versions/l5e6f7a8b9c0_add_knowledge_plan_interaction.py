"""add knowledge plan interaction state

Revision ID: l5e6f7a8b9c0
Revises: k4d5e6f7a8b9
Create Date: 2026-08-11 00:10:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "l5e6f7a8b9c0"
down_revision = "k4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_parse_plan",
        sa.Column(
            "interaction_mode",
            sa.String(length=16),
            nullable=False,
            server_default="collaborative",
        ),
    )
    op.add_column(
        "knowledge_parse_plan",
        sa.Column("pending_question", sa.JSON(), nullable=True),
    )
    op.alter_column("knowledge_parse_plan", "interaction_mode", server_default=None)


def downgrade() -> None:
    op.drop_column("knowledge_parse_plan", "pending_question")
    op.drop_column("knowledge_parse_plan", "interaction_mode")
