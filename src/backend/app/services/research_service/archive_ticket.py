"""产物打包下载的短时票据。

背景：想让浏览器**自己去下**这个 zip（``<a href>`` / ``window.location``），才能用
上浏览器自带的下载面板；可原生导航带不了 ``Authorization`` 头，而接口又必须鉴权。
于是先用正常的带鉴权请求换一张短命票据，再把票据放进下载 URL 的查询串里。

票据是一枚 5 分钟有效的 JWT，复用 ``settings.SECRET_KEY`` 与 ``security.ALGORITHM``，
scope 标成 ``download`` —— 与 ``access`` / ``refresh`` / ``code`` 互不通用，即便被
人从浏览器历史或网关日志里捞到，也只能用来下这一份产物，且很快就过期。

**刻意不放进 ``app.core.security``**：那里的 ``scope`` Literal 是登录/刷新令牌的公共
契约，为下载票据去扩它会把一次性需求写进通用模块。这里自己签、自己验，现有登录、
刷新、code-server 三条链路一行都不用动。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import jwt
from fastapi import HTTPException, status
from sqlmodel import Session

from app.core.config import settings
from app.core.security import ALGORITHM
from app.models import User

# 票据有效期：只够发起一次下载。用户点完按钮到浏览器真正发出 GET 是同一秒内的事，
# 5 分钟留足重试余量，同时把「查询串里的凭证」这个泄露窗口压到最小。
_TICKET_TTL = timedelta(minutes=5)

# 票据作用域。单独一个常量而不是复用 security 里的字面量：它属于本模块的私有约定。
_TICKET_SCOPE = "download"


def issue_archive_ticket(user: User) -> str:
    """为用户签发一枚打包下载票据。

    Args:
        user: 票据归属用户。

    Returns:
        编码后的 JWT；调用方负责把它拼进下载 URL 的查询串。
    """
    expire = datetime.now(UTC) + _TICKET_TTL
    payload = {"exp": expire, "sub": str(user.id), "scope": _TICKET_SCOPE}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def verify_archive_ticket(db: Session, ticket: str) -> User:
    """校验票据并返回其归属用户。

    Args:
        db: 数据库会话。
        ticket: 查询串里带回的票据。

    Returns:
        票据对应的用户实体。

    Raises:
        HTTPException: 票据无效/过期/作用域不符（401），或用户不存在/未激活（401）。
    """
    invalid = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="下载票据无效或已过期")
    try:
        payload = jwt.decode(ticket, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise invalid from exc
    if payload.get("scope") != _TICKET_SCOPE:
        raise invalid
    user = db.get(User, payload.get("sub"))
    if not user or not user.is_active:
        raise invalid
    return user


def archive_disposition(filename: str) -> str:
    """构造 zip 下载响应的 ``Content-Disposition`` 值。

    同时给 ASCII 回退位与 RFC 5987 的 ``filename*``：中文标题走后者能正确落成文件名，
    旧客户端拿前者也不会得到空名字。

    Args:
        filename: 目标文件名（可能含中文）。

    Returns:
        形如 ``attachment; filename="artifacts.zip"; filename*=UTF-8''...`` 的头值。
    """
    return f"attachment; filename=\"artifacts.zip\"; filename*=UTF-8''{quote(filename)}"


def artifact_archive_download_name(title: str | None, session_id: uuid.UUID) -> str:
    """由会话标题推导下载文件名。

    非字母数字字符统一替换成下划线，末尾拼会话 id 前 8 位避免同名会话互相覆盖。

    Args:
        title: 会话标题，可为空。
        session_id: 会话 id。

    Returns:
        形如 ``科研_测试-1a2b3c4d.zip`` 的文件名，兜底 ``artifacts-1a2b3c4d.zip``。
    """
    safe_title = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in (title or "")).strip("_")
    return f"{safe_title or 'artifacts'}-{str(session_id)[:8]}.zip"
