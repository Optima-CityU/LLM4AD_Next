"""Coder configuration schemas.

Defines configuration classes for different code generation backends:
ClaudeCode, OpenCode CLI, and custom naive LLM coder.
"""

from typing import Literal

from pydantic import BaseModel, Field

from llm4ad.config.ui import ui


class CoderConfig(BaseModel):
    """Base coder configuration with discriminated union for auto-instantiation.

    Uses Pydantic's discriminator feature to automatically instantiate the correct
    concrete config subclass based on the `type` field.
    """

    type: Literal["claude_code", "opencode", "codex_cli", "custom"] = Field(
        default="claude_code", description="Coder type",
        json_schema_extra=ui(
            label_zh="类型", label_en="Type",
            desc_zh="代码生成器类型：claude_code、opencode、codex_cli 或 custom",
            desc_en="Code generator type: claude_code, opencode, codex_cli, or custom",
        ),
    )
    timeout: float = Field(
        default=300.0, gt=0, description="Generation timeout",
        json_schema_extra=ui(
            hidden=True,
            label_zh="超时时间", label_en="Timeout",
            desc_zh="单次代码生成的超时时间（秒）", desc_en="Timeout for a single code generation attempt in seconds",
        ),
    )
    max_retries: int = Field(
        default=2, ge=0, description="Maximum retries",
        json_schema_extra=ui(
            label_zh="最大重试次数", label_en="Max Retries",
            desc_zh="代码生成失败后的最大重试次数", desc_en="Maximum retry attempts after a failed code generation",
        ),
    )
    log_to_console: bool = Field(
        default=False, description="Enable logging to console",
        json_schema_extra=ui(
            hidden=True,
            label_zh="控制台日志", label_en="Log to Console",
            desc_zh="是否将代码生成过程的日志输出到控制台", desc_en="Whether to output code generation logs to the console",
        ),
    )
    provider: str = Field(
        default="default",
        description="Name of the LLM provider to use for code generation",
        json_schema_extra=ui(
            hidden=True,
            label_zh="LLM 提供者", label_en="Provider",
            desc_zh="用于代码生成的 LLM 提供者名称，需在 providers 列表中预先配置",
            desc_en="Name of the LLM provider for code generation; must be defined in the providers list",
        ),
    )


class CodexCLIConfig(CoderConfig):
    """Codex CLI settings; authentication comes from the local ChatGPT login."""

    type: Literal["codex_cli"] = "codex_cli"
    binary_path: str = Field(default="codex", description="Codex executable name or absolute path")
    model: str = Field(default="", description="Optional model override; empty uses the Codex default")
    timeout: float = Field(default=600.0, gt=0, description="CLI turn timeout in seconds")
    provider: str = Field(default="", description="Unused: Codex CLI uses its own ChatGPT login")


class ClaudeCodeConfig(CoderConfig):
    """ClaudeCode coder configuration.

    Configuration specific to the ClaudeCode coder implementation.
    LLM credentials (api_key, auth_token, base_url, model) are resolved
    from the provider referenced by the inherited ``provider`` field.
    """

    type: Literal["claude_code"] = Field(
        default="claude_code", description="Coder type",
        json_schema_extra=ui(
            hidden=True,
            label_zh="类型", label_en="Type",
            desc_zh="代码生成器类型", desc_en="Code generator type",
        ),
    )


class OpenCodeConfig(CoderConfig):
    """OpenCode coder configuration.

    Configuration specific to the OpenCode CLI coder implementation.
    OpenCode is invoked as an external CLI tool in non-interactive mode.
    """

    type: Literal["opencode"] = Field(
        default="opencode", description="Coder type",
        json_schema_extra=ui(
            hidden=True,
            label_zh="类型", label_en="Type",
            desc_zh="代码生成器类型", desc_en="Code generator type",
        ),
    )
    binary_path: str = Field(
        default="opencode", description="Path to the opencode CLI binary",
        json_schema_extra=ui(
            label_zh="二进制路径", label_en="Binary Path",
            desc_zh="OpenCode CLI 可执行文件的路径", desc_en="Path to the OpenCode CLI executable binary",
        ),
    )
    provider_name: str = Field(
        default="",
        description="CLI provider name for OpenCode (e.g. openai, anthropic). If empty, derived from the resolved provider type.",
        json_schema_extra=ui(
            label_zh="CLI 提供者名称", label_en="CLI Provider Name",
            desc_zh="OpenCode CLI 使用的提供者名称（如 openai、anthropic），留空则从 provider 类型自动推导",
            desc_en="OpenCode CLI provider name (e.g. openai, anthropic); if empty, derived from provider type",
        ),
    )


class CustomCoderConfig(CoderConfig):
    """Custom naive coder configuration.

    Configuration specific to the custom naive coder that uses a plain LLM
    provider (no agent/tool-use required) for code generation.
    Supports targeted EVOLVE block replacement and multi-file output.
    """

    type: Literal["custom"] = Field(
        default="custom", description="Coder type",
        json_schema_extra=ui(
            hidden=True,
            label_zh="类型", label_en="Type",
            desc_zh="代码生成器类型", desc_en="Code generator type",
        ),
    )
    prompt_template: str | None = Field(
        default=None,
        description="Custom prompt template for initial generation (overrides default)",
        json_schema_extra=ui(
            label_zh="提示词模板", label_en="Prompt Template",
            desc_zh="自定义初始代码生成的提示词模板，留空使用默认模板",
            desc_en="Custom prompt template for initial generation; empty for default",
            multiline=True,
        ),
    )
    mutation_prompt_template: str | None = Field(
        default=None,
        description="Custom prompt template for mutation (overrides default)",
        json_schema_extra=ui(
            label_zh="变异提示词模板", label_en="Mutation Prompt Template",
            desc_zh="自定义代码变异的提示词模板，留空使用默认模板",
            desc_en="Custom prompt template for mutation; empty for default",
            multiline=True,
        ),
    )
    context_max_tokens: int = Field(
        default=4096,
        gt=0,
        description="Maximum tokens of project context to inject into prompts",
        json_schema_extra=ui(
            hidden=True,
            label_zh="上下文最大令牌数", label_en="Context Max Tokens",
            desc_zh="注入提示词的项目上下文最大令牌数", desc_en="Maximum tokens of project context injected into prompts",
        ),
    )
    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="Sampling temperature for code generation",
        json_schema_extra=ui(
            hidden=True,
            label_zh="采样温度", label_en="Temperature",
            desc_zh="代码生成的采样温度，值越高输出越多样（0.0-2.0）",
            desc_en="Sampling temperature; higher values produce more diverse output",
        ),
    )
    max_gen_tokens: int = Field(
        default=4096, gt=0, description="Maximum tokens for LLM generation output",
        json_schema_extra=ui(
            hidden=True,
            label_zh="最大生成令牌数", label_en="Max Generation Tokens",
            desc_zh="LLM 单次生成输出的最大令牌数", desc_en="Maximum tokens for a single LLM generation output",
        ),
    )
    default_extension: str = Field(
        default="py", description="Default file extension when language is not detected",
        json_schema_extra=ui(
            label_zh="默认扩展名", label_en="Default Extension",
            desc_zh="无法检测语言时使用的默认文件扩展名", desc_en="Default file extension used when language cannot be detected",
        ),
    )
