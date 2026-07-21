"""
用户默认模型配置服务层。

封装用户默认模型配置相关的业务逻辑，包括获取与更新操作。
每个用户有且仅有一条配置记录。
"""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlmodel import Session, select

from app import models
from app.core.config import settings
from app.schemas.user_default_model import UserDefaultModelResponse

_MODEL_SLOT_PREFIXES = ("planner", "coder", "report", "other")


def build_default_init_kwargs(db: Session, user_id: uuid.UUID) -> dict:
    """构造新建 UserDefaultModel 时的初始字段。

    若存在面向所有用户可见的内置供应商，则四个槽位默认指向该供应商，
    模型名取 BUILTIN_PROVIDER_DEFAULT_MODEL，未配置则取 model 列表的第一个。

    Args:
        db: 数据库会话。
        user_id: 当前用户 ID。

    Returns:
        可直接传入 models.UserDefaultModel(**kwargs) 的字典。
    """
    init_kwargs: dict = {"user_id": user_id}
    stmt = (
        select(models.LLMProvider)
        .where(
            models.LLMProvider.is_builtin.is_(True),  # type: ignore[union-attr]
            models.LLMProvider.visible_to_all.is_(True),  # type: ignore[union-attr]
        )
        .order_by(models.LLMProvider.created_time.asc())
    )
    builtin = db.exec(stmt).first()
    if not builtin or not builtin.model:
        return init_kwargs
    default_model = settings.BUILTIN_PROVIDER_DEFAULT_MODEL.strip()
    if not default_model:
        first = builtin.model.split(";")[0].strip()
        default_model = first
    if not default_model:
        return init_kwargs
    for prefix in _MODEL_SLOT_PREFIXES:
        init_kwargs[f"{prefix}_provider_id"] = builtin.id
        init_kwargs[f"{prefix}_model_name"] = default_model
    return init_kwargs


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
    db: Session, user_id: uuid.UUID,
) -> UserDefaultModelResponse:
    """获取用户的默认模型配置，若不存在则自动创建。"""
    stmt = select(models.UserDefaultModel).where(
        models.UserDefaultModel.user_id == user_id,
    )
    config = db.exec(stmt).first()
    if not config:
        init_kwargs = build_default_init_kwargs(db, user_id)
        config = models.UserDefaultModel(**init_kwargs)
        db.add(config)
        db.commit()
        db.refresh(config)
    return _enrich_with_provider_names(db, config)


def update_user_default_model(
    db: Session,
    user_id: uuid.UUID,
    update_data: dict,
) -> UserDefaultModelResponse:
    """更新用户的默认模型配置。"""
    stmt = select(models.UserDefaultModel).where(
        models.UserDefaultModel.user_id == user_id,
    )
    config = db.exec(stmt).first()
    if not config:
        init_kwargs = build_default_init_kwargs(db, user_id)
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
            if embedding_enabled:
                raise HTTPException(
                    status_code=404,
                    detail=f"embedding 供应商配置 {embedding_provider_id} 不存在",
                )
            update_data["embedding_provider_id"] = None
        elif embedding_provider.user_id != user_id:
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
