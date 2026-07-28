"""add seq field to research_message and research_log

Revision ID: h1a2b3c4d5e6
Revises: g0a1b2c3d4e5
Create Date: 2026-07-28 17:00:00.000000

添加 seq 字段（per-turn 递增序列号）到 research_message 和 research_log 表，
用于严格保证事件顺序。解决同一微秒内多个事件（如 stage 快速切换）的排序问题。

排序策略从 `ORDER BY created_time, id` 改为 `ORDER BY created_time, seq`。
seq 由 ResearchEventSink._seq 分配，线程安全单调递增。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "h1a2b3c4d5e6"
down_revision = "g0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    # 给 research_message 添加 seq 字段
    op.add_column(
        "research_message",
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
    )

    # 给 research_log 添加 seq 字段
    op.add_column(
        "research_log",
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
    )

    # 更新 research_message 的索引，用 seq 替换 id
    op.drop_index("ix_research_message_session_turn_created", table_name="research_message")
    op.create_index(
        "ix_research_message_session_turn_created_seq",
        "research_message",
        ["session_id", "turn_id", "created_time", "seq"],
    )

    op.drop_index("ix_research_message_session_created", table_name="research_message")
    op.create_index(
        "ix_research_message_session_created_seq",
        "research_message",
        ["session_id", "created_time", "seq"],
    )

    # 更新 research_log 的索引，用 seq 替换 id
    op.drop_index("ix_research_log_session_turn_time", table_name="research_log")
    op.create_index(
        "ix_research_log_session_turn_time_seq",
        "research_log",
        ["session_id", "turn_id", "created_time", "seq"],
    )

    op.drop_index("ix_research_log_session_time", table_name="research_log")
    op.create_index(
        "ix_research_log_session_time_seq",
        "research_log",
        ["session_id", "created_time", "seq"],
    )


def downgrade():
    # 恢复 research_log 的旧索引
    op.drop_index("ix_research_log_session_time_seq", table_name="research_log")
    op.create_index(
        "ix_research_log_session_time",
        "research_log",
        ["session_id", "created_time", "id"],
    )

    op.drop_index("ix_research_log_session_turn_time_seq", table_name="research_log")
    op.create_index(
        "ix_research_log_session_turn_time",
        "research_log",
        ["session_id", "turn_id", "created_time", "id"],
    )

    # 恢复 research_message 的旧索引
    op.drop_index("ix_research_message_session_created_seq", table_name="research_message")
    op.create_index(
        "ix_research_message_session_created",
        "research_message",
        ["session_id", "created_time"],
    )

    op.drop_index("ix_research_message_session_turn_created_seq", table_name="research_message")
    op.create_index(
        "ix_research_message_session_turn_created",
        "research_message",
        ["session_id", "turn_id", "created_time", "id"],
    )

    # 删除 seq 字段
    op.drop_column("research_log", "seq")
    op.drop_column("research_message", "seq")
