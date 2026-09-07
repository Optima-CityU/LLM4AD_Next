"""add visible knowledge extraction plans

Revision ID: k4d5e6f7a8b9
Revises: j3c4d5e6f7a8
Create Date: 2026-08-11 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "k4d5e6f7a8b9"
down_revision = "j3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_parse_plan",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("parser_provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parser_provider_name", sa.String(length=255), nullable=True),
        sa.Column("parser_model", sa.String(length=255), nullable=True),
        sa.Column("plan_object_key", sa.String(length=1024), nullable=True),
        sa.Column("container_id", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_source.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_parse_plan_source_id", "knowledge_parse_plan", ["source_id"])
    op.create_index(
        "ix_knowledge_parse_plan_source_created",
        "knowledge_parse_plan",
        ["source_id", "created_time"],
    )
    op.add_column(
        "knowledge_parse_run",
        sa.Column("parse_mode", sa.String(length=16), nullable=False, server_default="direct"),
    )
    op.add_column(
        "knowledge_parse_run",
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "knowledge_parse_run",
        sa.Column("plan_strategy_id", sa.String(length=128), nullable=True),
    )
    op.create_foreign_key(
        "fk_knowledge_parse_run_plan_id",
        "knowledge_parse_run",
        "knowledge_parse_plan",
        ["plan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_knowledge_parse_run_plan_id", "knowledge_parse_run", ["plan_id"])
    op.alter_column("knowledge_parse_run", "parse_mode", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_knowledge_parse_run_plan_id", table_name="knowledge_parse_run")
    op.drop_constraint("fk_knowledge_parse_run_plan_id", "knowledge_parse_run", type_="foreignkey")
    op.drop_column("knowledge_parse_run", "plan_strategy_id")
    op.drop_column("knowledge_parse_run", "plan_id")
    op.drop_column("knowledge_parse_run", "parse_mode")
    op.drop_table("knowledge_parse_plan")
