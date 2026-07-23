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


def _get_folder(
    db: Session, folder_id: uuid.UUID, user: models.User
) -> ResearchFolder:
    """按 ID + user 校验拉取文件夹；不存在返 404。"""
    folder = db.get(ResearchFolder, folder_id)
    if not folder or folder.user_id != user.id:
        raise HTTPException(status_code=404, detail="folder not found")
    return folder


def _get_session(
    db: Session, session_id: uuid.UUID, user: models.User
) -> ResearchSession:
    """按 ID + user 校验拉取会话；不存在返 404。"""
    session = db.get(ResearchSession, session_id)
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def _get_session_and_turn(
    db: Session, session_id: uuid.UUID, turn_id: uuid.UUID, user: models.User
) -> tuple[ResearchSession, ResearchTurn]:
    """一次拿到 (session, turn) 并校验归属（跨用户/跨会话都 404）。"""
    session = _get_session(db, session_id, user)
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
