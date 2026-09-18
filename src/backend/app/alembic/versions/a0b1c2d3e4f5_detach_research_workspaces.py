"""Detach research workspaces from normal projects.

Revision ID: a0b1c2d3e4f5
Revises: z9a0b1c2d3e4
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a0b1c2d3e4f5"
down_revision: str | None = "z9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove research-only project shells while preserving real task projects."""
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("paper_workspace")}
    if "project_id" not in columns:
        return
    op.execute(sa.text("""
            WITH linked_projects AS (
                SELECT DISTINCT project_id
                FROM paper_workspace
                WHERE project_id IS NOT NULL
            ), detached AS (
                UPDATE paper_workspace
                SET project_id = NULL
                WHERE project_id IS NOT NULL
                RETURNING id
            )
            DELETE FROM project
            WHERE id IN (SELECT project_id FROM linked_projects)
              AND NOT EXISTS (
                  SELECT 1 FROM task WHERE task.project_id = project.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM project_memory_config
                  WHERE project_memory_config.project_id = project.id
              )
            """))
    indexes = {index["name"] for index in inspector.get_indexes("paper_workspace")}
    if "ix_paper_workspace_project_id" in indexes:
        op.drop_index("ix_paper_workspace_project_id", table_name="paper_workspace")
    for foreign_key in inspector.get_foreign_keys("paper_workspace"):
        if foreign_key.get("constrained_columns") == ["project_id"]:
            op.drop_constraint(
                foreign_key["name"],
                "paper_workspace",
                type_="foreignkey",
            )
            break
    op.drop_column("paper_workspace", "project_id")


def downgrade() -> None:
    """Restore the nullable legacy link without recreating removed shells."""
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("paper_workspace")}
    if "project_id" in columns:
        return
    op.add_column(
        "paper_workspace",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_paper_workspace_project_id",
        "paper_workspace",
        "project",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_paper_workspace_project_id",
        "paper_workspace",
        ["project_id"],
    )
