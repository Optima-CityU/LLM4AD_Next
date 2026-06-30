"""
FastAPI 应用入口。

初始化 FastAPI 应用实例，配置 CORS 中间件、Sentry 监控，
并注册 API 路由。
"""

import asyncio
import contextlib
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from loguru import logger

from app.api.main import api_router
from app.core.config import settings
from app.services.code_server_service import run_idle_cleanup_loop


def custom_generate_unique_id(route: APIRoute) -> str:
    """为 OpenAPI 生成唯一的操作 ID，格式为 {tag}-{route_name}。"""
    return f"{route.tags[0]}-{route.name}"


# 初始化 Sentry（非本地环境且配置了 DSN 时生效）
if settings.SENTRY_DSN and settings.ENVIRONMENT != "local":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期：启动 / 关闭后台任务。

    启动时拉起 code-server 空闲容器清理循环，并兜底处理上一次进程
    遗留的"幽灵 generating"调参轮次；关闭时取消清理循环。
    多 worker 部署时各进程都会运行清理，``docker stop`` 幂等无副作用。
    """
    from app.services.chat_tune_service import reset_orphan_turns

    try:
        reset_orphan_turns()
    except Exception:
        logger.exception("启动时重置 chat tune 幽灵轮次失败")

    try:
        from app.services.container_service import (
            cleanup_orphaned_chat_tune_containers,
        )

        cleanup_orphaned_chat_tune_containers()
    except Exception:
        logger.exception("启动时清理孤儿调参容器失败")

    cleanup_task = asyncio.create_task(run_idle_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        logger.info("code-server 空闲清理循环已停止")


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
    lifespan=lifespan,
)


# 注册所有 API 路由
app.include_router(api_router, prefix=settings.API_V1_STR)
