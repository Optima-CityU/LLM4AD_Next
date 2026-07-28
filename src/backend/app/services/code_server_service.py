"""
Code-Server 容器管理服务。

封装 Docker 容器的创建、启动以及用户工作空间初始化等逻辑。
"""

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

import docker
from docker.errors import NotFound
from docker.models.containers import Container
from fastapi import HTTPException
from loguru import logger

from app.core.config import settings
from app.core.constants import (
    CODE_SERVER_CPU_LIMIT,
    CODE_SERVER_IMAGE,
    CODE_SERVER_MEMORY_LIMIT,
    get_code_server_vscode_settings,
)
from app.core.docker import ensure_code_server_network, get_docker_client
from app.core.redis import pop_idle_code_users
from app.services.container_service import validate_host_project_home

# code-server 启动完成的日志特征。不同 code-server 版本日志略有差异。
_SERVICE_READY_MARKERS = (
    b"HTTP server listening on",
    b"Session server listening on",
)
# 等待服务就绪的最长时间（秒）
_SERVICE_READY_TIMEOUT = 60
# 轮询日志的间隔（秒）
_SERVICE_READY_POLL_INTERVAL = 0.5
_CODE_SERVER_HOME = "/data/project_home"
_CODE_SERVER_CONFIG_HOME = f"{_CODE_SERVER_HOME}/.config"
_CODE_SERVER_DATA_HOME = f"{_CODE_SERVER_HOME}/.local/share"
_CODE_SERVER_CACHE_HOME = f"{_CODE_SERVER_HOME}/.cache"
_CODE_SERVER_USER_DATA_DIR = f"{_CODE_SERVER_DATA_HOME}/code-server"
_CODE_SERVER_SETTINGS_PATH = f"{_CODE_SERVER_USER_DATA_DIR}/User/settings.json"
_CURRENT_RUNTIME_ENV_MARKER = f"HOME={_CODE_SERVER_HOME}"


def _ensure_container_network(container: Container, network_name: str) -> None:
    """Keep an existing code-server container only on the code-server network."""
    container.reload()
    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    if network_name in networks:
        logger.debug(f"code-server 容器 {container.name} 已在 Docker 网络: {network_name}")
    else:
        get_docker_client().networks.get(network_name).connect(container, aliases=[container.name])
        logger.info(f"已将 code-server 容器 {container.name} 接入 Docker 网络: {network_name}")
        container.reload()
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})

    for old_network in list(networks):
        if old_network == network_name:
            continue
        try:
            get_docker_client().networks.get(old_network).disconnect(container)
            logger.info(
                f"已将 code-server 容器 {container.name} 从旧 Docker 网络断开: {old_network}"
            )
        except docker.errors.APIError as exc:
            logger.warning(
                f"断开 code-server 容器 {container.name} 的旧网络 {old_network} 失败: {exc}"
            )


def _code_server_host_path(container_name: str, *parts: str) -> str:
    """Build a host path under HOST_PROJECT_HOME for code-server mounts."""
    return str(Path(settings.HOST_PROJECT_HOME) / container_name / Path(*parts))


def ensure_user_workspace(container_name: str, user_email: str, dark: bool = True) -> str:
    """确保用户工作空间目录存在，创建 README 和 VS Code 配置文件。

    Args:
        container_name: 容器名称，同时作为用户目录名
        user_email: 用户邮箱，写入 README
        dark: True for dark theme, False for light theme.

    Returns:
        用户工作空间的绝对路径
    """
    user_home = f"{settings.DOCKER_PROJECT_HOME.rstrip('/')}/{container_name}/"
    os.makedirs(user_home, exist_ok=True)

    # 创建 README（仅首次）
    readme_path = os.path.join(user_home, "README.md")
    if not os.path.exists(readme_path):
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write("# LLM4AD项目说明\n\n")
            f.write(f"- {user_email}：`{container_name}`\n\n")

    # 创建或更新 VS Code 配置文件
    os.makedirs(os.path.join(user_home, ".config"), exist_ok=True)
    os.makedirs(os.path.join(user_home, ".cache"), exist_ok=True)
    settings_dir = os.path.join(user_home, ".local", "share", "code-server", "User")
    os.makedirs(settings_dir, exist_ok=True)
    # Keep this file path aligned with the bind mount in get_or_start_container.
    # Docker creates a directory for a missing bind source, which makes a later
    # file mount fail permanently for that user.
    settings_path = os.path.join(user_home, ".env_code.json")
    vscode_settings = get_code_server_vscode_settings(dark=dark)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(vscode_settings, f, ensure_ascii=False)

    return user_home


