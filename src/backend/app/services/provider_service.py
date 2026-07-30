"""
LLM 供应商服务层。

封装供应商相关的业务逻辑，包括权限校验、CRUD 操作等。
路由层调用本模块的函数，实现关注点分离。
"""

import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import or_
from sqlmodel import Session, func, select

from app import models
from app.core.config import settings
from app.models import Message
from app.schemas.provider import (
    _MASKED_SECRET,
    BuiltinProviderQuotaResponse,
    ProviderTestRequest,
    ProviderTestResponse,
)

logger = logging.getLogger(__name__)

_PROVIDER_MODEL_CACHE_TTL_SECONDS = 30 * 60
_LITELLM_MODEL_WILDCARDS = {"all-proxy-models", "all-team-models", "no-default-models"}
_provider_model_cache: dict[tuple[Any, ...], tuple[float, list[str]]] = {}
_provider_model_cache_lock = threading.Lock()


def _builtin_visible_clause():
    """构造"内置 + 全员可见"的过滤条件。"""
    return (
        models.LLMProvider.is_builtin.is_(True)  # type: ignore[union-attr]
        & models.LLMProvider.visible_to_all.is_(True)  # type: ignore[union-attr]
    )


def resolve_builtin_base_url(base_url: str | None, access_token: str | None = None) -> str | None:
    """Resolve runtime placeholders used by the built-in provider URL."""
    if not base_url:
        return base_url
    resolved = base_url
    replacements = {
        "{accessToken}": access_token or "",
        "${TEAM_ID}": settings.TEAM_ID,
        "{TEAM_ID}": settings.TEAM_ID,
        "{teamId}": settings.TEAM_ID,
    }
    for marker, value in replacements.items():
        resolved = resolved.replace(marker, value)
    return resolved


def create_builtin_gateway_access_token(
    user_id: uuid.UUID | str,
    *,
    ttl_seconds: int | None = None,
) -> str:
    """Create a task-scoped user token for background built-in model requests.

    HTTP-triggered model calls reuse the caller's access token. Background jobs
    do not retain that request context, so they need an equivalent token before
    resolving the built-in gateway URL. The token lives no longer than the LLM
    credential proxy token and is hidden behind that proxy during task execution.
    """
    from app.core.security import create_access_token

    return create_access_token(
        user_id,
        expires_delta=timedelta(
            seconds=ttl_seconds or settings.LLM_PROXY_TOKEN_TTL
        ),
    )


def get_provider_with_auth(db: Session, provider_id: uuid.UUID, current_user: models.User) -> models.LLMProvider:
    """获取供应商并校验当前用户的访问权限。"""
    provider = db.get(models.LLMProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="供应商配置不存在")
    is_visible_builtin = provider.is_builtin and provider.visible_to_all
    if (
        not current_user.is_superuser
        and provider.user_id != current_user.id
        and not is_visible_builtin
    ):
        raise HTTPException(status_code=403, detail="无权访问该供应商配置")
    return provider


def create_provider(db: Session, provider_in: models.LLMProviderBase, user_id: uuid.UUID) -> models.LLMProvider:
    """创建新供应商配置。"""
    data = provider_in.model_dump()
    # 禁止用户通过创建接口伪装为内置
    data["is_builtin"] = False
    data["visible_to_all"] = False
    db_provider = models.LLMProvider(**data, user_id=user_id)
    db.add(db_provider)
    db.commit()
    db.refresh(db_provider)
    return db_provider


def list_providers(
    db: Session,
    user_id: uuid.UUID,
    skip: int,
    limit: int,
    access_token: str | None = None,
) -> tuple[list[models.LLMProvider], int]:
    """分页查询用户的供应商配置列表，包含全员可见的内置供应商。"""
    query = select(models.LLMProvider).where(
        or_(models.LLMProvider.user_id == user_id, _builtin_visible_clause()),
    )
    total = db.exec(select(func.count()).select_from(query.subquery())).one()
    query = query.order_by(
        models.LLMProvider.is_builtin.desc(),  # 内置置顶
        models.LLMProvider.created_time.asc(),
    )
    page_query = query.offset(skip).limit(limit) if limit > 0 else query
    providers = db.exec(page_query).all()
    for provider in providers:
        if provider.is_builtin:
            dynamic_models = fetch_builtin_provider_models(
                provider,
                access_token,
                user_id=str(user_id),
            )
            if dynamic_models:
                provider.model = ";".join(dynamic_models)
    return list(providers), total


