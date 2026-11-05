"""科研工作区模型网关健康跟踪。

`llm_proxy` 把上游模型网关的失败（限流、断连、超时等）记录到 Redis 并向
``runtime-events`` SSE 流推送事件；同时防抖地把一条内联通知投递到 CloudCLI
运行时（``/internal/session-notice``），让失败原因直接出现在对话流里。
本模块所有函数都吞掉自身异常——监控路径绝不拖垮代理主链路。
"""

from __future__ import annotations

import json
import time
import uuid

import httpx
from loguru import logger

from app.core.redis import get_async_redis, get_sync_redis
from app.services.paper_workspace_runtime import (
    paper_workspace_container_name,
    paper_workspace_runtime_token,
)

# Redis 键前缀与默认参数。
_EVENT_PREFIX = "llm4ad:runtime:events"
_STATE_PREFIX = "llm4ad:runtime:state"
_NOTICE_LOCK_PREFIX = "llm4ad:runtime:notice-lock"
_STATE_TTL_SECONDS = 6 * 3600
_STREAM_MAXLEN = 200
# 同一会话两次内联通知的最小间隔（秒），避免 CLI 重试风暴刷屏。
_NOTICE_MIN_INTERVAL_SECONDS = 60
# 调用 CloudCLI 内部端点的超时。
_NOTICE_HTTP_TIMEOUT = 3.0

# 稳定失败原因码 → 中文标签（内联通知用）。
REASON_LABELS = {
    "rate_limited": "上游限流（429）",
    "auth": "上游凭据失效（401/403）",
    "upstream_error": "上游服务错误（5xx）",
    "timeout": "上游无响应（超时）",
    "disconnected": "上游连接中断",
    "other": "上游请求失败",
}


def runtime_event_key(workspace_id: str | uuid.UUID) -> str:
    """返回一个科研工作区运行时事件的 Redis Stream 键。"""
    return f"{_EVENT_PREFIX}:{workspace_id}"


def _state_key(workspace_id: str | uuid.UUID) -> str:
    return f"{_STATE_PREFIX}:{workspace_id}"


def _notice_lock_key(session_id: str) -> str:
    return f"{_NOTICE_LOCK_PREFIX}:{session_id}"


def _control_key(workspace_id: str, session_id: str) -> str:
    """Scope cancellation across API workers to one native conversation."""
    return f"{_STATE_PREFIX}:{workspace_id}:control:{session_id}"


def _request_cancelled(control: dict, request_started_at: float) -> bool:
    """Keep old requests cancelled when a new turn starts in the same session."""
    values = {
        key.decode() if isinstance(key, bytes) else key:
        value.decode() if isinstance(value, bytes) else value
        for key, value in control.items()
    }
    stopped = float(values.get("cancelled_at", 0) or 0)
    started = float(values.get("started_at", 0) or 0)
    return stopped > 0 and (stopped >= started or stopped >= request_started_at)


async def runtime_request_cancelled(workspace_id: str, session_id: str, request_started_at: float) -> bool:
    """Check shared turn cancellation without blocking the proxy event loop.

    Args:
        workspace_id: Authenticated research workspace.
        session_id: Native conversation associated with the credential token.
        request_started_at: Wall-clock start of this individual model request.

    Returns:
        Whether the request belongs to a cancelled turn.
    """
    try:
        client = await get_async_redis()
        return _request_cancelled(await client.hgetall(_control_key(workspace_id, session_id)), request_started_at)
    except Exception:
        logger.warning("Could not read research cancellation state for {}", workspace_id)
        return False


