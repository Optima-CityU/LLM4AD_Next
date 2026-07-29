"""自动科研（Research）相关的请求/响应 Schema。

按端点分组：
- 分组文件夹（folders）
- 会话（sessions）
- 轮次（turns，含 start/stop/retry）
- 消息（messages，含分页历史）
- 产物（artifacts）
- 状态快照（state）

命名与 chat_tune 对齐，方便前端复用组件与类型定义。
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.research import (
    ResearchMessageRole,
    ResearchMode,
    ResearchSessionStatus,
    ResearchTurnStatus,
)

# ---- 分组文件夹 ----


class ResearchFolderCreateRequest(BaseModel):
    """新建文件夹的请求体。"""

    name: str = Field(min_length=1, max_length=255, description="文件夹名")
    parent_id: uuid.UUID | None = Field(
        default=None, description="父文件夹 ID，None 表示根"
    )
    sort_order: int = Field(default=0, description="同级排序权重")


class ResearchFolderUpdateRequest(BaseModel):
    """改文件夹（改名、移动、排序）。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "新的父文件夹 ID。传 None 且请求体显式包含该键时移到根；"
            "未提供该键则不改变。"
        ),
    )
    sort_order: int | None = Field(default=None)


class ResearchFolderItem(BaseModel):
    """文件夹响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    sort_order: int
    session_count: int = Field(
        default=0,
        description="该文件夹直接归属的会话数（不含子文件夹内的）",
    )
    created_time: datetime
    updated_time: datetime


class ResearchFolderTreeNode(BaseModel):
    """树形节点，含子节点与该文件夹直接归属的会话数量。"""

    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    sort_order: int
    session_count: int = 0
    children: list["ResearchFolderTreeNode"] = Field(default_factory=list)


class ResearchFolderTreeResponse(BaseModel):
    """`GET /folders/tree` 响应：嵌套树 + 未归属会话计数。"""

    tree: list[ResearchFolderTreeNode] = Field(default_factory=list)
    ungrouped_session_count: int = 0


class ResearchFolderReorderItem(BaseModel):
    """批量重排单条：只需 id + 新 sort_order。"""

    id: uuid.UUID
    sort_order: int


class ResearchFolderReorderRequest(BaseModel):
    """`POST /folders/reorder` 请求体：一次性重排多个文件夹。"""

    items: list[ResearchFolderReorderItem] = Field(
        min_length=1, max_length=500,
        description="要重排的文件夹列表；未列出的不动",
    )


class ResearchFolderListResponse(BaseModel):
    """扁平文件夹列表（前端可自选按 parent 组织树）。"""

    items: list[ResearchFolderItem] = Field(default_factory=list)
    total: int = 0
    ungrouped_session_count: int = Field(
        default=0, description="未归属任何文件夹的会话数量"
    )


# ---- 会话 ----


class ResearchLLM4ADWorkspaceRef(BaseModel):
    """LLM4AD workspace 引用的三种形态。"""

    kind: Literal["task_ref", "inline", "upload"] = Field(
        description=(
            "task_ref: 引用现有调参任务；inline: 前端直接内联 LLM4AD "
            "AppConfig；upload: 走 upload-file 端点上传后传相对路径"
        )
    )
    task_id: uuid.UUID | None = Field(default=None, description="task_ref 用")
    config: dict[str, Any] | None = Field(
        default=None, description="inline 用；结构与 LLM4AD AppConfig.from_dict 一致"
    )
    path: str | None = Field(
        default=None, description="upload 用；相对 session 数据目录的路径"
    )

    @model_validator(mode="after")
    def _validate(self) -> "ResearchLLM4ADWorkspaceRef":
        if self.kind == "task_ref" and not self.task_id:
            raise ValueError("kind=task_ref 时必须提供 task_id")
        if self.kind == "inline" and not self.config:
            raise ValueError("kind=inline 时必须提供 config")
        if self.kind == "upload" and not self.path:
            raise ValueError("kind=upload 时必须提供 path")
        return self


class ResearchSessionCreateRequest(BaseModel):
    """创建会话（不立即触发首轮）。"""

    title: str | None = Field(
        default=None,
        max_length=255,
        description="会话显示名；缺省时后端用 topic 前 60 字符生成",
    )
    topic: str = Field(
        min_length=1, max_length=20000, description="研究问题 / 主题"
    )
    profile: str = Field(
        default="algorithm_evolution",
        max_length=64,
        description="ARC domain profile id",
    )
    mode: ResearchMode = Field(
        default=ResearchMode.CO_PILOT, description="ARC HITL 模式"
    )
    folder_id: uuid.UUID | None = Field(default=None, description="归属分组，可选")
    provider_id: str | None = Field(
        default=None,
        max_length=64,
        description="默认 LLM Provider（支持 'default' / 'mock' / UUID）",
    )
    model_name: str | None = Field(default=None, max_length=255)
    llm4ad_workspace: ResearchLLM4ADWorkspaceRef | None = Field(
        default=None,
        description="LLM4AD workspace 引用；缺省时首轮会引导用户补齐",
    )


class ResearchSessionUpdateRequest(BaseModel):
    """更新会话（改名 / 移动分组 / 改默认 provider / 改 mode / 改 topic 等）。"""

    title: str | None = Field(default=None, max_length=255)
    topic: str | None = Field(default=None, max_length=2000, description="研究主题/问题")
    profile: str | None = Field(
        default=None,
        max_length=64,
        description="ARC domain profile id（如 ml_vision）；未提供不变",
    )
    folder_id: uuid.UUID | None = Field(
        default=None,
        description="传 None 且请求体显式包含该键时移到未分组；未提供不变",
    )
    mode: ResearchMode | None = Field(default=None)
    provider_id: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, max_length=255)


class ResearchSessionItem(BaseModel):
    """会话响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    folder_id: uuid.UUID | None
    title: str
    topic: str
    profile: str
    mode: str
    provider_id: str | None
    model_name: str | None
    status: ResearchSessionStatus
    active_turn_id: uuid.UUID | None
    active_stage: int | None
    active_stage_name: str | None
    run_dir: str | None
    best_objective: float | None
    best_code_sha256: str | None
    ended_time: datetime | None
    error: str | None
    created_time: datetime
    updated_time: datetime


