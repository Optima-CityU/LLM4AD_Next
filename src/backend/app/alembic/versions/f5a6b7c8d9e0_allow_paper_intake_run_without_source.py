"""Allow an AutoDiscovery intake session before a paper is imported.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Permit a native baseline run to exist during arXiv intake."""
    op.alter_column("paper_agent_run", "source_version_id", nullable=True)


def downgrade() -> None:
    """Restore the original required source after removing empty-source runs."""
    op.execute("DELETE FROM paper_agent_run WHERE source_version_id IS NULL")
    op.alter_column("paper_agent_run", "source_version_id", nullable=False)
