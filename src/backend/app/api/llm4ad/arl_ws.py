"""ARL WebSocket 端点：前端 ↔ 两个 Paseo daemon 的消息中转。

用户创建 session_type='arl' 的会话后，前端连接:
    WS /api/v1/llm4ad/research/sessions/{session_id}/arl/ws?token=xxx

后端职责：
1. 验证 token，确认会话属于该用户且 type=arl
2. 解析会话里两个角色的 provider 凭证（base_url / key / 模型）
3. 按需启动两个容器（如果还没启动）
4. 连接两个 daemon WebSocket
5. 确保每个容器里有一个可用的 agent
6. 订阅 agent 事件并推送前端
7. 路由前端消息到对应 daemon
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from loguru import logger
from sqlmodel import Session, select

from app.core.db import engine
from app.core.security import decode_access_token
from app.models.research import ResearchSession
from app.models.user import User
from app.services.arl.daemon_client import PaseoDaemonClient
from app.services.arl.runtime import get_arl_runtime
from app.tasks.research_runner.config_builder import resolve_provider_for_arc

router = APIRouter(prefix="/research")

#: 每个角色固定使用的 agent provider，与容器镜像里安装的 CLI 一致
#: （见 third_party/auto-dl-loop/docker/entrypoint.sh）
ROLE_PROVIDERS = {"worker": "claude", "validator": "codex"}

#: 每个角色默认的权限模式：两个容器都是隔离沙箱里的无人值守 agent，
#: 默认用各自 provider 的「不再逐条确认」模式（Paseo 里 isUnattended 的那个），
#: 否则 daemon 会落到 ``default``，每调一次工具就弹一个确认框。
#: 用户可以在界面上改，改完写回 ``arl_config[role]["mode_id"]``，下次连接沿用。
ROLE_DEFAULT_MODES = {"worker": "bypassPermissions", "validator": "full-access"}

#: ARL 的两个角色，顺序即「先 worker 后 validator」
ROLES = ("worker", "validator")

#: 历史回填时每个 agent 最多拉取的条目数（daemon 侧上限为 200）
HISTORY_LIMIT = 200


def _resolve_role_providers(
    arl_config: dict,
    user_id: uuid.UUID,
) -> dict[str, dict[str, Any]]:
    """把 ``arl_config`` 里的 provider_id / model_name 解析成真实凭证。

    复用 pipeline 侧的 :func:`resolve_provider_for_arc`，这样 base_url、
    api_key/auth_token、primary_model 的解析规则（含代理与默认模型回落）
    只有一份实现；``provider_id`` 缺失时它会回落到用户默认模型。

    Args:
        arl_config: 会话的 ``arl_config``，形如
            ``{"worker": {"provider_id": ..., "model_name": ...}, "validator": {...}}``
        user_id: 当前用户 ID（解析默认模型用）

    Returns:
        ``{"worker": <凭证字典>, "validator": <凭证字典>}``。
    """
    resolved: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        role_config = arl_config.get(role) or {}
        resolved[role] = resolve_provider_for_arc(
            role_config.get("provider_id"),
            role_config.get("model_name"),
            user_id,
        )
    return resolved


def _credential(provider_config: dict[str, Any], *, prefer_auth_token: bool = False) -> str:
    """取凭证字段。

    Args:
        provider_config: :func:`resolve_provider_for_arc` 的返回值
        prefer_auth_token: True 时优先用 ``auth_token``（Claude CLI 走
            ``ANTHROPIC_AUTH_TOKEN``，provider 同时配了两者时它才是对的那个）；
            False 时优先用 ``api_key``。

    Returns:
        凭证字符串；两者皆空（例如 mock 兜底）时返回空串。
    """
    api_key = provider_config.get("api_key") or ""
    auth_token = provider_config.get("auth_token") or ""
    if prefer_auth_token:
        return auth_token or api_key
    return api_key or auth_token


async def _push(
    websocket: WebSocket,
    msg_type: str,
    data: dict,
    role: str | None = None,
) -> None:
    """向前端推送一帧，并盖上角色标识。

    Args:
        websocket: 前端连接
        msg_type: 帧类型（``system`` / ``error`` / ``timeline_event`` ...）
        data: 帧负载
        role: 角色标识；``None`` 表示该帧不属于某个具体容器
            （例如容器启动前的 system 帧）。除 ``agents_ready`` 外，
            前端按 ``role`` 分流，因此这里统一从这里盖章。
    """
    frame: dict = {"type": msg_type, "data": data}
    if role:
        frame["role"] = role
    await websocket.send_json(frame)


async def _ensure_agent(
    client: PaseoDaemonClient,
    role: str,
    model: str | None = None,
    mode_id: str | None = None,
) -> dict[str, Any]:
    """确保容器里有一个可用的 agent，返回它的快照。

    同时通过 fetch_agents 订阅 agent 目录更新，这样后续的
    agent_update / agent_stream 事件才会推到本连接。

    权限模式在这里对齐：新建时直接带上 ``modeId``；复用已有 agent 时，
    只有当它当前的模式和期望不一致才切换（切换失败不致命，退回逐条确认）。

    Args:
        client: 已完成握手的 daemon 客户端
        role: 容器角色（worker / validator）
        model: 新建 agent 时使用的模型名（即会话 provider 解析出的
            ``primary_model``）。``None`` 表示用 CLI 自己的默认模型。
            复用已有 agent 时不改其模型。
        mode_id: 期望的权限模式，见 :data:`ROLE_DEFAULT_MODES`。

    Returns:
        agent 快照，至少含 ``id``，通常还有 ``currentModeId`` /
        ``availableModes`` / ``pendingPermissions``。
    """
    agents = await client.fetch_agents(subscribe=True)
    if agents:
        entry = agents[0]
        snapshot = entry.get("agent")
        agent = dict(snapshot) if isinstance(snapshot, dict) else dict(entry)
        agent_id = agent.get("id")
        logger.info(
            "♻️ [{}] 复用已有 agent: id={}, status={}, mode={}",
            role,
            agent_id,
            agent.get("status"),
            agent.get("currentModeId"),
        )
        if mode_id and agent_id and agent.get("currentModeId") != mode_id:
            try:
                await client.set_agent_mode(agent_id, mode_id)
                agent["currentModeId"] = mode_id
                logger.info("🔧 [{}] 已把权限模式切到 {}", role, mode_id)
            except Exception as e:
                # 模式切换失败只影响「是否逐条确认」，不该把会话拖垮
                logger.warning("⚠️ [{}] 切换权限模式到 {} 失败: {}", role, mode_id, e)
        return agent

    provider = ROLE_PROVIDERS.get(role, "claude")
    agent = await client.create_agent(
        provider=provider, title=f"arl-{role}", model=model, mode_id=mode_id
    )
    logger.info(
        "🆕 [{}] 已创建 agent: provider={}, model={}, mode={}, id={}",
        role,
        provider,
        model,
        agent.get("currentModeId") or mode_id,
        agent.get("id"),
    )
    return agent


def _agent_summary(agent: dict[str, Any]) -> dict[str, Any]:
    """把 agent 快照压成前端面板需要的那几项。

    Args:
        agent: daemon 的 agent 快照

    Returns:
        ``agents_ready`` 帧里每个角色的数据。
    """
    return {
        "agent_id": agent.get("id"),
        "status": agent.get("status"),
        # 非空表示这个 agent 正在跑一个回合：刷新页面后靠它把「停止」按钮恢复出来
        "active_turn": agent.get("activeTurn"),
        "mode_id": agent.get("currentModeId"),
        "modes": agent.get("availableModes") or [],
        # 刷新页面后仍未处理的确认请求，否则面板会一直停在「运行中」
        "pending_permissions": agent.get("pendingPermissions") or [],
    }


def _persist_role_mode(session_id: uuid.UUID, role: str, mode_id: str) -> None:
    """把用户选的权限模式写回会话配置，下次连接沿用。

    Args:
        session_id: 会话 ID
        role: 容器角色（worker / validator）
        mode_id: 模式 ID
    """
    try:
        with Session(engine) as db:
            session = db.get(ResearchSession, session_id)
            if not session:
                return
            # JSON 列要整体换成新 dict，原地改 SQLAlchemy 认不出来
            arl_config = dict(session.arl_config or {})
            role_config = dict(arl_config.get(role) or {})
            role_config["mode_id"] = mode_id
            arl_config[role] = role_config
            session.arl_config = arl_config
            db.add(session)
            db.commit()
    except Exception as e:
        # 持久化失败只影响下次连接的默认值，当前会话已经切好了
        logger.warning("⚠️ [{}] 保存权限模式失败: {}", role, e)


async def _backfill_history(
    websocket: WebSocket,
    client: PaseoDaemonClient,
    role: str,
    agent_id: str,
) -> None:
    """把容器里已有的聊天记录回填给前端。

    Paseo daemon 只在订阅后推送增量事件，不会补发历史，所以刷新页面
    或重连后面板会是空的。这里主动拉一次 tail 历史。

    历史条目与实时 ``agent_stream`` 事件里的 item 结构一致，只是服务端
    已折叠好（``collapsed``），前端按 ``agentId`` 分流即可。

    Args:
        websocket: 前端连接
        client: 已握手的 daemon 客户端
        role: 容器角色（worker / validator）
        agent_id: 目标 agent ID
    """
    if not agent_id:
        return

    try:
        payload = await client.fetch_agent_timeline(agent_id, limit=HISTORY_LIMIT)
    except Exception as e:
        # 历史拉取失败不该阻断实时流，前端至少还能拿到后续增量
        logger.warning(f"⚠️ [{role}] 拉取历史失败，跳过回填: {e}")
        return

    await _push(
        websocket,
        "timeline_history",
        {
            "agent_id": agent_id,
            "entries": payload.get("entries") or [],
            "has_older": bool(payload.get("hasOlder")),
            "epoch": payload.get("epoch"),
        },
        role=role,
    )


async def _authenticate_ws(
    websocket: WebSocket,
    token: str,
    session_id: uuid.UUID,
    db: Session,
) -> tuple[User, ResearchSession] | None:
    """验证 WebSocket 连接。

    Returns:
        (user, session) if valid, None otherwise
    """
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload.get("sub"))

        user = db.exec(select(User).where(User.id == user_id)).first()
        if not user:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid user")
            return None

        session = db.exec(
            select(ResearchSession).where(
                ResearchSession.id == session_id,
                ResearchSession.user_id == user_id,
            )
        ).first()

        if not session:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session not found")
            return None

        if session.session_type != "arl":
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Not an ARL session")
            return None

        return user, session

    except Exception as e:
        logger.error(f"WebSocket auth failed: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Auth failed")
        return None


@router.websocket("/sessions/{session_id}/arl/ws")
async def arl_websocket(
    websocket: WebSocket,
    session_id: uuid.UUID,
    token: str = Query(..., description="JWT access token"),
):
    """ARL WebSocket 端点：前端 ↔ daemon 消息中转。

    前端发送消息格式：
    ```json
    {
      "type": "agent_send" | "agent_cancel" | "set_agent_mode"
            | "file_read" | "file_write" | "file_list" | "permission_response",
      "role": "worker" | "validator",
      "payload": { ... }
    }
    ```

    后端推送消息格式：
    ```json
    {
      "type": "timeline_history" | "timeline_event" | "agents_ready"
            | "agent_cancel_result" | "agent_mode_result"
            | "file_content" | "file_list" | "error" | "system",
      "role": "worker" | "validator",
      "data": { ... }
    }
    ```

    ``role`` 是每条推送必带的角色标识：除了 ``agents_ready``（其 data
    自带 worker/validator 两个键）之外，所有帧都带。前端据此把消息
    分流到左边的 Worker 面板或右边的 Validator 面板。
    """
    logger.info(f"🔌 [Session {session_id}] New WebSocket connection request")
    await websocket.accept()
    logger.info(f"✅ [Session {session_id}] WebSocket accepted")

    # 1. 鉴权
    with Session(engine) as db:
        logger.info(f"🔐 [Session {session_id}] Authenticating...")
        auth_result = await _authenticate_ws(websocket, token, session_id, db)
        if not auth_result:
            logger.warning(f"❌ [Session {session_id}] Authentication failed")
            return
        user, session = auth_result
        logger.info(f"✅ [Session {session_id}] Authenticated: user_id={user.id}, email={user.email}")
        # 会话关闭后 ORM 实例可能脱离，先把后续要用的字段取成普通值
        user_id = user.id
        arl_config = dict(session.arl_config or {})

    # 2. 解析两个角色的 provider 凭证
    #    容器启动（API base_url/key）与 agent 创建（model）都要用，所以
    #    无论容器是否已存在都先解析一次。
    role_providers = _resolve_role_providers(arl_config, user_id)
    for role in ROLES:
        provider_config = role_providers[role]
        logger.info(
            "📋 [Session {}] {} provider: type={}, base_url={}, model={}",
            session_id,
            role,
            provider_config["provider"],
            provider_config["base_url"],
            provider_config["primary_model"],
        )

    # 3. 按需启动容器
    runtime = get_arl_runtime()

    # 使用数据库 session_id 作为容器标识
    container_session_id = str(session_id)
    arl_session = runtime.get_session(container_session_id)

    if not arl_session:
        # 首次连接，启动容器
        logger.info(f"🚀 [Session {session_id}] ARL session not found, starting new containers...")
        if not arl_config:
            logger.error(f"❌ [Session {session_id}] ARL config not found in database")
            await _push(websocket, "error", {"message": "ARL config not found"})
            await websocket.close()
            return

        # 没有可用凭证时容器起来也调不通大模型，提前失败
        missing = [role for role in ROLES if not _credential(role_providers[role])]
        if missing:
            logger.error(
                f"❌ [Session {session_id}] No usable credential for: {missing}"
            )
            await _push(
                websocket,
                "error",
                {"message": f"Incomplete ARL config: no credential for {', '.join(missing)}"},
            )
            await websocket.close()
            return

        try:
            await _push(websocket, "system", {"message": "Starting ARL containers..."})
            logger.info(f"📦 [Session {session_id}] Notified frontend: starting containers")

            worker_provider = role_providers["worker"]
            validator_provider = role_providers["validator"]

            # 准备 daemon 配置（base_url 的 /v1 后缀由 runtime 按角色规范化）
            from app.services.arl.types import ARLDaemonConfig

            daemon_config = ARLDaemonConfig(
                # Worker (Claude)
                base_url=worker_provider["base_url"],
                auth_token=_credential(worker_provider, prefer_auth_token=True),
                model=worker_provider["primary_model"],
                small_fast_model=(arl_config.get("worker") or {}).get("small_fast_model"),
                # Validator (OpenAI/Codex)
                validator_base_url=validator_provider["base_url"],
                validator_api_key=_credential(validator_provider),
                validator_model=validator_provider["primary_model"],
            )

            # 启动容器（传入数据库 session_id）
            arl_session = await runtime.create_session(
                session_id=container_session_id,
                project_id=0,  # TODO: 使用实际的 project_id
                user_id=str(user_id),
                daemon_config=daemon_config,
            )

            await _push(websocket, "system", {"message": "ARL session ready"})

        except Exception as e:
            logger.error(f"Failed to start ARL session: {e}")
            await _push(websocket, "error", {"message": f"Failed to start: {e}"})
            await websocket.close()
            return
    else:
        # 容器已存在，直接连接
        logger.info(f"✅ [Session {session_id}] ARL session already exists, reconnecting...")
        await _push(websocket, "system", {"message": "ARL session ready"})

    # 4. 连接两个 daemon
    worker_client = None
    validator_client = None

    try:
        # 自动检测运行环境，选择正确的连接方式
        from app.core.env_detect import use_container_network

        if use_container_network():
            # backend 在容器内：通过 Docker 网络连接（容器名 + 内部端口）
            worker_url = f"ws://{arl_session.worker.name}:6767/ws"
            validator_url = f"ws://{arl_session.validator.name}:6767/ws"
            logger.info(f"🔌 [Session {session_id}] Connecting via Docker network: worker={arl_session.worker.name}, validator={arl_session.validator.name}")
        else:
            # backend 在宿主机：通过 localhost 连接（映射端口）
            worker_url = f"ws://127.0.0.1:{arl_session.worker.port}/ws"
            validator_url = f"ws://127.0.0.1:{arl_session.validator.port}/ws"
            logger.info(f"🔌 [Session {session_id}] Connecting via localhost: worker_port={arl_session.worker.port}, validator_port={arl_session.validator.port}")

        worker_client = PaseoDaemonClient(
            url=worker_url,
            password=arl_session.worker.password,
            cwd="/workspace/worker",
        )
        validator_client = PaseoDaemonClient(
            url=validator_url,
            password=arl_session.validator.password,
            cwd="/workspace/validator",
        )

        logger.info("🔌 [Worker] Connecting...")
        await worker_client.connect()
        logger.info("✅ [Worker] Connected successfully")

        logger.info("🔌 [Validator] Connecting...")
        await validator_client.connect()
        logger.info("✅ [Validator] Connected successfully")

        # 确保两个容器里都有 agent，并订阅其事件
        # model 用会话 provider 解析出的 primary_model，否则新建的 agent
        # 会退回 CLI 默认模型，和会话里选的模型不一致。
        # mode 用会话里存的模式（用户在界面上改过的），没有就用角色默认。
        worker_mode = (arl_config.get("worker") or {}).get("mode_id") or ROLE_DEFAULT_MODES[
            "worker"
        ]
        validator_mode = (arl_config.get("validator") or {}).get(
            "mode_id"
        ) or ROLE_DEFAULT_MODES["validator"]

        worker_agent = await _ensure_agent(
            worker_client,
            "worker",
            role_providers["worker"]["primary_model"],
            worker_mode,
        )
        validator_agent = await _ensure_agent(
            validator_client,
            "validator",
            role_providers["validator"]["primary_model"],
            validator_mode,
        )
        worker_agent_id = worker_agent.get("id")
        validator_agent_id = validator_agent.get("id")

        # 先把已有的聊天记录回填给前端（刷新/重连后靠它恢复消息列表），
        # 再广播 agents_ready，避免前端在 agent id 就绪前先收到历史。
        await _backfill_history(websocket, worker_client, "worker", worker_agent_id)
        await _backfill_history(websocket, validator_client, "validator", validator_agent_id)

        await websocket.send_json({
            "type": "agents_ready",
            "data": {
                "worker": _agent_summary(worker_agent),
                "validator": _agent_summary(validator_agent),
            },
        })

        logger.info(f"✅ [Session {session_id}] All daemons connected, starting message forwarding...")

        # 5. 启动三个并发任务
        tasks = [
            asyncio.create_task(
                _forward_from_frontend(
                    websocket,
                    session_id,
                    worker_client,
                    validator_client,
                    worker_agent_id,
                    validator_agent_id,
                )
            ),
            asyncio.create_task(_forward_timeline(websocket, worker_client, "worker")),
            asyncio.create_task(_forward_timeline(websocket, validator_client, "validator")),
        ]

        logger.info(f"🚀 [Session {session_id}] Started 3 forwarding tasks")

        # 等待任务完成，但不使用 return_exceptions
        # 如果任何一个 task 失败，其他 tasks 会被取消
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"❌ [Session {session_id}] One of the forwarding tasks failed: {e}", exc_info=True)
            # 取消所有其他任务
            for task in tasks:
                if not task.done():
                    task.cancel()
            # 等待所有任务清理完成
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        logger.info(f"🏁 [Session {session_id}] All forwarding tasks completed")

    except WebSocketDisconnect:
        logger.info(f"🔌 [Session {session_id}] Frontend WebSocket disconnected normally")
    except Exception as e:
        logger.error(f"❌ [Session {session_id}] ARL WebSocket error: {e}", exc_info=True)
        try:
            await _push(websocket, "error", {"message": str(e)})
        except Exception as send_err:
            logger.warning(f"⚠️ [Session {session_id}] Failed to send error to frontend: {send_err}")
    finally:
        logger.info(f"🧹 [Session {session_id}] Cleaning up daemon connections...")
        if worker_client:
            logger.info("🔌 [Worker] Closing connection...")
            await worker_client.close()
            logger.info("✅ [Worker] Connection closed")
        if validator_client:
            logger.info("🔌 [Validator] Closing connection...")
            await validator_client.close()
            logger.info("✅ [Validator] Connection closed")
        logger.info(f"✅ [Session {session_id}] ARL WebSocket fully closed")


async def _forward_from_frontend(
    websocket: WebSocket,
    session_id: uuid.UUID,
    worker_client: PaseoDaemonClient,
    validator_client: PaseoDaemonClient,
    worker_agent_id: str,
    validator_agent_id: str,
):
    """转发前端消息到对应的 daemon。"""
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            role = data.get("role")
            payload = data.get("payload", {})

            logger.info(f"📨 [Frontend → Backend] Received message: type={msg_type}, role={role}, payload={payload}")

            is_worker = role == "worker"
            client = worker_client if is_worker else validator_client
            agent_id = worker_agent_id if is_worker else validator_agent_id
            target_name = "worker" if is_worker else "validator"

            if msg_type == "agent_send":
                # 发送消息给 agent
                content = payload.get("content", "")
                logger.info(f"📤 [Backend → {target_name}] Sending agent_send: agent={agent_id}, content_len={len(content)}")

                try:
                    await client.send_agent_message(
                        agent_id=payload.get("agent_id") or agent_id,
                        text=content,
                        active_turn_behavior=payload.get("active_turn_behavior"),
                    )
                    logger.info(f"✅ [Backend → {target_name}] agent_send sent successfully")
                except Exception as e:
                    logger.error(f"❌ [Backend → {target_name}] Failed to send agent_send: {e}")
                    raise

            elif msg_type == "file_read":
                # 读取文件
                logger.info(f"📤 [Backend → {target_name}] Reading file: path={payload['path']}")
                content = await client.read_file(payload["path"])
                await websocket.send_json({
                    "type": "file_content",
                    "role": role,
                    "data": {"path": payload["path"], "content": content}
                })
                logger.info(f"✅ [Backend → Frontend] Sent file_content: path={payload['path']}, len={len(content)}")

            elif msg_type == "file_write":
                # 写入文件
                logger.info(f"📤 [Backend → {target_name}] Writing file: path={payload['path']}, len={len(payload['content'])}")
                await client.write_file(payload["path"], payload["content"])
                await websocket.send_json({
                    "type": "file_written",
                    "role": role,
                    "data": {"path": payload["path"]}
                })
                logger.info(f"✅ [Backend → Frontend] File written: path={payload['path']}")

            elif msg_type == "file_list":
                # 列出目录（空路径表示项目根目录）
                path = payload.get("path", "")
                logger.info(f"📤 [Backend → {target_name}] Listing files: path={path!r}")
                files = await client.list_files(path)
                await websocket.send_json({
                    "type": "file_list",
                    "role": role,
                    "data": {"path": path, "files": files}
                })
                logger.info(f"✅ [Backend → Frontend] Sent file_list: path={path!r}, count={len(files)}")

            elif msg_type == "permission_response":
                # 响应权限请求；request_id 来自 agent_permission_request 事件
                request_id = payload["request_id"]
                response = payload["response"]
                logger.info(f"📤 [Backend → {target_name}] Permission response: request_id={request_id}, response={response}")
                await client.resolve_permission(
                    agent_id=payload.get("agent_id") or agent_id,
                    request_id=request_id,
                    response=response,
                )
                logger.info(f"✅ [Backend → {target_name}] Permission response sent")

            elif msg_type == "agent_cancel":
                # 停止当前回合（前端「停止」按钮）。失败不致命：只回一帧结果，
                # 让前端把按钮状态解开即可。
                target_agent = payload.get("agent_id") or agent_id
                logger.info(f"📤 [Backend → {target_name}] Cancel turn: agent={target_agent}")
                try:
                    await client.cancel_agent(target_agent)
                    await _push(
                        websocket, "agent_cancel_result", {"ok": True}, role=role
                    )
                    logger.info(f"✅ [Backend → {target_name}] Cancel sent")
                except Exception as e:
                    logger.warning(f"⚠️ [Backend → {target_name}] Cancel failed: {e}")
                    await _push(
                        websocket,
                        "agent_cancel_result",
                        {"ok": False, "message": str(e)},
                        role=role,
                    )

            elif msg_type == "set_agent_mode":
                # 切换权限模式（auto / bypass / 逐条确认…），并写回会话配置
                mode_id = payload.get("mode_id")
                target_agent = payload.get("agent_id") or agent_id
                logger.info(
                    f"📤 [Backend → {target_name}] Set mode: agent={target_agent}, mode={mode_id}"
                )
                if not mode_id:
                    await _push(
                        websocket,
                        "agent_mode_result",
                        {"ok": False, "message": "mode_id is required"},
                        role=role,
                    )
                    continue
                try:
                    await client.set_agent_mode(target_agent, mode_id)
                    _persist_role_mode(session_id, target_name, mode_id)
                    await _push(
                        websocket,
                        "agent_mode_result",
                        {"ok": True, "mode_id": mode_id},
                        role=role,
                    )
                    logger.info(f"✅ [Backend → {target_name}] Mode switched to {mode_id}")
                except Exception as e:
                    logger.warning(f"⚠️ [Backend → {target_name}] Set mode failed: {e}")
                    await _push(
                        websocket,
                        "agent_mode_result",
                        {"ok": False, "mode_id": mode_id, "message": str(e)},
                        role=role,
                    )

            else:
                logger.warning(f"⚠️ [Frontend → Backend] Unknown message type: {msg_type}")

    except WebSocketDisconnect:
        logger.info("🔌 [Frontend] WebSocket disconnected")
        # 正常断开，不抛出异常
    except Exception as e:
        logger.error(f"❌ [Frontend → Backend] Error forwarding message: {e}", exc_info=True)
        # 严重错误，抛出异常以关闭连接
        raise


async def _forward_timeline(
    websocket: WebSocket,
    client: PaseoDaemonClient,
    role: str,
):
    """订阅 daemon 的 agent 事件并转发给前端。

    事件是 Paseo 的 session 内层消息，形如
    ``{"type": "agent_stream", "payload": {...}}``。
    """
    try:
        logger.info(f"📡 [{role}] Started timeline subscription")
        async for event in client.subscribe_timeline_async():
            event_type = event.get("type", "unknown") if isinstance(event, dict) else "unknown"
            logger.debug(f"📨 [{role} → Frontend] Timeline event: type={event_type}")
            await websocket.send_json({
                "type": "timeline_event",
                "role": role,
                "data": event,
            })
            logger.debug(f"✅ [{role} → Frontend] Timeline event sent")

    except asyncio.CancelledError:
        logger.info(f"🛑 [{role}] Timeline subscription cancelled")
        # 正常取消，不抛出异常
    except Exception as e:
        logger.error(f"❌ [{role}] Error forwarding timeline: {e}", exc_info=True)
        # 严重错误，抛出异常以关闭连接
        raise
