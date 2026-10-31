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
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from loguru import logger
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.core.redis import touch_arl_session_active
from app.core.security import decode_access_token
from app.models.research import ResearchSession
from app.models.user import User
from app.services.arl.daemon_client import PaseoDaemonClient
from app.services.arl.runtime import daemon_endpoints, get_arl_runtime
from app.services.arl.types import ARLDaemonConfig
from app.services.arl.validation import (
    ValidationStatus,
    probe_validation,
    sync_to_worker,
)
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

#: 向上翻页时每页拉取的条目数。比首屏小一些，滚到顶部时加载更快
HISTORY_PAGE_LIMIT = 100

#: 两次因缺口重拉历史之间的最短间隔（秒），见 :func:`_reconcile_event`
RESYNC_COOLDOWN_SECONDS = 5.0

#: 前端允许发送的消息类型。白名单之外一律回 error 帧，不做静默忽略。
KNOWN_MESSAGE_TYPES = frozenset(
    {
        "agent_send",
        "agent_cancel",
        "set_agent_mode",
        "file_read",
        "file_write",
        "file_list",
        "permission_response",
        "sync_validation",
        "list_commands",
        "fs_request",
        "timeline_fetch_older",
        "subagent_timeline",
        "peer_timeline",
        "agent_reset",
    }
)

#: 右侧文件面板单次预览允许读取的最大字节数。超限由 daemon 直接报错，
#: 避免把大文件（模型权重、数据集）整个塞进 WS 帧。
FS_PREVIEW_MAX_BYTES = 2 * 1024 * 1024

#: ``fs_request`` 支持的操作。只读的 list/read 与变更类 write/create/rename/
#: duplicate/delete 共用一个消息类型和一个结果帧 ``fs_result``，前端按
#: ``request_id`` 配对。路径一律相对于该角色的项目目录，越界由 daemon 拒绝。
FS_OPS = frozenset({"list", "read", "write", "create", "rename", "duplicate", "delete"})


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


def _agent_title(role: str) -> str:
    """本角色创建的 agent 使用的标题。

    复用时按它反查，故与 :func:`_ensure_agent` 的创建参数共用一处定义。

    Args:
        role: 容器角色（worker / validator）

    Returns:
        agent 标题
    """
    return f"arl-{role}"


def _agent_snapshot(entry: dict[str, Any]) -> dict[str, Any]:
    """从目录条目里取出 agent 快照。

    daemon 既可能把快照嵌在 ``entry["agent"]`` 里，也可能条目本身就是快照。

    Args:
        entry: ``fetch_agents`` 返回的单个条目

    Returns:
        agent 快照（副本）
    """
    snapshot = entry.get("agent")
    return dict(snapshot) if isinstance(snapshot, dict) else dict(entry)


def _pick_agent_entry(
    entries: list[dict[str, Any]], role: str
) -> dict[str, Any] | None:
    """从 agent 目录里挑出本角色该复用的那个。

    优先认 :func:`_agent_title` 这个创建时写死的标题，避免容器里存在多个
    agent（已归档的、用户手动建的）时按 daemon 的返回序碰运气——挑中一个
    已归档的 agent 会让会话「连上了却收不到任何响应」，且日志里只有一句
    「复用已有 agent」，很难查。

    刻意不做严格的状态过滤：daemon 对归档/状态字段的全部取值尚未确认，
    过滤写窄了会导致每次连接都新建 agent，容器里越堆越多。故匹配不到标题
    时仍回落到首个条目，与原行为一致。唯一的例外是带 :data:`PEER_AGENT_LABEL`
    的 agent：它们是验证流程派生的同级 agent，绝不能被当成主 agent 复用。

    Args:
        entries: ``fetch_agents`` 返回的目录条目
        role: 容器角色（worker / validator）

    Returns:
        该复用的条目；目录为空（或只剩同级 agent）时返回 None。
    """
    if not entries:
        return None

    expected = _agent_title(role)
    for entry in entries:
        if _agent_snapshot(entry).get("title") == expected:
            return entry

    candidates = [e for e in entries if not _has_peer_label(_agent_snapshot(e))]
    if not candidates:
        logger.info(
            "ℹ️ [{}] agent 目录里只有 {} 个同级 agent，新建主 agent", role, len(entries)
        )
        return None
    logger.warning(
        "⚠️ [{}] agent 目录里没有标题为 {} 的条目（共 {} 个），回落到首个",
        role,
        expected,
        len(entries),
    )
    return candidates[0]


#: ARIS 验证流程用 ``paseo run --label aris.role=...`` 派生的 agent 带这个标签。
#: 它们与主 agent 同级（``parentAgentId`` 为空），不会出现在 provider 子 agent
#: 列表里，只能从 agent 目录里按标签挑出来。
PEER_AGENT_LABEL = "aris.role"


def _has_peer_label(agent: dict[str, Any]) -> bool:
    """agent 是否带 :data:`PEER_AGENT_LABEL` 标签。

    Args:
        agent: agent 快照

    Returns:
        True 表示由验证流程派生
    """
    labels = agent.get("labels")
    return isinstance(labels, dict) and bool(labels.get(PEER_AGENT_LABEL))


def _is_peer_agent(agent: dict[str, Any], main_agent_id: str | None) -> bool:
    """agent 是否应作为同级 agent 展示在子任务栏里。

    Args:
        agent: agent 快照
        main_agent_id: 该角色当前的主 agent ID

    Returns:
        带标签、不是主 agent、未归档且非 daemon 内部 agent 时为 True
    """
    return (
        _has_peer_label(agent)
        and agent.get("id") != main_agent_id
        and not agent.get("archivedAt")
        and not agent.get("internal")
    )


def _peer_summary(agent: dict[str, Any]) -> dict[str, Any]:
    """把同级 agent 快照压成子任务栏需要的字段（沿用 daemon 的字段名）。

    前端用同一个归一化函数处理这里的摘要和 ``agent_update`` 里的完整快照，
    故字段名保持 daemon 原样。

    Args:
        agent: agent 快照

    Returns:
        ``peer_agent_list`` 帧里的单个条目
    """
    return {
        "id": agent.get("id"),
        "title": agent.get("title"),
        "status": agent.get("status"),
        "labels": agent.get("labels") or {},
        "createdAt": agent.get("createdAt"),
        "updatedAt": agent.get("updatedAt"),
    }


#: codex 找不到线程 rollout 文件时的报错片段：线程从没收到过消息就不会落盘，
#: 这种 agent 永远恢复不了，只能换一个新的。
UNRECOVERABLE_THREAD_ERROR = "no rollout found"


