"""容器化任务执行入口。

在 Docker 容器内执行 LLM4AD 演化任务。无需命令行参数：宿主机将
任务运行目录挂载到 ``DATA_DIR``，并在启动容器前写入 AppConfig JSON
文件，容器读取该配置后即可运行。

所有输出统一写入 stdout/stderr —— 宿主机上的 Celery worker 实时拉取
Docker 日志并转发到 Redis。本模块**不依赖** Redis 与数据库。
"""

import asyncio
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

# 与 container_runner.py 同目录，容器内以脚本方式启动时 sys.path[0] 即本目录
import task_config_crypto  # noqa: E402
from llm4ad import LLM4AD
from llm4ad.config import AppConfig
from loguru import logger

DATA_DIR = "/task/data"
CONFIG_FILENAME = ".app_config.json"
FINAL_STATE_FILENAME = ".final_state.json"


def _install_dependencies(data_dir: str) -> None:
    """检查 data_dir 下是否存在 requirements.txt，若存在则安装依赖。

    Args:
        data_dir: 任务运行目录，宿主机已将用户项目挂载到此处。

    Raises:
        RuntimeError: requirements.txt 安装失败时抛出。
    """
    req_path = Path(data_dir) / "requirements.txt"
    if not req_path.exists():
        return

    logger.info(f"Installing dependencies from {req_path}")

    try:
        install_timeout = int(os.environ.get("TASK_DEP_INSTALL_TIMEOUT", "600"))
    except ValueError:
        install_timeout = 600

    try:
        result = subprocess.run(
            [
                "uv", "pip", "install",
                "--python", sys.executable,
                "--no-progress",
                "-r", str(req_path),
            ],
            capture_output=True,
            text=True,
            timeout=install_timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Dependency installation timed out after {install_timeout}s"
        )
    if result.returncode != 0:
        logger.error(f"Dependency installation failed:\n{result.stderr}")
        raise RuntimeError(f"Failed to install dependencies: {result.stderr}")
    logger.info("Dependencies installed successfully")


def main() -> None:
    """容器入口：读取配置、安装依赖、运行 LLM4AD 演化任务。

    宿主机将任务运行目录挂载至 ``DATA_DIR``，并写入 AppConfig 兼容的
    JSON 配置文件（``CONFIG_FILENAME``），其中所有路径均已转换为容器内
    路径、敏感字段（api_key/auth_token）以对称加密存储。解密密钥经
    ``LLM4AD_CONFIG_KEY`` 环境变量传入，本函数在运行用户代码前完成解密
    并立即从环境变量中删除。任务运行结束后会将最终 state 序列化到
    ``FINAL_STATE_FILENAME``。
    """
    try:
        # 最早处取出并删除解密密钥：必须早于 _install_dependencies（会 spawn
        # 继承父进程环境变量的 uv 子进程）与任何用户代码，避免密钥被读取或继承。
        config_key = os.environ.pop("LLM4AD_CONFIG_KEY", None)

        config_path = os.path.join(DATA_DIR, CONFIG_FILENAME)

        logger.info(f"Container starting, data_dir={DATA_DIR}")

        _install_dependencies(DATA_DIR)

        # 依赖安装完成后清除可能含私有源凭据的环境变量，使后续用户代码及其
        # 子进程继承到的环境中不残留任何秘密（解密密钥已在上面 pop）。
        os.environ.pop("UV_INDEX_URL", None)

        # 配置文件整体加密落盘：读出密文 → 解密 → 还原为 dict
        with open(config_path, encoding="utf-8") as f:
            token = f.read()
        # 密文已读入内存，磁盘文件不再需要，立即删除以减少敏感配置暴露面
        try:
            os.remove(config_path)
        except OSError:
            pass
        if not config_key:
            raise RuntimeError("缺少 LLM4AD_CONFIG_KEY，无法解密任务配置")
        data = task_config_crypto.decrypt_config(token, config_key)
        del token
        del config_key  # 不再保留密钥引用

        # 复刻 AppConfig.from_json 的全局设置合并，但不把解密后的明文写回磁盘
        from llm4ad.config.settings import (
            load_global_settings,
            merge_with_global_settings,
        )

        data = merge_with_global_settings(load_global_settings(), data)
        config = AppConfig.from_dict(data)

        # 切换工作目录到挂载的数据目录：配置以内存 AppConfig 对象传入，
        # LLM4AD 的 _config_dir 为 None，dataset/自定义评估器模块等相对路径
        # 会回退到 CWD 解析。若不切换，CWD 为镜像 WORKDIR(/app/backend)，
        # 相对路径将解析到错误位置（FileNotFoundError）。切到 DATA_DIR 后
        # 所有相对路径都落到挂载目录。
        os.chdir(DATA_DIR)

        llm4ad = LLM4AD(config)
        llm4ad.print_run_summary()

        result = asyncio.run(llm4ad.run(resume_from_checkpoint=None))
        logger.info(result.state.value)

        try:
            final_state = llm4ad.export_state()
            state_path = os.path.join(DATA_DIR, FINAL_STATE_FILENAME)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(final_state, f, ensure_ascii=False, default=str)
        except Exception:
            pass

        logger.info("Task completed successfully")

    except Exception as exc:
        logger.error(f"Task failed: {exc}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
