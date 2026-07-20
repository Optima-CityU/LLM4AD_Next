"""Application-level configuration schemas.

Defines the top-level AppConfig and supporting general-purpose configs
(providers, workspace, logging, repository analyzer).
"""

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from llm4ad.config.coder import ClaudeCodeConfig, CustomCoderConfig, OpenCodeConfig
from llm4ad.config.evaluator import CustomEvaluatorConfig, ExecutableEvaluatorConfig
from llm4ad.config.evolution import DyCAConfig, IslandGAConfig, MCTSAHDConfig, MEoHConfig
from llm4ad.config.memory import MemoryConfig
from llm4ad.config.planner import PlannerConfig
from llm4ad.config.ui import ui
from llm4ad.infra.version_control.config import VersionControlConfig


class ProviderConfig(BaseModel):
    """LLM provider configuration.

    Supports multiple named providers that can be referenced by other
    components (e.g., planner uses one provider, coder uses another).
    """

    name: str = Field(
        default="default",
        description="Unique name for this provider to be referenced by other components",
        json_schema_extra=ui(
            label_zh="提供者名称", label_en="Provider Name",
            desc_zh="用于在其他组件中引用此提供者的唯一名称",
            desc_en="Unique name to reference this provider from other components",
        ),
    )
    type: Literal["openai", "anthropic", "openai_compatible", "mock"] = Field(
        default="openai",
        json_schema_extra=ui(
            label_zh="提供者类型", label_en="Provider Type",
            desc_zh="LLM 服务提供商类型，如 OpenAI、Anthropic 等",
            desc_en="LLM service provider type, e.g. OpenAI, Anthropic",
        ),
    )
    api_key: str = Field(
        default="", description="API key for authentication",
        json_schema_extra=ui(
            user_required=True,
            label_zh="API 密钥", label_en="API Key",
            desc_zh="用于身份验证的 API 密钥，支持 ${ENV_VAR} 环境变量引用",
            desc_en="API key for authentication; supports ${ENV_VAR} expansion",
        ),
    )
    auth_token: str = Field(
        default="", description="Auth token for providers that require it (e.g. Anthropic)",
        json_schema_extra=ui(
            label_zh="认证令牌", label_en="Auth Token",
            desc_zh="部分提供者（如 Anthropic）需要的额外认证令牌",
            desc_en="Additional auth token for some providers (e.g. Anthropic)",
        ),
    )
    base_url: str | None = Field(
        default=None, description="Custom base URL for API",
        json_schema_extra=ui(
            label_zh="API 基础地址", label_en="Base URL",
            desc_zh="自定义 API 端点地址，留空则使用提供者默认地址",
            desc_en="Custom API endpoint URL; leave empty for provider default",
        ),
    )
    model: str = Field(
        default="gpt-4", description="Model to use",
        json_schema_extra=ui(
            user_required=True,
            label_zh="模型", label_en="Model",
            desc_zh="要使用的模型名称，如 gpt-4、claude-3-opus 等", desc_en="Model name to use, e.g. gpt-4, claude-3-opus, etc.",
        ),
    )

    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="Sampling temperature",
        json_schema_extra=ui(
            label_zh="采样温度", label_en="Temperature",
            desc_zh="控制生成随机性，0 为确定性输出，值越高越随机（0.0-2.0）",
            desc_en="Controls randomness; 0 is deterministic, higher is more random",
        ),
    )
    max_tokens: int = Field(
        default=32 * 1024, gt=0, description="Maximum tokens to generate",
        json_schema_extra=ui(
            label_zh="最大令牌数", label_en="Max Tokens",
            desc_zh="单次请求允许生成的最大令牌数量", desc_en="Maximum number of tokens to generate per request",
        ),
    )
    timeout: float = Field(
        default=60.0, gt=0, description="Request timeout in seconds",
        json_schema_extra=ui(
            label_zh="超时时间", label_en="Timeout",
            desc_zh="单次 API 请求的超时时间（秒）", desc_en="Timeout for a single API request in seconds",
        ),
    )
    max_retries: int = Field(
        default=3, ge=0, description="Maximum retry attempts",
        json_schema_extra=ui(
            label_zh="最大重试次数", label_en="Max Retries",
            desc_zh="请求失败后的最大重试次数", desc_en="Maximum number of retry attempts after a failed request",
        ),
    )
    json_mode: Literal["auto", "force", "off"] = Field(
        default="auto",
        description=(
            "Controls response_format={type: json_object} for schema-based "
            "OpenAI-compatible calls. auto enables it for DeepSeek only."
        ),
        json_schema_extra=ui(
            label_zh="JSON 模式",
            label_en="JSON Mode",
            desc_zh="结构化输出请求的 JSON 模式：auto 仅对 DeepSeek 启用，force 强制启用，off 禁用",
            desc_en="JSON mode for structured output: auto enables DeepSeek only, force enables it, off disables it",
        ),
    )


