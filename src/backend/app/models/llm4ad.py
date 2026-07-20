"""
LLM4AD 业务模型。

定义项目（Project）、任务（Task）和 LLM 供应商（LLMProvider）数据库模型，
以及任务状态枚举。这些模型是 LLM4AD 算法演化平台的核心数据结构。
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Optional

from sqlalchemy import DateTime, Index, Text, text
from sqlmodel import JSON, Column, Field, Relationship, SQLModel

from app.core.encryption import EncryptedString
from app.models.base import TimeMixin


class TaskStatus(StrEnum):
    """任务状态枚举。"""

    UNINITIALIZED = "uninitialized"  # 未初始化，未绑定 Celery 任务 ID
    PENDING = "pending"  # 已提交，等待执行
    RUNNING = "running"  # 正在运行
    COMPLETED = "completed"  # 执行完成
    FAILED = "failed"  # 执行失败


class ProviderType(StrEnum):
    """LLM 供应商类型枚举。"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"
    MOCK = "mock"


class EmbeddingProviderType(StrEnum):
    """Embedding 供应商类型枚举。"""

    OPENAI = "openai"
    JINA = "jina"
    OPENAI_COMPATIBLE = "openai_compatible"
    MOCK = "mock"
    LOCAL = "local"


class EmbeddingMode(StrEnum):
    """Embedding text/code 配置模式。"""

    SHARED = "shared"
    SPLIT = "split"


# ---- LLM 供应商 ----


class LLMProviderBase(SQLModel):
    """LLM 供应商基础 Schema，定义供应商的公共字段。"""

    name: str = Field(default="default", max_length=255, description="供应商名称，用于被其他组件引用")
    type: ProviderType = Field(default=ProviderType.OPENAI, description="供应商类型")
    api_key: str = Field(
        default="", sa_type=EncryptedString, description="API 密钥（加密存储）",
    )
    auth_token: str = Field(
        default="", sa_type=EncryptedString, description="认证令牌（如 Anthropic，加密存储）",
    )
    base_url: str | None = Field(default=None, max_length=512, description="自定义 API 基础 URL")
    model: str = Field(default="gpt-4", max_length=255, description="使用的模型名称，支持多个，使用;分割")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="采样温度")
    max_tokens: int = Field(default=32768, gt=0, description="最大生成 token 数")
    timeout: float = Field(default=60.0, gt=0, description="请求超时时间（秒）")
    max_retries: int = Field(default=3, ge=0, description="最大重试次数")
    is_builtin: bool = Field(default=False, description="系统内置供应商，仅 init_db 可修改")
    visible_to_all: bool = Field(default=False, description="对所有用户可见可用")


