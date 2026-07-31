"""自动科研（Research）业务数据模型。

对应「LLM4AD × AutoResearchClaw」的科研会话链路：

- ``ResearchFolder``：分组文件夹，可嵌套，用户在前端创建后用于归档会话。
- ``ResearchSession``：一次科研会话（对应 ARC 的一个 run_dir），可选归属分组。
- ``ResearchTurn``：单轮生成（start / stop 后 resume / retry 各占一轮），
  绑定 Celery 任务与 ARC subprocess 生命周期。
- ``ResearchMessage``：user / assistant / system 消息，参考 ``ChatTuneMessage``
  的表单交互模式，通过 ``payload`` 承载 ARC gate 提问与产物就绪通知。

会话-轮次-消息三层的意义：
- 前端页面刷新后按 ``session_id`` 拉历史；
- 订阅 SSE 时按 ``(session_id, turn_id)`` 走 Redis Stream；
- 用户表单回填由 ``respond_to_message_id + submission`` 触发新轮，与调参一致。
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.sql import text
from sqlmodel import JSON, Column, Field, Relationship, SQLModel

from app.models.base import TimeMixin

# ---- 枚举 ----


class ResearchSessionStatus(StrEnum):
    """会话整体状态。

    状态机：
        ``PENDING``（新建，未触发首轮） → ``RUNNING`` → 三个可暂停终态之一
        （``PAUSED`` 等待用户输入 / ``COMPLETED`` 23 阶段跑完 /
        ``FAILED`` 异常终止 / ``CANCELLED`` 用户主动停止）。

    ``PAUSED`` 是「非终止的暂停」，可以再次 ``POST /turns`` 恢复运行；
    其余三态是终止态但仍允许 ``retry`` 或删除。
    """

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchTurnStatus(StrEnum):
    """单轮生成状态。

    - ``RUNNING``：ARC subprocess 运行中。
    - ``PAUSED_GATE``：命中硬门控、释放 worker，等用户回复后新建一轮续跑。
    - ``COLLABORATING``：门控暂停时发起的「人 + AI 协作改产物」子会话，不推进 pipeline。
    - ``COMPLETED`` / ``FAILED`` / ``CANCELLED``：终态。
    """

    RUNNING = "running"
    PAUSED_GATE = "paused_gate"
    COLLABORATING = "collaborating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchMessageRole(StrEnum):
    """会话消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ResearchMode(StrEnum):
    """ARC 执行模式（映射到 ``researchclaw run --mode``）。

    与 ARC 的 ``PROJECT_MODES`` / 常见 CLI 值保持一致；未列出的值前端不给出，
    但 backend 允许透传以便未来 ARC 升级新增模式无需 backend 同步改动。
    """

    FULL_AUTO = "full-auto"          # 一路跑到底，尽量不问用户
    GATE_ONLY = "gate-only"          # 只在 HITL gate 阶段暂停
    CHECKPOINT = "checkpoint"        # 每个阶段落 checkpoint 供审阅
    STEP_BY_STEP = "step-by-step"    # 每个阶段完成后暂停
    CO_PILOT = "co-pilot"            # 每一步都可干预（推荐前端默认）
    EXPRESS = "express"              # 极简快跑
    THOROUGH = "thorough"            # 深度模式
    LEARNING = "learning"            # 教学模式


# ---- 分组文件夹 ----


class ResearchFolder(SQLModel, TimeMixin, table=True):
    """科研会话分组文件夹。

    支持嵌套：``parent_id`` 指向父文件夹，为 ``NULL`` 时是根文件夹。父文件夹
    被删除时子文件夹的 ``parent_id`` 会被置为 ``NULL``（``SET NULL``），避免
    级联删除误伤用户资料；被删除文件夹里的会话同理会脱离归属回到「未分组」。
    """

    __tablename__ = "research_folder"
    __table_args__ = (
        # 用户视角的典型查询：同一用户下按 parent 展开树状结构
        Index("ix_research_folder_user_parent", "user_id", "parent_id"),
        # 同一父目录下文件夹名称唯一，便于前端拒绝重名新建
        UniqueConstraint(
            "user_id",
            "parent_id",
            "name",
            name="uq_research_folder_user_parent_name",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", ondelete="CASCADE", index=True
    )
    parent_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="research_folder.id",
        ondelete="SET NULL",
        description="父文件夹 ID，NULL 表示根",
    )
    name: str = Field(max_length=255, description="文件夹显示名")
    sort_order: int = Field(
        default=0,
        sa_column=Column(
            Integer, nullable=False, server_default=text("0")
        ),
        description="同级排序权重，越小越靠前，等于 0 时按创建时间兜底",
    )

    user: Optional["User"] = Relationship()  # type: ignore[name-defined]  # noqa: F821


