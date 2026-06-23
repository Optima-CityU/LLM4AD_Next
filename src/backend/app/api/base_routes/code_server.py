"""
Code-Server 管理路由。

提供 code-server 令牌获取和容器启动端点。
注意：使用 /utils 前缀以保持 API URL 向后兼容。
"""

import asyncio
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser
from app.core import security
from app.core.config import settings
from app.services.code_server_service import (
    ensure_user_workspace,
    get_or_start_container,
    wait_for_service_ready,
)

router = APIRouter(prefix="/utils", tags=["utils.code-server"])


@router.post("/get_code_token", summary="获取code-server的token并且启动容器")
async def get_code_token(
    current_user: CurrentUser,
    dark: bool = Query(True, description="True for dark theme, False for light theme"),
):
    """获取 code-server 认证令牌，并确保用户容器已启动。

    流程：
    1. 生成 code 作用域的 JWT 令牌并设置为 httponly cookie
    2. 确保用户工作空间目录和配置文件存在
    3. 检查并启动 code-server Docker 容器
    4. 若容器是新启动的，异步等待服务就绪日志后再返回
    """
    # 生成 code-server 专用令牌
    token_expires = timedelta(minutes=settings.CODE_TOKEN_EXPIRE_MINUTES)
    code_token = security.create_access_token(
        current_user.id, expires_delta=token_expires, scope="code"
    )
    response = Response(status_code=200)
    response.set_cookie(
        "code_token",
        code_token,
        max_age=settings.CODE_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
        httponly=True,
    )

    container_name = f"code_user-{current_user.id}"

    # 初始化用户工作空间（同步 I/O，放到线程池执行）
    await asyncio.to_thread(
        ensure_user_workspace, container_name, current_user.email, dark
    )

    # 启动或创建容器（同步 Docker 调用走线程池）
    container, started_at = await asyncio.to_thread(
        get_or_start_container,
        container_name,
    )

    # 新启动的容器在事件循环里异步等待服务就绪，不占用线程池 worker
    if started_at is not None:
        ready = await wait_for_service_ready(container, since=started_at)
        if not ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Code Server 启动超时，请稍后重试",
            )

    return response
