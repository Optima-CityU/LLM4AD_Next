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


@router.get("/operations/summary")
def get_admin_operations_summary(
    db: SessionDep,
    _current_user: CurrentActiveSuperuserUser,
) -> dict[str, Any]:
    """Return operations summary statistics."""
    return admin_analytics_service.build_operations_summary(db)


@router.get("/operations/tasks")
def get_admin_operations_tasks(
    db: SessionDep,
    _current_user: CurrentActiveSuperuserUser,
) -> dict[str, Any]:
    """Return operations task statistics."""
    return admin_analytics_service.build_operations_tasks(db)


@router.get("/operations/feedback")
def get_admin_operations_feedback(
    db: SessionDep,
    _current_user: CurrentActiveSuperuserUser,
) -> dict[str, Any]:
    """Return operations feedback statistics."""
    return admin_analytics_service.build_operations_feedback(db)


@router.get("/operations/litellm")
def get_admin_operations_litellm(
    db: SessionDep,
    token: TokenDep,
    _current_user: CurrentActiveSuperuserUser,
) -> dict[str, Any]:
    """Return operations LiteLLM quota statistics."""
    return admin_analytics_service.build_operations_litellm(db, token)


@router.get("/visitors/plausible")
def get_admin_visitors_plausible(
    _current_user: CurrentActiveSuperuserUser,
    range: str = Query("30d", pattern="^(7d|30d|91d)$"),
) -> dict[str, Any]:
    """Return visitor Plausible statistics."""
    return admin_analytics_service.build_visitors_plausible(range)


@router.get("/visitors/github")
def get_admin_visitors_github(
    _current_user: CurrentActiveSuperuserUser,
) -> dict[str, Any]:
    """Return visitor GitHub statistics."""
    return admin_analytics_service.build_visitors_github()
