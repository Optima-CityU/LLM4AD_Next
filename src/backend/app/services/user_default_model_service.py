"""
用户默认模型配置服务层。

封装用户默认模型配置相关的业务逻辑，包括获取与更新操作。
每个用户有且仅有一条配置记录。
"""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from loguru import logger
from sqlmodel import Session, select

from app import models
from app.core.config import settings
from app.schemas.user_default_model import UserDefaultModelResponse

_MODEL_SLOT_PREFIXES = ("planner", "coder", "report", "other")


def build_default_init_kwargs(
    db: Session,
    user_id: uuid.UUID,
    access_token: str | None = None,
) -> dict:
    """构造新建 UserDefaultModel 时的初始字段。

    若存在面向所有用户可见的内置供应商，则四个槽位默认指向该供应商。
    模型名来自 LiteLLM 按当前用户/团队动态发现的可用模型；若配置了
    BUILTIN_PROVIDER_DEFAULT_MODEL，只有它仍在 LiteLLM 可用模型列表中才会被采用。

    Args:
        db: 数据库会话。
        user_id: 当前用户 ID。

    Returns:
        可直接传入 models.UserDefaultModel(**kwargs) 的字典。
    """
    init_kwargs: dict = {"user_id": user_id}
    init_kwargs.update(_build_default_slot_kwargs(db, user_id, access_token))
    init_kwargs.update(_build_default_embedding_kwargs(db))
    return init_kwargs


def _build_default_slot_kwargs(
    db: Session,
    user_id: uuid.UUID,
    access_token: str | None = None,
) -> dict:
    builtin, default_model, _available_models = _resolve_builtin_default_model(
        db,
        user_id,
        access_token,
    )
    if not builtin or not default_model:
        return {}

    slot_kwargs: dict = {}
    for prefix in _MODEL_SLOT_PREFIXES:
        slot_kwargs[f"{prefix}_provider_id"] = builtin.id
        slot_kwargs[f"{prefix}_model_name"] = default_model
    return slot_kwargs


def _resolve_builtin_default_model(
    db: Session,
    user_id: uuid.UUID,
    access_token: str | None = None,
) -> tuple[models.LLMProvider | None, str, list[str]]:
    stmt = (
        select(models.LLMProvider)
        .where(
            models.LLMProvider.is_builtin.is_(True),  # type: ignore[union-attr]
            models.LLMProvider.visible_to_all.is_(True),  # type: ignore[union-attr]
        )
        .order_by(models.LLMProvider.created_time.asc())
    )
    builtin = db.exec(stmt).first()
    if not builtin:
        return None, "", []

    from app.services.provider_service import fetch_builtin_provider_models

    try:
        dynamic_models = fetch_builtin_provider_models(
            builtin,
            access_token,
            user_id=str(user_id),
        )
    except Exception as exc:  # noqa: BLE001 - defaults should degrade to no default model
        logger.warning("Failed to refresh builtin default models: {}", exc.__class__.__name__)
        dynamic_models = []

    available_models = dynamic_models
    preferred_model = settings.BUILTIN_PROVIDER_DEFAULT_MODEL.strip()
    if preferred_model and preferred_model in available_models:
        default_model = preferred_model
    else:
        default_model = available_models[0] if available_models else ""
    return builtin, default_model, available_models


def _get_builtin_embedding_provider(db: Session) -> models.EmbeddingProvider | None:
    stmt = (
        select(models.EmbeddingProvider)
        .where(
            models.EmbeddingProvider.is_builtin.is_(True),  # type: ignore[union-attr]
            models.EmbeddingProvider.visible_to_all.is_(True),  # type: ignore[union-attr]
        )
        .order_by(models.EmbeddingProvider.created_time.asc())
    )
    return db.exec(stmt).first()


def _build_default_embedding_kwargs(db: Session) -> dict:
    builtin_embedding = _get_builtin_embedding_provider(db)
    if not builtin_embedding:
        return {}
    return {
        "embedding_enabled": True,
        "embedding_provider_id": builtin_embedding.id,
    }


def _fill_missing_default_slots(
    db: Session,
    config: models.UserDefaultModel,
    access_token: str | None = None,
) -> bool:
    """Fill empty default slots from the current built-in provider without overwriting user choices."""
    builtin, default_model, available_models = _resolve_builtin_default_model(
        db,
        config.user_id,
        access_token,
    )
    if not builtin or not default_model:
        changed = False
    else:
        changed = False
        available_model_set = set(available_models)
        for prefix in _MODEL_SLOT_PREFIXES:
            provider_field = f"{prefix}_provider_id"
            model_field = f"{prefix}_model_name"
            current_provider_id = getattr(config, provider_field)
            current_model_name = getattr(config, model_field)

            if current_provider_id is None:
                setattr(config, provider_field, builtin.id)
                setattr(config, model_field, default_model)
                changed = True
            elif current_provider_id == builtin.id and not current_model_name:
                setattr(config, model_field, default_model)
                changed = True
            elif (
                current_provider_id == builtin.id
                and current_model_name
                and available_model_set
                and current_model_name not in available_model_set
            ):
                logger.warning(
                    "Default {} model {} is no longer available for builtin provider {}; switching to {}.",
                    prefix,
                    current_model_name,
                    builtin.id,
                    default_model,
                )
                setattr(config, model_field, default_model)
                changed = True

    builtin_embedding = _get_builtin_embedding_provider(db)
    if builtin_embedding and config.embedding_provider_id is None:
        config.embedding_enabled = True
        config.embedding_provider_id = builtin_embedding.id
        changed = True

    return changed