async def _revive_closed_agent(
    client: PaseoDaemonClient, role: str, agent: dict[str, Any]
) -> dict[str, Any] | None:
    """让容器重启后处于 ``closed`` 的 agent 重新可用。

    daemon 重启后磁盘上的 agent 先以 ``closed`` 出现，provider 会话要另行
    恢复。claude 一般能自动恢复；codex 只在第一条消息后才写线程的 rollout
    文件，从没收到过消息的 agent 重启后就再也恢复不了（``no rollout found
    for thread id``）。这种 agent 发消息没反应、停止报 ``agent not found``，
    复用它会让整个面板失灵。

    故先显式 ``refresh`` 一次。只有报错明确是 provider 会话已不存在
    （:data:`UNRECOVERABLE_THREAD_ERROR`）才归档它，由调用方新建一个；这种
    agent 本来就无法继续对话，归档只是把它从目录里挪走，记录仍在磁盘上。
    其他失败（超时等）可能是暂时的，原样复用，下次连接再试。

    Args:
        client: 已完成握手的 daemon 客户端
        role: 容器角色（worker / validator）
        agent: 目录里挑中的 agent 快照

    Returns:
        可用的 agent 快照；恢复失败时返回 None，表示应新建。
    """
    agent_id = agent.get("id")
    if agent.get("status") != "closed" or not agent_id:
        return agent

    logger.info("🔄 [{}] agent {} 处于 closed，尝试重新加载", role, agent_id)
    try:
        await client.refresh_agent(agent_id)
        entries = await client.fetch_agents(subscribe=False)
    except Exception as e:
        if UNRECOVERABLE_THREAD_ERROR not in str(e):
            # 超时等可能是暂时性的失败：不归档，免得把一段还能恢复的对话挪走。
            # 沿用旧行为复用它，下次连接再试
            logger.warning("⚠️ [{}] agent {} 重新加载失败，暂按原样复用: {}", role, agent_id, e)
            return agent
        logger.warning(
            "⚠️ [{}] agent {} 的 provider 会话已不存在，归档后新建: {}", role, agent_id, e
        )
        try:
            await client.archive_agent(agent_id)
        except Exception as archive_err:
            # 归档失败也照样新建：旧 agent 反正用不了，面板至少能恢复工作
            logger.warning("⚠️ [{}] 归档 agent {} 失败: {}", role, agent_id, archive_err)
        return None

    for entry in entries:
        snapshot = _agent_snapshot(entry)
        if snapshot.get("id") == agent_id:
            logger.info(
                "✅ [{}] agent {} 已重新加载: status={}", role, agent_id, snapshot.get("status")
            )
            return snapshot
    agent["status"] = "idle"
    return agent


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
    entry = _pick_agent_entry(agents, role)
    if entry is not None:
        agent = await _revive_closed_agent(client, role, _agent_snapshot(entry))
    else:
        agent = None
    if agent is not None:
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
        # The agent outlives container recreation, so after the user changes
        # the session's model the reused agent must be switched explicitly.
        if model and agent_id and agent.get("model") != model:
            try:
                await client.set_agent_model(agent_id, model)
                logger.info(
                    "🔧 [{}] 已把模型从 {} 切到 {}", role, agent.get("model"), model
                )
                agent["model"] = model
            except Exception as e:
                # Not fatal: the agent keeps working on its previous model
                logger.warning("⚠️ [{}] 切换模型到 {} 失败: {}", role, model, e)
        return agent

    provider = ROLE_PROVIDERS.get(role, "claude")
    agent = await client.create_agent(
        provider=provider, title=_agent_title(role), model=model, mode_id=mode_id
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

    阻塞式数据库调用，调用方须经 ``asyncio.to_thread`` 下沉。

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


@dataclass
class _RoleStream:
    """一个角色的实时事件流，以及它与已推给前端的历史之间的对账位置。

    ``epoch`` / ``end_seq`` 是前端手里最新一条时间线行的位置（来自最近一次
    历史回填，或之后放行的实时事件）。daemon 给每条时间线行一个按 agent
    连续递增的 ``seq``，据此即可判断一条实时事件是重复（已在历史里）、
    正好接上，还是中间缺了一段——与原生客户端
    （``session-stream-reducers.ts`` 的 ``classifySessionTimelineSeq``）一致。

    Attributes:
        role: 容器角色（worker / validator）
        client: 该角色的 daemon 客户端
        agent_id: 当前主 agent；``agent_reset`` 后会被换掉
        queue: :meth:`PaseoDaemonClient.open_event_queue` 返回的事件队列
        epoch: 对账位置所在的时间线 epoch；``None`` 表示尚无基准
        end_seq: 对账位置的 seq
    """

    role: str
    client: PaseoDaemonClient
    agent_id: str
    queue: asyncio.Queue
    epoch: str | None = None
    end_seq: int | None = None
    #: 上一次因缺口重拉历史的时间（``time.monotonic``）
    last_resync: float = 0.0

    def reset_cursor(self) -> None:
        """丢掉对账基准，下一条时间线事件直接作为新起点。"""
        self.epoch = None
        self.end_seq = None


def _end_cursor(payload: dict[str, Any]) -> dict[str, Any] | None:
    """取一页历史最新一条的位置。

    优先用 daemon 给的 ``endCursor``；没有时按末条的 ``seqEnd`` 拼出来。

    Args:
        payload: daemon 的 ``fetch_agent_timeline_response`` payload

    Returns:
        ``{"epoch": str, "seq": int}``；历史为空时返回 None。
    """
    end = payload.get("endCursor")
    if (
        isinstance(end, dict)
        and isinstance(end.get("epoch"), str)
        and isinstance(end.get("seq"), int)
    ):
        return {"epoch": end["epoch"], "seq": end["seq"]}
    entries = payload.get("entries") or []
    epoch = payload.get("epoch")
    if entries and isinstance(epoch, str):
        last = entries[-1].get("seqEnd")
        if isinstance(last, int):
            return {"epoch": epoch, "seq": last}
    return None


async def _sync_history(websocket: WebSocket, stream: _RoleStream) -> bool:
    """拉一次 tail 历史推给前端（整体替换），并把对账基准移到历史末尾。

    用在三处：建连回填（daemon 只推订阅之后的增量，不补发历史）、实时
    事件出现缺口、daemon 重建了时间线。历史条目与实时事件的 item 结构
    一致，只是已在服务端折叠好。

    Args:
        websocket: 前端连接
        stream: 目标角色的事件流

    Returns:
        是否成功推送。失败时对账基准保持不变。
    """
    if not stream.agent_id:
        return False
    try:
        payload = await stream.client.fetch_agent_timeline(
            stream.agent_id, limit=HISTORY_LIMIT
        )
    except Exception as e:
        # 历史拉取失败不该阻断实时流，前端至少还能拿到后续增量
        logger.warning(f"⚠️ [{stream.role}] 拉取历史失败，跳过回填: {e}")
        return False

    cursor = _end_cursor(payload)
    stream.epoch = cursor["epoch"] if cursor else None
    stream.end_seq = cursor["seq"] if cursor else None
    await _push(
        websocket,
        "timeline_history",
        _timeline_page(stream.agent_id, payload),
        role=stream.role,
    )
    return True


def _classify_seq(stream: _RoleStream, epoch: str, seq: int) -> str:
    """判断一条实时时间线事件与对账基准的关系。

    Args:
        stream: 目标角色的事件流
        epoch: 事件的 epoch
        seq: 事件的 seq

    Returns:
        ``accept``（接上或尚无基准）、``drop``（已在历史里）、
        ``resync``（缺了一段，或 epoch 已变）。
    """
    if stream.epoch is None or stream.end_seq is None:
        return "accept"
    if epoch != stream.epoch:
        return "resync"
    if seq <= stream.end_seq:
        return "drop"
    if seq == stream.end_seq + 1:
        return "accept"
    return "resync"


async def _reconcile_event(
    websocket: WebSocket, stream: _RoleStream, event: Any
) -> bool:
    """按 seq 对账一条 daemon 推送，决定是否转发给前端。

    只管当前主 agent 的时间线事件；其余推送（回合状态、权限、子 agent、
    其他 agent）原样放行，由前端按 ``agentId`` 自行过滤。

    缺口与 epoch 变化都靠重拉 tail 历史修复，而不是按游标补拉增量：
    增量页里折叠好的条目可能与前端已有的流式片段重叠，拼接会叠出重复
    文本；整体替换则一定正确，代价只是向上翻页加载过的更早消息需要
    重新翻。

    Args:
        websocket: 前端连接
        stream: 目标角色的事件流
        event: daemon 推送的 session 内层消息

    Returns:
        True 表示转发给前端。
    """
    if not isinstance(event, dict):
        return True
    msg_type = event.get("type")
    payload = event.get("payload")
    if not isinstance(payload, dict) or payload.get("agentId") != stream.agent_id:
        return True

    if msg_type == "agent.timeline.replacement":
        # 时间线被重建（例如 rewind）：旧 epoch 的一切都作废，以新历史为准。
        # 这一帧本身不转发，前端收到的是随后的 timeline_history。
        logger.info(f"🔁 [{stream.role}] daemon 重建了时间线，重拉历史")
        await _sync_history(websocket, stream)
        return False

    inner = payload.get("event")
    if msg_type != "agent_stream" or not isinstance(inner, dict):
        return True
    if inner.get("type") != "timeline":
        return True
    seq = payload.get("seq")
    epoch = payload.get("epoch")
    # bool 是 int 的子类，单独排除；没带位置的事件无从对账，直接放行
    if not isinstance(seq, int) or isinstance(seq, bool) or not isinstance(epoch, str):
        return True

    decision = _classify_seq(stream, epoch, seq)
    if decision == "drop":
        return False
    if (
        decision == "resync"
        and time.monotonic() - stream.last_resync < RESYNC_COOLDOWN_SECONDS
    ):
        # 冷却期内不再重拉：以这条事件为新基准继续。若 daemon 的 seq 出现
        # 系统性跳号，没有这道闸每条事件都会触发一次整页历史拉取
        decision = "accept"
    if decision == "resync":
        stream.last_resync = time.monotonic()
        logger.warning(
            "⚠️ [{}] 时间线不连续（基准 {}#{}，收到 {}#{}），重拉历史",
            stream.role,
            stream.epoch,
            stream.end_seq,
            epoch,
            seq,
        )
        await _sync_history(websocket, stream)
        # 重拉成功时这条事件通常已含在新历史里；重拉失败或基准仍对不上时
        # 宁可放行并以它为新基准，也不能让后续事件全被当作缺口反复重拉。
        if _classify_seq(stream, epoch, seq) == "drop":
            return False
    stream.epoch = epoch
    stream.end_seq = seq
    return True


def _timeline_page(agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """把 ``fetch_agent_timeline`` 的响应压成推给前端的一页历史。

    ``start_cursor`` 是这一页最早一条的位置，前端向上翻页时原样带回来，
    作为下一次 ``direction=before`` 请求的游标。daemon 没给 ``startCursor``
    时按首条的 ``seqStart`` 自行拼出来。

    Args:
        agent_id: 目标 agent ID
        payload: daemon 的 ``fetch_agent_timeline_response`` payload

    Returns:
        ``timeline_history`` / ``timeline_older`` 帧的数据部分。
    """
    entries = payload.get("entries") or []
    epoch = payload.get("epoch")
    start_cursor = payload.get("startCursor")
    if not start_cursor and entries and epoch:
        first_seq = entries[0].get("seqStart")
        if isinstance(first_seq, int):
            start_cursor = {"epoch": epoch, "seq": first_seq}
    agent = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
    return {
        "agent_id": agent_id,
        "entries": entries,
        "has_older": bool(payload.get("hasOlder")),
        "epoch": epoch,
        "start_cursor": start_cursor,
        # 回合仍在进行时，末条文本还会被后续实时片段续写，前端据此保持它
        # 处于流式状态，而不是另起一行
        "active_turn": bool(agent.get("activeTurn")),
    }


def _parse_cursor(raw: Any) -> dict[str, Any]:
    """校验前端带回来的时间线游标。

    Args:
        raw: 前端 payload 里的 ``cursor``

    Returns:
        ``{"epoch": str, "seq": int}``

    Raises:
        ValueError: 游标缺字段或类型不对。
    """
    if not isinstance(raw, dict):
        raise ValueError("cursor is required")
    epoch = raw.get("epoch")
    seq = raw.get("seq")
    if not isinstance(epoch, str) or not epoch:
        raise ValueError("cursor.epoch must be a non-empty string")
    # bool 是 int 的子类，单独排除
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise ValueError("cursor.seq must be a non-negative integer")
    return {"epoch": epoch, "seq": seq}


async def _push_subagents(
    websocket: WebSocket,
    client: PaseoDaemonClient,
    role: str,
    agent_id: str,
) -> None:
    """把某个 agent 当前的子 agent 列表推给前端（刷新/重连后恢复子任务栏）。

    之后的增量由 daemon 的 ``agent.provider_subagents.update`` 推送，经
    ``timeline_event`` 原样转发。列表拉取失败只记日志：子任务栏是辅助信息，
    不该拦住主对话。

    Args:
        websocket: 前端连接
        client: 已握手的 daemon 客户端
        role: 容器角色（worker / validator）
        agent_id: 父 agent ID
    """
    if not agent_id:
        return
    try:
        subagents = await client.list_provider_subagents(agent_id)
    except Exception as e:
        logger.warning(f"⚠️ [{role}] 拉取子 agent 列表失败，跳过: {e}")
        return
    await _push(
        websocket,
        "subagent_list",
        {"agent_id": agent_id, "subagents": subagents},
        role=role,
    )


async def _push_peer_agents(
    websocket: WebSocket,
    client: PaseoDaemonClient,
    role: str,
    agent_id: str,
) -> None:
    """把容器里的同级 agent 列表推给前端（见 :data:`PEER_AGENT_LABEL`）。

    之后的状态变化由已订阅的 ``agent_update`` 推送，经 ``timeline_event``
    原样转发，前端据此增量更新。拉取失败只记日志：这是辅助信息，不该拦住
    主对话。

    Args:
        websocket: 前端连接
        client: 已握手的 daemon 客户端
        role: 容器角色（worker / validator）
        agent_id: 该角色的主 agent ID（排除用）
    """
    try:
        entries = await client.fetch_agents(subscribe=False)
    except Exception as e:
        logger.warning(f"⚠️ [{role}] 拉取同级 agent 列表失败，跳过: {e}")
        return
    peers = [
        _peer_summary(agent)
        for agent in (_agent_snapshot(entry) for entry in entries)
        if _is_peer_agent(agent, agent_id)
    ]
    await _push(
        websocket,
        "peer_agent_list",
        {"agent_id": agent_id, "peers": peers},
        role=role,
    )


async def _fetch_peer_timeline(
    client: PaseoDaemonClient, main_agent_id: str, peer_id: str
) -> dict[str, Any]:
    """拉一个同级 agent 的 tail 历史，只允许已加载在内存里的 agent。

    daemon 处理 ``fetch_agent_timeline_request`` 时会先 ``ensureAgentLoaded``：
    不在内存里的 agent（容器重启后显示为 ``closed``）会被从磁盘恢复、重新
    拉起一个 provider 进程。仅为查看就唤醒它不划算，故先查目录确认状态。
    前端也会置灰 ``closed`` 卡片，这里是兜底，防止过期状态或手工构造的帧。

    Args:
        client: 该角色的 daemon 客户端
        main_agent_id: 该角色的主 agent ID
        peer_id: 要查看的同级 agent ID

    Returns:
        daemon 的 ``fetch_agent_timeline_response`` payload

    Raises:
        ValueError: 目标不是同级 agent，或已不在内存里。
    """
    entries = await client.fetch_agents(subscribe=False)
    agent = next(
        (
            snapshot
            for snapshot in (_agent_snapshot(entry) for entry in entries)
            if snapshot.get("id") == peer_id
        ),
        None,
    )
    if agent is None or not _is_peer_agent(agent, main_agent_id):
        raise ValueError(f"not a peer agent: {peer_id}")
    if agent.get("status") == "closed":
        raise ValueError("agent is closed; its transcript is unavailable")
    return await client.fetch_agent_timeline(peer_id, limit=HISTORY_LIMIT)


def _load_role_mode(session_id: uuid.UUID, role: str) -> str:
    """读出会话里该角色当前的权限模式（阻塞，供 ``to_thread`` 调用）。

    每次切换模式都会经 :func:`_persist_role_mode` 写回，所以这里拿到的
    就是用户最近一次的选择；没存过则用角色默认值。

    Args:
        session_id: 会话 ID
        role: 容器角色（worker / validator）

    Returns:
        模式 ID
    """
    try:
        with Session(engine) as db:
            session = db.get(ResearchSession, session_id)
            arl_config = (session.arl_config or {}) if session else {}
            mode_id = (arl_config.get(role) or {}).get("mode_id")
            if mode_id:
                return str(mode_id)
    except Exception as e:
        logger.warning("⚠️ [{}] 读取权限模式失败，用默认值: {}", role, e)
    return ROLE_DEFAULT_MODES[role]


async def _reset_agent(
    *,
    session_id: uuid.UUID,
    client: PaseoDaemonClient,
    role: str,
    agent_id: str,
    model: str | None,
) -> dict[str, Any]:
    """``/clear``：归档当前 agent，再用同样的 provider / 模型 / 模式新建一个。

    与原生 UI 的 ``/clear`` 一致（归档旧 agent、开一个空白会话）。容器和
    工作区文件不动，只是对话上下文从零开始。先归档再新建：归档失败时
    旧 agent 原样保留，会话不会落到「两个同名 agent」的状态。

    Args:
        session_id: 会话 ID（读权限模式用）
        client: 该角色的 daemon 客户端
        role: 容器角色（worker / validator）
        agent_id: 要归档的 agent ID
        model: 新 agent 使用的模型名

    Returns:
        新 agent 的快照。
    """
    mode_id = await asyncio.to_thread(_load_role_mode, session_id, role)
    await client.archive_agent(agent_id)
    agent = await client.create_agent(
        provider=ROLE_PROVIDERS.get(role, "claude"),
        title=_agent_title(role),
        model=model,
        mode_id=mode_id,
    )
    logger.info(
        "🧹 [{}] 已重置 agent: old={}, new={}, model={}, mode={}",
        role,
        agent_id,
        agent.get("id"),
        model,
        mode_id,
    )
    return agent


def _load_session_context(
    token: str, session_id: uuid.UUID
) -> tuple[uuid.UUID, str, str | None, dict] | None:
    """校验 token 并取出会话上下文（阻塞，供 ``to_thread`` 调用）。

    一次性把后续要用的字段取成普通值返回，避免把 ORM 实例或 DB session
    带出线程（``Session`` 关闭后实例会脱离）。

    Args:
        token: JWT access token
        session_id: 会话 ID

    Returns:
        ``(user_id, user_email, folder_id, arl_config)``；校验失败返回 None。
    """
    try:
        payload = decode_access_token(token)
        subject = (payload or {}).get("sub")
        if not subject:
            logger.warning("ARL WS 鉴权失败：token 缺少 sub")
            return None
        user_id = uuid.UUID(subject)
    except Exception as e:
        logger.warning(f"ARL WS token 解析失败: {e}")
        return None

    with Session(engine) as db:
        user = db.exec(select(User).where(User.id == user_id)).first()
        if not user:
            logger.warning(f"ARL WS 鉴权失败：用户 {user_id} 不存在")
            return None
        if not user.is_active:
            logger.warning(f"ARL WS auth failed: user {user_id} is inactive")
            return None

        session = db.exec(
            select(ResearchSession).where(
                ResearchSession.id == session_id,
                ResearchSession.user_id == user_id,
            )
        ).first()
        if not session:
            logger.warning(f"ARL WS 鉴权失败：会话 {session_id} 不存在或不属于该用户")
            return None
        if session.session_type != "arl":
            logger.warning(f"ARL WS 鉴权失败：会话 {session_id} 不是 ARL 类型")
            return None

        return (
            user.id,
            user.email,
            str(session.folder_id) if session.folder_id else None,
            dict(session.arl_config or {}),
        )


async def _watch_validation(websocket: WebSocket, session_id: uuid.UUID) -> None:
    """轮询 Validator 的验证服务状态，变化时推给前端的 worker 角色。

    验证服务是用户在 Validator 侧交互后由 agent 自行拉起的，daemon 不感知，
    没有事件可订阅，只能轮询文件（见 :mod:`app.services.arl.validation`）。

    只在**前端可见状态发生变化**时推帧：每轮都推会把前端的消息处理和日志
    刷满，而用户关心的只有「按钮该不该出现」。首轮无论如何推一次，让界面
    在连接建立后立刻有确定状态，而不是空着等第一次变化。

    探测异常只记日志不外抛：这是个辅助功能，它失败不该把整条会话拆掉
    （上层 ``asyncio.wait`` 是 FIRST_COMPLETED，这里一退出整个会话就收摊）。

    Args:
        websocket: 前端连接
        session_id: 会话 ID
    """
    last: ValidationStatus | None = None
    while True:
        try:
            status = await asyncio.to_thread(probe_validation, str(session_id))
            if not status.same_state_as(last):
                logger.info(
                    "🔎 [Session {}] 验证服务状态变化: configured={}, url={}, has_task={}, reason={}",
                    session_id,
                    status.configured,
                    status.url,
                    status.has_task,
                    status.reason,
                )
                if status.detail:
                    logger.debug(
                        "🔎 [Session {}] 验证服务探测详情: {}", session_id, status.detail
                    )
                await _push(
                    websocket, "validation_status", status.to_payload(), role="worker"
                )
                last = status
        except asyncio.CancelledError:
            logger.info(f"🛑 [Session {session_id}] 验证服务轮询已取消")
            raise
        except WebSocketDisconnect:
            logger.info(f"🔌 [Session {session_id}] 推送验证状态时前端已断开")
            return
        except Exception as e:
            # 不更新 last：下一轮重新探测并重推，不会因这次失败漏掉状态变化
            logger.warning(
                f"⚠️ [Session {session_id}] 验证服务探测失败: {e}", exc_info=True
            )

        await asyncio.sleep(settings.ARL_VALIDATION_POLL_INTERVAL)


async def _heartbeat(session_id: uuid.UUID) -> None:
    """连接期间周期性刷新会话活跃时间。

    空闲回收靠活跃时间戳决定停谁的容器。只在连接建立时打一次是不够的：
    用户长时间静默观察 agent 输出（ARL 的常态）会越过空闲阈值，容器被
    停掉，正在跑的回合就断了。故连接存续期间持续心跳。

    心跳只是辅助功能：单次刷新失败只记日志，下个周期再试。否则它一旦
    抛异常就会结束，触发 ``asyncio.wait(FIRST_COMPLETED)`` 提前收摊，
    把正在跑的会话一起断掉。``CancelledError`` 不属于 ``Exception``，
    取消语义不受影响。

    Args:
        session_id: 会话 ID
    """
    while True:
        try:
            await asyncio.to_thread(touch_arl_session_active, str(session_id))
        except Exception:
            logger.opt(exception=True).warning(
                "⚠️ [Session {}] 刷新活跃时间失败，下个周期重试", session_id
            )
        await asyncio.sleep(settings.ARL_SESSION_HEARTBEAT_INTERVAL)


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
            | "file_read" | "file_write" | "file_list" | "permission_response"
            | "list_commands" | "fs_request" | "timeline_fetch_older"
            | "subagent_timeline" | "peer_timeline" | "agent_reset",
      "role": "worker" | "validator",
      "payload": { ... }
    }
    ```

    后端推送消息格式：
    ```json
    {
      "type": "timeline_history" | "timeline_event" | "agents_ready"
            | "agent_cancel_result" | "agent_mode_result" | "command_list"
            | "fs_result" | "timeline_older" | "subagent_list"
            | "subagent_timeline" | "agent_reset_result"
            | "peer_agent_list" | "peer_timeline"
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

    # 1. 鉴权（DB 调用下沉到线程池，不阻塞 event loop）
    logger.info(f"🔐 [Session {session_id}] Authenticating...")
    context = await asyncio.to_thread(_load_session_context, token, session_id)
    if context is None:
        logger.warning(f"❌ [Session {session_id}] Authentication failed")
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Auth failed"
        )
        return
    user_id, user_email, folder_id, arl_config = context
    logger.info(
        f"✅ [Session {session_id}] Authenticated: user_id={user_id}, email={user_email}"
    )

    # 2. 解析两个角色的 provider 凭证
    #    容器启动（API base_url/key）与 agent 创建（model）都要用，所以
    #    无论容器是否已存在都先解析一次。
    #    内部要查 provider 表，同样下沉到线程池。
    role_providers = await asyncio.to_thread(
        _resolve_role_providers, arl_config, user_id
    )
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

    # 3. 确保容器就绪
    #    ensure_session 幂等并自带跨进程互斥：不存在就创建、配置变了就重建、
    #    停着就原地拉起、在跑就复用，返回前一定按真实可达路径探活过。
    if not arl_config:
        logger.error(f"❌ [Session {session_id}] ARL config not found in database")
        await _push(websocket, "error", {"message": "ARL config not found"})
        await websocket.close()
        return

    # 没有可用凭证时容器起来也调不通大模型，提前失败
    missing = [role for role in ROLES if not _credential(role_providers[role])]
    if missing:
        logger.error(f"❌ [Session {session_id}] No usable credential for: {missing}")
        await _push(
            websocket,
            "error",
            {"message": f"Incomplete ARL config: no credential for {', '.join(missing)}"},
        )
        await websocket.close()
        return

    runtime = get_arl_runtime()
    container_session_id = str(session_id)
    worker_provider = role_providers["worker"]
    validator_provider = role_providers["validator"]

    # daemon 配置里装的是真实凭据；是否换发为代理 token 由 runtime 决定
    # （LLM_PROXY_ENABLE 开启时真实 key 不进容器）。base_url 的 /v1 后缀
    # 也由 runtime 按角色规范化。
    daemon_config = ARLDaemonConfig(
        # Worker (Claude)
        base_url=worker_provider["base_url"],
        auth_token=_credential(worker_provider, prefer_auth_token=True),
        model=worker_provider["primary_model"],
        small_fast_model=(arl_config.get("worker") or {}).get("small_fast_model"),
        provider_type=worker_provider.get("provider") or "anthropic",
        # Validator (OpenAI/Codex)
        validator_base_url=validator_provider["base_url"],
        validator_api_key=_credential(validator_provider),
        validator_model=validator_provider["primary_model"],
        validator_provider_type=validator_provider.get("provider") or "openai",
    )

    try:
        await _push(websocket, "system", {"message": "Preparing ARL containers..."})
        arl_session = await runtime.ensure_session(
            session_id=container_session_id,
            user_id=str(user_id),
            daemon_config=daemon_config,
            folder_id=folder_id,
        )
        await _push(websocket, "system", {"message": "ARL session ready"})
    except Exception as e:
        logger.error(f"❌ [Session {session_id}] Failed to prepare ARL session: {e}")
        await _push(websocket, "error", {"message": f"Failed to start: {e}"})
        await websocket.close()
        return

    # 4. 连接两个 daemon
    worker_client = None
    validator_client = None

    try:
        # 地址由 runtime 统一给出：容器网络走容器名 DNS，宿主机模式走映射
        # 端口。与探活用的是同一套地址，不会出现「探通了但连不上」。
        _, worker_url = daemon_endpoints(arl_session.worker)
        _, validator_url = daemon_endpoints(arl_session.validator)
        logger.info(
            f"🔌 [Session {session_id}] Connecting daemons: "
            f"worker={worker_url}, validator={validator_url}"
        )

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

        # 在订阅（_ensure_agent 里的 fetch_agents）之前就开始缓存推送，
        # 覆盖「订阅已生效、转发任务还没启动」的窗口，见 open_event_queue
        worker_queue = worker_client.open_event_queue()
        validator_queue = validator_client.open_event_queue()

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
        # daemon 理应总返回 id；缺了就没法把消息投给 agent，提前失败比让
        # 后续每条消息都带着 None agent_id 去 daemon 碰运气更好诊断。
        worker_agent_id = worker_agent.get("id")
        validator_agent_id = validator_agent.get("id")
        if not worker_agent_id or not validator_agent_id:
            raise RuntimeError(
                "daemon 返回的 agent 快照缺少 id："
                f"worker={worker_agent_id!r}, validator={validator_agent_id!r}"
            )

        streams = {
            "worker": _RoleStream("worker", worker_client, worker_agent_id, worker_queue),
            "validator": _RoleStream(
                "validator", validator_client, validator_agent_id, validator_queue
            ),
        }

        # 先把已有的聊天记录回填给前端（刷新/重连后靠它恢复消息列表），
        # 再广播 agents_ready，避免前端在 agent id 就绪前先收到历史。
        # 这期间到达的推送都在队列里，转发任务启动后按 seq 对账：已含在
        # 历史里的丢掉，其余照常转发。
        await _sync_history(websocket, streams["worker"])
        await _sync_history(websocket, streams["validator"])

        await websocket.send_json({
            "type": "agents_ready",
            "data": {
                "worker": _agent_summary(worker_agent),
                "validator": _agent_summary(validator_agent),
            },
        })

        # 子任务栏的初始列表，增量随后经 timeline_event 到达
        await _push_subagents(websocket, worker_client, "worker", worker_agent_id)
        await _push_subagents(websocket, validator_client, "validator", validator_agent_id)
        # 验证流程派生的同级 agent，同样进子任务栏
        await _push_peer_agents(websocket, worker_client, "worker", worker_agent_id)
        await _push_peer_agents(websocket, validator_client, "validator", validator_agent_id)

        logger.info(f"✅ [Session {session_id}] All daemons connected, starting message forwarding...")

        # 5. 启动并发任务：前端→daemon 转发、两路 timeline 转发、活跃心跳
        tasks = [
            asyncio.create_task(
                _forward_from_frontend(
                    websocket,
                    session_id,
                    worker_client,
                    validator_client,
                    worker_agent_id,
                    validator_agent_id,
                    agent_models={
                        role: role_providers[role]["primary_model"] for role in ROLES
                    },
                    streams=streams,
                ),
                name="arl-frontend",
            ),
            asyncio.create_task(
                _forward_timeline(
                    websocket, worker_client, "worker", stream=streams["worker"]
                ),
                name="arl-timeline-worker",
            ),
            asyncio.create_task(
                _forward_timeline(
                    websocket,
                    validator_client,
                    "validator",
                    stream=streams["validator"],
                ),
                name="arl-timeline-validator",
            ),
            asyncio.create_task(_heartbeat(session_id), name="arl-heartbeat"),
            asyncio.create_task(
                _watch_validation(websocket, session_id), name="arl-validation"
            ),
        ]

        logger.info(f"🚀 [Session {session_id}] Started {len(tasks)} forwarding tasks")

        # 任一任务结束即收摊，然后取消其余任务。
        #
        # 不能用 gather：前端断开时 _forward_from_frontend 捕获
        # WebSocketDisconnect 后**正常返回**，而 _forward_timeline 的循环条件
        # 是 daemon 侧的 _connected，与前端死活无关——daemon 还活着，它就
        # 永不退出。gather 于是一直等下去，finally 里的 close() 永不执行，
        # 每次刷新页面泄漏两条 daemon WS 连接和若干 task。心跳任务更是永不
        # 自行结束。故必须 FIRST_COMPLETED + 主动取消。
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        # 先结束的那个若是因异常退出，这里重新抛出，交由外层统一处理
        for task in done:
            exc = task.exception()
            if exc is not None:
                logger.error(
                    f"❌ [Session {session_id}] Task {task.get_name()} failed: {exc}",
                    exc_info=exc,
                )
                raise exc

        logger.info(f"🏁 [Session {session_id}] Forwarding stopped")

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
    agent_models: dict[str, str | None] | None = None,
    streams: dict[str, _RoleStream] | None = None,
):
    """转发前端消息到对应的 daemon。

    单条消息的处理失败（字段缺失、daemon 报错、路径不存在等）只回一帧
    ``error`` 给前端，**不中断连接**：一个畸形帧或一次失败的文件读取不该
    把两个容器的会话一起拆掉。只有前端断开或 ``receive_json`` 本身失效
    （连接已不可用）才退出循环。

    Args:
        websocket: 前端连接
        session_id: 会话 ID
        worker_client: Worker 容器的 daemon 客户端
        validator_client: Validator 容器的 daemon 客户端
        worker_agent_id: Worker 当前的 agent ID
        validator_agent_id: Validator 当前的 agent ID
        agent_models: 每个角色新建 agent 时用的模型（``agent_reset`` 用）
        streams: 每个角色的事件流；``agent_reset`` 换掉 agent 后要同步过去
    """
    # ``agent_reset`` 会换掉某个角色的 agent，后续消息必须投给新 agent
    agent_ids = {"worker": worker_agent_id, "validator": validator_agent_id}
    models = agent_models or {}
    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                # 非 JSON 帧：连接本身还在，回错误帧后继续
                logger.warning(f"⚠️ [Frontend → Backend] Malformed frame: {e}")
                await _push(websocket, "error", {"message": "malformed message frame"})
                continue

            if not isinstance(data, dict):
                await _push(websocket, "error", {"message": "message must be an object"})
                continue

            msg_type = data.get("type")
            role = data.get("role")
            payload = data.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            # Log only payload keys: file_write / fs_request frames carry whole
            # file bodies that must not end up in logs.
            logger.info(
                f"📨 [Frontend → Backend] Received message: type={msg_type}, role={role}, "
                f"payload_keys={sorted(payload)}"
            )

            # role 必须显式合法：原先 `role == "worker"` 之外的一切（None、
            # 拼错的值）都被当成 validator，会把消息静默投错容器。
            if role not in ROLES:
                logger.warning(f"⚠️ [Frontend → Backend] Invalid role: {role!r}")
                await _push(
                    websocket,
                    "error",
                    {"message": f"invalid role: {role!r}; expected one of {list(ROLES)}"},
                )
                continue

            if msg_type not in KNOWN_MESSAGE_TYPES:
                logger.warning(f"⚠️ [Frontend → Backend] Unknown message type: {msg_type}")
                await _push(
                    websocket,
                    "error",
                    {"message": f"unknown message type: {msg_type!r}"},
                    role=role,
                )
                continue

            is_worker = role == "worker"
            client = worker_client if is_worker else validator_client
            agent_id = agent_ids[role]
            target_name = role

            if msg_type == "agent_reset":
                # 有专门的结果帧；成功后切换本角色的 agent ID
                try:
                    agent = await _reset_agent(
                        session_id=session_id,
                        client=client,
                        role=role,
                        agent_id=agent_id,
                        model=models.get(role),
                    )
                    agent_ids[role] = agent["id"]
                    if streams and role in streams:
                        # 新 agent 的时间线从零开始，旧基准已无意义
                        streams[role].agent_id = agent["id"]
                        streams[role].reset_cursor()
                    await _push(
                        websocket,
                        "agent_reset_result",
                        {"ok": True, "agent": _agent_summary(agent)},
                        role=role,
                    )
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    logger.warning(f"⚠️ [Backend → {target_name}] Reset agent failed: {e}")
                    await _push(
                        websocket,
                        "agent_reset_result",
                        {"ok": False, "message": str(e)},
                        role=role,
                    )
                continue

            try:
                await _handle_frontend_message(
                    websocket=websocket,
                    session_id=session_id,
                    client=client,
                    role=role,
                    target_name=target_name,
                    agent_id=agent_id,
                    msg_type=msg_type,
                    payload=payload,
                )
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.warning(
                    f"⚠️ [Backend → {target_name}] Handling {msg_type} failed: {e}",
                    exc_info=True,
                )
                await _push(
                    websocket,
                    "error",
                    {"message": f"{msg_type} failed: {e}", "type": msg_type},
                    role=role,
                )

    except WebSocketDisconnect:
        logger.info("🔌 [Frontend] WebSocket disconnected")
        # 正常断开：返回后由上层 asyncio.wait 收摊并取消其余任务
    except Exception as e:
        logger.error(f"❌ [Frontend → Backend] Error forwarding message: {e}", exc_info=True)
        raise