def _get_started_at(container: Container) -> datetime:
    """读取容器最近一次启动时刻（由 Docker daemon 记录）。

    与 `container.logs(since=...)` 共用同一个时钟源，避免后端容器与
    宿主机时钟不一致导致的 since 过滤偏差。

    Args:
        container: Docker 容器对象，调用前需已 start。

    Returns:
        StartedAt 对应的 timezone-aware datetime。
    """
    container.reload()
    started_at = container.attrs["State"]["StartedAt"]
    # Docker 返回形如 "2024-01-15T08:30:45.1234567Z"，纳秒位需截断到微秒
    iso = started_at.replace("Z", "+00:00")
    if "." in iso:
        head, _, tail = iso.partition(".")
        frac, _, tz = tail.partition("+")
        iso = f"{head}.{frac[:6]}+{tz}" if tz else f"{head}.{frac[:6]}"
    return datetime.fromisoformat(iso)


def _uses_current_runtime_config(container: Container) -> bool:
    env = container.attrs.get("Config", {}).get("Env") or []
    return _CURRENT_RUNTIME_ENV_MARKER in env


async def wait_for_service_ready(
    container: Container,
    since: datetime,
    markers: tuple[bytes, ...] = _SERVICE_READY_MARKERS,
    timeout: int = _SERVICE_READY_TIMEOUT,
    interval: float = _SERVICE_READY_POLL_INTERVAL,
) -> bool:
    """异步轮询容器日志，直到出现服务就绪标志或超时。

    用 `asyncio.sleep` 让出事件循环，期间不占用线程池 worker；
    `container.logs` 是同步 IO，只在每次调用时通过 `asyncio.to_thread`
    单次卸到线程池，远比"占住一个 worker 直到 60 秒后再释放"更友好。

    通过 `since` 过滤本次启动之后的日志，避免命中重启前残留的旧日志。

    Args:
        container: Docker 容器对象。
        since: 起始时间点，应来自 Docker daemon 自身记录的 StartedAt，
            确保与日志时间戳同源。
        markers: 表示服务已启动的日志关键字节串。
        timeout: 最长等待时间（秒）。
        interval: 轮询间隔（秒）。

    Returns:
        True 表示在超时前观察到 marker，False 表示超时未就绪。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            logs = await asyncio.to_thread(
                container.logs, stdout=True, stderr=True, since=since
            )
        except docker.errors.APIError as exc:
            logger.warning(f"读取容器日志失败: {exc}")
            return False
        if any(marker in logs for marker in markers):
            return True
        await asyncio.sleep(interval)
    logger.warning(
        f"等待容器 {container.name} 服务就绪超时（{timeout}s），未捕获到日志标志"
    )
    return False


def get_or_start_container(container_name: str) -> tuple[Container, datetime | None]:
    """检查容器是否存在，不存在则创建并启动，已存在但未运行则启动。

    本函数是同步的，调用方应通过 `asyncio.to_thread` 调度。函数本身只做
    Docker 同步调用并立即返回，不等待 code-server 服务就绪。调用方拿到
    `started_at` 后应再 `await wait_for_service_ready(container, started_at)`
    在事件循环里完成等待，避免长时间占用线程池。

    Args:
        container_name: 容器名称。

    Returns:
        (container, started_at):
            - `started_at` 为 None 表示容器原本就在运行，无需等待服务就绪
            - 否则为本次启动的时间戳，调用方应据此调用 `wait_for_service_ready`

    Raises:
        HTTPException: Docker 网络不存在（404）。
    """
    validate_host_project_home()

    client = get_docker_client()
    try:
        runtime_network = ensure_code_server_network()
        logger.debug(f"使用 Docker code-server 网络: {runtime_network}")
    except NotFound:
        network_name = (
            settings.CODE_SERVER_NETWORK_NAME
            or settings.DOCKER_NETWORK_NAME
            or f"{settings.PROJECT_NAME}_default"
        )
        raise HTTPException(status_code=404, detail=f"Docker 网络不存在: {network_name}")

    # 检查容器是否已存在
    try:
        container = client.containers.get(container_name)
        if not _uses_current_runtime_config(container):
            logger.info(f"code-server 容器 {container.name} 使用旧运行配置，正在重建")
            container.remove(force=True, v=True)
            raise docker.errors.NotFound("stale code-server container")
        _ensure_container_network(container, runtime_network)
        if container.status == "running":
            return container, None
        container.start()
        return container, _get_started_at(container)
    except docker.errors.NotFound:
        pass

    # 创建并启动新容器
    container = client.containers.run(
        CODE_SERVER_IMAGE,
        name=container_name,
        user="root",
        environment={
            "DOCKER_USER": "root",
            "DOCKER_PASSWORD": "",
            "HOME": _CODE_SERVER_HOME,
            "XDG_CONFIG_HOME": _CODE_SERVER_CONFIG_HOME,
            "XDG_DATA_HOME": _CODE_SERVER_DATA_HOME,
            "XDG_CACHE_HOME": _CODE_SERVER_CACHE_HOME,
        },
        command=[
            "--auth",
            "none",
            "--user-data-dir",
            _CODE_SERVER_USER_DATA_DIR,
            _CODE_SERVER_HOME,
        ],
        working_dir=_CODE_SERVER_HOME,
        network=runtime_network,
        detach=True,
        restart_policy={"Name": "always"},
        mem_limit=CODE_SERVER_MEMORY_LIMIT,
        nano_cpus=int(CODE_SERVER_CPU_LIMIT * 1e9),
        security_opt=["no-new-privileges"],
        cap_drop=["ALL"],
        pids_limit=256,
        tmpfs={"/tmp": "rw,nosuid,nodev,size=256m"},
        volumes={
            # 宿主上用户目录挂载到 VS Code 服务主目录
            _code_server_host_path(container_name): {
                "bind": _CODE_SERVER_HOME,
                "mode": "rw",
            },
            _code_server_host_path(container_name, ".env_code.json"): {
                "bind": _CODE_SERVER_SETTINGS_PATH,
                "mode": "rw",
            },
        },
    )
    return container, _get_started_at(container)


def stop_container(container_name: str) -> bool:
    """停止指定的 code-server 容器，幂等。

    `restart_policy=always` 下，`docker stop` 是用户主观停止，daemon 不会
    自动重启该容器，符合空闲回收语义。下次用户访问时由 `get_or_start_container`
    重新拉起。

    Args:
        container_name: 容器名称。

    Returns:
        True 表示发出了停止操作；False 表示容器不存在或本就不在运行。
    """
    client = get_docker_client()
    try:
        container = client.containers.get(container_name)
    except docker.errors.NotFound:
        return False
    if container.status != "running":
        return False
    container.stop(timeout=10)
    return True


async def stop_idle_containers() -> int:
    """扫描 Redis，停止所有最后活跃时间超过阈值的 code-server 容器。

    Returns:
        实际停止的容器数量。
    """
    threshold_ts = time.time() - settings.CODE_SERVER_IDLE_TIMEOUT_SECONDS
    idle_users = await asyncio.to_thread(pop_idle_code_users, threshold_ts)
    if not idle_users:
        return 0

    stopped = 0
    for user_id in idle_users:
        container_name = f"code_user-{user_id}"
        try:
            ok = await asyncio.to_thread(stop_container, container_name)
        except Exception as exc:
            logger.warning(f"停止空闲容器 {container_name} 失败: {exc}")
            continue
        if ok:
            stopped += 1
            logger.info(f"已停止空闲 code-server 容器: {container_name}")
    return stopped


async def run_idle_cleanup_loop() -> None:
    """周期性触发空闲容器清理；异常仅记录日志，循环不退出。

    应作为后台 task 在应用启动时拉起，关闭时取消。
    """
    interval = settings.CODE_SERVER_CLEANUP_INTERVAL_SECONDS
    logger.info(
        f"启动 code-server 空闲清理循环（间隔 {interval}s，"
        f"空闲阈值 {settings.CODE_SERVER_IDLE_TIMEOUT_SECONDS}s）"
    )
    while True:
        try:
            await stop_idle_containers()
        except Exception as exc:
            logger.warning(f"code-server 空闲清理任务异常: {exc}")
        await asyncio.sleep(interval)
