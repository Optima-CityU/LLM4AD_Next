"""add memory config tables

Revision ID: e8f9a0b1c2d3
Revises: 9a0b1c2d3e4f
Create Date: 2026-07-06 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e8f9a0b1c2d3"
down_revision = "9a0b1c2d3e4f"
branch_labels = None
depends_on = None


def _memory_columns() -> list[sa.Column]:
    return [
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("include_user_memory", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("include_project_memory", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("include_task_memory", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("user_memory_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("project_memory_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("task_memory_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("mindmemos_search_strategy", sa.String(length=32), nullable=False, server_default="fast"),
        sa.Column("mindmemos_rerank", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("mindmemos_score_threshold", sa.Float(), nullable=True, server_default="0.65"),
        sa.Column("mindmemos_fail_open", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("mindmemos_binding_id", sa.String(length=128), nullable=True),
        sa.Column("mindmemos_chat_provider_id", sa.Uuid(), nullable=True),
        sa.Column("mindmemos_chat_model", sa.String(length=255), nullable=True),
        sa.Column("mindmemos_embedding_provider_id", sa.Uuid(), nullable=True),
        sa.Column("mindmemos_embedding_model", sa.String(length=255), nullable=True),
        sa.Column("mindmemos_embedding_dim", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "user_memory_config",
        *_memory_columns(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_user_memory_config_user_id"), "user_memory_config", ["user_id"], unique=True)

    op.create_table(
        "project_memory_config",
        *_memory_columns(),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index(
        op.f("ix_project_memory_config_project_id"),
        "project_memory_config",
        ["project_id"],
        unique=True,
    )


def downgrade():
    op.drop_index(op.f("ix_project_memory_config_project_id"), table_name="project_memory_config")
    op.drop_table("project_memory_config")
    op.drop_index(op.f("ix_user_memory_config_user_id"), table_name="user_memory_config")
    op.drop_table("user_memory_config")