async def _handle_frontend_message(
    *,
    websocket: WebSocket,
    session_id: uuid.UUID,
    client: PaseoDaemonClient,
    role: str,
    target_name: str,
    agent_id: str,
    msg_type: str,
    payload: dict,
) -> None:
    """处理单条前端消息。

    不自行捕获异常——由 :func:`_forward_from_frontend` 统一转成 error 帧。
    例外是 ``agent_cancel`` / ``set_agent_mode`` / ``list_commands`` /
    ``fs_request``：它们有专门的结果帧（``*_result`` / ``command_list`` /
    ``fs_result``），前端靠这些帧解开按钮或
    菜单的加载状态，故在此就地处理失败。

    Args:
        websocket: 前端连接
        session_id: 会话 ID
        client: 目标 daemon 客户端
        role: 角色标识（worker / validator）
        target_name: 日志用的目标名
        agent_id: 该角色的默认 agent ID
        msg_type: 消息类型（已校验在白名单内）
        payload: 消息负载

    Raises:
        KeyError: 必填字段缺失（由调用方转成 error 帧）
    """
    target_agent = payload.get("agent_id") or agent_id

    if msg_type == "agent_send":
        content = payload.get("content", "")
        logger.info(
            f"📤 [Backend → {target_name}] Sending agent_send: "
            f"agent={target_agent}, content_len={len(content)}"
        )
        await client.send_agent_message(
            agent_id=target_agent,
            text=content,
            active_turn_behavior=payload.get("active_turn_behavior"),
        )
        logger.info(f"✅ [Backend → {target_name}] agent_send sent successfully")

    elif msg_type == "file_read":
        path = payload["path"]
        logger.info(f"📤 [Backend → {target_name}] Reading file: path={path}")
        content = await client.read_file(path)
        await _push(
            websocket, "file_content", {"path": path, "content": content}, role=role
        )
        logger.info(
            f"✅ [Backend → Frontend] Sent file_content: path={path}, len={len(content)}"
        )

    elif msg_type == "file_write":
        path = payload["path"]
        content = payload["content"]
        logger.info(
            f"📤 [Backend → {target_name}] Writing file: path={path}, len={len(content)}"
        )
        await client.write_file(path, content)
        await _push(websocket, "file_written", {"path": path}, role=role)
        logger.info(f"✅ [Backend → Frontend] File written: path={path}")

    elif msg_type == "file_list":
        # 列出目录（空路径表示项目根目录）
        path = payload.get("path", "")
        logger.info(f"📤 [Backend → {target_name}] Listing files: path={path!r}")
        files = await client.list_files(path)
        await _push(websocket, "file_list", {"path": path, "files": files}, role=role)
        logger.info(
            f"✅ [Backend → Frontend] Sent file_list: path={path!r}, count={len(files)}"
        )

    elif msg_type == "permission_response":
        # 响应权限请求；request_id 来自 agent_permission_request 事件
        request_id = payload["request_id"]
        response = payload["response"]
        logger.info(
            f"📤 [Backend → {target_name}] Permission response: "
            f"request_id={request_id}, response={response}"
        )
        await client.resolve_permission(
            agent_id=target_agent,
            request_id=request_id,
            response=response,
        )
        logger.info(f"✅ [Backend → {target_name}] Permission response sent")

    elif msg_type == "agent_cancel":
        # 停止当前回合（前端「停止」按钮）。失败不致命：只回一帧结果，
        # 让前端把按钮状态解开即可。
        logger.info(f"📤 [Backend → {target_name}] Cancel turn: agent={target_agent}")
        try:
            await client.cancel_agent(target_agent)
            await _push(websocket, "agent_cancel_result", {"ok": True}, role=role)
            logger.info(f"✅ [Backend → {target_name}] Cancel sent")
        except Exception as e:
            logger.warning(f"⚠️ [Backend → {target_name}] Cancel failed: {e}")
            await _push(
                websocket,
                "agent_cancel_result",
                {"ok": False, "message": str(e)},
                role=role,
            )

    elif msg_type == "sync_validation":
        # 把 Validator 的 task.md 与验证服务凭据同步到 Worker 工作目录。
        # 有专门的结果帧，失败就地处理——前端靠它解开按钮 loading 状态。
        logger.info(f"📤 [Backend → {target_name}] 同步验证产物到 Worker")
        try:
            # 现场重新探测，不信前端传来的状态：用户可能在按钮出现后
            # 又把 Validator 的服务停了，拿旧状态写进连接信息文件会让
            # Worker 带着一个连不上的地址干跑一整轮
            status_now = await asyncio.to_thread(probe_validation, str(session_id))
            if not status_now.configured:
                await _push(
                    websocket,
                    "validation_sync_result",
                    {
                        "ok": False,
                        "message": status_now.reason or "验证服务尚未就绪",
                    },
                    role="worker",
                )
                # 顺带把最新状态推回去，让界面与实际一致
                await _push(
                    websocket,
                    "validation_status",
                    status_now.to_payload(),
                    role="worker",
                )
                return
            if not status_now.has_task:
                await _push(
                    websocket,
                    "validation_sync_result",
                    {"ok": False, "message": "Validator 尚未生成 task.md"},
                    role="worker",
                )
                return

            result = await asyncio.to_thread(
                sync_to_worker, str(session_id), status_now
            )
            await _push(
                websocket,
                "validation_sync_result",
                {"ok": True, **result},
                role="worker",
            )
            logger.info(f"✅ [Backend → {target_name}] 验证产物同步完成: {result}")
        except Exception as e:
            logger.warning(
                f"⚠️ [Backend → {target_name}] 验证产物同步失败: {e}", exc_info=True
            )
            await _push(
                websocket,
                "validation_sync_result",
                {"ok": False, "message": str(e)},
                role="worker",
            )

    elif msg_type == "list_commands":
        # 斜杠命令补全（前端输入框键入 "/" 时触发）。有专门的结果帧，
        # 失败就地处理——前端靠它结束菜单的加载状态，不弹通用 error toast。
        logger.info(f"📤 [Backend → {target_name}] List commands: agent={target_agent}")
        try:
            commands = await client.list_commands(target_agent)
            await _push(
                websocket,
                "command_list",
                {"ok": True, "agent_id": target_agent, "commands": commands},
                role=role,
            )
            logger.info(
                f"✅ [Backend → Frontend] Sent command_list: count={len(commands)}"
            )
        except Exception as e:
            logger.warning(f"⚠️ [Backend → {target_name}] List commands failed: {e}")
            await _push(
                websocket,
                "command_list",
                {"ok": False, "agent_id": target_agent, "message": str(e)},
                role=role,
            )

    elif msg_type == "fs_request":
        # 右侧文件面板。有专门的结果帧，失败就地处理——前端按 request_id
        # 结束对应操作的等待，在面板里就地展示错误，不弹通用 error toast。
        request_id = payload.get("request_id")
        op = payload.get("op")
        logger.info(
            f"📤 [Backend → {target_name}] fs_request: op={op}, request_id={request_id}"
        )
        try:
            if op not in FS_OPS:
                raise ValueError(f"unknown fs op: {op!r}")
            data = await _run_fs_op(client, op, payload)
            await _push(
                websocket,
                "fs_result",
                {"request_id": request_id, "op": op, "ok": True, **data},
                role=role,
            )
        except Exception as e:
            logger.warning(f"⚠️ [Backend → {target_name}] fs_request {op} failed: {e}")
            await _push(
                websocket,
                "fs_result",
                {"request_id": request_id, "op": op, "ok": False, "message": str(e)},
                role=role,
            )

    elif msg_type == "timeline_fetch_older":
        # 向上翻页：从前端当前最早一条往前再取一页。有专门的结果帧，失败
        # 就地处理——前端靠它结束顶部的加载状态。
        try:
            cursor = _parse_cursor(payload.get("cursor"))
            page = await client.fetch_agent_timeline(
                target_agent,
                limit=HISTORY_PAGE_LIMIT,
                direction="before",
                cursor=cursor,
            )
            await _push(
                websocket,
                "timeline_older",
                {
                    "ok": True,
                    # 游标失效说明 daemon 已重建时间线，前端据此丢弃这一页
                    "stale": bool(page.get("staleCursor") or page.get("reset")),
                    **_timeline_page(target_agent, page),
                },
                role=role,
            )
        except Exception as e:
            logger.warning(f"⚠️ [Backend → {target_name}] Fetch older history failed: {e}")
            await _push(
                websocket,
                "timeline_older",
                {"ok": False, "agent_id": target_agent, "message": str(e)},
                role=role,
            )

    elif msg_type == "subagent_timeline":
        # 前端点开某个子任务时按需拉它的对话。有专门的结果帧，失败就地处理。
        subagent_id = payload.get("subagent_id")
        try:
            if not isinstance(subagent_id, str) or not subagent_id:
                raise ValueError("subagent_id is required")
            page = await client.fetch_subagent_timeline(target_agent, subagent_id)
            await _push(
                websocket,
                "subagent_timeline",
                {
                    "ok": True,
                    "agent_id": target_agent,
                    "subagent_id": subagent_id,
                    "rows": page.get("rows") or [],
                    "has_older": bool(page.get("hasOlder")),
                },
                role=role,
            )
        except Exception as e:
            logger.warning(f"⚠️ [Backend → {target_name}] Fetch subagent timeline failed: {e}")
            await _push(
                websocket,
                "subagent_timeline",
                {
                    "ok": False,
                    "agent_id": target_agent,
                    "subagent_id": subagent_id,
                    "message": str(e),
                },
                role=role,
            )

    elif msg_type == "peer_timeline":
        # 前端点开某个同级 agent 时按需拉它的对话。复用 subagent_timeline 的
        # 结果帧结构（rows），失败就地处理。
        peer_id = payload.get("peer_id")
        try:
            if not isinstance(peer_id, str) or not peer_id:
                raise ValueError("peer_id is required")
            page = await _fetch_peer_timeline(client, agent_id, peer_id)
            await _push(
                websocket,
                "peer_timeline",
                {
                    "ok": True,
                    "peer_id": peer_id,
                    "rows": page.get("entries") or [],
                    "has_older": bool(page.get("hasOlder")),
                },
                role=role,
            )
        except Exception as e:
            logger.warning(f"⚠️ [Backend → {target_name}] Fetch peer timeline failed: {e}")
            await _push(
                websocket,
                "peer_timeline",
                {"ok": False, "peer_id": peer_id, "message": str(e)},
                role=role,
            )

    elif msg_type == "set_agent_mode":
        # 切换权限模式（auto / bypass / 逐条确认…），并写回会话配置
        mode_id = payload.get("mode_id")
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
            return
        try:
            await client.set_agent_mode(target_agent, mode_id)
            # 写库是阻塞调用，下沉到线程池：切模式走的是消息循环，
            # 在 event loop 里同步开事务会卡住本 worker 的所有请求。
            await asyncio.to_thread(_persist_role_mode, session_id, role, mode_id)
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


