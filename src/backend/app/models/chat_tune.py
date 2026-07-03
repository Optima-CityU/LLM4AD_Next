"""Chat 调参数据模型。

定义调参会话与对话消息两张核心表。会话与任务严格 1:1 绑定，
``task_id`` 作为唯一外键；每一轮用户提问 + 助手回复共享同一个 ``turn_id``，
前端可据此订阅对应的 Redis Stream 拿到流式增量。
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.sql import text
from sqlmodel import JSON, Column, Field, Relationship, SQLModel

from app.models.base import TimeMixin


class ChatTuneMessageRole(StrEnum):
    """调参对话消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatTuneTurnStatus(StrEnum):
    """单轮调参生成状态。

    状态机：``GENERATING`` 是唯一非终态，最终单向流转到
    ``COMPLETED`` / ``FAILED`` / ``CANCELLED`` 之一。
    """

    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChatTuneGenerationKind(StrEnum):
    """单轮调参生成的协程类型。

    用于在重试时确定要重跑哪条后台协程：与 ``start_turn`` 的三分支一一对应。
    """

    CHAT_TUNE = "chat_tune"
    AI_BUILD = "ai_build"
    AI_AGENT = "ai_agent"
    REVIEW = "review"


class ChatTuneStageStatus(StrEnum):
    """调参会话中某个阶段的生命周期状态。

    每个阶段（gathering/build/review）维护一份独立的状态：协程开始执行
    置 ``RUNNING``，对应成功事件到达置 ``COMPLETED``，进入异常处理置
    ``FAILED``。取消不更新阶段状态——保留最后一次显式置位的值。
    """

    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ChatTuneActiveStage(StrEnum):
    """调参会话当前激活的阶段。

    默认激活需求收集阶段；任意一个后台协程开始执行都会把激活阶段
    更新为对应值（无回退语义，只反映"最近一次启动了哪个阶段"）。
    """

    GATHERING = "gathering"
    BUILD = "build"
    REVIEW = "review"


# ---- 调参会话 ----


class ChatTuneSession(SQLModel, TimeMixin, table=True):
    """调参会话，与任务严格 1:1 绑定。

    每个任务最多只对应一个调参会话；会话由首次访问时按需自动创建，
    无需独立的创建/列表/删除接口。
    """

    __tablename__ = "chat_tune_session"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", ondelete="CASCADE", index=True
    )
    task_id: uuid.UUID = Field(
        foreign_key="task.id",
        ondelete="CASCADE",
        unique=True,
        description="绑定的任务 ID，每个任务最多一个调参会话",
    )
    # 当前活跃的调参轮次 ID。指向"逻辑 turn"（user + assistant 共享 turn_id），
    # 不是任何具体行的 FK；前端刷新页面后据此判断是否要订阅流接口。
    active_turn_id: uuid.UUID | None = Field(default=None)
    # 最近一次完成轮次产出的可执行配置快照，方便前端"应用"按钮直接拿
    latest_config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            JSON, nullable=False, server_default=text("'{}'::json")
        ),
    )
    # 持久化的需求收集上下文（NeedsGatheringContext 的序列化形式）
    # 字段：phase_messages / user_context / language；user_input 不持久化（仅单轮使用）
    gathering_context: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    # ---- 三阶段状态机（gathering / build / review） ----
    # 每个阶段独立维护生命周期状态；前端可据此渲染"步骤条"。
    gathering_status: str = Field(
        default=ChatTuneStageStatus.NOT_STARTED.value,
        sa_column=Column(
            SAEnum(
                ChatTuneStageStatus,
                name="chattunestagestatus",
                native_enum=False,
                length=20,
                values_callable=lambda e: [m.value for m in e],
            ),
            nullable=False,
            server_default=ChatTuneStageStatus.NOT_STARTED.value,
        ),
    )
    build_status: str = Field(
        default=ChatTuneStageStatus.NOT_STARTED.value,
        sa_column=Column(
            SAEnum(
                ChatTuneStageStatus,
                name="chattunestagestatus",
                native_enum=False,
                length=20,
                values_callable=lambda e: [m.value for m in e],
                create_type=False,
            ),
            nullable=False,
            server_default=ChatTuneStageStatus.NOT_STARTED.value,
        ),
    )
    review_status: str = Field(
        default=ChatTuneStageStatus.NOT_STARTED.value,
        sa_column=Column(
            SAEnum(
                ChatTuneStageStatus,
                name="chattunestagestatus",
                native_enum=False,
                length=20,
                values_callable=lambda e: [m.value for m in e],
                create_type=False,
            ),
            nullable=False,
            server_default=ChatTuneStageStatus.NOT_STARTED.value,
        ),
    )
    active_stage: str = Field(
        default=ChatTuneActiveStage.GATHERING.value,
        sa_column=Column(
            SAEnum(
                ChatTuneActiveStage,
                name="chattuneactivestage",
                native_enum=False,
                length=20,
                values_callable=lambda e: [m.value for m in e],
            ),
            nullable=False,
            server_default=ChatTuneActiveStage.GATHERING.value,
        ),
    )

    user: Optional["User"] = Relationship()  # type: ignore[name-defined]  # noqa: F821
    messages: list["ChatTuneMessage"] = Relationship(
        back_populates="session", cascade_delete=True
    )


