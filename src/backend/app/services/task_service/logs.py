"""任务日志读取。

提供游标分页的任务日志查询，优先读取数据库中已持久化的日志，运行中任务
回退到 Redis，并支持按类型/级别/消息全文过滤。
"""

import uuid
from datetime import datetime

from sqlmodel import Session, select

from app import models
from app.schemas import task as schemas

from .auth import get_task_with_auth


def has_persisted_logs(db: Session, task_id: uuid.UUID) -> bool:
    """检查任务在 task_log 表中是否存在已持久化的日志条目。"""
    from sqlalchemy import func as sa_func

    from app.models import TaskLog

    return db.exec(select(sa_func.count()).where(TaskLog.task_id == task_id)).one() > 0


def get_task_logs(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    cursor: str | None = None,
    limit: int = 100,
    log_type: list[str] | None = None,
    level: list[str] | None = None,
    message_query: str | None = None,
) -> schemas.TaskLogsResponse:
    """读取任务日志：游标分页，倒序查询。

    游标编码为 base64(timestamp_iso|uuid)，标识上一页最早的一条记录。
    首次请求不传 cursor，返回最新的 limit 条；后续传入 next_cursor 向前翻页。
    返回的 entries 按时间正序排列，方便前端直接渲染。

    Args:
        db: 数据库会话。
        task_id: 任务 ID。
        current_user: 当前认证用户。
        cursor: 分页游标（base64 编码）。
        limit: 每页条数。
        log_type: 按日志类型过滤，支持多选。
        level: 按日志级别过滤，支持多选。
        message_query: 在消息字段中进行全文检索。

    Returns:
        任务日志游标分页响应。
    """
    from sqlalchemy import func as sa_func

    from app.models import TaskLog

    task = get_task_with_auth(db, task_id, current_user)

    db_count = db.exec(select(sa_func.count()).where(TaskLog.task_id == task.id)).one()

    if db_count > 0:
        if limit == 0:
            return _get_all_task_logs_from_db(db, task, log_type, level, message_query)
        return _get_task_logs_from_db(
            db,
            task,
            cursor,
            limit,
            log_type,
            level,
            message_query,
        )

    if limit == 0:
        return _get_all_task_logs_from_redis(task, log_type, level, message_query)
    return _get_task_logs_from_redis(task, cursor, limit, log_type, level, message_query)


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Decode a cursor string into (timestamp, id)."""
    import base64

    raw = base64.urlsafe_b64decode(cursor).decode()
    ts_str, id_str = raw.rsplit("|", 1)
    ts = datetime.fromisoformat(ts_str)
    return ts, uuid.UUID(id_str)


def _encode_cursor(timestamp: datetime, log_id: uuid.UUID) -> str:
    """Encode (timestamp, id) into an opaque cursor string."""
    import base64

    raw = f"{timestamp.isoformat()}|{log_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _get_all_task_logs_from_db(
    db: Session,
    task: models.Task,
    log_type: list[str] | None,
    level: list[str] | None,
    message_query: str | None,
) -> schemas.TaskLogsResponse:
    """Return all logs from DB without pagination."""
    from app.models import TaskLog
    from app.utils.log_persist import strip_generated_fields_for_list, task_log_to_dict

    query = select(TaskLog).where(TaskLog.task_id == task.id)

    if log_type:
        query = query.where(TaskLog.type.in_(log_type))
    if level:
        query = query.where(TaskLog.level.in_(level))
    if message_query:
        query = query.where(TaskLog.message.contains(message_query))

    query = query.order_by(TaskLog.timestamp.asc(), TaskLog.id.asc())
    rows = list(db.exec(query).all())

    entries = [task_log_to_dict(row) for row in rows]
    for entry in entries:
        strip_generated_fields_for_list(entry)

    return schemas.TaskLogsResponse(
        task_id=task.id,
        source="db",
        entries=entries,
        next_cursor=None,
        has_more=False,
    )


def _filter_redis_entries(
    entries: list[dict],
    log_type: list[str] | None,
    level: list[str] | None,
    message_query: str | None,
) -> list[dict]:
    """对 Redis 中的日志条目按 type/level/message 做内存过滤。"""
    if not entries:
        return entries
    if not log_type and not level and not message_query:
        return entries

    type_set = set(log_type) if log_type else None
    level_set = set(level) if level else None
    result = []
    for entry in entries:
        if type_set is not None and entry.get("type") not in type_set:
            continue
        if level_set is not None and entry.get("level") not in level_set:
            continue
        if message_query:
            msg = entry.get("message")
            if not msg or message_query not in msg:
                continue
        result.append(entry)
    return result


def _get_all_task_logs_from_redis(
    task: models.Task,
    log_type: list[str] | None = None,
    level: list[str] | None = None,
    message_query: str | None = None,
) -> schemas.TaskLogsResponse:
    """Return all logs from Redis without pagination."""
    from app.core.redis import read_all_logs
    from app.utils.log_persist import strip_generated_fields_for_list

    all_entries = read_all_logs(task.id)
    filtered = _filter_redis_entries(all_entries or [], log_type, level, message_query)
    for entry in filtered:
        strip_generated_fields_for_list(entry)

    return schemas.TaskLogsResponse(
        task_id=task.id,
        source="redis",
        entries=filtered,
        next_cursor=None,
        has_more=False,
    )


def _get_task_logs_from_db(
    db: Session,
    task: models.Task,
    cursor: str | None,
    limit: int,
    log_type: list[str] | None,
    level: list[str] | None,
    message_query: str | None,
) -> schemas.TaskLogsResponse:
    """Cursor-paginated log retrieval from DB, reverse chronological."""
    from sqlalchemy import tuple_

    from app.models import TaskLog
    from app.utils.log_persist import strip_generated_fields_for_list, task_log_to_dict

    query = select(TaskLog).where(TaskLog.task_id == task.id)

    if log_type:
        query = query.where(TaskLog.type.in_(log_type))
    if level:
        query = query.where(TaskLog.level.in_(level))
    if message_query:
        query = query.where(TaskLog.message.contains(message_query))

    if cursor:
        cursor_ts, cursor_id = _decode_cursor(cursor)
        query = query.where(tuple_(TaskLog.timestamp, TaskLog.id) < tuple_(cursor_ts, cursor_id))

    query = query.order_by(TaskLog.timestamp.desc(), TaskLog.id.desc()).limit(limit + 1)
    rows = list(db.exec(query).all())

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    rows.reverse()

    next_cursor = None
    if has_more and rows:
        oldest = rows[0]
        next_cursor = _encode_cursor(oldest.timestamp, oldest.id)

    entries = [task_log_to_dict(row) for row in rows]
    for entry in entries:
        strip_generated_fields_for_list(entry)

    return schemas.TaskLogsResponse(
        task_id=task.id,
        source="db",
        entries=entries,
        next_cursor=next_cursor,
        has_more=has_more,
    )


def _get_task_logs_from_redis(
    task: models.Task,
    cursor: str | None,
    limit: int,
    log_type: list[str] | None = None,
    level: list[str] | None = None,
    message_query: str | None = None,
) -> schemas.TaskLogsResponse:
    """Cursor-paginated log retrieval from Redis, reverse chronological."""
    import base64

    from app.core.redis import read_all_logs
    from app.utils.log_persist import strip_generated_fields_for_list

    all_entries = read_all_logs(task.id)
    all_entries = _filter_redis_entries(all_entries or [], log_type, level, message_query)

    if not all_entries:
        return schemas.TaskLogsResponse(
            task_id=task.id,
            source="redis",
            entries=[],
            next_cursor=None,
            has_more=False,
        )

    if cursor:
        cursor_idx = int(base64.urlsafe_b64decode(cursor).decode())
    else:
        cursor_idx = len(all_entries)

    start = max(cursor_idx - limit, 0)
    end = cursor_idx
    page = all_entries[start:end]

    for entry in page:
        strip_generated_fields_for_list(entry)

    has_more = start > 0
    next_cursor = None
    if has_more:
        next_cursor = base64.urlsafe_b64encode(str(start).encode()).decode()

    return schemas.TaskLogsResponse(
        task_id=task.id,
        source="redis",
        entries=page,
        next_cursor=next_cursor,
        has_more=has_more,
    )
