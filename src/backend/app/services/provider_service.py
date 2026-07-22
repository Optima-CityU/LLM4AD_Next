"""
LLM 供应商服务层。

封装供应商相关的业务逻辑，包括权限校验、CRUD 操作等。
路由层调用本模块的函数，实现关注点分离。
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import or_
from sqlmodel import Session, func, select

from app import models
from app.models import Message
from app.schemas.provider import (
    _MASKED_SECRET,
    ProviderTestRequest,
    ProviderTestResponse,
)

logger = logging.getLogger(__name__)


def _builtin_visible_clause():
    """构造"内置 + 全员可见"的过滤条件。"""
    return (
        models.LLMProvider.is_builtin.is_(True)  # type: ignore[union-attr]
        & models.LLMProvider.visible_to_all.is_(True)  # type: ignore[union-attr]
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


def list_providers(db: Session, user_id: uuid.UUID, skip: int, limit: int) -> tuple[list[models.LLMProvider], int]:
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
    return list(providers), total


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
        available_models = [m.strip() for m in provider.model.split(";") if m.strip()]
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

    if provider.is_builtin and base_url and access_token:
        base_url = base_url.replace("{accessToken}", access_token)

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
        return ProviderTestResponse(success=False, message=f"连接失败: {e}")
