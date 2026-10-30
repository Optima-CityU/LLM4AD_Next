"""ARL 实时事件与历史回填的对账测试。

覆盖两件事：

1. **建连窗口不丢事件**：订阅生效到转发任务启动之间，daemon 的推送必须
   被缓存下来，而不是因为没有回调被丢掉。
2. **按 seq 对账**：已含在历史里的实时事件丢弃，接得上的放行，出现缺口
   或时间线被重建时重拉历史。
"""

from __future__ import annotations

import asyncio

import pytest

from app.api.llm4ad import arl_ws
from app.services.arl.daemon_client import PaseoDaemonClient


class _FakeWebSocket:
    """只记录推送出去的帧。"""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, frame: dict):
        self.sent.append(frame)

    def frames(self, msg_type: str) -> list[dict]:
        return [f for f in self.sent if f.get("type") == msg_type]


class _HistoryClient:
    """``fetch_agent_timeline`` 每次返回一页以 ``end_seq`` 结尾的历史。"""

    def __init__(self, end_seq: int = 10, epoch: str = "e1", fail: bool = False):
        self.end_seq = end_seq
        self.epoch = epoch
        self.fail = fail
        self.fetches = 0

    async def fetch_agent_timeline(self, agent_id, **_kwargs):
        self.fetches += 1
        if self.fail:
            raise RuntimeError("boom")
        return {
            "epoch": self.epoch,
            "endCursor": {"epoch": self.epoch, "seq": self.end_seq},
            "entries": [{"item": {"type": "user_message", "text": "hi"}, "seqEnd": self.end_seq}],
            "agent": {"id": agent_id, "activeTurn": {"id": "t1"}},
        }


def _stream(client, *, epoch="e1", end_seq=10) -> arl_ws._RoleStream:
    stream = arl_ws._RoleStream("worker", client, "a1", asyncio.Queue())
    stream.epoch = epoch
    stream.end_seq = end_seq
    return stream


def _timeline(seq: int, *, epoch: str = "e1", agent_id: str = "a1") -> dict:
    return {
        "type": "agent_stream",
        "payload": {
            "agentId": agent_id,
            "event": {"type": "timeline", "item": {"type": "assistant_message", "text": "x"}},
            "seq": seq,
            "epoch": epoch,
        },
    }


# ---- seq 判定 ----


def test_classify_seq() -> None:
    stream = _stream(_HistoryClient())
    assert arl_ws._classify_seq(stream, "e1", 9) == "drop"
    assert arl_ws._classify_seq(stream, "e1", 10) == "drop"
    assert arl_ws._classify_seq(stream, "e1", 11) == "accept"
    assert arl_ws._classify_seq(stream, "e1", 13) == "resync"
    assert arl_ws._classify_seq(stream, "e2", 1) == "resync"

    stream.reset_cursor()
    # 没有基准时任何事件都是新起点
    assert arl_ws._classify_seq(stream, "e9", 500) == "accept"


def test_end_cursor_falls_back_to_last_entry() -> None:
    assert arl_ws._end_cursor({"endCursor": {"epoch": "e", "seq": 4}}) == {"epoch": "e", "seq": 4}
    assert arl_ws._end_cursor(
        {"epoch": "e", "entries": [{"seqEnd": 2}, {"seqEnd": 7}]}
    ) == {"epoch": "e", "seq": 7}
    assert arl_ws._end_cursor({"epoch": "e", "entries": []}) is None


# ---- 对账 ----


@pytest.mark.asyncio
async def test_events_already_in_history_are_dropped() -> None:
    websocket = _FakeWebSocket()
    stream = _stream(_HistoryClient())

    assert await arl_ws._reconcile_event(websocket, stream, _timeline(10)) is False
    assert await arl_ws._reconcile_event(websocket, stream, _timeline(11)) is True
    assert await arl_ws._reconcile_event(websocket, stream, _timeline(11)) is False
    assert stream.end_seq == 11
    assert websocket.sent == []


@pytest.mark.asyncio
async def test_gap_resyncs_history_once_then_cools_down() -> None:
    websocket = _FakeWebSocket()
    client = _HistoryClient(end_seq=20)
    stream = _stream(client)

    # 10 之后直接来了 15：缺了一段，重拉的历史末尾是 20，15 已含在其中
    assert await arl_ws._reconcile_event(websocket, stream, _timeline(15)) is False
    assert client.fetches == 1
    assert stream.end_seq == 20
    history = websocket.frames("timeline_history")
    assert len(history) == 1
    assert history[0]["data"]["active_turn"] is True

    # 冷却期内再出现缺口：不再重拉，以这条事件为新基准继续
    assert await arl_ws._reconcile_event(websocket, stream, _timeline(30)) is True
    assert client.fetches == 1
    assert stream.end_seq == 30