# ---- 科研会话 ----


class ResearchSession(SQLModel, TimeMixin, table=True):
    """一次科研会话，对应 ARC 的一个 ``run_dir``。

    与 chat_tune 不同：一个会话可跨多个 turn（stop 之后可以「切换 mode/model
    再发送」触发新 turn），因此 ``active_turn_id`` 是可空指针。
    产物、状态与 ARC pipeline 事件都通过 ``run_dir`` 落盘并由 tail worker
    折算成 messages / Redis Stream 事件。
    """

    __tablename__ = "research_session"
    __table_args__ = (
        Index("ix_research_session_user_folder", "user_id", "folder_id"),
        Index("ix_research_session_user_updated", "user_id", "updated_time"),
        # 部分索引：只覆盖非终态会话，供后台监控 orphan 会话
        Index(
            "ix_research_session_alive",
            "updated_time",
            postgresql_where=text(
                "status in ('pending','running','paused')"
            ),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", ondelete="CASCADE", index=True
    )
    # 归属分组：NULL 表示未分组。删除文件夹时会被置 NULL。
    folder_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="research_folder.id",
        ondelete="SET NULL",
    )

    # 显示名，默认取 topic 前 60 字符；用户可改
    title: str = Field(max_length=255)
    # 用户提出的原始研究问题（首轮 user_message 的内容也是这个）
    topic: str = Field(sa_column=Column(Text, nullable=False, server_default=""))
    # ARC 域 profile id（默认 algorithm_design）；用户可通过前端改成其他 ARC 支持的域
    profile: str = Field(
        default="algorithm_design",
        max_length=64,
        description="ARC domain_id，控制 pipeline 走哪个 adapter",
    )
    mode: str = Field(
        default=ResearchMode.CO_PILOT.value,
        max_length=32,
        description="ARC HITL 模式；映射到 --mode 参数",
    )

    # 默认 provider / model（新一轮可覆盖）。参考 chat_tune，允许 'default'/'mock' 等非 UUID
    provider_id: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, max_length=255)

    # 会话整体状态；由 tail worker 与用户操作驱动
    status: str = Field(
        default=ResearchSessionStatus.PENDING.value,
        sa_column=Column(
            SAEnum(
                ResearchSessionStatus,
                name="researchsessionstatus",
                native_enum=False,
                length=20,
                values_callable=lambda e: [m.value for m in e],
            ),
            nullable=False,
            server_default=ResearchSessionStatus.PENDING.value,
        ),
    )

    # 当前活跃轮次指针；PAUSED_GATE 状态时也保留最后一个 turn。
    # FK ondelete=SET NULL：turn 被删除（比如 sweeper 清理孤儿）时 session 保留。
    active_turn_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="research_turn.id",
        ondelete="SET NULL",
    )

    # 当前 ARC 阶段号（1-23），tail worker 观察到 stage_transition 事件时更新
    active_stage: int | None = Field(default=None)
    # 当前阶段可读名（例如 EXPERIMENT_RUN），冗余方便前端渲染无需查阶段字典
    active_stage_name: str | None = Field(default=None, max_length=64)

    # ARC 的 run_dir 绝对路径（宿主机视角）。产物获取的根路径。
    run_dir: str | None = Field(default=None, max_length=1024)
    # ARC 用的 config.arc.yaml 生成快照，便于审计 / 重放
    latest_config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            JSON, nullable=False, server_default=text("'{}'::json")
        ),
    )
    # LLM4AD workspace 引用：用户上传的 config.yaml 或引用一个已有 llm4ad_task_id
    # 结构示例：{"kind": "task_ref", "task_id": "..."} 或
    #          {"kind": "inline", "config": {...}} 或
    #          {"kind": "upload", "path": ".../workspace.zip"}
    llm4ad_workspace: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    # 结果快照，tail worker 观察到关键事件时更新，供列表页快速展示
    best_objective: float | None = Field(default=None)
    best_code_sha256: str | None = Field(default=None, max_length=64)

    # 结束时间；PAUSED 不算结束
    ended_time: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    # 结束原因（用户可读）
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    # 结果分析 LLM 报告缓存：{"content","status","provider_model","language",
    # "created_at","updated_at","error"}。结构化聚合数据实时读盘、不落此字段。
    analysis_report: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    user: Optional["User"] = Relationship()  # type: ignore[name-defined]  # noqa: F821
    # 显式 foreign_keys：session ↔ turn 有两条 FK 路径，指明本 collection 只走
    # turn.session_id，否则 SQLAlchemy 抛 AmbiguousForeignKeys。
    turns: list["ResearchTurn"] = Relationship(
        back_populates="session",
        cascade_delete=True,
        sa_relationship_kwargs={"foreign_keys": "[ResearchTurn.session_id]"},
    )
    messages: list["ResearchMessage"] = Relationship(
        back_populates="session", cascade_delete=True
    )


