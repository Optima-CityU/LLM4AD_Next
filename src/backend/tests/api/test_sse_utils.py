import pytest

from app.api.llm4ad import sse_utils


class FakeRedis:
    def __init__(self) -> None:
        self.entries = [
            ("1-0", {"data": "old"}),
            ("2-0", {"data": "new"}),
            ("3-0", {"data": "end"}),
        ]

    async def xread(self, streams, count=100, block=1000):
        last_id = next(iter(streams.values()))
        pending = [(entry_id, fields) for entry_id, fields in self.entries if entry_id > last_id]
        return [("task:logs", pending[:count])] if pending else []


def entry_handler(_entry_id: str, fields: dict) -> tuple[str, bool]:
    payload = fields["data"]
    return f"data: {payload}\n\n", payload == "end"


@pytest.mark.asyncio
async def test_redis_sse_stream_resumes_after_last_id_and_emits_sse_ids(monkeypatch):
    """SSE frames should be resumable from a Redis Stream id."""

    async def fake_get_async_redis():
        return FakeRedis()

    monkeypatch.setattr("app.core.redis.get_async_redis", fake_get_async_redis)

    stream = sse_utils.redis_sse_stream(
        redis_key="task:logs",
        connected_data={"task_id": "task-1"},
        entry_handler=entry_handler,
        last_id="1-0",
    )

    frames: list[str] = []
    async for frame in stream:
        frames.append(frame)
        if frame.startswith("event: done"):
            break

    assert not any("data: old" in frame for frame in frames)
    assert any(frame.startswith("id: 2-0\n") and "data: new" in frame for frame in frames)
    assert any(frame.startswith("id: 3-0\n") and "data: end" in frame for frame in frames)
