"""
用户模型。

定义用户数据库模型及其相关的请求/响应 Schema，
包括用户创建、更新、注册、密码修改等操作所需的数据结构。
"""

import uuid
from datetime import UTC, datetime
from typing import Optional

from pydantic import EmailStr
from sqlalchemy import DateTime
from sqlmodel import Field, Relationship, SQLModel

from app.models.base import TimeMixin
from app.models.rbac import UserRoleLink

# ---- 基础 Schema ----


class UserBase(SQLModel):
    """用户基础 Schema，定义用户的公共字段。"""

    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    email_verified: bool = False
    full_name: str | None = Field(default=None, max_length=255)
    privacy_policy_accepted: bool = False
    privacy_policy_accepted_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )


# ---- 请求 Schema ----


class UserCreate(UserBase):
    """用户创建请求（管理员创建用户）。"""

    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    """用户自注册请求。"""

    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    privacy_policy_accepted: bool = Field(default=False)


class UserUpdate(UserBase):
    """用户更新请求（管理员更新用户）。"""

    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    """用户自行更新个人信息请求。"""

    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    """用户修改密码请求。"""

    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# ---- 数据库模型 ----


class User(UserBase, TimeMixin, table=True):
    """用户数据库模型。"""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    # 关联关系
    roles: list["Role"] = Relationship(back_populates="users", link_model=UserRoleLink)  # type: ignore[name-defined]  # noqa: F821
    items: list["Item"] = Relationship(back_populates="owner", cascade_delete=True)  # type: ignore[name-defined]  # noqa: F821
    projects: list["Project"] = Relationship(back_populates="user", cascade_delete=True)  # type: ignore[name-defined]  # noqa: F821
    providers: list["LLMProvider"] = Relationship(back_populates="user", cascade_delete=True)  # type: ignore[name-defined]  # noqa: F821
    embedding_providers: list["EmbeddingProvider"] = Relationship(cascade_delete=True)  # type: ignore[name-defined]  # noqa: F821
    default_model: Optional["UserDefaultModel"] = Relationship(back_populates="user", cascade_delete=True)  # type: ignore[name-defined]  # noqa: F821


# ---- 响应 Schema ----


class UserPublic(UserBase):
    """用户公开信息响应（隐藏密码等敏感字段）。"""

    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    """用户列表响应，包含分页总数。"""

    data: list[UserPublic]
    count: int
