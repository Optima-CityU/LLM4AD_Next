"""LLM 凭据代理 broker。

为演化任务下发的每个 provider 发放一次性代理 token，并把该 provider 的**真实
凭据**（加密后）存于 Redis。容器内代码只持有不透明的代理 token，经
:mod:`app.api.llm4ad.llm_proxy` 反向代理调用大模型，真实 api_key/auth_token
始终留在 backend 侧，绝不进入隔离容器。

设计要点：

- token 用 :func:`secrets.token_urlsafe` 生成，不可预测、与凭据无任何可逆关系；
  容器即便读到 token 也无法从中还原凭据。
- 真实凭据经 :func:`app.core.encryption.encrypt_secret`（Fernet）加密后存入
  Redis；即使 Redis 落盘快照（RDB/AOF）泄漏也只是密文，且容器本就无 Redis 访问。
- 代理转发时一次 Redis ``GET`` + 解密即可拿到全部凭据，**无需再查数据库**。
- 每个 token 设 TTL（默认覆盖任务最长运行时长）；同时把 token 记入按 task_id
  归集的集合，任务结束即可批量吊销。密文留在 Redis（而非容器），故删除即时生效；
  即便漏吊销，TTL 也兜底失效。

复用 :mod:`app.core.redis` 的同步客户端（Redis DB/2）。
"""

from __future__ import annotations

import json
import secrets
import uuid

from loguru import logger

from app.core.encryption import decrypt_secret, encrypt_secret
from app.core.redis import get_sync_redis

# 单个代理 token 的映射记录（值为加密后的凭据 blob）。
_TOKEN_PREFIX = "llmproxy:token:"
# 按 task_id 归集本任务发放的全部 token，便于结束时批量吊销。
_TASK_TOKENS_PREFIX = "llmproxy:task:"
# Per-user reverse index used to revoke every token when an account is deleted.
_USER_TOKENS_PREFIX = "llmproxy:user:"


def _token_key(token: str) -> str:
    """构造 token 映射在 Redis 中的 key。"""
    return f"{_TOKEN_PREFIX}{token}"


def _task_tokens_key(task_id: str | uuid.UUID) -> str:
    """构造按任务归集 token 集合的 Redis key。"""
    return f"{_TASK_TOKENS_PREFIX}{task_id}"


def _user_tokens_key(user_id: str | uuid.UUID) -> str:
    return f"{_USER_TOKENS_PREFIX}{user_id}"


def issue_token(
    *,
    user_id: str | uuid.UUID,
    task_id: str | uuid.UUID,
    ttl: int,
    provider_type: str,
    base_url: str,
    api_key: str,
    auth_token: str = "",
    model: str = "",
    timeout: float = 60.0,
) -> str:
    """发放一次性代理 token，并把加密后的真实凭据写入 Redis。

    Args:
        user_id: 归属用户 ID（审计用）。
        task_id: 归属任务 ID，用于结束时批量吊销。
        ttl: token 有效期（秒）。
        provider_type: 真实供应商类型（``openai`` / ``anthropic`` / ``openai_compatible``）。
        base_url: 真实上游 base_url（含内置 provider 已替换的 access token）。
        api_key: 真实 api_key。
        auth_token: 真实 auth_token（部分 anthropic 网关使用）。
        model: 模型名（审计用）。
        timeout: 转发到上游时的超时（秒）。

    Returns:
        生成的代理 token（URL-safe 随机串）。
    """
    token = secrets.token_urlsafe(32)
    payload = {
        "type": provider_type,
        "base_url": base_url,
        "api_key": api_key,
        "auth_token": auth_token,
        "model": model,
        "timeout": timeout,
        "user_id": str(user_id),
        "task_id": str(task_id),
    }
    blob = encrypt_secret(json.dumps(payload))
    r = get_sync_redis()
    pipe = r.pipeline()
    pipe.set(_token_key(token), blob, ex=ttl)
    # 反向集合：记录本任务发放的所有 token，集合本身也设 TTL 兜底回收。
    task_key = _task_tokens_key(task_id)
    pipe.sadd(task_key, token)
    pipe.expire(task_key, ttl)
    user_key = _user_tokens_key(user_id)
    pipe.sadd(user_key, token)
    pipe.expire(user_key, ttl)
    pipe.execute()
    return token


def resolve_token(token: str) -> dict | None:
    """校验代理 token 并解密还原真实凭据。

    Args:
        token: 待校验的代理 token。

    Returns:
        解密后的凭据 payload（含 type/base_url/api_key/auth_token/...），token
        不存在、已过期或解密失败时返回 ``None``。
    """
    if not token:
        return None
    try:
        blob = get_sync_redis().get(_token_key(token))
    except Exception:
        logger.warning("解析代理 token 时访问 Redis 失败", exc_info=True)
        return None
    if not blob:
        return None
    # decrypt_secret 对损坏/密钥不匹配的密文 fail-closed 返回空串。
    plaintext = decrypt_secret(blob)
    if not plaintext:
        return None
    try:
        return json.loads(plaintext)
    except (ValueError, TypeError):
        return None


def revoke_task_tokens(task_id: str | uuid.UUID) -> int:
    """吊销某任务发放的全部代理 token。

    任务正常结束或被停止时调用。读取按任务归集的 token 集合，删除每个 token
    映射及集合本身。幂等：集合不存在时返回 0。

    Args:
        task_id: 业务任务 ID。

    Returns:
        被删除的 token 数量。
    """
    try:
        r = get_sync_redis()
        task_key = _task_tokens_key(task_id)
        tokens = r.smembers(task_key)
        if not tokens:
            r.delete(task_key)
            return 0
        pipe = r.pipeline()
        for token in tokens:
            pipe.delete(_token_key(token))
        pipe.delete(task_key)
        pipe.execute()
        return len(tokens)
    except Exception:
        logger.warning(f"吊销任务 {task_id} 的代理 token 失败", exc_info=True)
        return 0


def revoke_user_tokens(user_id: str | uuid.UUID, *, raise_on_error: bool = False) -> int:
    """Revoke every proxy token indexed to a user; safe to call repeatedly."""
    try:
        r = get_sync_redis()
        user_key = _user_tokens_key(user_id)
        tokens = r.smembers(user_key)
        pipe = r.pipeline()
        for token in tokens:
            pipe.delete(_token_key(token))
        pipe.delete(user_key)
        pipe.execute()
        return len(tokens)
    except Exception:
        logger.warning(f"吊销用户 {user_id} 的代理 token 失败", exc_info=True)
        if raise_on_error:
            raise
        return 0
