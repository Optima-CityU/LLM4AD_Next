"""协作（Collaborate Agent）子域：常驻对话 agent 的一轮发起。

常驻输入框：只要**流水线没在跑**（session 非 ``running``）就能发消息。agent 是与门控
按钮**平行的独立通道**——用 AgentScope ReAct 在隔离容器内答疑 + 读写 ``stage-NN/``
产物。每条消息新建一个 ``COLLABORATING`` turn、起短命容器跑一轮、跑完落终态，
**不推进 pipeline、不改 session 主状态、不驱动门控动作**。

并发守卫：同一 session 同时只允许一个 ``COLLABORATING`` turn（已在跑时 409）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from loguru import logger
from sqlmodel import Session, select

from app import models
from app.models.research import (
    ResearchMessage,
    ResearchMessageRole,
    ResearchSessionStatus,
    ResearchTurn,
    ResearchTurnStatus,
)
from app.schemas.research import (
    ResearchCollabStartRequest,
    ResearchCollabStartResponse,
    ResearchMessageItem,
    ResearchSessionItem,
    ResearchTurnItem,
)
from app.tasks.research_runner import enqueue_collab_turn

from ._common import _find_turn_by_status, _get_session


def start_collab_turn(
    db: Session,
    session_id: uuid.UUID,
    request: ResearchCollabStartRequest,
    user: models.User,
) -> ResearchCollabStartResponse:
    """发起一轮协作：新建 COLLABORATING turn 并入队（见模块 docstring）。

    前置：session 非 ``running``（否则 409）。并发：已有 COLLABORATING turn 在跑
    → 409（同一时刻只允许一个 agent 轮）。

    并发不变量：对 session 行加 ``FOR UPDATE`` 后再读守卫并建轮，与
    :func:`start_turn` / :func:`retry_turn` 共用同一把 session 行锁，杜绝协作轮
    与 pipeline 轮同时穿过各自守卫（TOCTOU）。锁随本函数末尾 commit 释放。
    """
    session = _get_session(db, session_id, user, for_update=True)

    if session.status == ResearchSessionStatus.RUNNING.value:
        raise HTTPException(
            status_code=409,
            detail="pipeline is running; wait for it to pause or finish before collaborating",
        )

    if (
        _find_turn_by_status(
            db, session.id, ResearchTurnStatus.COLLABORATING.value
        )
        is not None
    ):
        raise HTTPException(
            status_code=409,
            detail="a collaboration turn is already running; wait for it to finish",
        )

    # 解析协作 stage（供 agent 上下文 + 会话态目录分层）：请求显式 > 当前门控
    # form 消息的 stage > session.active_stage > 0（pending 无产物时）。
    stage_num = request.stage
    if stage_num is None:
        paused_turn = _find_turn_by_status(
            db, session.id, ResearchTurnStatus.PAUSED_GATE.value
        )
        gate_msg = None
        if paused_turn is not None:
            gate_msg = db.exec(
                select(ResearchMessage)
                .where(ResearchMessage.turn_id == paused_turn.id)
                .where(ResearchMessage.event_type == "waiting_for_input")
                .order_by(ResearchMessage.created_time.desc())
            ).first()
        if gate_msg and gate_msg.stage:
            stage_num = int(gate_msg.stage)
        elif session.active_stage:
            stage_num = int(session.active_stage)
        else:
            stage_num = 0

    # 新建 COLLABORATING turn：from_stage 记 stage 号、user_input 记协作消息。
    # celery_task_id 预生成、与 turn 同事务落库（同 start_turn 契约）。
    turn = ResearchTurn(
        id=uuid.uuid4(),
        session_id=session.id,
        status=ResearchTurnStatus.COLLABORATING.value,
        celery_task_id=str(uuid.uuid4()),
        provider_id=session.provider_id,
        model_name=session.model_name,
        mode=session.mode,
        from_stage=str(stage_num),
        user_input=request.message,
    )
    db.add(turn)
    db.flush()

    user_msg = ResearchMessage(
        session_id=session.id,
        turn_id=turn.id,
        role=ResearchMessageRole.USER,
        content=request.message,
        turn_status=ResearchTurnStatus.COLLABORATING.value,
        event_type="collab_message",
        event_key=f"collab-user:{turn.id}",
        stage=stage_num or None,
        payload={"kind": "collab_turn", "stage": stage_num, "text": request.message},
    )
    db.add(user_msg)

    # 不改 session.status：协作是叠加层，会话主状态保持不变（pending/paused/终态
    # 起手皆可）。只更新 updated_time 让列表页排序把它顶上来。
    session.updated_time = datetime.now(UTC)
    db.add(session)

    db.commit()
    db.refresh(session)
    db.refresh(turn)
    db.refresh(user_msg)

    # 入队失败补偿：turn 已落库为 COLLABORATING，若 enqueue 抛异常（broker 不可达等），
    # 这一轮永远没 worker 认领、卡在 COLLABORATING——而并发守卫（上面的 409）据此把
    # **后续所有**协作请求也一并拒掉，直到 worker 重启 sweep。故此处补偿：把本 turn
    # 就地标 FAILED（协作是叠加层，不动 session 主状态），再向上抛 503 让前端可重试。
    try:
        enqueue_collab_turn(session.id, turn.id, turn.celery_task_id)
    except Exception as exc:
        logger.opt(exception=True).error(
            f"enqueue_collab_turn failed turn={turn.id}; marking FAILED"
        )
        try:
            turn.status = ResearchTurnStatus.FAILED.value
            turn.error = "failed to enqueue collaboration task"
            turn.ended_at = datetime.now(UTC)
            turn.updated_time = datetime.now(UTC)
            db.add(turn)
            db.commit()
        except Exception:
            db.rollback()
            logger.opt(exception=True).warning(
                f"compensating FAILED write failed turn={turn.id}"
            )
        raise HTTPException(
            status_code=503,
            detail="failed to enqueue collaboration task; please retry",
        ) from exc

    return ResearchCollabStartResponse(
        session=ResearchSessionItem.model_validate(session),
        turn=ResearchTurnItem.model_validate(turn),
        user_message=ResearchMessageItem.model_validate(user_msg),
    )
