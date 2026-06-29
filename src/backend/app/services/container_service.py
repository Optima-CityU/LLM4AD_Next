"""
任务容器管理服务。

封装 Docker 容器的创建、监控和清理，用于在隔离环境中执行 LLM4AD 演化任务。
每个任务运行在独立的 Docker 容器中，通过 Redis 推送日志和指标。
"""

import json
import os
import shutil
import time
from pathlib import PureWindowsPath

from docker.errors import ImageNotFound, NotFound
from fastapi import HTTPException
from loguru import logger

from app.core.config import settings
from app.core.constants import (
    APP_CONFIG_FILENAME,
    CHAT_TUNE_CONTAINER_DATA_DIR,
    CHAT_TUNE_CONTAINER_NAME_PREFIX,
    TASK_CONTAINER_DATA_DIR,
    TASK_CONTAINER_NAME_PREFIX,
)
from app.core.docker import (
    ensure_chat_tune_container_network,
    ensure_task_container_network,
    get_docker_client,
)
from app.tasks import task_config_crypto


def _container_name(task_id: str) -> str:
    """生成容器名称，取 task_id 保持唯一。"""
    short_id = str(task_id).replace("-", "")
    return f"{TASK_CONTAINER_NAME_PREFIX}{short_id}"


def _is_absolute_host_path(path: str) -> bool:
    """Return True for POSIX or Windows absolute host paths."""
    value = path.strip()
    return os.path.isabs(value) or PureWindowsPath(value).is_absolute()


def validate_host_project_home() -> None:
    """Validate that HOST_PROJECT_HOME can be used as a Docker bind source."""
    host_project_home = settings.HOST_PROJECT_HOME.strip()
    if not host_project_home or not _is_absolute_host_path(host_project_home):
        raise HTTPException(
            status_code=500,
            detail=(
                "HOST_PROJECT_HOME 必须配置为宿主机绝对路径，当前值为 "
                f"{settings.HOST_PROJECT_HOME!r}。请在 docker/.env 中改为类似 "
                "/absolute/path/to/LLM4AD/docker/app-data 后重新启动服务。"
            ),
        )


def validate_task_container_host_path() -> None:
    """Validate task container bind mounts when isolated task containers are enabled."""
    if not settings.TASK_CONTAINER_ENABLE:
        return
    validate_host_project_home()


def _resolve_host_path(docker_path: str) -> str:
    """将容器内路径（DOCKER_PROJECT_HOME 前缀）转换为宿主机路径（HOST_PROJECT_HOME 前缀）。

    例如:
        /data/project_home/code_user-xxx/20240101/
        → D:\\data\\project_home\\code_user-xxx\\20240101\\  (Windows)
        → /data/project_home/code_user-xxx/20240101/  (Linux 生产环境)
    """
    docker_prefix = settings.DOCKER_PROJECT_HOME.rstrip("/")
    host_prefix = settings.HOST_PROJECT_HOME.rstrip("/").rstrip("\\")

    if docker_path.startswith(docker_prefix):
        relative = docker_path[len(docker_prefix) :]
        return host_prefix + relative
    return docker_path


