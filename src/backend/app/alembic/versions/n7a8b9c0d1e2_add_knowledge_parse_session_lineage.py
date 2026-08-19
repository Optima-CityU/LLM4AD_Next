"""add knowledge parser session lineage

Revision ID: n7a8b9c0d1e2
Revises: m6f7a8b9c0d1
Create Date: 2026-08-12 17:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "n7a8b9c0d1e2"
down_revision = "m6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_parse_run", sa.Column("parent_run_id", sa.Uuid(), nullable=True))
    op.add_column(
        "knowledge_parse_run",
        sa.Column("session_owner_kind", sa.String(length=8), nullable=False, server_default="run"),
    )
    op.add_column("knowledge_parse_run", sa.Column("session_owner_id", sa.Uuid(), nullable=True))
    op.create_index("ix_knowledge_parse_run_parent_run_id", "knowledge_parse_run", ["parent_run_id"])
    op.create_index("ix_knowledge_parse_run_session_owner_id", "knowledge_parse_run", ["session_owner_id"])
    op.create_foreign_key(
        "fk_knowledge_parse_run_parent_run_id",
        "knowledge_parse_run",
        "knowledge_parse_run",
        ["parent_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.alter_column("knowledge_parse_run", "session_owner_kind", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "fk_knowledge_parse_run_parent_run_id",
        "knowledge_parse_run",
        type_="foreignkey",
    )
    op.drop_index("ix_knowledge_parse_run_session_owner_id", table_name="knowledge_parse_run")
    op.drop_index("ix_knowledge_parse_run_parent_run_id", table_name="knowledge_parse_run")
    op.drop_column("knowledge_parse_run", "session_owner_id")
    op.drop_column("knowledge_parse_run", "session_owner_kind")
    op.drop_column("knowledge_parse_run", "parent_run_id")
