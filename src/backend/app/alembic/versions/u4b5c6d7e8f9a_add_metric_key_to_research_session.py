"""add metric_key + default-empty metric_direction to research_session

Revision ID: u4b5c6d7e8f9a
Revises: u4b5c6d7e8f9
Create Date: 2026-09-09 00:00:00.000000

1. 把 metric_direction 的 server_default 从 'maximize' 改为 ''（空串表示未指定，
   不再强设默认值）；保留列值，把历史 'maximize' 值归一为空串，统一「未指定」语义。
2. 新增 metric_key 列（server_default 为空串），供用户经接口配置 ARC 指标 key；
   空串由 config_builder 回落 ARC 默认 "primary_metric"。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "u4b5c6d7e8f9a"
down_revision = "u4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade():
    # 归一：旧默认 'maximize' 只在未显式指定时由 DB 写入，现在统一为空串语义。
    op.execute(
        "UPDATE research_session SET metric_direction = '' "
        "WHERE metric_direction = 'maximize'"
    )
    op.alter_column(
        "research_session",
        "metric_direction",
        existing_type=sa.Text(),
        server_default="",
    )
    op.add_column(
        "research_session",
        sa.Column(
            "metric_key",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )


def downgrade():
    op.drop_column("research_session", "metric_key")
    op.alter_column(
        "research_session",
        "metric_direction",
        existing_type=sa.Text(),
        server_default="maximize",
    )
