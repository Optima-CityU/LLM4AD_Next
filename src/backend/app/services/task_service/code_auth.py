"""code-server 的认证转发端点支持。

校验 code-server 的 cookie token，刷新用户活跃时间，并以响应头形式返回
用户身份头供网关使用。
"""

from fastapi import HTTPException, Request, Response, status
from sqlmodel import Session

from app.core.redis import touch_code_user_active
from app.core.security import decode_access_token
from app.models import User


def verify_code_auth(request: Request, db: Session) -> Response:
    """验证 code-server 的 cookie token 并返回用户身份头。

    Args:
        request: HTTP 请求对象
        db: 数据库会话

    Returns:
        包含 X-User-ID / X-User-Email 头的 200 响应

    Raises:
        HTTPException: 缺少 token（401）、token 无效（401）或用户不存在（401）
    """
    code_token = request.cookies.get("code_token")
    if not code_token:
        raise HTTPException(status_code=401, detail="缺少认证令牌")

    payload = decode_access_token(token=code_token, scope="code")
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token无效！")

    user = db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或未启用！")

    # 刷新活跃时间，供后台空闲清理任务判断；失败已在内部记录日志
    touch_code_user_active(user.id)

    # 认证成功，将用户身份放在响应头中，供网关后续过滤器使用。
    response = Response(status_code=200)
    response.headers["X-User-ID"] = str(user.id)
    response.headers["X-User-Email"] = user.email
    return response