def _enrich_with_provider_names(
    db: Session, config: models.UserDefaultModel,
) -> UserDefaultModelResponse:
    """Build response with provider name fields populated from DB."""
    provider_ids = [
        getattr(config, f"{prefix}_provider_id")
        for prefix in _MODEL_SLOT_PREFIXES
    ]
    unique_ids = {pid for pid in provider_ids if pid is not None}

    name_map: dict[uuid.UUID, str] = {}
    if unique_ids:
        stmt = select(models.LLMProvider.id, models.LLMProvider.name).where(
            models.LLMProvider.id.in_(unique_ids),  # type: ignore[union-attr]
        )
        for row in db.exec(stmt).all():
            name_map[row[0]] = row[1]  # type: ignore[index]

    data = {c: getattr(config, c) for c in config.__class__.model_fields}
    for prefix in _MODEL_SLOT_PREFIXES:
        pid = getattr(config, f"{prefix}_provider_id")
        data[f"{prefix}_provider_name"] = name_map.get(pid) if pid else None
    embedding_provider_id = getattr(config, "embedding_provider_id", None)
    data["embedding_provider_name"] = None
    if embedding_provider_id:
        embedding_provider = db.get(models.EmbeddingProvider, embedding_provider_id)
        if embedding_provider:
            data["embedding_provider_name"] = embedding_provider.name

    return UserDefaultModelResponse.model_validate(data)


def get_user_default_model(
    db: Session,
    user_id: uuid.UUID,
    access_token: str | None = None,
) -> UserDefaultModelResponse:
    """获取用户的默认模型配置，若不存在则自动创建。"""
    stmt = select(models.UserDefaultModel).where(
        models.UserDefaultModel.user_id == user_id,
    )
    config = db.exec(stmt).first()
    if not config:
        init_kwargs = build_default_init_kwargs(db, user_id, access_token)
        config = models.UserDefaultModel(**init_kwargs)
        db.add(config)
        db.commit()
        db.refresh(config)
    elif _fill_missing_default_slots(db, config, access_token):
        config.updated_time = datetime.now(UTC)
        db.add(config)
        db.commit()
        db.refresh(config)
    return _enrich_with_provider_names(db, config)


def update_user_default_model(
    db: Session,
    user_id: uuid.UUID,
    update_data: dict,
    access_token: str | None = None,
) -> UserDefaultModelResponse:
    """更新用户的默认模型配置。"""
    stmt = select(models.UserDefaultModel).where(
        models.UserDefaultModel.user_id == user_id,
    )
    config = db.exec(stmt).first()
    if not config:
        init_kwargs = build_default_init_kwargs(db, user_id, access_token)
        config = models.UserDefaultModel(**init_kwargs)
        db.add(config)
        db.commit()
        db.refresh(config)

    provider_id_fields = [
        "planner_provider_id",
        "coder_provider_id",
        "report_provider_id",
        "other_provider_id",
    ]
    providers_by_id: dict[uuid.UUID, models.LLMProvider] = {}
    for field in provider_id_fields:
        pid = update_data.get(field)
        if pid is not None:
            provider = db.get(models.LLMProvider, pid)
            if not provider:
                raise HTTPException(
                    status_code=404,
                    detail=f"供应商配置 {pid} 不存在",
                )
            is_visible_builtin = provider.is_builtin and provider.visible_to_all
            if provider.user_id != user_id and not is_visible_builtin:
                raise HTTPException(
                    status_code=403,
                    detail=f"无权使用供应商配置 {pid}",
                )
            providers_by_id[pid] = provider

    embedding_provider_id = update_data.get(
        "embedding_provider_id",
        getattr(config, "embedding_provider_id", None),
    )
    embedding_enabled = update_data.get(
        "embedding_enabled",
        getattr(config, "embedding_enabled", False),
    )
    if embedding_provider_id is not None:
        embedding_provider = db.get(models.EmbeddingProvider, embedding_provider_id)
        if not embedding_provider:
            raise HTTPException(
                status_code=404,
                detail=f"embedding 供应商配置 {embedding_provider_id} 不存在",
            )
        is_visible_builtin_embedding = (
            embedding_provider.is_builtin and embedding_provider.visible_to_all
        )
        if embedding_provider.user_id != user_id and not is_visible_builtin_embedding:
            raise HTTPException(
                status_code=403,
                detail=f"无权使用 embedding 供应商配置 {embedding_provider_id}",
            )
    elif embedding_enabled:
        raise HTTPException(
            status_code=400,
            detail="启用轨迹分析前请先选择 embedding 供应商配置",
        )

    config.sqlmodel_update(update_data)
    config.updated_time = datetime.now(UTC)
    db.add(config)
    db.commit()
    db.refresh(config)
    return _enrich_with_provider_names(db, config)
