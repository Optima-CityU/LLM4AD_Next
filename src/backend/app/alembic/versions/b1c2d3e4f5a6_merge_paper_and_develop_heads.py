"""Merge the paper workspace and develop migration heads.

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5, w6d7e8f9a0b1
Create Date: 2026-09-18
"""

from collections.abc import Sequence

revision: str = "b1c2d3e4f5a6"
down_revision: tuple[str, str] = ("a0b1c2d3e4f5", "w6d7e8f9a0b1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge the migration branches without changing the schema."""


def downgrade() -> None:
    """Split the migration branches without changing the schema."""
