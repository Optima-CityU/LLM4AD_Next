"""Embedding 供应商服务层。"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import HTTPException
from llm4ad.config.app import EmbeddingConfig, TaskSpecificConfig
from llm4ad.orchestrator.embedding_client import EmbeddingClient
from sqlalchemy import or_
from sqlmodel import Session, func, select

from app import models
from app.schemas import embedding_provider as schemas
from app.schemas.provider import _MASKED_SECRET

JINA_DEFAULT_BASE_URL = "https://api.jinaai.cn/v1"
JINA_DEFAULT_MODEL = "jina-embeddings-v4"
JINA_DEFAULT_DIM = 2048


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _require_http_url(value: Any, *, field_label: str) -> None:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"{field_label}必须是有效的 HTTP(S) API 地址")


def _normalize_provider_data(data: dict[str, Any]) -> dict[str, Any]:
    """Apply provider defaults and validate mode-specific fields."""
    provider_type = data.get("type", models.EmbeddingProviderType.JINA)
    mode = data.get("mode", models.EmbeddingMode.SHARED)

    def _reject_model_list(*field_names: str) -> None:
        for field_name in field_names:
            value = data.get(field_name)
            if isinstance(value, str) and ";" in value:
                raise HTTPException(
                    status_code=400,
                    detail="Embedding 配置每个字段只能配置单个模型，不支持使用 ; 配置多个模型或负载均衡",
                )

    _reject_model_list("model", "text_model", "code_model")

    if provider_type == models.EmbeddingProviderType.JINA:
        data["mode"] = models.EmbeddingMode.SHARED
        data["base_url"] = data.get("base_url") or JINA_DEFAULT_BASE_URL
        data["model"] = data.get("model") or JINA_DEFAULT_MODEL
        data["dim"] = data.get("dim") or JINA_DEFAULT_DIM
        _require_http_url(data["base_url"], field_label="Embedding API 地址")
        if not (data.get("api_key") or data.get("auth_token")):
            raise HTTPException(status_code=400, detail="Jina embedding 必须配置 API Key 或 Auth Token")
        data["text_type"] = models.EmbeddingProviderType.JINA
        data["code_type"] = models.EmbeddingProviderType.JINA
        data["text_task"] = data.get("text_task") or "text-matching"
        data["code_task"] = data.get("code_task") or "code.passage"
        return data

    if provider_type == models.EmbeddingProviderType.MOCK:
        data["mode"] = models.EmbeddingMode.SHARED
        data["model"] = data.get("model") or "mock"
        data["text_type"] = models.EmbeddingProviderType.MOCK
        data["code_type"] = models.EmbeddingProviderType.MOCK
        return data

    if mode != models.EmbeddingMode.SPLIT:
        raise HTTPException(status_code=400, detail="非 Jina embedding 必须分别配置 text/code 模型")
    if not (data.get("text_model") and data.get("code_model")):
        raise HTTPException(status_code=400, detail="分流模式必须同时配置 text/code 模型")

    default_task_type = (
        models.EmbeddingProviderType.OPENAI_COMPATIBLE
        if provider_type == models.EmbeddingProviderType.LOCAL
        else provider_type
    )
    data["text_type"] = data.get("text_type") or default_task_type
    data["code_type"] = data.get("code_type") or default_task_type
    text_type = data["text_type"]
    code_type = data["code_type"]
    if text_type == models.EmbeddingProviderType.LOCAL:
        text_type = models.EmbeddingProviderType.OPENAI_COMPATIBLE
        data["text_type"] = text_type
    if code_type == models.EmbeddingProviderType.LOCAL:
        code_type = models.EmbeddingProviderType.OPENAI_COMPATIBLE
        data["code_type"] = code_type
    if text_type in (models.EmbeddingProviderType.OPENAI_COMPATIBLE, models.EmbeddingProviderType.JINA) and not data.get("text_base_url"):
        if text_type == models.EmbeddingProviderType.JINA:
            data["text_base_url"] = JINA_DEFAULT_BASE_URL
        else:
            raise HTTPException(status_code=400, detail="Text embedding 必须配置 API 地址")
    if code_type in (models.EmbeddingProviderType.OPENAI_COMPATIBLE, models.EmbeddingProviderType.JINA) and not data.get("code_base_url"):
        if code_type == models.EmbeddingProviderType.JINA:
            data["code_base_url"] = JINA_DEFAULT_BASE_URL
        else:
            raise HTTPException(status_code=400, detail="Code embedding 必须配置 API 地址")
    if not (data.get("text_api_key") or data.get("text_auth_token")):
        raise HTTPException(status_code=400, detail="Text embedding 必须配置 API Key 或 Auth Token")
    if not (data.get("code_api_key") or data.get("code_auth_token")):
        raise HTTPException(status_code=400, detail="Code embedding 必须配置 API Key 或 Auth Token")
    data["text_task"] = data.get("text_task") or "text-matching"
    data["code_task"] = data.get("code_task") or "code.passage"
    return data


def _embedding_config_from_provider_data(data: dict[str, Any]) -> EmbeddingConfig:
    data = _normalize_provider_data(data.copy())
    provider_type = data.get("type", models.EmbeddingProviderType.JINA)
    mode = data.get("mode", models.EmbeddingMode.SHARED)

    if mode == models.EmbeddingMode.SPLIT or provider_type == models.EmbeddingProviderType.LOCAL:
        text_type = data.get("text_type") or (
            models.EmbeddingProviderType.OPENAI_COMPATIBLE
            if provider_type == models.EmbeddingProviderType.LOCAL
            else provider_type
        )
        code_type = data.get("code_type") or (
            models.EmbeddingProviderType.OPENAI_COMPATIBLE
            if provider_type == models.EmbeddingProviderType.LOCAL
            else provider_type
        )
        return EmbeddingConfig(
            type="local",
            dim=data.get("dim") or JINA_DEFAULT_DIM,
            timeout=data.get("timeout") or 60.0,
            embedding_func_max_async=data.get("embedding_func_max_async") or 2,
            text_config=TaskSpecificConfig(
                type=_enum_value(text_type),
                api_key=data.get("text_api_key") or "",
                auth_token=data.get("text_auth_token") or "",
                base_url=data.get("text_base_url") or "",
                model=data.get("text_model") or data.get("model") or "",
                timeout=data.get("timeout") or 60.0,
                task=data.get("text_task") or None,
            ),
            code_config=TaskSpecificConfig(
                type=_enum_value(code_type),
                api_key=data.get("code_api_key") or "",
                auth_token=data.get("code_auth_token") or "",
                base_url=data.get("code_base_url") or "",
                model=data.get("code_model") or data.get("model") or "",
                timeout=data.get("timeout") or 60.0,
                task=data.get("code_task") or None,
            ),
        )

    return EmbeddingConfig(
        type=_enum_value(provider_type),
        api_key=data.get("api_key") or "",
        auth_token=data.get("auth_token") or "",
        base_url=data.get("base_url") or None,
        model=data.get("model") or "",
        text_task=data.get("text_task") or None,
        code_task=data.get("code_task") or None,
        dim=data.get("dim") or JINA_DEFAULT_DIM,
        timeout=data.get("timeout") or 60.0,
        embedding_func_max_async=data.get("embedding_func_max_async") or 2,
    )


async def _run_embedding_connectivity_test(
    config: EmbeddingConfig,
    task_type: Literal["text", "code"],
    sample: str | None,
) -> schemas.EmbeddingProviderTestResponse:
    client = EmbeddingClient(config)
    try:
        text = sample or (
            "LLM4AD embedding connectivity test"
            if task_type == "text"
            else "def llm4ad_embedding_connectivity_test(x):\n    return x"
        )
        vector = await client.run_single(text, task_type=task_type)
        dimension = len(vector)
        is_zero_vector = dimension > 0 and all(value == 0 for value in vector)
        if dimension == 0 or is_zero_vector:
            return schemas.EmbeddingProviderTestResponse(
                success=False,
                message="Embedding 测试未返回有效向量，请检查模型名称、API 地址或凭据",
                task_type=task_type,
                dimension=dimension,
                sample=vector[:5],
            )
        return schemas.EmbeddingProviderTestResponse(
            success=True,
            message=f"{task_type} embedding 测试成功，返回 {dimension} 维向量",
            task_type=task_type,
            dimension=dimension,
            sample=vector[:5],
        )
    except Exception as exc:
        return schemas.EmbeddingProviderTestResponse(
            success=False,
            message=f"Embedding 测试失败：{exc}",
            task_type=task_type,
        )
    finally:
        await client.shutdown()


async def test_embedding_provider_connectivity(
    request: schemas.EmbeddingProviderTestRequest,
) -> schemas.EmbeddingProviderTestResponse:
    data = request.model_dump(exclude={"task_type", "sample"})
    config = _embedding_config_from_provider_data(data)
    return await _run_embedding_connectivity_test(config, request.task_type, request.sample)


async def test_stored_embedding_provider_connectivity(
    db: Session,
    provider_id: uuid.UUID,
    current_user: models.User,
    request: schemas.EmbeddingProviderTestByIdRequest,
) -> schemas.EmbeddingProviderTestResponse:
    provider = get_embedding_provider_with_auth(db, provider_id, current_user)
    update_data: dict[str, Any] = request.model_dump(exclude_unset=True, exclude={"task_type", "sample"})
    for secret_field in (
        "api_key",
        "auth_token",
        "text_api_key",
        "text_auth_token",
        "code_api_key",
        "code_auth_token",
    ):
        if update_data.get(secret_field) == _MASKED_SECRET:
            update_data.pop(secret_field)

    data = provider.model_dump()
    data.update(update_data)
    config = _embedding_config_from_provider_data(data)
    return await _run_embedding_connectivity_test(config, request.task_type, request.sample)


def get_embedding_provider_with_auth(
    db: Session,
    provider_id: uuid.UUID,
    current_user: models.User,
) -> models.EmbeddingProvider:
    provider = db.get(models.EmbeddingProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="embedding 供应商配置不存在")
    is_visible_builtin = provider.is_builtin and provider.visible_to_all
    if (
        not current_user.is_superuser
        and provider.user_id != current_user.id
        and not is_visible_builtin
    ):
        raise HTTPException(status_code=403, detail="无权访问该 embedding 供应商配置")
    return provider


def create_embedding_provider(
    db: Session,
    provider_in: models.EmbeddingProviderBase,
    user_id: uuid.UUID,
) -> models.EmbeddingProvider:
    data = _normalize_provider_data(provider_in.model_dump())
    data["is_builtin"] = False
    data["visible_to_all"] = False
    provider = models.EmbeddingProvider(**data, user_id=user_id)
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def list_embedding_providers(
    db: Session,
    user_id: uuid.UUID,
    skip: int,
    limit: int,
) -> tuple[list[models.EmbeddingProvider], int]:
    builtin_visible_clause = (
        models.EmbeddingProvider.is_builtin.is_(True)  # type: ignore[union-attr]
        & models.EmbeddingProvider.visible_to_all.is_(True)  # type: ignore[union-attr]
    )
    query = select(models.EmbeddingProvider).where(
        or_(models.EmbeddingProvider.user_id == user_id, builtin_visible_clause),
    )
    total = db.exec(select(func.count()).select_from(query.subquery())).one()
    query = query.order_by(
        models.EmbeddingProvider.is_builtin.desc(),
        models.EmbeddingProvider.created_time.asc(),
    )
    page_query = query.offset(skip).limit(limit) if limit > 0 else query
    providers = db.exec(page_query).all()
    return list(providers), total


def update_embedding_provider(
    db: Session,
    provider_id: uuid.UUID,
    current_user: models.User,
    update_data: dict[str, Any],
) -> models.EmbeddingProvider:
    provider = get_embedding_provider_with_auth(db, provider_id, current_user)
    if provider.is_builtin:
        raise HTTPException(status_code=403, detail="内置 embedding 供应商不可修改")

    for secret_field in (
        "api_key",
        "auth_token",
        "text_api_key",
        "text_auth_token",
        "code_api_key",
        "code_auth_token",
    ):
        if update_data.get(secret_field) == _MASKED_SECRET:
            update_data.pop(secret_field)

    data = provider.model_dump()
    data.update(update_data)
    data = _normalize_provider_data(data)
    for field, value in data.items():
        if hasattr(provider, field):
            setattr(provider, field, value)
    provider.updated_time = datetime.now(UTC)
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def delete_embedding_provider(
    db: Session,
    provider_id: uuid.UUID,
    current_user: models.User,
) -> models.Message:
    provider = get_embedding_provider_with_auth(db, provider_id, current_user)
    if provider.is_builtin:
        raise HTTPException(status_code=403, detail="内置 embedding 供应商不可删除")
    default_models = db.exec(
        select(models.UserDefaultModel).where(
            models.UserDefaultModel.embedding_provider_id == provider_id,
        ),
    ).all()
    for default_model in default_models:
        default_model.embedding_provider_id = None
        default_model.embedding_enabled = False
        default_model.updated_time = datetime.now(UTC)
        db.add(default_model)
    db.delete(provider)
    db.commit()
    return models.Message(message="embedding 供应商配置已删除")
