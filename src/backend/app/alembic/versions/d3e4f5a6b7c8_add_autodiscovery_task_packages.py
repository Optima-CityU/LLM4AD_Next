"""Add validated AutoDiscovery task-package metadata.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the package location, manifest, and Agent validation result."""
    op.add_column(
        "paper_algorithm_proposal",
        sa.Column("package_object_prefix", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "paper_algorithm_proposal",
        sa.Column(
            "package_manifest",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "paper_algorithm_proposal",
        sa.Column(
            "validation_report",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove validated AutoDiscovery task-package metadata."""
    op.drop_column("paper_algorithm_proposal", "validation_report")
    op.drop_column("paper_algorithm_proposal", "package_manifest")
    op.drop_column("paper_algorithm_proposal", "package_object_prefix")
