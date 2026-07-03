"""Chat 调参 Service。

会话与任务严格 1:1 绑定，通过 ``task_id`` 自动 upsert，无需独立的会话 CRUD。

复用 ``report_service`` 的流式 + 协作式取消模式：
- 每一轮对话使用一个独立的 Redis Stream（按 ``(session_id, turn_id)`` 索引）
- 通过覆写 generation_id 实现协作式取消，旧协程发现 id 变了会自行退出
- assistant 消息持久化在数据库中，前端刷新页面后可重放整段历史

调参业务逻辑（构造 prompt、解析 LLM 输出为可执行配置等）暂未实现，
后续由 ``_run_chat_tune_generation`` 内部的 TODO 段落补齐即可。
"""

import asyncio
import copy
import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app import models
from app.core.config import settings
from app.core.redis import (
    check_chat_tune_rate_limit,
    clear_chat_tune_generation_id,
    delete_chat_tune_stream,
    get_chat_tune_generation_id,
    push_chat_tune_chunk,
    set_chat_tune_generation_id,
)
from app.models.chat_tune import (
    ChatTuneActiveStage,
    ChatTuneGenerationKind,
    ChatTuneMessage,
    ChatTuneMessageRole,
    ChatTuneSession,
    ChatTuneStageStatus,
    ChatTuneTurnStatus,
)
from app.schemas.chat_tune import (
    ChatTuneMessageItem,
    ChatTuneRetryResponse,
    ChatTuneSessionDetailResponse,
    ChatTuneSessionItem,
    ChatTuneStopResponse,
    ChatTuneTurnRetryRequest,
    ChatTuneTurnStartRequest,
    ChatTuneTurnStartResponse,
)
from app.services.report_service import _resolve_provider_config
from app.services.task_service import get_task_with_auth

# ---- 内部辅助 ----


def _get_or_create_session(
        db: Session, task: models.Task, current_user: models.User
) -> ChatTuneSession:
    """获取任务绑定的调参会话，不存在则按需创建。

    使用 ``unique(task_id)`` 约束保证并发安全：若两个请求同时进入，
    数据库唯一约束会让其中一个 INSERT 失败，捕获后重新查询即可。
    """
    session = db.exec(
        select(ChatTuneSession).where(ChatTuneSession.task_id == task.id)
    ).first()
    if session is not None:
        return session

    # 以任务现有的 input_args 作为初始配置快照
    initial_config = copy.deepcopy(dict(task.input_args or {}))
    session = ChatTuneSession(
        user_id=current_user.id,
        task_id=task.id,
        latest_config=initial_config,
    )
    db.add(session)
    try:
        db.commit()
    except IntegrityError:
        # 并发下另一个请求先建好了——回滚后重查即可
        db.rollback()
        session = db.exec(
            select(ChatTuneSession).where(ChatTuneSession.task_id == task.id)
        ).first()
        if session is None:
            raise
        return session
    db.refresh(session)
    return session


def resolve_task_session(
        db: Session, task_id: uuid.UUID, current_user: models.User
) -> tuple[models.Task, ChatTuneSession]:
    """统一入口：通过任务权限校验并获取/创建对应的调参会话。"""
    task = get_task_with_auth(db, task_id, current_user)
    session = _get_or_create_session(db, task, current_user)
    return task, session


def _safe_error_message(exc: Exception, max_len: int = 2000) -> str:
    """裁剪后的异常文本，避免把凭证回写给前端。"""
    msg = f"{type(exc).__name__}: {exc}"
    return msg[:max_len]


def _t(language: str | None, zh: str, en: str) -> str:
    """按语言挑选面向用户的文案，缺省回退中文。

    Args:
        language: 语言码（'zh'/'en' 等），通常取自 ``gathering_context['language']``。
        zh: 中文文案。
        en: 英文文案。

    Returns:
        ``language == 'en'`` 时返回英文，否则返回中文。
    """
    return en if (language or "zh") == "en" else zh


@dataclass
class TurnStreamContext:
    """stream_turn 路由所需的上下文数据。

    路由层根据 ``is_finished`` 决定返回回放流还是实时 Redis 流。
    """

    session_id: uuid.UUID
    turn_id: uuid.UUID
    is_finished: bool
    terminal_type: str | None = None
    content: str | None = None
    payload: dict[str, Any] | None = field(default=None)
    error: str | None = None


def get_turn_stream_context(
        db: Session,
        task_id: uuid.UUID,
        turn_id: uuid.UUID,
        current_user: models.User,
) -> TurnStreamContext:
    """获取调参 SSE 流所需的上下文。

    与 ``resolve_task_session`` 不同，此方法不会自动创建会话——
    stream 端点仅用于读取已有轮次，不应产生创建副作用。
    """
    task = get_task_with_auth(db, task_id, current_user)
    session = db.exec(
        select(ChatTuneSession).where(ChatTuneSession.task_id == task.id)
    ).first()
    if session is None:
        raise HTTPException(status_code=404, detail="调参会话不存在")

    msg = _get_assistant_message(db, session.id, turn_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="调参轮次不存在")

    if msg.turn_status != ChatTuneTurnStatus.GENERATING:
        terminal_type = {
            ChatTuneTurnStatus.COMPLETED: "done",
            ChatTuneTurnStatus.CANCELLED: "cancelled",
            ChatTuneTurnStatus.FAILED: "error",
        }.get(msg.turn_status, "done")
        return TurnStreamContext(
            session_id=session.id,
            turn_id=turn_id,
            is_finished=True,
            terminal_type=terminal_type,
            content=msg.content,
            payload=msg.payload,
            error=msg.error,
        )

    return TurnStreamContext(
        session_id=session.id,
        turn_id=turn_id,
        is_finished=False,
    )


# ---- 对外 Service 接口 ----


_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 100

# Proxy token TTL for an AI-agent build turn (seconds). One turn lives minutes;
# 2h covers long multi-round debug builds with margin. Bounded and self-expiring
# because the token is NOT explicitly revoked (it is task-scoped and an evolution
# run for the same task issues its own token).
_AGENT_BUILD_TOKEN_TTL = 2 * 3600


def get_session_detail(
        db: Session,
        task_id: uuid.UUID,
        current_user: models.User,
        *,
        before: uuid.UUID | None = None,
        limit: int = _DEFAULT_PAGE_LIMIT,
) -> ChatTuneSessionDetailResponse:
    """获取任务对应的调参会话详情，含游标分页历史消息。

    Args:
        before: 游标——返回该消息 ID 之前（更早）的消息。为 None 时返回最新的一页。
        limit: 每页条数，上限 ``_MAX_PAGE_LIMIT``。
    """
    _, session = resolve_task_session(db, task_id, current_user)

    limit = max(1, min(limit, _MAX_PAGE_LIMIT))
    fetch_count = limit + 1

    stmt = (
        select(ChatTuneMessage)
        .where(ChatTuneMessage.session_id == session.id)
    )
    if before is not None:
        cursor_msg = db.get(ChatTuneMessage, before)
        if cursor_msg is not None and cursor_msg.session_id == session.id:
            # 用 (created_time, id) 复合游标避免同时间戳消息漏读
            stmt = stmt.where(
                (ChatTuneMessage.created_time < cursor_msg.created_time)
                | (
                    (ChatTuneMessage.created_time == cursor_msg.created_time)
                    & (ChatTuneMessage.id < cursor_msg.id)
                )
            )

    # 次级键 id 兜底：created_time 在低精度时钟下可能撞值（如 Windows），
    # 单靠 created_time 排序会让同时刻的两条消息顺序未定义
    stmt = stmt.order_by(
        ChatTuneMessage.created_time.desc(), ChatTuneMessage.id.desc()
    ).limit(fetch_count)
    messages = list(db.exec(stmt).all())

    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]
    messages.reverse()

    return ChatTuneSessionDetailResponse(
        session=ChatTuneSessionItem.model_validate(session),
        messages=[ChatTuneMessageItem.model_validate(m) for m in messages],
        has_more=has_more,
    )


def reset_session(
        db: Session, task_id: uuid.UUID, current_user: models.User
) -> ChatTuneSessionItem:
    """清空任务的调参对话历史，保留创建时注入的种子消息（欢迎语、模板提示等）。"""
    task, session = resolve_task_session(db, task_id, current_user)

    # 取消活跃轮次（如果有）
    if session.active_turn_id is not None:
        _cancel_turn_inplace(session.id, session.active_turn_id)

    # 种子消息的 turn_id 集合：没有对应 USER 消息的 turn_id 都是种子轮次
    user_turn_ids = set(
        db.exec(
            select(ChatTuneMessage.turn_id)
            .where(ChatTuneMessage.session_id == session.id)
            .where(ChatTuneMessage.role == ChatTuneMessageRole.USER)
            .distinct()
        ).all()
    )
    all_turn_ids = set(
        db.exec(
            select(ChatTuneMessage.turn_id)
            .where(ChatTuneMessage.session_id == session.id)
            .distinct()
        ).all()
    )
    seed_turn_ids = all_turn_ids - user_turn_ids
    non_seed_turn_ids = all_turn_ids - seed_turn_ids

    # 只删除非种子消息
    if non_seed_turn_ids:
        db.exec(
            delete(ChatTuneMessage)
            .where(ChatTuneMessage.session_id == session.id)
            .where(ChatTuneMessage.turn_id.in_(non_seed_turn_ids))
        )

    # 解锁种子消息中的 payload（模板选择表单可能已被提交过）
    if seed_turn_ids:
        db.exec(
            update(ChatTuneMessage)
            .where(ChatTuneMessage.session_id == session.id)
            .where(ChatTuneMessage.turn_id.in_(seed_turn_ids))
            .where(ChatTuneMessage.payload_locked.is_(True))
            .values(
                payload_locked=False,
                payload_submission=None,
                payload_locked_at=None,
                updated_time=datetime.now(UTC),
            )
        )

    session.active_turn_id = None
    session.latest_config = copy.deepcopy(dict(task.input_args or {}))
    session.gathering_context = None
    # 重置三阶段状态机：所有阶段回到未开始、激活阶段回到 gathering
    session.gathering_status = ChatTuneStageStatus.NOT_STARTED.value
    session.build_status = ChatTuneStageStatus.NOT_STARTED.value
    session.review_status = ChatTuneStageStatus.NOT_STARTED.value
    session.active_stage = ChatTuneActiveStage.GATHERING.value
    session.updated_time = datetime.now(UTC)
    flag_modified(session, "latest_config")
    flag_modified(session, "gathering_context")
    db.add(session)
    db.commit()
    db.refresh(session)

    # 主动清理非种子轮次的 Redis Stream
    for tid in non_seed_turn_ids:
        delete_chat_tune_stream(session.id, tid)
        clear_chat_tune_generation_id(session.id, tid)

    return ChatTuneSessionItem.model_validate(session)