class ResearchSessionListResponse(BaseModel):
    """会话分页列表（游标分页，与 turn/message 列表统一）。"""

    items: list[ResearchSessionItem] = Field(default_factory=list)
    next_cursor: str | None = Field(
        default=None,
        description="下一页游标 = 本页最后一条的 updated_time ISO；None 表示无更多",
    )
    has_more: bool = False


# ---- 消息 ----


class ResearchMessageItem(BaseModel):
    """单条消息。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    turn_id: uuid.UUID
    role: ResearchMessageRole
    content: str
    turn_status: ResearchTurnStatus
    error: str | None = None
    payload: dict[str, Any] | None = None
    payload_locked: bool = False
    payload_locked_at: datetime | None = None
    payload_submission: dict[str, Any] | None = None
    stage: int | None = None
    event_type: str | None = None
    event_key: str = ""
    created_time: datetime
    updated_time: datetime


class ResearchStageGuideRequest(BaseModel):
    """`POST /sessions/{sid}/stages/{stage_num}/guide` 请求体。"""

    message: str = Field(
        min_length=1, max_length=8192,
        description="要注入的引导文本；ARC 下次跑到该 stage 时读取并注入 prompt",
    )


class ResearchStageGuideResponse(BaseModel):
    """`POST /guide` 响应。"""

    session_id: uuid.UUID
    stage: int
    length: int
    guidance_path: str


class ResearchMessageListResponse(BaseModel):
    """`/turns/{tid}/messages` 分页响应。

    正序游标翻页：``cursor`` = 上一页最后一条的 ``created_time`` ISO 字符串，
    首次不传；``next_cursor`` 为 ``None`` 时表示无更多数据。
    """

    items: list[ResearchMessageItem]
    next_cursor: str | None = None
    has_more: bool = False


class ResearchSessionMessagesResponse(BaseModel):
    """`/sessions/{sid}/messages` 会话级消息分页响应。

    与 ``ResearchSessionDetailResponse.messages`` 同款翻页语义（``before``/倒序
    游标，返回时升序），但脱离会话详情单独成端点：供消息列表与日志面板各自带
    ``event_type`` 过滤、各自分页，互不饥饿。``messages`` 升序（最旧在前）；
    ``has_more`` 为 True 时用最早一条 ``id`` 作 ``before`` 继续往前翻。
    """

    messages: list[ResearchMessageItem] = Field(default_factory=list)
    has_more: bool = Field(
        default=False,
        description="是否还有更早的历史消息；True 时用最早一条 id 做 before 游标",
    )


class ResearchTurnListResponse(BaseModel):
    """`/sessions/{sid}/turns` 分页响应，倒序（最新的在前）。"""

    items: list["ResearchTurnItem"]
    next_cursor: str | None = None
    has_more: bool = False


class ResearchSessionDetailResponse(BaseModel):
    """会话详情 + 分页消息 + 最近一轮。"""

    session: ResearchSessionItem
    active_turn: "ResearchTurnItem | None" = None
    messages: list[ResearchMessageItem] = Field(default_factory=list)
    has_more: bool = Field(
        default=False,
        description="是否还有更早的历史消息；True 时用最早一条 id 做 before 游标",
    )


# ---- 轮次（Turn） ----


class ResearchTurnStartRequest(BaseModel):
    """触发新一轮（首启 / stop 后 resume / 表单回填 均走这里）。

    三种触发方式：
    - 首启会话（``session.status == PENDING``）：只需 ``content`` 或直接空
      body（会话已带 topic）。
    - stop 后继续：可携带 ``content`` 追加消息；也可直接空 body 走 resume
      语义（复用会话现有 topic + 现有 workspace）。
    - 表单回填：填 ``respond_to_message_id + submission``，``content`` 可省略。

    ``provider_id``/``model_name``/``mode``/``from_stage``/``to_stage`` 允许覆盖
    会话默认值，本轮生效但不写回 session（用户下次不指定则回退到 session 默认）。
    """

    content: str | None = Field(default=None, max_length=20000)
    respond_to_message_id: uuid.UUID | None = Field(default=None)
    submission: dict[str, Any] | None = Field(
        default=None,
        description="表单提交值；与 respond_to_message_id 必须同时出现或同时缺省",
    )

    provider_id: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, max_length=255)
    mode: ResearchMode | None = Field(default=None)
    from_stage: str | None = Field(
        default=None, max_length=64, description="ARC --from-stage"
    )
    to_stage: str | None = Field(
        default=None, max_length=64, description="ARC --to-stage"
    )

    @model_validator(mode="after")
    def _validate_inputs(self) -> "ResearchTurnStartRequest":
        """``submission`` 与 ``respond_to_message_id`` 必须配对出现。"""
        has_sub = self.submission is not None
        has_ref = self.respond_to_message_id is not None
        if has_sub != has_ref:
            raise ValueError(
                "submission 与 respond_to_message_id 必须同时提供或同时缺省"
            )
        return self


class ResearchTurnRetryRequest(BaseModel):
    """重跑一个失败 / 停止的轮次。"""

    provider_id: str | None = Field(default=None, max_length=64)
    model_name: str | None = Field(default=None, max_length=255)
    mode: ResearchMode | None = Field(default=None)


class ResearchTurnItem(BaseModel):
    """轮次响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    celery_task_id: str | None
    status: ResearchTurnStatus
    provider_id: str | None
    model_name: str | None
    mode: str | None
    from_stage: str | None
    to_stage: str | None
    user_input: str | None
    respond_to_message_id: uuid.UUID | None
    error: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_time: datetime
    updated_time: datetime


