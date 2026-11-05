"""Bound research model waits and retry before any response is forwarded."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.core.config import settings
from app.services.runtime_health import classify_upstream_error, record_runtime_failure, record_runtime_recovery

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_CANCEL_POLL_INTERVAL_SECONDS = 0.2
CancellationCheck = Callable[[], Awaitable[bool]]


class ResearchRequestCancelled(Exception):
    """A stopped conversation no longer needs this upstream response."""


async def _watch_cancellation(cancelled: CancellationCheck) -> None:
    """Wait for a user stop or downstream disconnect independently of model I/O."""
    while not await cancelled():
        await asyncio.sleep(_CANCEL_POLL_INTERVAL_SECONDS)


async def _until_cancelled[Result](operation: Awaitable[Result], cancelled: CancellationCheck) -> Result:
    """Cancel blocked I/O or retry sleep without recording an upstream failure."""
    pending = asyncio.ensure_future(operation)
    watcher = asyncio.create_task(_watch_cancellation(cancelled))
    try:
        await asyncio.wait({pending, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher.done():
            watcher.result()
            raise ResearchRequestCancelled()
        return pending.result()
    finally:
        for task in (pending, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(pending, watcher, return_exceptions=True)


async def iter_research_response(iterator: AsyncIterator[bytes], cancelled: CancellationCheck) -> AsyncIterator[bytes]:
    """Close an active model stream promptly when its conversation stops."""
    watcher = asyncio.create_task(_watch_cancellation(cancelled))
    pending = None
    try:
        while True:
            pending = asyncio.create_task(anext(iterator))
            await asyncio.wait({pending, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher.done():
                watcher.result()
                raise ResearchRequestCancelled()
            try:
                yield pending.result()
            except StopAsyncIteration:
                return
    finally:
        tasks = [watcher] + ([pending] if pending is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _retry_delay(response: httpx.Response | None, attempt: int) -> float | None:
    """Honor short Retry-After values and surface long cooldowns to the user."""
    delay = settings.PAPER_MODEL_RETRY_DELAY_SECONDS * 2 ** (attempt - 1)
    value = response.headers.get("retry-after") if response is not None else None
    if value:
        try:
            retry_after = float(value)
        except ValueError:
            try:
                retry_after = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                retry_after = delay
        delay = max(delay, retry_after)
    return delay if delay <= settings.PAPER_MODEL_FIRST_RESPONSE_TIMEOUT_SECONDS else None


async def open_research_response(
    client: httpx.AsyncClient,
    build_request: Callable[[], httpx.Request],
    *,
    workspace_id: str,
    session_id: str,
    cancelled: CancellationCheck | None = None,
    request_started_at: float | None = None,
) -> tuple[httpx.Response, AsyncIterator[bytes], bytes | None]:
    """Open and prime a response, retrying only before downstream output.

    Args:
        client: Owned upstream client with a separate stream idle timeout.
        build_request: Builds a replayable request with the same body each time.
        workspace_id: Workspace whose health stream receives attempt progress.
        session_id: Native conversation used for terminal failure notices.
        cancelled: Check for a stopped turn or disconnected downstream.
        request_started_at: Timestamp used to discard late failure reports.

    Returns:
        Upstream response, remaining iterator, and optional already-read chunk.
    """
    operation = _open_research_response(
        client, build_request, workspace_id=workspace_id, session_id=session_id,
        cancelled=cancelled, request_started_at=request_started_at or time.time(),
    )
    if cancelled is None:
        return await operation
    return await _until_cancelled(operation, cancelled)


async def _open_research_response(
    client: httpx.AsyncClient,
    build_request: Callable[[], httpx.Request],
    *,
    workspace_id: str,
    session_id: str,
    cancelled: CancellationCheck | None,
    request_started_at: float,
) -> tuple[httpx.Response, AsyncIterator[bytes], bytes | None]:
    """Retry one live request only while its downstream turn still needs it."""
    maximum = settings.PAPER_MODEL_MAX_RETRIES + 1
    for attempt in range(1, maximum + 1):
        response = None
        iterator = None
        first = None
        error = None
        try:
            async with asyncio.timeout(settings.PAPER_MODEL_FIRST_RESPONSE_TIMEOUT_SECONDS):
                request = build_request()
                response = await client.send(request, stream=True)
                iterator = response.aiter_bytes()
                first = None
                if response.is_success and response.status_code != 204 and request.method != "HEAD":
                    first = await anext(iterator, b"")
                    if not first:
                        raise httpx.RemoteProtocolError("Upstream closed before sending response data")
        except BaseException as exc:
            if response is not None:
                await response.aclose()
            if isinstance(exc, TimeoutError):
                exc = httpx.ReadTimeout(
                    f"No response data within {settings.PAPER_MODEL_FIRST_RESPONSE_TIMEOUT_SECONDS:g}s"
                )
            if not isinstance(exc, httpx.HTTPError):
                raise
            error = exc

        if cancelled is not None and await cancelled():
            if response is not None:
                await response.aclose()
            raise ResearchRequestCancelled()
        if error is None:
            assert response is not None and iterator is not None
        if error is None and response.status_code < 400:
            record_runtime_recovery(workspace_id, session_id)
            return response, iterator, first

        transient = (
            isinstance(error, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))
            if error is not None else response.status_code in _RETRYABLE_STATUS
        )
        delay = _retry_delay(response, attempt) if transient and attempt < maximum else None
        retrying = delay is not None
        status = response.status_code if error is None else None
        record_runtime_failure(
            workspace_id, session_id,
            reason=classify_upstream_error(error, status), status_code=status,
            message=str(error) or type(error).__name__ if error is not None else response.reason_phrase,
            notify=not retrying, attempt=attempt + 1 if retrying else attempt,
            max_attempts=maximum, retrying=retrying, retry_in=delay or 0,
            request_started_at=request_started_at,
        )
        if retrying:
            if response is not None:
                await response.aclose()
            await asyncio.sleep(delay)
            continue
        if error is not None:
            raise error
        assert response is not None and iterator is not None
        return response, iterator, first
    raise AssertionError("Research model attempt budget was not resolved")
