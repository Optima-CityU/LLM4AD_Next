"""Redis Stream 到 SSE 桥接工具。

提供通用异步生成器，订阅 Redis Stream 并产出 Server-Sent Events，
内置心跳保活、空闲超时、批量追赶和终止事件检测。
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from loguru import logger
from starlette.responses import StreamingResponse


async def redis_sse_stream(
    redis_key: str,
    connected_data: dict[str, Any],
    entry_handler: Callable[[str, dict], tuple[str, bool] | None],
    *,
    last_id: str = "0-0",
    max_idle: float | None = 300.0,
    heartbeat_interval: float = 15.0,
    batch_size: int = 100,
    block_ms: int = 1000,
    use_draining: bool = False,
    xread_timeout: float | None = None,
) -> AsyncIterator[str]:
    """订阅 Redis Stream 并产出 SSE 帧。

    Args:
        redis_key: 要读取的 Redis Stream 键。
        connected_data: 初始 ``connected`` 事件中携带的数据。
        entry_handler: 对每条 Redis Stream 条目的 ID 和 *fields* 字典调用。
            返回 ``(sse_text, is_terminal)`` 表示产出，返回 ``None`` 表示跳过。
            ``sse_text`` 只应包含 ``event:`` / ``data:`` 行——``id:`` 行由本
            生成器统一前置拼接，handler 不要重复输出。
            当 *is_terminal* 为 True 时会额外产出 ``done`` 事件并关闭流。
        last_id: 起始 Redis Stream ID。
        max_idle: 无数据超时秒数，超出后产出 ``timeout`` 事件并关闭。
            传 ``None`` 表示**永不因静默关流**（长任务场景，如演化/研究单阶段
            可能静默几十分钟）；此时仅靠 ``heartbeat`` 保活，终止只由 ``is_terminal``
            事件（done/error）驱动。
        heartbeat_interval: ``heartbeat`` 保活事件的发送间隔（秒）。
        batch_size: 每次 ``XREAD`` 调用的最大条目数。
        block_ms: ``XREAD`` 阻塞等待时间（毫秒）。
        use_draining: 为 True 时，在存在积压数据期间切换为非阻塞读取（追赶模式）。
        xread_timeout: 若设置，使用 ``asyncio.wait_for`` 包裹每次 ``XREAD``（秒）。
    """
    from app.core.redis import get_async_redis

    redis_client = await get_async_redis()

    yield (
        "event: connected\n"
        f"data: {json.dumps(connected_data, ensure_ascii=False)}\n\n"
    )

    now = time.monotonic()
    last_data_time = now
    last_heartbeat_time = now
    draining = False

    while True:
        if max_idle is not None and time.monotonic() - last_data_time > max_idle:
            yield f"event: timeout\ndata: {json.dumps({'reason': 'idle_timeout'})}\n\n"
            return

        effective_block = 0 if (use_draining and draining) else block_ms

        try:
            coro = redis_client.xread(
                {redis_key: last_id}, count=batch_size, block=effective_block
            )
            if xread_timeout is not None:
                results = await asyncio.wait_for(coro, timeout=xread_timeout)
            else:
                results = await coro
        except TimeoutError:
            results = []
        except Exception:
            logger.warning("SSE 读取 Redis Stream 失败，将退避重试", exc_info=True)
            await asyncio.sleep(1.0)
            results = []

        now = time.monotonic()
        fetched = 0

        if results:
            last_data_time = now
            last_heartbeat_time = now
            for _stream_key, entries in results:
                fetched += len(entries)
                for entry_id, fields in entries:
                    last_id = entry_id
                    result = entry_handler(str(entry_id), fields)
                    if result is None:
                        continue
                    sse_text, is_terminal = result
                    yield f"id: {entry_id}\n{sse_text}"
                    if is_terminal:
                        yield f"event: done\ndata: {json.dumps({'status': 'finished'})}\n\n"
                        return

        if use_draining:
            draining = fetched >= batch_size

        if now - last_heartbeat_time >= heartbeat_interval:
            yield f"event: heartbeat\ndata: {json.dumps({'ts': int(now)})}\n\n"
            last_heartbeat_time = now


def sse_response(stream: AsyncIterator[str]) -> StreamingResponse:
    """将异步 SSE 生成器包装为带标准响应头的 StreamingResponse。"""
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
