"""Exercise the authenticated HTTP boundary used by Docker experiments."""

from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from llm4ad.frontend import codex_bridge
from llm4ad.infra.codex_cli import CodexResult

pytestmark = pytest.mark.unit

TOKEN = "bridge-test-token-" * 3
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.asyncio
async def test_status_requires_auth_and_reports_no_secrets(monkeypatch):
    """Checking login must not invoke inference or disclose authentication data."""
    status = AsyncMock(
        return_value={"installed": True, "authenticated": True, "version": "codex test"}
    )
    monkeypatch.setattr(codex_bridge, "login_status", status)
    async with TestClient(TestServer(codex_bridge.create_app(TOKEN))) as client:
        assert (await client.get("/status")).status == 401
        assert (
            await client.get("/status", headers={"Authorization": "Bearer wrong"})
        ).status == 401
        response = await client.get("/status", headers=HEADERS)
        assert await response.json() == status.return_value
    status.assert_awaited_once_with("codex")


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_openai_completion_uses_cli_default_and_preserves_usage(monkeypatch, stream):
    """Run HTTP requests through the real provider, mocking only the CLI process."""
    call = AsyncMock(
        return_value=CodexResult('{"answer":"OK"}', {"input_tokens": 10, "output_tokens": 4})
    )
    monkeypatch.setattr("llm4ad.infra.provider.codex_cli.run_codex", call)
    async with TestClient(TestServer(codex_bridge.create_app(TOKEN))) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "codex-default",
                "stream": stream,
                "messages": [{"role": "user", "content": "Return OK"}],
                "response_format": {"type": "json_object"},
            },
        )
        assert response.status == 200
        if stream:
            text = await response.text()
            assert "chat.completion.chunk" in text
            assert "data: [DONE]" in text
            assert '"total_tokens": 14' in text
        else:
            body = await response.json()
            assert body["choices"][0]["message"]["content"] == '{"answer":"OK"}'
            assert body["usage"]["total_tokens"] == 14
    assert call.call_args.kwargs["model"] == ""
    assert "Return only a valid JSON object" in call.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"messages": []},
        {"messages": [{"role": "user", "content": "hi"}], "response_format": ["bad"]},
        {"messages": [{"role": "user", "content": "hi"}], "tools": [{"type": "function"}]},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}
                    ],
                }
            ]
        },
    ],
)
async def test_invalid_or_unsupported_requests_fail_before_inference(monkeypatch, payload):
    """Do not silently discard unsupported modalities or tool calls."""
    call = AsyncMock()
    monkeypatch.setattr("llm4ad.infra.provider.codex_cli.run_codex", call)
    async with TestClient(TestServer(codex_bridge.create_app(TOKEN))) as client:
        response = await client.post("/v1/chat/completions", headers=HEADERS, json=payload)
        assert response.status == 400
    call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status"), [(TimeoutError(), 504), (RuntimeError("Usage limit reached"), 503)]
)
async def test_cli_failure_is_not_returned_as_a_success(monkeypatch, error, status):
    """Failed CLI turns must reach the experiment's existing error handling."""
    monkeypatch.setattr("llm4ad.infra.provider.codex_cli.run_codex", AsyncMock(side_effect=error))
    async with TestClient(TestServer(codex_bridge.create_app(TOKEN))) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert response.status == status
        assert "error" in await response.json()