class ResearchTurnStartResponse(BaseModel):
    """触发新一轮后的响应。"""

    session: ResearchSessionItem
    turn: ResearchTurnItem
    user_message: ResearchMessageItem | None = None
    assistant_message: ResearchMessageItem
    locked_message: ResearchMessageItem | None = Field(
        default=None,
        description="若本轮由表单提交触发，被锁定的旧 assistant 消息（最新状态）",
    )


class ResearchTurnStopResponse(BaseModel):
    """停止轮次的响应。"""

    session_id: uuid.UUID
    turn_id: uuid.UUID
    status: ResearchTurnStatus
    message: str


# ---- 协作（Collaborate Agent）----


class ResearchCollabStartRequest(BaseModel):
    """向常驻协作 agent 发一条消息（answer 问题 / 改 stage 产物）。

    只要**流水线没在跑**（session 非 ``running``）就能发——pending / paused（门控中）
    / 终态皆可。协作是与门控按钮平行的独立通道：agent 只读写 ``stage-NN/`` 产物 +
    答疑，**不推进流水线、不改 session 主状态**。``stage`` 缺省时后端按
    当前门控 stage > ``session.active_stage`` > 0 推断。
    """

    message: str = Field(min_length=1, max_length=20000, description="发给 agent 的消息")
    stage: int | None = Field(
        default=None, description="协作针对的 stage 号；缺省由后端推断",
    )


