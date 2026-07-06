"""Redis 客户端模块。

提供同步与异步 Redis 客户端，用于基于 Redis Streams 的实时任务日志推送。
使用 Redis DB/2，避免与 Celery 的 DB/0（broker）和 DB/1（result backend）冲突。
"""

import json
import time
import uuid

import redis

from app.core.config import settings

TASK_LOGS_PREFIX = "task_logs:"
TASK_LOGS_DB = 2
TASK_LOGS_TTL = 7 * 24 * 3600  # 7 days


def _build_url() -> str:
    """构造任务日志专用的 Redis 连接 URL（含数据库编号）。"""
    return f"{settings.REDIS_BASE_URL}/{TASK_LOGS_DB}"


# ---- Sync client (Celery worker) ----

_sync_redis: redis.Redis | None = None


def get_sync_redis() -> redis.Redis:
    """获取同步 Redis 客户端单例（供 Celery worker 使用）。"""
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = redis.from_url(_build_url(), decode_responses=True)
    return _sync_redis


# ---- Async client (FastAPI SSE) ----

_async_redis = None
_async_redis_loop = None


async def get_async_redis():
    """获取异步 Redis 客户端单例（供 FastAPI SSE 接口使用）。

    Celery worker 每次任务创建新的 event loop，旧连接会绑定已关闭的 loop。
    检测 loop 变化并自动重建客户端以避免 "Event loop is closed" 错误。
    """
    import asyncio

    global _async_redis, _async_redis_loop
    current_loop = asyncio.get_running_loop()
    if _async_redis is not None and _async_redis_loop is not current_loop:
        try:
            await _async_redis.aclose()
        except Exception:
            pass
        _async_redis = None
    if _async_redis is None:
        import redis.asyncio as aioredis

        _async_redis = aioredis.from_url(_build_url(), decode_responses=True)
        _async_redis_loop = current_loop
    return _async_redis


# ---- Task log operations (Redis Stream) ----


def task_logs_key(task_id: str | uuid.UUID) -> str:
    """构造任务日志 Stream 在 Redis 中的 key。"""
    return f"{TASK_LOGS_PREFIX}{task_id}"


def push_log_entry(task_id: str | uuid.UUID, entry: dict) -> None:
    """将一条日志条目追加到 Redis Stream（XADD）。

    使用近似 MAXLEN 进行裁剪以限制内存占用；首次写入时设置 key TTL，
    避免 Stream 永久存在。

    Args:
        task_id: 业务任务 ID，用于定位 Stream key。
        entry: 待写入的日志条目字典，会被 JSON 序列化后存入。
    """
    try:
        r = get_sync_redis()
        key = task_logs_key(task_id)
        payload = json.dumps(entry, ensure_ascii=False, default=str)
        r.xadd(key, {"data": payload}, maxlen=settings.TASK_LOGS_MAXLEN, approximate=True)
        if r.ttl(key) == -1:
            r.expire(key, TASK_LOGS_TTL)
    except Exception:
        import logging

        logging.getLogger(__name__).warning("Failed to push log entry to Redis, task_id=%s", task_id, exc_info=True)


def read_all_logs(task_id: str | uuid.UUID) -> list[dict]:
    """读取指定任务 Stream 中的全部日志条目（XRANGE）。"""
    r = get_sync_redis()
    key = task_logs_key(task_id)
    entries = r.xrange(key)
    return [json.loads(fields["data"]) for _entry_id, fields in entries]


def delete_task_logs(task_id: str | uuid.UUID) -> None:
    """删除指定任务的日志 Stream key。"""
    r = get_sync_redis()
    r.delete(task_logs_key(task_id))


# ---- Report generation ID & stream ----

REPORT_GEN_PREFIX = "report_gen:"
REPORT_GEN_TTL = 600  # 10 minutes safety TTL

REPORT_STREAM_PREFIX = "report_stream:"
REPORT_STREAM_TTL = 3600  # 1 hour
REPORT_STREAM_MAXLEN = 10000


def report_gen_key(task_id: str | uuid.UUID, report_type: str) -> str:
    """构造报告生成 ID 在 Redis 中的 key。"""
    return f"{REPORT_GEN_PREFIX}{task_id}:{report_type}"


def report_stream_key(task_id: str | uuid.UUID, report_type: str) -> str:
    """构造报告 SSE Stream 在 Redis 中的 key。"""
    return f"{REPORT_STREAM_PREFIX}{task_id}:{report_type}"


def set_report_generation_id(task_id: str | uuid.UUID, report_type: str, generation_id: str) -> None:
    """保存当前报告生成 ID，覆盖之前的值。

    用于在并发触发同类型报告生成时，识别仅最新一次的输出有效。
    """
    r = get_sync_redis()
    key = report_gen_key(task_id, report_type)
    r.set(key, generation_id, ex=REPORT_GEN_TTL)