async def _run_fs_op(
    client: PaseoDaemonClient, op: str, payload: dict
) -> dict[str, Any]:
    """执行一次文件面板操作，返回并入 ``fs_result`` 的数据字段。

    Args:
        client: 目标角色的 daemon 客户端
        op: :data:`FS_OPS` 之一
        payload: 前端负载（``path`` / ``parent_path`` / ``name`` / ``kind``；
            write 另带 ``content`` / ``expected_modified_at`` / ``expected_revision``）

    Returns:
        list 返回 ``directory``，read 返回 ``file``，write 返回 ``write``
        （daemon 写入结果，``status`` 为 ``written`` 或 ``conflict``；冲突不算失败，
        由前端让用户选择覆盖或重新加载），其余变更类返回 ``path``。

    Raises:
        KeyError: 必填字段缺失。
        ValueError: ``kind`` 非法，或 write 的 ``content`` 不是字符串。
    """
    if op == "list":
        return {"directory": await client.explore_directory(payload.get("path") or ".")}
    if op == "read":
        return {"file": await client.explore_file(payload["path"], FS_PREVIEW_MAX_BYTES)}
    if op == "write":
        content = payload.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        result = await client.save_file(
            payload["path"],
            content,
            payload.get("expected_modified_at") or "",
            payload.get("expected_revision"),
        )
        return {"path": payload["path"], "write": result}
    if op == "create":
        kind = payload.get("kind")
        if kind not in ("file", "directory"):
            raise ValueError(f"invalid kind: {kind!r}")
        path = await client.create_entry(
            payload.get("parent_path") or ".", payload["name"], kind
        )
        return {"path": path}
    if op == "rename":
        return {"path": await client.rename_entry(payload["path"], payload["name"])}
    if op == "duplicate":
        return {"path": await client.duplicate_entry(payload["path"])}
    # delete
    await client.delete_entry(payload["path"])
    return {"path": payload["path"]}