# ---- 触发调参轮次 ----


def start_turn(
        db: Session,
        task_id: uuid.UUID,
        current_user: models.User,
        request: ChatTuneTurnStartRequest,
        access_token: str | None = None,
) -> ChatTuneTurnStartResponse:
    """触发一轮新的调参生成。

    支持两种输入来源（可叠加）：
    - 普通文字消息：仅填 ``content``。
    - 响应上一轮的表单：填 ``respond_to_message_id`` + ``submission``，
      后端会原子地完成"锁定旧消息 + 触发新一轮"。

    流程：
    1. 通过 task_id 获取/创建会话；
    2. （若有表单提交）原子地锁定目标 assistant 消息；
    3. 取消上一轮（若仍在生成中）；
    4. 落库 user 消息 + 占位的 assistant 消息（generating 状态）；
    5. 启动后台协程进行流式生成；
    6. 返回两条消息让前端立即渲染并连上 ``/stream`` 端点。
    """
    task, session = resolve_task_session(db, task_id, current_user)

    if not check_chat_tune_rate_limit(session.id):
        raise HTTPException(
            status_code=429, detail="操作过于频繁，请稍后再试"
        )

    provider_config = _resolve_provider_config(
        db, current_user, task, request.provider_id, request.model_name, access_token
    )

    # ---- 步骤 2：若是表单回复，先原子锁定旧消息 ----
    locked_message: ChatTuneMessage | None = None
    if request.respond_to_message_id is not None:
        locked_message = _lock_payload_message(
            db,
            session.id,
            request.respond_to_message_id,
            request.submission or {},
        )

    # ---- 组装 LLM 输入：表单提交 + 用户附加文字可叠加，供 LLM 理解 ----
    llm_user_content = _build_user_content(
        text=request.content,
        submission=request.submission,
    )

    # ---- 判定本轮要执行的协程类型，记到 assistant_message 上 ----
    # 优先级：前端显式指定 target_stage > 后端按状态机自动判断。
    # 自动判断规则：
    #   build_status == COMPLETED        → review
    #   else if is_confirm_build         → ai_build
    #   else                             → chat_tune（默认）
    # 这里不再读 task.ai_build_started——该字段已被 session.build_status 取代。
    is_confirm_build = (
            locked_message is not None
            and isinstance(locked_message.payload, dict)
            and locked_message.payload.get("stage") == "confirm_build"
            and isinstance(request.submission, dict)
            and request.submission.get("value") == _CONFIRM_BUILD_VALUE
    )
    if request.target_stage is ChatTuneActiveStage.REVIEW:
        generation_kind = ChatTuneGenerationKind.REVIEW
    elif request.target_stage is ChatTuneActiveStage.BUILD:
        generation_kind = ChatTuneGenerationKind.AI_BUILD
    elif request.target_stage is ChatTuneActiveStage.GATHERING:
        generation_kind = ChatTuneGenerationKind.CHAT_TUNE
    elif session.build_status == ChatTuneStageStatus.COMPLETED.value:
        generation_kind = ChatTuneGenerationKind.REVIEW
    elif is_confirm_build:
        generation_kind = ChatTuneGenerationKind.AI_BUILD
    else:
        generation_kind = ChatTuneGenerationKind.CHAT_TUNE

    # AI 构建 (Beta)：前端 beta 标志在后端开关开启时，覆盖为 AgentScope agent
    # 路径。该 agent 在一轮内承接对话+构建+验证，不依赖三阶段状态机，故无视上面
    # 的阶段判定。开关关闭时忽略 beta，沿用上面的默认分发。
    if request.beta and settings.ENABLE_AI_AGENT_BUILD:
        generation_kind = ChatTuneGenerationKind.AI_AGENT

    # 稳健路由：若本轮是在回复一条 ai_agent 生成的消息（如确认卡片），无论前端是否
    # 仍带 beta 标志（刷新后可能丢失），都继续走 agent 路径。
    replying_to_agent = (
        locked_message is not None
        and locked_message.generation_kind == ChatTuneGenerationKind.AI_AGENT.value
    )
    if replying_to_agent and settings.ENABLE_AI_AGENT_BUILD:
        generation_kind = ChatTuneGenerationKind.AI_AGENT

    # agent 的 build 阶段闸：进入 build 阶段（拥有 build/edit 工具）的条件——
    #   (a) 用户在 confirm_build 卡片上点了“确认构建”，或
    #   (b) 本会话已经构建过（build_status 完成 / 已有 blueprint_data），此时后续
    #       轮次都应带 build/edit 工具，以便 agent 帮用户调 config、改代码、改评估器。
    _already_built = (
        session.build_status == ChatTuneStageStatus.COMPLETED.value
        or bool((session.gathering_context or {}).get("blueprint_data"))
    )
    agent_allow_build = generation_kind is ChatTuneGenerationKind.AI_AGENT and (
        (
            replying_to_agent
            and isinstance(locked_message.payload, dict)
            and locked_message.payload.get("stage") == "confirm_build"
            and isinstance(request.submission, dict)
            and request.submission.get("value") == _CONFIRM_BUILD_VALUE
        )
        or _already_built
    )

    # 取消上一轮：直接覆写 generation_id 让旧协程感知到
    if session.active_turn_id is not None:
        _cancel_turn_inplace(session.id, session.active_turn_id)

    turn_id = uuid.uuid4()
    now = datetime.now(UTC)
    # 显式拆分 created_time，保证 user 严格早于 assistant：
    # default_factory 在两次构造时分别调用 datetime.now(UTC)，在 Windows 等
    # 低精度时钟下可能拿到完全相同的时间戳，导致前端按 created_time 排序时
    # assistant 出现在 user 之前。
    assistant_created_time = now + timedelta(microseconds=1)

    user_message = ChatTuneMessage(
        session_id=session.id,
        turn_id=turn_id,
        role=ChatTuneMessageRole.USER,
        content=request.content or "",
        turn_status=ChatTuneTurnStatus.COMPLETED,
        created_time=now,
        updated_time=now,
    )
    assistant_message = ChatTuneMessage(
        session_id=session.id,
        turn_id=turn_id,
        role=ChatTuneMessageRole.ASSISTANT,
        content="",
        turn_status=ChatTuneTurnStatus.GENERATING,
        generation_kind=generation_kind.value,
        created_time=assistant_created_time,
        updated_time=assistant_created_time,
    )
    db.add(user_message)
    db.add(assistant_message)

    session.active_turn_id = turn_id
    session.updated_time = now
    db.add(session)

    if not task.ai_built:
        task.ai_built = True
        db.add(task)

    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)
    if locked_message is not None:
        db.refresh(locked_message)

    # 设置 generation_id 并启动后台流式协程
    generation_id = uuid.uuid4().hex
    set_chat_tune_generation_id(session.id, turn_id, generation_id)
    delete_chat_tune_stream(session.id, turn_id)

    def _ctx_with_language() -> dict[str, Any]:
        """在上一轮持久化的 gathering_context 上覆写本轮前端传入的 language。

        首轮 session.gathering_context 为空，返回仅含 language 的 dict 即可——
        容器内 runner 用 ``gctx.get(...)`` 带默认值取字段，不影响首轮判定。
        """
        ctx = (
            copy.deepcopy(session.gathering_context)
            if session.gathering_context
            else {}
        )
        ctx["language"] = request.language
        return ctx

    try:
        if generation_kind is ChatTuneGenerationKind.REVIEW:
            asyncio.create_task(
                _run_review_generation(
                    session_id=session.id,
                    turn_id=turn_id,
                    assistant_message_id=assistant_message.id,
                    generation_id=generation_id,
                    task_id=task.id,
                    provider_config=provider_config,
                    user_content=llm_user_content,
                    gathering_context=_ctx_with_language(),
                    input_data_path=task.input_data_path,
                )
            )
        elif generation_kind is ChatTuneGenerationKind.AI_BUILD:
            asyncio.create_task(
                _run_ai_build(
                    session_id=session.id,
                    turn_id=turn_id,
                    assistant_message_id=assistant_message.id,
                    generation_id=generation_id,
                    task_id=task.id,
                    provider_config=provider_config,
                    gathering_context=_ctx_with_language(),
                    input_data_path=task.input_data_path,
                )
            )
        elif generation_kind is ChatTuneGenerationKind.AI_AGENT:
            asyncio.create_task(
                _run_agent_build(
                    session_id=session.id,
                    turn_id=turn_id,
                    assistant_message_id=assistant_message.id,
                    generation_id=generation_id,
                    task_id=task.id,
                    user_id=current_user.id,
                    provider_config=provider_config,
                    user_content=llm_user_content,
                    gathering_context=_ctx_with_language(),
                    input_data_path=task.input_data_path,
                    allow_build=agent_allow_build,
                    proposed=(
                        (session.gathering_context or {}).get("proposed")
                        if agent_allow_build
                        else None
                    ),
                )
            )
        else:
            asyncio.create_task(
                _run_chat_tune_generation(
                    session_id=session.id,
                    turn_id=turn_id,
                    assistant_message_id=assistant_message.id,
                    provider_config=provider_config,
                    user_content=llm_user_content,
                    base_config=copy.deepcopy(session.latest_config or {}),
                    generation_id=generation_id,
                    gathering_context=_ctx_with_language(),
                    input_data_path=task.input_data_path,
                )
            )
    except RuntimeError:
        # 事件循环不可用（如已关闭），兜底将消息标记为失败，避免永远卡在 GENERATING
        logger.error(
            "无法调度后台协程，session_id=%s, turn_id=%s", session.id, turn_id
        )
        assistant_message.turn_status = ChatTuneTurnStatus.FAILED
        assistant_message.error = "后台任务调度失败，请重试"
        assistant_message.updated_time = datetime.now(UTC)
        session.active_turn_id = None
        db.add(assistant_message)
        db.add(session)
        db.commit()
        db.refresh(assistant_message)
        clear_chat_tune_generation_id(session.id, turn_id)

    return ChatTuneTurnStartResponse(
        session_id=session.id,
        turn_id=turn_id,
        user_message=ChatTuneMessageItem.model_validate(user_message),
        assistant_message=ChatTuneMessageItem.model_validate(assistant_message),
        locked_message=(
            ChatTuneMessageItem.model_validate(locked_message)
            if locked_message
            else None
        ),
    )


