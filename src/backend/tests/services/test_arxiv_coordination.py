"""Tests for process-independent arXiv request coordination."""

from __future__ import annotations

import asyncio

import pytest

from app.services.arxiv_coordination import RedisArxivRateLimiter, retry_delay_seconds


class _RateLimitError(RuntimeError):
    """Represent the terminal rate-limit exception emitted by the MCP server."""

    status_code = 429
    retry_after_seconds = 60.0


class _AsyncLock:
    """Adapt a shared asyncio lock to the subset used by redis-py."""

    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock

    async def acquire(self, **_kwargs) -> bool:
        await self._lock.acquire()
        return True

    async def release(self) -> None:
        self._lock.release()


class _AsyncRedis:
    """Small in-memory async Redis substitute for limiter tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.shared_lock = asyncio.Lock()

    def lock(self, *_args, **_kwargs) -> _AsyncLock:
        return _AsyncLock(self.shared_lock)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, **_kwargs) -> None:
        self.values[key] = value


def test_retry_delay_never_shortens_retry_after() -> None:
    """Do not let jitter violate the server-provided cooldown."""
    assert retry_delay_seconds(0, "60", jitter=0.5) >= 60.0
    assert retry_delay_seconds(5, "120", jitter=0.5) >= 120.0


@pytest.mark.asyncio
async def test_terminal_rate_limit_delays_the_next_workspace_request() -> None:
    """Share a terminal arXiv cooldown across otherwise independent limiters."""
    redis = _AsyncRedis()
    now = [1_000.0]
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        now[0] += delay

    first = RedisArxivRateLimiter(
        async_client=redis,
        clock=lambda: now[0],
        async_sleep=sleep,
    )
    second = RedisArxivRateLimiter(
        async_client=redis,
        clock=lambda: now[0],
        async_sleep=sleep,
    )

    async def limited() -> None:
        raise _RateLimitError("rate limited")

    with pytest.raises(_RateLimitError):
        await first.run_async(limited)

    called = False

    async def successful() -> str:
        nonlocal called
        called = True
        return "ok"

    assert await second.run_async(successful) == "ok"
    assert called is True
    assert sleeps == [60.0]