@pytest.mark.asyncio
async def test_failed_resync_still_lets_the_event_through() -> None:
    """重拉失败时放行事件并以它为基准，避免后续事件全被当成缺口。"""
    websocket = _FakeWebSocket()
    stream = _stream(_HistoryClient(fail=True))

    assert await arl_ws._reconcile_event(websocket, stream, _timeline(15)) is True
    assert stream.end_seq == 15


@pytest.mark.asyncio
async def test_epoch_change_resyncs() -> None:
    websocket = _FakeWebSocket()
    client = _HistoryClient(end_seq=3, epoch="e2")
    stream = _stream(client)

    assert await arl_ws._reconcile_event(websocket, stream, _timeline(3, epoch="e2")) is False
    assert client.fetches == 1
    assert (stream.epoch, stream.end_seq) == ("e2", 3)


@pytest.mark.asyncio
async def test_timeline_replacement_refetches_instead_of_forwarding() -> None:
    websocket = _FakeWebSocket()
    client = _HistoryClient(end_seq=5, epoch="e2")
    stream = _stream(client)
    replacement = {"type": "agent.timeline.replacement", "payload": {"agentId": "a1", "epoch": "e2"}}

    assert await arl_ws._reconcile_event(websocket, stream, replacement) is False
    assert client.fetches == 1
    assert len(websocket.frames("timeline_history")) == 1
    assert (stream.epoch, stream.end_seq) == ("e2", 5)


@pytest.mark.asyncio
async def test_other_agents_and_non_timeline_events_pass_through() -> None:
    websocket = _FakeWebSocket()
    client = _HistoryClient()
    stream = _stream(client)

    # 其他 agent 的事件不参与对账，由前端按 agentId 过滤
    assert await arl_ws._reconcile_event(websocket, stream, _timeline(2, agent_id="other")) is True
    # 回合状态事件没有 seq
    turn = {"type": "agent_stream", "payload": {"agentId": "a1", "event": {"type": "turn_completed"}}}
    assert await arl_ws._reconcile_event(websocket, stream, turn) is True
    assert await arl_ws._reconcile_event(websocket, stream, {"type": "agent_update", "payload": {}}) is True
    assert client.fetches == 0
    assert stream.end_seq == 10


@pytest.mark.asyncio
async def test_sync_history_sets_cursor_and_pushes() -> None:
    websocket = _FakeWebSocket()
    stream = _stream(_HistoryClient(end_seq=42))
    stream.reset_cursor()

    assert await arl_ws._sync_history(websocket, stream) is True
    assert (stream.epoch, stream.end_seq) == ("e1", 42)
    frame = websocket.frames("timeline_history")[0]
    assert frame["role"] == "worker"
    assert frame["data"]["agent_id"] == "a1"


# ---- 建连窗口缓存 ----


async def test_event_queue_buffers_before_consumer_starts() -> None:
    """消费者启动前到达的推送必须留在队列里，而不是被丢弃。"""
    client = PaseoDaemonClient(url="ws://fake/ws", password="pw")
    client._connected = True
    queue = client.open_event_queue()

    event = {"type": "agent_stream", "payload": {"agentId": "a1"}}
    await client._handle_message({"type": "session", "message": event})

    assert queue.qsize() == 1
    received = []
    async for item in client.iter_event_queue(queue):
        received.append(item)
        break
    assert received == [event]

    client.close_event_queue(queue)
    await client._handle_message({"type": "session", "message": event})
    assert queue.qsize() == 0


@pytest.mark.asyncio
async def test_forward_timeline_reconciles_buffered_events() -> None:
    """转发任务从缓存队列消费，并跳过已在历史里的事件。"""
    websocket = _FakeWebSocket()
    client = PaseoDaemonClient(url="ws://fake/ws", password="pw")
    client._connected = True
    queue = client.open_event_queue()
    stream = arl_ws._RoleStream("worker", client, "a1", queue)
    stream.epoch, stream.end_seq = "e1", 10

    for seq in (9, 10, 11, 12):
        queue.put_nowait(_timeline(seq))

    task = asyncio.create_task(arl_ws._forward_timeline(websocket, client, "worker", stream=stream))
    for _ in range(50):
        if len(websocket.frames("timeline_event")) >= 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    seqs = [f["data"]["payload"]["seq"] for f in websocket.frames("timeline_event")]
    assert seqs == [11, 12]