def get_report_generation_id(task_id: str | uuid.UUID, report_type: str) -> str | None:
    """获取当前报告生成 ID，未设置时返回 None。"""
    r = get_sync_redis()
    return r.get(report_gen_key(task_id, report_type))


def clear_report_generation_id(task_id: str | uuid.UUID, report_type: str) -> None:
    """清除当前报告生成 ID。"""
    r = get_sync_redis()
    r.delete(report_gen_key(task_id, report_type))


def push_report_chunk(task_id: str | uuid.UUID, report_type: str, entry: dict) -> None:
    """向报告 Stream 追加一条增量数据（XADD）。

    使用近似 MAXLEN 限流，并在首次写入时设置 TTL。失败仅记录日志，
    不抛异常，避免影响主流程。
    """
    try:
        r = get_sync_redis()
        key = report_stream_key(task_id, report_type)
        payload = json.dumps(entry, ensure_ascii=False, default=str)
        r.xadd(key, {"data": payload}, maxlen=REPORT_STREAM_MAXLEN, approximate=True)
        if r.ttl(key) == -1:
            r.expire(key, REPORT_STREAM_TTL)
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "Push report chunk to Redis failed, task_id=%s, type=%s",
            task_id,
            report_type,
            exc_info=True,
        )


def delete_report_stream(task_id: str | uuid.UUID, report_type: str) -> None:
    """删除指定任务的报告 Stream key。"""
    r = get_sync_redis()
    r.delete(report_stream_key(task_id, report_type))


# ---- Chat tune generation ID & stream ----

CHAT_TUNE_GEN_PREFIX = "chat_tune_gen:"
CHAT_TUNE_GEN_TTL = 1800  # 30 分钟安全 TTL，覆盖单轮调参可能的较长耗时

CHAT_TUNE_STREAM_PREFIX = "chat_tune_stream:"
CHAT_TUNE_STREAM_TTL = 3600  # 1 小时
CHAT_TUNE_STREAM_MAXLEN = 10000


def chat_tune_gen_key(session_id: str | uuid.UUID, turn_id: str | uuid.UUID) -> str:
    """构造调参生成 ID 在 Redis 中的 key。"""
    return f"{CHAT_TUNE_GEN_PREFIX}{session_id}:{turn_id}"


def chat_tune_stream_key(
    session_id: str | uuid.UUID, turn_id: str | uuid.UUID
) -> str:
    """构造调参 SSE Stream 在 Redis 中的 key。"""
    return f"{CHAT_TUNE_STREAM_PREFIX}{session_id}:{turn_id}"


def set_chat_tune_generation_id(
    session_id: str | uuid.UUID, turn_id: str | uuid.UUID, generation_id: str
) -> None:
    """保存当前调参生成 ID，覆盖之前的值。

    用于在并发触发同一轮调参时，识别仅最新一次的输出有效。
    """
    r = get_sync_redis()
    key = chat_tune_gen_key(session_id, turn_id)
    r.set(key, generation_id, ex=CHAT_TUNE_GEN_TTL)


def get_chat_tune_generation_id(
    session_id: str | uuid.UUID, turn_id: str | uuid.UUID
) -> str | None:
    """获取当前调参生成 ID，未设置时返回 None。"""
    r = get_sync_redis()
    return r.get(chat_tune_gen_key(session_id, turn_id))


def clear_chat_tune_generation_id(
    session_id: str | uuid.UUID, turn_id: str | uuid.UUID
) -> None:
    """清除当前调参生成 ID。"""
    r = get_sync_redis()
    r.delete(chat_tune_gen_key(session_id, turn_id))


def push_chat_tune_chunk(
    session_id: str | uuid.UUID, turn_id: str | uuid.UUID, entry: dict
) -> None:
    """向调参 Stream 追加一条增量数据（XADD）。

    使用近似 MAXLEN 限流，并在首次写入时设置 TTL。失败仅记录日志，
    不抛异常，避免影响主流程。
    """
    try:
        r = get_sync_redis()
        key = chat_tune_stream_key(session_id, turn_id)
        payload = json.dumps(entry, ensure_ascii=False, default=str)
        r.xadd(
            key, {"data": payload}, maxlen=CHAT_TUNE_STREAM_MAXLEN, approximate=True
        )
        if r.ttl(key) == -1:
            r.expire(key, CHAT_TUNE_STREAM_TTL)
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "Push chat tune chunk to Redis failed, session_id=%s, turn_id=%s",
            session_id,
            turn_id,
            exc_info=True,
        )


def delete_chat_tune_stream(
    session_id: str | uuid.UUID, turn_id: str | uuid.UUID
) -> None:
    """删除指定调参轮次的 Stream key。"""
    r = get_sync_redis()
    r.delete(chat_tune_stream_key(session_id, turn_id))