class ResearchCollabStartResponse(BaseModel):
    """发起协作后的响应：新建的 COLLABORATING turn + 用户消息回显。"""

    session: ResearchSessionItem
    turn: ResearchTurnItem
    user_message: ResearchMessageItem


# 更新前向引用
ResearchSessionDetailResponse.model_rebuild()
ResearchTurnListResponse.model_rebuild()


# ---- 产物 ----


class ResearchArtifactItem(BaseModel):
    """单个产物元数据（不含内容）。"""

    path: str = Field(description="相对 run_dir 的路径")
    kind: Literal[
        "paper_draft",
        "paper_final",
        "figure",
        "code",
        "data",
        "log",
        "state",
        "config",
        "other",
    ] = Field(description="产物类别，前端可据此选渲染方式")
    stage: int | None = Field(default=None, description="来自哪个 ARC 阶段")
    size: int | None = Field(default=None, description="字节数")
    mtime: datetime | None = Field(default=None, description="最后修改时间")
    mime: str | None = Field(default=None)


class ResearchArtifactListResponse(BaseModel):
    """产物列表。"""

    session_id: uuid.UUID
    run_dir: str | None
    items: list[ResearchArtifactItem] = Field(default_factory=list)


class ResearchArtifactWriteRequest(BaseModel):
    """覆写产物文件内容（门控编辑）。"""

    content: str = Field(description="文件新全文（UTF-8）")


class ResearchArtifactWriteResponse(BaseModel):
    """覆写产物文件的结果。"""

    session_id: uuid.UUID
    path: str = Field(description="相对 run_dir 的路径")
    size: int = Field(description="写入后字节数")


class ResearchArtifactTranslateRequest(BaseModel):
    """翻译单个产物文件的请求体（入参对齐 ``ResearchAnalysisGenerateRequest``）。"""

    target_language: Literal["zh", "en"] = Field(
        default="zh", description="目标翻译语言：'zh' 中文，'en' 英文"
    )
    provider_id: str | None = Field(
        default=None,
        max_length=64,
        description="LLM Provider：空/'default' 用用户默认，'mock' 走 mock，或真实 UUID",
    )
    model_name: str | None = Field(
        default=None, max_length=255, description="模型名；为空用 Provider 默认模型"
    )
    force: bool = Field(
        default=False,
        description="强制翻译开关：True 时不读缓存、重新翻译（覆盖同源缓存）",
    )


class ResearchArtifactTranslateResponse(BaseModel):
    """翻译受理响应：``cached`` 带全文；``translating`` 需连 SSE 拉增量。"""

    session_id: uuid.UUID
    path: str = Field(description="相对 run_dir 的路径")
    status: Literal["cached", "translating"]
    source_hash: str = Field(description="源文件内容哈希；SSE 流按此键控")
    target_language: str
    content: str | None = Field(
        default=None, description="命中缓存时的译文全文；translating 时为 None"
    )