def create_and_start_task_container(data: dict) -> str:
    """创建并启动任务执行容器。

    容器内**不会**注入任何敏感环境变量（无 Redis URL、无数据库凭据）。
    任务配置通过挂载的 JSON 文件读取，输出全部写入 stdout/stderr，由
    宿主机侧的 Celery worker 拉取 Docker 日志并转发到 Redis。

    Args:
        data: 完整的任务参数字典，包含 task_id、run_dir 与 run_args，
            会被写入挂载目录下的配置文件。

    Returns:
        Docker 容器 ID。

    Raises:
        docker.errors.ImageNotFound: 任务运行镜像未构建。
        docker.errors.APIError: Docker API 调用失败。
    """
    validate_task_container_host_path()

    task_id = str(data["task_id"])
    run_dir = data["run_dir"]
    name = _container_name(task_id)

    try:
        old = get_docker_client().containers.get(name)
        logger.warning(f"Found stale container {name}, removing")
        old.remove(force=True)
    except NotFound:
        pass

    host_run_dir = _resolve_host_path(run_dir)
    os.makedirs(run_dir, exist_ok=True)

    # Write AppConfig-compatible JSON with container-local paths
    run_args_str = json.dumps(data["run_args"])
    run_args_str = run_args_str.replace(run_dir.rstrip("/"), TASK_CONTAINER_DATA_DIR.rstrip("/"))
    run_args = json.loads(run_args_str)
    run_args["base_dir"] = TASK_CONTAINER_DATA_DIR
    run_args["run_id"] = "run"

    # 整体加密后落盘：解密密钥每任务一次性生成，经环境变量传入容器，容器在
    # 运行用户代码前解密并立即从环境变量中删除（见 container_runner.main）。
    # 加密整个配置而非逐字段，避免遗漏藏在 base_url 等字段中的凭据。
    config_key = task_config_crypto.generate_key()
    enc_token = task_config_crypto.encrypt_config(run_args, config_key)

    app_config_path = os.path.join(run_dir, APP_CONFIG_FILENAME)
    with open(app_config_path, "w", encoding="utf-8") as f:
        f.write(enc_token)

    try:
        get_docker_client().images.get(settings.TASK_RUNNER_IMAGE)
    except ImageNotFound:
        raise ImageNotFound(
            f"Task runner image {settings.TASK_RUNNER_IMAGE} not found. "
            f"Build it first: docker build -f src/backend/Dockerfile.task -t {settings.TASK_RUNNER_IMAGE} ."
        )

    runtime_network = ensure_task_container_network()

    container = get_docker_client().containers.run(
        settings.TASK_RUNNER_IMAGE,
        name=name,
        volumes={
            host_run_dir.rstrip("/").rstrip("\\"): {
                "bind": TASK_CONTAINER_DATA_DIR,
                "mode": "rw",
            },
        },
        detach=True,
        mem_limit=settings.TASK_CONTAINER_MEMORY_LIMIT,
        nano_cpus=int(settings.TASK_CONTAINER_CPU_LIMIT * 1e9),
        security_opt=["no-new-privileges"],
        cap_drop=["ALL"],
        pids_limit=512,
        tmpfs={"/tmp": "rw,nosuid,nodev,size=512m"},
        network=runtime_network,
        environment={
            "PYTHONUNBUFFERED": "1",
            "NO_COLOR": "1",
            "LOGURU_COLORIZE": "false",
            "LLM4AD_CONFIG_KEY": config_key,
            "TASK_DEP_INSTALL_TIMEOUT": str(settings.TASK_DEP_INSTALL_TIMEOUT),
            **({"UV_INDEX_URL": uv_index} if (uv_index := os.environ.get("UV_INDEX_URL")) else {}),
        },
    )

    logger.info(f"Task container started: name={name}, id={container.id[:12]}")
    return container.id


def wait_for_container(
    container_id: str,
    check_cancelled=None,
    poll_interval: int = 1,
    timeout: int | None = None,
) -> dict:
    """轮询等待容器运行结束。

    Args:
        container_id: 容器 ID
        check_cancelled: 可选回调，返回 True 时取消容器
        poll_interval: 轮询间隔（秒）
        timeout: 超时时间（秒）；为 None 时取 ``settings.TASK_TIME_LIMIT``

    Returns:
        {"exit_code": int, "timed_out": bool, "oom_killed": bool, "cancelled": bool}
    """
    if timeout is None:
        timeout = settings.TASK_TIME_LIMIT
    start_time = time.time()

    while True:
        try:
            container = get_docker_client().containers.get(container_id)
            container.reload()
        except NotFound:
            logger.warning(f"容器 {container_id[:12]} 已不存在")
            return {"exit_code": -1, "timed_out": False, "oom_killed": False, "cancelled": False}

        # 检查任务是否被取消
        if check_cancelled and check_cancelled():
            logger.info(f"任务被取消，正在停止容器 {container_id[:12]}")
            container.stop(timeout=10)
            return {"exit_code": -1, "timed_out": False, "oom_killed": False, "cancelled": True}

        # 检查容器是否已退出
        if container.status in ("exited", "dead"):
            state = container.attrs.get("State", {})
            exit_code = state.get("ExitCode", -1)
            oom_killed = state.get("OOMKilled", False)
            logger.info(f"容器 {container_id[:12]} 已退出: exit_code={exit_code}, oom_killed={oom_killed}")
            return {
                "exit_code": exit_code,
                "timed_out": False,
                "oom_killed": oom_killed,
                "cancelled": False,
            }

        # 检查超时
        elapsed = time.time() - start_time
        if elapsed > timeout:
            logger.warning(f"容器 {container_id[:12]} 执行超时 ({timeout}s)，正在终止")
            container.kill()
            return {"exit_code": -1, "timed_out": True, "oom_killed": False, "cancelled": False}

        time.sleep(poll_interval)


