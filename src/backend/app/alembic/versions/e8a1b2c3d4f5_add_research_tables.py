"""add research tables (folder / session / turn / message)

Revision ID: e8a1b2c3d4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-07-14 15:00:00.000000

自动科研（Research）功能的四张表。相比早期版本，这份合并迁移一次性建齐：

- 完整 FK 约束（含 session ↔ turn 的双向指针 + turn.respond_to_message_id）；
- ``/messages`` 端点热路径的复合索引 (session_id, turn_id, created_time, id)；
- 已知的 3 个「未来给 sweeper 用」的部分索引。

**双向 FK 处理**：``research_session.active_turn_id → research_turn.id`` 与
``research_turn.session_id → research_session.id`` 形成表间循环依赖。Alembic
建表时会先出 session 再出 turn，所以 session 上的 FK 用 ``use_alter=True``
让它在两张表都建完后由独立的 ALTER TABLE 补上。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e8a1b2c3d4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade():
    # ---- research_folder ----
    op.create_table(
        "research_folder",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["research_folder.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "parent_id", "name",
            name="uq_research_folder_user_parent_name",
        ),
    )
    op.create_index(
        op.f("ix_research_folder_user_id"), "research_folder", ["user_id"],
    )
    op.create_index(
        "ix_research_folder_user_parent",
        "research_folder", ["user_id", "parent_id"],
    )

    # ---- research_session ----
    # active_turn_id 的 FK 用 use_alter=True：turn 表还没建，先建 session、
    # 之后用 ALTER TABLE 补 FK；否则 CREATE TABLE 阶段就报「关系不存在」。
    op.create_table(
        "research_session",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "profile", sa.String(length=64), nullable=False,
            server_default="algorithm_design",
        ),
        sa.Column(
            "mode", sa.String(length=32), nullable=False,
            server_default="co-pilot",
        ),
        sa.Column("provider_id", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "running", "paused",
                "completed", "failed", "cancelled",
                name="researchsessionstatus",
                native_enum=False, length=20,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("active_turn_id", sa.Uuid(), nullable=True),
        sa.Column("active_stage", sa.Integer(), nullable=True),
        sa.Column("active_stage_name", sa.String(length=64), nullable=True),
        sa.Column("run_dir", sa.String(length=1024), nullable=True),
        sa.Column(
            "latest_config", sa.JSON(), nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column("llm4ad_workspace", sa.JSON(), nullable=True),
        sa.Column("best_objective", sa.Float(), nullable=True),
        sa.Column("best_code_sha256", sa.String(length=64), nullable=True),
        sa.Column("ended_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["folder_id"], ["research_folder.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["active_turn_id"], ["research_turn.id"],
            name="fk_research_session_active_turn",
            ondelete="SET NULL",
            use_alter=True,  # ← turn 表尚不存在，延后到 ALTER TABLE
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_research_session_user_id"), "research_session", ["user_id"],
    )
    op.create_index(
        "ix_research_session_user_folder",
        "research_session", ["user_id", "folder_id"],
    )
    op.create_index(
        "ix_research_session_user_updated",
        "research_session", ["user_id", "updated_time"],
    )
    # 部分索引：sweeper 找孤儿会话（当前 sweeper 只覆盖 turn；session 层日后再加）
    op.create_index(
        "ix_research_session_alive",
        "research_session", ["updated_time"],
        postgresql_where=sa.text("status in ('pending','running','paused')"),
    )

    # ---- research_turn ----
    # respond_to_message_id 的 FK 也用 use_alter=True（message 表下面才建）。
    op.create_table(
        "research_turn",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "running", "completed", "failed", "cancelled",
                name="researchturnstatus",
                native_enum=False, length=20,
            ),
            nullable=False,
            server_default="running",
        ),
        sa.Column("provider_id", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=True),
        sa.Column("from_stage", sa.String(length=64), nullable=True),
        sa.Column("to_stage", sa.String(length=64), nullable=True),
        sa.Column("user_input", sa.Text(), nullable=True),
        sa.Column("respond_to_message_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["research_session.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["respond_to_message_id"], ["research_message.id"],
            name="fk_research_turn_respond_to_message",
            ondelete="SET NULL",
            use_alter=True,  # ← message 表尚不存在，延后
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_research_turn_session_id"), "research_turn", ["session_id"],
    )
    op.create_index(
        "ix_research_turn_session_created",
        "research_turn", ["session_id", "created_time"],
    )
    # 部分索引：worker 启动时 sweep_orphan_running_turns 用
    op.create_index(
        "ix_research_turn_alive",
        "research_turn", ["updated_time"],
        postgresql_where=sa.text("status = 'running'"),
    )

    # ---- research_message ----
    op.create_table(
        "research_message",
        sa.Column("created_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "user", "assistant", "system",
                name="researchmessagerole",
                native_enum=False, length=20,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "turn_status",
            sa.Enum(
                "running", "completed", "failed", "cancelled",
                name="researchturnstatus",
                native_enum=False, length=20,
                create_type=False,
            ),
            nullable=False,
            server_default="completed",
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "payload_locked", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "payload_locked_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column("payload_submission", sa.JSON(), nullable=True),
        sa.Column("stage", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=True),
        sa.Column("event_key", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(
            ["session_id"], ["research_session.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"], ["research_turn.id"],
            name="fk_research_message_turn",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        # (role, event_key) 二元幂等键：重放事件不会产生重复行
        sa.UniqueConstraint(
            "session_id", "turn_id", "role", "event_key",
            name="uq_research_message_turn_role_event",
        ),
    )
    op.create_index(
        op.f("ix_research_message_session_id"),
        "research_message", ["session_id"],
    )
    op.create_index(
        op.f("ix_research_message_turn_id"),
        "research_message", ["turn_id"],
    )
    # /messages 端点热路径：WHERE session_id=? AND turn_id=?
    # [AND created_time > cursor] ORDER BY created_time, id
    op.create_index(
        "ix_research_message_session_turn_created",
        "research_message",
        ["session_id", "turn_id", "created_time", "id"],
    )
    op.create_index(
        "ix_research_message_session_created",
        "research_message", ["session_id", "created_time"],
    )
    # 部分索引：sweeper 找生成中长时间无更新的 assistant 消息
    op.create_index(
        "ix_research_message_generating",
        "research_message", ["updated_time"],
        postgresql_where=sa.text("turn_status = 'running'"),
    )


def downgrade():
    op.drop_index(
        "ix_research_message_generating", table_name="research_message"
    )
    op.drop_index(
        "ix_research_message_session_created", table_name="research_message"
    )
    op.drop_index(
        "ix_research_message_session_turn_created",
        table_name="research_message",
    )
    op.drop_index(
        op.f("ix_research_message_turn_id"), table_name="research_message"
    )
    op.drop_index(
        op.f("ix_research_message_session_id"), table_name="research_message"
    )
    op.drop_table("research_message")

    op.drop_index("ix_research_turn_alive", table_name="research_turn")
    op.drop_index(
        "ix_research_turn_session_created", table_name="research_turn"
    )
    op.drop_index(
        op.f("ix_research_turn_session_id"), table_name="research_turn"
    )
    op.drop_table("research_turn")

    op.drop_index(
        "ix_research_session_alive", table_name="research_session"
    )
    op.drop_index(
        "ix_research_session_user_updated", table_name="research_session"
    )
    op.drop_index(
        "ix_research_session_user_folder", table_name="research_session"
    )
    op.drop_index(
        op.f("ix_research_session_user_id"), table_name="research_session"
    )
    op.drop_table("research_session")

    op.drop_index(
        "ix_research_folder_user_parent", table_name="research_folder"
    )
    op.drop_index(
        op.f("ix_research_folder_user_id"), table_name="research_folder"
    )
    op.drop_table("research_folder")
