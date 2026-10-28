"""
项目管理路由。

提供项目的创建、查询、更新、删除等端点。
所有端点需要用户登录，项目按用户隔离。
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile

from app.api.deps import CurrentUser, SessionDep
from app.models import Message
from app.schemas import project as schemas
from app.services import project_service

# tags 加前缀防止前端 OpenAPI 重名冲突
router = APIRouter(prefix="/projects", tags=["llm4ad.projects"])


@router.post("/import-package", response_model=schemas.ProjectPackageImportResponse, status_code=201)
def import_project_package(
    db: SessionDep,
    current_user: CurrentUser,
    file: Annotated[UploadFile, File(description="Cloud task ZIP containing one project folder")],
    name: Annotated[str | None, Form(max_length=255)] = None,
    description: Annotated[str | None, Form(max_length=255)] = None,
    language: Annotated[Literal["en", "zh"], Form()] = "en",
):
    """Import source files and configuration without starting evolution.

    Args:
        db: Request-scoped database session.
        current_user: Authenticated project owner.
        file: Uploaded cloud package.
        name: Optional project name override.
        description: Optional project description override.
        language: Preferred language for the imported description.

    Returns:
        Identifiers of the created project and task.
    """
    from app.services.project_package_service import import_project_package as import_package

    return import_package(db, current_user, file.file, name=name, description=description, language=language)


@router.post(
    "/",
    response_model=schemas.ProjectResponse,
    status_code=201,
    summary="创建新项目（属于当前用户）",
)
def create_project(
    project_in: schemas.ProjectCreate, db: SessionDep, current_user: CurrentUser
):
    """创建一个新项目，自动关联到当前登录用户。

    Args:
        project_in: 项目创建请求体，包含名称与描述。
        db: 数据库会话依赖。
        current_user: 当前登录用户，作为项目所有者。

    Returns:
        ProjectResponse: 新创建项目的响应数据。
    """
    return project_service.create_project(
        db, project_in.name, project_in.description, current_user.id
    )


@router.get(
    "/",
    response_model=schemas.PaginatedProjectResponse,
    summary="分页查询项目列表",
)
def list_projects(
    db: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=0, le=200),
    sort: str = Query(
        "created_time",
        pattern="^(created_time|name)$",
        description="排序字段，支持 created_time / name",
    ),
    order: str = Query(
        "desc",
        pattern="^(asc|desc)$",
        description="排序方向，asc 升序 / desc 降序",
    ),
    q: str | None = Query(
        None,
        max_length=255,
        description="按项目名称或描述模糊搜索（不区分大小写），为空时返回全部",
    ),
):
    """分页查询当前用户的所有项目。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于范围过滤。
        skip: 跳过的记录数，用于分页起点。
        limit: 单页最大返回数量，范围 [0, 200]。
        sort: 排序字段，默认 ``created_time``。
        order: 排序方向，默认 ``desc``。
        q: 名称/描述模糊搜索关键字，``None`` 或空白时不过滤。

    Returns:
        PaginatedProjectResponse: 分页结果，包含数据项与总数。
    """
    projects, total = project_service.list_projects(
        db, current_user.id, skip, limit, sort=sort, order=order, q=q
    )
    return schemas.PaginatedProjectResponse(
        items=projects, total=total, skip=skip, limit=limit
    )


@router.get(
    "/{project_id}",
    response_model=schemas.ProjectResponse,
    summary="获取单个项目详情",
)
def get_project(
    db: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
):
    """获取项目详情。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于权限校验。
        project_id: 项目 ID。

    Returns:
        ProjectResponse: 项目详情。
    """
    return project_service.get_project_with_auth(db, project_id, current_user)


@router.patch(
    "/{project_id}",
    response_model=schemas.ProjectResponse,
    summary="更新项目信息",
)
def update_project(
    db: SessionDep,
    current_user: CurrentUser,
    project_id: uuid.UUID,
    project_update: schemas.ProjectUpdate,
):
    """更新项目的名称或描述。

    仅包含显式提供的字段，未传字段不会被覆盖。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于权限校验。
        project_id: 待更新的项目 ID。
        project_update: 项目更新请求体。

    Returns:
        ProjectResponse: 更新后的项目数据。
    """
    update_data = project_update.model_dump(exclude_unset=True)
    return project_service.update_project(db, project_id, current_user, update_data)


@router.delete("/{project_id}", summary="删除项目（同时删除其下的所有任务）")
def delete_project(
    db: SessionDep, current_user: CurrentUser, project_id: uuid.UUID
) -> Message:
    """删除项目及其关联的所有任务（级联删除）。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于权限校验。
        project_id: 待删除的项目 ID。

    Returns:
        Message: 删除结果提示。
    """
    return project_service.delete_project(db, project_id, current_user)
