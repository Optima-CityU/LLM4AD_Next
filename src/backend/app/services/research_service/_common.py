"""Research Service 层的跨子模块共享辅助：实体归属校验查询。

这些 ``_get_*`` 都做同一件事：按 ID 拉取实体并用 ``user.id`` 校验归属，
跨用户 / 跨会话访问一律返回 404（而非 403，避免被枚举探测）。
folders / sessions / turns / artifacts / messages 各子模块都复用它们。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlmodel import Session, select

from app import models
from app.models.research import (
    ResearchFolder,
    ResearchSession,
    ResearchTurn,
)


def _parse_cursor(cursor: str) -> datetime:
    """解析游标（上一页末条的 ISO 时间戳）；非法值 → 400。"""
    try:
        return datetime.fromisoformat(cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


# ---- 复合正序游标 (created_time, seq, id) ----
#
# turn 级消息/日志正序分页若只用 created_time 做严格大于，会在同一 created_time
# 的批量写入处丢数据（Windows 下时间戳精度粗，同批 flush 常撞同一时间戳）：一页
# 取满后 next_cursor=该时间戳，下一页 created_time > 该值 命中 0 行、提前
# has_more=False，剩余同时间戳行永久漏掉。故 next_cursor 携带三元组，翻页用
# (created_time, seq, id) > (...) 元组比较给出全序、无缝的边界。
#
# 游标编码为不透明字符串 ``{iso}|{seq}|{id}``（ISO 时间戳不含 '|'，可安全分隔），
# 前端原样回传。兼容旧版纯 ISO 游标（无 '|'）：退化为仅按时间戳严格大于——可能在
# 部署切换瞬间对同时间戳行少量重复返回，但不丢数据。
_CURSOR_SEP = "|"


def _encode_forward_cursor(created_time: datetime, seq: int, row_id: uuid.UUID) -> str:
    """把一行的 (created_time, seq, id) 编码成不透明正序游标字符串。"""
    return f"{created_time.isoformat()}{_CURSOR_SEP}{seq}{_CURSOR_SEP}{row_id}"


def _parse_forward_cursor(
    cursor: str,
) -> tuple[datetime, int | None, uuid.UUID | None]:
    """解析正序复合游标；返回 (created_time, seq, id)。

    - 新版 ``{iso}|{seq}|{id}`` → 三元组齐全；
    - 旧版纯 ISO（无分隔符）→ (ts, None, None)，调用方退化为仅时间戳比较。

    非法值 → 400。
    """
    parts = cursor.split(_CURSOR_SEP)
    try:
        ts = datetime.fromisoformat(parts[0])
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if len(parts) < 3:
        return ts, None, None
    try:
        seq = int(parts[1])
        row_id = uuid.UUID(parts[2])
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    return ts, seq, row_id


def _encode_reverse_cursor(ts: datetime, row_id: uuid.UUID) -> str:
    """把一行的 (updated_time, id) 编码成不透明倒序游标字符串。"""
    return f"{ts.isoformat()}{_CURSOR_SEP}{row_id}"


def _parse_reverse_cursor(cursor: str) -> tuple[datetime, uuid.UUID | None]:
    """解析倒序复合游标；返回 (ts, id)。

    - 新版 ``{iso}|{id}`` → 二元组齐全；
    - 旧版纯 ISO（无分隔符）→ (ts, None)，调用方退化为仅时间戳比较。

    非法值 → 400。
    """
    parts = cursor.split(_CURSOR_SEP)
    try:
        ts = datetime.fromisoformat(parts[0])
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    if len(parts) < 2:
        return ts, None
    try:
        return ts, uuid.UUID(parts[1])
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


def _get_folder(
    db: Session, folder_id: uuid.UUID, user: models.User
) -> ResearchFolder:
    """按 ID + user 校验拉取文件夹；不存在返 404。"""
    folder = db.get(ResearchFolder, folder_id)
    if not folder or folder.user_id != user.id:
        raise HTTPException(status_code=404, detail="folder not found")
    return folder


def _get_session(
    db: Session,
    session_id: uuid.UUID,
    user: models.User,
    *,
    for_update: bool = False,
) -> ResearchSession:
    """按 ID + user 校验拉取会话；不存在返 404。

    ``for_update=True`` 时对 session 行加 ``SELECT ... FOR UPDATE`` 行锁，把
    「读状态守卫 + 建轮 + 改 session」整段临界区串行化——消除并发
    start/collab/retry/门控回复之间的 TOCTOU（多个请求同时穿过 RUNNING /
    PAUSED_GATE / COLLABORATING 守卫，各自建轮）。第二个请求会阻塞在行锁上，
    直到第一个 commit 后才加载 session，此时状态已推进（如 RUNNING），守卫
    据此正确拒绝。锁随事务 commit/rollback 释放，不留孤儿（对比 Redis 锁需
    TTL 兜底、崩溃会阻塞）。所有建轮入口只锁 session 一行且顺序一致，不死锁。
    """
    if for_update:
        session = db.exec(
            select(ResearchSession)
            .where(ResearchSession.id == session_id)
            .with_for_update()
        ).first()
    else:
        session = db.get(ResearchSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def _get_session_and_turn(
    db: Session,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    user: models.User,
    *,
    for_update: bool = False,
) -> tuple[ResearchSession, ResearchTurn]:
    """一次拿到 (session, turn) 并校验归属（跨用户/跨会话都 404）。

    ``for_update`` 透传给 :func:`_get_session`：建轮类操作（如 retry）传 True 锁
    session 行，纯读操作（stop / get / stream）保持 False。
    """
    session = _get_session(db, session_id, user, for_update=for_update)
    turn = db.get(ResearchTurn, turn_id)
    if not turn or turn.session_id != session.id:
        raise HTTPException(status_code=404, detail="turn not found")
    return session, turn


def _find_turn_by_status(
    db: Session, session_id: uuid.UUID, status: str
) -> ResearchTurn | None:
    """会话下命中某状态的**最新** turn（无则 None）。

    按 ``created_time`` 倒序取最新一条：门控 / 协作恢复都只关心当前活跃的那轮，
    历史遗留的同状态旧 turn（若有）不应被选中。
    """
    stmt = (
        select(ResearchTurn)
        .where(ResearchTurn.session_id == session_id)
        .where(ResearchTurn.status == status)
        .order_by(ResearchTurn.created_time.desc())
    )
    return db.exec(stmt).first()
