"""add metric_direction to research_session

Revision ID: u4b5c6d7e8f9
Revises: t3a4b5c6d7e8
Create Date: 2026-09-07 00:00:00.000000

为 research_session 表增加 metric_direction 列，用于告知 ARC pipeline 指标
优化方向（maximize / minimize）。默认值 'maximize' 与 researchclaw 内置默认
一致，已有行升级后行为不变。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "u4b5c6d7e8f9"
down_revision = "t3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "research_session",
        sa.Column(
            "metric_direction",
            sa.Text(),
            nullable=False,
            server_default="maximize",
        ),
    )


def downgrade():
    op.drop_column("research_session", "metric_direction")