# ---- 调参对话消息 ----


class ChatTuneMessage(SQLModel, TimeMixin, table=True):
    """单条调参对话消息，user 与 assistant 各占一条记录。"""

    __tablename__ = "chat_tune_message"
    __table_args__ = (
        # 热路径查询：service 层都按 (session_id, turn_id) 过滤再取 role
        Index(
            "ix_chat_tune_message_session_turn",
            "session_id",
            "turn_id",
        ),
        # 启动钩子 reset_orphan_turns 的部分索引：只覆盖 generating 状态的行
        Index(
            "ix_chat_tune_message_generating",
            "updated_time",
            postgresql_where=text("turn_status = 'generating'"),
        ),
        # 业务不变量：同一 turn 内每种 role 至多一条
        UniqueConstraint(
            "session_id",
            "turn_id",
            "role",
            name="uq_chat_tune_message_turn_role",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(
        foreign_key="chat_tune_session.id",
        ondelete="CASCADE",
        index=True,
    )
    # 同一轮对话中 user 消息与 assistant 消息共享 turn_id，Redis stream key 也用它
    turn_id: uuid.UUID = Field(index=True)
    role: ChatTuneMessageRole = Field(
        sa_column=Column(
            SAEnum(
                ChatTuneMessageRole,
                name="chattunemessagerole",
                native_enum=False,
                length=20,
                values_callable=lambda e: [m.value for m in e],
            ),
            nullable=False,
        )
    )
    content: str = Field(sa_column=Column(Text, nullable=False, default=""))
    # 单轮生成状态。仅 assistant 消息会随生命周期变化，user 消息固定为 completed
    turn_status: ChatTuneTurnStatus = Field(
        default=ChatTuneTurnStatus.COMPLETED,
        sa_column=Column(
            SAEnum(
                ChatTuneTurnStatus,
                name="chattuneturnstatus",
                native_enum=False,
                length=20,
                values_callable=lambda e: [m.value for m in e],
            ),
            nullable=False,
            server_default=ChatTuneTurnStatus.COMPLETED.value,
        ),
    )
    # 失败原因。可能含换行/堆栈，使用 Text 而非定长 VARCHAR
    error: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # ---- 仅 assistant 消息使用：交互负载 ----
    # 结构化负载，前端按 ``payload.kind`` 决定渲染什么组件（form/choice/...）
    payload: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    # 表单是否已被用户提交并锁定。锁定后前端只读展示，不允许再次交互
    payload_locked: bool = Field(
        default=False,
        sa_column=Column(
            Boolean, nullable=False, server_default=text("false")
        ),
    )
    payload_locked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    # 用户提交表单时回填的值，用于审计与"快照式"只读渲染
    payload_submission: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    # 记录本轮 assistant 消息由哪条后台协程生成（chat_tune / ai_build / review），
    # 供 ``retry_turn`` 复用 ``start_turn`` 的三分支调度。仅 assistant 消息使用；
    # 历史数据为 ``NULL``，retry 时兜底走 ``chat_tune``。
    generation_kind: str | None = Field(
        default=None, max_length=20
    )

    session: ChatTuneSession | None = Relationship(back_populates="messages")