def cleanup_container(container_id: str) -> None:
    """强制移除容器及其匿名卷。"""
    try:
        container = get_docker_client().containers.get(container_id)
        container.remove(force=True, v=True)
        logger.info(f"容器 {container_id[:12]} 已清理")
    except NotFound:
        pass
    except Exception as e:
        logger.error(f"清理容器 {container_id[:12]} 失败: {e}")


def kill_task_container(task_id: str) -> None:
    """根据任务 ID 强制终止并移除对应的容器。

    使用 ``SIGKILL`` 立即终止容器进程，跳过优雅期，适用于用户点击
    "停止任务" 等需要快速响应的场景。

    Args:
        task_id: 业务任务 ID。
    """
    name = _container_name(task_id)
    try:
        container = get_docker_client().containers.get(name)
        container.kill()
        container.remove(force=True, v=True)
        logger.info(f"已强制终止并移除任务容器: {name}")
    except NotFound:
        logger.debug(f"任务容器 {name} 不存在，无需停止")
    except Exception as e:
        logger.error(f"强制终止任务容器 {name} 失败: {e}")


def cleanup_orphaned_containers() -> None:
    """清理所有孤儿任务容器（worker 重启时调用）。"""
    try:
        containers = get_docker_client().containers.list(
            all=True,
            filters={"name": TASK_CONTAINER_NAME_PREFIX},
        )
        for container in containers:
            logger.warning(f"发现孤儿任务容器: {container.name}，正在清理")
            container.remove(force=True, v=True)
    except Exception as e:
        logger.error(f"清理孤儿容器失败: {e}")


# ---- 调参（chat-tune）隔离容器生命周期 ----


def _chat_tune_container_name(session_id: str) -> str:
    """生成调参容器名称（同时作为 docker 网络内的 DNS 主机名）。"""
    short_id = str(session_id).replace("-", "")
    return f"{CHAT_TUNE_CONTAINER_NAME_PREFIX}{short_id}"


def chat_tune_container_host(session_id: str) -> str:
    """返回 backend 用于连接容器 SSE 服务的主机名。

    CHAT_TUNE_CONTAINER_NETWORK=True 时通过容器名 DNS 解析（生产环境 Docker 内）；
    False 时使用 localhost（本地开发，需配合端口发布）。
    """
    if settings.CHAT_TUNE_CONTAINER_NETWORK:
        return _chat_tune_container_name(session_id)
    return "localhost"


def _chat_tune_docker_workdir(session_id: str, turn_id: str) -> str:
    """调参临时数据目录的容器内（= backend 内）路径。

    按 ``turn_id`` 隔离：同一会话相邻两轮（取消旧轮 + 立即起新轮）使用不同
    目录，避免旧轮 ``finally`` 的清理误删新轮正在挂载的目录。
    """
    sess = str(session_id).replace("-", "")
    turn = str(turn_id).replace("-", "")
    base = settings.DOCKER_PROJECT_HOME.rstrip("/")
    return f"{base}/chat_tune/{sess}/{turn}/"