# ---- 单轮生成 ----


class ResearchTurn(SQLModel, TimeMixin, table=True):
    """单轮生成，绑定 Celery 任务与 ARC subprocess 生命周期。

    首轮触发时创建（对应 ``POST /sessions/{id}/turns`` 首次调用）。
    用户 ``stop`` 之后再次 ``POST /turns`` 会新建下一轮（``session_id`` 相同、
    ``turn_id`` 新）；表单回填也走新轮（``respond_to_message_id + submission``）。
    ``retry`` 复用同一轮，只重置状态与 Celery 任务。
    """

    __tablename__ = "research_turn"
    __table_args__ = (
        Index("ix_research_turn_session_created", "session_id", "created_time"),
        # 用于后台 sweeper 找孤儿：RUNNING 且长时间未更新
        Index(
            "ix_research_turn_alive",
            "updated_time",
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(
        foreign_key="research_session.id",
        ondelete="CASCADE",
        index=True,
    )
    # 关联的 Celery 任务 ID（研究桥接任务在 tasks/research_runner.py）
    celery_task_id: str | None = Field(default=None, max_length=255)

    status: str = Field(
        default=ResearchTurnStatus.RUNNING.value,
        sa_column=Column(
            SAEnum(
                ResearchTurnStatus,
                name="researchturnstatus",
                native_enum=False,
                length=20,
                values_callable=lambda e: [m.value for m in e],
            ),
            nullable=False,
            server_default=ResearchTurnStatus.RUNNING.value,
        ),
    )

    # 本轮触发参数（可覆盖会话默认）
    provider_id: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, max_length=255)
    mode: str | None = Field(default=None, max_length=32)

    # ARC pipeline 起止阶段（映射到 --from-stage / --to-stage）
    from_stage: str | None = Field(default=None, max_length=64)
    to_stage: str | None = Field(default=None, max_length=64)

    # 触发本轮的用户输入（普通文字消息 or 表单提交 or「resume」空输入）
    user_input: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    # 触发本轮的响应消息 ID（表单回填链路），指向被锁定的 assistant 消息。
    # FK ondelete=SET NULL：目标 message 若被清理，本 turn 仍保留。
    respond_to_message_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="research_message.id",
        ondelete="SET NULL",
    )

    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    ended_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # 反向关系也要指同一列，back_populates 才能对齐
    session: ResearchSession | None = Relationship(
        back_populates="turns",
        sa_relationship_kwargs={"foreign_keys": "[ResearchTurn.session_id]"},
    )


# ---- 会话消息 ----


