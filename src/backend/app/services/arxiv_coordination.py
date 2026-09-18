"""Coordinate arXiv requests across isolated research workspace processes."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")

_DEFAULT_RETRY_AFTER_SECONDS = 60.0
_MAX_BACKOFF_SECONDS = 60.0


def retry_delay_seconds(
    attempt: int,
    retry_after: str | None,
    *,
    jitter: float | None = None,
) -> float:
    """Return an exponential retry delay without violating Retry-After.

    Args:
        attempt: Zero-based retry attempt.
        retry_after: Numeric Retry-After header, when supplied by arXiv.
        jitter: Optional multiplier used by deterministic tests.

    Returns:
        Delay in seconds. Jitter can increase the delay but never shorten a
        server-provided cooldown.
    """
    base_delay = min(2.0 * (2**attempt), _MAX_BACKOFF_SECONDS)
    required_delay = 0.0
    if retry_after:
        try:
            required_delay = max(0.0, float(retry_after))
        except ValueError:
            pass
    delay = max(base_delay, required_delay)
    multiplier = jitter if jitter is not None else random.uniform(1.0, 1.25)
    upper_bound = max(_MAX_BACKOFF_SECONDS, required_delay)
    return float(max(required_delay, min(delay * max(1.0, multiplier), upper_bound)))


def _float_value(value: object) -> float | None:
    """Parse a Redis scalar into a float timestamp."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _rate_limit_cooldown(error: BaseException) -> float | None:
    """Extract a cooldown only from terminal arXiv throttling errors."""
    status_code = getattr(error, "status_code", None)
    if status_code not in {429, 503}:
        return None
    retry_after = getattr(error, "retry_after_seconds", None)
    try:
        return max(_DEFAULT_RETRY_AFTER_SECONDS, float(str(retry_after)))
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_AFTER_SECONDS


class RedisArxivRateLimiter:
    """Serialize arXiv access and share cooldowns through Redis.

    Each research workspace starts its own stdio MCP process, while arXiv
    applies limits to their shared public egress address. This limiter keeps a
    single connection active across those processes and propagates terminal
    429/503 cooldowns to the next workspace.
    """

    def __init__(
        self,
        *,
        sync_client: Any | None = None,
        async_client: Any | None = None,
        key_prefix: str = "llm4ad:arxiv",
        min_interval: float = 3.0,
        lock_timeout: float = 900.0,
        clock: Callable[[], float] = time.time,
        sync_sleep: Callable[[float], None] = time.sleep,
        async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.sync_client = sync_client
        self.async_client = async_client
        self.lock_key = f"{key_prefix}:request-lock"
        self.last_started_key = f"{key_prefix}:last-started"
        self.cooldown_key = f"{key_prefix}:cooldown-until"
        self.min_interval = min_interval
        self.lock_timeout = lock_timeout
        self.clock = clock
        self.sync_sleep = sync_sleep
        self.async_sleep = async_sleep

    def _delay(self, last_started: object, cooldown_until: object) -> float:
        """Calculate the wait required by spacing and shared cooldown."""
        now = self.clock()
        last = _float_value(last_started)
        cooldown = _float_value(cooldown_until)
        next_start = (last + self.min_interval) if last is not None else now
        if cooldown is not None:
            next_start = max(next_start, cooldown)
        return max(0.0, next_start - now)

    def run_sync(self, operation: Callable[[], T]) -> T:
        """Run one blocking request through the distributed request gate."""
        if self.sync_client is None:
            raise RuntimeError("Synchronous Redis client is not configured")
        lock = self.sync_client.lock(
            self.lock_key,
            timeout=self.lock_timeout,
            blocking_timeout=self.lock_timeout,
        )
        if not lock.acquire(blocking=True):
            raise TimeoutError("Timed out waiting for the shared arXiv request gate")
        try:
            delay = self._delay(
                self.sync_client.get(self.last_started_key),
                self.sync_client.get(self.cooldown_key),
            )
            if delay:
                self.sync_sleep(delay)
            self.sync_client.set(self.last_started_key, str(self.clock()), ex=3600)
            try:
                return operation()
            except BaseException as error:
                cooldown = _rate_limit_cooldown(error)
                if cooldown is not None:
                    self.sync_client.set(
                        self.cooldown_key,
                        str(self.clock() + cooldown),
                        ex=max(1, int(cooldown) + 60),
                    )
                raise
        finally:
            lock.release()

    async def run_async(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run one async request through the distributed request gate."""
        if self.async_client is None:
            raise RuntimeError("Asynchronous Redis client is not configured")
        lock = self.async_client.lock(
            self.lock_key,
            timeout=self.lock_timeout,
            blocking_timeout=self.lock_timeout,
        )
        if not await lock.acquire(blocking=True):
            raise TimeoutError("Timed out waiting for the shared arXiv request gate")
        try:
            delay = self._delay(
                await self.async_client.get(self.last_started_key),
                await self.async_client.get(self.cooldown_key),
            )
            if delay:
                await self.async_sleep(delay)
            await self.async_client.set(self.last_started_key, str(self.clock()), ex=3600)
            try:
                return await operation()
            except BaseException as error:
                cooldown = _rate_limit_cooldown(error)
                if cooldown is not None:
                    await self.async_client.set(
                        self.cooldown_key,
                        str(self.clock() + cooldown),
                        ex=max(1, int(cooldown) + 60),
                    )
                raise
        finally:
            await lock.release()