def prepare_chat_tune_workdir(
    input_data_path: str | None, session_id: str, turn_id: str
) -> tuple[str, str]:
    """为本轮调参准备临时数据目录：清空重建并从存储下载用户数据。

    下载时**保留 S3 原有目录结构**（不 strip first level）：

    - 上传端 :func:`chat_tune_upload_file/data` 把文件落到
      ``input_data_path/{first_subdir}/...``（默认 ``code/``），并把含子目录
      的相对路径写回消息 payload 作为 LLM 的可读路径；若下载时 strip 掉第一
      级，容器内挂载点的相对路径就会与 payload 不一致，LLM 调 ``read_file``
      时会找不到对话中刚上传的文件。
    - 调参容器仅作为 LLM 工具的沙箱根，与实际任务运行容器（其 CWD 等于项目
      根）用途不同，故无需保持同样的 strip 语义。

    Args:
        input_data_path: 任务的存储前缀（rustfs key），为空表示无用户数据。
        session_id: 调参会话 ID。
        turn_id: 本轮 ID，用于隔离目录。

    Returns:
        ``(docker_path, host_path)``：前者为 backend 进程内可写路径（用于清理），
        后者为传给 ``volumes`` 的宿主机绝对路径（用于挂载）。
    """
    docker_path = _chat_tune_docker_workdir(session_id, turn_id)

    if os.path.isdir(docker_path):
        shutil.rmtree(docker_path, ignore_errors=True)
    os.makedirs(docker_path, exist_ok=True)

    if input_data_path:
        from app.core.storage import storage

        storage.download_files_local(
            input_data_path, local_path=docker_path, strip_first_level=False
        )

    host_path = _resolve_host_path(docker_path)
    return docker_path, host_path


def start_chat_tune_container(session_id: str, host_workdir: str) -> str:
    """创建并启动调参 SSE 容器。

    容器复用 ``TASK_RUNNER_IMAGE``，只挂载本会话临时数据目录（rw），加入
    与 backend 相同的用户网络以支持按容器名 DNS 解析；端口**不发布**到宿主。

    Args:
        session_id: 调参会话 ID。
        host_workdir: 宿主机数据目录绝对路径（由 :func:`prepare_chat_tune_workdir` 返回）。

    Returns:
        Docker 容器 ID。

    Raises:
        docker.errors.ImageNotFound: 任务运行镜像未构建。
    """
    name = _chat_tune_container_name(session_id)

    try:
        old = get_docker_client().containers.get(name)
        logger.warning(f"Found stale chat-tune container {name}, removing")
        old.remove(force=True, v=True)
    except NotFound:
        pass

    try:
        get_docker_client().images.get(settings.TASK_RUNNER_IMAGE)
    except ImageNotFound:
        raise ImageNotFound(
            f"Task runner image {settings.TASK_RUNNER_IMAGE} not found. "
            f"Build it first: docker build -f src/backend/Dockerfile.task -t {settings.TASK_RUNNER_IMAGE} ."
        )

    use_network = settings.CHAT_TUNE_CONTAINER_NETWORK
    port = settings.CHAT_TUNE_CONTAINER_PORT

    network_kwargs: dict = {}
    chat_tune_network: str | None = None
    if use_network:
        chat_tune_network = ensure_chat_tune_container_network()
        network_kwargs["network"] = chat_tune_network
    else:
        network_kwargs["ports"] = {f"{port}/tcp": ("127.0.0.1", port)}

    container = get_docker_client().containers.run(
        settings.TASK_RUNNER_IMAGE,
        name=name,
        command=["python", "/app/backend/app/tasks/chat_tune_runner.py"],
        working_dir=CHAT_TUNE_CONTAINER_DATA_DIR,
        volumes={
            host_workdir.rstrip("/").rstrip("\\"): {
                "bind": CHAT_TUNE_CONTAINER_DATA_DIR,
                "mode": "rw",
            },
        },
        detach=True,
        mem_limit=settings.CHAT_TUNE_CONTAINER_MEMORY_LIMIT,
        nano_cpus=int(settings.CHAT_TUNE_CONTAINER_CPU_LIMIT * 1e9),
        security_opt=["no-new-privileges"],
        cap_drop=["ALL"],
        pids_limit=256,
        tmpfs={"/tmp": "rw,nosuid,nodev,size=256m"},
        **network_kwargs,
        environment={
            "PYTHONUNBUFFERED": "1",
            "NO_COLOR": "1",
            "PORT": str(settings.CHAT_TUNE_CONTAINER_PORT),
            "CHAT_TUNE_DATA_DIR": CHAT_TUNE_CONTAINER_DATA_DIR,
        },
    )

    if use_network:
        try:
            task_network = ensure_task_container_network()
            if task_network != chat_tune_network:
                get_docker_client().networks.get(task_network).connect(container)
                logger.info(
                    "Chat-tune container connected to provider network: name={}, network={}",
                    name,
                    task_network,
                )
        except Exception:
            try:
                container.remove(force=True, v=True)
            except Exception:
                logger.exception(
                    "Failed to remove chat-tune container after provider network attach failure: {}",
                    name,
                )
            logger.exception(
                "Failed to attach chat-tune container to provider network: name={}",
                name,
            )
            raise

    logger.info(f"Chat-tune container started: name={name}, id={container.id[:12]}")
    return container.id