class ResearchArtifactTranslateStopResponse(BaseModel):
    """翻译停止响应：``stopped`` 已中断在跑任务；``idle`` 无任务可停。"""

    session_id: uuid.UUID
    source_hash: str = Field(description="源文件内容哈希；SSE 流按此键控")
    target_language: str
    status: Literal["stopped", "idle"]


class ResearchArtifactTreeNode(BaseModel):
    """产物目录树节点。"""

    name: str
    path: str
    is_dir: bool
    size: int | None = None
    mtime: datetime | None = None
    children: list["ResearchArtifactTreeNode"] = Field(default_factory=list)


class ResearchArtifactTreeResponse(BaseModel):
    """产物目录树响应。"""

    session_id: uuid.UUID
    run_dir: str | None
    root: ResearchArtifactTreeNode | None = None


ResearchArtifactTreeNode.model_rebuild()


# ---- generated 解（内容内联） ----


class ResearchGeneratedItem(BaseModel):
    """单个 ``generated/*.json`` 解，内容内联且已剥离大字段。

    剥离策略复用演化任务持久化的
    :data:`app.utils.log_persist.LIST_STRIPPED_GENERATED_FIELDS`
    （``code_artifacts`` / ``generation_meta`` / ``worktree`` / ``description``
    置空），避免整段源码/长文本撑爆响应。
    """

    path: str = Field(description="相对 run_dir 的路径，可直接用于 /artifacts/download")
    name: str = Field(description="文件名")
    stage: int | None = Field(default=None, description="来自哪个 ARC 阶段")
    run_id: str | None = Field(
        default=None,
        description="llm4ad 演化 run 短 id（路径中 generated 的上一级目录名）",
    )
    size: int | None = Field(default=None, description="字节数")
    mtime: datetime | None = Field(default=None, description="最后修改时间")
    data: dict[str, Any] | None = Field(
        default=None,
        description="剥离大字段后的解内容；解析失败为 null",
    )


class ResearchGeneratedStageGroup(BaseModel):
    """按 stage 分组的 generated 解。"""

    stage: int | None = Field(default=None, description="stage 号；无法解析为 null")
    items: list[ResearchGeneratedItem] = Field(default_factory=list)


class ResearchGeneratedResponse(BaseModel):
    """所有 generated 解，内容内联、按 stage 分组。"""

    session_id: uuid.UUID
    run_dir: str | None
    groups: list[ResearchGeneratedStageGroup] = Field(default_factory=list)



# ---- 状态快照 ----


class ResearchStageSnapshot(BaseModel):
    """单个阶段的状态快照。"""

    stage: int
    name: str
    status: Literal[
        "pending", "running", "done", "failed", "skipped", "waiting"
    ]
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None


class ResearchStateResponse(BaseModel):
    """会话当前状态的结构化快照。

    比 SSE 流更"高层"：只有当前阶段号 + 进度、最优个体、最近关键 metrics。
    前端列表页 / 详情页头部展示用。
    """

    session_id: uuid.UUID
    status: ResearchSessionStatus
    active_stage: int | None
    active_stage_name: str | None
    stages: list[ResearchStageSnapshot] = Field(default_factory=list)
    best_objective: float | None = None
    best_code_sha256: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    hypotheses: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


# ---- 结果分析（Analysis）----


