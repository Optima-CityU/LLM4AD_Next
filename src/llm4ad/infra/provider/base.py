"""Base provider interface for LLM4AD."""

import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import Enum
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from llm4ad.infra.timing import ExecutionTiming
from llm4ad.utils.registry import Registrable


class ProviderType(Enum):
    """Supported LLM provider types."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


class ToolDefinition(BaseModel):
    """Definition of a tool the LLM can call."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema format


class ToolCall(BaseModel):
    """A tool call from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


class ContentPart(BaseModel):
    """A single content part in a multimodal message.

    Supports text and image_url types following the OpenAI multimodal
    message format.
    """

    type: Literal["text", "image_url"]
    text: str | None = None
    image_url: dict[str, str] | None = None


class ChatMessage(BaseModel):
    """A chat message for chat-based APIs.

    Supports both plain text content (str) and multimodal content
    (list of ContentPart). Plain text is the default for backward
    compatibility.
    """

    role: str  # system, user, assistant, tool
    content: str | list[ContentPart]
    name: str | None = None
    tool_calls: list[ToolCall] | None = None  # Tool calls in assistant messages
    tool_call_id: str | None = None  # Tool call ID for role="tool" messages
    reasoning_content: str | None = None  # DeepSeek thinking-mode reasoning

    def is_multimodal(self) -> bool:
        """Check if this message contains multimodal content."""
        return isinstance(self.content, list)

    def get_text_content(self) -> str:
        """Extract text content regardless of format.

        Returns:
            Combined text from all text parts if multimodal,
            or the plain string content if text-only.
        """
        if isinstance(self.content, str):
            return self.content
        return "\n".join(
            part.text for part in self.content
            if part.type == "text" and part.text is not None
        )


class StreamResponse:
    """Async iterator yielding text chunks while capturing tool calls.

    Use as a drop-in replacement for ``AsyncIterator[str]`` — supports
    ``async for chunk in stream``. After iteration completes,
    ``.tool_calls`` contains any tool calls made by the LLM.
    """

    def __init__(self) -> None:
        """Initialize the stream response."""
        self.tool_calls: list[ToolCall] = []
        self.reasoning_content: str | None = None
        self._gen: AsyncIterator[str] | None = None

    def __aiter__(self) -> "StreamResponse":  # noqa: D105
        return self

    async def __anext__(self) -> str:  # noqa: D105
        if self._gen is None:
            raise StopAsyncIteration
        return await self._gen.__anext__()


class GenerationResult(BaseModel):
    """Result from a generation request."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    model: str = ""
    request_stage: str = ""  # planner, coder, etc.
    timing: ExecutionTiming = Field(default_factory=ExecutionTiming)
    finish_reason: str = "stop"  # stop, length, content_filter, tool_calls
    metadata: dict[str, Any] = Field(default_factory=dict)
    parsed: BaseModel | None = None  # Parsed structured output if schema was provided
    tool_calls: list[ToolCall] | None = None  # Tool calls from the LLM


class ProviderRequestError(RuntimeError):
    """User-facing provider request error with sanitized diagnostics."""


def build_provider_error_message(
    error: BaseException,
    *,
    provider_type: str,
    model: str,
    base_url: str | None = None,
) -> str:
    """Build a concise, actionable error message for provider failures."""
    status_code = _extract_status_code(error)
    raw_text = _extract_error_text(error)
    lower_text = raw_text.lower()
    context = f"provider={provider_type}, model={model or 'unknown'}"
    safe_base_url = _redact_url(base_url)
    if safe_base_url:
        context += f", base_url={safe_base_url}"
    if status_code is not None:
        context += f", status={status_code}"

    is_litellm_endpoint = "litellm" in lower_text or "litellm" in (base_url or "").lower()
    title_zh, title_en, guidance_zh, guidance_en = _classify_provider_error(
        status_code,
        lower_text,
        is_litellm_endpoint=is_litellm_endpoint,
    )
    detail = _truncate(_redact_error_text(raw_text), 600)
    return (
        f"模型请求失败：{title_zh}。{guidance_zh} "
        f"Model request failed: {title_en}. {guidance_en} "
        f"请求信息/Request info: {context}. 上游错误/Upstream error: {detail}"
    )


