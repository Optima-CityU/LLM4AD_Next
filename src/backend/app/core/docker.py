"""共享的 Docker 客户端单例。

提供延迟初始化的 Docker 客户端，遵循 ``DOCKER_HOST`` 配置项，
同时支持本地 socket 与远程连接（例如 ``ssh://user@host``）。
"""

import docker
from docker.errors import NotFound
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


def _fallback_runtime_network_name() -> str:
    """Return the legacy runtime network fallback."""
    return settings.DOCKER_NETWORK_NAME or f"{settings.PROJECT_NAME}_default"


def get_task_container_network_name() -> str:
    """Return the Docker network used by task runner containers."""
    return settings.TASK_CONTAINER_NETWORK_NAME or _fallback_runtime_network_name()


def get_code_server_network_name() -> str:
    """Return the Docker network used by code-server containers."""
    return settings.CODE_SERVER_NETWORK_NAME or _fallback_runtime_network_name()


def get_chat_tune_container_network_name() -> str:
    """Return the Docker network used by chat-tune containers."""
    return settings.CHAT_TUNE_CONTAINER_NETWORK_NAME or _fallback_runtime_network_name()


def ensure_docker_network(network_name: str, purpose: str) -> str:
    """Validate that a Docker network exists and return its name."""
    try:
        get_docker_client().networks.get(network_name)
    except NotFound:
        logger.error("Docker network for {} does not exist: {}", purpose, network_name)
        raise
    return network_name


def ensure_task_container_network() -> str:
    """Validate the task runner Docker network and return its name."""
    return ensure_docker_network(get_task_container_network_name(), "task containers")


def ensure_code_server_network() -> str:
    """Validate the code-server Docker network and return its name."""
    return ensure_docker_network(get_code_server_network_name(), "code-server containers")


def ensure_chat_tune_container_network() -> str:
    """Validate the chat-tune Docker network and return its name."""
    return ensure_docker_network(
        get_chat_tune_container_network_name(), "chat-tune containers"
    )
