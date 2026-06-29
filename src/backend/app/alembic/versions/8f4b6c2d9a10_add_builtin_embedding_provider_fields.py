"""add builtin embedding provider fields

Revision ID: 8f4b6c2d9a10
Revises: 7c2d9e4f8a1b
Create Date: 2026-06-29 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8f4b6c2d9a10"
down_revision = "7c2d9e4f8a1b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "embeddingprovider",
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "embeddingprovider",
        sa.Column("visible_to_all", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.alter_column("embeddingprovider", "is_builtin", server_default=None)
    op.alter_column("embeddingprovider", "visible_to_all", server_default=None)
    op.alter_column("embeddingprovider", "user_id", nullable=True)
    op.create_index(
        "uq_embedding_provider_builtin_name",
        "embeddingprovider",
        ["name"],
        unique=True,
        postgresql_where=sa.text("is_builtin = true"),
    )


def downgrade():
    op.drop_index("uq_embedding_provider_builtin_name", table_name="embeddingprovider")
    op.alter_column("embeddingprovider", "user_id", nullable=False)
    op.drop_column("embeddingprovider", "visible_to_all")
    op.drop_column("embeddingprovider", "is_builtin")