class TaskSpecificConfig(BaseModel):
    """Configuration for a specific task type (text or code)."""
    type: str | None = Field(default=None, description="Provider type for this task")
    model: str | None = Field(default=None, description="Model name for this specific task")
    base_url: str | None = Field(default=None, description="Custom base URL for this task")
    api_key: str | None = Field(default=None, description="API key for this task")
    auth_token: str | None = Field(default=None, description="Auth token for this task")
    timeout: float | None = Field(default=None, gt=0, description="Request timeout in seconds for this task")
    task: str | None = Field(default=None, description="Provider-specific embedding task mode")

class EmbeddingConfig(BaseModel):
    """Embedding provider configuration.

    Supports a single shared embedding model, or task-specific text/code models in local mode.
    """

    type: Literal["openai", "jina", "openai_compatible", "mock", "local"] | None = Field(
        default=None,
        json_schema_extra=ui(
            label_zh="提供者类型",
            label_en="Provider Type",
            desc_zh="嵌入服务提供商类型，'local' 允许为文本和代码任务配置不同的端点",
            desc_en="Provider type; 'local' allows separate configurations for text and code tasks",
        ),
    )

    base_url: str | None = Field(default=None)
    api_key: str = Field(default="")
    auth_token: str = Field(default="")
    model: str = Field(default="")
    text_task: str | None = Field(default=None, description="Provider-specific task mode for text inputs")
    code_task: str | None = Field(default=None, description="Provider-specific task mode for code inputs")
    dim: int = Field(default=3072, ge=1)
    timeout: float = Field(default=60.0, gt=0, description="Embedding request timeout in seconds")
    embedding_func_max_async: int = Field(default=2, ge=1)

    # New fields for 'local' type
    text_config: TaskSpecificConfig | None = Field(
        default=None,
        json_schema_extra=ui(
            label_zh="文本任务配置",
            label_en="Text Task Config",
            desc_zh="当类型为 'local' 时，处理文本（text）任务的专用配置",
            desc_en="Specific configuration for 'text' tasks when type is 'local'",
        ),
    )

    code_config: TaskSpecificConfig | None = Field(
        default=None,
        json_schema_extra=ui(
            label_zh="代码任务配置",
            label_en="Code Task Config",
            desc_zh="当类型为 'local' 时，处理代码（code）任务的专用配置",
            desc_en="Specific configuration for 'code' tasks when type is 'local'",
        ),
    )