class ResearchAnalysisStageItem(BaseModel):
    """结构化聚合中单个阶段（含重跑版本）的耗时/决策快照。

    直接读盘拼装，**不经过 LLM**：``duration_sec`` 取 ``stage_health.json``，
    ``decision``/``next_stage`` 取 ``decision.json``，``versions`` 覆盖回跳产生的
    ``stage-NN_v1/_v2`` 目录（每次重跑一段）。
    """

    stage: int = Field(description="阶段号（1-23）")
    name: str = Field(description="阶段可读名，如 EXPERIMENT_RUN")
    status: str = Field(default="pending", description="done/failed/pending 等")
    decision: str | None = Field(default=None, description="proceed/pivot/refine/degraded")
    next_stage: int | None = Field(default=None)
    duration_sec: float = Field(default=0.0, description="该阶段总耗时（含全部重跑）")
    versions: list[float] = Field(
        default_factory=list, description="每次执行的耗时（秒），按 base/v1/v2 顺序"
    )
    artifacts_count: int = Field(default=0)
    error: str | None = Field(default=None)
    is_gate: bool = Field(default=False, description="是否门控阶段（5/9/20）")


class ResearchAnalysisDecisionItem(BaseModel):
    """一次 pivot/refine 回滚记录（来自 decision_history.json）。"""

    decision: str
    rollback_target: str | None = None
    rollback_stage_num: int | None = None
    attempt: int | None = None
    timestamp: str | None = None


class ResearchAnalysisData(BaseModel):
    """会话结果的结构化聚合（纯读盘、零 LLM），供前端直接渲染分析页。

    各字段对应 ``run_dir`` 下现成文件；缺文件时降级为 None/空，不报错。
    这份数据既独立可用（图表/时间线），也作为 LLM 报告的输入上下文。
    """

    session_id: uuid.UUID
    run_dir: str | None = None
    overview: dict[str, Any] = Field(
        default_factory=dict, description="pipeline_summary.json + checkpoint.json 摘要"
    )
    quality_gate: dict[str, Any] | None = Field(
        default=None, description="degradation_signal.json：score/verdict/weaknesses"
    )
    experiment: dict[str, Any] | None = Field(
        default=None, description="experiment_summary_best.json 指标摘要"
    )
    deliverables: dict[str, Any] | None = Field(
        default=None, description="deliverables/manifest.json"
    )
    decisions: list[ResearchAnalysisDecisionItem] = Field(default_factory=list)
    stages: list[ResearchAnalysisStageItem] = Field(default_factory=list)


class ResearchAnalysisGenerateRequest(BaseModel):
    """触发 LLM 生成结果分析报告的请求体。

    入参对齐 chat 端 ``ReportGenerateRequest``：由用户传入模型与语言。
    ``provider_id``/``model_name`` 为空时回退到用户默认报告模型，再兜底 mock。
    """

    language: Literal["zh", "en"] = Field(
        default="zh", description="报告输出语言：'zh' 中文，'en' 英文"
    )
    provider_id: str | None = Field(
        default=None,
        max_length=64,
        description="LLM Provider：空/'default' 用用户默认，'mock' 走 mock，或真实 UUID",
    )
    model_name: str | None = Field(
        default=None, max_length=255, description="模型名；为空用 Provider 默认模型"
    )
    prompt_template: str | None = Field(
        default=None, description="自定义 user prompt 模板；为空用内置默认模板"
    )


class ResearchAnalysisEntry(BaseModel):
    """存储于 ``session.analysis_report`` 的单条报告条目。"""

    status: Literal["generating", "completed", "failed", "cancelled"]
    content: str | None = None
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    provider_model: str | None = None
    language: str | None = None


class ResearchAnalysisGenerateResponse(BaseModel):
    """触发分析报告生成后的受理响应。"""

    session_id: uuid.UUID
    status: Literal["generating", "completed", "failed", "cancelled"]
    message: str


class ResearchAnalysisDetailResponse(BaseModel):
    """获取分析报告详情：结构化聚合数据 + 最近一次 LLM 报告（可为空）。"""

    session_id: uuid.UUID
    data: ResearchAnalysisData
    report: ResearchAnalysisEntry | None = None


class ResearchAnalysisStopResponse(BaseModel):
    """停止分析报告生成后的响应。"""

    session_id: uuid.UUID
    status: Literal["generating", "completed", "failed", "cancelled"]
    message: str


# ---- 通用 ----


class ResearchDeleteResponse(BaseModel):
    """删除操作的响应（会话 / 文件夹）。"""

    id: uuid.UUID
    message: str = "deleted"
