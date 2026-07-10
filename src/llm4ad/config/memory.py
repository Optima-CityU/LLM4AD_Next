"""Memory configuration schemas.

Defines configuration classes for the memory system including
static memory cards, auto-extraction settings, and prompt integration.
"""

from typing import Any

from pydantic import BaseModel, Field

from llm4ad.config.ui import ui


class MemoryCardConfig(BaseModel):
    """Static memory card defined inline in YAML config.

    Allows users to inject domain knowledge, hints, or constraints
    directly into the memory system via the configuration file.
    """

    type: str = Field(
        ...,
        description="Memory type: good_algorithm, error_reflection, domain_knowledge, general_insight",
        json_schema_extra=ui(
            user_required=True,
            label_zh="记忆类型", label_en="Memory Type",
            desc_zh="记忆卡片类型：good_algorithm、error_reflection、domain_knowledge 或 general_insight",
            desc_en="Memory card type: good_algorithm, error_reflection, domain_knowledge, or general_insight",
        ),
    )
    title: str = Field(
        ..., description="Short human-readable title for this memory card",
        json_schema_extra=ui(
            user_required=True,
            label_zh="标题", label_en="Title",
            desc_zh="记忆卡片的简短标题，便于检索和展示",
            desc_en="Short title for retrieval and display",
        ),
    )
    content: str = Field(
        ..., description="Main textual content of the memory card",
        json_schema_extra=ui(
            user_required=True,
            label_zh="内容", label_en="Content",
            desc_zh="记忆卡片的主要文本内容", desc_en="Main textual content of the memory card",
            multiline=True,
        ),
    )
    enabled: bool = Field(
        default=True,
        description="Whether this memory card can be injected into prompts",
        json_schema_extra=ui(
            label_zh="启用",
            label_en="Enabled",
            desc_zh="是否允许该记忆卡片注入后续提示词",
            desc_en="Whether this memory card can be injected into future prompts",
        ),
    )
    tags: list[str] = Field(
        default_factory=list, description="Tags for filtering and retrieval",
        json_schema_extra=ui(
            label_zh="标签", label_en="Tags",
            desc_zh="用于过滤和检索的标签列表", desc_en="Tags for filtering and retrieval",
        ),
    )
    score: float | None = Field(
        default=None, description="Optional associated score",
        json_schema_extra=ui(
            label_zh="评分", label_en="Score",
            desc_zh="可选的关联评分值", desc_en="Optional associated score value",
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata",
        json_schema_extra=ui(
            hidden=True,
            label_zh="元数据", label_en="Metadata",
            desc_zh="附加的键值对元数据", desc_en="Additional key-value metadata",
        ),
    )


class AutoExtractionConfig(BaseModel):
    """Configuration for LLM-based auto-extraction of memory cards.

    Controls when and how the system automatically extracts insights
    from evaluated algorithms. Supports two extraction paths:
    - Good algorithms: capture what worked and why
    - Bad algorithms: capture what to avoid
    """

    type: str = Field(
        default="llm_card_extractor",
        description="Memory extractor implementation registered name",
        json_schema_extra=ui(
            hidden=True,
            label_zh="提取器类型",
            label_en="Extractor Type",
            desc_zh="记忆自动提取器的注册名称",
            desc_en="Registered memory extractor implementation name",
        ),
    )
    module: str | None = Field(
        default=None,
        description="Optional Python module to import before creating the extractor",
        json_schema_extra=ui(
            hidden=True,
            label_zh="提取器模块",
            label_en="Extractor Module",
            desc_zh="创建提取器前导入的可选 Python 模块",
            desc_en="Optional Python module imported before creating the extractor",
        ),
    )
    enabled: bool = Field(
        default=True, description="Enable auto-extraction after evaluation",
        json_schema_extra=ui(
            label_zh="启用", label_en="Enabled",
            desc_zh="是否在评估后自动提取记忆卡片", desc_en="Whether to auto-extract memory cards after evaluation",
        ),
    )
    max_cards_per_generation: int = Field(
        default=3, ge=0, description="Maximum cards to auto-extract per generation",
        json_schema_extra=ui(
            label_zh="每代最大卡片数", label_en="Max Cards Per Generation",
            desc_zh="每代进化后自动提取的最大记忆卡片数量",
            desc_en="Max memory cards auto-extracted per generation",
        ),
    )
    extraction_temperature: float = Field(
        default=0.3, ge=0.0, le=2.0, description="Temperature for extraction LLM calls",
        json_schema_extra=ui(
            label_zh="提取温度", label_en="Extraction Temperature",
            desc_zh="提取记忆时 LLM 调用的采样温度（0.0-2.0）",
            desc_en="LLM sampling temperature for memory extraction (0.0-2.0)",
        ),
    )

    # Good algorithm extraction
    extract_good: bool = Field(
        default=True, description="Extract insights from well-performing algorithms",
        json_schema_extra=ui(
            label_zh="提取优秀算法", label_en="Extract Good",
            desc_zh="是否从表现优秀的算法中提取经验", desc_en="Whether to extract insights from well-performing algorithms",
        ),
    )
    good_score_threshold: float | None = Field(
        default=None, description="Absolute score threshold for good extraction (None = use relative)",
        json_schema_extra=ui(
            label_zh="优秀评分阈值", label_en="Good Score Threshold",
            desc_zh="优秀算法的绝对评分阈值，留空则使用相对百分位阈值",
            desc_en="Absolute score threshold for good algorithms; empty uses percentile",
        ),
    )
    good_relative_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Percentile threshold: extract from algorithms scoring above this percentile",
        json_schema_extra=ui(
            label_zh="优秀相对阈值", label_en="Good Relative Threshold",
            desc_zh="百分位阈值：从评分高于此百分位的算法中提取（0.0-1.0）",
            desc_en="Extract from algorithms above this percentile (0.0-1.0)",
        ),
    )

    # Bad algorithm extraction
    extract_bad: bool = Field(
        default=True, description="Extract avoidance lessons from poorly-performing algorithms",
        json_schema_extra=ui(
            label_zh="提取失败算法", label_en="Extract Bad",
            desc_zh="是否从表现差的算法中提取教训",
            desc_en="Whether to extract lessons from poorly-performing algorithms",
        ),
    )
    bad_score_threshold: float | None = Field(
        default=None, description="Absolute score threshold for bad extraction (None = use relative)",
        json_schema_extra=ui(
            label_zh="失败评分阈值", label_en="Bad Score Threshold",
            desc_zh="失败算法的绝对评分阈值，留空则使用相对百分位阈值",
            desc_en="Absolute score threshold for bad algorithms; empty uses percentile",
        ),
    )
    bad_relative_threshold: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Percentile threshold: extract from algorithms scoring below this percentile",
        json_schema_extra=ui(
            label_zh="失败相对阈值", label_en="Bad Relative Threshold",
            desc_zh="百分位阈值：从评分低于此百分位的算法中提取（0.0-1.0）",
            desc_en="Extract from algorithms below this percentile (0.0-1.0)",
        ),
    )
    extract_on_failure: bool = Field(
        default=True, description="Extract error reflections from evaluation failures",
        json_schema_extra=ui(
            label_zh="失败时提取", label_en="Extract on Failure",
            desc_zh="评估失败时是否提取错误反思记忆",
            desc_en="Whether to extract error reflections when evaluation fails",
        ),
    )