def record_runtime_chat_control(workspace_id: str, message: str | bytes) -> None:
    """Track authenticated chat starts and stops at the WebSocket boundary.

    Args:
        workspace_id: Workspace whose owner was authenticated by the proxy.
        message: Unmodified browser command.
    """
    try:
        payload = json.loads(message)
        if not isinstance(payload, dict):
            return
        command = payload.get("type")
        cancelled = command == "chat.abort"
        if not cancelled and command not in {"chat.send", "chat.edit-send"}:
            return
        session_id = payload.get("sessionId")
        if not isinstance(session_id, str) or not session_id.strip():
            return
        session_id = session_id.strip()
        client = get_sync_redis()
        timestamp = time.time()
        client.hset(_control_key(workspace_id, session_id), mapping={"cancelled_at" if cancelled else "started_at": timestamp})
        client.expire(_control_key(workspace_id, session_id), _STATE_TTL_SECONDS)
        state = client.hgetall(_state_key(workspace_id))
        owner = state.get("session_id", state.get(b"session_id"))
        if isinstance(owner, bytes):
            owner = owner.decode()
        if owner in {None, session_id}:
            client.delete(_state_key(workspace_id), f"{_state_key(workspace_id)}:failures", _notice_lock_key(session_id))
            _publish(workspace_id, {"type": "proxy_cancelled" if cancelled else "proxy_started", "session_id": session_id, "ts": timestamp})
    except (ValueError, TypeError, UnicodeDecodeError):
        return
    except Exception:
        logger.warning("Could not record research turn control for {}", workspace_id)


def classify_upstream_error(exception: Exception | None, status_code: int | None) -> str:
    """把一次上游失败归类为稳定的 reason 码。

    Args:
        exception: ``httpx`` 抛出的传输层异常（可为空）。
        status_code: 上游返回的 HTTP 状态码（可为空）。

    Returns:
        ``rate_limited`` / ``auth`` / ``upstream_error`` / ``timeout`` /
        ``disconnected`` / ``other`` 之一。
    """
    if status_code == 429:
        return "rate_limited"
    if status_code in (401, 403):
        return "auth"
    if status_code is not None and status_code >= 500:
        return "upstream_error"
    if isinstance(exception, (httpx.TimeoutException, TimeoutError)):
        return "timeout"
    if isinstance(exception, (httpx.NetworkError, httpx.RemoteProtocolError)):
        return "disconnected"
    text = str(exception or "").lower()
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "disconnected" in text or "connect" in text:
        return "disconnected"
    return "other"


def runtime_snapshot(workspace_id: str | uuid.UUID) -> dict:
    """读取当前失败状态快照（SSE ``connected`` 帧 / 前端恢复时用）。"""
    try:
        r = get_sync_redis()
        raw = r.hgetall(_state_key(workspace_id))
        if not raw:
            return {"state": "ok", "failures": 0}
        raw = {
            key.decode() if isinstance(key, bytes) else key:
            value.decode() if isinstance(value, bytes) else value
            for key, value in raw.items()
        }
        return {
            "state": "failing",
            "failures": int(raw.get("failures", 0) or 0),
            "reason": raw.get("reason", "other"),
            "status": int(raw.get("status", 0) or 0),
            "message": raw.get("message", ""),
            "ts": float(raw.get("ts", 0) or 0),
            "attempt": int(raw.get("attempt", 0) or 0),
            "max_attempts": int(raw.get("max_attempts", 0) or 0),
            "retrying": raw.get("retrying") == "1",
            "retry_in": float(raw.get("retry_in", 0) or 0),
        }
    except Exception:
        logger.warning("科研工作区运行时状态读取失败", exc_info=True)
        return {"state": "ok", "failures": 0}


def _publish(workspace_id: str | uuid.UUID, event: dict) -> None:
    """推送一条事件到 SSE 流；失败仅告警，绝不冒泡。"""
    try:
        r = get_sync_redis()
        key = runtime_event_key(workspace_id)
        r.xadd(key, {"data": json.dumps(event, ensure_ascii=False, default=str)}, maxlen=_STREAM_MAXLEN, approximate=True)
        if r.ttl(key) == -1:
            r.expire(key, _STATE_TTL_SECONDS)
    except Exception:
        logger.warning("科研工作区运行时事件推送失败 workspace=%s", workspace_id, exc_info=True)


