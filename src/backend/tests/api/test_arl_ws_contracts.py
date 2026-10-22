"""ARL WebSocket 端点契约测试。

重点是两条：

1. **断连收摊**：前端断开后处理器必须返回并关闭两条 daemon 连接。原先用
   ``asyncio.gather`` 等所有转发任务，而 timeline 任务的循环条件是 daemon
   侧状态（与前端无关），于是永不退出——处理器挂住、daemon 连接泄漏。
2. **单条消息的失败隔离**：畸形帧 / 缺字段 / daemon 报错只回 error 帧，
   不得拆掉整个会话。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import WebSocketDisconnect

from app.api.llm4ad import arl_ws


class _FakeWebSocket:
    """按脚本回放前端消息，并记录推送出去的帧。"""

    def __init__(self, incoming: list[dict | Exception]):
        self._incoming = list(incoming)
        self.sent: list[dict] = []

    async def receive_json(self):
        if not self._incoming:
            raise WebSocketDisconnect(code=1000)
        item = self._incoming.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def send_json(self, frame: dict):
        self.sent.append(frame)

    def frames(self, msg_type: str) -> list[dict]:
        return [f for f in self.sent if f.get("type") == msg_type]


class _FakeDaemonClient:
    """记录调用的 daemon 客户端替身。"""

    def __init__(self, *, fail: set[str] | None = None):
        self.fail = fail or set()
        self.calls: list[str] = []
        self.closed = False
        self._connected = True

    async def _maybe_fail(self, name: str):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"{name} boom")

    async def send_agent_message(self, **_kwargs):
        await self._maybe_fail("send_agent_message")

    async def read_file(self, path, cwd=None):
        await self._maybe_fail("read_file")
        return f"content of {path}"

    async def write_file(self, path, content, cwd=None):
        await self._maybe_fail("write_file")

    async def list_files(self, path, cwd=None):
        await self._maybe_fail("list_files")
        return ["a.py"]

    async def resolve_permission(self, **_kwargs):
        await self._maybe_fail("resolve_permission")

    async def cancel_agent(self, _agent_id):
        await self._maybe_fail("cancel_agent")

    async def set_agent_mode(self, _agent_id, _mode_id):
        await self._maybe_fail("set_agent_mode")

    async def close(self):
        self.closed = True
        self._connected = False

    async def subscribe_timeline_async(self):
        """模拟真实实现：只看 daemon 侧状态，前端断开它不会自行结束。"""
        while self._connected:
            await asyncio.sleep(0.01)
            if False:
                yield {}


# ---- 断连收摊 ----


@pytest.mark.asyncio
async def test_frontend_disconnect_cancels_timeline_tasks() -> None:
    """前端断开后 timeline 任务必须被取消，不能靠它自行退出。

    这正是 gather → asyncio.wait(FIRST_COMPLETED) 的理由：
    subscribe_timeline_async 的循环条件与前端无关，gather 会一直等它。
    """
    websocket = _FakeWebSocket([])  # 立即断开
    worker = _FakeDaemonClient()
    validator = _FakeDaemonClient()

    frontend = asyncio.create_task(
        arl_ws._forward_from_frontend(
            websocket, uuid.uuid4(), worker, validator, "wa", "va"
        )
    )
    timelines = [
        asyncio.create_task(arl_ws._forward_timeline(websocket, worker, "worker")),
        asyncio.create_task(
            arl_ws._forward_timeline(websocket, validator, "validator")
        ),
    ]
    tasks = [frontend, *timelines]

    done, pending = await asyncio.wait(
        tasks, return_when=asyncio.FIRST_COMPLETED, timeout=2.0
    )

    # 前端任务先结束，两个 timeline 仍在跑（证明它们不会自行退出）
    assert frontend in done
    assert set(pending) == set(timelines)

    for task in pending:
        task.cancel()
    results = await asyncio.gather(*pending, return_exceptions=True)
    assert all(isinstance(r, asyncio.CancelledError) for r in results)


@pytest.mark.asyncio
async def test_timeline_reraises_cancellation() -> None:
    """timeline 任务不得吞掉 CancelledError，否则破坏取消语义。"""
    websocket = _FakeWebSocket([])
    client = _FakeDaemonClient()
    task = asyncio.create_task(arl_ws._forward_timeline(websocket, client, "worker"))
    await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


# ---- 心跳 ----


@pytest.mark.asyncio
async def test_heartbeat_survives_refresh_failure_and_stays_cancellable(
    monkeypatch,
) -> None:
    """单次刷新失败不得结束心跳，否则 asyncio.wait 会把整个会话收摊。"""
    calls: list[str] = []

    def flaky_touch(session_id: str) -> None:
        calls.append(session_id)
        if len(calls) == 1:
            raise RuntimeError("thread pool boom")

    monkeypatch.setattr(arl_ws, "touch_arl_session_active", flaky_touch)
    monkeypatch.setattr(arl_ws.settings, "ARL_SESSION_HEARTBEAT_INTERVAL", 0.01)

    session_id = uuid.uuid4()
    task = asyncio.create_task(arl_ws._heartbeat(session_id))
    await asyncio.sleep(0.2)

    # 第一次抛了异常，后面仍在继续刷新
    assert not task.done()
    assert len(calls) >= 2
    assert set(calls) == {str(session_id)}

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---- 单条消息的失败隔离 ----


async def _run_messages(messages: list[dict | Exception], **client_kwargs):
    """把一串前端消息喂给转发循环，返回 (websocket, worker_client)。"""
    websocket = _FakeWebSocket(messages)
    worker = _FakeDaemonClient(**client_kwargs)
    validator = _FakeDaemonClient()
    await arl_ws._forward_from_frontend(
        websocket, uuid.uuid4(), worker, validator, "wa", "va"
    )
    return websocket, worker


@pytest.mark.asyncio
async def test_missing_required_field_returns_error_and_keeps_going() -> None:
    """file_read 缺 path 只回 error 帧，后续消息照常处理。"""
    websocket, worker = await _run_messages(
        [
            {"type": "file_read", "role": "worker", "payload": {}},
            {"type": "file_list", "role": "worker", "payload": {"path": "src"}},
        ]
    )

    assert len(websocket.frames("error")) == 1
    # 连接没断，后一条正常完成
    assert websocket.frames("file_list")
    assert "list_files" in worker.calls


@pytest.mark.asyncio
async def test_daemon_failure_does_not_tear_down_session() -> None:
    """daemon 侧报错同样只回 error 帧。"""
    websocket, _ = await _run_messages(
        [
            {"type": "file_read", "role": "worker", "payload": {"path": "x"}},
            {"type": "file_list", "role": "worker", "payload": {"path": ""}},
        ],
        fail={"read_file"},
    )

    errors = websocket.frames("error")
    assert len(errors) == 1
    assert errors[0]["role"] == "worker"
    assert websocket.frames("file_list")


@pytest.mark.asyncio
async def test_malformed_frame_is_rejected_without_closing() -> None:
    """非 JSON 帧回 error 帧后继续读下一条。"""
    websocket, _ = await _run_messages(
        [
            ValueError("not json"),
            {"type": "file_list", "role": "worker", "payload": {}},
        ]
    )

    assert websocket.frames("error")
    assert websocket.frames("file_list")


@pytest.mark.asyncio
async def test_invalid_role_is_rejected_not_routed_to_validator() -> None:
    """role 非法时必须报错，不能像原先那样静默当成 validator。"""
    websocket = _FakeWebSocket(
        [
            {"type": "agent_send", "role": "wrker", "payload": {"content": "hi"}},
            {"type": "agent_send", "role": None, "payload": {"content": "hi"}},
        ]
    )
    worker = _FakeDaemonClient()
    validator = _FakeDaemonClient()

    await arl_ws._forward_from_frontend(
        websocket, uuid.uuid4(), worker, validator, "wa", "va"
    )

    assert len(websocket.frames("error")) == 2
    # 两个容器都没收到这条投错的消息
    assert worker.calls == []
    assert validator.calls == []


@pytest.mark.asyncio
async def test_unknown_message_type_returns_error() -> None:
    """未知消息类型回 error 帧（原先只记一条日志，前端毫无反馈）。"""
    websocket, _ = await _run_messages(
        [{"type": "do_something_weird", "role": "worker", "payload": {}}]
    )
    errors = websocket.frames("error")
    assert len(errors) == 1
    assert "unknown message type" in errors[0]["data"]["message"]


@pytest.mark.asyncio
async def test_cancel_failure_reports_result_frame() -> None:
    """取消失败要回 agent_cancel_result(ok=False)，让前端解开按钮状态。"""
    websocket, _ = await _run_messages(
        [{"type": "agent_cancel", "role": "worker", "payload": {}}],
        fail={"cancel_agent"},
    )
    frames = websocket.frames("agent_cancel_result")
    assert len(frames) == 1
    assert frames[0]["data"]["ok"] is False
    # 用专门的结果帧，不要再多推一个通用 error 帧
    assert websocket.frames("error") == []


@pytest.mark.asyncio
async def test_set_mode_without_mode_id_returns_result_frame() -> None:
    """缺 mode_id 时回 agent_mode_result(ok=False)。"""
    websocket, _ = await _run_messages(
        [{"type": "set_agent_mode", "role": "worker", "payload": {}}]
    )
    frames = websocket.frames("agent_mode_result")
    assert len(frames) == 1
    assert frames[0]["data"]["ok"] is False


@pytest.mark.asyncio
async def test_set_mode_persists_off_the_event_loop(monkeypatch) -> None:
    """切模式成功后写库，且走线程池（消息循环里不能同步开事务）。"""
    persisted: list[tuple] = []
    monkeypatch.setattr(
        arl_ws,
        "_persist_role_mode",
        lambda sid, role, mode: persisted.append((str(sid), role, mode)),
    )
    session_id = uuid.uuid4()
    websocket = _FakeWebSocket(
        [
            {
                "type": "set_agent_mode",
                "role": "validator",
                "payload": {"mode_id": "full-access"},
            }
        ]
    )
    worker = _FakeDaemonClient()
    validator = _FakeDaemonClient()

    await arl_ws._forward_from_frontend(
        websocket, session_id, worker, validator, "wa", "va"
    )

    assert persisted == [(str(session_id), "validator", "full-access")]
    frames = websocket.frames("agent_mode_result")
    assert frames[0]["data"] == {"ok": True, "mode_id": "full-access"}
    # 必须投给 validator，而不是 worker
    assert "set_agent_mode" in validator.calls
    assert worker.calls == []
