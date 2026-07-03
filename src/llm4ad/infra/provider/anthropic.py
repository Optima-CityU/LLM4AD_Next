"""Anthropic Claude API provider.

Supports official Anthropic Claude API for code generation and chat.
"""

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from llm4ad.infra.provider.base import (
    BaseProvider,
    ChatMessage,
    ContentPart,
    GenerationResult,
    StreamResponse,
    ToolCall,
    ToolDefinition,
)
from llm4ad.infra.timing import ExecutionTiming

# Import Anthropic lazily to avoid dependency issues if not installed
_AsyncAnthropic = None


def _get_async_anthropic():
    global _AsyncAnthropic
    if _AsyncAnthropic is None:
        from anthropic import AsyncAnthropic

        _AsyncAnthropic = AsyncAnthropic
    return _AsyncAnthropic


@BaseProvider.register("anthropic")
class AnthropicProvider(BaseProvider):
    """Anthropic Claude API provider.

    Supports all Claude models from Anthropic's official API, including:
    - claude-3-5-sonnet-latest
    - claude-3-5-haiku-latest
    - claude-3-opus-latest
    """

    # Standard model pricing as of 2024
    PRICING = {
        "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
        "claude-3-5-haiku": {"input": 0.80, "output": 4.0},
        "claude-3-opus": {"input": 15.0, "output": 75.0},
        "claude-3-sonnet": {"input": 3.0, "output": 15.0},
        "claude-3-haiku": {"input": 0.25, "output": 1.25},
    }

    def __init__(self, config: dict[str, Any]):
        """Initialize Anthropic provider.

        Args:
            config: Provider configuration:
                - api_key: API key for authentication
                - base_url: Optional custom base URL for proxy/alternative endpoints
                - model: Model name to use (e.g., "claude-3-5-sonnet-latest")
                - timeout: Request timeout in seconds (default: 60)
                - max_retries: Maximum retry attempts (default: 3)
                - input_cost_per_million: Cost per million input tokens (USD, optional)
                - output_cost_per_million: Cost per million output tokens (USD, optional)
        """
        super().__init__(config)

        # Initialize async Anthropic client.
        # Support both auth styles: ``api_key`` -> ``x-api-key`` header (official
        # API) and ``auth_token`` -> ``Authorization: Bearer`` header (used by some
        # Anthropic-compatible gateways, e.g. DeepSeek's /anthropic endpoint). Pass
        # each only when set so the SDK can resolve a single method without the
        # "Could not resolve authentication method" error.
        auth_token = config.get("auth_token") or ""
        client_kwargs: dict[str, Any] = {
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        if auth_token:
            client_kwargs["auth_token"] = auth_token
        if not self.api_key and not auth_token:
            # Preserve prior behavior (SDK falls back to ANTHROPIC_API_KEY env)
            # rather than silently sending no credential.
            client_kwargs["api_key"] = self.api_key
        if self.base_url is not None:
            client_kwargs["base_url"] = self.base_url

        async_anthropic_cls = _get_async_anthropic()
        self.client = async_anthropic_cls(**client_kwargs)

        # Cost settings - use provided or use standard pricing
        model_key = self._get_model_key()
        self.input_cost_per_million = config.get(
            "input_cost_per_million", self.PRICING.get(model_key, {}).get("input", 0.0)
        )
        self.output_cost_per_million = config.get(
            "output_cost_per_million", self.PRICING.get(model_key, {}).get("output", 0.0)
        )

    def _get_model_key(self) -> str:
        """Extract base model name for pricing lookup."""
        model = self.model.lower()
        if "claude-3-5-sonnet" in model:
            return "claude-3-5-sonnet"
        elif "claude-3-5-haiku" in model:
            return "claude-3-5-haiku"
        elif "claude-3-opus" in model:
            return "claude-3-opus"
        elif "claude-3-sonnet" in model:
            return "claude-3-sonnet"
        elif "claude-3-haiku" in model:
            return "claude-3-haiku"
        return model.split("-")[0] if "-" in model else model

    def _convert_messages(
        self, messages: list[ChatMessage]
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Convert internal ChatMessage format to Anthropic format.

        Args:
            messages: List of ChatMessage objects.

        Returns:
            Tuple of (anthropic_messages, system_message).
        """
        anthropic_messages: list[dict[str, Any]] = []
        system_message: str | None = None

        for msg in messages:
            if msg.role == "system":
                system_message = msg.get_text_content()
            elif msg.role == "assistant" and msg.tool_calls:
                content_blocks: list[dict[str, Any]] = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                anthropic_messages.append({"role": "assistant", "content": content_blocks})
            elif msg.role == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content,
                    }],
                })
            else:
                content = self._convert_content(msg.content)
                anthropic_messages.append({"role": msg.role, "content": content})

        return anthropic_messages, system_message

    @staticmethod
    def _convert_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Convert ToolDefinition list to Anthropic tools format.

        Args:
            tools: List of ToolDefinition objects.

        Returns:
            List of Anthropic-format tool dicts.
        """
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

    async def generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
        **kwargs,
    ) -> GenerationResult:
        """Generate text from a simple prompt."""
        messages = [ChatMessage(role="user", content=prompt)]
        return await self.chat(messages, schema=schema, **kwargs)

    async def generate_stream(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        """Generate streaming text from a simple prompt."""
        messages = [ChatMessage(role="user", content=prompt)]
        stream = await self.chat_stream(messages, **kwargs)
        async for chunk in stream:
            yield chunk

    async def chat(
        self,
        messages: list[ChatMessage],
        schema: type[BaseModel] | None = None,
        tools: list[ToolDefinition] | None = None,
        **kwargs,
    ) -> GenerationResult:
        """Chat with messages.

        Args:
            messages: List of chat messages.
            schema: Optional Pydantic BaseModel to parse the response into.
            tools: Optional list of tool definitions the LLM can call.
            **kwargs: Additional generation parameters.
        """
        start_time = time.time()

        # Pop LLM4AD-internal kwargs that must not reach the Anthropic SDK
        request_stage = kwargs.pop("request_stage", "")

        # Convert internal ChatMessage format to Anthropic format
        anthropic_messages, system_message = self._convert_messages(messages)

        # Build request kwargs
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "stream": False,
            "max_tokens": self.config.get("max_tokens", 32768),
        }
        if system_message is not None:
            request_kwargs["system"] = system_message
        if tools:
            request_kwargs["tools"] = self._convert_tools(tools)

        # Add any user-provided kwargs (temperature, max_tokens, etc.)
        # Note: Remove schema and request_stage from kwargs as they're not direct API parameters
        request_kwargs.update({
            k: v for k, v in kwargs.items() if k not in ("schema", "request_stage")
        })

        # Call API
        response = await self.client.messages.create(**request_kwargs)

        latency_ms = (time.time() - start_time) * 1000

        # Extract usage information
        prompt_tokens = response.usage.input_tokens
        completion_tokens = response.usage.output_tokens
        total_tokens = prompt_tokens + completion_tokens

        # Extract text content and tool calls
        text = ""
        result_tool_calls: list[ToolCall] = []
        for content_block in response.content:
            if content_block.type == "text":
                text += content_block.text
            elif content_block.type == "tool_use":
                result_tool_calls.append(
                    ToolCall(
                        id=content_block.id,
                        name=content_block.name,
                        arguments=content_block.input,
                    )
                )

        # Map stop reason
        finish_reason_map = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
        }
        finish_reason = finish_reason_map.get(response.stop_reason, response.stop_reason or "stop")

        # Parse response if schema was provided
        parsed_result = None
        if schema is not None:
            try:
                parsed_result = schema.model_validate_json(text)
            except Exception:
                parsed_result = None

        # Build result
        return GenerationResult(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=self.estimate_cost(prompt_tokens, completion_tokens),
            latency_ms=latency_ms,
            model=response.model,
            finish_reason=finish_reason,
            request_stage=request_stage,
            metadata={
                "id": response.id,
                "type": response.type,
                "role": response.role,
            },
            parsed=parsed_result,
            timing=ExecutionTiming.from_llm_stage(request_stage, latency_ms),
            tool_calls=result_tool_calls or None,
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        **kwargs,
    ) -> StreamResponse:
        """Chat with streaming response.

        Args:
            messages: List of chat messages.
            tools: Optional list of tool definitions the LLM can call.
            **kwargs: Additional generation parameters.

        Returns:
            StreamResponse that yields text chunks and captures tool calls.
        """
        anthropic_messages, system_message = self._convert_messages(messages)

        # Build request kwargs
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "stream": True,
            "max_tokens": self.config.get("max_tokens", 32768),
        }
        if system_message is not None:
            request_kwargs["system"] = system_message
        if tools:
            request_kwargs["tools"] = self._convert_tools(tools)

        # Add any user-provided kwargs
        request_kwargs.update(kwargs)

        # Call API with streaming enabled
        raw_stream = await self.client.messages.create(**request_kwargs)
        response = StreamResponse()

        async def _generate() -> AsyncIterator[str]:
            current_tool: dict[str, str] | None = None
            async for event in raw_stream:
                if event.type == "content_block_start":
                    if (
                        hasattr(event, "content_block")
                        and hasattr(event.content_block, "type")
                        and event.content_block.type == "tool_use"
                    ):
                        current_tool = {
                            "id": event.content_block.id,
                            "name": event.content_block.name,
                            "input_json": "",
                        }
                elif event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        yield event.delta.text
                    elif hasattr(event.delta, "partial_json") and current_tool is not None:
                        current_tool["input_json"] += event.delta.partial_json
                elif event.type == "content_block_stop" and current_tool is not None:
                    args = (
                        json.loads(current_tool["input_json"])
                        if current_tool["input_json"]
                        else {}
                    )
                    response.tool_calls.append(
                        ToolCall(
                            id=current_tool["id"],
                            name=current_tool["name"],
                            arguments=args,
                        )
                    )
                    current_tool = None

        response._gen = _generate()
        return response

    async def count_tokens(self, text: str) -> int:
        """Count tokens in text using Anthropic's tokenizer."""
        try:
            response = await self.client.count_tokens(text)
            return response
        except Exception:
            # Fallback to simple approximation if counting fails
            return len(text) // 4

    def get_model_info(self) -> dict[str, Any]:
        """Get information about the current model."""
        return {
            "name": self.model,
            "provider": "anthropic",
            "base_url": self.base_url,
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
        }

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost of a request based on configured token prices."""
        input_cost = (input_tokens / 1_000_000) * self.input_cost_per_million
        output_cost = (output_tokens / 1_000_000) * self.output_cost_per_million
        return input_cost + output_cost

    @staticmethod
    def _convert_content(content: str | list[ContentPart]) -> str | list[dict]:
        """Convert ChatMessage content to Anthropic API format.

        Anthropic uses a different image format from OpenAI:
        - Text: ``{"type": "text", "text": "..."}``
        - Image: ``{"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}``

        Args:
            content: Plain text string or list of ContentPart objects.

        Returns:
            String for text-only, or list of dicts for multimodal content.
        """
        if isinstance(content, str):
            return content
        parts: list[dict] = []
        for part in content:
            if part.type == "text" and part.text is not None:
                parts.append({"type": "text", "text": part.text})
            elif part.type == "image_url" and part.image_url is not None:
                url = part.image_url.get("url", "")
                media_type, data = _parse_data_url(url)
                parts.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                })
        return parts


def _parse_data_url(url: str) -> tuple[str, str]:
    """Parse a data URL into media_type and raw base64 data.

    Args:
        url: Data URL in format ``data:<media_type>;base64,<data>``.

    Returns:
        Tuple of (media_type, base64_data). Defaults to ``image/png``
        if the URL cannot be parsed.
    """
    if url.startswith("data:"):
        # data:image/png;base64,iVBOR...
        header, _, data = url.partition(",")
        media_type = header.split(":")[1].split(";")[0] if ":" in header else "image/png"
        return media_type, data
    return "image/png", url


if __name__ == "__main__":
    import asyncio
    import os

    from dotenv import load_dotenv

    # Load environment variables from .env file
    load_dotenv()

    async def main():
        """Main function for debugging the Anthropic provider."""
        # Configuration - modify these values for debugging
        config = {
            "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
            "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
            "timeout": 120,
            "max_retries": 2,
        }

        print(config)

        provider = AnthropicProvider(config)

        # Debug: print provider info
        print("Provider configuration:")
        print(provider.get_model_info())
        print("\n" + "=" * 50 + "\n")

        # Test simple generation
        prompt = "Hello, how are you? Please introduce yourself briefly."
        print(f"Prompt: {prompt}")
        print("\nGenerating response...\n")

        result = await provider.generate(prompt, temperature=0.7, max_tokens=512)

        # Debug: print full result details
        print("Response received:")
        print(f"Text: {result.text}")
        print(
            f"\nUsage: {result.prompt_tokens} input + {result.completion_tokens} "
            f"output = {result.total_tokens} tokens"
        )
        print(f"Cost: ${result.cost_usd:.6f}")
        print(f"Latency: {result.latency_ms:.2f}ms")
        print(f"Finish reason: {result.finish_reason}")
        print(f"Model: {result.model}")

        # Optional: Test streaming
        # print("\n" + "="*50 + "\n")
        # print("Streaming response test:\n")
        # async for chunk in provider.generate_stream("Write a 2 sentence poem about AI:"):
        #     print(chunk, end="", flush=True)
        # print("\n")

    asyncio.run(main())
