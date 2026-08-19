"""add independent knowledge parser token limits

Revision ID: m6f7a8b9c0d1
Revises: l5e6f7a8b9c0
Create Date: 2026-08-12 13:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "m6f7a8b9c0d1"
down_revision = "l5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_parser_binding",
        sa.Column(
            "context_window_tokens",
            sa.Integer(),
            nullable=False,
            server_default="128000",
        ),
    )
    op.add_column(
        "knowledge_parser_binding",
        sa.Column(
            "max_output_tokens",
            sa.Integer(),
            nullable=False,
            server_default="16384",
        ),
    )
    op.alter_column("knowledge_parser_binding", "context_window_tokens", server_default=None)
    op.alter_column("knowledge_parser_binding", "max_output_tokens", server_default=None)


def downgrade() -> None:
    op.drop_column("knowledge_parser_binding", "max_output_tokens")
    op.drop_column("knowledge_parser_binding", "context_window_tokens")