# ---- Chat tune rate limit ----

CHAT_TUNE_RATE_LIMIT_PREFIX = "chat_tune_rl:"
CHAT_TUNE_RATE_LIMIT_COOLDOWN = 2  # 秒


def check_chat_tune_rate_limit(session_id: str | uuid.UUID) -> bool:
    """检查调参请求是否在冷却期内。

    使用 Redis SET NX + EX 实现简单的固定窗口限流。

    Returns:
        True 表示允许通过，False 表示应拒绝（仍在冷却期）。
    """
    r = get_sync_redis()
    key = f"{CHAT_TUNE_RATE_LIMIT_PREFIX}{session_id}"
    acquired = r.set(key, "1", nx=True, ex=CHAT_TUNE_RATE_LIMIT_COOLDOWN)
    return bool(acquired)


# ---- Email verification code ----

VERIFY_CODE_PREFIX = "email_verify:"


def _verify_code_key(email: str) -> str:
    """构造邮箱验证码在 Redis 中的 key。"""
    return f"{VERIFY_CODE_PREFIX}{email}"


def save_verify_code(email: str, code: str, ttl_seconds: int) -> None:
    """保存邮箱验证码，设置 TTL 自动过期。"""
    r = get_sync_redis()
    r.set(_verify_code_key(email), code, ex=ttl_seconds)


def get_verify_code(email: str) -> str | None:
    """获取邮箱验证码，不存在或过期返回 None。"""
    r = get_sync_redis()
    return r.get(_verify_code_key(email))


def delete_verify_code(email: str) -> None:
    """删除邮箱验证码。"""
    r = get_sync_redis()
    r.delete(_verify_code_key(email))


# ---- Code-Server 活跃时间追踪 ----

CODE_SERVER_ACTIVE_KEY = "code_server:last_active"


def touch_code_user_active(user_id: str | uuid.UUID, ts: float | None = None) -> None:
    """刷新某 code-server 用户的最后活跃时间戳。

    使用 ZSET 存储，便于按时间戳范围扫描出空闲用户；写入失败仅记录日志，
    不抛异常，避免影响认证主路径。

    Args:
        user_id: 用户 ID。
        ts: Unix 时间戳（秒），缺省取当前时间。
    """
    if ts is None:
        ts = time.time()
    try:
        r = get_sync_redis()
        r.zadd(CODE_SERVER_ACTIVE_KEY, {str(user_id): ts})
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "Failed to touch code-server active for user_id=%s", user_id, exc_info=True
        )


def pop_idle_code_users(threshold_ts: float) -> list[str]:
    """原子地取出并移除所有最后活跃时间早于阈值的用户 ID。

    通过 redis-py 默认事务（MULTI/EXEC）保证 zrangebyscore 和
    zremrangebyscore 同时生效，避免在两步之间用户被重新刷新而漏处理或误删。

    Args:
        threshold_ts: Unix 时间戳，活跃时间小于等于该值视为空闲。

    Returns:
        被弹出的空闲用户 ID 列表，可能为空。
    """
    r = get_sync_redis()
    pipe = r.pipeline()
    pipe.zrangebyscore(CODE_SERVER_ACTIVE_KEY, "-inf", threshold_ts)
    pipe.zremrangebyscore(CODE_SERVER_ACTIVE_KEY, "-inf", threshold_ts)
    members, _ = pipe.execute()
    return list(members)


# ---- 首页资讯缓存 ----
# wiki 是唯一真源，后台定时拉取并整体覆盖缓存；GET /news?lang=<..> 直接读该缓存。
# 无 TTL：若拉取长时间失败，宁可返回旧内容也不返回空，由 updated_at 让前端判断新鲜度。
# 按语种分 key —— 未配置对应语种 URL 时，key 不会被创建，读也返回 None。

NEWS_CACHE_PREFIX = "news:"


def _news_cache_key(lang: str) -> str:
    """构造某语种资讯缓存的 Redis key。"""
    return f"{NEWS_CACHE_PREFIX}{lang}"


def set_cached_news(lang: str, payload: dict) -> None:
    """整体覆盖某语种资讯缓存。

    Args:
        lang: 语种代码，如 "zh"、"en"。
        payload: 已可 JSON 序列化的字典，通常来自 ``NewsList.model_dump(mode="json")``。
    """
    try:
        r = get_sync_redis()
        r.set(_news_cache_key(lang), json.dumps(payload, ensure_ascii=False))
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "Failed to write news cache to Redis, lang=%s", lang, exc_info=True
        )


def get_cached_news(lang: str) -> dict | None:
    """读取某语种资讯缓存。缓存不存在或反序列化失败均返回 None。"""
    try:
        r = get_sync_redis()
        raw = r.get(_news_cache_key(lang))
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "Failed to read news cache from Redis, lang=%s", lang, exc_info=True
        )
        return None