def _classify_provider_error(
    status_code: int | None,
    lower_text: str,
    *,
    is_litellm_endpoint: bool,
) -> tuple[str, str, str, str]:
    quota_keywords = (
        "insufficient_quota",
        "quota",
        "exceeded your current quota",
        "billing",
        "credit",
        "credits",
        "balance",
        "budget",
        "spend limit",
        "hard limit",
        "soft limit",
        "余额",
        "额度",
        "配额",
        "欠费",
        "超额度",
        "超出额度",
    )
    if any(keyword in lower_text for keyword in quota_keywords):
        guidance_zh = (
            "请检查供应商配置和账号额度，或联系管理员；也可以切换其他供应商/模型、"
            "补充额度、降低并发或减少 max_tokens。"
        )
        guidance_en = (
            "Check the provider configuration and account quota, or contact your administrator. "
            "You can also switch provider/model, top up or increase quota, lower concurrency, "
            "or reduce max_tokens."
        )
        if is_litellm_endpoint:
            guidance_zh += " 如果该服务由 LiteLLM 管理，请管理员检查 LiteLLM key/team 的预算和消费日志。"
            guidance_en += (
                " For LiteLLM-managed endpoints, ask an administrator to check the LiteLLM key/team "
                "budget and spend logs."
            )
        return "额度或预算已用完", "quota or budget is exhausted", guidance_zh, guidance_en

    rate_keywords = ("rate limit", "rate_limit", "too many requests", "rpm", "tpm", "qps")
    if status_code == 429 or any(keyword in lower_text for keyword in rate_keywords):
        guidance_zh = (
            "请检查供应商配置和限流设置，或联系管理员；可以稍后重试、降低并发，"
            "或切换到限额更高的供应商/模型。"
        )
        guidance_en = (
            "Check the provider configuration and rate-limit settings, or contact your administrator. "
            "Retry later, lower concurrency, or switch to a provider/model with a higher rate limit."
        )
        return "请求触发供应商限流", "the provider is rate limiting requests", guidance_zh, guidance_en

    auth_keywords = (
        "unauthorized",
        "forbidden",
        "invalid api key",
        "invalid_api_key",
        "invalid token",
        "permission denied",
        "authentication",
        "api key",
    )
    if status_code in (401, 403) or any(keyword in lower_text for keyword in auth_keywords):
        guidance_zh = (
            "请检查供应商配置、API key/token、base_url、模型访问权限和账号权限，或联系管理员。"
        )
        guidance_en = (
            "Check the provider configuration, API key/token, base_url, model access, "
            "and account permissions, or contact your administrator."
        )
        return "认证或权限校验失败", "authentication or permission check failed", guidance_zh, guidance_en

    context_keywords = (
        "context length",
        "maximum context",
        "token limit",
        "max tokens",
        "too many tokens",
        "context_length_exceeded",
    )
    if any(keyword in lower_text for keyword in context_keywords):
        guidance_zh = (
            "请减少提示词/上下文长度或 max_tokens，或切换到上下文窗口更大的模型；"
            "如果不确定如何调整，请联系管理员检查供应商配置。"
        )
        guidance_en = (
            "Reduce prompt/context size or max_tokens, or switch to a model with a larger context window. "
            "If you are unsure how to adjust this, contact your administrator to check the provider configuration."
        )
        return "请求内容超过模型上下文限制", "the request exceeds the model context limit", guidance_zh, guidance_en

    if status_code == 404 or "model not found" in lower_text or "does not exist" in lower_text:
        guidance_zh = (
            "请检查模型名称、base_url 和供应商路由配置，或联系管理员确认该模型是否可用。"
        )
        guidance_en = (
            "Check the model name, base_url, and provider routing, or contact your administrator "
            "to confirm that the model is available."
        )
        return "模型或接口不存在", "the model or endpoint was not found", guidance_zh, guidance_en

    if status_code in (408, 504) or "timeout" in lower_text or "timed out" in lower_text:
        guidance_zh = (
            "请稍后重试，或检查供应商配置和网络状态；也可以增加 timeout、减少 max_tokens，"
            "或联系管理员排查上游服务。"
        )
        guidance_en = (
            "Retry later, or check provider configuration and network health. You can also increase timeout, "
            "reduce max_tokens, or contact your administrator to inspect the upstream service."
        )
        return "供应商请求超时", "the provider request timed out", guidance_zh, guidance_en

    connection_keywords = (
        "connection error",
        "connection refused",
        "connection reset",
        "connection aborted",
        "all connection attempts failed",
        "connecterror",
        "connect error",
        "connect_tcp",
        "network is unreachable",
        "name or service not known",
        "temporary failure in name resolution",
        "could not resolve",
        "nodename nor servname",
        "getaddrinfo",
        "dns",
    )
    if any(keyword in lower_text for keyword in connection_keywords):
        guidance_zh = (
            "请检查供应商配置、base_url、网络连通性和上游服务状态；如果通过网关或代理访问，"
            "请确认网关/代理服务可达，并联系管理员检查容器网络和上游日志。"
        )
        guidance_en = (
            "Check provider configuration, base_url, network connectivity, and upstream service health. "
            "If the request goes through a gateway or proxy, confirm that it is reachable and contact "
            "your administrator to inspect container networking and upstream logs."
        )
        return "供应商或网关不可达", "the provider or gateway is unreachable", guidance_zh, guidance_en

    if status_code in (500, 502, 503, 529):
        guidance_zh = (
            "请稍后重试，并检查供应商配置或上游服务日志；如果持续失败，请联系管理员。"
        )
        guidance_en = (
            "Retry later and check provider configuration or upstream service logs. "
            "If it keeps failing, contact your administrator."
        )
        return "供应商或代理服务暂时不可用", "the provider or proxy is temporarily unavailable", guidance_zh, guidance_en

    guidance_zh = (
        "请检查供应商配置、模型名称、base_url、API key 和上游服务日志；如果无法确认原因，请联系管理员。"
    )
    guidance_en = (
        "Check provider configuration, model name, base_url, API key, and upstream service logs. "
        "If the cause is unclear, contact your administrator."
    )
    return "原因未明确", "the cause is unclear", guidance_zh, guidance_en


