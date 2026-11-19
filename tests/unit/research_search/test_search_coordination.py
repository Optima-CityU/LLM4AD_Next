"""Exercise the actual Redis Lua scripts across independent gate instances."""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
import pytest_asyncio
from app.services.search_coordination import RedisSearchGate


@pytest_asyncio.fixture
async def gates():
    """Use an explicitly supplied disposable Redis, never application Redis."""
    url = os.environ.get("LLM4AD_SEARCH_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set LLM4AD_SEARCH_TEST_REDIS_URL to a disposable Redis to test Lua coordination")
    redis = pytest.importorskip("redis.asyncio")
    async with redis.Redis.from_url(url, decode_responses=True) as client:
        prefix = f"llm4ad:test-search:{uuid.uuid4().hex}"
        yield client, prefix
        keys = [key async for key in client.scan_iter(f"{prefix}:*")]
        if keys:
            await client.delete(*keys)


@pytest.mark.asyncio
async def test_shared_gate_bounds_concurrency_and_start_rate(gates):
    """Separate workspace gates must collectively honor both global bounds."""
    client, prefix = gates
    first = RedisSearchGate(client, key_prefix=prefix, max_concurrency=2, min_interval=0.03)
    second = RedisSearchGate(client, key_prefix=prefix, max_concurrency=2, min_interval=0.03)
    active = 0
    peak = 0
    starts = []

    async def operation():
        nonlocal active, peak
        starts.append(time.monotonic())
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.09)
        active -= 1

    await asyncio.gather(*(gate.run_async(operation) for gate in [first, second] * 3))
    assert 1 < peak <= 2
    assert all(after - before >= 0.025 for before, after in zip(starts, starts[1:], strict=False))
    assert await client.zcard(first.keys[0]) == 0


@pytest.mark.asyncio
async def test_shared_cooldown_never_shortens_an_existing_wait(gates):
    """Publish a throttle from one worker and observe it from another."""
    client, prefix = gates
    first = RedisSearchGate(client, key_prefix=prefix, min_interval=0)
    second = RedisSearchGate(client, key_prefix=prefix, min_interval=0)
    await first.cooldown(0.15)
    await second.cooldown(0.01)
    started = time.monotonic()

    async def operation():
        return "ok"

    assert await second.run_async(operation) == "ok"
    assert time.monotonic() - started >= 0.12


@pytest.mark.asyncio
async def test_operation_timeout_and_cancellation_release_the_shared_slot(gates):
    """A timed-out or cancelled request cannot strand the global semaphore."""
    client, prefix = gates
    gate = RedisSearchGate(client, key_prefix=prefix, min_interval=0, request_timeout=0.03)

    async def slow():
        await asyncio.sleep(10)

    with pytest.raises(TimeoutError):
        await gate.run_async(slow)
    assert await client.zcard(gate.keys[0]) == 0
    admitted = asyncio.Event()

    async def cancelled():
        admitted.set()
        await asyncio.sleep(10)

    task = asyncio.create_task(gate.run_async(cancelled))
    await admitted.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await client.zcard(gate.keys[0]) == 0


@pytest.mark.asyncio
async def test_expired_worker_leases_are_recovered_and_queue_wait_is_bounded(gates):
    """Recover a crashed worker but stop waiting for an occupied live slot."""
    client, prefix = gates
    gate = RedisSearchGate(client, key_prefix=prefix, min_interval=0, max_concurrency=1, wait_timeout=0.04)
    await client.zadd(gate.keys[0], {"crashed": 1})

    async def operation():
        return "ok"

    assert await gate.run_async(operation) == "ok"
    await client.zadd(gate.keys[0], {"busy": time.time() + 60})
    with pytest.raises(TimeoutError):
        await gate.run_async(operation)


@pytest.mark.asyncio
async def test_redis_failure_is_closed():
    """Never replace a failed distributed limit with unlimited local traffic."""

    class UnavailableRedis:
        async def eval(self, *_args):
            raise ConnectionError("Redis unavailable")

    called = False

    async def operation():
        nonlocal called
        called = True

    with pytest.raises(ConnectionError):
        await RedisSearchGate(UnavailableRedis()).run_async(operation)
    assert called is False
