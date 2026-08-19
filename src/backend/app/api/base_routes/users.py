"""
用户管理路由。

提供用户注册、登录用户个人信息管理、管理员用户 CRUD 等端点。
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import EmailStr
from sqlmodel import Field, SQLModel, col, delete, func, select

from app.api.deps import CurrentUser, SessionDep, require_superuser
from app.core.config import settings
from app.core.redis import delete_verify_code, get_verify_code, save_verify_code
from app.core.security import get_password_hash, verify_password
from app.crud import user as crud
from app.models import (
    Item,
    Message,
    UpdatePassword,
    User,
    UserCreate,
    UserPublic,
    UserRegister,
    UsersPublic,
    UserUpdate,
    UserUpdateMe,
)
from app.utils.email import (
    generate_new_account_email,
    generate_verify_code,
    generate_verify_email,
    send_email,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", dependencies=[require_superuser], response_model=UsersPublic)
def read_users(session: SessionDep, skip: int = 0, limit: int = 100) -> Any:
    """获取用户列表（仅超级管理员可用）。

    按创建时间倒序返回，并附带总记录数用于前端分页。

    Args:
        session: 数据库会话依赖。
        skip: 跳过的记录数。
        limit: 单页最大返回数量。

    Returns:
        UsersPublic: 包含用户列表与总数的响应对象。
    """
    count_statement = select(func.count()).select_from(User)
    count = session.exec(count_statement).one()
    statement = select(User).order_by(User.created_at.desc()).offset(skip).limit(limit)
    users = session.exec(statement).all()
    return UsersPublic(data=users, count=count)


@router.post("/", dependencies=[require_superuser], response_model=UserPublic)
def create_user(*, session: SessionDep, user_in: UserCreate) -> Any:
    """管理员创建新用户，创建成功后发送欢迎邮件。

    管理员创建的账号默认 email_verified=True，无需用户再次进行邮箱验证；
    若邮件功能未启用或未提供邮箱则跳过邮件发送步骤。

    Args:
        session: 数据库会话依赖。
        user_in: 用户创建请求体。

    Returns:
        UserPublic: 新创建的用户公开数据。

    Raises:
        HTTPException: 邮箱已被注册（400）。
    """
    user = crud.get_user_by_email(session=session, email=user_in.email)
    if user:
        raise HTTPException(status_code=400, detail="该邮箱已被注册")
    user_in.email_verified = True
    user = crud.create_user(session=session, user_create=user_in)
    if settings.emails_enabled and user_in.email:
        email_data = generate_new_account_email(
            email_to=user_in.email, username=user_in.email, password=user_in.password
        )
        send_email(email_to=user_in.email, subject=email_data.subject, html_content=email_data.html_content)
    return user


@router.patch("/me", response_model=UserPublic)
def update_user_me(*, session: SessionDep, user_in: UserUpdateMe, current_user: CurrentUser) -> Any:
    """更新当前登录用户的个人信息。

    若修改邮箱，需确保新邮箱未被其他账号占用。

    Args:
        session: 数据库会话依赖。
        user_in: 用户更新请求体。
        current_user: 当前登录用户。

    Returns:
        UserPublic: 更新后的用户数据。

    Raises:
        HTTPException: 新邮箱已被其他账号注册（409）。
    """
    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != current_user.id:
            raise HTTPException(status_code=409, detail="该邮箱已被注册")
    user_data = user_in.model_dump(exclude_unset=True)
    current_user.sqlmodel_update(user_data)
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user


@router.patch("/me/password", response_model=Message)
def update_password_me(*, session: SessionDep, body: UpdatePassword, current_user: CurrentUser) -> Any:
    """修改当前登录用户的密码。

    需校验旧密码正确且新密码与旧密码不同。

    Args:
        session: 数据库会话依赖。
        body: 包含旧/新密码的请求体。
        current_user: 当前登录用户。

    Returns:
        Message: 修改结果提示。

    Raises:
        HTTPException: 当前密码错误或新旧密码相同（400）。
    """
    verified, _ = verify_password(body.current_password, current_user.hashed_password)
    if not verified:
        raise HTTPException(status_code=400, detail="当前密码错误")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    hashed_password = get_password_hash(body.new_password)
    current_user.hashed_password = hashed_password
    session.add(current_user)
    session.commit()
    return Message(message="密码修改成功")


@router.get("/me", response_model=UserPublic)
def read_user_me(current_user: CurrentUser) -> Any:
    """获取当前登录用户信息。"""
    return current_user


@router.delete("/me", response_model=Message)
def delete_user_me(session: SessionDep, current_user: CurrentUser) -> Any:
    """删除当前登录用户账号（超级管理员不可自删）。

    Args:
        session: 数据库会话依赖。
        current_user: 当前登录用户。

    Returns:
        Message: 删除结果提示。

    Raises:
        HTTPException: 超级管理员尝试删除自己（403）。
    """
    if current_user.is_superuser:
        raise HTTPException(status_code=403, detail="超级管理员不能删除自己")
    from app.services import knowledge_cleanup

    cleanup_job = knowledge_cleanup.prepare_user_cleanup_job(session, current_user.id)
    session.add(cleanup_job)
    session.delete(current_user)
    session.commit()
    knowledge_cleanup.run_or_schedule_cleanup(cleanup_job.id)
    return Message(message="用户已删除")


class VerifyEmailRequest(SQLModel):
    """邮箱验证码校验请求。"""

    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class ResendVerifyCodeRequest(SQLModel):
    """重新发送验证码请求。"""

    email: EmailStr


class ResendVerifyCodeResponse(SQLModel):
    """重新发送验证码响应，当 SKIP_EMAIL_VERIFICATION 开启时额外返回验证码。"""

    message: str = ""
    verify_code: str | None = None


class UserRegisterResponse(UserPublic):
    """注册响应，当 SKIP_EMAIL_VERIFICATION 开启时额外返回验证码。"""

    verify_code: str | None = None


def _send_verify_code(email: str) -> str:
    """生成并发送邮箱验证码，开发环境则打印验证码到控制台。

    Returns:
        生成的验证码字符串。
    """
    code = generate_verify_code()
    ttl = settings.EMAIL_VERIFY_CODE_EXPIRE_MINUTES * 60
    save_verify_code(email, code, ttl)
    if settings.emails_enabled:
        email_data = generate_verify_email(email_to=email, code=code)
        send_email(email_to=email, subject=email_data.subject, html_content=email_data.html_content)
    return code


@router.post("/signup", response_model=UserRegisterResponse)
def register_user(session: SessionDep, user_in: UserRegister) -> Any:
    """用户自助注册，注册后发送邮箱验证码。

    用户创建后 email_verified 默认为 False，需通过验证码完成邮箱验证后才能登录。
    当 SKIP_EMAIL_VERIFICATION 配置为 True 时，响应中额外返回 verify_code 供前端自动填充。

    Args:
        session: 数据库会话依赖。
        user_in: 注册请求体。

    Returns:
        UserRegisterResponse: 新注册用户的公开数据，可能包含验证码。

    Raises:
        HTTPException: 邮箱已被注册（400）；未同意隐私协议（400）。
    """
    user = crud.get_user_by_email(session=session, email=user_in.email)
    if user:
        raise HTTPException(status_code=400, detail="该邮箱已被注册")
    if not user_in.privacy_policy_accepted:
        raise HTTPException(status_code=400, detail="请先同意隐私协议")
    user_create = UserCreate.model_validate(user_in)
    user_create.privacy_policy_accepted = True
    user_create.privacy_policy_accepted_at = datetime.now(UTC)
    user = crud.create_user(session=session, user_create=user_create)
    code = _send_verify_code(user.email)

    response = UserRegisterResponse.model_validate(user)
    if settings.SKIP_EMAIL_VERIFICATION:
        response.verify_code = code
    return response


@router.post("/verify-email", response_model=Message)
def verify_email(session: SessionDep, body: VerifyEmailRequest) -> Any:
    """校验邮箱验证码，验证通过后标记用户邮箱已验证。

    Args:
        session: 数据库会话依赖。
        body: 包含邮箱和 6 位验证码的请求体。

    Returns:
        Message: 验证结果提示。

    Raises:
        HTTPException: 用户不存在（404）；验证码错误或已过期（400）；邮箱已验证（400）。
    """
    user = crud.get_user_by_email(session=session, email=body.email)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="邮箱已验证，无需重复操作")

    stored_code = get_verify_code(body.email)
    if not stored_code or stored_code != body.code:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    user.email_verified = True
    session.add(user)
    session.commit()
    delete_verify_code(body.email)
    return Message(message="邮箱验证成功")


@router.post("/resend-verify-code", response_model=ResendVerifyCodeResponse)
def resend_verify_code(session: SessionDep, body: ResendVerifyCodeRequest) -> Any:
    """重新发送邮箱验证码。

    当 SKIP_EMAIL_VERIFICATION 配置为 True 时，响应中额外返回 verify_code 供前端自动填充。

    Args:
        session: 数据库会话依赖。
        body: 包含邮箱的请求体。

    Returns:
        ResendVerifyCodeResponse: 发送结果提示，可能包含验证码。

    Raises:
        HTTPException: 用户不存在（404）；邮箱已验证（400）。
    """
    user = crud.get_user_by_email(session=session, email=body.email)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="邮箱已验证，无需重复操作")

    code = _send_verify_code(body.email)
    response = ResendVerifyCodeResponse(message="验证码已发送，请查收邮件")
    if settings.SKIP_EMAIL_VERIFICATION:
        response.verify_code = code
    return response


@router.get("/{user_id}", response_model=UserPublic)
def read_user_by_id(user_id: uuid.UUID, session: SessionDep, current_user: CurrentUser) -> Any:
    """根据 ID 获取用户信息。

    普通用户只能查看自己；超级管理员可查看所有用户。

    Args:
        user_id: 目标用户 ID。
        session: 数据库会话依赖。
        current_user: 当前登录用户。

    Returns:
        UserPublic: 目标用户的公开数据。

    Raises:
        HTTPException: 当前用户无权查看其他用户（403）；用户不存在（404）。
    """
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == current_user.id:
        return user
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="权限不足")
    return user


@router.patch("/{user_id}", dependencies=[require_superuser], response_model=UserPublic)
def update_user(*, session: SessionDep, user_id: uuid.UUID, user_in: UserUpdate) -> Any:
    """管理员更新指定用户信息。

    若修改邮箱，需校验新邮箱未被其他账号占用。

    Args:
        session: 数据库会话依赖。
        user_id: 目标用户 ID。
        user_in: 用户更新请求体。

    Returns:
        UserPublic: 更新后的用户数据。

    Raises:
        HTTPException: 用户不存在（404）；新邮箱已被其他账号注册（409）。
    """
    db_user = session.get(User, user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="该用户不存在")
    if user_in.email:
        existing_user = crud.get_user_by_email(session=session, email=user_in.email)
        if existing_user and existing_user.id != user_id:
            raise HTTPException(status_code=409, detail="该邮箱已被注册")
    db_user = crud.update_user(session=session, db_user=db_user, user_in=user_in)
    return db_user


@router.delete("/{user_id}", dependencies=[require_superuser])
def delete_user(session: SessionDep, current_user: CurrentUser, user_id: uuid.UUID) -> Message:
    """管理员删除指定用户（不可删除自己）。

    删除前会先级联删除该用户名下的所有 Item 数据。

    Args:
        session: 数据库会话依赖。
        current_user: 当前登录的超级管理员。
        user_id: 待删除的目标用户 ID。

    Returns:
        Message: 删除结果提示。

    Raises:
        HTTPException: 用户不存在（404）；尝试删除自己（403）。
    """
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == current_user.id:
        raise HTTPException(status_code=403, detail="超级管理员不能删除自己")
    from app.services import knowledge_cleanup

    cleanup_job = knowledge_cleanup.prepare_user_cleanup_job(session, user.id)
    session.add(cleanup_job)
    # 先删除用户的所有条目
    statement = delete(Item).where(col(Item.owner_id) == user_id)
    session.exec(statement)  # type: ignore
    session.delete(user)
    session.commit()
    knowledge_cleanup.run_or_schedule_cleanup(cleanup_job.id)
    return Message(message="用户已删除")