def kill_chat_tune_container(session_id: str) -> None:
    """按会话 ID 强制终止并移除调参容器（供 stop/cancel 即时终止）。

    幂等：容器不存在时静默返回；其它错误仅记录日志。
    """
    name = _chat_tune_container_name(session_id)
    try:
        container = get_docker_client().containers.get(name)
        container.kill()
        container.remove(force=True, v=True)
        logger.info(f"已强制终止并移除调参容器: {name}")
    except NotFound:
        logger.debug(f"调参容器 {name} 不存在，无需终止")
    except Exception as e:
        logger.error(f"强制终止调参容器 {name} 失败: {e}")


def cleanup_chat_tune_container(container_id: str) -> None:
    """强制移除指定调参容器及其匿名卷。"""
    try:
        container = get_docker_client().containers.get(container_id)
        container.remove(force=True, v=True)
        logger.info(f"调参容器 {container_id[:12]} 已清理")
    except NotFound:
        pass
    except Exception as e:
        logger.error(f"清理调参容器 {container_id[:12]} 失败: {e}")


def get_chat_tune_container_diagnostics(
    container_id: str, *, log_tail: int = 80, max_log_chars: int = 4000
) -> dict[str, object]:
    """读取调参容器状态和尾部日志，用于启动失败诊断。"""
    container = get_docker_client().containers.get(container_id)
    container.reload()
    state = container.attrs.get("State") or {}
    raw_logs = container.logs(stdout=True, stderr=True, tail=log_tail)
    logs = raw_logs.decode("utf-8", errors="replace")
    if len(logs) > max_log_chars:
        logs = logs[-max_log_chars:]
    return {
        "id": container.id[:12],
        "name": container.name,
        "status": container.status,
        "exit_code": state.get("ExitCode"),
        "error": state.get("Error"),
        "logs": logs,
    }


def cleanup_chat_tune_workdir(docker_path: str) -> None:
    """删除调参临时数据目录。"""
    try:
        if os.path.isdir(docker_path):
            shutil.rmtree(docker_path, ignore_errors=True)
    except Exception as e:
        logger.error(f"清理调参数据目录 {docker_path} 失败: {e}")


def cleanup_orphaned_chat_tune_containers() -> None:
    """清理所有孤儿调参容器（进程重启时调用）。"""
    try:
        containers = get_docker_client().containers.list(
            all=True,
            filters={"name": CHAT_TUNE_CONTAINER_NAME_PREFIX},
        )
        for container in containers:
            logger.warning(f"发现孤儿调参容器: {container.name}，正在清理")
            container.remove(force=True, v=True)
    except Exception as e:
        logger.error(f"清理孤儿调参容器失败: {e}")
