"""消息（Message）与日志（Log）子域：分页查询 + Stage 引导注入。

按数据源拆成两个互不掺和的查询：

- ``list_messages``：查 ``research_message`` 表（对话 + stage/artifact/guidance
  等系统事件，**不含 log**），``turn_id`` 可选（传=单轮，不传=跨会话），
  ``order`` 决定翻页方向（``desc`` 历史翻页 / ``asc`` SSE 回放）；
- ``list_logs``：查独立的 ``research_log`` 表，同样支持 ``turn_id`` / ``order``；
- ``inject_stage_guidance``：对应 ARC CLI ``researchclaw guide``，把引导文本落成
  ``run_dir/stage-NN/hitl_guidance.md``，ARC 下次跑到该 stage 时读入 prompt。

三者都以消息/日志表 / guidance 文件为中心，故合于一模块。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from loguru import logger
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, func, select, tuple_

from app import models
from app.models.research import (
    ResearchLog,
    ResearchMessage,
    ResearchMessageRole,
    ResearchTurnStatus,
)
from app.schemas.research import (
    ResearchLogItem,
    ResearchLogPageResponse,
    ResearchMessageItem,
    ResearchMessageListResponse,
    ResearchStageGuideRequest,
    ResearchStageGuideResponse,
)
from app.tasks.research_runner import (
    is_valid_stage,
    stage_display_name,
    write_stage_guidance,
)

from ._common import (
    _encode_forward_cursor,
    _get_session,
    _get_session_and_turn,
    _parse_forward_cursor,
)

# 消息 / 日志分页单页硬上限，防误刷。
_MESSAGE_LIMIT_MAX = 500
_LOG_LIMIT_MAX = 2000


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
                # 补 seq：guidance 走 REST 同步落库、无 sink 计数器可共享，取该 turn
                # 当前 max(seq)+1。否则默认 seq=0，在 created_time 撞车时会被 tiebreaker
                # 排到该时刻所有事件最前，造成引导消息乱序显示在最新状态之前。
                max_seq = db.exec(
                    select(func.max(ResearchMessage.seq))
                    .where(ResearchMessage.session_id == session.id)
                    .where(ResearchMessage.turn_id == active_turn_id)
                ).one()
                next_seq = (max_seq or 0) + 1
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
                    seq=next_seq,
                ))
            db.commit()
    except Exception:
        # guidance 文件已成功落盘（真正驱动 ARC 的是那份 .md），这条 system message
        # 仅用于历史回看，失败不阻断主流程；但它意味着前端历史缺一条，属可观测异常，
        # 用 warning 而非 debug，便于在日志里发现。
        logger.opt(exception=True).warning("guidance message persist skipped")
        db.rollback()

    return ResearchStageGuideResponse(
        session_id=session.id,
        stage=stage_num,
        length=len(message),
        guidance_path=str(guidance_path.resolve()),
    )


def _apply_cursor_and_order(stmt, model, order: str, cursor: str | None):
    """给查询套上「复合游标 + 排序方向」。

    统一 ``messages`` / ``logs`` 两表的翻页逻辑：都以 ``(created_time, seq, id)``
    三元组做全序游标——``seq`` 是 per-turn 递增（跨 turn 不唯一），且 Windows 下
    ``created_time`` 精度粗、同批 flush 常撞时间戳，故必须追加全局唯一的 ``id``
    作最终 tiebreaker，否则漏行 / 提前 ``has_more=False``。

    - ``order == "asc"``：``ORDER BY ... ASC``，游标取严格 ``>``（SSE 回放，越翻越新）；
    - ``order == "desc"``：``ORDER BY ... DESC``，游标取严格 ``<``（历史翻页，越翻越旧）。

    兼容旧版纯 ISO 游标（无 ``|`` 分隔符）：退化为仅时间戳比较，切换期短暂、不丢数据。
    """
    triple = tuple_(model.created_time, model.seq, model.id)
    if cursor:
        cur_ts, cur_seq, cur_id = _parse_forward_cursor(cursor)
        if cur_seq is not None and cur_id is not None:
            if order == "asc":
                stmt = stmt.where(triple > (cur_ts, cur_seq, cur_id))
            else:
                stmt = stmt.where(triple < (cur_ts, cur_seq, cur_id))
        elif order == "asc":
            stmt = stmt.where(model.created_time > cur_ts)
        else:
            stmt = stmt.where(model.created_time < cur_ts)
    if order == "asc":
        stmt = stmt.order_by(
            model.created_time.asc(), model.seq.asc(), model.id.asc()
        )
    else:
        stmt = stmt.order_by(
            model.created_time.desc(), model.seq.desc(), model.id.desc()
        )
    return stmt


def _finalize_page(rows, page: int, order: str):
    """把「多取一条」的查询结果裁成一页，统一升序返回。

    ``rows`` 已按 ``order`` 方向排序（``desc`` 最新在前 / ``asc`` 最旧在前）。

    - ``next_cursor`` 取**查询顺序下的最后一条**——即翻页前进边界（``desc`` 指向
      这批最旧那条、继续往更旧翻；``asc`` 指向最新那条、继续往更新翻）。故它必须
      在反转之前取。
    - 返回列表**恒定升序**（最旧在前）：``order`` 只决定「取更新的一页还是更旧的
      一页」及游标方向，**不改变单页内部的时间顺序**，前端拼接逻辑无需分方向讨论。

    返回 ``(items_ascending, next_cursor, has_more)``。
    """
    has_more = len(rows) > page
    items = list(rows[:page])
    next_cursor: str | None = None
    if has_more and items:
        last = items[-1]
        next_cursor = _encode_forward_cursor(last.created_time, last.seq, last.id)
    if order != "asc":
        items = list(reversed(items))
    return items, next_cursor, has_more


def _has_beyond(db: Session, base_stmt, model, boundary_row, *, newer: bool) -> bool:
    """探测边界行 ``boundary_row`` 之外某方向是否还有匹配行。

    ``base_stmt`` 是已套好所有过滤（session/turn/level/q）但**未套游标与排序**的
    查询。用复合键 ``(created_time, seq, id)`` 与边界行严格比较：``newer=True`` 查
    是否存在更新的行（``> 边界``），否则查更旧的（``< 边界``）。只取 1 行判存在，
    两端探测互不依赖客户端的翻页方向，恒定正确。
    """
    triple = tuple_(model.created_time, model.seq, model.id)
    key = (boundary_row.created_time, boundary_row.seq, boundary_row.id)
    probe = base_stmt.where(triple > key if newer else triple < key)
    return db.exec(probe.limit(1)).first() is not None


def list_messages(
    db: Session,
    session_id: uuid.UUID,
    user: models.User,
    *,
    turn_id: uuid.UUID | None,
    order: str,
    cursor: str | None,
    limit: int,
    event_type: list[str] | None,
    role: ResearchMessageRole | None,
) -> ResearchMessageListResponse:
    """消息分页（查 ``research_message``，不含 log）。

    - ``turn_id``：传 → 只返回该轮消息（附带 turn 归属校验）；不传 → 跨全会话。
    - ``order``：只决定**翻页前进方向**，不改变单页内部顺序——返回列表**恒定升序**
      （最旧在前）。``desc``（默认）从最新开始、往更旧翻（历史翻页）；``asc`` 从
      最旧开始、往更新翻（SSE 回放）。
    - ``cursor``：不透明复合游标 ``{iso}|{seq}|{id}``，首次不传；用返回的
      ``next_cursor`` 原样回传继续翻。
    - ``limit``：单页大小；后端硬上限 500 防误刷。
    - ``event_type``：可选类型白名单（如 ``["stage_transition"]``）；空 → 不过滤。
      注意 log 已拆到 ``research_log`` 表，此处不接受也不返回 ``"log"``。
    - ``role``：可选角色过滤。

    Raises:
        HTTPException 404: session/turn 不存在或不属于该用户。
        HTTPException 400: cursor 格式非法。
    """
    if turn_id is not None:
        session, turn = _get_session_and_turn(db, session_id, turn_id, user)
    else:
        session = _get_session(db, session_id, user)
        turn = None

    stmt = select(ResearchMessage).where(
        ResearchMessage.session_id == session.id
    )
    if turn is not None:
        stmt = stmt.where(ResearchMessage.turn_id == turn.id)
    if event_type:
        stmt = stmt.where(ResearchMessage.event_type.in_(event_type))
    if role is not None:
        stmt = stmt.where(ResearchMessage.role == role)

    stmt = _apply_cursor_and_order(stmt, ResearchMessage, order, cursor)

    page = max(1, min(limit, _MESSAGE_LIMIT_MAX))
    rows = db.exec(stmt.limit(page + 1)).all()
    items, next_cursor, has_more = _finalize_page(rows, page, order)
    return ResearchMessageListResponse(
        items=[ResearchMessageItem.model_validate(m) for m in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


def list_logs(
    db: Session,
    session_id: uuid.UUID,
    user: models.User,
    *,
    turn_id: uuid.UUID | None,
    order: str,
    cursor: str | None,
    limit: int,
    level: list[str] | None,
    q: str | None,
) -> ResearchLogPageResponse:
    """日志双端游标窗口 / 全量查询（查独立的 ``research_log`` 表）。

    ``items`` **恒定升序**（旧→新），并返回窗口两端的游标与是否还有更多，供日志
    查看器上下双向翻页。两端 ``has_older`` / ``has_newer`` 用独立的 EXISTS 探测
    （与客户端从哪个方向导航过来无关，恒定正确）。

    - ``turn_id``：传 → 仅该轮；不传 → 跨全会话。
    - ``order`` / ``cursor``：只决定「本页取更旧还是更新的一批」及游标锚点方向，
      不改变返回的升序。``order=desc``（默认）从最新往旧翻、``order=asc`` 从最旧
      往新翻；``cursor`` 用返回的 ``older_cursor`` / ``newer_cursor`` 原样回传。
    - ``level``：可选级别白名单（INFO/WARNING/...）。
    - ``q``：可选关键字，对 ``message`` 做大小写不敏感模糊匹配（ILIKE，转义
      用户输入里的 ``% _ \\`` 通配符）。
    - ``limit``：单页大小；后端硬上限 2000。**传 ``0`` 表示不分页、一次返回全部
      匹配行**（两端游标为 None、两个 has_* 均 False）——导出 / 全量检索用，大
      会话可能上万行，慎用。

    Raises:
        HTTPException 404: session/turn 不存在或不属于该用户。
        HTTPException 400: cursor 格式非法。
    """
    if turn_id is not None:
        session, turn = _get_session_and_turn(db, session_id, turn_id, user)
    else:
        session = _get_session(db, session_id, user)
        turn = None

    # base_stmt：套齐所有过滤但**不含游标与排序**，供两端 EXISTS 探测复用。
    base_stmt = select(ResearchLog).where(ResearchLog.session_id == session.id)
    if turn is not None:
        base_stmt = base_stmt.where(ResearchLog.turn_id == turn.id)
    if level:
        base_stmt = base_stmt.where(ResearchLog.level.in_(level))
    if q and (term := q.strip()):
        # 转义 LIKE 通配符，避免用户输入的 % / _ / \ 被当作通配符。
        esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        base_stmt = base_stmt.where(
            ResearchLog.message.ilike(f"%{esc}%", escape="\\")
        )

    # limit == 0：不分页，取全部匹配行（升序，两端无更多）。
    if limit <= 0:
        rows = db.exec(
            base_stmt.order_by(
                ResearchLog.created_time.asc(),
                ResearchLog.seq.asc(),
                ResearchLog.id.asc(),
            )
        ).all()
        return ResearchLogPageResponse(
            items=[ResearchLogItem.model_validate(log) for log in rows],
        )

    stmt = _apply_cursor_and_order(base_stmt, ResearchLog, order, cursor)
    page = min(limit, _LOG_LIMIT_MAX)
    rows = db.exec(stmt.limit(page + 1)).all()
    # 复用 _finalize_page 得到升序 items（丢弃其 next_cursor/has_more——双端窗口
    # 用首尾行独立探测两端，不走单向游标语义）。
    items, _, _ = _finalize_page(rows, page, order)

    older_cursor: str | None = None
    newer_cursor: str | None = None
    has_older = False
    has_newer = False
    if items:
        oldest, newest = items[0], items[-1]
        older_cursor = _encode_forward_cursor(
            oldest.created_time, oldest.seq, oldest.id
        )
        newer_cursor = _encode_forward_cursor(
            newest.created_time, newest.seq, newest.id
        )
        has_older = _has_beyond(db, base_stmt, ResearchLog, oldest, newer=False)
        has_newer = _has_beyond(db, base_stmt, ResearchLog, newest, newer=True)

    return ResearchLogPageResponse(
        items=[ResearchLogItem.model_validate(log) for log in items],
        older_cursor=older_cursor,
        has_older=has_older,
        newer_cursor=newer_cursor,
        has_newer=has_newer,
    )