class MemoryConfig(BaseModel):
    """Memory system configuration."""

    type: str = Field(
        default="local_yaml",
        description="Memory implementation registered name",
        json_schema_extra=ui(
            label_zh="Memory 类型",
            label_en="Memory Type",
            desc_zh="记忆模块注册名称：local_yaml 或 mindmemos_cloud；自定义实现可填写注册名",
            desc_en=(
                "Registered memory implementation: local_yaml or mindmemos_cloud; "
                "custom backends can use their registered name"
            ),
        ),
    )
    module: str | None = Field(
        default=None,
        description="Optional Python module to import before creating memory",
        json_schema_extra=ui(
            hidden=True,
            label_zh="Memory 模块",
            label_en="Memory Module",
            desc_zh="创建记忆模块前导入的可选 Python 模块",
            desc_en="Optional Python module imported before creating memory",
        ),
    )
    max_entries: int = Field(
        default=10000, ge=1, description="Maximum entries",
        json_schema_extra=ui(
            label_zh="最大条目数", label_en="Max Entries",
            desc_zh="记忆系统中允许存储的最大条目数量", desc_en="Maximum number of entries allowed in the memory system",
        ),
    )
    similarity_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Similarity threshold",
        json_schema_extra=ui(
            label_zh="相似度阈值", label_en="Similarity Threshold",
            desc_zh="去重时的相似度阈值，超过此值的记忆将被视为重复（0.0-1.0）",
            desc_en="Dedup threshold; memories above this are duplicates (0.0-1.0)",
        ),
    )
    decay_factor: float = Field(
        default=0.99, ge=0.0, le=1.0, description="Memory decay factor",
        json_schema_extra=ui(
            label_zh="衰减因子", label_en="Decay Factor",
            desc_zh="记忆随时间衰减的因子，值越小衰减越快（0.0-1.0）",
            desc_en="Temporal decay factor; lower values decay faster (0.0-1.0)",
        ),
    )
    enabled: bool = Field(
        default=True,
        description="Whether memory is enabled for this task",
        json_schema_extra=ui(
            label_zh="启用记忆",
            label_en="Enable Memory",
            desc_zh="是否在当前任务中启用记忆系统",
            desc_en="Whether to enable memory for this task",
        ),
    )

    # MindMemOS backend settings
    mindmemos_base_url: str = Field(
        default="",
        description="MindMemOS API base URL",
        json_schema_extra=ui(
            label_zh="MindMemOS 服务地址",
            label_en="MindMemOS Base URL",
            desc_zh="容器内默认使用 http://mindmemos-api:8000",
            desc_en="Use http://mindmemos-api:8000 from Docker containers",
        ),
    )
    mindmemos_api_key: str = Field(
        default="",
        description="MindMemOS API key",
        json_schema_extra=ui(
            label_zh="MindMemOS API Key",
            label_en="MindMemOS API Key",
            desc_zh="MindMemOS api_keys.yaml 中配置的 bearer key",
            desc_en="Bearer key configured in MindMemOS api_keys.yaml",
        ),
    )
    mindmemos_user_id: str = Field(
        default="",
        description="MindMemOS user scope",
        json_schema_extra=ui(
            label_zh="用户级记忆范围",
            label_en="User Scope",
            desc_zh="用于隔离不同用户或租户的记忆",
            desc_en="Memory isolation key for users or tenants",
        ),
    )
    mindmemos_app_id: str = Field(
        default="llm4ad",
        description="MindMemOS app scope",
        json_schema_extra=ui(
            label_zh="应用范围",
            label_en="App Scope",
            desc_zh="默认 llm4ad",
            desc_en="Defaults to llm4ad",
        ),
    )
    mindmemos_agent_id: str = Field(
        default="planner",
        description="MindMemOS agent scope",
        json_schema_extra=ui(
            label_zh="Agent 范围",
            label_en="Agent Scope",
            desc_zh="默认 planner",
            desc_en="Defaults to planner",
        ),
    )
    mindmemos_session_id: str = Field(
        default="",
        description="MindMemOS session/task scope",
        json_schema_extra=ui(
            label_zh="任务级记忆范围",
            label_en="Session Scope",
            desc_zh="建议使用当前 task_id 或 run_id",
            desc_en="Use the current task_id or run_id",
        ),
    )
    mindmemos_project_id: str = Field(
        default="",
        description="Project memory scope stored as metadata filter",
        json_schema_extra=ui(
            label_zh="项目级记忆范围",
            label_en="Project Scope",
            desc_zh="用于项目级长期记忆过滤",
            desc_en="Used as project-level long-term memory filter",
        ),
    )
    mindmemos_search_strategy: str = Field(
        default="fast",
        description="MindMemOS search strategy: fast or agentic",
        json_schema_extra=ui(
            label_zh="检索策略",
            label_en="Search Strategy",
            desc_zh="fast 或 agentic",
            desc_en="fast or agentic",
        ),
    )
    mindmemos_rerank: bool = Field(
        default=False,
        description="Enable MindMemOS rerank for search",
        json_schema_extra=ui(
            label_zh="启用重排",
            label_en="Enable Rerank",
            desc_zh="是否启用 MindMemOS 检索结果重排",
            desc_en="Whether to rerank MindMemOS search results",
        ),
    )
    mindmemos_score_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional MindMemOS search score threshold",
        json_schema_extra=ui(
            label_zh="检索分数阈值",
            label_en="Score Threshold",
            desc_zh="可选，过滤低相关记忆",
            desc_en="Optional threshold for filtering weak memories",
        ),
    )
    mindmemos_fail_open: bool = Field(
        default=True,
        description="Do not interrupt evolution when MindMemOS runtime calls fail",
        json_schema_extra=ui(
            label_zh="失败时不中断任务",
            label_en="Fail Open",
            desc_zh="MindMemOS 运行中不可用时返回空记忆并记录告警",
            desc_en="Return empty memory context and log warnings when MindMemOS is unavailable",
        ),
    )
    mindmemos_sync_static_cards: bool = Field(
        default=False,
        description="Sync static cards to MindMemOS on load",
        json_schema_extra=ui(
            label_zh="同步静态卡片",
            label_en="Sync Static Cards",
            desc_zh="默认关闭，避免重复污染远端记忆",
            desc_en="Disabled by default to avoid duplicating remote memories",
        ),
    )
    mindmemos_allow_remote_clear: bool = Field(
        default=False,
        description="Allow clear() to delete remote memories",
        json_schema_extra=ui(
            hidden=True,
            label_zh="允许远端清理",
            label_en="Allow Remote Clear",
            desc_zh="危险选项，默认关闭",
            desc_en="Dangerous option, disabled by default",
        ),
    )
    include_project_memory: bool = Field(
        default=True,
        description="Include project-scoped memory when building prompt context",
        json_schema_extra=ui(
            label_zh="注入项目级记忆",
            label_en="Include Project Memory",
            desc_zh="是否在当前任务中注入项目级共享记忆",
            desc_en="Whether to include project-scoped shared memory in this task",
        ),
    )
    include_user_memory: bool = Field(
        default=True,
        description="Include user-scoped memory when building prompt context",
        json_schema_extra=ui(
            label_zh="注入用户级记忆",
            label_en="Include User Memory",
            desc_zh="是否在当前任务中注入用户级共享记忆",
            desc_en="Whether to include user-scoped shared memory in this task",
        ),
    )
    include_task_memory: bool = Field(
        default=True,
        description="Include task-scoped memory when building prompt context",
        json_schema_extra=ui(
            label_zh="注入任务级记忆",
            label_en="Include Task Memory",
            desc_zh="是否在当前任务中注入任务级记忆",
            desc_en="Whether to include task-scoped memory in this task",
        ),
    )
    project_memory_limit: int = Field(
        default=5,
        ge=0,
        description="Maximum project-scoped memories to include",
        json_schema_extra=ui(
            label_zh="项目级记忆数量",
            label_en="Project Memory Limit",
            desc_zh="每次提示词中最多注入的项目级记忆数量",
            desc_en="Maximum project-scoped memories injected into each prompt",
        ),
    )
    user_memory_limit: int = Field(
        default=5,
        ge=0,
        description="Maximum user-scoped memories to include",
        json_schema_extra=ui(
            label_zh="用户级记忆数量",
            label_en="User Memory Limit",
            desc_zh="每次提示词中最多注入的用户级记忆数量",
            desc_en="Maximum user-scoped memories injected into each prompt",
        ),
    )
    task_memory_limit: int = Field(
        default=5,
        ge=0,
        description="Maximum task-scoped memories to include",
        json_schema_extra=ui(
            label_zh="任务级记忆数量",
            label_en="Task Memory Limit",
            desc_zh="每次提示词中最多注入的任务级记忆数量",
            desc_en="Maximum task-scoped memories injected into each prompt",
        ),
    )

    # Static memory cards
    static_cards: list[MemoryCardConfig] = Field(
        default_factory=list,
        description="Static memory cards defined inline in config",
        json_schema_extra=ui(
            label_zh="静态记忆卡片", label_en="Static Cards",
            desc_zh="在配置文件中直接定义的静态记忆卡片，用于注入领域知识或约束",
            desc_en="Static memory cards defined inline for domain knowledge or constraints",
        ),
    )

    # Auto-extraction settings
    auto_extraction: AutoExtractionConfig = Field(
        default_factory=AutoExtractionConfig,
        description="Auto-extraction settings for learning from evaluated algorithms",
        json_schema_extra=ui(
            label_zh="自动提取", label_en="Auto Extraction",
            desc_zh="配置从评估结果中自动提取记忆卡片的行为",
            desc_en="Configure automatic memory card extraction from evaluations",
        ),
    )

    # Prompt integration
    max_prompt_cards: int = Field(
        default=5, ge=0, description="Maximum number of memory cards to inject into sampler prompts",
        json_schema_extra=ui(
            label_zh="提示词最大卡片数", label_en="Max Prompt Cards",
            desc_zh="注入采样器提示词的最大记忆卡片数量", desc_en="Maximum number of memory cards injected into sampler prompts",
        ),
    )

    # Persistence
    persist: bool = Field(
        default=True, description="Persist auto-extracted cards to the memory/ directory",
        json_schema_extra=ui(
            label_zh="持久化", label_en="Persist",
            desc_zh="是否将自动提取的记忆卡片持久化到 memory/ 目录",
            desc_en="Whether to persist auto-extracted cards to the memory/ directory",
        ),
    )
