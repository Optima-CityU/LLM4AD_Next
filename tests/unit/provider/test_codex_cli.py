"""Subscription CLI transport, provider, and coder regression tests."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from llm4ad.coder.base import BaseCoder, GenerateStatus
from llm4ad.coder.codex_cli import CodexCLI
from llm4ad.config import AppConfig, CodexCLIConfig, ProviderConfig
from llm4ad.infra import codex_cli as transport
from llm4ad.infra.codex_cli import CodexResult
from llm4ad.infra.provider import BaseProvider, ChatMessage, ContentPart, ToolDefinition
from llm4ad.infra.provider.codex_cli import CodexCLIProvider
from pydantic import BaseModel, ValidationError

pytestmark = pytest.mark.unit


class Answer(BaseModel):
    """Schema resembling an algorithm planner response."""

    name: str
    description: str


def test_config_defaults_and_registration():
    """Codex defaults must not inherit the API's gpt-4 model or require a key."""
    config = AppConfig.from_dict(
        {
            "providers": [{"name": "default", "type": "codex_cli"}],
            "coder": {"type": "codex_cli"},
        }
    )
    assert isinstance(config.coder, CodexCLIConfig)
    provider_config = config.providers[0]
    assert provider_config.model == ""
    assert provider_config.timeout == 600
    assert isinstance(
        BaseProvider.create("codex_cli", config=provider_config.model_dump()), CodexCLIProvider
    )
    assert isinstance(BaseCoder.create("codex_cli", config=config.coder), CodexCLI)


@pytest.mark.parametrize("key", ["api_key", "auth_token", "base_url"])
def test_api_credentials_rejected(key):
    """A local-login configuration must not silently consume API credits."""
    with pytest.raises(ValidationError, match="local ChatGPT login"):
        ProviderConfig(type="codex_cli", **{key: "api-value"})


@pytest.mark.asyncio
async def test_runner_preserves_login_and_uses_stdin(monkeypatch, tmp_path):
    """Separate prompts from shell arguments and strip authentication overrides."""
    monkeypatch.setattr(transport.shutil, "which", lambda _: "/bin/codex")
    monkeypatch.setenv("CODEX_HOME", "/tmp/test-codex-home")
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-use")
    monkeypatch.setenv("CODEX_API_KEY", "do-not-use")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "do-not-use")
    calls = []

    async def communicate(command, env, cwd, timeout, prompt=""):
        calls.append((command, env, cwd, prompt))
        if command[1:3] == ["login", "status"]:
            return 0, "", "Logged in using ChatGPT"
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text("final answer")
        schema = Path(command[command.index("--output-schema") + 1])
        assert json.loads(schema.read_text()) == {"type": "object"}
        return (
            0,
            json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 11, "output_tokens": 3}}
            ),
            "",
        )

    monkeypatch.setattr(transport, "_communicate", communicate)
    prompt = '中文 prompt with quotes " and $(touch unwanted)'
    result = await transport.run_codex(
        prompt,
        cwd=tmp_path,
        output_schema={"type": "object"},
        model="chosen-model",
    )
    command, env, cwd, submitted = calls[-1]
    assert submitted == prompt
    assert prompt not in command
    assert command[-1] == "-"
    assert 'forced_login_method="chatgpt"' in command
    assert "read-only" in command
    assert env["CODEX_HOME"] == "/tmp/test-codex-home"
    assert not {"OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"} & env.keys()
    assert os.environ["OPENAI_API_KEY"] == "do-not-use"
    assert result.text == "final answer"
    assert result.usage["input_tokens"] == 11


@pytest.mark.asyncio
async def test_api_login_fails_before_inference(monkeypatch, tmp_path):
    """An API-key login must not trigger a paid inference or a logout."""
    monkeypatch.setattr(transport.shutil, "which", lambda _: "/bin/codex")
    communicate = AsyncMock(return_value=(0, "", "Logged in using an API key"))
    monkeypatch.setattr(transport, "_communicate", communicate)
    with pytest.raises(RuntimeError, match="ChatGPT login"):
        await transport.run_codex("hello", cwd=tmp_path)
    assert communicate.await_count == 1
    assert communicate.call_args.args[0] == ["/bin/codex", "login", "status"]


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "turn.failed", "error": {"message": "Usage limit reached"}}],
        [{"type": "item.completed", "item": {"type": "agent_message", "text": "partial"}}],
        [{"type": "turn.completed"}, {"type": "turn.failed", "error": "failed"}],
    ],
)
def test_failed_or_incomplete_stream_is_not_success(events):
    """Never evaluate a partial candidate as a completed CLI turn."""
    with pytest.raises(RuntimeError, match="did not complete"):
        transport._parse_result("\n".join(json.dumps(event) for event in events), "partial")


