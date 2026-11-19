"""Bound search traffic across independent workspace MCP processes."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")

# Redis time avoids clock skew between workers; expired leases recover crashes.
_ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local next_start = tonumber(redis.call('GET', KEYS[2]) or '0') + tonumber(ARGV[2])
local cooldown = tonumber(redis.call('GET', KEYS[3]) or '0')
local delay = math.max(0, next_start - now, cooldown - now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
    local first = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    delay = math.max(delay, tonumber(first[2]) - now, 0.01)
end
if delay > 0 then return tostring(delay) end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[3]), ARGV[4])
redis.call('EXPIRE', KEYS[1], math.ceil(tonumber(ARGV[3]) * 2))
redis.call('SET', KEYS[2], tostring(now), 'EX', 3600)
return '0'
"""

_COOLDOWN = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local until_time = math.max(tonumber(redis.call('GET', KEYS[1]) or '0'), now + tonumber(ARGV[1]))
redis.call('SET', KEYS[1], tostring(until_time), 'EX', math.ceil(until_time - now) + 60)
return '1'
"""


class RedisSearchGate:
    """Enforce shared concurrency, start spacing, and upstream cooldowns.

    Redis failures propagate instead of falling back to process-local limits.
    Every operation has a deadline shorter than its crash-recovery lease.
    """

    def __init__(
        self,
        client: Any,
        *,
        max_concurrency: int = 2,
        min_interval: float = 1.0,
        wait_timeout: float = 10.0,
        request_timeout: float = 20.0,
        key_prefix: str = "llm4ad:research-search",
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Configure one participant in the platform-wide request gate.

        Args:
            client: Async redis-py client shared by all research workspaces.
            max_concurrency: Maximum active operations across MCP processes.
            min_interval: Minimum seconds between operation starts.
            wait_timeout: Maximum seconds spent waiting for admission.
            request_timeout: Deadline for one admitted operation.
            key_prefix: Common Redis namespace, independent of workspace ID.
            clock: Monotonic clock used for the local admission deadline.
            sleep: Async sleep function, replaceable in tests.
        """
        if max_concurrency < 1 or min_interval < 0 or min(wait_timeout, request_timeout) <= 0:
            raise ValueError("Invalid search coordination limits")
        self.client = client
        self.max_concurrency = max_concurrency
        self.min_interval = min_interval
        self.wait_timeout = wait_timeout
        self.request_timeout = request_timeout
        self.lease_seconds = request_timeout + 5.0
        self.keys = [f"{key_prefix}:{name}" for name in ("leases", "last-started", "cooldown")]
        self.clock = clock
        self.sleep = sleep

    async def cooldown(self, seconds: float) -> None:
        """Extend the shared cooldown without shortening another worker's wait."""
        await self.client.eval(_COOLDOWN, 1, self.keys[2], max(0.0, seconds))

    async def run_async(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run a bounded operation after atomic admission through Redis."""
        token = uuid.uuid4().hex
        admitted = False
        try:
            async with asyncio.timeout(self.wait_timeout):
                deadline = self.clock() + self.wait_timeout
                while True:
                    delay = float(
                        await self.client.eval(
                            _ACQUIRE,
                            3,
                            *self.keys,
                            self.max_concurrency,
                            self.min_interval,
                            self.lease_seconds,
                            token,
                        )
                    )
                    if delay <= 0:
                        admitted = True
                        break
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise TimeoutError("Shared search request gate is busy")
                    await self.sleep(min(delay, 0.2, remaining))
            async with asyncio.timeout(self.request_timeout):
                return await operation()
        finally:
            if admitted:
                # Shield release from cancellation; the lease also bounds a crash.
                await asyncio.shield(self.client.zrem(self.keys[0], token))