class LLMProvider(LLMProviderBase, TimeMixin, table=True):
    """LLM 供应商数据库模型，每个供应商配置属于一个用户或为系统内置。"""

    __tablename__ = "llmprovider"
    __table_args__ = (
        # 仅对内置行强制 name 唯一，用户行允许重名
        Index(
            "uq_provider_builtin_name",
            "name",
            unique=True,
            postgresql_where=text("is_builtin = true"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # 关联关系；内置供应商的 user_id 为 NULL
    user_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="CASCADE",
    )
    user: Optional["User"] = Relationship(back_populates="providers")  # type: ignore[name-defined]  # noqa: F821


# ---- Embedding 供应商 ----


class EmbeddingProviderBase(SQLModel):
    """Embedding 供应商基础 Schema，独立于 LLMProvider。"""

    name: str = Field(default="embedding", max_length=255, description="Embedding 配置名称")
    type: EmbeddingProviderType = Field(default=EmbeddingProviderType.JINA, description="Embedding 供应商类型")
    api_key: str = Field(default="", sa_type=EncryptedString, description="API 密钥（加密存储）")
    auth_token: str = Field(default="", sa_type=EncryptedString, description="认证令牌（加密存储）")
    base_url: str | None = Field(default=None, max_length=512, description="Embedding API 基础 URL")
    mode: EmbeddingMode = Field(default=EmbeddingMode.SHARED, description="text/code 共用或分流")
    model: str = Field(default="", max_length=255, description="共用 embedding 模型名称")
    dim: int = Field(default=3072, gt=0, description="Embedding 向量维度")
    timeout: float = Field(default=60.0, gt=0, description="请求超时时间（秒）")
    embedding_func_max_async: int = Field(default=2, ge=1, description="最大并发 embedding 请求数")
    text_type: EmbeddingProviderType = Field(
        default=EmbeddingProviderType.OPENAI_COMPATIBLE,
        description="文本 embedding 供应商类型",
    )
    text_base_url: str | None = Field(default=None, max_length=512, description="文本 embedding API 基础 URL")
    text_api_key: str = Field(default="", sa_type=EncryptedString, description="文本 embedding API 密钥")
    text_auth_token: str = Field(default="", sa_type=EncryptedString, description="文本 embedding 认证令牌")
    text_model: str = Field(default="", max_length=255, description="文本 embedding 模型名称")
    text_task: str = Field(default="text-matching", max_length=64, description="文本 embedding 任务模式")
    code_type: EmbeddingProviderType = Field(
        default=EmbeddingProviderType.OPENAI_COMPATIBLE,
        description="代码 embedding 供应商类型",
    )
    code_base_url: str | None = Field(default=None, max_length=512, description="代码 embedding API 基础 URL")
    code_api_key: str = Field(default="", sa_type=EncryptedString, description="代码 embedding API 密钥")
    code_auth_token: str = Field(default="", sa_type=EncryptedString, description="代码 embedding 认证令牌")
    code_model: str = Field(default="", max_length=255, description="代码 embedding 模型名称")
    code_task: str = Field(default="code.passage", max_length=64, description="代码 embedding 任务模式")
    is_builtin: bool = Field(default=False, description="系统内置 embedding 供应商，仅 init_db 可修改")
    visible_to_all: bool = Field(default=False, description="对所有用户可见可用")


class EmbeddingProvider(EmbeddingProviderBase, TimeMixin, table=True):
    """Embedding 配置；用户配置归属用户，系统内置配置 user_id 为 NULL。"""

    __tablename__ = "embeddingprovider"
    __table_args__ = (
        Index(
            "uq_embedding_provider_builtin_name",
            "name",
            unique=True,
            postgresql_where=text("is_builtin = true"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="user.id",
        ondelete="CASCADE",
        index=True,
    )


# ---- 项目 ----


class ProjectBase(SQLModel):
    """项目基础 Schema，定义项目的公共字段。"""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default="", max_length=255)
    icon: str | None = Field(default=None, max_length=255, description="项目图标名称")


class Project(ProjectBase, TimeMixin, table=True):
    """项目数据库模型，每个项目属于一个用户，可包含多个任务。"""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # 关联关系
    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE")
    user: Optional["User"] = Relationship(back_populates="projects")  # type: ignore[name-defined]  # noqa: F821
    tasks: list["Task"] = Relationship(back_populates="project", cascade_delete=True)
    memory_config: Optional["ProjectMemoryConfig"] = Relationship(
        back_populates="project",
        cascade_delete=True,
    )  # type: ignore[name-defined]  # noqa: F821


# ---- 任务 ----


class TaskBase(SQLModel):
    """任务基础 Schema，定义任务的公共字段。"""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default="", max_length=255)
    status: TaskStatus = Field(default=TaskStatus.UNINITIALIZED)
    input_args: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="任务参数 JSON，对应 YAML 配置",
    )
    input_data_path: str | None = Field(
        None,
        description="任务对应的数据路径，关联 S3 的 path，同 path 只能被一个任务关联",
    )
    group_id: uuid.UUID | None = Field(
        default=None,
        description="分组 ID，表示该任务所属分组；根任务为空",
    )
    parent_id: uuid.UUID | None = Field(
        default=None,
        description="父任务 ID，表示由哪个任务调参复制而来；根任务为空",
    )
    tag: str | None = Field(
        default=None,
        max_length=255,
        description="任务标签",
    )
    active_child_id: uuid.UUID | None = Field(
        default=None,
        description="当前活跃版本任务 ID；为空时按指向自身处理（读取时回退到自身的 status）",
    )
    ai_built: bool = Field(
        default=False,
        description="是否由 AI 构建",
    )
    ai_build_started: bool = Field(
        default=False,
        description="AI 是否已开始构建",
    )


class Task(TaskBase, TimeMixin, table=True):
    """任务数据库模型，包含 Celery 异步任务的关联信息。"""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    version_time: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=DateTime(timezone=True),
        description="版本时间，input_args 变更时同步更新",
    )
    celery_task_id: str | None = Field(
        default=None,
        max_length=255,
        description="关联的 Celery 任务 ID",
    )

    reports: dict | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="演化洞察报告，键为报告类型，值为报告内容字典",
    )

    result_render: dict | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="任务结果渲染，键为结果类型，值为结果渲染数据",
    )

    # 关联关系
    project_id: uuid.UUID = Field(foreign_key="project.id", ondelete="CASCADE")
    project: Project = Relationship(back_populates="tasks")
    task_logs: list["TaskLog"] = Relationship(back_populates="task", cascade_delete=True)


# ---- 用户默认模型配置 ----


