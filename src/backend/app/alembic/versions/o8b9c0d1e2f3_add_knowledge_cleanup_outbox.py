"""add durable knowledge cleanup outbox

Revision ID: o8b9c0d1e2f3
Revises: n7a8b9c0d1e2
Create Date: 2026-08-12 20:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "o8b9c0d1e2f3"
down_revision = "n7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_cleanup_job",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # No user FK: this row must survive the account deletion it cleans up.
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_cleanup_job_user_id", "knowledge_cleanup_job", ["user_id"])
    op.create_index("ix_knowledge_cleanup_job_status", "knowledge_cleanup_job", ["status"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_cleanup_job_status", table_name="knowledge_cleanup_job")
    op.drop_index("ix_knowledge_cleanup_job_user_id", table_name="knowledge_cleanup_job")
    op.drop_table("knowledge_cleanup_job")