def stop_turn(
        db: Session,
        task_id: uuid.UUID,
        turn_id: uuid.UUID,
        current_user: models.User,
) -> ChatTuneStopResponse:
    """停止指定轮次的生成。

    幂等：重复调用对已结束的轮次返回当前状态，不抛错。
    """
    _, session = resolve_task_session(db, task_id, current_user)

    msg = _get_assistant_message(db, session.id, turn_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="调参轮次不存在")

    # 已结束：幂等返回当前状态
    if msg.turn_status != ChatTuneTurnStatus.GENERATING:
        return ChatTuneStopResponse(
            session_id=session.id,
            turn_id=turn_id,
            status=msg.turn_status,
            message=f"该轮次已结束，当前状态: {msg.turn_status.value}",
        )

    gen_id = get_chat_tune_generation_id(session.id, turn_id)
    if not gen_id:
        # Redis key 已过期但 DB 仍挂着 generating——兜底成 cancelled
        msg.turn_status = ChatTuneTurnStatus.CANCELLED
        msg.updated_time = datetime.now(UTC)
        db.add(msg)
        if session.active_turn_id == turn_id:
            session.active_turn_id = None
            db.add(session)
        db.commit()
        delete_chat_tune_stream(session.id, turn_id)
        return ChatTuneStopResponse(
            session_id=session.id,
            turn_id=turn_id,
            status=ChatTuneTurnStatus.CANCELLED,
            message="该轮次已标记为取消",
        )

    _cancel_turn_inplace(session.id, turn_id)
    return ChatTuneStopResponse(
        session_id=session.id,
        turn_id=turn_id,
        status=ChatTuneTurnStatus.CANCELLED,
        message="停止信号已发送",
    )


def _cancel_turn_inplace(session_id: uuid.UUID, turn_id: uuid.UUID) -> None:
    """清除 generation_id 并推送 cancelled 事件，触发协程退出。

    同时按会话名强制终止隔离容器，确保即使 LLM 停滞（无新事件、协程仍阻塞在
    SSE 读取）也能立即停止。容器移除幂等，不存在时静默忽略。
    """
    push_chat_tune_chunk(session_id, turn_id, {"type": "cancelled"})
    clear_chat_tune_generation_id(session_id, turn_id)
    try:
        from app.services import container_service

        container_service.kill_chat_tune_container(str(session_id))
    except Exception:
        logger.debug(
            "Failed to kill chat tune container on cancel, session_id=%s",
            session_id,
            exc_info=True,
        )


def retry_turn(
        db: Session,
        task_id: uuid.UUID,
        turn_id: uuid.UUID,
        current_user: models.User,
        access_token: str | None = None,
        request: ChatTuneTurnRetryRequest | None = None,
) -> ChatTuneRetryResponse:
    """对失败或已停止的轮次原地重跑：复用 ``turn_id`` 和 assistant 消息行。

    与 ``start_turn`` 不同，重试不会新建 user/assistant 消息——直接把目标
    assistant 消息状态回到 ``GENERATING``、清空 ``content/payload/error``，
    并基于原始 user 消息和当前 ``latest_config`` 重新调度生成协程。
    前端可继续订阅同一个 ``/stream`` 端点。

    根据 assistant 消息上记录的 ``generation_kind`` 调度到与原轮次一致的
    后台协程（chat_tune / ai_build / review）；历史数据无 kind 时兜底走
    ``_run_chat_tune_generation`` 保持向后兼容。

    可重试的状态：``FAILED``（异常失败）和 ``CANCELLED``（用户手动停止）；
    其它状态返回 409。Provider 优先使用本次重试请求传入的 ``provider_id`` /
    ``model_name``；缺省时回退到用户默认报告模型，再回退到任务的第一个
    Provider。原轮次实际使用的 provider 选择未持久化，无法精确复现。
    """
    task, session = resolve_task_session(db, task_id, current_user)

    if not check_chat_tune_rate_limit(session.id):
        raise HTTPException(
            status_code=429, detail="操作过于频繁，请稍后再试"
        )

    assistant_msg = _get_assistant_message(db, session.id, turn_id)
    if assistant_msg is None:
        raise HTTPException(status_code=404, detail="调参轮次不存在")

    if assistant_msg.turn_status not in (
            ChatTuneTurnStatus.FAILED,
            ChatTuneTurnStatus.CANCELLED,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"仅失败或已停止的轮次可以重试，当前状态: {assistant_msg.turn_status.value}",
        )

    user_msg = db.exec(
        select(ChatTuneMessage)
        .where(ChatTuneMessage.session_id == session.id)
        .where(ChatTuneMessage.turn_id == turn_id)
        .where(ChatTuneMessage.role == ChatTuneMessageRole.USER)
    ).first()
    if user_msg is None:
        raise HTTPException(
            status_code=409, detail="该轮次缺失用户消息，无法重试"
        )

    provider_id = request.provider_id if request else None
    model_name = request.model_name if request else None
    provider_config = _resolve_provider_config(
        db, current_user, task, provider_id, model_name, access_token
    )

    # 若有别的轮次仍在跑，取消之；本轮已 FAILED，不会出现 active==turn_id 的情形，
    # 但仍做防御以避免重复推送 cancelled 给自己
    if (
            session.active_turn_id is not None
            and session.active_turn_id != turn_id
    ):
        _cancel_turn_inplace(session.id, session.active_turn_id)

    now = datetime.now(UTC)
    had_payload = assistant_msg.payload is not None
    assistant_msg.content = ""
    assistant_msg.payload = None
    assistant_msg.error = None
    assistant_msg.turn_status = ChatTuneTurnStatus.GENERATING
    assistant_msg.updated_time = now
    if had_payload:
        flag_modified(assistant_msg, "payload")
    db.add(assistant_msg)

    session.active_turn_id = turn_id
    session.updated_time = now
    db.add(session)
    db.commit()
    db.refresh(assistant_msg)

    generation_id = uuid.uuid4().hex
    set_chat_tune_generation_id(session.id, turn_id, generation_id)
    delete_chat_tune_stream(session.id, turn_id)

    # 按生成类型分发到对应协程（与 start_turn 三分支一致）。
    # 历史数据 generation_kind 为 NULL，兜底走 chat_tune 保持向后兼容。
    kind = assistant_msg.generation_kind or ChatTuneGenerationKind.CHAT_TUNE.value
    gathering_ctx = (
        copy.deepcopy(session.gathering_context)
        if session.gathering_context
        else None
    )

    try:
        if kind == ChatTuneGenerationKind.REVIEW.value:
            asyncio.create_task(
                _run_review_generation(
                    session_id=session.id,
                    turn_id=turn_id,
                    assistant_message_id=assistant_msg.id,
                    generation_id=generation_id,
                    task_id=task.id,
                    provider_config=provider_config,
                    user_content=user_msg.content,
                    gathering_context=gathering_ctx,
                    input_data_path=task.input_data_path,
                )
            )
        elif kind == ChatTuneGenerationKind.AI_BUILD.value:
            asyncio.create_task(
                _run_ai_build(
                    session_id=session.id,
                    turn_id=turn_id,
                    assistant_message_id=assistant_msg.id,
                    generation_id=generation_id,
                    task_id=task.id,
                    provider_config=provider_config,
                    gathering_context=gathering_ctx,
                    input_data_path=task.input_data_path,
                )
            )
        elif kind == ChatTuneGenerationKind.AI_AGENT.value:
            asyncio.create_task(
                _run_agent_build(
                    session_id=session.id,
                    turn_id=turn_id,
                    assistant_message_id=assistant_msg.id,
                    generation_id=generation_id,
                    task_id=task.id,
                    user_id=current_user.id,
                    provider_config=provider_config,
                    user_content=user_msg.content,
                    gathering_context=gathering_ctx,
                    input_data_path=task.input_data_path,
                )
            )
        else:
            asyncio.create_task(
                _run_chat_tune_generation(
                    session_id=session.id,
                    turn_id=turn_id,
                    assistant_message_id=assistant_msg.id,
                    provider_config=provider_config,
                    user_content=user_msg.content,
                    base_config=copy.deepcopy(session.latest_config or {}),
                    generation_id=generation_id,
                    gathering_context=gathering_ctx,
                    input_data_path=task.input_data_path,
                )
            )
    except RuntimeError:
        logger.error(
            "无法调度后台协程 (retry)，session_id=%s, turn_id=%s, kind=%s",
            session.id,
            turn_id,
            kind,
        )
        assistant_msg.turn_status = ChatTuneTurnStatus.FAILED
        assistant_msg.error = "后台任务调度失败，请重试"
        assistant_msg.updated_time = datetime.now(UTC)
        session.active_turn_id = None
        db.add(assistant_msg)
        db.add(session)
        db.commit()
        db.refresh(assistant_msg)
        clear_chat_tune_generation_id(session.id, turn_id)

    return ChatTuneRetryResponse(
        session_id=session.id,
        turn_id=turn_id,
        assistant_message=ChatTuneMessageItem.model_validate(assistant_msg),
    )


def _get_assistant_message(
        db: Session, session_id: uuid.UUID, turn_id: uuid.UUID
) -> ChatTuneMessage | None:
    """读取指定轮次的 assistant 消息。"""
    return db.exec(
        select(ChatTuneMessage)
        .where(ChatTuneMessage.session_id == session_id)
        .where(ChatTuneMessage.turn_id == turn_id)
        .where(ChatTuneMessage.role == ChatTuneMessageRole.ASSISTANT)
    ).first()


# ---- 表单锁定 + 内容拼装 ----


