"""Paseo daemon 客户端测试。

重点是**连接中断时在途 RPC 的收摊**。原先 ``_receive_loop`` 退出时只设
``_connected = False``，``_rpc_futures`` 里的 future 无人处置，调用方要干等
满 timeout（``create_agent`` 是 60s）才拿到 TimeoutError——把「连接断了」
伪装成「daemon 没响应」，日志也指向错误的方向。

典型触发路径：daemon 回了一条超过 ``max_size`` 的消息（如 200 条 timeline
回填），websockets 抛 ``ConnectionClosed`` 关掉整条连接，于是 ``fetch_agent_timeline``
卡 30s 后报超时，而真正的原因是帧超限。
"""

from __future__ import annotations

import asyncio

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode
from websockets.frames import Close

from app.services.arl import daemon_client
from app.services.arl.daemon_client import PaseoDaemonClient


class _FakeWebSocket:
    """按脚本驱动 ``_receive_loop`` 的假 WS。

    ``__aiter__`` 先吐出预设消息，再按 ``raise_on_exhaust`` 决定是抛异常
    还是正常结束迭代（对应对端优雅关闭）。
    """

    def __init__(
        self,
        messages: list[str] | None = None,
        raise_on_exhaust: BaseException | None = None,
    ):
        self._messages = list(messages or [])
        self._raise_on_exhaust = raise_on_exhaust
        self.sent: list[str] = []
        self.closed = False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for message in self._messages:
            yield message
        if self._raise_on_exhaust is not None:
            raise self._raise_on_exhaust

    async def send(self, data: str):
        self.sent.append(data)

    async def close(self):
        self.closed = True


def _client(ws: _FakeWebSocket) -> PaseoDaemonClient:
    """构造一个「已握手」状态的客户端，跳过真实 connect。"""
    client = PaseoDaemonClient(url="ws://fake/ws", password="pw", cwd="/workspace/worker")
    client._ws = ws  # type: ignore[assignment]
    client._connected = True
    return client


async def _pending_rpc(client: PaseoDaemonClient, request_id: str) -> asyncio.Future:
    """手工挂一个在途 RPC future，模拟已发出但未收到响应的请求。"""
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    client._rpc_futures[request_id] = future
    return future


async def test_oversize_frame_fails_pending_rpc_immediately() -> None:
    """帧超限关连接时，在途 RPC 必须立刻拿到 ConnectionError 而非等满超时。"""
    oversize = ConnectionClosedError(Close(1009, "message too big"), None, None)
    ws = _FakeWebSocket(raise_on_exhaust=oversize)
    client = _client(ws)
    future = await _pending_rpc(client, "req-1")

    await client._receive_loop()

    assert future.done()
    with pytest.raises(ConnectionError) as excinfo:
        future.result()
    # 错误信息要带上真实原因，否则排查时只看到一句「连接中断」
    assert "1009" in str(excinfo.value)
    assert client._connected is False
    # 字典整体换出，后续 pop 不应再命中旧 future
    assert client._rpc_futures == {}


async def test_graceful_close_also_fails_pending_rpc() -> None:
    """对端优雅关闭（迭代自然结束）同样不会再有响应，也要收摊。

    这是原实现漏掉的路径：既不抛 ConnectionClosed 也不抛其他异常，
    于是 future 永远悬着。
    """
    ws = _FakeWebSocket(messages=[])
    client = _client(ws)
    future = await _pending_rpc(client, "req-2")

    await client._receive_loop()

    assert future.done()
    with pytest.raises(ConnectionError):
        future.result()
    assert client._connected is False


async def test_disconnect_fails_pending_rpc() -> None:
    """主动断开时也要收摊。

    ``disconnect`` 是 cancel 掉接收任务，而 ``CancelledError`` 既不走
    ``except Exception`` 也不走 ``else``，不补一次调用方照样干等。
    """
    # 永不结束的迭代，让接收任务只能靠 cancel 退出
    never_ending = asyncio.Event()

    class _BlockingWebSocket(_FakeWebSocket):
        async def _iterate(self):
            await never_ending.wait()
            yield ""

    ws = _BlockingWebSocket()
    client = _client(ws)
    client._receive_task = asyncio.create_task(client._receive_loop())
    await asyncio.sleep(0)  # 让接收任务跑到 await
    future = await _pending_rpc(client, "req-3")

    await client.disconnect()

    assert future.done()
    with pytest.raises(ConnectionError):
        future.result()
    # 主动断开必须真的把 WS 关掉——_fail_pending_rpcs 会把 _connected 置否，
    # 若用它做前置判断就会提前 return，close 永不执行
    assert ws.closed is True


async def test_already_resolved_future_is_left_alone() -> None:
    """已完成的 future 不该被二次 set_exception（会抛 InvalidState）。"""
    ws = _FakeWebSocket(raise_on_exhaust=ConnectionClosedError(None, None, None))
    client = _client(ws)
    future = await _pending_rpc(client, "req-4")
    future.set_result({"ok": True})

    await client._receive_loop()  # 不应抛异常

    assert future.result() == {"ok": True}


async def test_normal_rpc_response_still_resolves() -> None:
    """收摊逻辑不得影响正常的响应配对。"""
    import json

    payload = {
        "type": "session",
        "message": {
            "type": "fetch_agents_response",
            "payload": {"requestId": "req-5", "entries": []},
        },
    }
    ws = _FakeWebSocket(messages=[json.dumps(payload)])
    client = _client(ws)
    future = await _pending_rpc(client, "req-5")

    await client._receive_loop()

    assert future.done()
    assert future.result()["entries"] == []


# ---- WS 升级就绪窗口：daemon 的 HTTP 与 WS 是两个就绪阶段 ----