class WorkspaceConfig(BaseModel):
    """Workspace configuration for automatic task directory creation.

    When auto_create is enabled, the system automatically creates a directory
    structure at {base_dir}/{project_name}/{run_id}/ with the specified
    subdirectories for different types of outputs.
    """

    auto_create: bool = Field(
        default=True, description="Whether to automatically create the task directory structure",
        json_schema_extra=ui(
            label_zh="自动创建目录", label_en="Auto Create",
            desc_zh="是否在运行时自动创建任务目录结构", desc_en="Automatically create the task directory structure on run",
        ),
    )
    subdirs: dict[str, str] = Field(
        default_factory=lambda: {
            "state": "state",
            "logs": "logs",
            "checkpoints": "checkpoints",
            "generated": "generated",
            "temp": "temp",
            "memory": "memory",
            "best": "best",
        },
        description="Subdirectory names within the task directory",
        json_schema_extra=ui(
            hidden=True,
            label_zh="子目录映射", label_en="Subdirectories",
            desc_zh="任务目录下各子目录的名称映射", desc_en="Name mapping for subdirectories within the task directory",
        ),
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="TRACE", description="Log level",
        json_schema_extra=ui(
            label_zh="日志级别", label_en="Log Level",
            desc_zh="日志输出的最低级别，低于此级别的日志将被忽略",
            desc_en="Minimum log level; messages below this are suppressed",
        ),
    )
    format: str | None = Field(
        default=None, description="Log format (default: loguru format)",
        json_schema_extra=ui(
            hidden=True,
            label_zh="日志格式", label_en="Log Format",
            desc_zh="自定义日志输出格式，留空使用 loguru 默认格式",
            desc_en="Custom log format; leave empty for loguru default",
        ),
    )
    file: str | None = Field(
        default=None, description="Log file path (auto-set by workspace if None)",
        json_schema_extra=ui(
            hidden=True,
            label_zh="日志文件", label_en="Log File",
            desc_zh="日志文件路径，留空由工作空间自动设置", desc_en="Log file path; auto-set by workspace if left empty",
        ),
    )
    console: bool = Field(
        default=True, description="Enable console logging",
        json_schema_extra=ui(
            label_zh="控制台输出", label_en="Console Output",
            desc_zh="是否将日志同时输出到控制台", desc_en="Whether to also output logs to the console",
        ),
    )
    json_format: bool = Field(
        default=False, description="Use JSON format",
        json_schema_extra=ui(
            label_zh="JSON 格式", label_en="JSON Format",
            desc_zh="是否以结构化 JSON 格式输出日志", desc_en="Whether to output logs in structured JSON format",
        ),
    )


class RepoAnalyzerConfig(BaseModel):
    """Repository analyzer configuration.

    Configures how the code repository is analyzed to find EVOLVE blocks
    marked with special comments for evolution.
    """

    type: str = Field(
        default="evolve_detector",
        description="Type of repository analyzer to use (registered name)",
        json_schema_extra=ui(
            hidden=True,
            label_zh="分析器类型", label_en="Analyzer Type",
            desc_zh="仓库分析器的注册名称", desc_en="Registered name of the repository analyzer",
        ),
    )
    context_lines_before: int = Field(
        default=5,
        ge=0,
        description="Number of context lines to capture before each EVOLVE block",
        json_schema_extra=ui(
            label_zh="前置上下文行数", label_en="Context Lines Before",
            desc_zh="在每个 EVOLVE 代码块前捕获的上下文行数", desc_en="Number of context lines captured before each EVOLVE block",
        ),
    )
    context_lines_after: int = Field(
        default=5,
        ge=0,
        description="Number of context lines to capture after each EVOLVE block",
        json_schema_extra=ui(
            label_zh="后置上下文行数", label_en="Context Lines After",
            desc_zh="在每个 EVOLVE 代码块后捕获的上下文行数", desc_en="Number of context lines captured after each EVOLVE block",
        ),
    )
    include: list[str] = Field(
        default_factory=lambda: [
            "*.py",
            "*.cpp",
            "*.cc",
            "*.c",
            "*.h",
            "*.hpp",
            "*.java",
            "*.js",
            "*.ts",
            "*.go",
            "*.rs",
            "*.rb",
            "*.sh",
            "*.php",
            "*.html",
            "*.xml",
        ],
        description="List of glob patterns for files to include in analysis",
        json_schema_extra=ui(
            label_zh="包含文件模式", label_en="Include Patterns",
            desc_zh="分析时包含的文件 glob 模式列表", desc_en="Glob patterns for files to include in analysis",
        ),
    )
    exclude: list[str] = Field(
        default_factory=lambda: [
            ".git/**",
            ".gitignore",
            "__pycache__/**",
            "*.pyc",
            "node_modules/**",
            "build/**",
            "dist/**",
            "*.egg-info/**",
            ".venv/**",
            "venv/**",
            ".idea/**",
            ".vscode/**",
            "*.log",
            "*.tmp",
            "*.bak",
        ],
        description="List of glob patterns for files to exclude from analysis",
        json_schema_extra=ui(
            label_zh="排除文件模式", label_en="Exclude Patterns",
            desc_zh="分析时排除的文件 glob 模式列表", desc_en="Glob patterns for files to exclude from analysis",
        ),
    )

