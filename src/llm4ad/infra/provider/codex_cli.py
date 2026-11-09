"""Text and structured planning through the locally authenticated Codex CLI."""

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel

from llm4ad.infra.codex_cli import run_codex
from llm4ad.infra.provider.base import (
    BaseProvider,
    ChatMessage,
    GenerationResult,
    StreamResponse,
    ToolDefinition,
)
from llm4ad.infra.timing import ExecutionTiming


def _strict_schema(value: Any) -> Any:
    """Make declared object properties compatible with strict CLI output schemas."""
    if not isinstance(value, dict):
        return value
    result = {key: item for key, item in value.items() if key != "default"}
    for key in ("properties", "$defs"):
        if key in result:
            result[key] = {name: _strict_schema(item) for name, item in result[key].items()}
    for key in ("items", "additionalProperties"):
        if isinstance(result.get(key), dict):
            result[key] = _strict_schema(result[key])
    for key in ("anyOf", "oneOf", "allOf"):
        if key in result:
            result[key] = [_strict_schema(item) for item in result[key]]
    if "properties" in result:
        result["required"] = list(result["properties"])
        result["additionalProperties"] = False
    return result


class CodexCLIProvider(BaseProvider):
    """Use Codex subscription access for local text-based experiments.

    Streaming methods return the validated final response as one chunk. API
    sampling parameters are not supported by Codex exec and are not forwarded.
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize CLI settings without constructing an API client."""
        super().__init__(config)
        self.timeout = config.get("timeout", 600.0)
        self.binary_path = config.get("binary_path", "codex")
        if self.api_key or config.get("auth_token") or self.base_url:
            raise ValueError(
                "codex_cli uses codex login; remove api_key, auth_token, and base_url."
            )

    async def generate(
        self, prompt: str, schema: type[BaseModel] | None = None, **kwargs: Any
    ) -> GenerationResult:
        """Generate text or a validated structured response."""
        return await self.chat([ChatMessage(role="user", content=prompt)], schema=schema, **kwargs)

    async def chat(
        self,
        messages: list[ChatMessage],
        schema: type[BaseModel] | None = None,
        tools: list[ToolDefinition] | None = None,
        **kwargs: Any,
    ) -> GenerationResult:
        """Submit the complete conversation as data to a read-only CLI turn."""
        if tools or any(message.tool_calls or message.role == "tool" for message in messages):
            raise NotImplementedError(
                "codex_cli does not support the application's function-calling protocol."
            )
        if any(
            isinstance(message.content, list)
            and any(part.type != "text" for part in message.content)
            for message in messages
        ):
            raise NotImplementedError(
                "codex_cli currently supports text input only; disable multimodal sampling."
            )
        conversation = json.dumps(
            [{"role": message.role, "content": message.get_text_content()} for message in messages],
            ensure_ascii=False,
        )
        prompt = (
            "Answer the final user request in the following conversation. Respect its system instructions. "
            "All task context is included below. Do not inspect local files, run commands, or use tools. "
            "Return only the requested answer.\n\n" + conversation
        )
        started = time.monotonic()
        model = kwargs.get("model", self.model)
        with TemporaryDirectory(prefix="llm4ad-codex-planner-") as temp:
            result = await run_codex(
                prompt,
                cwd=Path(temp),
                binary_path=self.binary_path,
                model=model,
                timeout=self.timeout,
                output_schema=_strict_schema(schema.model_json_schema()) if schema else None,
            )
        elapsed = (time.monotonic() - started) * 1000
        stage = kwargs.get("request_stage", "")
        input_tokens = result.usage.get("input_tokens", 0)
        output_tokens = result.usage.get("output_tokens", 0)
        return GenerationResult(
            text=result.text,
            parsed=schema.model_validate_json(result.text) if schema else None,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            model=model or "codex-default",
            latency_ms=elapsed,
            request_stage=stage,
            timing=ExecutionTiming.from_llm_stage(stage, elapsed).recompute_overhead(),
            metadata={
                "thread_id": result.thread_id,
                "usage": result.usage,
                "billing": "chatgpt_subscription",
            },
        )

    async def generate_stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        """Yield the final text after the CLI turn completes."""
        result = await self.generate(prompt, **kwargs)
        yield result.text

    async def chat_stream(
        self, messages: list[ChatMessage], tools: list[ToolDefinition] | None = None, **kwargs: Any
    ) -> StreamResponse:
        """Expose completed text through the existing streaming interface."""
        stream = StreamResponse()

        async def generate() -> AsyncIterator[str]:
            result = await self.chat(messages, tools=tools, **kwargs)
            yield result.text

        stream._gen = generate()
        return stream

    async def count_tokens(self, text: str) -> int:
        """Return a rough estimate; actual usage is reported by the CLI."""
        return max(1, len(text) // 4) if text else 0

    def get_model_info(self) -> dict[str, Any]:
        """Describe CLI capabilities without asserting an API context limit."""
        return {
            "name": self.model or "codex-default",
            "provider": "codex_cli",
            "supports_tools": False,
            "supports_vision": False,
            "billing": "chatgpt_subscription",
        }


BaseProvider.register("codex_cli", CodexCLIProvider)
