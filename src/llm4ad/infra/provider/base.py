"""Base provider interface for LLM4AD."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from llm4ad.infra.timing import ExecutionTiming
from llm4ad.utils.registry import Registrable


class ProviderType(Enum):
    """Supported LLM provider types."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"
    CODEX_CLI = "codex_cli"


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
    def generate_stream(self, prompt: str, **kwargs) -> AsyncIterator[str]:
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
