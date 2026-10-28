"""
项目（Project）请求/响应 Schema。

定义项目 CRUD 操作所需的数据验证和序列化模型。
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import ProjectBase

# ---- 请求 Schema ----


class ProjectCreate(ProjectBase):
    """项目创建请求。"""

    name: str
    description: str | None = None
    icon: str | None = None


class ProjectUpdate(BaseModel):
    """项目更新请求（所有字段均可选）。"""

    name: str | None = None
    description: str | None = None
    icon: str | None = None


# ---- 响应 Schema ----


class ProjectResponse(BaseModel):
    """项目基础响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_time: datetime
    updated_time: datetime
    name: str
    description: str | None
    icon: str | None
    user_id: uuid.UUID


class ProjectPackageImportResponse(BaseModel):
    """Identifiers of a newly imported project and its configured first task."""

    project_id: uuid.UUID
    task_id: uuid.UUID
    task_name: str
    evolution_type: str
    file_count: int


# ---- 分页 ----


class PaginationParams(BaseModel):
    """分页通用参数。"""

    skip: int = 0
    limit: int = 100


class PaginatedProjectResponse(BaseModel):
    """项目分页响应。"""

    items: list[ProjectResponse]
    total: int
    skip: int
    limit: int
