"""Add leased device-pool dispatch while preserving existing job semantics."""

import sqlalchemy as sa
from alembic import op

revision = "c62f8a901de3"
down_revision = "b41e8a60c2f9"
branch_labels = None
depends_on = None


def upgrade():
    """Add protocol negotiation and attempt accounting with legacy defaults."""
    op.add_column("evaluation_gateway", sa.Column("protocol_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("evaluation_gateway", sa.Column("ready", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("remote_evaluation_job", sa.Column("selection_mode", sa.String(16), nullable=False, server_default="selected"))
    op.add_column("remote_evaluation_job", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("remote_evaluation_job", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("remote_evaluation_job", sa.Column("attempt_history", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("remote_evaluation_job", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.create_index("ix_remote_evaluation_job_lease_expires_at", "remote_evaluation_job", ["lease_expires_at"])


def downgrade():
    """Remove only the additive pool scheduling metadata."""
    op.drop_index("ix_remote_evaluation_job_lease_expires_at", table_name="remote_evaluation_job")
    for column in ("lease_expires_at", "attempt_history", "attempt_count", "max_attempts", "selection_mode"):
        op.drop_column("remote_evaluation_job", column)
    op.drop_column("evaluation_gateway", "ready")
    op.drop_column("evaluation_gateway", "protocol_version")