def _lock_payload_message(
        db: Session,
        session_id: uuid.UUID,
        message_id: uuid.UUID,
        submission: dict[str, Any],
) -> ChatTuneMessage:
    """原子地锁定指定 assistant 消息的 payload 并存档用户提交值。

    先做 404/400 校验，最后通过条件 UPDATE 实现真正的乐观锁——
    只有 ``payload_locked = false`` 的行能被锁定，并发下只会有一个赢家。
    """
    message = db.get(ChatTuneMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="目标消息不存在")
    if message.session_id != session_id:
        raise HTTPException(
            status_code=404, detail="该消息不属于当前任务的调参会话"
        )
    if message.role != ChatTuneMessageRole.ASSISTANT:
        raise HTTPException(
            status_code=400, detail="仅 assistant 消息可作为表单回复目标"
        )
    if not message.payload:
        raise HTTPException(
            status_code=400, detail="该消息没有可交互的表单负载"
        )

    now = datetime.now(UTC)
    result = db.exec(
        update(ChatTuneMessage)
        .where(ChatTuneMessage.id == message_id)
        .where(ChatTuneMessage.payload_locked.is_(False))
        .values(
            payload_locked=True,
            payload_submission=submission,
            payload_locked_at=now,
            updated_time=now,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="表单已被提交并锁定")

    # 只 flush 不 commit，让调用方（start_turn）统一提交事务，
    # 保证"锁定 + 创建新消息"的原子性
    db.flush()
    db.refresh(message)
    return message


def _build_user_content(
        text: str | None, submission: dict[str, Any] | None
) -> str:
    """组装 user 消息内容：表单提交 + 用户附加文字可叠加。"""
    parts: list[str] = []
    if submission:
        parts.append(_render_submission_as_user_content(submission))
    if text and text.strip():
        parts.append(text.strip())
    return "\n\n".join(parts) if parts else ""


def _render_submission_as_user_content(submission: dict[str, Any]) -> str:
    """将表单提交序列化为带 marker 的 JSON 字符串。

    使用 ``[FORM_SUBMISSION]`` marker + JSON 编码，便于 LLM 在 prompt 中
    一致地识别用户的结构化输入，且不依赖任何特定语言的描述。
    """
    return f"[FORM_SUBMISSION] {submission.get('value','')}"


# ---- 后台流式生成协程 ----


async def _run_ai_build(
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        assistant_message_id: uuid.UUID,
        generation_id: str,
        task_id: uuid.UUID,
        provider_config: dict[str, Any],
        gathering_context: dict[str, Any] | None = None,
        input_data_path: str | None = None,
) -> None:
    """后台协程：在隔离容器中执行 AI 构建，并把事件转发到 Redis。"""
    from app.services import container_service

    language = (gathering_context or {}).get("language") or "zh"
    content_buffer = ""
    blueprint_data: dict[str, Any] | None = None
    cancelled = False
    container_id: str | None = None
    docker_workdir: str | None = None
    client = None

    def _is_cancelled() -> bool:
        return get_chat_tune_generation_id(session_id, turn_id) != generation_id

    try:
        await asyncio.to_thread(
            _update_session_stage,
            session_id, ChatTuneActiveStage.BUILD, ChatTuneStageStatus.RUNNING,
            activate=True,
            turn_id=turn_id,
        )

        import httpx
        from httpx_sse import aconnect_sse

        docker_workdir, host_workdir = await asyncio.to_thread(
            container_service.prepare_chat_tune_workdir,
            input_data_path, str(session_id), str(turn_id),
        )

        container_id = await asyncio.to_thread(
            container_service.start_chat_tune_container,
            str(session_id), host_workdir,
        )

        host = container_service.chat_tune_container_host(str(session_id))
        base_url = f"http://{host}:{settings.CHAT_TUNE_CONTAINER_PORT}"

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
        )

        if not await _wait_chat_tune_ready(
            client, base_url, settings.CHAT_TUNE_CONTAINER_READY_TIMEOUT
        ):
            raise RuntimeError(_t(
                language, "构建容器启动超时，请重试",
                "Build container timed out while starting. Please retry.",
            ))

        body = {
            "provider_config": provider_config,
            "gathering_context": gathering_context,
        }

        stream_done = False
        try:
            async with aconnect_sse(
                client, "POST", f"{base_url}/build", json=body
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    if _is_cancelled():
                        cancelled = True
                        await asyncio.to_thread(
                            _persist_assistant_message, assistant_message_id,
                            ChatTuneTurnStatus.CANCELLED, content_buffer or None, None,
                        )
                        await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
                        return

                    try:
                        event = json.loads(sse.data)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue

                    etype = event.get("type")
                    if etype == "chunk":
                        chunk = event.get("content", "")
                        content_buffer += chunk
                        push_chat_tune_chunk(
                            session_id, turn_id, {"type": "chunk", "content": chunk}
                        )
                    elif etype == "build_result":
                        blueprint_data = event.get("blueprint_data")
                        await asyncio.to_thread(
                            _update_session_stage,
                            session_id,
                            ChatTuneActiveStage.BUILD,
                            ChatTuneStageStatus.COMPLETED,
                            turn_id=turn_id,
                        )
                    elif etype == "error":
                        raise RuntimeError(event.get("error") or _t(
                            language, "构建容器执行失败",
                            "Build container execution failed",
                        ))
                    elif etype == "done":
                        stream_done = True
                        break
        except httpx.RemoteProtocolError:
            if not stream_done:
                raise

        # 构建成功后同步文件：容器已把构建产物写到挂载目录的 {project_name}/ 下。
        # 只上传该子目录，并清空 input_data_path 下所有旧对象——包括 build 前的
        # 用户上传文件（code/、对话中上传的临时文件等）以及容器临时文件——使
        # 持久化结果与构建产物完全一致，不残留 build 前的状态。
        if input_data_path and docker_workdir and isinstance(blueprint_data, dict):
            project_name = blueprint_data.get("project_name")
            if project_name:
                from pathlib import Path

                from app.core.storage import storage

                data_path = Path(docker_workdir)
                product_root = data_path / project_name
                if product_root.is_dir():
                    existing_keys = await asyncio.to_thread(
                        storage.list_objects, input_data_path.rstrip("/") + "/"
                    )
                    if existing_keys:
                        await asyncio.to_thread(storage.delete_many, existing_keys)

                    for file_path in product_root.rglob("*"):
                        if not file_path.is_file():
                            continue
                        rel = file_path.relative_to(data_path)
                        key = f"{input_data_path}/{rel.as_posix()}"
                        await asyncio.to_thread(storage.upload, key, file_path.read_bytes())

                    # 从构建产物根目录第一层的 yaml 文件回填 task.input_args；
                    # 解析失败则使用新建任务的默认值，保证 input_args 总是合法 dict。
                    new_input_args = await asyncio.to_thread(
                        _extract_input_args_from_product, product_root
                    )
                    await asyncio.to_thread(
                        _persist_task_input_args, task_id, new_input_args
                    )

        # 持久化 blueprint_data 到 gathering_context
        if blueprint_data is not None:
            updated_ctx = dict(gathering_context or {})
            updated_ctx["blueprint_data"] = blueprint_data
            await asyncio.to_thread(
                _persist_session_gathering_context, session_id, updated_ctx
            )

        await asyncio.to_thread(
            _persist_assistant_message, assistant_message_id,
            status=ChatTuneTurnStatus.COMPLETED, content=content_buffer, payload=None,
        )
        await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
        # 落库完成后再推终止事件：前端收到 done 后会立即回查 DB，须保证此时已是最终状态
        push_chat_tune_chunk(session_id, turn_id, {"type": "done"})

    except Exception as exc:
        if cancelled:
            return
        if _is_cancelled():
            cancelled = True
            await asyncio.to_thread(
                _persist_assistant_message, assistant_message_id,
                ChatTuneTurnStatus.CANCELLED, content_buffer or None, None,
            )
            await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
            return

        error_msg = _safe_error_message(exc)
        logger.exception("AI build failed: session_id=%s, turn_id=%s", session_id, turn_id)

        await asyncio.to_thread(
            _update_session_stage,
            session_id, ChatTuneActiveStage.BUILD, ChatTuneStageStatus.FAILED,
            turn_id=turn_id,
        )

        await asyncio.to_thread(
            _persist_assistant_message, assistant_message_id,
            status=ChatTuneTurnStatus.FAILED, content=content_buffer or None,
            payload=None, error=error_msg,
        )
        await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
        push_chat_tune_chunk(session_id, turn_id, {"type": "error", "error": error_msg})

    finally:
        if client is not None:
            await client.aclose()
        if container_id is not None:
            await asyncio.to_thread(
                container_service.cleanup_chat_tune_container, container_id
            )
        if docker_workdir is not None:
            await asyncio.to_thread(
                container_service.cleanup_chat_tune_workdir, docker_workdir
            )


def _maybe_proxy_provider_config(
    provider_config: dict[str, Any],
    user_id: uuid.UUID,
    task_id: uuid.UUID,
) -> dict[str, Any]:
    """Return a provider config safe to send into the agent container.

    When the LLM proxy is enabled, swap the real credentials for a one-time
    ``credential_broker`` token and point ``base_url`` at the in-cluster proxy,
    so the real api_key never enters the container running agent-driven code
    (which can execute generated code). When the proxy is disabled, return the
    config unchanged (matches the existing chat-tune behavior).

    Args:
        provider_config: The resolved provider config (contains the real key).
        user_id: Owner, for token auditing.
        task_id: Task the token is scoped to.

    Returns:
        A new provider config dict to send to the container.
    """
    if not settings.LLM_PROXY_ENABLE:
        return provider_config
    if provider_config.get("type") == "mock":
        return provider_config
    if not settings.LLM_PROXY_BASE_URL:
        raise RuntimeError(
            "LLM_PROXY_ENABLE is on but LLM_PROXY_BASE_URL is unset; refusing to "
            "send plaintext credentials into the agent container."
        )
    from app.services import credential_broker

    proxied = dict(provider_config)
    # Short bounded TTL: one build turn lives minutes, not the 7-day task TTL.
    # We deliberately do NOT revoke on completion (tokens are task-scoped and an
    # evolution run for the same task issues its own), so the TTL is the bound.
    token = credential_broker.issue_token(
        user_id=user_id,
        task_id=task_id,
        ttl=_AGENT_BUILD_TOKEN_TTL,
        provider_type=provider_config.get("type", "openai_compatible"),
        base_url=provider_config.get("base_url") or "",
        api_key=provider_config.get("api_key") or "",
        auth_token=provider_config.get("auth_token") or "",
        model=provider_config.get("model", ""),
        timeout=provider_config.get("timeout") or 60.0,
    )
    proxied["api_key"] = token
    proxied["auth_token"] = ""
    proxied["base_url"] = settings.LLM_PROXY_BASE_URL.rstrip("/")
    return proxied


async def _run_agent_build(
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        assistant_message_id: uuid.UUID,
        generation_id: str,
        task_id: uuid.UUID,
        user_id: uuid.UUID,
        provider_config: dict[str, Any],
        user_content: str = "",
        gathering_context: dict[str, Any] | None = None,
        input_data_path: str | None = None,
        allow_build: bool = False,
        proposed: dict[str, Any] | None = None,
) -> None:
    """Background coroutine: AI build (beta) via the AgentScope agent container.

    Two-phase (gather / build) with cross-turn memory:

    - GATHER (``allow_build=False``): the agent asks step-by-step; if it proposes a
      plan, a ``payload`` confirm card is emitted (persisted onto the message) and
      the proposal is stored in ``gathering_context`` for the build turn.
    - BUILD (``allow_build=True``): the agent builds from the confirmed proposal;
      artifacts are uploaded and ``input_args`` back-filled (as in ``_run_ai_build``).

    Conversation memory is carried across turns via ``gathering_context['agent_state']``
    (an ``agent_state`` event from the container, not forwarded to the frontend).
    Credentials are routed through the LLM proxy when enabled.
    """
    from app.services import container_service

    language = (gathering_context or {}).get("language") or "zh"
    content_buffer = ""
    blueprint_data: dict[str, Any] | None = None
    final_payload: dict[str, Any] | None = None
    new_proposed: dict[str, Any] | None = None
    new_agent_state: dict[str, Any] | None = None
    cancelled = False
    container_id: str | None = None
    docker_workdir: str | None = None
    client = None

    def _is_cancelled() -> bool:
        return get_chat_tune_generation_id(session_id, turn_id) != generation_id

    # The beta agent does both gathering and building in one coroutine, so the
    # left-panel stage must reflect the current phase: gather turns drive the
    # GATHERING stage, build turns drive the BUILD stage.
    phase_stage = (
        ChatTuneActiveStage.BUILD if allow_build else ChatTuneActiveStage.GATHERING
    )

    try:
        await asyncio.to_thread(
            _update_session_stage,
            session_id, phase_stage, ChatTuneStageStatus.RUNNING,
            activate=True,
            turn_id=turn_id,
        )

        import httpx
        from httpx_sse import aconnect_sse

        container_provider_config = await asyncio.to_thread(
            _maybe_proxy_provider_config, provider_config, user_id, task_id
        )

        docker_workdir, host_workdir = await asyncio.to_thread(
            container_service.prepare_chat_tune_workdir,
            input_data_path, str(session_id), str(turn_id),
        )

        container_id = await asyncio.to_thread(
            container_service.start_chat_tune_container,
            str(session_id), host_workdir,
        )

        host = container_service.chat_tune_container_host(str(session_id))
        base_url = f"http://{host}:{settings.CHAT_TUNE_CONTAINER_PORT}"

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
        )

        if not await _wait_chat_tune_ready(
            client, base_url, settings.CHAT_TUNE_CONTAINER_READY_TIMEOUT
        ):
            raise RuntimeError(_t(
                language, "构建容器启动超时，请重试",
                "Build container timed out while starting. Please retry.",
            ))

        body = {
            "provider_config": container_provider_config,
            "gathering_context": gathering_context,
            "user_content": user_content,
            "allow_build": allow_build,
            "agent_state": (gathering_context or {}).get("agent_state"),
            "proposed": proposed,
        }

        stream_done = False
        try:
            async with aconnect_sse(
                client, "POST", f"{base_url}/agent", json=body
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    if _is_cancelled():
                        cancelled = True
                        await asyncio.to_thread(
                            _persist_assistant_message, assistant_message_id,
                            ChatTuneTurnStatus.CANCELLED, content_buffer or None, None,
                        )
                        await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
                        return

                    try:
                        event = json.loads(sse.data)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue

                    etype = event.get("type")
                    if etype == "chunk":
                        chunk = event.get("content", "")
                        content_buffer += chunk
                        push_chat_tune_chunk(
                            session_id, turn_id, {"type": "chunk", "content": chunk}
                        )
                    elif etype == "payload":
                        # Interactive card from the gather phase — either a
                        # run_needs_gathering question card (ask_choice) or a
                        # confirm_build card. Forward to the frontend and remember
                        # it to persist onto the message; proposed is set only on
                        # the confirm card.
                        final_payload = event.get("data")
                        new_proposed = event.get("proposed")
                        if final_payload is not None:
                            push_chat_tune_chunk(
                                session_id, turn_id,
                                {"type": "payload", "data": final_payload},
                            )
                    elif etype == "agent_state":
                        # Conversation memory snapshot — persisted to
                        # gathering_context, NOT forwarded to the frontend.
                        new_agent_state = event.get("state")
                    elif etype == "build_result":
                        blueprint_data = event.get("blueprint_data")
                        await asyncio.to_thread(
                            _update_session_stage,
                            session_id,
                            ChatTuneActiveStage.BUILD,
                            ChatTuneStageStatus.COMPLETED,
                            turn_id=turn_id,
                        )
                    elif etype == "error":
                        raise RuntimeError(event.get("error") or _t(
                            language, "构建容器执行失败",
                            "Build container execution failed",
                        ))
                    elif etype == "done":
                        stream_done = True
                        break
        except httpx.RemoteProtocolError:
            if not stream_done:
                raise

        # Sync produced files: the agent wrote the package under
        # {project_name}/ in the mounted dir. Upload only that subdir and clear
        # all prior objects under input_data_path, matching _run_ai_build.
        if (
            input_data_path
            and docker_workdir
            and isinstance(blueprint_data, dict)
            and blueprint_data.get("built")
        ):
            project_name = blueprint_data.get("project_name")
            if project_name:
                from pathlib import Path

                from app.core.storage import storage

                data_path = Path(docker_workdir)
                product_root = data_path / project_name
                if product_root.is_dir():
                    existing_keys = await asyncio.to_thread(
                        storage.list_objects, input_data_path.rstrip("/") + "/"
                    )
                    if existing_keys:
                        await asyncio.to_thread(storage.delete_many, existing_keys)

                    for file_path in product_root.rglob("*"):
                        if not file_path.is_file():
                            continue
                        rel = file_path.relative_to(data_path)
                        key = f"{input_data_path}/{rel.as_posix()}"
                        await asyncio.to_thread(storage.upload, key, file_path.read_bytes())

                    new_input_args = await asyncio.to_thread(
                        _extract_input_args_from_product, product_root
                    )
                    await asyncio.to_thread(
                        _persist_task_input_args, task_id, new_input_args
                    )

        # Persist gathering_context updates: conversation memory (agent_state),
        # the confirmed/pending proposal, and the build blueprint when present.
        updated_ctx = dict(gathering_context or {})
        ctx_changed = False
        if new_agent_state is not None:
            updated_ctx["agent_state"] = new_agent_state
            ctx_changed = True
        if new_proposed is not None:
            updated_ctx["proposed"] = new_proposed
            ctx_changed = True
        if blueprint_data is not None:
            updated_ctx["blueprint_data"] = blueprint_data
            ctx_changed = True
        if ctx_changed:
            await asyncio.to_thread(
                _persist_session_gathering_context, session_id, updated_ctx
            )

        # Left-panel stage on a clean turn end. A gather turn that proposes the
        # plan (confirm_build card) marks GATHERING complete — requirements are
        # settled, awaiting the user's confirm. A gather turn that only asked a
        # question stays RUNNING (still gathering). Build-turn completion is
        # already marked via the build_result event above.
        if not allow_build:
            proposing = (
                isinstance(final_payload, dict)
                and final_payload.get("stage") == "confirm_build"
            )
            await asyncio.to_thread(
                _update_session_stage,
                session_id,
                ChatTuneActiveStage.GATHERING,
                ChatTuneStageStatus.COMPLETED if proposing else ChatTuneStageStatus.RUNNING,
                turn_id=turn_id,
            )

        # Persist the confirm-build card onto the assistant message so it survives
        # a refresh / replay (matches how chat_tune persists its payload).
        await asyncio.to_thread(
            _persist_assistant_message, assistant_message_id,
            status=ChatTuneTurnStatus.COMPLETED, content=content_buffer,
            payload=final_payload,
        )
        await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
        push_chat_tune_chunk(session_id, turn_id, {"type": "done"})

    except Exception as exc:
        if cancelled:
            return
        if _is_cancelled():
            cancelled = True
            await asyncio.to_thread(
                _persist_assistant_message, assistant_message_id,
                ChatTuneTurnStatus.CANCELLED, content_buffer or None, None,
            )
            await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
            return

        error_msg = _safe_error_message(exc)
        logger.exception("AI agent build failed: session_id=%s, turn_id=%s", session_id, turn_id)

        await asyncio.to_thread(
            _update_session_stage,
            session_id, phase_stage, ChatTuneStageStatus.FAILED,
            turn_id=turn_id,
        )

        await asyncio.to_thread(
            _persist_assistant_message, assistant_message_id,
            status=ChatTuneTurnStatus.FAILED, content=content_buffer or None,
            payload=None, error=error_msg,
        )
        await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
        push_chat_tune_chunk(session_id, turn_id, {"type": "error", "error": error_msg})

    finally:
        # NOTE: do NOT call revoke_task_tokens(task_id) here — proxy tokens are
        # keyed by task_id and an evolution run for the SAME task issues its own
        # token; a broad revoke would nuke it. The agent-build token is issued
        # with a short bounded TTL (_AGENT_BUILD_TOKEN_TTL) and self-expires,
        # matching how _run_ai_build relies on TTL rather than explicit revoke.
        if client is not None:
            await client.aclose()
        if container_id is not None:
            await asyncio.to_thread(
                container_service.cleanup_chat_tune_container, container_id
            )
        if docker_workdir is not None:
            await asyncio.to_thread(
                container_service.cleanup_chat_tune_workdir, docker_workdir
            )


async def _run_review_generation(
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        assistant_message_id: uuid.UUID,
        generation_id: str,
        task_id: uuid.UUID,  # noqa: ARG001
        provider_config: dict[str, Any],
        user_content: str,
        gathering_context: dict[str, Any] | None = None,
        input_data_path: str | None = None,
) -> None:
    """后台协程：在隔离容器中执行 review 对话，并把事件转发到 Redis。"""
    from app.services import container_service

    language = (gathering_context or {}).get("language") or "zh"
    content_buffer = ""
    cancelled = False
    container_id: str | None = None
    docker_workdir: str | None = None
    client = None
    review_result: dict[str, Any] | None = None

    def _is_cancelled() -> bool:
        return get_chat_tune_generation_id(session_id, turn_id) != generation_id

    try:
        await asyncio.to_thread(
            _update_session_stage,
            session_id, ChatTuneActiveStage.REVIEW, ChatTuneStageStatus.RUNNING,
            activate=True,
            turn_id=turn_id,
        )

        import httpx
        from httpx_sse import aconnect_sse

        docker_workdir, host_workdir = await asyncio.to_thread(
            container_service.prepare_chat_tune_workdir,
            input_data_path, str(session_id), str(turn_id),
        )

        container_id = await asyncio.to_thread(
            container_service.start_chat_tune_container,
            str(session_id), host_workdir,
        )

        host = container_service.chat_tune_container_host(str(session_id))
        base_url = f"http://{host}:{settings.CHAT_TUNE_CONTAINER_PORT}"

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
        )

        if not await _wait_chat_tune_ready(
            client, base_url, settings.CHAT_TUNE_CONTAINER_READY_TIMEOUT
        ):
            raise RuntimeError(_t(
                language, "Review 容器启动超时，请重试",
                "Review container timed out while starting. Please retry.",
            ))

        body = {
            "provider_config": provider_config,
            "user_content": user_content,
            "gathering_context": gathering_context,
        }

        stream_done = False
        try:
            async with aconnect_sse(
                client, "POST", f"{base_url}/review", json=body
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    if _is_cancelled():
                        cancelled = True
                        await asyncio.to_thread(
                            _persist_assistant_message, assistant_message_id,
                            ChatTuneTurnStatus.CANCELLED, content_buffer or None, None,
                        )
                        await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
                        return

                    try:
                        event = json.loads(sse.data)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue

                    etype = event.get("type")
                    if etype == "chunk":
                        chunk = event.get("content", "")
                        content_buffer += chunk
                        push_chat_tune_chunk(
                            session_id, turn_id, {"type": "chunk", "content": chunk}
                        )
                    elif etype == "review_response":
                        review_result = event
                        await asyncio.to_thread(
                            _update_session_stage,
                            session_id,
                            ChatTuneActiveStage.REVIEW,
                            ChatTuneStageStatus.COMPLETED,
                            turn_id=turn_id,
                        )
                    elif etype == "error":
                        raise RuntimeError(event.get("error") or _t(
                            language, "Review 容器执行失败",
                            "Review container execution failed",
                        ))
                    elif etype == "done":
                        stream_done = True
                        break
        except httpx.RemoteProtocolError:
            if not stream_done:
                raise

        # confirmed 或 regenerate 时容器已写文件到挂载目录，上传到 S3
        action = review_result.get("action") if review_result else None
        if action in ("confirmed", "regenerate") and input_data_path and docker_workdir:
            from pathlib import Path

            from app.core.storage import storage

            data_path = Path(docker_workdir)
            for file_path in data_path.rglob("*"):
                if not file_path.is_file():
                    continue
                rel = file_path.relative_to(data_path)
                key = f"{input_data_path}/{rel.as_posix()}"
                await asyncio.to_thread(storage.upload, key, file_path.read_bytes())

        # 持久化 blueprint_data 和 review_messages 到 gathering_context
        if review_result is not None:
            updated_ctx = dict(gathering_context or {})
            if review_result.get("blueprint_data"):
                updated_ctx["blueprint_data"] = review_result["blueprint_data"]
            if review_result.get("updated_messages") is not None:
                updated_ctx["review_messages"] = review_result["updated_messages"]
            await asyncio.to_thread(
                _persist_session_gathering_context, session_id, updated_ctx
            )

        await asyncio.to_thread(
            _persist_assistant_message, assistant_message_id,
            status=ChatTuneTurnStatus.COMPLETED, content=content_buffer, payload=None,
        )
        await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
        # 落库完成后再推终止事件：前端收到 done 后会立即回查 DB，须保证此时已是最终状态
        push_chat_tune_chunk(session_id, turn_id, {"type": "done"})

    except Exception as exc:
        if cancelled:
            return
        if _is_cancelled():
            cancelled = True
            await asyncio.to_thread(
                _persist_assistant_message, assistant_message_id,
                ChatTuneTurnStatus.CANCELLED, content_buffer or None, None,
            )
            await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
            return

        error_msg = _safe_error_message(exc)
        logger.exception(
            "Review generation failed: session_id=%s, turn_id=%s", session_id, turn_id
        )
        await asyncio.to_thread(
            _update_session_stage,
            session_id, ChatTuneActiveStage.REVIEW, ChatTuneStageStatus.FAILED,
            turn_id=turn_id,
        )
        await asyncio.to_thread(
            _persist_assistant_message, assistant_message_id,
            status=ChatTuneTurnStatus.FAILED, content=content_buffer or None,
            payload=None, error=error_msg,
        )
        await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
        push_chat_tune_chunk(session_id, turn_id, {"type": "error", "error": error_msg})

    finally:
        if client is not None:
            await client.aclose()
        if container_id is not None:
            await asyncio.to_thread(
                container_service.cleanup_chat_tune_container, container_id
            )
        if docker_workdir is not None:
            await asyncio.to_thread(
                container_service.cleanup_chat_tune_workdir, docker_workdir
            )


async def _run_chat_tune_generation(
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        assistant_message_id: uuid.UUID,
        provider_config: dict[str, Any],
        user_content: str,
        base_config: dict[str, Any],  # noqa: ARG001  # TODO(调参业务): 待用于 prompt 构造
        generation_id: str,
        gathering_context: dict[str, Any] | None = None,
        input_data_path: str | None = None,
) -> None:
    """后台协程：在隔离容器中执行 needs-gathering，并把事件转发到 Redis。

    出于安全考虑，``process_needs_gathering_turn_stream``（含 ``read_file``
    工具）不再在特权 backend 容器内执行，而是放到一个即用即销的隔离容器
    （复用 ``TASK_RUNNER_IMAGE``）中运行——容器只挂载本会话临时数据目录，
    端口不发布，backend 通过容器名 DNS 解析并消费其 SSE。

    每次处理事件前都会校验 generation_id；若发现新轮已被启动或被取消，则
    以协作式取消方式优雅退出。无论成功/失败/取消，``finally`` 都会销毁容器
    并清理临时数据目录。

    ``gathering_context`` 为上一轮持久化在 session 中的 NeedsGatheringContext
    快照（dict）。为 ``None`` 时表示首轮对话；本轮成功完成后会把更新后的
    ctx 写回 session。
    """
    from app.services import container_service

    language = (gathering_context or {}).get("language") or "zh"
    content_buffer = ""
    payload: dict[str, Any] | None = None
    updated_messages: list[dict[str, Any]] | None = None
    needs_profile: dict[str, Any] | None = None
    cancelled = False
    container_id: str | None = None
    docker_workdir: str | None = None
    client = None

    def _is_cancelled() -> bool:
        return get_chat_tune_generation_id(session_id, turn_id) != generation_id

    try:
        await asyncio.to_thread(
            _update_session_stage,
            session_id,
            ChatTuneActiveStage.GATHERING,
            ChatTuneStageStatus.RUNNING,
            activate=True,
            turn_id=turn_id,
        )

        import httpx
        from httpx_sse import aconnect_sse

        # 1. 准备临时数据目录（下载用户数据）—— 阻塞 IO 入线程池
        docker_workdir, host_workdir = await asyncio.to_thread(
            container_service.prepare_chat_tune_workdir,
            input_data_path,
            str(session_id),
            str(turn_id),
        )

        # 2. 启动隔离容器
        container_id = await asyncio.to_thread(
            container_service.start_chat_tune_container,
            str(session_id),
            host_workdir,
        )

        host = container_service.chat_tune_container_host(str(session_id))
        base_url = f"http://{host}:{settings.CHAT_TUNE_CONTAINER_PORT}"

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
        )

        # 3. 等待容器内 SSE 服务就绪
        if not await _wait_chat_tune_ready(
            client, base_url, settings.CHAT_TUNE_CONTAINER_READY_TIMEOUT
        ):
            raise RuntimeError(_t(
                language, "调参容器启动超时，请重试",
                "Chat-tune container timed out while starting. Please retry.",
            ))

        body = {
            "provider_config": provider_config,
            "user_content": user_content,
            "gathering_context": gathering_context,
            "max_tool_rounds": 3,
        }

        # 4. 消费容器 SSE 并转发到 Redis
        stream_done = False
        try:
            async with aconnect_sse(
                client, "POST", f"{base_url}/run", json=body
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    if _is_cancelled():
                        cancelled = True
                        logger.info(
                            "Chat tune generation cancelled: session_id=%s, turn_id=%s",
                            session_id,
                            turn_id,
                        )
                        await asyncio.to_thread(
                            _persist_assistant_message,
                            assistant_message_id,
                            status=ChatTuneTurnStatus.CANCELLED,
                            content=content_buffer or None,
                            payload=payload,
                        )
                        await asyncio.to_thread(
                            _maybe_clear_active_turn, session_id, turn_id
                        )
                        return

                    try:
                        event = json.loads(sse.data)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue

                    etype = event.get("type")
                    if etype == "chunk":
                        chunk = event.get("content", "")
                        content_buffer += chunk
                        push_chat_tune_chunk(
                            session_id, turn_id, {"type": "chunk", "content": chunk}
                        )
                    elif etype == "response":
                        updated_messages = event.get("updated_messages") or []
                        if event.get("response_type") == "choices":
                            payload = _build_choice_payload(event.get("choices") or [])
                        elif event.get("response_type") == "complete":
                            payload = _build_confirm_build_payload(language)
                            needs_profile = event.get("needs_profile")
                            await asyncio.to_thread(
                                _update_session_stage,
                                session_id,
                                ChatTuneActiveStage.GATHERING,
                                ChatTuneStageStatus.COMPLETED,
                                turn_id=turn_id,
                            )
                    elif etype == "error":
                        raise RuntimeError(event.get("error") or _t(
                            language, "调参容器执行失败",
                            "Chat-tune container execution failed",
                        ))
                    elif etype == "done":
                        stream_done = True
                        break
        except httpx.RemoteProtocolError:
            if not stream_done:
                raise

        if payload is not None:
            push_chat_tune_chunk(
                session_id, turn_id, {"type": "payload", "data": payload}
            )

        await asyncio.to_thread(
            _persist_assistant_message,
            assistant_message_id,
            status=ChatTuneTurnStatus.COMPLETED,
            content=content_buffer,
            payload=payload,
        )
        # 本轮成功完成，把更新后的 ctx 持久化回 session
        if updated_messages is not None:
            ctx_dict = _build_gathering_context(updated_messages, gathering_context)
            if needs_profile is not None:
                ctx_dict["needs_profile"] = needs_profile
            await asyncio.to_thread(
                _persist_session_gathering_context,
                session_id,
                ctx_dict,
            )
        await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
        # 落库完成后再推终止事件：前端收到 done 后会立即回查 DB，须保证此时已是最终状态
        push_chat_tune_chunk(session_id, turn_id, {"type": "done"})

    except Exception as exc:
        if cancelled:
            return
        # stop_turn 会 kill 容器，使 SSE 连接中断抛错；若本轮已被取消（generation_id
        # 变更/清除），按取消而非失败收尾。
        if _is_cancelled():
            cancelled = True
            logger.info(
                "Chat tune generation cancelled (connection closed): session_id=%s, turn_id=%s",
                session_id,
                turn_id,
            )
            await asyncio.to_thread(
                _persist_assistant_message,
                assistant_message_id,
                status=ChatTuneTurnStatus.CANCELLED,
                content=content_buffer or None,
                payload=payload,
            )
            await asyncio.to_thread(_maybe_clear_active_turn, session_id, turn_id)
            return
        logger.exception(
            "Chat tune generation failed: session_id=%s, turn_id=%s",
            session_id,
            turn_id,
        )
        safe_msg = _safe_error_message(exc)
        await asyncio.to_thread(
            _update_session_stage,
            session_id,
            ChatTuneActiveStage.GATHERING,
            ChatTuneStageStatus.FAILED,
            turn_id=turn_id,
        )
        await asyncio.to_thread(
            _persist_assistant_message,
            assistant_message_id,
            status=ChatTuneTurnStatus.FAILED,
            content=content_buffer or None,
            payload=payload,
            error=safe_msg,
        )
        await asyncio.to_thread(
            _maybe_clear_active_turn, session_id, turn_id
        )
        push_chat_tune_chunk(
            session_id,
            turn_id,
            {"type": "error", "error": safe_msg},
        )
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                logger.debug("Failed to aclose chat tune httpx client", exc_info=True)
        # 即用即销：强制移除容器（force 可移除仍在运行的容器）并清理临时目录
        if container_id is not None:
            await asyncio.to_thread(
                container_service.cleanup_chat_tune_container, container_id
            )
        if docker_workdir is not None:
            await asyncio.to_thread(
                container_service.cleanup_chat_tune_workdir, docker_workdir
            )
        if not cancelled:
            clear_chat_tune_generation_id(session_id, turn_id)


async def _wait_chat_tune_ready(client, base_url: str, timeout: float) -> bool:
    """轮询容器 ``/health`` 端点直到就绪或超时。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = await client.get(f"{base_url}/health", timeout=5.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


def _build_choice_payload(choices: list[dict[str, Any]]) -> dict[str, Any]:
    """把 consultant 回传的选项序列化为前端单选表单 payload。"""
    return {
        "cardId": f"card-{uuid.uuid4().hex[:8]}",
        "kind": "choice",
        "stage": "run_needs_gathering",
        "prompt": "",
        "hint": "",
        "options": [
            {
                "value": c.get("full_text", ""),
                "label": c.get("label", ""),
                "description": c.get("description", ""),
                "ask_for_path": c.get("ask_for_path", False),
                "ask_for_dir": c.get("ask_for_dir", False),
                "is_custom": c.get("is_custom", False),
            }
            for c in choices
        ],
    }


_CONFIRM_BUILD_VALUE = "confirm_build"
_DECLINE_BUILD_VALUE = "decline_build"


def _build_confirm_build_payload(language: str = "zh") -> dict[str, Any]:
    """构造确认是否开始 AI 构建的表单 payload。

    Args:
        language: 语言码（'zh'/'en'），决定表单文案语言；缺省中文。
    """
    return {
        "cardId": f"card-{uuid.uuid4().hex[:8]}",
        "kind": "choice",
        "stage": "confirm_build",
        "prompt": _t(
            language, "需求分析已完成，是否开始 AI 构建？",
            "Needs analysis is complete. Start the AI build?",
        ),
        "hint": "",
        "options": [
            {
                "value": _CONFIRM_BUILD_VALUE,
                "label": _t(language, "确认构建", "Start build"),
                "description": _t(
                    language, "开始 AI 自动构建任务配置",
                    "Let the AI build the task configuration automatically",
                ),
            },
            {
                "value": _DECLINE_BUILD_VALUE,
                "label": _t(language, "暂不构建", "Not now"),
                "description": _t(
                    language, "继续调参对话", "Keep chatting to tune the configuration",
                ),
            },
        ],
    }


def _build_gathering_context(
        updated_messages: list[dict[str, Any]],
        incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """根据本轮更新后的对话历史拼装可持久化的 gathering_context。

    ``user_context`` 在单轮内不变，沿用上一轮快照（首轮缺省）。
    ``language`` 取本轮 ``incoming`` 注入值（由 ``start_turn`` 从前端请求写入），
    使每轮都能按前端选择的语言回答；缺省回退到 ``"zh"``。
    ``user_input`` 为每轮瞬时字段，不持久化。
    """
    incoming = incoming or {}
    return {
        "phase_messages": list(updated_messages or []),
        "user_context": incoming.get("user_context"),
        "language": incoming.get("language") or "zh",
    }


# ---- Mock payload（CHAT_TUNE_MOCK=1 时在 LLM 流式输出结束后附加随机表单） ----

_MOCK_PAYLOADS: list[dict[str, Any]] = [
    {
        "kind": "choice",
        "stage": "evolution",
        "prompt": "选择进化算法策略",
        "hint": "不同算法适合不同问题类型，选择后我会推荐对应的默认参数。",
        "options": [
            {
                "value": "funsearch",
                "label": "FunSearch",
                "description": "适合数学/组合搜索类问题，简单稳定",
            },
            {
                "value": "eoh",
                "label": "EoH",
                "description": "Evolution of Heuristics，适合启发式生成",
            },
            {
                "value": "meoh",
                "label": "MEoH",
                "description": "多目标版本，需要 Pareto 前沿",
            },
            {
                "value": "regevo",
                "label": "RegEvo",
                "description": "正则化进化，适合稳健调优",
            },
        ],
    },
    {
        "kind": "number",
        "stage": "evaluator",
        "prompt": "单次评估的超时时间（秒）？",
        "hint": "复杂问题建议设置 60-120 秒，简单问题 10-30 秒即可。",
        "min": 5,
        "max": 600,
        "step": 5,
        "defaultValue": 60,
        "unit": "秒",
    },
    {
        "kind": "multichoice",
        "stage": "advanced",
        "prompt": "需要启用哪些高级能力？（可多选）",
        "hint": "未勾选的项会使用框架推荐默认值。",
        "options": [
            {
                "value": "memory",
                "label": "启用记忆模块",
                "description": "跨代复用历史经验",
            },
            {
                "value": "multimodal",
                "label": "多模态输入",
                "description": "支持图像/草图输入",
            },
            {
                "value": "version",
                "label": "细粒度版本控制",
                "description": "保存每代代码快照",
            },
            {
                "value": "logging",
                "label": "详细日志",
                "description": "结构化 JSON 日志便于检索",
            },
        ],
        "defaultSelected": ["logging"],
    },
    {
        "kind": "choice",
        "stage": "coder",
        "prompt": "选择代码生成策略",
        "hint": "Diff 模式可以在迭代后期节省约 40% token。",
        "options": [
            {
                "value": "standard",
                "label": "标准模板",
                "description": "通用 Prompt + 完整代码替换",
            },
            {
                "value": "diff",
                "label": "Diff 模式",
                "description": "基于差异的增量编辑，节省 token",
            },
            {
                "value": "custom",
                "label": "自定义 Prompt",
                "description": "你自己提供 Prompt 模板",
            },
        ],
    },
    {
        "kind": "number",
        "stage": "evolution",
        "prompt": "设置初始种群大小",
        "hint": "通常 8-32 之间，越大探索越充分但耗时增加。",
        "min": 4,
        "max": 128,
        "step": 2,
        "defaultValue": 16,
        "unit": "个个体",
    },
    {
        "kind": "text",
        "stage": "data",
        "prompt": "请输入自定义数据集路径或描述",
        "placeholder": "例如: /data/my_dataset/ 或描述数据格式...",
        "defaultValue": "",
    },
    {
        "kind": "choice",
        "stage": "data",
        "prompt": "你的项目数据是否已就绪？",
        "hint": "若尚未上传，可以稍后切换到「分步配置」补充上传步骤。",
        "options": [
            {"value": "ready", "label": "已上传，使用我项目里的数据"},
            {"value": "sample", "label": "使用示例数据集快速体验"},
            {"value": "later", "label": "暂不上传，先保存配置"},
        ],
    },
]


def _pick_mock_payload() -> dict[str, Any]:
    """Randomly pick a mock form payload with a unique cardId."""
    payload = copy.deepcopy(random.choice(_MOCK_PAYLOADS))
    payload["cardId"] = f"card-mock-{uuid.uuid4().hex[:8]}"
    return payload


# ---- 持久化辅助 ----


def _persist_assistant_message(
        message_id: uuid.UUID,
        status: ChatTuneTurnStatus,
        content: str | None,
        payload: dict[str, Any] | None,
        error: str | None = None,
) -> None:
    """新开数据库会话，落库 assistant 消息的最终状态。"""
    from app.core.db import engine

    with Session(engine) as db:
        message = db.get(ChatTuneMessage, message_id)
        if message is None:
            logger.warning(
                "Assistant message %s not found when persisting", message_id
            )
            return
        message.content = content or ""
        message.payload = payload
        message.turn_status = status
        message.error = error
        message.updated_time = datetime.now(UTC)
        if payload is not None:
            flag_modified(message, "payload")
        db.add(message)
        db.commit()


def _persist_session_gathering_context(
        session_id: uuid.UUID, context_dict: dict[str, Any]
) -> None:
    """把 NeedsGatheringContext 的序列化快照写回 session。

    供 ``_run_chat_tune_generation`` 在成功完成本轮后调用。
    """
    from app.core.db import engine

    with Session(engine) as db:
        session = db.get(ChatTuneSession, session_id)
        if session is None:
            logger.warning(
                "Chat tune session %s not found when persisting gathering_context",
                session_id,
            )
            return
        session.gathering_context = context_dict
        session.updated_time = datetime.now(UTC)
        flag_modified(session, "gathering_context")
        db.add(session)
        db.commit()


def _maybe_clear_active_turn(
        session_id: uuid.UUID, turn_id: uuid.UUID
) -> None:
    """若 session 当前活跃轮次仍是本轮，则清空 active_turn_id。"""
    from app.core.db import engine

    with Session(engine) as db:
        session = db.get(ChatTuneSession, session_id)
        if session is None:
            return
        if session.active_turn_id == turn_id:
            session.active_turn_id = None
            session.updated_time = datetime.now(UTC)
            db.add(session)
            db.commit()


# 三阶段状态机 helper：协程在生命周期关键节点调用本函数维护 session 状态。
# 取消路径不显式调用——保留最后一次显式置位的值；前端可结合 active_turn_id
# 判断"运行中"是否实际中断。
def _update_session_stage(
        session_id: uuid.UUID,
        stage: ChatTuneActiveStage,
        status: ChatTuneStageStatus,
        *,
        activate: bool = False,
        turn_id: uuid.UUID | None = None,
) -> None:
    """更新调参会话某个阶段的状态，可选地把它设为 active_stage。

    DB 落库后会向 ``(session_id, turn_id)`` 对应的 Redis Stream 推送一条
    ``stage_update`` 事件，含三个阶段的最新快照和当前 ``active_stage``，
    供前端实时刷新步骤条/状态指示。Stream 推送失败仅记日志，不影响主流程。

    Args:
        session_id: 会话 ID。
        stage: 要更新状态的阶段（gathering / build / review）。
        status: 该阶段的新状态。
        activate: 是否把该阶段同时设为 active_stage。仅协程"开始执行"
            时传 True；后续状态翻转不改激活阶段。
        turn_id: 当前轮次 ID。提供时会向对应 SSE 流推送状态变更事件；
            为 None 时只更新 DB 不推送（如 reset/批量场景）。
    """
    from app.core.db import engine

    snapshot: dict[str, Any] | None = None
    with Session(engine) as db:
        session = db.get(ChatTuneSession, session_id)
        if session is None:
            return
        if stage is ChatTuneActiveStage.GATHERING:
            session.gathering_status = status.value
        elif stage is ChatTuneActiveStage.BUILD:
            session.build_status = status.value
        elif stage is ChatTuneActiveStage.REVIEW:
            session.review_status = status.value
        if activate:
            session.active_stage = stage.value
        session.updated_time = datetime.now(UTC)
        db.add(session)
        db.commit()

        if turn_id is not None:
            snapshot = {
                "type": "stage_update",
                "stage": stage.value,
                "status": status.value,
                "active_stage": session.active_stage,
                "gathering_status": session.gathering_status,
                "build_status": session.build_status,
                "review_status": session.review_status,
            }

    if snapshot is not None and turn_id is not None:
        push_chat_tune_chunk(session_id, turn_id, snapshot)


def _extract_input_args_from_product(product_root: Any) -> dict[str, Any]:
    """从构建产物根目录第一层取第一个 yaml 文件并解析为 dict。

    解析失败（找不到、IO 错误、yaml 语法错误、顶层非 mapping）时回退到
    ``generate_default_input_args()``，保证调用方总能拿到合法 dict。

    Args:
        product_root: 构建产物根目录（``Path``）。

    Returns:
        解析得到或默认的 input_args。
    """
    import yaml

    from app.schemas.task import generate_default_input_args

    try:
        if product_root.is_dir():
            yaml_files = sorted(
                p
                for p in product_root.iterdir()
                if p.is_file() and p.suffix.lower() in (".yaml", ".yml")
            )
            for f in yaml_files:
                try:
                    with open(f, encoding="utf-8") as fh:
                        parsed = yaml.safe_load(fh)
                    if isinstance(parsed, dict):
                        return parsed
                    logger.warning(
                        "Yaml %s top-level is not a mapping, skip", f
                    )
                except (OSError, yaml.YAMLError):
                    logger.exception("Failed to parse yaml %s", f)
    except OSError:
        logger.exception("Failed to scan product root %s", product_root)

    return generate_default_input_args()


def _persist_task_input_args(
        task_id: uuid.UUID, input_args: dict[str, Any]
) -> None:
    """覆盖 task.input_args 并更新 version_time。

    在写入前回填 ``planner.provider`` / ``planner.provider_model`` /
    ``coder.provider`` / ``coder.provider_model``：AI 构建产物里的 provider
    引用只是占位符，真实的供应商选择来自用户在前端任务上保留的配置，
    持久化时必须保留这些字段而不被产物覆盖。
    """
    from app.core.db import engine

    with Session(engine) as db:
        task = db.get(models.Task, task_id)
        if task is None:
            logger.warning(
                "Task %s not found when persisting input_args", task_id
            )
            return

        existing_args = dict(task.input_args or {})
        for section_key in ("planner", "coder"):
            existing_section = existing_args.get(section_key)
            new_section = input_args.get(section_key)
            if not isinstance(existing_section, dict) or not isinstance(
                new_section, dict
            ):
                continue
            for field_key in ("provider", "provider_model"):
                if field_key in existing_section:
                    new_section[field_key] = existing_section[field_key]

        task.input_args = input_args
        now = datetime.now(UTC)
        task.updated_time = now
        task.version_time = now
        flag_modified(task, "input_args")
        db.add(task)
        db.commit()


# ---- 启动钩子：清理进程重启遗留的"幽灵 generating"轮次 ----


def reset_orphan_turns(idle_minutes: int = 10) -> int:
    """启动时调用，把进程重启遗留的 generating 轮次兜底为 failed。

    判定条件：``turn_status = generating`` 且 ``updated_time`` 早于阈值。
    同时清理对应 session 的 ``active_turn_id``。

    Returns:
        被重置的消息条数。
    """
    from app.core.db import engine

    cutoff = datetime.now(UTC) - timedelta(minutes=idle_minutes)
    with Session(engine) as db:
        orphan_msgs = db.exec(
            select(ChatTuneMessage)
            .where(ChatTuneMessage.turn_status == ChatTuneTurnStatus.GENERATING)
            .where(ChatTuneMessage.updated_time < cutoff)
        ).all()
        if not orphan_msgs:
            return 0

        affected_session_ids: set[uuid.UUID] = set()
        now = datetime.now(UTC)
        for msg in orphan_msgs:
            msg.turn_status = ChatTuneTurnStatus.FAILED
            msg.error = "进程重启或异常退出导致生成中断"
            msg.updated_time = now
            db.add(msg)
            affected_session_ids.add(msg.session_id)

        # 清理这些 session 的 active_turn_id（仅当它指向受影响的轮次）
        if affected_session_ids:
            sessions = db.exec(
                select(ChatTuneSession).where(
                    ChatTuneSession.id.in_(affected_session_ids)
                )
            ).all()
            orphan_turn_ids = {m.turn_id for m in orphan_msgs}
            for s in sessions:
                if s.active_turn_id in orphan_turn_ids:
                    s.active_turn_id = None
                    s.updated_time = now
                    db.add(s)

        db.commit()
        for msg in orphan_msgs:
            delete_chat_tune_stream(msg.session_id, msg.turn_id)
            clear_chat_tune_generation_id(msg.session_id, msg.turn_id)
        logger.info(
            "Reset %d orphan chat tune generating turns", len(orphan_msgs)
        )
        return len(orphan_msgs)


# ---- 任务创建时注入初始消息 ----

_WELCOME_TEXT = {
    "zh": (
        "## 👋 欢迎使用 LLM4AD AI 助手\n\n"
        "我将帮助你配置任务参数，让算法设计更加高效。你可以：\n\n"
        "- 直接**描述你的需求**，我来帮你生成配置\n"
        "- 随时对现有配置提出**修改建议**"
    ),
    "en": (
        "## 👋 Welcome to the LLM4AD AI Assistant\n\n"
        "I'll help you configure task parameters for efficient algorithm design. You can:\n\n"
        "- **Describe your needs** and I'll generate a configuration\n"
        "- Ask for **modifications** to the current config at any time"
    ),
}


def seed_initial_messages(
        db: Session,
        task: models.Task,
        current_user: models.User,
        language: str,
        template_name: str | None,  # noqa: ARG001
        config_name: str,  # noqa: ARG001
) -> None:
    """任务创建后注入初始欢迎消息。

    Args:
        db: 数据库会话。
        task: 已创建的任务实例。
        current_user: 当前用户。
        language: 语言标识（zh/en）。
        template_name: 保留参数，不再使用。
        config_name: 保留参数，不再使用。
    """
    lang = language if language in ("zh", "en") else "zh"
    session = _get_or_create_session(db, task, current_user)

    welcome_msg = ChatTuneMessage(
        session_id=session.id,
        turn_id=uuid.uuid4(),
        role=ChatTuneMessageRole.SYSTEM,
        content=_WELCOME_TEXT[lang],
        turn_status=ChatTuneTurnStatus.COMPLETED,
    )
    db.add(welcome_msg)
    db.commit()
