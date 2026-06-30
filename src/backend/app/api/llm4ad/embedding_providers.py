"""Embedding 供应商管理路由。"""

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, SessionDep
from app.models import Message
from app.schemas import embedding_provider as schemas
from app.services import embedding_provider_service

router = APIRouter(prefix="/embedding-providers", tags=["llm4ad.embedding-providers"])


@router.post(
    "/test",
    response_model=schemas.EmbeddingProviderTestResponse,
    summary="测试 embedding 供应商配置连通性",
)
async def test_embedding_provider(
    provider_test: schemas.EmbeddingProviderTestRequest,
    _current_user: CurrentUser,
) -> schemas.EmbeddingProviderTestResponse:
    return await embedding_provider_service.test_embedding_provider_connectivity(provider_test)


@router.post(
    "/",
    response_model=schemas.EmbeddingProviderResponse,
    status_code=201,
    summary="创建 embedding 供应商配置",
)
def create_embedding_provider(
    provider_in: schemas.EmbeddingProviderCreate,
    db: SessionDep,
    current_user: CurrentUser,
) -> schemas.EmbeddingProviderResponse:
    return embedding_provider_service.create_embedding_provider(db, provider_in, current_user.id)


@router.get(
    "/",
    response_model=schemas.PaginatedEmbeddingProviderResponse,
    summary="分页查询 embedding 供应商配置列表",
)
def list_embedding_providers(
    db: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=0, le=200),
) -> schemas.PaginatedEmbeddingProviderResponse:
    providers, total = embedding_provider_service.list_embedding_providers(
        db,
        current_user.id,
        skip,
        limit,
    )
    return schemas.PaginatedEmbeddingProviderResponse(
        items=providers,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{provider_id}",
    response_model=schemas.EmbeddingProviderResponse,
    summary="获取单个 embedding 供应商配置详情",
)
def get_embedding_provider(
    db: SessionDep,
    current_user: CurrentUser,
    provider_id: uuid.UUID,
) -> schemas.EmbeddingProviderResponse:
    return embedding_provider_service.get_embedding_provider_with_auth(db, provider_id, current_user)


@router.post(
    "/{provider_id}/test",
    response_model=schemas.EmbeddingProviderTestResponse,
    summary="测试已存储 embedding 供应商配置连通性",
)
async def test_stored_embedding_provider(
    db: SessionDep,
    current_user: CurrentUser,
    provider_id: uuid.UUID,
    provider_test: schemas.EmbeddingProviderTestByIdRequest,
) -> schemas.EmbeddingProviderTestResponse:
    return await embedding_provider_service.test_stored_embedding_provider_connectivity(
        db,
        provider_id,
        current_user,
        provider_test,
    )


@router.patch(
    "/{provider_id}",
    response_model=schemas.EmbeddingProviderResponse,
    summary="更新 embedding 供应商配置",
)
def update_embedding_provider(
    db: SessionDep,
    current_user: CurrentUser,
    provider_id: uuid.UUID,
    provider_update: schemas.EmbeddingProviderUpdate,
) -> schemas.EmbeddingProviderResponse:
    update_data = provider_update.model_dump(exclude_unset=True)
    return embedding_provider_service.update_embedding_provider(
        db,
        provider_id,
        current_user,
        update_data,
    )


@router.delete("/{provider_id}", summary="删除 embedding 供应商配置")
def delete_embedding_provider(
    db: SessionDep,
    current_user: CurrentUser,
    provider_id: uuid.UUID,
) -> Message:
    return embedding_provider_service.delete_embedding_provider(db, provider_id, current_user)
