"""共享的 Docker 客户端单例。

提供延迟初始化的 Docker 客户端，遵循 ``DOCKER_HOST`` 配置项，
同时支持本地 socket 与远程连接（例如 ``ssh://user@host``）。
"""

import docker
from loguru import logger

from app.core.config import settings

_client: docker.DockerClient | None = None


def get_docker_client() -> docker.DockerClient:
    """获取模块级 Docker 客户端，首次调用时创建并缓存。

    Returns:
        已连接的 Docker 客户端实例。

    Raises:
        Exception: Docker 客户端初始化失败时抛出，原始异常会被重新抛出。
    """
    global _client
    if _client is not None:
        return _client

    try:
        if settings.DOCKER_HOST:
            logger.info(f"Connecting to Docker daemon at {settings.DOCKER_HOST}")
            _client = docker.DockerClient(base_url=settings.DOCKER_HOST)
        else:
            _client = docker.from_env()
    except Exception:
        logger.exception("Failed to initialize Docker client")
        raise

    return _client