def _invalid_status(code: int) -> InvalidStatusCode:
    """构造一个握手被拒的异常。"""
    return InvalidStatusCode(code, Headers())


async def test_connect_waits_out_ws_not_ready(monkeypatch) -> None:
    """503 要轮询等待，而不是让新建会话的第一次连接直接失败。

    daemon 的 ``/api/health`` 在端口一开时就回 200（runtime 探活据此判就绪），
    但 WS 升级要等 ``beginAcceptingConnections()``，中间窗口里升级返回
    503 "Server not ready"。
    """
    attempts = 0
    ws = _FakeWebSocket()

    async def fake_connect(_url, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _invalid_status(503)
        return ws

    monkeypatch.setattr(daemon_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(daemon_client, "WS_READY_RETRY_INTERVAL", 0)

    client = PaseoDaemonClient(url="ws://fake/ws", password="pw")

    assert await client._open_socket() is ws
    assert attempts == 3


async def test_connect_does_not_retry_other_status(monkeypatch) -> None:
    """非 503 必须立刻上抛。

    401/403/404 是密码或地址错，重试只会把确定性故障拖满 30s 超时，
    还会把真正的原因埋在一堆重试日志里。
    """
    attempts = 0

    async def fake_connect(_url, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise _invalid_status(403)

    monkeypatch.setattr(daemon_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(daemon_client, "WS_READY_RETRY_INTERVAL", 0)

    client = PaseoDaemonClient(url="ws://fake/ws", password="pw")

    with pytest.raises(InvalidStatusCode) as excinfo:
        await client._open_socket()
    assert excinfo.value.status_code == 403
    assert attempts == 1


async def test_connect_gives_up_after_timeout(monkeypatch) -> None:
    """一直 503 时要在超时后上抛，不能无限等。"""
    attempts = 0

    async def fake_connect(_url, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise _invalid_status(503)

    monkeypatch.setattr(daemon_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(daemon_client, "WS_READY_RETRY_INTERVAL", 0)
    monkeypatch.setattr(daemon_client, "WS_READY_TIMEOUT", 0.05)

    client = PaseoDaemonClient(url="ws://fake/ws", password="pw")

    with pytest.raises(InvalidStatusCode) as excinfo:
        await client._open_socket()
    assert excinfo.value.status_code == 503
    assert attempts >= 2  # 至少重试过，不是一次就放弃


async def test_connect_succeeds_first_try_without_sleeping(monkeypatch) -> None:
    """daemon 已就绪时不得引入任何额外延迟。"""
    ws = _FakeWebSocket()
    slept: list[float] = []

    async def fake_connect(_url, **_kwargs):
        return ws

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(daemon_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(daemon_client.asyncio, "sleep", fake_sleep)

    client = PaseoDaemonClient(url="ws://fake/ws", password="pw")

    assert await client._open_socket() is ws
    assert slept == []


# ---- 右侧文件面板：fs.entry.* / file_explorer_request ----


def _recording_client(response: dict) -> tuple[PaseoDaemonClient, list[tuple[str, dict]]]:
    """把 ``_rpc_call`` 换成记录参数并返回固定 payload 的替身。"""
    client = _client(_FakeWebSocket())
    calls: list[tuple[str, dict]] = []

    async def fake_rpc(request_type, params=None, **_kwargs):
        calls.append((request_type, params or {}))
        return response

    client._rpc_call = fake_rpc  # type: ignore[method-assign]
    return client, calls


async def test_file_panel_requests_are_scoped_to_client_cwd() -> None:
    """所有文件面板请求都带本 client 的 cwd，根目录统一成 "."。"""
    client, calls = _recording_client(
        {"success": True, "error": None, "path": "b.py", "directory": {"path": ".", "entries": []}}
    )

    await client.explore_directory("")
    await client.create_entry("", "b.py", "file")
    await client.explore_file("a.py", 1024)

    assert all(params["cwd"] == "/workspace/worker" for _, params in calls)
    assert calls[0] == (
        "file_explorer_request",
        {"cwd": "/workspace/worker", "path": ".", "mode": "list"},
    )
    assert calls[1][1]["parentPath"] == "."
    assert calls[2][1]["maxBytes"] == 1024


async def test_entry_mutation_failure_raises() -> None:
    """daemon 回 success=False 时抛 OSError，带上 daemon 的错误文本。"""
    client, _ = _recording_client({"success": False, "error": "File or folder no longer exists"})

    with pytest.raises(OSError, match="no longer exists"):
        await client.delete_entry("gone.txt")


async def test_explore_file_error_raises() -> None:
    """读取超限等错误抛 FileNotFoundError。"""
    client, _ = _recording_client({"error": "File is too large to display", "file": None})

    with pytest.raises(FileNotFoundError, match="too large"):
        await client.explore_file("big.bin", 10)


async def test_save_file_sends_expected_version() -> None:
    """save_file 用调用方给的版本做乐观锁，冲突原样返回，error 抛 OSError。"""
    client, calls = _recording_client({"result": {"status": "conflict", "version": {}}})

    result = await client.save_file("a.py", "x", "2026-01-01T00:00:00.000Z", "rev")

    assert result["status"] == "conflict"
    assert calls == [
        (
            "fs.file.write.request",
            {
                "cwd": "/workspace/worker",
                "path": "a.py",
                "content": "x",
                "expectedModifiedAt": "2026-01-01T00:00:00.000Z",
                "expectedRevision": "rev",
            },
        )
    ]

    client, _ = _recording_client({"result": {"status": "error", "error": "EACCES"}})
    with pytest.raises(OSError, match="EACCES"):
        await client.save_file("a.py", "x", "t")