class MultimodalConfig(BaseModel):
    """Configuration for multimodal algorithm design.

    Controls whether algorithm behavior data (images, observations) is
    included in LLM prompts during evolution. When enabled, evaluators
    can return visual behavior data that is embedded in mutation/crossover
    prompts to help the LLM reason about algorithm behavior.
    """

    enabled: bool = Field(
        default=False,
        description="Master switch for multimodal behavior data in prompts",
        json_schema_extra=ui(
            label_zh="启用多模态", label_en="Enabled",
            desc_zh="总开关：是否在提示词中包含算法行为的图像数据",
            desc_en="Master switch for including algorithm behavior images",
        ),
    )
    max_images_per_prompt: int = Field(
        default=3, ge=1,
        description="Maximum number of behavior images to include per LLM call",
        json_schema_extra=ui(
            label_zh="每次最大图片数", label_en="Max Images Per Prompt",
            desc_zh="每次 LLM 调用中最多包含的行为图片数量",
            desc_en="Max behavior images included in each LLM call",
        ),
    )
    image_max_size_kb: int = Field(
        default=512, ge=64,
        description="Maximum image size in KB (images are compressed beyond this for prompts)",
        json_schema_extra=ui(
            label_zh="图片最大尺寸 (KB)", label_en="Max Image Size (KB)",
            desc_zh="图片超过此大小（KB）将被压缩后再嵌入提示词",
            desc_en="Images exceeding this size (KB) are compressed before embedding",
        ),
    )
    two_step_refinement: bool = Field(
        default=False,
        description="Enable two-step refinement: first describe image, then mutate",
        json_schema_extra=ui(
            label_zh="两步精炼", label_en="Two-Step Refinement",
            desc_zh="启用后先让 LLM 描述图像，再基于描述进行变异",
            desc_en="LLM first describes the image, then mutates based on it",
        ),
    )
    include_observation_text: bool = Field(
        default=True,
        description="Include text observations alongside behavior images in prompts",
        json_schema_extra=ui(
            label_zh="包含观察文本", label_en="Include Observation Text",
            desc_zh="在提示词中同时包含行为图像的文本观察描述",
            desc_en="Include text observation descriptions alongside behavior images",
        ),
    )
    behavior_storage: Literal["rendered", "raw", "none"] = Field(
        default="rendered",
        description="Hint for evaluators: how to store behavior data",
        json_schema_extra=ui(
            label_zh="行为数据存储方式", label_en="Behavior Storage",
            desc_zh="评估器存储行为数据的方式：rendered（渲染图）、raw（原始数据）或 none（不存储）",
            desc_en="How evaluators store behavior data: rendered (images), raw (data), or none",
        ),
    )

