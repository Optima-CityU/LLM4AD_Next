"""会话（Session）子域：CRUD + 分组过滤 + 详情组装 + 状态快照。

- ``create/update/list/get_detail/delete`` 覆盖会话生命周期的元数据管理；
- ``get_state`` 由 ``stage_transition`` 事件回放出结构化的 stage 快照，供前端首屏
  与状态面板消费（属会话读侧，故与会话 CRUD 同处一模块）。

会话 title 采用兜底策略：优先用户显式 title，否则从 topic 截取。
"""

from __future__ import annotations

import io
import shutil
import uuid
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import or_, tuple_
from sqlmodel import Session, select

from app import models
from app.core.redis import delete_research_stream
from app.models.research import (
    ResearchLog,
    ResearchMessage,
    ResearchMessageRole,
    ResearchSession,
    ResearchSessionStatus,
    ResearchTurn,
    ResearchTurnStatus,
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
from app.tasks.research_runner.snapshots import resolve_run_dir, snap_session

from ._common import (
    _encode_reverse_cursor,
    _find_turn_by_status,
    _get_folder,
    _get_session,
    _parse_reverse_cursor,
)
from .profile_switch import (
    is_cross_type_switch,
    purge_stage_artifacts,
    purge_stage_data,
)
from .templates import derive_topic, load_manifest, materialize, rematerialize

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


def _seed_template_checkpoint(
    db: Session,
    session: ResearchSession,
    *,
    topic_id: str,
) -> None:
    """把「模板已预置 stage-07/08/09」写成一枚种子轮 + 6 条 stage 事件。

    run_dir 里已有 stage-07/08/09 产物与 ``checkpoint.json``（见
    :func:`templates.materialize`），若 DB 里没有任何痕迹，前端阶段 rail 会显示
    空白。这里补一条 ``COMPLETED`` 的种子轮 + 每个阶段一对
    ``running``/``done`` 的 ``stage_transition`` 消息，让 :func:`get_state` 的
    回放逻辑（只读 ``event_type == "stage_transition"`` 的行）重建出「7/8/9 已完成」。

    刻意**不**置 ``session.status``：会话仍是 ``PENDING``，首轮走的是
    :func:`turns.start_turn` 的「新建 turn」路径，与非模板会话完全一致。种子轮只是
    这批消息的 FK 载体（``ResearchMessage.turn_id`` 非空）。

    Args:
        db: 数据库会话（调用方负责 commit）。
        session: 已 flush 出 id 的会话行。
        topic_id: 课题 id，仅用于文案。
    """
    now = _now_utc()
    turn = ResearchTurn(
        id=uuid.uuid4(),
        session_id=session.id,
        status=ResearchTurnStatus.COMPLETED.value,
        from_stage="7",
        to_stage="9",
        user_input=f"initialized from template {topic_id}",
        started_at=now,
        ended_at=now,
    )
    db.add(turn)
    db.flush()

    for seq, stage in enumerate((7, 8, 9), start=1):
        name = stage_display_name(stage)
        for offset, status in enumerate(("running", "done")):
            db.add(
                ResearchMessage(
                    session_id=session.id,
                    turn_id=turn.id,
                    role=ResearchMessageRole.SYSTEM,
                    content=f"[stage-{stage}] {name} {status}",
                    turn_status=ResearchTurnStatus.COMPLETED.value,
                    event_type="stage_transition",
                    event_key=f"stage_transition:{seq}{offset}",
                    stage=stage,
                    seq=seq * 10 + offset,
                    payload={
                        "kind": "stage_progress",
                        "stage": stage,
                        "name": name,
                        "status": status,
                    },
                )
            )


def create_session(
    db: Session, request: ResearchSessionCreateRequest, user: models.User
) -> ResearchSessionItem:
    """新建会话（不立即启动首轮）。

    ``request.template_id`` 给定即走「从模板创建」：把 ARC-Bench 课题的
    stage-07/08/09 产物物化进 run_dir，并补一枚种子轮 + 7/8/9 的 stage 事件。
    其余流程与非模板会话完全一致——模板只是**创建时的一次性初始化输入**，不落任何
    session 字段，后续对会话的读写路径无需感知它。
    """
    if request.folder_id is not None:
        _get_folder(db, request.folder_id, user)

    topic = (request.topic or "").strip()
    title = request.title
    metric_direction = request.metric_direction
    metric_key = request.metric_key
    profile = request.profile
    manifest: dict[str, Any] | None = None

    if request.template_id:
        # 课题不存在 / 镜像未装 extra 都在这里 404 / 503，早于任何写库。
        manifest = load_manifest(request.template_id)
        if not topic:
            topic = derive_topic(manifest)
        if not title:
            # 模板标题比派生 topic 全文更适合当会话名。
            title = str(manifest.get("title") or request.template_id)
        metrics = (manifest.get("experiment_design") or {}).get("metrics") or []
        if metrics:
            # 与 run_bench_init.materialize_config 同款：取首个 metric。
            # 显式传值优先——用户可以在建会话时覆盖模板建议。
            primary = metrics[0]
            if not metric_key:
                metric_key = str(primary.get("name") or "")
            if not metric_direction:
                direction = str(primary.get("direction") or "")
                if direction in ("maximize", "minimize"):
                    metric_direction = direction
        # profile 与模板域无关（config_builder 恒用 mathematics_optimization），
        # 不按域改写；模板自带的 domain_profile.json 由容器内 stage-10 消费。

    if not topic:
        raise HTTPException(
            status_code=400,
            detail="topic is required when template_id is not given",
        )

    workspace_dict = (
        request.llm4ad_workspace.model_dump(mode="json")
        if request.llm4ad_workspace
        else None
    )

    session = ResearchSession(
        user_id=user.id,
        folder_id=request.folder_id,
        title=_derive_session_title(title, topic),
        topic=topic,
        profile=profile,
        mode=request.mode.value,
        metric_direction=metric_direction,
        metric_key=metric_key,
        provider_id=request.provider_id,
        model_name=request.model_name,
        llm4ad_workspace=workspace_dict,
    )
    if request.template_id:
        # run_dir 在建会话时就定下来并落库：与 _bootstrap 的 resolve_run_dir 同口径
        # （session.run_dir 优先），物化与后续 pipeline 读写必然同一目录。
        session.run_dir = str(
            resolve_run_dir(snap_session(session))
        )
    db.add(session)
    db.commit()
    db.refresh(session)

    # 磁盘物化 + 种子轮都放在主提交之后（best-effort）：即便失败，会话已存在、
    # 首轮照常可跑，只是没有预置产物与 7/8/9 的进度痕迹。
    if request.template_id:
        if not materialize(Path(session.run_dir), request.template_id):
            logger.warning(
                f"template {request.template_id} not materialized for session {session.id}"
            )
        try:
            _seed_template_checkpoint(db, session, topic_id=request.template_id)
            db.commit()
        except Exception:
            db.rollback()
            logger.opt(exception=True).warning(
                f"seed template checkpoint failed session={session.id}"
            )
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

    # ---- 先做全部校验（任何删除/提交之前），保证「校验失败 → 零副作用」----
    # folder 归属校验（可能 404）必须前置：旧实现放在 purge 之后，一旦这里 404，
    # 磁盘/DB 已被清空却又回滚 profile，留下「清了盘但没切成」的损坏态。
    folder_change: tuple[bool, uuid.UUID | None] = (False, None)
    if "folder_id" in provided:
        if request.folder_id is not None:
            _get_folder(db, request.folder_id, user)
            folder_change = (True, request.folder_id)
        else:
            folder_change = (True, None)

    # 跨类 profile 切换：判定是否需要清盘 + 运行态守卫（都在删除动作之前）。
    new_profile: str | None = None
    needs_purge = False
    if request.profile is not None and request.profile.strip():
        # profile 不校验合法值（交由容器内 ARC 判定）；strip 后为空则不覆盖
        candidate = request.profile.strip()
        if candidate != session.profile and is_cross_type_switch(
            session.profile, candidate
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
            needs_purge = True
        new_profile = candidate

    # ---- 校验全过，开始改写字段 ----
    if request.title is not None:
        # 与 create 一致：strip + 截断；strip 后为空则保留原值不覆盖。
        stripped = request.title.strip()
        if stripped:
            session.title = stripped[:_TITLE_MAX]
    if request.topic is not None:
        # topic 也支持更新：strip 后保留，允许空字符串（清空 topic）
        session.topic = request.topic.strip()
    if new_profile is not None:
        session.profile = new_profile
    if folder_change[0]:
        session.folder_id = folder_change[1]
    if request.mode is not None:
        session.mode = request.mode.value
    if request.metric_direction is not None:
        session.metric_direction = request.metric_direction
    if request.metric_key is not None:
        session.metric_key = request.metric_key
    if request.provider_id is not None:
        session.provider_id = request.provider_id
    if request.model_name is not None:
        session.model_name = request.model_name
    session.updated_time = datetime.now(UTC)
    run_dir_snapshot = session.run_dir
    db.add(session)
    db.commit()

    # ---- 清盘放到 profile 落库成功之后（best-effort）----
    # profile 切换是权威结果；磁盘删除不可逆、purge_stage_data 自带 commit，放在主
    # 提交之后执行：即便清理失败，切换已生效，残留旧产物下一轮启动前会被覆盖/忽略。
    if needs_purge:
        purge_stage_artifacts(run_dir_snapshot)
        purge_stage_data(db, session.id)
        # 模型产物被清后，模板预置的 stage-09 也一并没了。若 run_dir 里还留着题面
        # （topic_manifest.json，见 templates.rematerialize），就地补回来——否则要拖到
        # 下一轮 worker bootstrap 才补，期间前端看到的是空 stage-09。
        if run_dir_snapshot:
            rematerialize(Path(run_dir_snapshot))

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
        # 复合游标 (updated_time, id)：仅按 updated_time 严格小于时，多条会话同一
        # updated_time 且恰好跨页边界会被整体跳过而丢失。带 id 次级键给出全序边界。
        cur_ts, cur_id = _parse_reverse_cursor(cursor)
        if cur_id is not None:
            query = query.where(
                tuple_(ResearchSession.updated_time, ResearchSession.id)
                < (cur_ts, cur_id)
            )
        else:
            # 旧版纯 ISO 游标兜底：仅时间戳比较（切换期短暂，可能少量重复不丢数据）。
            query = query.where(ResearchSession.updated_time < cur_ts)

    page = max(1, min(limit, 200))
    rows = db.exec(
        query.order_by(
            ResearchSession.updated_time.desc(),
            ResearchSession.id.desc(),
        ).limit(page + 1)
    ).all()
    has_more = len(rows) > page
    items = rows[:page]
    next_cursor = (
        _encode_reverse_cursor(items[-1].updated_time, items[-1].id)
        if has_more and items
        else None
    )
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

    # 活跃协作轮：协作是与 pipeline 并存的独立 turn，不被 session.active_turn_id
    # 跟踪，单独查出暴露给前端，供刷新后恢复协作 SSE 订阅（免翻 /turns 列表）。
    active_collab_turn = None
    collab_turn = _find_turn_by_status(
        db, session.id, ResearchTurnStatus.COLLABORATING.value
    )
    if collab_turn is not None:
        active_collab_turn = ResearchTurnItem.model_validate(collab_turn)

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
        active_collab_turn=active_collab_turn,
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
    # 收集本会话全部 turn id：一个 session 跨多轮，每轮可能有独立 Redis Stream。
    # 只清 active_turn 会漏掉历史轮的 stream，造成 Redis key 泄漏，故先全量取出。
    turn_ids = db.exec(
        select(ResearchTurn.id).where(ResearchTurn.session_id == session.id)
    ).all()
    db.delete(session)
    db.commit()
    # DB 删除已是权威结果；文件 / Redis 清理失败不应再翻成 500，记日志即可。
    try:
        cleanup_run_dir(run_dir)
        for tid in turn_ids:
            delete_research_stream(deleted_session_id, tid)
    except Exception:
        logger.opt(exception=True).warning(
            f"post-delete cleanup failed session={deleted_session_id}"
        )


# ---- 复制会话与产物导入 ----


def _now_utc() -> datetime:
    """当前 UTC 时间（带时区），与 ``TimeMixin`` 的存储口径一致。"""
    return datetime.now(UTC)


def _derive_copied_run_dir(source: ResearchSession, new_session_id: uuid.UUID) -> str:
    """为新会话派生 run_dir：沿用源码路径的目录骨架，仅把 ``{session_id}`` 段换成新 id。

    源码 ``run_dir`` 形如 ``{home}/code_user-{uid}/research/{session_id}``，复制后落在
    同一用户空间、新的会话目录下，与原始目录并存且物理隔离。若源码从未启动（无
    run_dir），返回空串；若路径里找不到会话 id 段，退而求其次在新目录名旁追加 id。
    """
    if not source.run_dir:
        return ""
    path = Path(source.run_dir)
    parts = list(path.parts)
    old_id = str(source.id)
    for idx in range(len(parts) - 1, -1, -1):
        if parts[idx] == old_id:
            parts[idx] = str(new_session_id)
            return str(Path(*parts))
    return str(path.parent / str(new_session_id))


def copy_session(
    db: Session, session_id: uuid.UUID, user: models.User
) -> ResearchSessionItem:
    """复制一个科研会话：整棵 DB 记录 + 落盘产物目录。

    - **DB**：生成全部新 UUID（session / turn / message / log），杜绝主键冲突；
      关联外键（``turn.session_id``、``message.session_id/turn_id``、
      ``session.active_turn_id``、``turn.respond_to_message_id``、``log.*``）在新 id
      之间重建映射，保证关联表一一对应。
    - **产物目录**：沿源码 ``run_dir`` 目录骨架复制到新会话 id 下（见
      :func:`_derive_copied_run_dir`）；复制失败不翻成 500，记日志后仍返回 DB 副本。
    - ``stream_id`` 全部清空：副本没有对应的 Redis Stream，保留旧 id 会误导 SSE 续传
      去读别的会话。
    """
    source = _get_session(db, session_id, user)
    new_session_id = uuid.uuid4()
    new_run_dir = _derive_copied_run_dir(source, new_session_id)
    now = _now_utc()

    new_session = ResearchSession(
        id=new_session_id,
        user_id=source.user_id,
        folder_id=source.folder_id,
        title=source.title,
        topic=source.topic,
        profile=source.profile,
        mode=source.mode,
        metric_direction=source.metric_direction,
        metric_key=source.metric_key,
        provider_id=source.provider_id,
        model_name=source.model_name,
        status=source.status,
        active_stage=source.active_stage,
        active_stage_name=source.active_stage_name,
        run_dir=new_run_dir or None,
        latest_config=deepcopy(source.latest_config),
        llm4ad_workspace=deepcopy(source.llm4ad_workspace),
        best_objective=source.best_objective,
        best_code_sha256=source.best_code_sha256,
        ended_time=source.ended_time,
        error=source.error,
        analysis_report=source.analysis_report,
        # 副本是新的生命周期起点：创建/更新时间取当前，避免沿用旧值被当成过期会话清理
        created_time=now,
        updated_time=now,
    )
    db.add(new_session)
    db.flush()  # 拿到 new_session_id（虽自生成，flush 保证后续 FK 可见）

    # 1) 复制 turn（respond_to_message_id 稍后按消息映射补齐）
    old_turns = db.exec(
        select(ResearchTurn).where(ResearchTurn.session_id == source.id)
    ).all()
    turn_map: dict[uuid.UUID, ResearchTurn] = {}
    for t in old_turns:
        new_turn = ResearchTurn(
            id=uuid.uuid4(),
            session_id=new_session_id,
            celery_task_id=None,  # 无活任务；旧 task id 只会误导
            status=t.status,
            provider_id=t.provider_id,
            model_name=t.model_name,
            mode=t.mode,
            from_stage=t.from_stage,
            to_stage=t.to_stage,
            user_input=t.user_input,
            respond_to_message_id=None,
            error=t.error,
            started_at=t.started_at,
            ended_at=t.ended_at,
            created_time=t.created_time,
            updated_time=t.updated_time,
        )
        db.add(new_turn)
        turn_map[t.id] = new_turn
    db.flush()

    # 2) 复制 message（建立旧→新 id 映射，供 respond_to_message_id / active_turn 用）
    old_messages = db.exec(
        select(ResearchMessage).where(ResearchMessage.session_id == source.id)
    ).all()
    message_map: dict[uuid.UUID, ResearchMessage] = {}
    for m in old_messages:
        target_turn = turn_map.get(m.turn_id)
        if target_turn is None:  # 消息挂到了不存在的 turn？跳过，避免 KeyError
            logger.warning(
                f"copy_session orphan message skipped msg={m.id} turn={m.turn_id}"
            )
            continue
        new_message = ResearchMessage(
            id=uuid.uuid4(),
            session_id=new_session_id,
            turn_id=target_turn.id,
            role=m.role,
            content=m.content,
            turn_status=m.turn_status,
            error=m.error,
            payload=m.payload,
            payload_locked=m.payload_locked,
            payload_locked_at=m.payload_locked_at,
            payload_submission=m.payload_submission,
            stage=m.stage,
            event_type=m.event_type,
            event_key=m.event_key,
            seq=m.seq,
            stream_id=None,  # 无 Redis stream（见函数 docstring）
            created_time=m.created_time,
            updated_time=m.updated_time,
        )
        db.add(new_message)
        message_map[m.id] = new_message
    db.flush()

    # 3) 补齐 turn.respond_to_message_id（消息已建，映射可解）
    for old_turn in old_turns:
        if old_turn.respond_to_message_id:
            new_msg = message_map.get(old_turn.respond_to_message_id)
            if new_msg is not None:
                new_turn = turn_map[old_turn.id]
                new_turn.respond_to_message_id = new_msg.id

    # 4) 复制 log
    old_logs = db.exec(
        select(ResearchLog).where(ResearchLog.session_id == source.id)
    ).all()
    for lg in old_logs:
        target_turn = turn_map.get(lg.turn_id)
        if target_turn is None:
            logger.warning(
                f"copy_session orphan log skipped log={lg.id} turn={lg.turn_id}"
            )
            continue
        new_log = ResearchLog(
            id=uuid.uuid4(),
            session_id=new_session_id,
            turn_id=target_turn.id,
            level=lg.level,
            message=lg.message,
            source=lg.source,
            module=lg.module,
            event_key=lg.event_key,
            turn_status=lg.turn_status,
            stage=lg.stage,
            ts=lg.ts,
            seq=lg.seq,
            stream_id=None,
            created_time=lg.created_time,
            updated_time=lg.updated_time,
        )
        db.add(new_log)

    # 5) 会话活动轮指针映射到新 turn
    if source.active_turn_id and source.active_turn_id in turn_map:
        new_session.active_turn_id = turn_map[source.active_turn_id].id

    db.commit()
    db.refresh(new_session)

    # 6) 复制落盘产物（best-effort：失败记录日志，不阻断已完成的 DB 复制）
    if source.run_dir and new_run_dir:
        src = Path(source.run_dir)
        dst = Path(new_run_dir)
        if src.is_dir() and not dst.exists():
            try:
                shutil.copytree(src, dst)
            except Exception:
                logger.opt(exception=True).warning(
                    f"copy_session files failed session={source.id} -> {new_session_id}"
                )

    return ResearchSessionItem.model_validate(new_session)


def import_artifacts_zip(
    db: Session,
    session_id: uuid.UUID,
    user: models.User,
    data: bytes,
    filename: str | None,
) -> dict[str, Any]:
    """上传 zip 解压覆盖到产物目录（run_dir）。

    - zip 条目以**相对 run_dir** 的关系解压（对齐 ``/artifacts/archive`` 的打包口径：
      归档内不含顶层容器目录）。
    - 已存在的文件直接覆盖；单个条目失败（解压/写入）**捕获并记录**，不中断整批，
      完成后返回失败清单。
    - 防 zip-slip：每个目标路径 resolve 后必须仍在 run_dir 之内，越界条目跳过并记失败。
    """
    session = _get_session(db, session_id, user)
    root = Path(session.run_dir) if session.run_dir else None
    if not root or not root.is_dir():
        raise HTTPException(status_code=404, detail="run_dir not initialized")
    root = root.resolve()

    imported = 0
    overwritten = 0
    failed: list[str] = []

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(status_code=400, detail="invalid zip file") from exc

    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            # 容错：去掉前导斜杠，防绝对路径 / 盘符越过 root 判断
            name = name.lstrip("/")
            if name.startswith("../") or ".." in name.split("/"):
                failed.append(info.filename)
                continue
            target = (root / name).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                failed.append(info.filename)
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                existed = target.exists()
                target.write_bytes(archive.read(info))
                imported += 1
                if existed:
                    overwritten += 1
            except Exception:
                logger.opt(exception=True).warning(
                    f"import artifacts failed entry={info.filename}"
                )
                failed.append(info.filename)

    return {
        "session_id": session.id,
        "run_dir": session.run_dir,
        "source": filename,
        "imported": imported,
        "overwritten": overwritten,
        "failed": failed,
    }


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
            # REFINE/回跳会让已 done/failed 的 stage 重新进入 running：清掉上一轮的
            # ended_at，否则前端会渲染出「正在运行却已有结束时间」的矛盾态。
            entry.ended_at = None
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
