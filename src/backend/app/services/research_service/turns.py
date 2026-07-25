"""轮次（Turn）子域：新一轮生成的入队、停止、重试 + 门控（gate）回复恢复。

核心是 :func:`start_turn` 的分派：会话下若存在 ``PAUSED_GATE`` turn，则走
:func:`_reply_to_gate` 恢复路径（把用户 submission 翻译成起始 stage、新建一轮
从断点续跑）；否则建全新 turn。轮次读侧（``get_turn`` / ``list_session_turns`` /
``get_stream_context``）也归此模块。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app import models
from app.core.redis import (
    check_research_rate_limit,
    clear_research_gate_reply,
    delete_research_stream,
)
from app.models.research import (
    ResearchMessage,
    ResearchMessageRole,
    ResearchSession,
    ResearchSessionStatus,
    ResearchTurn,
    ResearchTurnStatus,
)
from app.schemas.research import (
    ResearchMessageItem,
    ResearchSessionItem,
    ResearchTurnItem,
    ResearchTurnListResponse,
    ResearchTurnRetryRequest,
    ResearchTurnStartRequest,
    ResearchTurnStartResponse,
)
from app.tasks.research_runner import enqueue_research_turn, stage_display_name

from ._common import (
    _find_turn_by_status,
    _get_session,
    _get_session_and_turn,
    _parse_cursor,
)

# 哨兵：区分「调用方未传该覆盖」与「显式传 None」——from_stage /
# respond_to_message_id 的 None 都是合法值，不能用 None 当默认。
_UNSET: Any = object()


def _new_assistant_message(
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    created_time: datetime | None = None,
) -> ResearchMessage:
    """本轮 assistant 占位消息（空内容、RUNNING、event_key=assistant:<turn>）。

    ``created_time`` 可显式指定，用于让 assistant 严格晚于同轮 user 消息——
    二者若同一微秒创建（Windows 时钟精度粗），``ORDER BY created_time, id`` 会
    退化到按随机 uuid 排序，导致 assistant 偶尔冒到 user 前面。
    """
    msg = ResearchMessage(
        session_id=session_id,
        turn_id=turn_id,
        role=ResearchMessageRole.ASSISTANT,
        content="",
        turn_status=ResearchTurnStatus.RUNNING.value,
        event_key=f"assistant:{turn_id}",
    )
    if created_time is not None:
        msg.created_time = created_time
        msg.updated_time = created_time
    return msg


def _find_paused_gate_turn(
    db: Session, session_id: uuid.UUID
) -> ResearchTurn | None:
    """会话下命中硬门控、等待回复的 turn（status == PAUSED_GATE）。

    命中硬门控会**结束任务**并把 turn 落 ``PAUSED_GATE``；存在这样的 turn 即表示
    有待回复的门控，start_turn 据此分派到 :func:`_reply_to_gate`。（保留此包装供
    worker 侧 ``research_runner.collab`` 复用。）
    """
    return _find_turn_by_status(
        db, session_id, ResearchTurnStatus.PAUSED_GATE.value
    )


def _create_turn_row(
    session: ResearchSession,
    request: ResearchTurnStartRequest,
    *,
    from_stage: Any = _UNSET,
    to_stage: Any = _UNSET,
    user_input: Any = _UNSET,
    respond_to_message_id: Any = _UNSET,
) -> ResearchTurn:
    """构造 ResearchTurn ORM 行，尚未 add 到 session。

    ``from_stage`` / ``to_stage`` / ``user_input`` / ``respond_to_message_id`` 默认取
    ``request.*``；门控恢复路径（:func:`_reply_to_gate`）传入按 submission 算出的值
    覆盖。四者 None 均为合法值，故用 ``_UNSET`` 哨兵区分「未覆盖」与「显式传 None」。

    ``celery_task_id`` 在此预生成：与 turn 行同一事务落库，入队时用它作
    ``apply_async(task_id=...)``，杜绝 worker 抢先执行时 ID 仍为空的窗口。

    FK 不变量：turn 行落库后须先 ``db.flush()`` 再写 message / 更新 session——两者
    都有 FK 指向 ``research_turn.id``，不先 flush 则 UoW 可能把它们的写排到 turn
    INSERT 之前触发 FK 失败。
    """
    return ResearchTurn(
        id=uuid.uuid4(),
        session_id=session.id,
        status=ResearchTurnStatus.RUNNING.value,
        celery_task_id=str(uuid.uuid4()),
        provider_id=request.provider_id or session.provider_id,
        model_name=request.model_name or session.model_name,
        mode=(request.mode.value if request.mode else None),
        from_stage=(request.from_stage if from_stage is _UNSET else from_stage),
        to_stage=(request.to_stage if to_stage is _UNSET else to_stage),
        user_input=(request.content if user_input is _UNSET else user_input),
        respond_to_message_id=(
            request.respond_to_message_id
            if respond_to_message_id is _UNSET
            else respond_to_message_id
        ),
    )


def _persist_turn_messages(
    db: Session,
    session: ResearchSession,
    turn: ResearchTurn,
    request: ResearchTurnStartRequest,
) -> tuple[ResearchMessage, ResearchMessage]:
    """写 user_message（必写）+ assistant 占位（必写）。

    每轮必产一条 user 消息：前端传了 ``content`` 用它，否则按场景落一句动作
    文案（首轮 = start run / 停止后继续 = continue run），保证前端历史里每一轮
    都有对应的用户气泡。此函数在 :func:`_advance_session_pointer` 之前调用，故
    ``session.status`` 仍是本轮触发前的原始状态，可据此区分场景。
    """
    content = (request.content or "").strip()
    if not content:
        # PENDING = 从没跑过的首轮；其余（COMPLETED/FAILED/CANCELLED）= 停止后继续。
        content = (
            "start run"
            if session.status == ResearchSessionStatus.PENDING.value
            else "continue run"
        )
    # user 时间戳显式早于 assistant，杜绝同微秒下按随机 uuid 乱序（见
    # _new_assistant_message）。
    now = datetime.now(UTC)
    user_message = ResearchMessage(
        session_id=session.id,
        turn_id=turn.id,
        role=ResearchMessageRole.USER,
        content=content,
        turn_status=ResearchTurnStatus.COMPLETED.value,
        event_key=f"user:{turn.id}",
        created_time=now,
        updated_time=now,
    )
    db.add(user_message)
    assistant_message = _new_assistant_message(
        session.id, turn.id, created_time=now + timedelta(milliseconds=1)
    )
    db.add(assistant_message)
    return user_message, assistant_message


def _advance_session_pointer(
    session: ResearchSession,
    turn_id: uuid.UUID,
    request: ResearchTurnStartRequest,
) -> None:
    """把 session 指针切到新 turn，同步覆盖字段（mode/provider/model）。"""
    session.active_turn_id = turn_id
    session.status = ResearchSessionStatus.RUNNING.value
    session.ended_time = None
    session.error = None
    session.updated_time = datetime.now(UTC)
    if request.mode:
        session.mode = request.mode.value
    if request.provider_id:
        session.provider_id = request.provider_id
    if request.model_name:
        session.model_name = request.model_name


def _compute_gate_resume(
    gate_stage: int,
    submission: dict[str, Any],
) -> tuple[str | None, str | None, bool]:
    """把门控 submission 翻译成恢复参数 ``(from_stage, guidance, abort)``。

    ``gate_stage`` 为命中门控的 stage 号 N，``from_stage`` 是新一轮起始 stage
    （字符串；abort 时为 None）：

    - approve/skip/未知 → ``N+1``（门控 stage 产物已在盘，无需重跑）；
    - reject/pivot → ``rollback_to_stage`` 或回退到 ``N``（重跑本 stage）；
    - edit/inject/collaborate → ``N``（带 guidance 重跑本 stage）；
    - abort → ``abort=True``，会话置 CANCELLED、不建新 turn。

    ``guidance`` 为注入到 ``from_stage`` 的文字（worker 侧落成 hitl_guidance.md）。
    ``collaborate`` 暂按「带 guidance 重跑本 stage」降级处理。
    """
    action = str(submission.get("action") or "approve").lower()
    guidance = str(
        submission.get("guidance") or submission.get("message") or ""
    ).strip() or None

    if action == "abort":
        return None, None, True

    if action in ("reject", "pivot"):
        rollback = submission.get("rollback_to_stage")
        if rollback is not None:
            try:
                target: int = int(rollback)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="invalid rollback_to_stage"
                ) from exc
        else:
            target = gate_stage
        return str(target), guidance, False

    if action in ("edit", "inject", "collaborate"):
        return str(gate_stage), guidance, False

    # approve / skip / 未知动作 → 从下一 stage 续跑
    return str(gate_stage + 1), guidance, False


def _reply_to_gate(
    db: Session,
    session: ResearchSession,
    paused_turn: ResearchTurn,
    request: ResearchTurnStartRequest,
) -> ResearchTurnStartResponse:
    """门控回复：把 submission 翻译成 ``from_stage``，**新建一轮 turn** 从断点续跑。

    新链路下命中门控已结束任务（``paused_turn`` 落 PAUSED_GATE、无 worker 阻塞），
    故这里不再 PUBLISH 唤醒，而是：锁定门控消息 → 记录 gate_reply → 依动作算出
    起始 stage（approve→N+1 / reject→回滚 / edit→带 guidance 重跑 N）→ 建新 turn
    并入队。复用同一 ``run_dir``，前置 stage 产物直接复用。
    """
    if not (request.respond_to_message_id and request.submission):
        raise HTTPException(
            status_code=400,
            detail="session waiting for input — respond_to_message_id and submission are required",
        )

    locked = db.get(ResearchMessage, request.respond_to_message_id)
    if not locked or locked.session_id != session.id or locked.turn_id != paused_turn.id:
        raise HTTPException(status_code=404, detail="respond_to_message not found")
    if locked.payload_locked:
        raise HTTPException(
            status_code=409,
            detail="this gate has already been responded to",
        )

    gate_stage = int(locked.stage or 0)
    from_stage, guidance, abort = _compute_gate_resume(gate_stage, request.submission)

    # 幂等锁：payload_locked=True 之后不能再改
    locked.payload_locked = True
    locked.payload_locked_at = datetime.now(UTC)
    locked.payload_submission = request.submission
    flag_modified(locked, "payload_submission")
    db.add(locked)

    # user 侧展示一条 gate submission 消息（便于历史看回填内容）
    user_msg = ResearchMessage(
        session_id=session.id,
        turn_id=paused_turn.id,
        role=ResearchMessageRole.USER,
        content=str(request.submission.get("message") or request.submission.get("action") or ""),
        turn_status=ResearchTurnStatus.COMPLETED.value,
        event_key=f"gate-reply:{locked.id}",
        payload={"kind": "gate_reply", "submission": request.submission},
    )
    db.add(user_msg)

    # abort：不建新 turn，门控轮与会话一并置 CANCELLED（退场旧 paused 轮，
    # 否则会话虽终态、却残留 PAUSED_GATE 轮，令后续「再跑一轮」被误判为门控回复）。
    if abort:
        paused_turn.status = ResearchTurnStatus.CANCELLED.value
        db.add(paused_turn)
        session.status = ResearchSessionStatus.CANCELLED.value
        session.ended_time = datetime.now(UTC)
        session.updated_time = datetime.now(UTC)
        db.add(session)
        db.commit()
        db.refresh(session)
        db.refresh(paused_turn)
        db.refresh(user_msg)
        db.refresh(locked)
        return ResearchTurnStartResponse(
            session=ResearchSessionItem.model_validate(session),
            turn=ResearchTurnItem.model_validate(paused_turn),
            user_message=ResearchMessageItem.model_validate(user_msg),
            assistant_message=ResearchMessageItem.model_validate(locked),
            locked_message=ResearchMessageItem.model_validate(locked),
        )

    # 建新一轮 turn：from_stage 续跑，user_input 作为 guidance 由 worker 注入。
    # celery_task_id 预生成、与 turn 同事务落库（见 _create_turn_row 说明）。
    turn = _create_turn_row(
        session,
        request,
        from_stage=from_stage,
        to_stage=None,
        user_input=guidance,
        respond_to_message_id=locked.id,
    )
    db.add(turn)

    # approve/skip（往前走，from_stage > gate_stage）时给门控 stage 补一条
    # stage_transition=done：命中门控时该 stage 只发过 running + 补的 blocked_approval
    # （见 streaming.persist_gate_pause），ARC 从下一 stage 续跑不会回头标它 done，
    # 故这里补上，让 /state 回放把它显示为 done。reject/edit（回退重跑本 stage）不补
    # ——续跑 turn 会自己重发该 stage 的 running/done。
    try:
        resume_stage = int(from_stage) if from_stage is not None else gate_stage
    except (TypeError, ValueError):
        resume_stage = gate_stage
    if gate_stage > 0 and resume_stage > gate_stage:
        stage_name = str((locked.payload or {}).get("stage_name") or "")
        display = stage_name or stage_display_name(gate_stage)
        db.add(ResearchMessage(
            session_id=session.id,
            turn_id=paused_turn.id,
            role=ResearchMessageRole.SYSTEM,
            content=f"[stage-{gate_stage}] {display} done",
            event_type="stage_transition",
            event_key=f"stage-{gate_stage}:done",
            stage=gate_stage,
            turn_status=ResearchTurnStatus.COMPLETED.value,
            payload={
                "kind": "stage_progress",
                "stage": gate_stage,
                "name": display,
                "status": "done",
            },
        ))

    paused_turn.status = ResearchTurnStatus.COMPLETED.value
    db.add(paused_turn)

    # flush 让 turn 先落库（见 _create_turn_row：满足 message/session 的 FK）
    db.flush()

    assistant_message = _new_assistant_message(session.id, turn.id)
    db.add(assistant_message)
    _advance_session_pointer(session, turn.id, request)
    db.add(session)

    db.commit()
    db.refresh(session)
    db.refresh(turn)
    db.refresh(assistant_message)
    db.refresh(user_msg)
    db.refresh(locked)

    enqueue_research_turn(turn.session_id, turn.id, turn.celery_task_id)

    return ResearchTurnStartResponse(
        session=ResearchSessionItem.model_validate(session),
        turn=ResearchTurnItem.model_validate(turn),
        user_message=ResearchMessageItem.model_validate(user_msg),
        assistant_message=ResearchMessageItem.model_validate(assistant_message),
        locked_message=ResearchMessageItem.model_validate(locked),
    )


def start_pipeline_turn(
    db: Session,
    session: ResearchSession,
    *,
    from_stage: str | None,
    guidance: str | None,
) -> uuid.UUID | None:
    """非门控起跑：建一轮 pipeline turn 从 ``from_stage`` 续跑并入队。

    供 collab agent 的 ``run_pipeline`` 在**无门控**时调用（门控中仍走
    :func:`_reply_to_gate`）。与 :func:`start_turn` 路径 1 同逻辑，但接收已解析的
    ``session``、不做 HTTP 鉴权。session 正在跑（RUNNING）或限流时跳过并返回 None。

    Args:
        db: 数据库会话。
        session: 目标会话（调用方已鉴权 / 加载）。
        from_stage: 起始 stage 号（字符串，1-based）；None 表示从头。
        guidance: 注入到该 stage 的文字反馈（worker 落成 hitl_guidance.md）。

    Returns:
        新建 turn 的 id；若因 RUNNING / 限流跳过则 None。
    """
    if session.status == ResearchSessionStatus.RUNNING.value:
        return None
    if not check_research_rate_limit(session.id):
        return None

    request = ResearchTurnStartRequest()
    turn = _create_turn_row(
        session,
        request,
        from_stage=from_stage,
        to_stage=None,
        user_input=guidance,
    )
    db.add(turn)
    db.flush()
    assistant_message = _new_assistant_message(session.id, turn.id)
    db.add(assistant_message)
    _advance_session_pointer(session, turn.id, request)
    db.add(session)

    db.commit()
    db.refresh(turn)

    enqueue_research_turn(turn.session_id, turn.id, turn.celery_task_id)
    return turn.id


def start_turn(
    db: Session,
    session_id: uuid.UUID,
    request: ResearchTurnStartRequest,
    user: models.User,
) -> ResearchTurnStartResponse:
    """触发新一轮或响应门控。

    四种路径：

    1. **首轮 / 停止后继续**：session PENDING/COMPLETED/FAILED/CANCELLED
       → 建新 turn + enqueue Celery；
    2. **门控回复**（存在 PAUSED_GATE turn，此时 session=PAUSED）→ 依 submission
       算出 from_stage，**新建一轮 turn** 从断点续跑（不复用旧 turn）；
    3. **纯 RUNNING**（没在等门控）→ 409，要用户先 stop；
    4. **状态异常**（如 session 已删除）→ 404。

    时序不变量：先 **commit** 落库，再产生 Redis 副作用与 Celery 入队——否则
    commit 失败会留下已入队但无行的孤儿任务。
    """
    session = _get_session(db, session_id, user)

    # 协作进行中守卫：存在 COLLABORATING turn 时（gate 暂停期间的「人+AI 改产物」
    # 子会话在跑），禁止 gate 回复 / 新建轮，避免与协作容器并发改同一 run_dir。
    # 用户须先停止协作（POST /turns/{tid}/stop）再操作门控。
    collab = _find_turn_by_status(
        db, session.id, ResearchTurnStatus.COLLABORATING.value
    )
    if collab is not None:
        raise HTTPException(
            status_code=409,
            detail="a collaboration turn is running; end it before responding to the gate",
        )

    # 路径 2：门控回复 —— 存在 PAUSED_GATE turn（命中门控已结束任务、session=PAUSED）
    # 即表示有待回复的门控，走恢复分支：新建一轮 turn 从 from_stage 续跑。
    paused_turn = _find_turn_by_status(
        db, session.id, ResearchTurnStatus.PAUSED_GATE.value
    )
    if paused_turn is not None:
        return _reply_to_gate(db, session, paused_turn, request)

    # 路径 3：会话 RUNNING 且无待回复门控 = 有轮正在跑，禁止再触发。
    if session.status == ResearchSessionStatus.RUNNING.value:
        raise HTTPException(
            status_code=409,
            detail="a turn is already running; stop it first",
        )

    # 路径 1：建新 turn
    if not check_research_rate_limit(session.id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    turn = _create_turn_row(session, request)
    db.add(turn)
    # 先 flush 让 turn 落库满足 message/session 的 FK（见 _create_turn_row）。
    db.flush()
    user_message, assistant_message = _persist_turn_messages(db, session, turn, request)
    _advance_session_pointer(session, turn.id, request)
    db.add(session)

    # commit 落地 → 然后再入队 Celery
    db.commit()
    db.refresh(session)
    db.refresh(turn)
    db.refresh(assistant_message)
    db.refresh(user_message)

    enqueue_research_turn(turn.session_id, turn.id, turn.celery_task_id)

    return ResearchTurnStartResponse(
        session=ResearchSessionItem.model_validate(session),
        turn=ResearchTurnItem.model_validate(turn),
        user_message=ResearchMessageItem.model_validate(user_message),
        assistant_message=ResearchMessageItem.model_validate(assistant_message),
        locked_message=None,
    )


def stop_turn(
    db: Session,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    user: models.User,
) -> ResearchTurnItem:
    """**同步**停止指定轮次：abort Celery 任务 + SIGKILL 容器 + 原子写终态。

    同一接口停 pipeline 轮与协作轮，按 turn 状态分派：

    - ``RUNNING``（pipeline 轮）→ kill 研究容器 + 落 turn/session 双 ``CANCELLED``；
    - ``COLLABORATING``（协作轮）→ kill 协作容器 + **只**落 turn ``CANCELLED``（协作是
      叠加层，不动 session：停掉后会话仍暂停在原门控 / pending，门控表单重新可操作）。

    与演化 ``stop_task`` 同款：返回前任务已落 ``CANCELLED``，前端立刻看到终态，无需
    再轮询。单机部署下 API 进程与容器同一 docker 主机，故可直接 kill。

    幂等：非上述活跃态（已终态 / 暂停等待门控）直接回读当前状态。worker 侧 finally
    再次 finalize 时会因终态守卫短路，不会覆写 CANCELLED。
    """
    from celery.contrib.abortable import AbortableAsyncResult

    from app.core.celery import celery_app
    from app.services.container_service import (
        kill_container_by_name,
        research_collab_container_name,
        research_container_name,
    )
    from app.tasks.research_runner import finalize_turn
    from app.tasks.research_runner.collab import _finalize_collab

    session, turn = _get_session_and_turn(db, session_id, turn_id, user)
    is_pipeline = turn.status == ResearchTurnStatus.RUNNING.value
    is_collab = turn.status == ResearchTurnStatus.COLLABORATING.value
    if not (is_pipeline or is_collab):
        return ResearchTurnItem.model_validate(turn)

    # 1. abort Celery 任务（worker 若在边界会自行退出；容器已被 kill 时多为兜底）
    if turn.celery_task_id:
        try:
            AbortableAsyncResult(turn.celery_task_id, app=celery_app).abort()
        except Exception:
            pass

    # 2. 立即 SIGKILL 对应容器（亚秒级见效；不存在则 no-op）
    container_name = (
        research_container_name if is_pipeline else research_collab_container_name
    )
    kill_container_by_name(container_name(str(turn.id)))

    # 3. 原子写终态 CANCELLED（worker finally 再调 finalize 会被终态守卫短路）。
    #    pipeline 轮连带 session 一起 CANCELLED；协作轮只动 turn（叠加层，不碰 session）。
    if is_pipeline:
        finalize_turn(
            session_id=session.id,
            turn_id=turn.id,
            turn_status=ResearchTurnStatus.CANCELLED,
            session_status=ResearchSessionStatus.CANCELLED,
        )
    else:
        _finalize_collab(turn_id=turn.id, turn_status=ResearchTurnStatus.CANCELLED)
    db.refresh(turn)
    return ResearchTurnItem.model_validate(turn)


def retry_turn(
    db: Session,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    request: ResearchTurnRetryRequest,
    user: models.User,
) -> ResearchTurnStartResponse:
    """重跑失败或已停止的轮次，复用 ``turn_id``。"""
    session, turn = _get_session_and_turn(db, session_id, turn_id, user)
    if turn.status not in (
        ResearchTurnStatus.FAILED.value,
        ResearchTurnStatus.CANCELLED.value,
    ):
        raise HTTPException(
            status_code=409,
            detail="only failed/cancelled turns can be retried",
        )
    # 协作进行中守卫（与 start_turn 一致）：存在 COLLABORATING turn 时禁止重试，
    # 避免重试的 pipeline 容器与协作容器并发写同一 run_dir。
    collab = _find_turn_by_status(
        db, session.id, ResearchTurnStatus.COLLABORATING.value
    )
    if collab is not None:
        raise HTTPException(
            status_code=409,
            detail="a collaboration turn is running; end it before retrying",
        )
    if not check_research_rate_limit(session.id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    # 重置 turn 状态
    turn.status = ResearchTurnStatus.RUNNING.value
    turn.error = None
    turn.ended_at = None
    # 刷新 celery_task_id：复用旧 ID 会与 celery 已存在的 result（含旧 abort 状态）
    # 相撞，故每次重试都换一个新 ID，同事务落库后再据它 apply_async。
    turn.celery_task_id = str(uuid.uuid4())
    if request.provider_id:
        turn.provider_id = request.provider_id
    if request.model_name:
        turn.model_name = request.model_name
    if request.mode:
        turn.mode = request.mode.value
    turn.updated_time = datetime.now(UTC)
    db.add(turn)

    # 找到本轮 user message（缺就补一条）。retry 复用同一 turn_id，而
    # (session_id, turn_id, role, event_key) 唯一，故先查再补，避免二次重试撞约束。
    # content 用本轮触发输入 user_input 兜底，再退到会话 topic / 固定文案。
    user_message = db.exec(
        select(ResearchMessage).where(
            ResearchMessage.session_id == session.id,
            ResearchMessage.turn_id == turn.id,
            ResearchMessage.role == ResearchMessageRole.USER,
            ResearchMessage.event_key == f"user:{turn.id}",
        )
    ).first()
    if user_message is None:
        content = (turn.user_input or "").strip() or "retry run"
        user_message = ResearchMessage(
            session_id=session.id,
            turn_id=turn.id,
            role=ResearchMessageRole.USER,
            content=content,
            turn_status=ResearchTurnStatus.COMPLETED.value,
            event_key=f"user:{turn.id}",
        )
        db.add(user_message)

    # 找到本轮 assistant message，重置为 running（缺就补一条）
    assistant = db.exec(
        select(ResearchMessage).where(
            ResearchMessage.session_id == session.id,
            ResearchMessage.turn_id == turn.id,
            ResearchMessage.role == ResearchMessageRole.ASSISTANT,
            ResearchMessage.event_key == f"assistant:{turn.id}",
        )
    ).first()
    if assistant is None:
        assistant = _new_assistant_message(session.id, turn.id)
    else:
        assistant.content = ""
        assistant.error = None
        assistant.turn_status = ResearchTurnStatus.RUNNING.value
    db.add(assistant)

    # session 指针（复用 start_turn 的 helper 保证语义一致）
    _advance_session_pointer(session, turn.id, request)
    db.add(session)
    db.commit()
    db.refresh(turn)
    db.refresh(assistant)
    db.refresh(session)
    db.refresh(user_message)

    # commit 落库后再产生 Redis / broker 副作用（提交失败则不误清 gate reply）：
    # 清一下已锁定 gate reply（重试意味着重新走一遍），再据新 celery_task_id 派发。
    if turn.respond_to_message_id:
        clear_research_gate_reply(turn.session_id, turn.respond_to_message_id)
    # retry 复用同一 turn_id → 同一 Redis stream key。旧流里残留着上一轮的
    # done/error 终止帧；前端从 last_id=0-0 重放会立刻撞上旧 done 而秒关流，
    # 永远等不到新一轮事件。故派发新任务前先删旧流，让新一轮从空流开始，
    # 前端重放拿到的就是干净的新数据。
    delete_research_stream(turn.session_id, turn.id)
    enqueue_research_turn(turn.session_id, turn.id, turn.celery_task_id)

    return ResearchTurnStartResponse(
        session=ResearchSessionItem.model_validate(session),
        turn=ResearchTurnItem.model_validate(turn),
        user_message=ResearchMessageItem.model_validate(user_message),
        assistant_message=ResearchMessageItem.model_validate(assistant),
        locked_message=None,
    )


def get_turn(
    db: Session,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    user: models.User,
) -> ResearchTurnItem:
    """回读单轮状态。"""
    _session, turn = _get_session_and_turn(db, session_id, turn_id, user)
    return ResearchTurnItem.model_validate(turn)


def get_stream_context(
    db: Session, session_id: uuid.UUID, turn_id: uuid.UUID, user: models.User
) -> ResearchTurn:
    """SSE 端点校验：返回 turn，供路由读 status 做已终态短路。"""
    _session, turn = _get_session_and_turn(db, session_id, turn_id, user)
    return turn


def list_session_turns(
    db: Session,
    session_id: uuid.UUID,
    user: models.User,
    *,
    cursor: str | None,
    limit: int,
) -> ResearchTurnListResponse:
    """返回会话下的所有 turn，倒序（新的在前）游标分页。

    cursor = 上一页最后一条的 ``created_time`` ISO；首次不传。
    """
    session = _get_session(db, session_id, user)
    stmt = (
        select(ResearchTurn)
        .where(ResearchTurn.session_id == session.id)
    )
    if cursor:
        cursor_ts = _parse_cursor(cursor)
        stmt = stmt.where(ResearchTurn.created_time < cursor_ts)
    page = max(1, min(limit, 200))
    rows = db.exec(
        stmt.order_by(ResearchTurn.created_time.desc(), ResearchTurn.id.desc())
        .limit(page + 1)
    ).all()
    has_more = len(rows) > page
    items = rows[:page]
    next_cursor = items[-1].created_time.isoformat() if has_more and items else None
    return ResearchTurnListResponse(
        items=[ResearchTurnItem.model_validate(t) for t in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )
