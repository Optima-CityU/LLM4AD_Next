"""会话（Session）子域：CRUD + 分组过滤 + 详情组装 + 状态快照。

- ``create/update/list/get_detail/delete`` 覆盖会话生命周期的元数据管理；
- ``get_state`` 由 ``stage_transition`` 事件回放出结构化的 stage 快照，供前端首屏
  与状态面板消费（属会话读侧，故与会话 CRUD 同处一模块）。

会话 title 采用兜底策略：优先用户显式 title，否则从 topic 截取。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import or_, tuple_
from sqlmodel import Session, select

from app import models
from app.core.redis import delete_research_stream
from app.models.research import (
    ResearchMessage,
    ResearchSession,
    ResearchSessionStatus,
    ResearchTurn,
)
from app.schemas.research import (
    ResearchMessageItem,
    ResearchSessionCreateRequest,
    ResearchSessionDetailResponse,
    ResearchSessionItem,
    ResearchSessionListResponse,
    ResearchSessionUpdateRequest,
    ResearchStageSnapshot,
    ResearchStateResponse,
    ResearchTurnItem,
)
from app.tasks.research_runner import cleanup_run_dir, stage_display_name

from ._common import _get_folder, _get_session, _parse_cursor
from .profile_switch import (
    is_cross_type_switch,
    purge_stage_artifacts,
    purge_stage_data,
)

# 会话 title 兜底策略：优先用户传的 title；否则从 topic 截取，超长加省略号。
_TITLE_MAX = 60


def _derive_session_title(explicit: str | None, topic: str) -> str:
    """给 session 起个显示名：user title > topic 截取 > "untitled"。"""
    if explicit and explicit.strip():
        return explicit.strip()[:_TITLE_MAX]
    normalized = (topic or "").strip()
    if not normalized:
        return "untitled"
    if len(normalized) <= _TITLE_MAX:
        return normalized
    return normalized[: _TITLE_MAX - 1] + "…"


def create_session(
    db: Session, request: ResearchSessionCreateRequest, user: models.User
) -> ResearchSessionItem:
    """新建会话（不立即启动首轮）。"""
    if request.folder_id is not None:
        _get_folder(db, request.folder_id, user)

    title = _derive_session_title(request.title, request.topic)
    workspace_dict = (
        request.llm4ad_workspace.model_dump(mode="json")
        if request.llm4ad_workspace
        else None
    )

    session = ResearchSession(
        user_id=user.id,
        folder_id=request.folder_id,
        title=title,
        topic=request.topic,
        profile=request.profile,
        mode=request.mode.value,
        provider_id=request.provider_id,
        model_name=request.model_name,
        llm4ad_workspace=workspace_dict,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return ResearchSessionItem.model_validate(session)


def update_session(
    db: Session,
    session_id: uuid.UUID,
    request: ResearchSessionUpdateRequest,
    user: models.User,
) -> ResearchSessionItem:
    """会话通用改写：改名 / 改 topic / 移分组 / 改默认 mode/provider/model。

    ``folder_id`` 三值语义：未提供 = 不改；显式 null = 移到未分组；UUID = 移到该目录。
    通过 Pydantic v2 的 ``model_fields_set`` 判定字段是否显式提供。
    """
    session = _get_session(db, session_id, user)
    provided = request.model_fields_set
    if request.title is not None:
        # 与 create 一致：strip + 截断；strip 后为空则保留原值不覆盖。
        stripped = request.title.strip()
        if stripped:
            session.title = stripped[:_TITLE_MAX]
    if request.topic is not None:
        # topic 也支持更新：strip 后保留，允许空字符串（清空 topic）
        session.topic = request.topic.strip()
    if request.profile is not None and request.profile.strip():
        # profile 不校验合法值（交由容器内 ARC 判定）；strip 后为空则不覆盖
        new_profile = request.profile.strip()
        if new_profile != session.profile and is_cross_type_switch(
            session.profile, new_profile
        ):
            # sandbox ↔ 非 sandbox 跨类切换：第 9 步后的产物按旧类型生成，与新
            # 类型不兼容，须清空。运行中不允许切换（与 delete_session 同款守卫）。
            if session.status in (
                ResearchSessionStatus.RUNNING.value,
                ResearchSessionStatus.PAUSED.value,
            ):
                raise HTTPException(
                    status_code=409,
                    detail="session is running; stop it before switching profile type",
                )
            purge_stage_artifacts(session.run_dir)
            purge_stage_data(db, session.id)
        session.profile = new_profile
    if "folder_id" in provided:
        if request.folder_id is not None:
            _get_folder(db, request.folder_id, user)
            session.folder_id = request.folder_id
        else:
            session.folder_id = None
    if request.mode is not None:
        session.mode = request.mode.value
    if request.provider_id is not None:
        session.provider_id = request.provider_id
    if request.model_name is not None:
        session.model_name = request.model_name
    session.updated_time = datetime.now(UTC)
    db.add(session)
    db.commit()
    db.refresh(session)
    return ResearchSessionItem.model_validate(session)


def list_sessions(
    db: Session,
    user: models.User,
    *,
    folder_id: uuid.UUID | None,
    ungrouped_only: bool,
    statuses: list[ResearchSessionStatus] | None,
    q: str | None,
    cursor: str | None,
    limit: int,
) -> ResearchSessionListResponse:
    """按分组 / 状态 / 关键词过滤会话，游标分页（updated_time 倒序）。

    - ``q``：对 topic + title 做大小写不敏感模糊匹配（ILIKE）。
    - cursor = 上一页最后一条的 ``updated_time`` ISO 字符串；首次不传。
    """
    query = select(ResearchSession).where(ResearchSession.user_id == user.id)
    if ungrouped_only:
        query = query.where(ResearchSession.folder_id.is_(None))
    elif folder_id is not None:
        _get_folder(db, folder_id, user)
        query = query.where(ResearchSession.folder_id == folder_id)
    if statuses:
        query = query.where(
            ResearchSession.status.in_([s.value for s in statuses])
        )
    if q and (term := q.strip()):
        # 转义 LIKE 通配符，避免用户输入的 % / _ / \ 被当作通配符
        esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        query = query.where(
            or_(
                ResearchSession.topic.ilike(like, escape="\\"),
                ResearchSession.title.ilike(like, escape="\\"),
            )
        )
    if cursor:
        cursor_ts = _parse_cursor(cursor)
        query = query.where(ResearchSession.updated_time < cursor_ts)

    page = max(1, min(limit, 200))
    rows = db.exec(
        query.order_by(
            ResearchSession.updated_time.desc(),
            ResearchSession.id.desc(),
        ).limit(page + 1)
    ).all()
    has_more = len(rows) > page
    items = rows[:page]
    next_cursor = items[-1].updated_time.isoformat() if has_more and items else None
    return ResearchSessionListResponse(
        items=[ResearchSessionItem.model_validate(s) for s in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


def get_session_detail(
    db: Session,
    session_id: uuid.UUID,
    user: models.User,
    *,
    include_messages: bool,
    before: uuid.UUID | None,
    limit: int,
) -> ResearchSessionDetailResponse:
    """会话详情 + 最近一轮元数据。

    ``include_messages=True`` 时额外拉一页历史消息（默认关闭——推荐前端走
    独立的 ``GET /turns/{tid}/messages`` 端点做增量分页，此参数保留是为了
    "首屏一次拉全"这种场景的兼容）。
    """
    session = _get_session(db, session_id, user)
    active_turn = None
    if session.active_turn_id:
        turn = db.get(ResearchTurn, session.active_turn_id)
        if turn:
            active_turn = ResearchTurnItem.model_validate(turn)

    messages: list[ResearchMessageItem] = []
    has_more = False
    if include_messages:
        msg_query = select(ResearchMessage).where(
            ResearchMessage.session_id == session.id
        )
        if before is not None:
            anchor = db.get(ResearchMessage, before)
            if anchor and anchor.session_id == session.id:
                # 复合游标 (created_time, id)：日志批量写入常同一微秒，仅按
                # created_time 严格小于会漏掉与锚点同时间戳的行。带 id 次级键杜绝。
                msg_query = msg_query.where(
                    tuple_(
                        ResearchMessage.created_time, ResearchMessage.id
                    )
                    < (anchor.created_time, anchor.id)
                )
        msg_query = msg_query.order_by(
            ResearchMessage.created_time.desc(), ResearchMessage.id.desc()
        ).limit(limit + 1)
        rows = db.exec(msg_query).all()
        has_more = len(rows) > limit
        msg_rows = list(reversed(rows[:limit]))
        messages = [ResearchMessageItem.model_validate(m) for m in msg_rows]
    return ResearchSessionDetailResponse(
        session=ResearchSessionItem.model_validate(session),
        active_turn=active_turn,
        messages=messages,
        has_more=has_more,
    )


def delete_session(
    db: Session, session_id: uuid.UUID, user: models.User
) -> None:
    """删除会话（含 run_dir 与关联 Redis Stream）。终态会话推荐用这个接口清盘。"""
    session = _get_session(db, session_id, user)
    if session.status in (
        ResearchSessionStatus.RUNNING.value,
        ResearchSessionStatus.PAUSED.value,
    ):
        raise HTTPException(
            status_code=409,
            detail="session is running; stop it before delete",
        )
    deleted_session_id = session.id
    run_dir = session.run_dir
    active_turn_id = session.active_turn_id
    db.delete(session)
    db.commit()
    # DB 删除已是权威结果；文件 / Redis 清理失败不应再翻成 500，记日志即可。
    try:
        cleanup_run_dir(run_dir)
        if active_turn_id:
            delete_research_stream(deleted_session_id, active_turn_id)
    except Exception:
        logger.opt(exception=True).warning(
            f"post-delete cleanup failed session={deleted_session_id}"
        )


def get_state(
    db: Session, session_id: uuid.UUID, user: models.User
) -> ResearchStateResponse:
    """会话当前状态的结构化快照。"""
    session = _get_session(db, session_id, user)
    stage_rows = db.exec(
        select(ResearchMessage)
        .where(
            ResearchMessage.session_id == session.id,
            ResearchMessage.event_type == "stage_transition",
        )
        .order_by(ResearchMessage.created_time.asc())
    ).all()
    stages: list[ResearchStageSnapshot] = []
    seen: dict[int, ResearchStageSnapshot] = {}
    for row in stage_rows:
        payload = row.payload or {}
        stage = row.stage if row.stage is not None else payload.get("stage")
        if stage is None:
            continue
        name = payload.get("name") or stage_display_name(stage)
        entry = seen.get(stage) or ResearchStageSnapshot(
            stage=stage, name=name, status="pending"
        )
        status = payload.get("status") or "running"
        # ARC StageStatus → 前端 ResearchStageSnapshot 词汇表
        # (pending|running|done|failed|skipped|waiting)。running 记 started_at，
        # 终态记 ended_at；gate/审批类归 waiting；瞬时态（approved/pending 等）不覆盖。
        if status in ("running", "retrying"):
            entry.status = "running"
            entry.started_at = entry.started_at or row.created_time
        elif status == "done":
            entry.status = "done"
            entry.ended_at = row.created_time
            entry.error = None  # REFINE 重跑后恢复成功，清掉早前的失败原因
        elif status == "failed":
            entry.status = "failed"
            entry.ended_at = row.created_time
            err = payload.get("error")
            if err:
                entry.error = str(err)
        elif status in ("blocked_approval", "paused"):
            entry.status = "waiting"
        seen[stage] = entry
    stages = [seen[k] for k in sorted(seen)]

    # metrics/hypotheses 过去由 llm4ad_final_state 事件填充，但该事件从无来源
    # （见 research_container_runner 去 tail 化），恒为空；保留响应字段维持 API 契约。
    metrics: dict[str, Any] = {}
    hypotheses: dict[str, Any] = {}

    return ResearchStateResponse(
        session_id=session.id,
        status=ResearchSessionStatus(session.status),
        active_stage=session.active_stage,
        active_stage_name=session.active_stage_name,
        stages=stages,
        best_objective=session.best_objective,
        best_code_sha256=session.best_code_sha256,
        metrics=metrics,
        hypotheses=hypotheses,
        updated_at=session.updated_time,
    )