def fetch_builtin_provider_models(
    provider: models.LLMProvider,
    access_token: str | None = None,
    user_id: str | None = None,
) -> list[str]:
    """Fetch models allowed for the built-in provider team.

    Prefer the gateway-owned LiteLLM team metadata endpoint so over-budget users
    can still choose a model. The user-key /models endpoint remains a fallback
    for deployments without the gateway internal endpoint.
    """
    _ = user_id
    base_url = resolve_builtin_base_url(provider.base_url, access_token)
    team_models = _fetch_litellm_team_models_via_gateway(access_token)
    if team_models:
        return team_models
    return _fetch_builtin_provider_models_via_gateway(provider, base_url, access_token)


def _get_cached_provider_models(cache_key: tuple[Any, ...]) -> list[str] | None:
    now = time.monotonic()
    with _provider_model_cache_lock:
        cached = _provider_model_cache.get(cache_key)
        if cached is None:
            return None
        expires_at, models = cached
        if expires_at <= now:
            _provider_model_cache.pop(cache_key, None)
            return None
        return list(models)


def _set_cached_provider_models(cache_key: tuple[Any, ...], models: list[str]) -> None:
    if not models:
        return
    with _provider_model_cache_lock:
        _provider_model_cache[cache_key] = (
            time.monotonic() + _PROVIDER_MODEL_CACHE_TTL_SECONDS,
            list(models),
        )


def _clear_provider_model_cache() -> None:
    with _provider_model_cache_lock:
        _provider_model_cache.clear()


