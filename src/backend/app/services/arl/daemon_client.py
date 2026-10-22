"""
Paseo Daemon Client - Python 实现。

基于 WebSocket 的客户端，对齐 Paseo daemon 的消息协议
（@getpaseo/protocol 的 WSHelloMessageSchema / SessionInbound / SessionOutbound）：

- 握手：hello 必须带 ``clientType`` 与 ``auth``，daemon 回 server_info
- 请求：``{"type": "session", "message": {"type": "<x>_request", "requestId": ...}}``
- 响应：``{"type": "session", "message": {"type": "<x>_response", "payload": {...}}}``
  或 ``rpc_error``，均通过 ``payload.requestId`` 与请求配对
- 事件：``agent_stream`` / ``agent_update`` / ``agent_status`` 等推送消息

等官方 Python SDK 发布后可替换。
"""

import asyncio
import contextlib
import json
import secrets
import uuid
from collections.abc import Callable
from typing import Any

import websockets
from loguru import logger

#: ARL 容器内项目目录的兜底值。实际路径是 ``/workspace/<role>``
#: （容器里的 ``ARL_PROJECT_DIR``，见 third_party/auto-dl-loop/docker/entrypoint.sh），
#: 由调用方按角色传入。
DEFAULT_CWD = "/workspace"


