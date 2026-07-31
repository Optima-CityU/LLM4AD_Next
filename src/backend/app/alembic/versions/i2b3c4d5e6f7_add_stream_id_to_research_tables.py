"""add stream_id field to research_message and research_log

Revision ID: i2b3c4d5e6f7
Revises: h1a2b3c4d5e6
Create Date: 2026-07-31 10:00:00.000000

添加 stream_id 字段（Redis Stream entry id，形如 "<ms>-<seq>"）到 research_message
和 research_log 表。该 id 由 push_research_event 的 XADD 返回、SSE 帧 id: 行携带，
前端刷新后据此从「已拉取历史的末端」精确续传 SSE（免全量重放），并作精确去重键
（尤其修 retry 复用 turn_id 时 event_key="<type>:<seq>" per-turn 计数器归零导致的撞键）。

可空列，旧数据 / push 失败为 NULL，无需 server_default。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "i2b3c4d5e6f7"
down_revision = "h1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "research_message",
        sa.Column("stream_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "research_log",
        sa.Column("stream_id", sa.String(length=64), nullable=True),
    )


def downgrade():
    op.drop_column("research_log", "stream_id")
    op.drop_column("research_message", "stream_id")