def _fetch_builtin_provider_models_via_gateway(
    provider: models.LLMProvider,
    base_url: str | None,
    access_token: str | None,
) -> list[str]:
    """Call the built-in gateway once so it can provision the current LiteLLM user/key."""
    if not base_url or not access_token:
        return []

    models_url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {provider.api_key or 'EMPTY'}"}
    try:
        with httpx.Client(timeout=8.0, trust_env=False) as client:
            response = client.get(models_url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 - model discovery must not break provider listing
        logger.warning(
            "Failed to fetch builtin provider models from %s: %s",
            _safe_url_for_log(models_url),
            exc.__class__.__name__,
        )
        return []

    discovered = _extract_llm_model_ids(payload)
    if not discovered:
        logger.warning(
            "Builtin provider models response from %s contained no chat models",
            _safe_url_for_log(models_url),
        )
    return discovered


def _fetch_litellm_team_models_via_gateway(access_token: str | None) -> list[str]:
    if not access_token:
        return []

    try:
        payload = _fetch_gateway_json("/internal/litellm/team/models", access_token)
    except Exception as exc:  # noqa: BLE001 - dynamic model discovery should degrade gracefully
        logger.warning("Failed to fetch LiteLLM team models through gateway: %s", exc.__class__.__name__)
        return []

    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return _normalize_model_ids(data)
    return []


def _normalize_model_ids(items: list[Any]) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for item in items:
        model_id = _extract_model_id(item)
        if not model_id:
            continue
        lowered = model_id.lower()
        if lowered in _LITELLM_MODEL_WILDCARDS:
            continue
        if model_id in seen or _is_embedding_or_non_llm_model(item, model_id):
            continue
        seen.add(model_id)
        models.append(model_id)
    return models


def _safe_url_for_log(url: str) -> str:
    try:
        parsed = httpx.URL(url)
        return str(parsed.copy_with(query=None, fragment=None))
    except Exception:  # noqa: BLE001
        return "<invalid-url>"


def _split_models(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [m.strip() for m in raw.split(";") if m.strip()]


def _extract_llm_model_ids(payload: Any) -> list[str]:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return []

    models: list[str] = []
    seen: set[str] = set()
    for item in data:
        model_id = _extract_model_id(item)
        if not model_id or model_id in seen:
            continue
        if _is_litellm_model_wildcard(model_id):
            continue
        if _is_embedding_or_non_llm_model(item, model_id):
            continue
        seen.add(model_id)
        models.append(model_id)
    return models


def _is_litellm_model_wildcard(model_id: str) -> bool:
    return model_id.strip().lower() in _LITELLM_MODEL_WILDCARDS


def _extract_model_id(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    value = item.get("id") or item.get("model_name") or item.get("model")
    return str(value).strip() if value else ""


def _is_embedding_or_non_llm_model(item: Any, model_id: str) -> bool:
    mode_values: list[str] = []
    if isinstance(item, dict):
        for key in ("mode", "model_type", "type"):
            value = item.get(key)
            if value:
                mode_values.append(str(value).lower())
        for nested_key in ("litellm_params", "model_info", "metadata"):
            nested = item.get(nested_key)
            if isinstance(nested, dict):
                value = nested.get("mode") or nested.get("model_type") or nested.get("type")
                if value:
                    mode_values.append(str(value).lower())
    if any(mode in {"embedding", "embeddings", "rerank", "image", "audio"} for mode in mode_values):
        return True

    lowered = model_id.lower()
    return any(
        keyword in lowered
        for keyword in (
            "embedding",
            "embeddings",
            "embed",
            "rerank",
            "bge-reranker",
            "jina-embeddings",
        )
    )


def get_builtin_provider_quota(
    db: Session,
    current_user: models.User,
    access_token: str | None = None,
) -> BuiltinProviderQuotaResponse:
    """Return current user's built-in LiteLLM quota when admin API is configured."""
    provider = db.exec(
        select(models.LLMProvider)
        .where(_builtin_visible_clause())
        .order_by(models.LLMProvider.created_time.asc())
    ).first()
    if not provider:
        return BuiltinProviderQuotaResponse(
            available=False,
            message="未配置内置供应商 / Built-in provider is not configured.",
        )

    base_response = BuiltinProviderQuotaResponse(
        provider_id=provider.id,
        provider_name=provider.name,
    )
    fetch_builtin_provider_models(provider, access_token, user_id=str(current_user.id))
    if not access_token:
        base_response.message = (
            "暂无法查询剩余额度：缺少访问令牌。"
            "Quota is unavailable because the access token is missing."
        )
        return base_response

    try:
        user_info = _fetch_current_litellm_quota_via_gateway(access_token)
    except Exception as exc:  # noqa: BLE001 - quota display should degrade gracefully
        logger.warning("Failed to fetch LiteLLM user quota through gateway: %s", exc)
        base_response.message = (
            "暂无法查询剩余额度，请稍后重试或联系管理员。"
            "Quota is unavailable. Please retry later or contact your administrator."
        )
        return base_response

    spend = _find_first_number(user_info, ("spend", "user_spend"))
    budget = _find_first_number(user_info, ("max_budget", "budget", "user_budget"))
    if spend is None and budget is None:
        base_response.message = (
            "LiteLLM 未返回可识别的额度信息，请联系管理员检查供应商配置。"
            "LiteLLM did not return recognizable quota fields. Contact your administrator."
        )
        return base_response

    remaining = budget - spend if budget is not None and spend is not None else None
    return BuiltinProviderQuotaResponse(
        available=True,
        provider_id=provider.id,
        provider_name=provider.name,
        spend=spend,
        budget=budget,
        remaining=remaining,
        message="额度信息已更新 / Quota information is up to date.",
    )


def fetch_litellm_user_quotas_via_gateway(db: Session, access_token: str) -> dict[str, Any]:
    """Return LiteLLM user quotas through the gateway admin endpoint."""
    gateway_base_url = settings.LITELLM_GATEWAY_BASE_URL.rstrip("/")
    if not gateway_base_url:
        return {
            "available": False,
            "items": [],
            "total": 0,
            "message": "LiteLLM gateway is not configured.",
        }

    url = f"{gateway_base_url}/internal/litellm/users/quotas"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        with httpx.Client(timeout=8.0, trust_env=False) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 - admin UI should show a controlled error
        logger.warning("Failed to fetch LiteLLM user quotas through gateway: %s", exc)
        return {
            "available": False,
            "items": [],
            "total": 0,
            "message": f"Failed to fetch LiteLLM user quotas through gateway: {exc}",
        }

    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        items = _attach_backend_user_full_names(db, payload["data"])
        return {
            "available": True,
            "items": items,
            "total": len(items),
            "message": str(payload.get("message") or "success"),
        }

    return {
        "available": False,
        "items": [],
        "total": 0,
        "message": "Gateway returned an unexpected LiteLLM user quota response.",
    }


def _attach_backend_user_full_names(db: Session, items: list[Any]) -> list[Any]:
    user_ids: set[uuid.UUID] = set()
    emails: set[str] = set()
    normalized_items: list[Any] = []

    for item in items:
        if not isinstance(item, dict):
            normalized_items.append(item)
            continue

        normalized_items.append(item)
        user_id = item.get("user_id")
        if isinstance(user_id, str):
            try:
                user_ids.add(uuid.UUID(user_id))
            except ValueError:
                pass

        email = item.get("user_email")
        if isinstance(email, str) and email:
            emails.add(email.lower())

    if not user_ids and not emails:
        return normalized_items

    clauses = []
    if user_ids:
        clauses.append(models.User.id.in_(user_ids))  # type: ignore[attr-defined]
    if emails:
        clauses.append(func.lower(models.User.email).in_(emails))  # type: ignore[attr-defined]

    users = db.exec(select(models.User).where(or_(*clauses))).all()
    users_by_id = {str(user.id): user for user in users}
    users_by_email = {user.email.lower(): user for user in users}

    for item in normalized_items:
        if not isinstance(item, dict):
            continue
        user = None
        user_id = item.get("user_id")
        if isinstance(user_id, str):
            user = users_by_id.get(user_id)
        email = item.get("user_email")
        if user is None and isinstance(email, str):
            user = users_by_email.get(email.lower())
        if user and user.full_name:
            item["full_name"] = user.full_name
        else:
            item.setdefault("full_name", None)

    return normalized_items


def _fetch_current_litellm_quota_via_gateway(access_token: str) -> dict[str, Any]:
    payload = _fetch_gateway_json("/internal/litellm/users/me/quota", access_token)
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        return data
    raise ValueError("Gateway returned an unexpected current LiteLLM quota response.")


def _fetch_gateway_json(path: str, access_token: str) -> Any:
    gateway_base_url = settings.LITELLM_GATEWAY_BASE_URL.rstrip("/")
    if not gateway_base_url:
        raise ValueError("LiteLLM gateway is not configured.")
    url = f"{gateway_base_url}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {access_token}"}
    with httpx.Client(timeout=8.0, trust_env=False) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


def _find_first_number(payload: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            parsed = _parse_number(value)
            if parsed is not None:
                return parsed
        for value in payload.values():
            parsed = _find_first_number(value, keys)
            if parsed is not None:
                return parsed
    elif isinstance(payload, list):
        for value in payload:
            parsed = _find_first_number(value, keys)
            if parsed is not None:
                return parsed
    return None


def _parse_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def update_provider(
    db: Session,
    provider_id: uuid.UUID,
    current_user: models.User,
    update_data: dict,
) -> models.LLMProvider:
    """更新供应商配置。内置供应商对所有 API 调用方均只读。"""
    provider = db.get(models.LLMProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="供应商配置不存在")
    if provider.is_builtin:
        raise HTTPException(status_code=409, detail="内置供应商不支持修改")
    if not current_user.is_superuser and provider.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权更新该供应商配置")

    # 防止通过 PATCH 把普通供应商升级为内置
    update_data.pop("is_builtin", None)
    update_data.pop("visible_to_all", None)

    # 凭据脱敏防线：前端可能回传占位符 sk-***（表示"未改动"），
    # 绝不能把占位符写成真实凭据，故丢弃等于占位符的凭据字段。
    for secret_field in ("api_key", "auth_token"):
        if update_data.get(secret_field) == _MASKED_SECRET:
            update_data.pop(secret_field)

    provider.sqlmodel_update(update_data)
    provider.updated_time = datetime.now(UTC)
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def delete_provider(db: Session, provider_id: uuid.UUID, current_user: models.User) -> Message:
    """删除供应商配置。内置供应商不可删。"""
    provider = db.get(models.LLMProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="供应商配置不存在")
    if provider.is_builtin:
        raise HTTPException(status_code=409, detail="内置供应商不支持删除")
    if not current_user.is_superuser and provider.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权删除该供应商配置")
    db.delete(provider)
    db.commit()
    return Message(message="供应商配置已删除")


async def test_provider_connectivity(request: ProviderTestRequest) -> ProviderTestResponse:
    """Test LLM provider connectivity by sending a simple chat completion request.

    Args:
        request: Provider test request containing type, model, credentials, and prompt.

    Returns:
        ProviderTestResponse with success status, message, and LLM response data.
    """
    provider_config = {
        "type": request.type.value,
        "api_key": request.api_key,
        "auth_token": request.auth_token,
        "base_url": request.base_url,
        "model": request.model,
        "timeout": 15,
        "max_retries": 0,
        "max_tokens": 100,
    }
    return await _run_connectivity_test(provider_config, request.prompt)


async def test_stored_provider_connectivity(
    db: Session,
    provider_id: uuid.UUID,
    current_user: models.User,
    model: str,
    prompt: str = "hi",
    access_token: str | None = None,
    overrides: dict | None = None,
) -> ProviderTestResponse:
    """Test connectivity of a stored provider using its persisted credentials.

    Useful for builtin providers, whose api_key/base_url are masked in API
    responses and therefore unavailable to the frontend. Credentials are read
    from the database; only the model name to test is supplied by the caller.

    When editing a user-owned provider, the frontend may pass field-level
    ``overrides`` (current form values): a non-empty override replaces the stored
    value, while an empty/omitted one falls back to the decrypted database value.
    This supports testing "new form value + existing stored value" together.
    Overrides are ignored for builtin providers, so a builtin gateway's secret
    can never be redirected to an arbitrary base_url.

    Args:
        db: Database session.
        provider_id: ID of the stored provider to test.
        current_user: Current user, used for access control.
        model: Model name to test. For builtin providers, it must belong to the
            provider's registered model list.
        prompt: Prompt sent to the provider.
        access_token: Current login token, used to substitute the ``{accessToken}``
            placeholder in a builtin provider's base_url.
        overrides: Optional field-level overrides (api_key/auth_token/base_url)
            from the edit form; applied only to non-builtin providers.

    Returns:
        ProviderTestResponse with success status, message, and LLM response data.
    """
    provider = get_provider_with_auth(db, provider_id, current_user)
    if provider.is_builtin:
        available_models = fetch_builtin_provider_models(
            provider,
            access_token,
            user_id=str(current_user.id),
        )
        if model not in available_models:
            raise HTTPException(status_code=400, detail="所选模型不属于该供应商")

    # 凭据默认取库中解密后的真实值（ORM 读取自动解密）
    api_key = provider.api_key
    auth_token = provider.auth_token
    base_url = provider.base_url

    # 字段级覆盖：仅对用户自有供应商生效，且仅当覆盖值非空时替换
    if overrides and not provider.is_builtin:
        if overrides.get("api_key"):
            api_key = overrides["api_key"]
        if overrides.get("auth_token"):
            auth_token = overrides["auth_token"]
        if overrides.get("base_url"):
            base_url = overrides["base_url"]

    if provider.is_builtin:
        base_url = resolve_builtin_base_url(base_url, access_token)

    provider_config = {
        "type": provider.type.value,
        "api_key": api_key,
        "auth_token": auth_token,
        "base_url": base_url,
        "model": model,
        "timeout": 15,
        "max_retries": 0,
        "max_tokens": 100,
    }
    return await _run_connectivity_test(provider_config, prompt)


async def _run_connectivity_test(provider_config: dict, prompt: str) -> ProviderTestResponse:
    """Run a single chat completion against the given provider config.

    Args:
        provider_config: Provider configuration dict accepted by ``BaseProvider.create``.
        prompt: Prompt sent to the provider.

    Returns:
        ProviderTestResponse with success status, message, and LLM response data.
    """
    from llm4ad.infra.provider.base import BaseProvider

    try:
        provider = BaseProvider.create(provider_config["type"], config=provider_config)
        result = await provider.generate(prompt, max_tokens=100)
        return ProviderTestResponse(success=True, message="连接成功", data=result.text)
    except Exception as e:
        logger.warning("Provider connectivity test failed: %s", e)
        return ProviderTestResponse(success=False, message=_format_connectivity_error(e))


def _format_connectivity_error(error: Exception) -> str:
    raw_message = str(error)
    normalized = raw_message.lower()
    if (
        "status=402" in normalized
        or "status 402" in normalized
        or "payment required" in normalized
        or "quota is exhausted" in normalized
        or "quota_exhausted" in normalized
    ):
        return (
            "连接失败: 内置模型免费额度已用尽，请联系管理员增加额度或切换其他供应商。"
            " Builtin model free quota has been exhausted. "
            "Please contact an administrator to increase quota or switch providers."
        )
    return f"连接失败: {raw_message}"
