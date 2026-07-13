"""消息（Message）子域：单轮历史消息分页 + Stage 引导注入。

- ``list_turn_messages``：正序游标分页返回单轮全部消息（含 log/stage/evolution
  等系统事件），供刷新恢复与增量拉取；
- ``inject_stage_guidance``：对应 ARC CLI ``researchclaw guide``，把引导文本落成
  ``run_dir/stage-NN/hitl_guidance.md``，ARC 下次跑到该 stage 时读入 prompt。

两者都以消息表 / guidance 文件为中心，故合于一模块。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from loguru import logger
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, or_, select, tuple_

from app import models
from app.models.research import (
    ResearchMessage,
    ResearchMessageRole,
    ResearchTurnStatus,
)
from app.schemas.research import (
    ResearchMessageItem,
    ResearchMessageListResponse,
    ResearchSessionMessagesResponse,
    ResearchStageGuideRequest,
    ResearchStageGuideResponse,
)
from app.tasks.research_runner import (
    is_valid_stage,
    stage_display_name,
    write_stage_guidance,
)

from ._common import _get_session, _get_session_and_turn, _parse_cursor


def inject_stage_guidance(
    db: Session,
    session_id: uuid.UUID,
    stage_num: int,
    request: ResearchStageGuideRequest,
    user: models.User,
) -> ResearchStageGuideResponse:
    """为某个 stage 注入引导文本。

    等价于 ARC CLI ``researchclaw guide artifacts/rc-xxx --stage N -m "..."``：
    落一份 markdown 到 ``run_dir/stage-NN/hitl_guidance.md``；ARC 下次跑到
    该 stage 时会读文件把 guidance 拼进 prompt。同时 :class:`HITLStore`
    也会持久化一份便于 attach / status 查看。

    可以在 stage **未开始跑之前**调用（预注入）——用户完全可以在建 session
    的同时就把关键 stage 的 guidance 备好。

    幂等：同一 stage 再调一次会 **覆盖** 之前的引导内容。
    """
    session = _get_session(db, session_id, user)
    if not session.run_dir:
        raise HTTPException(
            status_code=409,
            detail="session has no run_dir yet — trigger a turn first",
        )
    if not is_valid_stage(stage_num):
        raise HTTPException(
            status_code=400,
            detail=f"invalid stage number: {stage_num}",
        )

    run_dir = Path(session.run_dir)
    message = request.message.strip()

    # 落 guidance 文件 + HITLStore（复用 write_stage_guidance）
    try:
        guidance_path = write_stage_guidance(run_dir, stage_num, message)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"failed to write guidance file: {exc}",
        ) from exc

    # 落一条 system message 便于历史回看（覆盖同 stage 之前的引导）
    stage_name = stage_display_name(stage_num)
    try:
        active_turn_id = session.active_turn_id
        if active_turn_id is not None:
            existing = db.exec(
                select(ResearchMessage)
                .where(ResearchMessage.session_id == session.id)
                .where(ResearchMessage.turn_id == active_turn_id)
                .where(ResearchMessage.event_key == f"guide:{stage_num}")
            ).first()
            payload = {
                "kind": "guidance",
                "stage": stage_num,
                "stage_name": stage_name,
                "message": message,
            }
            if existing:
                existing.content = message
                existing.payload = payload
                flag_modified(existing, "payload")
                existing.updated_time = datetime.now(UTC)
                db.add(existing)
            else:
                db.add(ResearchMessage(
                    session_id=session.id,
                    turn_id=active_turn_id,
                    role=ResearchMessageRole.SYSTEM,
                    content=message,
                    payload=payload,
                    event_type="guidance",
                    event_key=f"guide:{stage_num}",
                    stage=stage_num,
                    turn_status=ResearchTurnStatus.RUNNING.value,
                ))
            db.commit()
    except Exception:
        logger.opt(exception=True).debug("guidance message persist skipped")
        db.rollback()

    return ResearchStageGuideResponse(
        session_id=session.id,
        stage=stage_num,
        length=len(message),
        guidance_path=str(guidance_path.resolve()),
    )


def list_turn_messages(
    db: Session,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    user: models.User,
    *,
    cursor: str | None,
    limit: int,
    event_type: list[str] | None,
    role: ResearchMessageRole | None,
) -> ResearchMessageListResponse:
    """返回单轮消息（含 log/stage/evolution 等所有系统事件），正序游标分页。

    - ``cursor``：ISO8601 时间戳；只返回 ``created_time > cursor`` 的行。
      首次不传 → 从头开始。
    - ``limit``：单页大小；后端硬上限 500 防误刷。
    - ``event_type``：可选类型过滤（如 ``["log", "stage_transition"]``），
      为空 → 不过滤。
    - ``role``：可选角色过滤。

    Raises:
        HTTPException 404: session/turn 不存在或不属于该用户。
        HTTPException 400: cursor 格式非法。
    """
    session, turn = _get_session_and_turn(db, session_id, turn_id, user)

    stmt = (
        select(ResearchMessage)
        .where(ResearchMessage.session_id == session.id)
        .where(ResearchMessage.turn_id == turn.id)
    )
    if cursor:
        cursor_ts = _parse_cursor(cursor)
        stmt = stmt.where(ResearchMessage.created_time > cursor_ts)
    if event_type:
        stmt = stmt.where(ResearchMessage.event_type.in_(event_type))
    if role is not None:
        stmt = stmt.where(ResearchMessage.role == role)

    # 多取一条判断 has_more；硬上限 500 防误刷。
    page = max(1, min(limit, 500))
    rows = db.exec(
        stmt.order_by(ResearchMessage.created_time.asc(), ResearchMessage.id.asc())
        .limit(page + 1)
    ).all()
    has_more = len(rows) > page
    items = rows[:page]
    next_cursor: str | None = None
    if has_more and items:
        next_cursor = items[-1].created_time.isoformat()
    return ResearchMessageListResponse(
        items=[ResearchMessageItem.model_validate(m) for m in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


def list_session_messages(
    db: Session,
    session_id: uuid.UUID,
    user: models.User,
    *,
    before: uuid.UUID | None,
    limit: int,
    event_type: list[str] | None,
    exclude_event_type: list[str] | None,
) -> ResearchSessionMessagesResponse:
    """会话级消息分页（跨所有 turn），``before``/倒序游标，返回时升序。

    与 ``sessions.get_session_detail`` 的附带消息同款翻页语义，但独立成端点、
    额外支持类型过滤——让消息列表与日志面板各调一次、各自分页，避免共享一页
    数据时互相饥饿。

    - ``before``：消息 id 游标；只返回严格早于该消息（``created_time, id`` 复合
      键比较）的行。首次不传 → 从最新一页开始。
    - ``limit``：单页大小；后端硬上限 100。
    - ``event_type``：**白名单**，只保留这些类型（如日志面板传 ``["log"]``）；
      空则不限。
    - ``exclude_event_type``：**黑名单**，剔除这些类型（如消息列表传 ``["log"]``
      排除日志）。用黑名单而非白名单是因为 user/assistant 消息 ``event_type`` 为
      NULL，白名单会漏掉它们。二者可叠加，一般只用其一。

    Raises:
        HTTPException 404: session 不存在或不属于该用户。
    """
    session = _get_session(db, session_id, user)

    stmt = select(ResearchMessage).where(
        ResearchMessage.session_id == session.id
    )
    if event_type:
        stmt = stmt.where(ResearchMessage.event_type.in_(event_type))
    if exclude_event_type:
        # 剔除黑名单类型，但显式保留 NULL event_type（user/assistant 对话消息）。
        # SQL 三值逻辑下 `NULL NOT IN (...)` 求值为 NULL 而非 TRUE，会被 WHERE 连同
        # 一起过滤掉，故必须 OR 上 IS NULL 才能把对话消息留下。
        stmt = stmt.where(
            or_(
                ResearchMessage.event_type.is_(None),
                ResearchMessage.event_type.notin_(exclude_event_type),
            )
        )
    if before is not None:
        anchor = db.get(ResearchMessage, before)
        if anchor and anchor.session_id == session.id:
            # 复合游标 (created_time, id)：日志批量写入常同一微秒，仅按
            # created_time 严格小于会漏掉与锚点同时间戳的行，带 id 次级键杜绝。
            stmt = stmt.where(
                tuple_(ResearchMessage.created_time, ResearchMessage.id)
                < (anchor.created_time, anchor.id)
            )

    page = max(1, min(limit, 2000))
    rows = db.exec(
        stmt.order_by(
            ResearchMessage.created_time.desc(), ResearchMessage.id.desc()
        ).limit(page + 1)
    ).all()
    has_more = len(rows) > page
    # 倒序取一页后反转成升序（最旧在前），与详情端点一致，前端拼接逻辑不变。
    msg_rows = list(reversed(rows[:page]))
    return ResearchSessionMessagesResponse(
        messages=[ResearchMessageItem.model_validate(m) for m in msg_rows],
        has_more=has_more,
    )