class PaseoDaemonClient:
    """Paseo daemon WebSocket 客户端。

    连接到容器内的 Paseo daemon，提供：
    - 查询与创建 agent
    - 发送消息到 agent
    - 订阅 agent 事件流
    - 文件操作（读写、列目录）
    - 权限处理
    """

    #: daemon 推送中与 agent 内容无关、不需要上抛给前端的消息
    IGNORED_MESSAGE_TYPES = frozenset({"status", "pong"})

    #: 已知的 agent 推送事件类型。仅用于日志分类：实际转发不做白名单过滤，
    #: 因为 RPC 响应已在上游按 ``requestId`` 消费掉，剩下的 session 消息
    #: 都是 daemon 的推送，漏掉任何一种都会让前端少渲染一块内容。
    AGENT_EVENT_TYPES = frozenset(
        {
            "agent_stream",
            "agent_update",
            "agent_status",
            "agent_list",
            "agent_deleted",
            "agent_archived",
            "agent_attention_required",
            "agent_permission_request",
            "agent_permission_resolved",
            "agent.timeline.replacement",
            "agent.create.update",
            "agent.provider_subagents.update",
        }
    )

    def __init__(
        self,
        url: str,
        password: str,
        cwd: str = DEFAULT_CWD,
        client_id: str | None = None,
        client_type: str = "cli",
    ):
        """初始化客户端。

        Args:
            url: WebSocket URL (ws://host:port/ws)
            password: Daemon 密码
            cwd: 容器内的项目目录，即 ``ARL_PROJECT_DIR``（``/workspace/<role>``）。
                文件操作与创建 agent 都以它为根。
            client_id: 客户端 ID（默认随机生成）
            client_type: 客户端类型（默认 "cli"）
        """
        self.url = url
        self.password = password
        self.cwd = cwd
        self.client_id = client_id or f"python-client-{secrets.token_hex(4)}"
        self.client_type = client_type

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._connected = False
        self._session_id: str | None = None
        self._server_info: dict[str, Any] | None = None
        self._receive_task: asyncio.Task | None = None
        self._rpc_futures: dict[str, asyncio.Future] = {}
        self._timeline_callbacks: list[Callable[[dict[str, Any]], None]] = []

    async def connect(self):
        """连接到 daemon 并完成 hello 握手。

        握手流程遵循 Paseo 的 WSHelloMessageSchema：
        hello 必须携带 ``clientType`` 和 ``auth``，否则 daemon 会以
        4002 (Invalid hello) 关闭连接。

        Raises:
            PermissionError: 密码被 daemon 拒绝。
            TimeoutError: 等待 server_info 超时。
        """
        if self._connected:
            return

        try:
            # 1. 建立 WebSocket 连接
            self._ws = await websockets.connect(self.url)
            logger.info("已连接到 Paseo daemon: {}", self.url)

            # 2. 发送 hello 消息
            #    注意：接收循环必须等握手结束后再启动，否则它与
            #    _wait_for_server_info 会同时 recv，websockets 会抛
            #    RuntimeError（同一连接不允许并发 recv）。
            hello_message = {
                "type": "hello",
                "clientId": self.client_id,
                "clientType": self.client_type,
                "protocolVersion": 1,
                "auth": {"kind": "password", "password": self.password},
                "appVersion": "0.1.0",
                # 声明该能力后，daemon 会在拒绝时先回 hello.rejected，
                # 带上具体 reason，而不是只给一个 close code。
                "capabilities": {"hello_rejection": True},
            }

            logger.info(
                "发送 hello 消息: clientId={}, clientType={}",
                self.client_id,
                self.client_type,
            )
            await self._ws.send(json.dumps(hello_message))

            # 3. 等待 server_info（daemon 接受 hello 后下发的 status 消息）
            try:
                await asyncio.wait_for(self._wait_for_server_info(), timeout=10.0)
            except TimeoutError:
                await self._ws.close()
                raise TimeoutError("等待 daemon server_info 响应超时") from None

            # 4. 握手完成，交给后台接收循环
            self._connected = True
            self._receive_task = asyncio.create_task(self._receive_loop())
            logger.info(
                "已完成 daemon 握手: serverId={}, daemonVersion={}",
                (self._server_info or {}).get("serverId"),
                (self._server_info or {}).get("version"),
            )

        except Exception as e:
            logger.exception("连接 Paseo daemon 失败: {}", e)
            self._connected = False
            raise

    async def _wait_for_server_info(self) -> dict[str, Any]:
        """等待 daemon 的 server_info 消息。

        Paseo 在 hello 被接受后下发
        ``{"type": "session", "message": {"type": "status", "payload": {"status": "server_info", ...}}}``；
        被拒绝时下发 ``{"type": "hello.rejected", "reason": ...}``。

        Returns:
            server_info 的 payload。

        Raises:
            PermissionError: hello 被拒绝（密码错误或需要密码）。
        """
        if not self._ws:
            raise RuntimeError("WebSocket 未连接")

        async for message in self._ws:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logger.warning("收到非 JSON 消息: {}", message)
                continue

            if data.get("type") == "hello.rejected":
                reason = data.get("reason", "unknown")
                raise PermissionError(f"daemon 拒绝 hello: {reason}")

            if data.get("type") == "session":
                payload = data.get("message", {}).get("payload", {})
                if payload.get("status") == "server_info":
                    self._server_info = payload
                    # daemon 侧的 session 由它自己分配；之后的 session
                    # 消息都带这个 id。
                    self._session_id = payload.get("serverId")
                    return payload

            logger.debug("握手期间忽略消息: type={}", data.get("type"))

        raise ConnectionError("WebSocket 在收到 server_info 之前关闭")

    async def disconnect(self):
        """断开连接。"""
        if self._receive_task:
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task
            self._receive_task = None

        if not self._connected or not self._ws:
            return

        try:
            await self._ws.close()
            self._connected = False
            logger.info("已断开 Paseo daemon 连接")
        except Exception as e:
            logger.warning("断开连接失败: {}", e)

    async def close(self):
        """关闭连接（disconnect 的别名）。"""
        await self.disconnect()

    async def _receive_loop(self):
        """接收消息循环。"""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    logger.warning("收到非 JSON 消息: {}", message)
        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket 连接已关闭")
            self._connected = False
        except Exception as e:
            logger.exception("接收消息失败: {}", e)
            self._connected = False

    async def _handle_message(self, data: dict[str, Any]):
        """处理收到的消息。

        Paseo 的出站消息都包在 session 信封里：
        ``{"type": "session", "message": {"type": ..., "payload": {...}}}``。
        响应通过 ``payload.requestId`` 与请求配对。

        Args:
            data: 消息数据
        """
        if data.get("type") != "session":
            # ping/pong、hello.rejected 等非 session 帧与 RPC 无关。
            logger.debug("📨 [DaemonClient] 非 session 帧: type={}", data.get("type"))
            return

        message = data.get("message") or {}
        message_type = message.get("type")
        payload = message.get("payload") or {}
        request_id = payload.get("requestId")

        # RPC 响应：按 requestId 配对，不依赖具体的 response type 名
        if request_id and request_id in self._rpc_futures:
            future = self._rpc_futures.pop(request_id)
            if future.done():
                return
            if message_type == "rpc_error":
                error = payload.get("error", "unknown error")
                code = payload.get("code")
                logger.error(
                    "❌ [DaemonClient] RPC 失败: requestId={}, code={}, error={}",
                    request_id,
                    code,
                    error,
                )
                future.set_exception(RuntimeError(f"RPC 失败({code}): {error}"))
            else:
                logger.debug(
                    "✅ [DaemonClient] RPC 响应: requestId={}, type={}",
                    request_id,
                    message_type,
                )
                future.set_result(payload)
            return

        # daemon 推送：除握手用的 status / 心跳外，全部上抛给上层。
        # 这里刻意不做类型白名单——前端渲染依赖完整的事件序列，
        # 任何被丢掉的推送都会表现为「消息收到了但渲染不出来」。
        if message_type in self.IGNORED_MESSAGE_TYPES:
            logger.debug("📨 [DaemonClient] 忽略消息: type={}", message_type)
            return

        if message_type not in self.AGENT_EVENT_TYPES:
            logger.debug("📨 [DaemonClient] 转发未登记的推送: type={}", message_type)

        for callback in list(self._timeline_callbacks):
            try:
                callback(message)
            except Exception as e:
                logger.exception("Timeline 回调失败: {}", e)

    async def _send_session_message(self, message: dict[str, Any]):
        """发送一条 session 信封消息（不等待响应）。

        Args:
            message: session 内层消息（需自带 ``type``）
        """
        if not self._connected or not self._ws:
            raise RuntimeError("未连接到 daemon")

        await self._ws.send(json.dumps({"type": "session", "message": message}))

    async def _rpc_call(
        self,
        request_type: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """发起一次请求/响应式调用。

        Args:
            request_type: Paseo 请求类型，例如 ``fetch_agents_request``
            params: 该请求类型的其余字段
            timeout: 等待响应的秒数

        Returns:
            响应消息的 payload。

        Raises:
            RuntimeError: 未连接，或 daemon 返回 rpc_error。
            TimeoutError: 等待响应超时。
        """
        if not self._connected or not self._ws:
            logger.error("❌ [DaemonClient] RPC 调用失败：未连接到 daemon")
            raise RuntimeError("未连接到 daemon")

        # requestId 在协议里是字符串
        request_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._rpc_futures[request_id] = future

        message = {"type": request_type, "requestId": request_id, **(params or {})}
        logger.debug(
            "📤 [DaemonClient] 发送请求: type={}, requestId={}", request_type, request_id
        )

        try:
            await self._send_session_message(message)
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            logger.error(
                "⏰ [DaemonClient] RPC 超时: type={}, requestId={}", request_type, request_id
            )
            raise TimeoutError(f"RPC 调用超时: {request_type}") from None
        finally:
            self._rpc_futures.pop(request_id, None)

    async def fetch_agents(self, subscribe: bool = True) -> list[dict[str, Any]]:
        """列出 daemon 上的 agent，并可同时订阅后续更新。

        Args:
            subscribe: 是否订阅 agent 目录的增量更新

        Returns:
            agent 目录条目列表。
        """
        params: dict[str, Any] = {}
        if subscribe:
            params["subscribe"] = {}

        payload = await self._rpc_call("fetch_agents_request", params)
        entries = payload.get("entries", [])
        logger.info("📋 [DaemonClient] 查询到 {} 个 agent", len(entries))
        return entries

    async def create_agent(
        self,
        provider: str,
        cwd: str | None = None,
        title: str | None = None,
        model: str | None = None,
        mode_id: str | None = None,
        initial_prompt: str | None = None,
    ) -> dict[str, Any]:
        """创建一个 agent。

        Args:
            provider: agent provider（例如 ``claude`` / ``codex``）
            cwd: agent 工作目录（默认用 client 的项目目录）
            title: agent 标题
            model: 模型名（省略则用 provider 默认）
            mode_id: 权限模式（如 claude 的 ``bypassPermissions``、codex 的
                ``full-access``）。省略时 daemon 会落到 ``default``，也就是
                每次用工具都要人工确认。
            initial_prompt: 创建后立即发送的首条消息

        Returns:
            新建 agent 的快照，至少包含 ``id``；daemon 通常还会带上
            ``currentModeId`` / ``availableModes`` / ``pendingPermissions``。

        Raises:
            RuntimeError: daemon 未返回 agentId。
        """
        config: dict[str, Any] = {"provider": provider, "cwd": cwd or self.cwd}
        if title:
            config["title"] = title
        if model:
            config["model"] = model
        if mode_id:
            config["modeId"] = mode_id

        params: dict[str, Any] = {"config": config, "labels": {}}
        if initial_prompt:
            params["initialPrompt"] = initial_prompt

        payload = await self._rpc_call("create_agent_request", params, timeout=60.0)
        agent = dict(payload.get("agent") or {})
        agent_id = agent.get("id") or payload.get("agentId")
        if not agent_id:
            raise RuntimeError(f"create_agent 未返回 agentId: {payload}")
        agent["id"] = agent_id

        logger.info(
            "🆕 [DaemonClient] 已创建 agent: provider={}, mode={}, id={}",
            provider,
            agent.get("currentModeId") or mode_id,
            agent_id,
        )
        return agent

    async def cancel_agent(self, agent_id: str) -> dict[str, Any]:
        """中断 agent 正在进行的回合（前端「停止」按钮）。

        对应 Paseo 的 ``cancel_agent_request``；daemon 随后会推送
        ``turn_canceled`` 事件，前端据此把状态切回空闲。

        Args:
            agent_id: 目标 agent ID

        Returns:
            响应 payload（含 ``agent`` 快照）。

        Raises:
            RuntimeError: daemon 拒绝取消。
        """
        logger.info("🛑 [DaemonClient] 取消 agent 回合: agentId={}", agent_id)
        payload = await self._rpc_call("cancel_agent_request", {"agentId": agent_id})
        if payload.get("error"):
            raise RuntimeError(f"取消 agent 失败: {payload['error']}")
        return payload

    async def set_agent_mode(self, agent_id: str, mode_id: str) -> dict[str, Any]:
        """切换 agent 的权限模式。

        模式 ID 由 provider 决定，取值来自 agent 快照的 ``availableModes``：
        claude 是 ``plan`` / ``default`` / ``acceptEdits`` / ``auto`` /
        ``bypassPermissions``，codex 是 ``auto`` / ``auto-review`` /
        ``full-access``。

        Args:
            agent_id: 目标 agent ID
            mode_id: 模式 ID

        Returns:
            响应 payload（含 ``accepted`` 与可选的 ``notice``）。

        Raises:
            RuntimeError: daemon 拒绝切换。
        """
        logger.info(
            "🔧 [DaemonClient] 切换权限模式: agentId={}, modeId={}", agent_id, mode_id
        )
        payload = await self._rpc_call(
            "set_agent_mode_request", {"agentId": agent_id, "modeId": mode_id}
        )
        if payload.get("error") or payload.get("accepted") is False:
            raise RuntimeError(
                f"切换权限模式失败: {payload.get('error') or '被 daemon 拒绝'}"
            )
        return payload

    async def send_agent_message(
        self,
        agent_id: str,
        text: str,
        active_turn_behavior: str | None = None,
    ):
        """发送消息给指定 agent。

        Args:
            agent_id: 目标 agent ID
            text: 消息内容
            active_turn_behavior: agent 正在响应时的行为（``interrupt`` / ``steer``）
        """
        params: dict[str, Any] = {"agentId": agent_id, "text": text}
        if active_turn_behavior:
            params["activeTurnBehavior"] = active_turn_behavior

        logger.info(
            "📤 [DaemonClient] 发送消息: agentId={}, 长度={}", agent_id, len(text)
        )
        await self._rpc_call("send_agent_message_request", params)

    async def read_file(self, path: str, cwd: str | None = None) -> str:
        """读取文件。

        Args:
            path: 文件路径（相对于 cwd）
            cwd: 覆盖默认的项目目录

        Returns:
            文件内容。

        Raises:
            FileNotFoundError: daemon 报告该路径不可读。
        """
        target_cwd = cwd or self.cwd
        payload = await self._rpc_call(
            "file_explorer_request", {"cwd": target_cwd, "path": path, "mode": "file"}
        )
        if payload.get("error"):
            raise FileNotFoundError(f"读取 {target_cwd}/{path} 失败: {payload['error']}")

        return (payload.get("file") or {}).get("content", "")

    async def _file_version(self, cwd: str, path: str) -> dict[str, Any] | None:
        """读取文件的当前版本信息。

        Args:
            cwd: 项目目录
            path: 文件路径（相对于 cwd）

        Returns:
            文件信息（含 ``modifiedAt``），文件不存在时返回 None。
        """
        payload = await self._rpc_call(
            "file_explorer_request", {"cwd": cwd, "path": path, "mode": "file"}
        )
        if payload.get("error"):
            return None
        return payload.get("file")

    async def write_file(self, path: str, content: str, cwd: str | None = None):
        """写入文件，文件不存在时先创建。

        ``fs.file.write.request`` 只能改写已存在的文件（对缺失路径会返回
        conflict），所以新建场景要先走 ``fs.entry.create.request``。

        Args:
            path: 文件路径（相对于 cwd）
            content: 文件内容
            cwd: 覆盖默认的项目目录

        Raises:
            OSError: daemon 拒绝写入，或与磁盘上的版本冲突。
        """
        target_cwd = cwd or self.cwd

        file_info = await self._file_version(target_cwd, path)
        if file_info is None:
            # 先创建空文件，再按正常改写路径写入内容
            parent_path, _, name = path.rpartition("/")
            create_payload = await self._rpc_call(
                "fs.entry.create.request",
                {
                    "cwd": target_cwd,
                    "parentPath": parent_path,
                    "name": name or path,
                    "kind": "file",
                },
            )
            if create_payload.get("error"):
                raise OSError(
                    f"创建 {target_cwd}/{path} 失败: {create_payload['error']}"
                )
            file_info = await self._file_version(target_cwd, path)

        # expectedModifiedAt 是乐观锁，必须与磁盘上的 mtime 完全一致
        expected_modified_at = (file_info or {}).get("modifiedAt", "")

        payload = await self._rpc_call(
            "fs.file.write.request",
            {
                "cwd": target_cwd,
                "path": path,
                "content": content,
                "expectedModifiedAt": expected_modified_at,
            },
        )

        result = payload.get("result") or {}
        status = result.get("status")
        if status != "written":
            detail = result.get("error") or result.get("version") or result
            raise OSError(f"写入 {target_cwd}/{path} 失败({status}): {detail}")

        logger.debug("💾 [DaemonClient] 已写入 {}/{}", target_cwd, path)

    async def list_files(
        self, path: str = "", cwd: str | None = None
    ) -> list[dict[str, Any]]:
        """列出目录内容。

        Args:
            path: 目录路径（相对于 cwd，空字符串表示 cwd 本身）
            cwd: 覆盖默认的项目目录

        Returns:
            目录条目列表。

        Raises:
            FileNotFoundError: daemon 报告该目录不可读。
        """
        target_cwd = cwd or self.cwd
        params: dict[str, Any] = {"cwd": target_cwd, "mode": "list"}
        if path:
            params["path"] = path

        payload = await self._rpc_call("file_explorer_request", params)
        if payload.get("error"):
            raise FileNotFoundError(f"列出 {target_cwd}/{path} 失败: {payload['error']}")

        return (payload.get("directory") or {}).get("entries", [])

    async def fetch_agent_timeline(
        self,
        agent_id: str,
        limit: int = 200,
        direction: str = "tail",
        projection: str = "projected",
    ) -> dict[str, Any]:
        """拉取某个 agent 的历史时间线。

        前端刷新/重连后靠它回填聊天记录，否则面板会是空的。

        Args:
            agent_id: 目标 agent ID
            limit: 最多返回多少条（daemon 侧上限 200）
            direction: ``tail`` 取最近 limit 条；``before`` / ``after`` 需配合 cursor
            projection: daemon 侧投影模式，只能是 ``projected`` 或 ``canonical``

        Returns:
            响应 payload，``entries`` 是已在上游折叠好的时间线条目：
            ``{provider, item, timestamp, seqStart, seqEnd, turnId, collapsed}``。
        """
        payload = await self._rpc_call(
            "fetch_agent_timeline_request",
            {
                "agentId": agent_id,
                "direction": direction,
                "limit": limit,
                "projection": projection,
            },
            timeout=30.0,
        )
        if payload.get("error"):
            raise RuntimeError(f"拉取 agent 历史失败: {payload['error']}")

        entries = payload.get("entries") or []
        logger.info(
            "📜 [DaemonClient] 拉取到 agent 历史: agentId={}, 条数={}, hasOlder={}",
            agent_id,
            len(entries),
            payload.get("hasOlder"),
        )
        return payload

    def subscribe_timeline(self, callback: Callable[[dict[str, Any]], None]):
        """订阅 agent 事件。

        Args:
            callback: 事件回调函数，入参是 session 内层消息
        """
        self._timeline_callbacks.append(callback)

    async def subscribe_timeline_async(self):
        """订阅 agent 事件（异步生成器模式）。

        用于 async for 循环。内部使用队列转发事件。

        Yields:
            dict: session 内层消息（含 ``type`` 与 ``payload``）
        """
        # 创建队列来存储事件
        queue: asyncio.Queue = asyncio.Queue()

        def callback(event: dict[str, Any]):
            """回调函数：将事件放入队列"""
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Timeline event queue full, dropping event")

        # 注册回调
        self.subscribe_timeline(callback)

        try:
            # 持续从队列中取出事件并 yield
            while self._connected:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield event
                except TimeoutError:
                    # 超时后继续循环，检查连接状态
                    continue
        finally:
            # 清理：取消订阅
            self.unsubscribe_timeline(callback)

    def unsubscribe_timeline(self, callback: Callable[[dict[str, Any]], None]):
        """取消订阅 timeline 事件。

        Args:
            callback: 事件回调函数
        """
        if callback in self._timeline_callbacks:
            self._timeline_callbacks.remove(callback)

    async def resolve_permission(
        self, agent_id: str, request_id: str, response: dict[str, Any]
    ):
        """响应 agent 的权限请求。

        该消息是单向的，daemon 不会回 response 帧。

        Args:
            agent_id: 发起请求的 agent ID
            request_id: 权限请求 ID（来自 agent_permission_request 事件）
            response: 响应数据，形如 ``{"behavior": "allow"}``
        """
        logger.info(
            "🔐 [DaemonClient] 响应权限请求: agentId={}, requestId={}, behavior={}",
            agent_id,
            request_id,
            response.get("behavior"),
        )
        await self._send_session_message(
            {
                "type": "agent_permission_response",
                "agentId": agent_id,
                "requestId": request_id,
                "response": response,
            }
        )
