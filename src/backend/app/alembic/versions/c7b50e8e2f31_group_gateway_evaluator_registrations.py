"""Group evaluator registrations under their connected gateway instance."""

import sqlalchemy as sa
from alembic import op

revision = "c7b50e8e2f31"
down_revision = "c62f8a901de3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow gateways to advertise a stable instance and display name."""
    op.add_column("evaluation_gateway", sa.Column("instance_id", sa.Uuid(), nullable=True))
    op.add_column("evaluation_gateway", sa.Column("instance_name", sa.String(255), nullable=True))


def downgrade() -> None:
    """Remove optional grouping without changing evaluator registrations."""
    op.drop_column("evaluation_gateway", "instance_name")
    op.drop_column("evaluation_gateway", "instance_id")