async def _forward_timeline(
    websocket: WebSocket,
    client: PaseoDaemonClient,
    role: str,
    stream: _RoleStream | None = None,
):
    """订阅 daemon 的 agent 事件并转发给前端。

    事件是 Paseo 的 session 内层消息，形如
    ``{"type": "agent_stream", "payload": {...}}``。

    Args:
        websocket: 前端连接
        client: 该角色的 daemon 客户端
        role: 容器角色（worker / validator）
        stream: 建连时就开始缓存的事件流。给了就从它的队列消费并按 seq
            对账（见 :func:`_reconcile_event`）；不给则从此刻起订阅、不对账。
    """
    try:
        logger.info(f"📡 [{role}] Started timeline subscription")
        events = (
            client.iter_event_queue(stream.queue)
            if stream is not None
            else client.subscribe_timeline_async()
        )
        async for event in events:
            if stream is not None and not await _reconcile_event(websocket, stream, event):
                continue
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
        # 前端断开后由上层主动取消，属正常路径。必须重新抛出：吞掉
        # CancelledError 会让 task 以「正常完成」收尾，破坏取消语义。
        raise
    except WebSocketDisconnect:
        logger.info(f"🔌 [{role}] Frontend disconnected during timeline forward")
    except Exception as e:
        logger.error(f"❌ [{role}] Error forwarding timeline: {e}", exc_info=True)
        # 严重错误，抛出异常以关闭连接
        raise