class AppConfig(BaseModel):
    """Main application configuration.

    This is the top-level configuration class that contains all
    sub-configurations for different components of the system.
    """

    # Component configurations
    providers: list[ProviderConfig] = Field(
        default_factory=list, description="List of configured LLM providers",
        json_schema_extra=ui(
            label_zh="LLM 提供者", label_en="LLM Providers",
            desc_zh="配置一个或多个 LLM 提供者，可被规划器和代码生成器分别引用",
            desc_en="Configure one or more LLM providers for planner and coder",
            hidden=True,
        ),
    )

    embedding: EmbeddingConfig = Field(
        default_factory=EmbeddingConfig,
        description="Embedding provider configuration",
        json_schema_extra=ui(
            label_zh="嵌入提供者", label_en="Embedding Provider",
            desc_zh="配置用于文本嵌入的提供者，如 OpenAI、Jina 等",
            desc_en="Configure the embedding provider (e.g., OpenAI, Jina) for text embeddings",
        ),
    )

    evaluator: CustomEvaluatorConfig | ExecutableEvaluatorConfig = Field(
        discriminator="type", default_factory=CustomEvaluatorConfig,
        description="Evaluator configuration",
        json_schema_extra=ui(
            label_zh="评估器", label_en="Evaluator",
            desc_zh="配置算法评估方式，支持自定义 Python 评估器或可执行文件评估器",
            desc_en="Configure how algorithms are evaluated; supports custom or executable evaluators",
        ),
    )
    evolution: IslandGAConfig | DyCAConfig | MEoHConfig | MCTSAHDConfig = Field(
        default_factory=DyCAConfig,
        discriminator="type",
        json_schema_extra=ui(
            label_zh="进化算法", label_en="Evolution",
            desc_zh="选择并配置进化算法策略：岛屿遗传算法、DyCA 或 MEoH",
            desc_en="Select and configure evolution strategy: Island GA, DyCA, or MEoH",
        ),
    )
    coder: CustomCoderConfig | ClaudeCodeConfig | OpenCodeConfig = Field(
        discriminator="type", default_factory=CustomCoderConfig,
        json_schema_extra=ui(
            label_zh="代码生成器", label_en="Coder",
            desc_zh="配置代码生成方式，支持自定义 LLM、Claude Code 或 OpenCode",
            desc_en="Configure code generation: custom LLM, Claude Code, or OpenCode",
        ),
    )
    memory: MemoryConfig = Field(
        default_factory=MemoryConfig,
        json_schema_extra=ui(
            label_zh="记忆系统", label_en="Memory",
            desc_zh="配置记忆系统，用于存储和检索进化过程中的经验知识",
            desc_en="Configure memory system for storing knowledge during evolution",
            hidden=True,
        ),
    )
    logging: LoggingConfig = Field(
        default_factory=LoggingConfig,
        json_schema_extra=ui(
            label_zh="日志", label_en="Logging",
            desc_zh="配置日志级别、格式和输出方式", desc_en="Configure log level, format, and output destinations",
            hidden=True,
        ),
    )
    workspace: WorkspaceConfig = Field(
        default_factory=WorkspaceConfig, description="Workspace configuration",
        json_schema_extra=ui(
            hidden=True,
            label_zh="工作空间", label_en="Workspace",
            desc_zh="配置任务运行时的目录结构和自动创建行为",
            desc_en="Configure task directory structure and auto-creation",
        ),
    )
    version_control: VersionControlConfig = Field(
        default_factory=VersionControlConfig, description="Version control configuration",
        json_schema_extra=ui(
            label_zh="版本控制", label_en="Version Control",
            desc_zh="配置 Git 版本控制，用于跟踪算法变更和管理隔离工作空间",
            desc_en="Configure Git version control for tracking changes and workspaces",
            hidden=True,
        ),
    )
    multimodal: MultimodalConfig = Field(
        default_factory=MultimodalConfig, description="Multimodal behavior data configuration",
        json_schema_extra=ui(
            label_zh="多模态", label_en="Multimodal",
            desc_zh="配置是否在进化提示词中包含算法行为的图像和观察数据",
            desc_en="Configure algorithm behavior images and observations in prompts",
        ),
    )

    # Repository analysis configuration
    repo_analyzer: dict[str, Any] | None = Field(
        default=None, description="Repository analyzer configuration (find EVOLVE blocks)",
        json_schema_extra=ui(
            hidden=True,
            label_zh="仓库分析器", label_en="Repo Analyzer",
            desc_zh="配置仓库分析器，用于查找代码中的 EVOLVE 标记块",
            desc_en="Configure the repo analyzer for finding EVOLVE-marked blocks",
        ),
    )

    # Planner configuration
    planner: PlannerConfig = Field(
        default_factory=PlannerConfig, description="Planner configuration with samplers",
        json_schema_extra=ui(
            label_zh="规划器", label_en="Planner",
            desc_zh="配置算法设计规划器，包括采样策略和父代选择方式",
            desc_en="Configure the algorithm design planner and sampling strategies",
        ),
    )

    # General settings
    project_name: str = Field(
        default="llm4ad", description="Project name",
        json_schema_extra=ui(
            hidden=True,
            user_required=True,
            label_zh="项目名称", label_en="Project Name",
            desc_zh="项目的唯一标识名称，用于组织运行目录", desc_en="Unique project name used to organize run directories",
        ),
    )
    background: str = Field(
        default="",
        description=(
            "Background information and context for the task "
            "(can be used in prompts)"
        ),
        json_schema_extra=ui(
            label_zh="任务背景", label_en="Background",
            desc_zh="任务的背景信息和上下文描述，可被注入到 LLM 提示词中",
            desc_en="Background info and context for the task; can be injected into LLM prompts",
            multiline=True,
        ),
    )
    run_id: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:8], description="Run identifier",
        json_schema_extra=ui(
            hidden=True,
            label_zh="运行 ID", label_en="Run ID",
            desc_zh="本次运行的唯一标识符，自动生成", desc_en="Unique identifier for this run, auto-generated",
        ),
    )
    random_seed: int = Field(
        default=42, description="Random seed",
        json_schema_extra=ui(
            label_zh="随机种子", label_en="Random Seed",
            desc_zh="全局随机种子，用于保证实验可复现性", desc_en="Global random seed for experiment reproducibility",
        ),
    )

    # Base paths
    base_dir: str = Field(
        default="./runs", description="Base working directory where task directories are created",
        json_schema_extra=ui(
            label_zh="基础目录", label_en="Base Directory",
            desc_zh="任务运行目录的根路径，所有运行结果将存储在此目录下",
            desc_en="Root path for task run directories; all outputs are stored here",
            hidden=True,
        ),
    )
    path_resolution: Literal["config_dir", "cwd"] = Field(
        default="config_dir",
        description="How relative paths in the config are resolved: "
        "'config_dir' resolves relative to the config file's directory, "
        "'cwd' resolves relative to the current working directory.",
        json_schema_extra=ui(
            label_zh="路径解析方式", label_en="Path Resolution",
            desc_zh="配置文件中相对路径的解析基准：'config_dir' 相对配置文件所在目录，"
            "'cwd' 相对执行命令的目录",
            desc_en="Base for resolving relative paths: 'config_dir' relative to config file, "
            "'cwd' relative to working directory",
        ),
    )

    @field_validator("run_id", mode="before")
    @classmethod
    def generate_run_id(cls, v: str | None) -> str | None:
        """Generate a run ID if not provided."""
        if v is None:
            import uuid

            return str(uuid.uuid4())[:8]
        return v

    @model_validator(mode="after")
    def ensure_run_id(self) -> "AppConfig":
        """Ensure run_id is always generated if None."""
        if self.run_id is None:
            import uuid

            object.__setattr__(self, "run_id", str(uuid.uuid4())[:8])
        return self

    @model_validator(mode="after")
    def validate_multimodal_consistency(self) -> "AppConfig":
        """Validate multimodal configuration consistency.

        Check 1: If any sampler name contains 'multimodal', multimodal.enabled must be True.
        Check 4: If behavior_storage is 'raw', warn that renderers must be registered.
        """
        from loguru import logger

        # Check 1: Multimodal samplers require multimodal.enabled=True
        if self.planner and self.planner.samplers:
            multimodal_samplers = [
                s.name for s in self.planner.samplers if "multimodal" in s.name.lower()
            ]
            if multimodal_samplers and not self.multimodal.enabled:
                raise ValueError(
                    f"Multimodal samplers {multimodal_samplers} require "
                    f"multimodal.enabled=true in configuration"
                )

        # Check 4: behavior_storage='raw' requires renderers to be registered
        if self.multimodal.behavior_storage == "raw":
            # Import here to avoid circular dependency
            try:
                from llm4ad.evaluator.renderer import BaseRenderer

                registered_renderers = BaseRenderer.list()
                if not registered_renderers:
                    logger.warning(
                        "behavior_storage is 'raw' but no renderers are registered. "
                        "Ensure renderer modules are imported before running, "
                        "or images cannot be reconstructed from raw data."
                    )
                else:
                    logger.debug(
                        f"behavior_storage is 'raw', registered renderers: {registered_renderers}"
                    )
            except ImportError:
                logger.warning(
                    "Could not check renderer registry. "
                    "Ensure renderers are registered if using behavior_storage='raw'."
                )

        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary.

        Returns:
            Dictionary representation of the configuration
        """
        return self.model_dump()

    def to_json(self, indent: int = 2) -> str:
        """Convert configuration to JSON string.

        Args:
            indent: JSON indentation level

        Returns:
            JSON string representation of the configuration
        """
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        """Create configuration from dictionary.

        Automatically infers the evaluator ``type`` discriminator when it is
        missing: ``"custom"`` if a ``module`` key is present, ``"executable"``
        if an ``executable`` key is present.

        Args:
            data: Dictionary containing configuration values

        Returns:
            AppConfig instance
        """
        evaluator = data.get("evaluator")
        if isinstance(evaluator, dict) and "type" not in evaluator:
            if "module" in evaluator:
                evaluator["type"] = "custom"
            elif "executable" in evaluator:
                evaluator["type"] = "executable"

        return cls.model_validate(data)

    @classmethod
    def from_yaml(cls, path: str, *, use_global_settings: bool = True) -> "AppConfig":
        """Load configuration from YAML file.

        Automatically expands environment variables in the form ${VAR_NAME}.
        Raises KeyError if a referenced environment variable is not set.

        When *use_global_settings* is ``True`` (the default), provider
        definitions from ``~/.llm4ad/settings.yaml`` are merged before
        validation so that task configs can reference providers by name only.

        Args:
            path: Path to YAML file.
            use_global_settings: Whether to merge with global settings.

        Returns:
            AppConfig instance
        """
        from llm4ad.config.settings import (
            load_global_settings,
            load_yaml_with_env_expansion,
            merge_with_global_settings,
        )

        data = load_yaml_with_env_expansion(path)
        if use_global_settings:
            global_data = load_global_settings()
            data = merge_with_global_settings(global_data, data)

        return cls.from_dict(data)

    @classmethod
    def from_json(cls, path: str, *, use_global_settings: bool = True) -> "AppConfig":
        """Load configuration from JSON file.

        Args:
            path: Path to JSON file.
            use_global_settings: Whether to merge with global settings.

        Returns:
            AppConfig instance
        """
        if use_global_settings:
            from llm4ad.config.settings import (
                load_global_settings,
                merge_with_global_settings,
            )

        with open(path, encoding='UTF-8') as f:
            data = json.load(f)

        if use_global_settings:
            global_data = load_global_settings()
            data = merge_with_global_settings(global_data, data)

        return cls.from_dict(data)