def test_recovered_error_and_json_message_fallback():
    """Recoverable CLI errors followed by completion can still succeed."""
    events = [
        {"type": "thread.started", "thread_id": "thread"},
        {"type": "error", "message": "retrying"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 1}},
    ]
    result = transport._parse_result("\n".join(map(json.dumps, events)), "")
    assert result.text == "done"
    assert result.thread_id == "thread"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_stop_process(monkeypatch, tmp_path, cancel):
    """Stop real CLI processes when a task times out or is cancelled."""
    original = asyncio.create_subprocess_exec
    processes = []

    async def create(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    task = asyncio.create_task(
        transport._communicate(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            os.environ.copy(),
            tmp_path,
            60 if cancel else 0.05,
        )
    )
    if cancel:
        while not processes:
            await asyncio.sleep(0.01)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_planner_schema_usage_and_history(monkeypatch):
    """Preserve conversation roles and validate planner output and timing."""
    response = '{"name":"Nearest neighbor","description":"Choose the closest unvisited node."}'
    call = AsyncMock(return_value=CodexResult(response, {"input_tokens": 10, "output_tokens": 4}))
    monkeypatch.setattr("llm4ad.infra.provider.codex_cli.run_codex", call)
    provider = CodexCLIProvider(ProviderConfig(type="codex_cli").model_dump())
    result = await provider.chat(
        [
            ChatMessage(role="system", content="Plan a TSP solver."),
            ChatMessage(role="user", content="Use nearest neighbor."),
        ],
        schema=Answer,
        request_stage="planner",
        temperature=0.7,
        max_tokens=100,
    )
    assert result.parsed.name == "Nearest neighbor"
    assert result.total_tokens == 14
    assert result.timing.llm_planning_ms > 0
    assert '"role": "system"' in call.call_args.args[0]
    schema = call.call_args.kwargs["output_schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["name", "description"]
    assert "temperature" not in call.call_args.kwargs


@pytest.mark.asyncio
async def test_unsupported_capabilities_fail_explicitly():
    """Never silently drop images or external tool definitions."""
    provider = CodexCLIProvider({"model": ""})
    with pytest.raises(NotImplementedError, match="text input"):
        await provider.chat(
            [
                ChatMessage(
                    role="user",
                    content=[
                        ContentPart(
                            type="image_url", image_url={"url": "https://example.com/test.png"}
                        ),
                    ],
                )
            ]
        )
    with pytest.raises(NotImplementedError, match="function-calling"):
        await provider.chat([], tools=[ToolDefinition(name="test", description="", parameters={})])


@pytest.mark.asyncio
async def test_coder_edits_usage_and_noop(monkeypatch, tmp_path):
    """Count actual edited files and reject a successful turn with no changes."""
    source = tmp_path / "algorithm.py"
    source.write_text("return 0")

    async def run(*args, **kwargs):
        assert kwargs["sandbox"] == "workspace-write"
        source.write_text("return 1")
        return CodexResult("Updated algorithm.", {"input_tokens": 50, "output_tokens": 10})

    monkeypatch.setattr("llm4ad.coder.codex_cli.run_codex", run)
    coder = CodexCLI(CodexCLIConfig())
    result = await coder.generate("Improve algorithm.", {}, str(tmp_path))
    assert result.is_success
    assert result.generated_files == ["algorithm.py"]
    assert result.token_used == 60
    result = await coder.generate("Improve algorithm.", {}, str(tmp_path))
    assert result.status == GenerateStatus.FAILED
    assert "no workspace files changed" in result.error_message


@pytest.mark.asyncio
async def test_coder_timeout_status(monkeypatch, tmp_path):
    """Expose timeouts to the experiment's retry and failure handling."""
    monkeypatch.setattr("llm4ad.coder.codex_cli.run_codex", AsyncMock(side_effect=TimeoutError))
    result = await CodexCLI(CodexCLIConfig()).generate("Improve.", {}, str(tmp_path))
    assert result.status == GenerateStatus.TIMEOUT
