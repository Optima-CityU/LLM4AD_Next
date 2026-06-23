"""
LLM 供应商管理路由。

提供供应商配置的创建、查询、更新、删除等端点。
所有端点需要用户登录，供应商配置按用户隔离。
"""

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, SessionDep, TokenDep
from app.models import Message
from app.schemas import provider as schemas
from app.services import provider_service

router = APIRouter(prefix="/providers", tags=["llm4ad.providers"])


@router.post(
    "/test",
    response_model=schemas.ProviderTestResponse,
    summary="测试供应商联通性",
)
async def test_provider(request: schemas.ProviderTestRequest, _current_user: CurrentUser):
    """Test LLM provider connectivity by sending a simple chat completion request.

    Args:
        request: Provider test request containing type, model, credentials, base_url, and prompt.

    Returns:
        ProviderTestResponse: Test result with success status, message, and LLM response.
    """
    return await provider_service.test_provider_connectivity(request)


@router.post(
    "/",
    response_model=schemas.ProviderResponse,
    status_code=201,
    summary="创建新供应商配置（属于当前用户）",
)
def create_provider(
    provider_in: schemas.ProviderCreate, db: SessionDep, current_user: CurrentUser
):
    """创建一个新的 LLM 供应商配置，自动关联到当前登录用户。

    Args:
        provider_in: 供应商创建请求体（含 base_url、api_key、model 等）。
        db: 数据库会话依赖。
        current_user: 当前登录用户，作为配置的所有者。

    Returns:
        ProviderResponse: 新创建的供应商配置。
    """
    return provider_service.create_provider(db, provider_in, current_user.id)


@router.get(
    "/",
    response_model=schemas.PaginatedProviderResponse,
    summary="分页查询供应商配置列表",
)
def list_providers(
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=0, le=200),
):
    """分页查询当前用户的所有供应商配置。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于范围过滤。
        skip: 跳过的记录数。
        limit: 单页最大返回数量，范围 [0, 200]。

    Returns:
        PaginatedProviderResponse: 分页结果，包含数据项与总数。
    """
    providers, total = provider_service.list_providers(
        db, current_user.id, skip, limit, token
    )
    return schemas.PaginatedProviderResponse(
        items=providers, total=total, skip=skip, limit=limit
    )


@router.get(
    "/builtin/quota",
    response_model=schemas.BuiltinProviderQuotaResponse,
    summary="查询当前用户的内置供应商额度",
)
def get_builtin_provider_quota(
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
):
    """Get current user's built-in provider quota when LiteLLM admin API is configured."""
    return provider_service.get_builtin_provider_quota(db, current_user, token)


@router.get(
    "/{provider_id}",
    response_model=schemas.ProviderResponse,
    summary="获取单个供应商配置详情",
)
def get_provider(
    db: SessionDep, current_user: CurrentUser, provider_id: uuid.UUID
):
    """获取供应商配置详情。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于权限校验。
        provider_id: 供应商配置 ID。

    Returns:
        ProviderResponse: 供应商配置详情。
    """
    return provider_service.get_provider_with_auth(db, provider_id, current_user)


@router.post(
    "/{provider_id}/test",
    response_model=schemas.ProviderTestResponse,
    summary="测试已存储供应商的联通性（按 ID）",
)
async def test_stored_provider(
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
    provider_id: uuid.UUID,
    request: schemas.ProviderTestByIdRequest,
):
    """Test connectivity of a stored provider using its persisted credentials.

    Intended for builtin providers whose credentials are masked in API
    responses; the caller only needs to supply the model name to test.

    Args:
        db: Database session dependency.
        current_user: Current logged-in user, used for access control.
        token: Current login token, used to substitute the ``{accessToken}``
            placeholder in a builtin provider's base_url.
        provider_id: ID of the stored provider to test.
        request: Test request containing the selected model name and prompt.

    Returns:
        ProviderTestResponse: Test result with success status, message, and LLM response.
    """
    return await provider_service.test_stored_provider_connectivity(
        db,
        provider_id,
        current_user,
        request.model,
        request.prompt,
        token,
        overrides={
            "api_key": request.api_key,
            "auth_token": request.auth_token,
            "base_url": request.base_url,
        },
    )


@router.patch(
    "/{provider_id}",
    response_model=schemas.ProviderResponse,
    summary="更新供应商配置",
)
def update_provider(
    db: SessionDep,
    current_user: CurrentUser,
    provider_id: uuid.UUID,
    provider_update: schemas.ProviderUpdate,
):
    """更新供应商配置信息。

    仅包含显式提供的字段，未传字段不会被覆盖。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于权限校验。
        provider_id: 待更新的供应商配置 ID。
        provider_update: 更新请求体。

    Returns:
        ProviderResponse: 更新后的供应商配置。
    """
    update_data = provider_update.model_dump(exclude_unset=True)
    return provider_service.update_provider(db, provider_id, current_user, update_data)


@router.delete("/{provider_id}", summary="删除供应商配置")
def delete_provider(
    db: SessionDep, current_user: CurrentUser, provider_id: uuid.UUID
) -> Message:
    """删除指定的供应商配置。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于权限校验。
        provider_id: 待删除的供应商配置 ID。

    Returns:
        Message: 删除结果提示。
    """
    return provider_service.delete_provider(db, provider_id, current_user)
