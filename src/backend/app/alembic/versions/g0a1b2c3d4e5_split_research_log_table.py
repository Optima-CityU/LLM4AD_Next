"""split research_log table

Revision ID: g0a1b2c3d4e5
Revises: f2a3b4c5d6e7
Create Date: 2026-07-28 16:00:00.000000

将 research_message 表中的日志类型消息拆分到独立的 research_log 表。

日志约占总消息的 90-95%，拆分后可显著提升查询性能：
- research_message：用户/助手对话、阶段转换、进化事件等结构化消息
- research_log：容器/ARC/bridge 日志输出

**注意**：本迁移只创建新表，不迁移历史数据。测试环境可忽略历史 log 数据。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "g0a1b2c3d4e5"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    # ---- research_log ----
    op.create_table(
        "research_log",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("module", sa.String(length=128), nullable=True),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("turn_status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["research_session.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"], ["research_turn.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 索引：查询热路径
    op.create_index(
        "ix_research_log_session_turn_time",
        "research_log",
        ["session_id", "turn_id", "created_time", "id"],
    )
    op.create_index(
        "ix_research_log_session_time",
        "research_log",
        ["session_id", "created_time", "id"],
    )
    op.create_index(
        op.f("ix_research_log_turn_id"),
        "research_log",
        ["turn_id"],
    )


def downgrade():
    op.drop_index(op.f("ix_research_log_turn_id"), table_name="research_log")
    op.drop_index("ix_research_log_session_time", table_name="research_log")
    op.drop_index("ix_research_log_session_turn_time", table_name="research_log")
    op.drop_table("research_log")
