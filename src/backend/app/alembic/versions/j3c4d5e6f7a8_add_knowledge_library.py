"""add independent multi-document knowledge library

Revision ID: j3c4d5e6f7a8
Revises: i2b3c4d5e6f7
Create Date: 2026-08-10 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "j3c4d5e6f7a8"
down_revision = "i2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_parser_binding",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["llmprovider.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_knowledge_parser_binding_user_id",
        "knowledge_parser_binding",
        ["user_id"],
        unique=True,
    )
    op.create_index(
        "ix_knowledge_parser_binding_provider_id",
        "knowledge_parser_binding",
        ["provider_id"],
    )

    op.create_table(
        "knowledge_source",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("parse_status", sa.String(length=24), nullable=False),
        sa.Column("active_parse_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_source_user_id", "knowledge_source", ["user_id"])
    op.create_index(
        "ix_knowledge_source_user_updated",
        "knowledge_source",
        ["user_id", "updated_time"],
    )
    op.create_index(
        "ix_knowledge_source_active_parse_run_id",
        "knowledge_source",
        ["active_parse_run_id"],
    )

    op.create_table(
        "knowledge_source_file",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_size", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["knowledge_source.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "original_filename",
            name="uq_knowledge_source_file_name",
        ),
    )
    op.create_index(
        "ix_knowledge_source_file_source_id",
        "knowledge_source_file",
        ["source_id"],
    )
    op.create_index(
        "ix_knowledge_source_file_source_order",
        "knowledge_source_file",
        ["source_id", "sort_order"],
    )

    op.create_table(
        "knowledge_parse_run",
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
        sa.Column("parser_name", sa.String(length=64), nullable=False),
        sa.Column("parser_provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parser_provider_name", sa.String(length=255), nullable=True),
        sa.Column("parser_model", sa.String(length=255), nullable=True),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column("skill_version", sa.String(length=64), nullable=False),
        sa.Column("manifest_object_key", sa.String(length=1024), nullable=True),
        sa.Column("container_id", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"], ["knowledge_source.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_parse_run_source_id", "knowledge_parse_run", ["source_id"]
    )
    op.create_index(
        "ix_knowledge_parse_run_source_created",
        "knowledge_parse_run",
        ["source_id", "created_time"],
    )

    op.create_table(
        "knowledge_document",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parse_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_size", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("user_modified", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["knowledge_source.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parse_run_id"], ["knowledge_parse_run.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["knowledge_document.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "parse_run_id", "sort_order", name="uq_knowledge_document_run_order"
        ),
    )
    op.create_index(
        "ix_knowledge_document_source_id", "knowledge_document", ["source_id"]
    )
    op.create_index(
        "ix_knowledge_document_parse_run_id",
        "knowledge_document",
        ["parse_run_id"],
    )
    op.create_index(
        "ix_knowledge_document_source_order",
        "knowledge_document",
        ["source_id", "sort_order"],
    )


def downgrade() -> None:
    op.drop_table("knowledge_document")
    op.drop_table("knowledge_parse_run")
    op.drop_table("knowledge_source_file")
    op.drop_table("knowledge_source")
    op.drop_table("knowledge_parser_binding")