def _extract_status_code(error: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _extract_error_text(error: BaseException) -> str:
    parts: list[str] = []
    body = getattr(error, "body", None)
    if body:
        parts.append(_body_to_text(body))
    response = getattr(error, "response", None)
    if response is not None:
        text = getattr(response, "text", None)
        if text:
            parts.append(str(text))
    error_text = str(error)
    if error_text:
        parts.append(error_text)
    return " | ".join(dict.fromkeys(part for part in parts if part)) or error.__class__.__name__


def _body_to_text(body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or error.get("type")
            if message:
                return str(message)
        message = body.get("message") or body.get("detail")
        if message:
            return str(message)
    return str(body)


def _redact_error_text(text: str) -> str:
    return re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        "***",
        text,
    )


def _redact_url(url: str | None) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return _truncate(url, 120)

    redacted_segments = []
    for segment in parts.path.split("/"):
        if _looks_secret(segment):
            redacted_segments.append("***")
        else:
            redacted_segments.append(segment)
    return urlunsplit((parts.scheme, parts.netloc, "/".join(redacted_segments), "", ""))


def _looks_secret(value: str) -> bool:
    if not value:
        return False
    lower = value.lower()
    return lower.startswith(("sk-", "sk_", "key-", "token-")) or len(value) >= 24


def _truncate(value: str, max_len: int) -> str:
    value = " ".join(value.split())
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


class BaseProvider(Registrable, ABC, registry_name="provider"):
    """Abstract LLM provider interface.

    Defines the common interface for all LLM providers (OpenAI, Anthropic, etc.).
    Implementations should handle authentication, rate limiting, retries, etc.
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize provider with configuration.

        Args:
            config: Provider configuration dict containing:
                - api_key: API key for authentication
                - base_url: Optional custom base URL
                - model: Default model to use
                - timeout: Request timeout in seconds
                - max_retries: Maximum retry attempts
        """
        self.config = config
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url")
        self.model = config.get("model", "")
        self.timeout = config.get("timeout", 600.0)
        self.max_retries = config.get("max_retries", 3)

    @abstractmethod
    async def generate(
        self, prompt: str, schema: type[BaseModel] | None = None, **kwargs
    ) -> GenerationResult:
        """Generate text from a simple prompt.

        This is the simplest interface - just provide a prompt and get text back.

        Args:
            prompt: The prompt text
            schema: Optional Pydantic BaseModel to parse the response into
            **kwargs: Additional generation parameters:
                - temperature: Sampling temperature (0-2)
                - max_tokens: Maximum tokens to generate
                - top_p: Nucleus sampling parameter
                - stop: Stop sequences

        Returns:
            GenerationResult with generated text and metadata. If schema is provided,
            the parsed result will be in the 'parsed' field.
        """
        pass

    @abstractmethod
    async def generate_stream(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        """Generate text with streaming from a simple prompt.

        Args:
            prompt: The prompt text
            **kwargs: Additional generation parameters

        Yields:
            Chunks of generated text as they become available
        """
        pass

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        schema: type[BaseModel] | None = None,
        tools: list[ToolDefinition] | None = None,
        **kwargs,
    ) -> GenerationResult:
        """Chat with messages.

        This interface supports multi-turn conversations with system prompts.

        Args:
            messages: List of chat messages (system, user, assistant, tool).
            schema: Optional Pydantic BaseModel to parse the response into.
            tools: Optional list of tool definitions the LLM can call.
            **kwargs: Additional generation parameters.

        Returns:
            GenerationResult with assistant's response. If schema is provided,
            the parsed result will be in the 'parsed' field. If tools are provided
            and the LLM calls one, 'tool_calls' will be populated.
        """
        pass

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        **kwargs,
    ) -> StreamResponse:
        """Chat with streaming.

        Args:
            messages: List of chat messages.
            tools: Optional list of tool definitions the LLM can call.
            **kwargs: Additional generation parameters.

        Returns:
            StreamResponse that yields text chunks. After iteration,
            ``.tool_calls`` contains any tool calls from the LLM.
        """
        pass

    @abstractmethod
    async def count_tokens(self, text: str) -> int:
        """Count tokens in text.

        Args:
            text: Text to count tokens for

        Returns:
            Number of tokens
        """
        pass

    @abstractmethod
    def get_model_info(self) -> dict[str, Any]:
        """Get information about the current model.

        Returns:
            Dictionary with model information (name, context length, etc.)
        """
        pass

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate the cost of a request.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Estimated cost in USD
        """
        # Base implementation - subclasses should override with model-specific pricing
        return 0.0