class ResearchMessage(SQLModel, TimeMixin, table=True):
    """会话消息（user / assistant / system）。

    ``payload`` 承载结构化交互载荷，前端按 ``payload.kind`` 决定渲染组件：

    - ``kind == "form"``：ARC gate 阶段的表单，含 ``fields[]`` 与 ``prompt``。
      用户填完通过 ``POST /turns`` 带 ``submission`` 回填。
    - ``kind == "choice"``：单/多选表单（``options[]``），点击即可发送。
    - ``kind == "stage_progress"``：23 阶段 rail 更新事件（首次或跳阶段时落库）。
    - ``kind == "artifact_ready"``：某产物就绪（paper draft / best_code / figure）。
    - ``kind == "log"``：普通日志，只在没有更结构化事件时兜底落库。

    表单类载荷提交后 ``payload_locked=True``，UI 只读展示历史提交。
    """

    __tablename__ = "research_message"
    __table_args__ = (
        # /messages 端点热路径：WHERE session_id=? AND turn_id=?
        # [AND created_time > cursor] ORDER BY created_time, id
        Index(
            "ix_research_message_session_turn_created",
            "session_id",
            "turn_id",
            "created_time",
            "id",
        ),
        Index(
            "ix_research_message_session_created",
            "session_id",
            "created_time",
        ),
        # sweeper：找生成中长时间无更新的 assistant 消息
        Index(
            "ix_research_message_generating",
            "updated_time",
            postgresql_where=text("turn_status = 'running'"),
        ),
        # 同一 turn 内 (role, event_key) 二元唯一，保证事件流重放幂等。
        # event_key 约定见下方字段定义。
        UniqueConstraint(
            "session_id",
            "turn_id",
            "role",
            "event_key",
            name="uq_research_message_turn_role_event",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(
        foreign_key="research_session.id",
        ondelete="CASCADE",
        index=True,
    )
    turn_id: uuid.UUID = Field(
        foreign_key="research_turn.id",
        ondelete="CASCADE",
        index=True,
    )
    role: ResearchMessageRole = Field(
        sa_column=Column(
            SAEnum(
                ResearchMessageRole,
                name="researchmessagerole",
                native_enum=False,
                length=20,
                values_callable=lambda e: [m.value for m in e],
            ),
            nullable=False,
        )
    )
    content: str = Field(sa_column=Column(Text, nullable=False, server_default=""))
    turn_status: str = Field(
        default=ResearchTurnStatus.COMPLETED.value,
        sa_column=Column(
            SAEnum(
                ResearchTurnStatus,
                name="researchturnstatus",
                native_enum=False,
                length=20,
                values_callable=lambda e: [m.value for m in e],
                create_type=False,
            ),
            nullable=False,
            server_default=ResearchTurnStatus.COMPLETED.value,
        ),
    )
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    # ---- 交互载荷 ----
    payload: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
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
    payload_submission: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    # ---- 事件维度 ----
    # ARC pipeline 阶段号 (1-23)。用户消息 / 顶层 assistant 消息为 NULL。
    stage: int | None = Field(default=None)
    # 系统事件分类：log / stage_transition / artifact_ready /
    # waiting_gate / …；用户与主 assistant 消息为 NULL。
    event_type: str | None = Field(default=None, max_length=32)
    # 幂等键：同一 (session, turn, role) 下唯一。约定：
    #   - 主 user/assistant 消息："user:<turn_id>" / "assistant:<turn_id>"
    #   - 系统事件用自然键，如 "stage-12:running"、"gen:foo.py"
    #   - 通用 log 事件由 sink 自动分配 "<event_type>:<seq>"
    # 永远非空（server_default=""）；空串仅在缺省场景兜底，正式路径都会填。
    event_key: str = Field(
        default="",
        max_length=128,
        sa_column=Column(Text, nullable=False, server_default=""),
    )
    # per-turn 递增序列号，保证事件严格顺序（解决同一微秒内多事件的排序问题）
    seq: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    # 本条对应的 Redis Stream entry id（<ms>-<seq>）。前端刷新后据此从「已拉取
    # 历史的末端」精确续传 SSE（免全量重放），并作精确去重键。旧数据 / push 失败为 NULL。
    stream_id: str | None = Field(default=None, max_length=64)

    session: ResearchSession | None = Relationship(back_populates="messages")


class ResearchLog(SQLModel, TimeMixin, table=True):
    """研究日志表（从 ResearchMessage 中拆分出来的 log 类型事件）。

    日志约占总消息的 90-95%，拆分后可显著提升查询性能。
    包含容器、ARC、bridge、collab 等各个来源的日志输出。

    字段说明：
    - ``level``：日志级别（INFO/WARNING/ERROR/DEBUG）
    - ``message``：日志消息文本
    - ``source``：日志来源（arc/container/bridge/collab）
    - ``module``：可选的模块名
    - ``event_key``：幂等键，与 ResearchMessage 保持一致的约定
    - ``turn_status``：记录日志时的轮次状态
    - ``stage``：可选的 ARC pipeline 阶段号 (1-23)
    - ``ts``：可选的原始时间戳（从事件 payload 中提取）
    """

    __tablename__ = "research_log"
    __table_args__ = (
        # 查询热路径：按 session + turn + 时间排序
        Index(
            "ix_research_log_session_turn_time",
            "session_id",
            "turn_id",
            "created_time",
            "id",
        ),
        # 会话级日志查询
        Index(
            "ix_research_log_session_time",
            "session_id",
            "created_time",
            "id",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(
        foreign_key="research_session.id",
        ondelete="CASCADE",
        index=True,
    )
    turn_id: uuid.UUID = Field(
        foreign_key="research_turn.id",
        ondelete="CASCADE",
        index=True,
    )
    level: str = Field(max_length=16)
    message: str = Field(sa_column=Column(Text, nullable=False))
    source: str = Field(max_length=32)
    module: str | None = Field(default=None, max_length=128)
    event_key: str = Field(max_length=128)
    turn_status: str = Field(
        sa_column=Column(
            SAEnum(
                ResearchTurnStatus,
                name="researchturnstatus",
                native_enum=False,
                length=20,
                values_callable=lambda e: [m.value for m in e],
                create_type=False,
            ),
            nullable=False,
        ),
    )
    stage: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    ts: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    # per-turn 递增序列号，保证事件严格顺序（解决同一微秒内多事件的排序问题）
    seq: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    # 本条对应的 Redis Stream entry id（<ms>-<seq>）。前端刷新后据此续传 SSE
    # 并作精确去重键（尤其修 retry 复用 turn_id 时 event_key="<type>:<seq>" 撞键）。
    stream_id: str | None = Field(default=None, max_length=64)
