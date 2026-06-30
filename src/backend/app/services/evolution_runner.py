"""演化任务的容器运行适配层。

把 Celery 演化任务对接到通用 :class:`app.services.container_runtime.ContainerJob`：
写入加密配置、构造容器规格、将容器事件/输出推送到 Redis，并把容器终态翻译为
异常或成功结果。容器生命周期、资源限额、网络、取消/超时/OOM 由 ``ContainerJob``
负责；本模块只处理演化任务专属的前置与回调映射。
"""

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime

from loguru import logger

from app.core.config import settings
from app.core.constants import APP_CONFIG_FILENAME, EVENTS_FILENAME, TASK_CONTAINER_DATA_DIR
from app.core.redis import push_log_entry
from app.services.container_runtime import (
    ContainerJob,
    ContainerJobCallbacks,
    ContainerJobSpec,
    ContainerJobStatus,
)
from app.services.container_service import container_name, resolve_host_path
from app.tasks import task_config_crypto


def _to_container_args(run_args: dict, run_dir: str) -> dict:
    """把 run_args 中的宿主路径改写为容器内路径，并固定 base_dir/run_id。"""
    text = json.dumps(run_args).replace(run_dir.rstrip("/"), TASK_CONTAINER_DATA_DIR.rstrip("/"))
    args = json.loads(text)
    args["base_dir"] = TASK_CONTAINER_DATA_DIR
    args["run_id"] = "run"
    return args


def _write_encrypted_config(run_args: dict, run_dir: str) -> str:
    """整体加密容器配置并写入 ``run_dir``，返回一次性解密密钥。

    加密整个配置（而非逐字段）可避免遗漏藏在 base_url 等字段中的凭据；密钥经
    环境变量传入容器，容器解密后立即删除（见 ``container_runner.main``）。
    """
    container_args = _to_container_args(run_args, run_dir)
    key = task_config_crypto.generate_key()
    token = task_config_crypto.encrypt_config(container_args, key)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, APP_CONFIG_FILENAME), "w", encoding="utf-8") as f:
        f.write(token)
    return key


def _build_spec(data: dict, config_key: str) -> ContainerJobSpec:
    """根据任务参数构造容器规格。

    mem/cpu/network/security 等沿用 ``ContainerJobSpec`` 的项目默认（分别取
    ``settings.TASK_CONTAINER_*`` 与默认网络），无需在此显式指定。
    """
    task_id = str(data["task_id"])
    run_dir = data["run_dir"]
    env = {
        "PYTHONUNBUFFERED": "1",
        "NO_COLOR": "1",
        "LOGURU_COLORIZE": "false",
        "LLM4AD_CONFIG_KEY": config_key,
        "TASK_DEP_INSTALL_TIMEOUT": str(settings.TASK_DEP_INSTALL_TIMEOUT),
    }
    if uv_index := os.environ.get("UV_INDEX_URL"):
        env["UV_INDEX_URL"] = uv_index
    return ContainerJobSpec(
        name=container_name(task_id),
        image=settings.TASK_RUNNER_IMAGE,
        mounts={resolve_host_path(run_dir): TASK_CONTAINER_DATA_DIR},
        env=env,
        events_file=os.path.join(run_dir, EVENTS_FILENAME),
        labels={"task_id": task_id},
    )


def run_evolution_container(data: dict, *, check_cancelled: Callable[[], bool]) -> dict:
    """在隔离容器中运行一次演化任务。

    Args:
        data: 任务参数（``task_id``、``run_dir``、``run_args`` 等）。
        check_cancelled: 取消检查回调（如 Celery ``AbortableTask.is_aborted``）。

    Returns:
        ``{"status": "success", "task_id": ...}``。

    Raises:
        RuntimeError: 任务被取消或容器异常退出。
        TimeoutError: 容器执行超时。
        MemoryError: 容器因内存超限（OOM）被终止。
    """
    task_id = str(data["task_id"])

    config_key = _write_encrypted_config(data["run_args"], data["run_dir"])
    spec = _build_spec(data, config_key)

    callbacks = ContainerJobCallbacks(
        on_event=lambda event: push_log_entry(task_id, event),
        on_stdout=lambda line: push_log_entry(
            task_id,
            {
                "type": "print",
                "stream": "stdout",
                "timestamp": datetime.now(UTC).isoformat(),
                "message": line,
            },
        ),
        check_cancelled=check_cancelled,
    )

    push_log_entry(
        task_id,
        {
            "type": "system",
            "message": f"启动任务容器: task_id={task_id}",
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )

    result = ContainerJob(spec, callbacks).run()

    if result.status is ContainerJobStatus.CANCELLED:
        raise RuntimeError("任务已被取消")
    if result.status is ContainerJobStatus.TIMED_OUT:
        raise TimeoutError("任务容器执行超时")
    if result.status is ContainerJobStatus.OOM:
        raise MemoryError(f"任务容器因内存超限被终止（限制: {settings.TASK_CONTAINER_MEMORY_LIMIT}）")
    if result.status is not ContainerJobStatus.COMPLETED or result.exit_code != 0:
        raise RuntimeError(f"任务容器异常退出，exit_code={result.exit_code}")

    logger.info(f"[容器模式] 任务完成: task_id={task_id}")
    return {"status": "success", "task_id": task_id}
