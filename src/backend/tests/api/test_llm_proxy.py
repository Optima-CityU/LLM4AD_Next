"""Credential proxy URL construction tests."""

import pytest

from app.api.llm4ad import llm_proxy


@pytest.mark.parametrize(
    ("base_url", "path", "expected"),
    [
        (
            "https://api.openai.com/v1",
            "v1/chat/completions",
            "https://api.openai.com/v1/chat/completions",
        ),
        (
            "https://gateway.example",
            "v1/chat/completions",
            "https://gateway.example/v1/chat/completions",
        ),
        (
            "https://api.anthropic.com",
            "v1/messages",
            "https://api.anthropic.com/v1/messages",
        ),
        (
            "https://gateway.example/v1/",
            "/chat/completions",
            "https://gateway.example/v1/chat/completions",
        ),
    ],
)
def test_join_upstream_url_avoids_duplicate_api_version(
    base_url: str,
    path: str,
    expected: str,
) -> None:
    assert llm_proxy._join_upstream_url(base_url, path) == expected


@pytest.mark.parametrize("runtime", [False, True])
def test_proxy_retries_only_research_calls_before_output(monkeypatch, runtime):
    """Keep evolution traffic unchanged and prime research streams exactly once."""
    import asyncio
    from unittest.mock import AsyncMock, Mock

    import httpx
    from starlette.requests import Request

    from app.services import research_llm_transport

    requests = []
    factory = httpx.AsyncClient

    async def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("")
        return httpx.Response(200, content=b"data: ok\n\n")

    def client(**kwargs):
        return factory(transport=httpx.MockTransport(handler), **kwargs)

    credentials = {"type": "openai", "base_url": "https://model.invalid/v1", "api_key": "synthetic", "timeout": 600}
    if runtime:
        credentials.update(workspace_id="workspace", session_id="session")
    monkeypatch.setattr(llm_proxy.credential_broker, "resolve_token", lambda _token: credentials)
    monkeypatch.setattr(llm_proxy.httpx, "AsyncClient", client)
    monkeypatch.setattr(llm_proxy, "runtime_request_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(research_llm_transport, "record_runtime_failure", Mock())
    monkeypatch.setattr(research_llm_transport, "record_runtime_recovery", Mock())
    monkeypatch.setattr(llm_proxy.settings, "PAPER_MODEL_RETRY_DELAY_SECONDS", 0)

    async def receive():
        return {"type": "http.request", "body": b'{"stream":true}', "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/", "query_string": b"", "headers": [(b"authorization", b"Bearer opaque")]}, receive)

    async def exercise():
        response = await llm_proxy.proxy_llm("chat/completions", request)
        if runtime:
            assert response.status_code == 200
            assert b"".join([chunk async for chunk in response.body_iterator]) == b"data: ok\n\n"
            await response.background()
        else:
            assert response.status_code == 502

    asyncio.run(exercise())
    assert len(requests) == (2 if runtime else 1)


def test_cancelled_native_request_closes_without_gateway_failure(monkeypatch):
    """A stopped assistant receives no synthetic upstream error or retry."""
    import asyncio
    from unittest.mock import AsyncMock, Mock

    import httpx
    from starlette.requests import Request

    from app.services import research_llm_transport

    factory = httpx.AsyncClient
    clients = []

    async def handler(_request):
        await asyncio.Event().wait()

    def client(**kwargs):
        instance = factory(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(instance)
        return instance

    monkeypatch.setattr(llm_proxy.credential_broker, "resolve_token", lambda _token: {
        "type": "openai", "base_url": "https://model.invalid/v1", "api_key": "synthetic",
        "workspace_id": "workspace", "session_id": "session",
    })
    monkeypatch.setattr(llm_proxy.httpx, "AsyncClient", client)
    monkeypatch.setattr(llm_proxy, "runtime_request_cancelled", AsyncMock(return_value=True))
    failure = Mock()
    monkeypatch.setattr(research_llm_transport, "record_runtime_failure", failure)

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                       "headers": [(b"authorization", b"Bearer opaque")]}, receive)
    response = asyncio.run(llm_proxy.proxy_llm("chat/completions", request))
    assert response.status_code == 499
    assert response.headers["x-should-retry"] == "false"
    assert all(client.is_closed for client in clients)
    failure.assert_not_called()
