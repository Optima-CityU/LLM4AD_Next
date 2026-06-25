"""管理员统计大屏 API。"""

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import CurrentActiveSuperuserUser, SessionDep, TokenDep
from app.services import admin_analytics_service

router = APIRouter(prefix="/admin/analytics", tags=["admin.analytics"])


@router.get("/overview")
def get_admin_analytics_overview(
    db: SessionDep,
    token: TokenDep,
    _current_user: CurrentActiveSuperuserUser,
    range: str = Query("30d", pattern="^(7d|30d|91d)$"),
) -> dict[str, Any]:
    """返回管理员运营大屏聚合数据。"""
    return admin_analytics_service.build_analytics_overview(
        db,
        access_token=token,
        date_range=range,
    )
