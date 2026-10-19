"""add_arl_fields_to_research_session

Revision ID: 237e606ea3ac
Revises: f5a6b7c8d9e0
Create Date: 2026-10-19 15:30:28.761869

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '237e606ea3ac'
down_revision = 'f5a6b7c8d9e0'
branch_labels = None
depends_on = None


def upgrade():
    # MANUALLY CLEANED: Only add ARL fields, removed all other auto-generated changes
    # to avoid accidentally dropping tables or modifying indexes in production.
    op.add_column('research_session', sa.Column('session_type', sa.Text(), server_default='research', nullable=False))
    op.add_column('research_session', sa.Column('arl_config', sa.JSON(), nullable=True))


def downgrade():
    # MANUALLY CLEANED: Only remove ARL fields
    op.drop_column('research_session', 'arl_config')
    op.drop_column('research_session', 'session_type')
