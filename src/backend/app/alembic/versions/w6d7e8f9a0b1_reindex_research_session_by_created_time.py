"""reindex research_session by created_time

Revision ID: w6d7e8f9a0b1
Revises: v5c6d7e8f9a0
Create Date: 2026-09-10 00:00:00.000000

The session list now sorts by ``created_time DESC, id DESC`` instead of
``updated_time DESC, id DESC``. ``updated_time`` was rewritten by writes that
have nothing to do with "the user is working on this session" (background stage
progress, config snapshots, folder moves, empty PATCH bodies), so list positions
kept shifting. ``created_time`` is immutable, which makes the position stable.

Replace the ``(user_id, updated_time)`` index with ``(user_id, created_time)``
to match the new ordering key. The old index is dropped rather than kept: it was
only ever there to serve this list query.

No data migration is needed -- ``created_time`` already exists and is populated
for every row.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "w6d7e8f9a0b1"
down_revision = "v5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_research_session_user_created",
        "research_session",
        ["user_id", "created_time"],
    )
    op.drop_index(
        "ix_research_session_user_updated", table_name="research_session"
    )


def downgrade():
    op.create_index(
        "ix_research_session_user_updated",
        "research_session",
        ["user_id", "updated_time"],
    )
    op.drop_index(
        "ix_research_session_user_created", table_name="research_session"
    )
