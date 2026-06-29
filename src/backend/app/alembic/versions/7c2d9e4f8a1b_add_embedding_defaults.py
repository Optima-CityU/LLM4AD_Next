"""add embedding provider settings

Revision ID: 7c2d9e4f8a1b
Revises: 43a838dff679, d4e5f6a7b8c9
Create Date: 2026-06-27 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7c2d9e4f8a1b"
down_revision = ("43a838dff679", "d4e5f6a7b8c9")
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_default_model",
        sa.Column("embedding_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.alter_column("user_default_model", "embedding_enabled", server_default=None)
    op.create_table(
        "embeddingprovider",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=17), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("auth_token", sa.Text(), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("mode", sa.String(length=6), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("timeout", sa.Float(), nullable=False),
        sa.Column("embedding_func_max_async", sa.Integer(), nullable=False),
        sa.Column("text_type", sa.String(length=17), nullable=False),
        sa.Column("text_base_url", sa.String(length=512), nullable=True),
        sa.Column("text_api_key", sa.Text(), nullable=False),
        sa.Column("text_auth_token", sa.Text(), nullable=False),
        sa.Column("text_model", sa.String(length=255), nullable=False),
        sa.Column("text_task", sa.String(length=64), nullable=False),
        sa.Column("code_type", sa.String(length=17), nullable=False),
        sa.Column("code_base_url", sa.String(length=512), nullable=True),
        sa.Column("code_api_key", sa.Text(), nullable=False),
        sa.Column("code_auth_token", sa.Text(), nullable=False),
        sa.Column("code_model", sa.String(length=255), nullable=False),
        sa.Column("code_task", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_embeddingprovider_user_id"), "embeddingprovider", ["user_id"], unique=False)
    op.add_column("user_default_model", sa.Column("embedding_provider_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "user_default_model_embedding_provider_id_fkey",
        "user_default_model",
        "embeddingprovider",
        ["embedding_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute("ALTER TABLE llmprovider DROP COLUMN IF EXISTS embedding_model")
    op.execute("ALTER TABLE llmprovider DROP COLUMN IF EXISTS embedding_dim")
    op.execute(
        """
        ALTER TABLE user_default_model
        DROP CONSTRAINT IF EXISTS user_default_model_text_embedding_provider_id_fkey
        """
    )
    op.execute(
        """
        ALTER TABLE user_default_model
        DROP CONSTRAINT IF EXISTS user_default_model_code_embedding_provider_id_fkey
        """
    )
    op.execute("ALTER TABLE user_default_model DROP COLUMN IF EXISTS text_embedding_provider_id")
    op.execute("ALTER TABLE user_default_model DROP COLUMN IF EXISTS text_embedding_model_name")
    op.execute("ALTER TABLE user_default_model DROP COLUMN IF EXISTS code_embedding_provider_id")
    op.execute("ALTER TABLE user_default_model DROP COLUMN IF EXISTS code_embedding_model_name")


def downgrade():
    op.drop_constraint(
        "user_default_model_embedding_provider_id_fkey",
        "user_default_model",
        type_="foreignkey",
    )
    op.drop_column("user_default_model", "embedding_provider_id")
    op.drop_index(op.f("ix_embeddingprovider_user_id"), table_name="embeddingprovider")
    op.drop_table("embeddingprovider")
    op.drop_column("user_default_model", "embedding_enabled")