class UserDefaultModelBase(SQLModel):
    """用户默认模型配置基础 Schema，定义各角色默认模型的公共字段。"""

    planner_provider_id: uuid.UUID | None = Field(
        default=None, foreign_key="llmprovider.id", ondelete="SET NULL",
        description="默认 Planner 供应商 ID",
    )
    planner_model_name: str | None = Field(
        default=None, max_length=255, description="默认 Planner 模型名称",
    )
    coder_provider_id: uuid.UUID | None = Field(
        default=None, foreign_key="llmprovider.id", ondelete="SET NULL",
        description="默认 Coder 供应商 ID",
    )
    coder_model_name: str | None = Field(
        default=None, max_length=255, description="默认 Coder 模型名称",
    )
    report_provider_id: uuid.UUID | None = Field(
        default=None, foreign_key="llmprovider.id", ondelete="SET NULL",
        description="默认报告供应商 ID",
    )
    report_model_name: str | None = Field(
        default=None, max_length=255, description="默认报告模型名称",
    )
    other_provider_id: uuid.UUID | None = Field(
        default=None, foreign_key="llmprovider.id", ondelete="SET NULL",
        description="默认其它供应商 ID",
    )
    other_model_name: str | None = Field(
        default=None, max_length=255, description="默认其它模型名称",
    )
    embedding_enabled: bool = Field(default=False, description="是否启用轨迹 embedding")
    embedding_provider_id: uuid.UUID | None = Field(
        default=None, foreign_key="embeddingprovider.id", ondelete="SET NULL",
        description="默认 embedding 供应商 ID",
    )


class UserDefaultModel(UserDefaultModelBase, TimeMixin, table=True):
    """用户默认模型配置数据库模型，与用户一对一关联。"""

    __tablename__ = "user_default_model"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # 关联关系
    user_id: uuid.UUID = Field(
        foreign_key="user.id", ondelete="CASCADE", unique=True, index=True,
    )
    user: Optional["User"] = Relationship(back_populates="default_model")  # type: ignore[name-defined]  # noqa: F821


class MemoryConfigBase(SQLModel):
    """Shared per-user/per-project memory defaults."""

    enabled: bool = Field(default=True, description="Whether memory injection is enabled by default")
    include_user_memory: bool = Field(default=False, description="Inject user-scoped memories")
    include_project_memory: bool = Field(default=False, description="Inject project-scoped memories")
    include_task_memory: bool = Field(default=True, description="Inject task-scoped memories")
    user_memory_limit: int = Field(default=0, ge=0, description="Max user memories injected")
    project_memory_limit: int = Field(default=0, ge=0, description="Max project memories injected")
    task_memory_limit: int = Field(default=5, ge=0, description="Max task memories injected")
    retrieval_mode: str = Field(
        default="auto", max_length=16,
        description="Long-term memory retrieval mode: auto or manual",
    )
    pinned_card_ids: list[str] = Field(
        default_factory=list, sa_type=JSON,
        description="Fixed memory card ids injected in manual retrieval mode",
    )
    task_injection_mode: str = Field(
        default="topk", max_length=16,
        description="Task memory injection ordering: topk, weight, or random",
    )
    mindmemos_search_strategy: str = Field(default="fast", max_length=32)
    mindmemos_rerank: bool = Field(default=False)
    mindmemos_score_threshold: float | None = Field(default=None)
    mindmemos_fail_open: bool = Field(default=True)
    mindmemos_binding_id: str | None = Field(default=None, max_length=128)
    mindmemos_chat_provider_id: uuid.UUID | None = Field(default=None)
    mindmemos_chat_model: str | None = Field(default=None, max_length=255)
    mindmemos_embedding_provider_id: uuid.UUID | None = Field(default=None)
    mindmemos_embedding_model: str | None = Field(default=None, max_length=255)
    mindmemos_embedding_dim: int | None = Field(default=None, gt=0)


class UserMemoryConfig(MemoryConfigBase, TimeMixin, table=True):
    """User-level memory defaults, one row per user."""

    __tablename__ = "user_memory_config"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id",
        ondelete="CASCADE",
        unique=True,
        index=True,
    )


class ProjectMemoryConfig(MemoryConfigBase, TimeMixin, table=True):
    """Project-level memory defaults, one row per project."""

    __tablename__ = "project_memory_config"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        foreign_key="project.id",
        ondelete="CASCADE",
        unique=True,
        index=True,
    )
    project: Project = Relationship(back_populates="memory_config")


class TaskLog(SQLModel, TimeMixin, table=True):
    """单条任务日志记录。

    任务执行期间日志先写入 Redis，任务结束后持久化到此表。

    Attributes:
        id: 日志主键 UUID。
        task_id: 关联的任务 ID（外键，删除时级联）。
        type: 日志类型标识（如 stdout、stderr、event 等）。
        level: 日志级别（INFO、WARNING、ERROR 等）。
        timestamp: 日志产生时间。
        message: 文本消息内容。
        data: 结构化附加数据（JSON）。
    """

    __tablename__ = "task_log"
    __table_args__ = (
        # 服务「按类型过滤 + 按时间排序」的日志查询（如 type=generated），
        # 索引区间扫描即可拿到有序结果，免回表过滤与额外排序。
        Index("ix_task_log_task_type_ts", "task_id", "type", "timestamp", "id"),
        # 服务「不分类型、按时间游标翻页」的默认列表查询。
        Index("ix_task_log_task_ts", "task_id", "timestamp", "id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    task_id: uuid.UUID = Field(foreign_key="task.id", ondelete="CASCADE", index=True)
    type: str = Field(max_length=50)
    level: str | None = Field(default=None, max_length=20)
    timestamp: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    data: dict | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    task: Task | None = Relationship(back_populates="task_logs")