def record_runtime_failure(
    workspace_id: str,
    session_id: str,
    *,
    reason: str,
    status_code: int | None,
    message: str,
    notify: bool = True,
    attempt: int = 0,
    max_attempts: int = 0,
    retrying: bool = False,
    retry_in: float = 0,
    request_started_at: float | None = None,
) -> int:
    """记录一次上游失败：计数 + 状态哈希 + SSE 事件 + 防抖内联通知。

    Args:
        notify: 是否投递会话内联通知。流式中途断流时传 False，避免每次断流都
            刷一条通知——重试后的请求阶段失败会再触发完整上报。
        attempt: Next attempt while retrying, or the failed terminal attempt.
        max_attempts: Total allowed attempts for this request.
        retrying: Whether a bounded automatic retry is scheduled.
        retry_in: Delay before the next attempt, in seconds.
        request_started_at: Request start used to discard late cancelled failures.

    Returns:
        当前连续失败次数（读取失败时返回 0）。
    """
    try:
        r = get_sync_redis()
        if request_started_at is not None and _request_cancelled(
            r.hgetall(_control_key(workspace_id, session_id)), request_started_at,
        ):
            return 0
        count = int(r.incr(f"{_state_key(workspace_id)}:failures"))
        r.expire(f"{_state_key(workspace_id)}:failures", _STATE_TTL_SECONDS)
        ts = time.time()
        r.hset(
            _state_key(workspace_id),
            mapping={
                "failures": count,
                "session_id": session_id,
                "reason": reason,
                "status": status_code or 0,
                "message": (message or "")[:500],
                "ts": ts,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "retrying": int(retrying),
                "retry_in": retry_in,
            },
        )
        r.expire(_state_key(workspace_id), _STATE_TTL_SECONDS)
    except Exception:
        logger.warning("科研工作区失败计数写入失败", exc_info=True)
        count = 0

    _publish(
        workspace_id,
        {
            "type": "proxy_error",
            "reason": reason,
            "status": status_code,
            "message": (message or "")[:500],
            "count": count,
            "ts": time.time(),
            "attempt": attempt,
            "max_attempts": max_attempts,
            "retrying": retrying,
            "retry_in": retry_in,
        },
    )
    if notify:
        label = REASON_LABELS.get(reason, REASON_LABELS["other"])
        _try_notify_session(
            workspace_id,
            session_id,
            summary=f"模型网关请求失败（第 {count} 次）：{label}。建议更换模型或稍后重试。",
            status="failed",
        )
    return count


def record_runtime_recovery(workspace_id: str, session_id: str) -> None:
    """上游恢复：清状态、推送恢复事件，若此前失败过则投递一条恢复通知。"""
    try:
        r = get_sync_redis()
        was_failing = bool(r.get(f"{_state_key(workspace_id)}:failures"))
        r.delete(_state_key(workspace_id), f"{_state_key(workspace_id)}:failures")
    except Exception:
        logger.warning("科研工作区恢复状态清理失败", exc_info=True)
        was_failing = False

    if was_failing:
        _publish(workspace_id, {"type": "proxy_recovered", "ts": time.time()})
        _try_notify_session(
            workspace_id,
            session_id,
            summary="模型网关已恢复，可以继续对话。",
            status="completed",
        )


def _try_notify_session(workspace_id: str, session_id: str, summary: str, status: str) -> bool:
    """防抖地调用 CloudCLI 的 ``/internal/session-notice`` 投递一条内联通知。

    Returns:
        是否真正发出调用（被限频时返回 False）。
    """
    try:
        r = get_sync_redis()
        if not r.set(_notice_lock_key(session_id), "1", nx=True, ex=_NOTICE_MIN_INTERVAL_SECONDS):
            return False
        url = f"http://{paper_workspace_container_name(workspace_id)}:3001/internal/session-notice"
        response = httpx.put(
            url,
            json={"sessionId": session_id, "summary": summary, "status": status},
            headers={"x-llm4ad-runtime-token": paper_workspace_runtime_token(workspace_id)},
            timeout=_NOTICE_HTTP_TIMEOUT,
        )
        return response.status_code == 200
    except Exception:
        logger.warning("科研工作区会话通知投递失败 workspace=%s", workspace_id, exc_info=True)
        return False
