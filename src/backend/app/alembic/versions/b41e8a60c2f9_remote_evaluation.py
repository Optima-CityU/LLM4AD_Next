"""Add durable remote evaluation gateways and jobs."""

from alembic import op
import sqlalchemy as sa

revision = "b41e8a60c2f9"
down_revision = "9c27b6a4d190"
branch_labels = None
depends_on = None


def upgrade():
    """Create additive gateway and job metadata tables."""
    op.create_table(
        "evaluation_gateway",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("allowed_user_ids", sa.JSON(), nullable=False),
        sa.Column("contract", sa.JSON(), nullable=False),
        sa.Column("contract_key", sa.String(64), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evaluation_gateway_contract_key", "evaluation_gateway", ["contract_key"])
    op.create_table(
        "remote_evaluation_job",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("task.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("gateway_id", sa.Uuid(), sa.ForeignKey("evaluation_gateway.id")),
        sa.Column("selected_gateway_ids", sa.JSON(), nullable=False),
        sa.Column("contract_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("task_id", "user_id", "gateway_id", "contract_key", "state"):
        op.create_index(f"ix_remote_evaluation_job_{column}", "remote_evaluation_job", [column])


def downgrade():
    """Remove only remote-evaluation metadata."""
    op.drop_table("remote_evaluation_job")
    op.drop_table("evaluation_gateway")
