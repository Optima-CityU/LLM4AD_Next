"""Exercise bounded retries without making model or network requests."""

import asyncio
from unittest.mock import Mock

import httpx
import pytest

from app.core.config import settings
from app.services import research_llm_transport as transport


@pytest.fixture
def policy(monkeypatch):
    """Use short real deadlines and isolated health notifications."""
    monkeypatch.setattr(settings, "PAPER_MODEL_FIRST_RESPONSE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(settings, "PAPER_MODEL_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "PAPER_MODEL_RETRY_DELAY_SECONDS", 0)
    failure, recovery = Mock(), Mock()
    monkeypatch.setattr(transport, "record_runtime_failure", failure)
    monkeypatch.setattr(transport, "record_runtime_recovery", recovery)
    return failure, recovery


@pytest.mark.parametrize("stall", ["headers", "body"])
def test_retry_before_first_response_preserves_exact_output(policy, stall):
    """Retry both delayed headers and stalled first bytes without duplicate output."""
    calls = []

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.1)
            yield b"stale response"

    async def handler(request):
        calls.append(request.content)
        if len(calls) == 1:
            if stall == "headers":
                await asyncio.sleep(0.1)
            else:
                return httpx.Response(200, stream=SlowBody())
        return httpx.Response(200, content=b"data: response\n\n")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response, remaining, first = await transport.open_research_response(
                client, lambda: client.build_request("POST", "https://model.invalid", content=b"same payload"),
                workspace_id="workspace", session_id="session",
            )
            body = (first or b"") + b"".join([chunk async for chunk in remaining])
            await response.aclose()
            return body

    assert asyncio.run(exercise()) == b"data: response\n\n"
    assert calls == [b"same payload", b"same payload"]
    first_error = policy[0].call_args_list[0].kwargs
    assert first_error["reason"] == "timeout"
    assert first_error["retrying"] and first_error["attempt"] == 2
    assert first_error["notify"] is False
    policy[1].assert_called_once_with("workspace", "session")


def test_exhausted_retries_report_terminal_failure(policy):
    """Bound one request rather than leaving the SDK to repeat a ten-minute wait."""
    calls = []

    async def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await transport.open_research_response(
                client, lambda: client.build_request("POST", "https://model.invalid"),
                workspace_id="workspace", session_id="session",
            )

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(exercise())
    assert len(calls) == 3
    assert policy[0].call_args.kwargs["attempt"] == 3
    assert policy[0].call_args.kwargs["retrying"] is False
    assert policy[0].call_args.kwargs["notify"] is True
    policy[1].assert_not_called()


@pytest.mark.parametrize(("status", "retry_after", "attempts"), [(503, None, 3), (401, None, 1), (429, "120", 1)])
def test_retryable_statuses_do_not_retry_auth_or_ignore_cooldowns(policy, status, retry_after, attempts):
    """Preserve terminal errors and respect provider cooldowns."""
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"retry-after": retry_after} if retry_after else {}, content=b"provider error")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response, remaining, first = await transport.open_research_response(
                client, lambda: client.build_request("POST", "https://model.invalid"),
                workspace_id="workspace", session_id="session",
            )
            assert response.status_code == status
            assert first is None
            assert b"".join([chunk async for chunk in remaining]) == b"provider error"
            await response.aclose()

    asyncio.run(exercise())
    assert len(calls) == attempts
    assert policy[0].call_args.kwargs["notify"] is True


def test_partial_output_is_not_replayed(policy):
    """A stream failure after output must not duplicate a response or tool request."""
    calls = []

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial tool arguments"
            raise httpx.ReadTimeout("")

    async def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=BrokenBody())

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response, remaining, first = await transport.open_research_response(
                client, lambda: client.build_request("POST", "https://model.invalid"),
                workspace_id="workspace", session_id="session",
            )
            assert first == b"partial tool arguments"
            try:
                with pytest.raises(httpx.ReadTimeout):
                    async for _chunk in remaining:
                        pass
            finally:
                await response.aclose()

    asyncio.run(exercise())
    assert len(calls) == 1
    policy[0].assert_not_called()


def test_cancellation_is_not_retried(policy):
    """User cancellation is not classified as an upstream failure."""
    async def handler(_request):
        raise asyncio.CancelledError()

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await transport.open_research_response(
                client, lambda: client.build_request("POST", "https://model.invalid"),
                workspace_id="workspace", session_id="session",
            )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(exercise())
    policy[0].assert_not_called()


def test_stopped_session_cancels_a_stalled_request_without_notifying(policy, monkeypatch):
    """Stop a request before headers arrive instead of waiting for retry deadlines."""
    monkeypatch.setattr(transport, "_CANCEL_POLL_INTERVAL_SECONDS", 0.001, raising=False)
    monkeypatch.setattr(settings, "PAPER_MODEL_FIRST_RESPONSE_TIMEOUT_SECONDS", 10)

    async def exercise():
        started, closed = asyncio.Event(), asyncio.Event()
        stopped = False

        async def handler(_request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def cancelled():
            return stopped

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = asyncio.create_task(transport.open_research_response(
                client, lambda: client.build_request("POST", "https://model.invalid"),
                workspace_id="workspace", session_id="session", cancelled=cancelled,
            ))
            await asyncio.wait_for(started.wait(), timeout=0.5)
            stopped = True
            with pytest.raises(transport.ResearchRequestCancelled):
                await asyncio.wait_for(request, timeout=0.5)
            assert closed.is_set()

    asyncio.run(exercise())
    policy[0].assert_not_called()
    policy[1].assert_not_called()


def test_stop_interrupts_retry_sleep_without_another_model_call(policy, monkeypatch):
    """A pending backoff must not launch another paid request after a user stop."""
    monkeypatch.setattr(transport, "_CANCEL_POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(settings, "PAPER_MODEL_FIRST_RESPONSE_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(settings, "PAPER_MODEL_RETRY_DELAY_SECONDS", 0.5)
    calls = []

    async def exercise():
        stopped = False

        async def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("Transient failure")

        async def cancelled():
            return stopped

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            operation = asyncio.create_task(transport.open_research_response(
                client, lambda: client.build_request("POST", "https://model.invalid"),
                workspace_id="workspace", session_id="session", cancelled=cancelled,
            ))
            while not policy[0].called:
                await asyncio.sleep(0.001)
            stopped = True
            with pytest.raises(transport.ResearchRequestCancelled):
                await asyncio.wait_for(operation, timeout=0.2)

    asyncio.run(exercise())
    assert len(calls) == 1
    assert policy[0].call_count == 1
    assert policy[0].call_args.kwargs["notify"] is False


def test_stop_closes_a_stream_waiting_for_its_next_chunk(policy, monkeypatch):
    """Cancellation remains effective after response headers and first output."""
    monkeypatch.setattr(transport, "_CANCEL_POLL_INTERVAL_SECONDS", 0.001)

    async def exercise():
        stopped = False
        closed = asyncio.Event()

        async def cancelled():
            return stopped

        async def content():
            try:
                yield b"first"
                await asyncio.Event().wait()
                yield b"late"
            finally:
                closed.set()

        stream = transport.iter_research_response(content(), cancelled)
        assert await anext(stream) == b"first"
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        stopped = True
        with pytest.raises(transport.ResearchRequestCancelled):
            await asyncio.wait_for(pending, timeout=0.2)
        await stream.aclose()
        assert closed.is_set()

    asyncio.run(exercise())
    policy[0].assert_not_called()
