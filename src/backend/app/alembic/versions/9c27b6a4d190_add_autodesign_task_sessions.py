"""Add AutoDesign task-session associations.

Revision ID: 9c27b6a4d190
Revises: 237e606ea3ac
"""

import sqlalchemy as sa
from alembic import op

revision = "9c27b6a4d190"
down_revision = "237e606ea3ac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store session associations without moving existing task or paper data."""
    op.create_table(
        "autodesign_session",
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("paper_workspace.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("project.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("task.id", ondelete="SET NULL"), nullable=True),
        sa.Column("package_object_key", sa.String(1024), nullable=True),
        sa.Column("package_sha256", sa.String(64), nullable=True),
        sa.Column("package_name", sa.String(255), nullable=True),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("published_package_sha256", sa.String(64), nullable=True),
        sa.Column("published_task_id", sa.Uuid(), sa.ForeignKey("task.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_autodesign_session_project_id", "autodesign_session", ["project_id"])
    op.create_index("ix_autodesign_session_task_id", "autodesign_session", ["task_id"], unique=True)


def downgrade() -> None:
    """Remove only the additive AutoDesign metadata table."""
    op.drop_index("ix_autodesign_session_task_id", table_name="autodesign_session")
    op.drop_index("ix_autodesign_session_project_id", table_name="autodesign_session")
    op.drop_table("autodesign_session")
