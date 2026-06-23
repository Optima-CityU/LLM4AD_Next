"""
用户默认模型配置路由。

提供用户默认模型配置的获取与更新端点。
每个用户有且仅有一条配置记录，所有端点需要用户登录。
"""

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep, TokenDep
from app.schemas import user_default_model as schemas
from app.services import user_default_model_service

router = APIRouter(prefix="/user-default-models", tags=["llm4ad.user-default-models"])


@router.get(
    "/",
    response_model=schemas.UserDefaultModelResponse,
    summary="获取当前用户的默认模型配置",
)
def get_user_default_model(
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
):
    """获取当前用户的默认模型配置，若不存在则自动创建。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户。

    Returns:
        UserDefaultModelResponse: 用户的默认模型配置。
    """
    return user_default_model_service.get_user_default_model(db, current_user.id, token)


@router.put(
    "/",
    response_model=schemas.UserDefaultModelResponse,
    summary="更新当前用户的默认模型配置",
)
def update_user_default_model(
    update_in: schemas.UserDefaultModelUpdate,
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
):
    """更新当前用户的默认模型配置。

    仅包含显式提供的字段，未传字段不会被覆盖。

    Args:
        update_in: 更新请求体，包含 provider_id 和 model_name 对。
        db: 数据库会话依赖。
        current_user: 当前登录用户。

    Returns:
        UserDefaultModelResponse: 更新后的默认模型配置。
    """
    update_data = update_in.model_dump(exclude_unset=True)
    return user_default_model_service.update_user_default_model(
        db,
        current_user.id,
        update_data,
        token,
    )
