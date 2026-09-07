"""add analysis_report column to research_session

Revision ID: f2a3b4c5d6e7
Revises: e8a1b2c3d4f5
Create Date: 2026-07-22 10:30:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'f2a3b4c5d6e7'
down_revision = 'e8a1b2c3d4f5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'research_session',
        sa.Column('analysis_report', sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column('research_session', 'analysis_report')
