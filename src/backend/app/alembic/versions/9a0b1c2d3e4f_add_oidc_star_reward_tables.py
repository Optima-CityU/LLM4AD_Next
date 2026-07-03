"""add oidc and star reward tables

Revision ID: 9a0b1c2d3e4f
Revises: 7c2d9e4f8a1b
Create Date: 2026-07-02 18:26:00.000000

"""

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op


revision = "9a0b1c2d3e4f"
down_revision = "7c2d9e4f8a1b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_oidc_account",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("provider_subject", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("provider_username", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("provider_email", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("token_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("scope", sqlmodel.sql.sqltypes.AutoString(length=512), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_user_oidc_provider_subject"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_oidc_user_provider"),
    )
    op.create_index(op.f("ix_user_oidc_account_provider"), "user_oidc_account", ["provider"], unique=False)
    op.create_index(
        op.f("ix_user_oidc_account_provider_subject"),
        "user_oidc_account",
        ["provider_subject"],
        unique=False,
    )
    op.create_index(op.f("ix_user_oidc_account_user_id"), "user_oidc_account", ["user_id"], unique=False)

    op.create_table(
        "user_star_reward",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("repo", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("github_user_id", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("starred_at_checked", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reward_amount", sa.Float(), nullable=False),
        sa.Column("reward_status", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sqlmodel.sql.sqltypes.AutoString(length=512), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "repo", name="uq_user_star_reward_user_repo"),
    )
    op.create_index(op.f("ix_user_star_reward_repo"), "user_star_reward", ["repo"], unique=False)
    op.create_index(op.f("ix_user_star_reward_user_id"), "user_star_reward", ["user_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_user_star_reward_user_id"), table_name="user_star_reward")
    op.drop_index(op.f("ix_user_star_reward_repo"), table_name="user_star_reward")
    op.drop_table("user_star_reward")
    op.drop_index(op.f("ix_user_oidc_account_user_id"), table_name="user_oidc_account")
    op.drop_index(op.f("ix_user_oidc_account_provider_subject"), table_name="user_oidc_account")
    op.drop_index(op.f("ix_user_oidc_account_provider"), table_name="user_oidc_account")
    op.drop_table("user_oidc_account")
