"""容器化任务执行入口。

在 Docker 容器内执行 LLM4AD 演化任务。无需命令行参数：宿主机将任务运行目录挂载
到 ``DATA_DIR``，并在启动容器前写入加密的 AppConfig 文件，容器解密后即可运行。

结构化日志与产物写入挂载盘的 NDJSON 事件文件（``EVENTS_FILENAME``），由宿主侧
tail；stdout/stderr 仅承载依赖安装、用户 print 与未捕获 traceback 等非结构化输出。
本模块**不依赖** Redis 与数据库。
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path

# 与 container_runner.py 同目录，容器内以脚本方式启动时 sys.path[0] 即本目录
import task_config_crypto  # noqa: E402
from llm4ad import LLM4AD
from llm4ad.config import AppConfig
from loguru import logger

DATA_DIR = "/task/data"
CONFIG_FILENAME = ".app_config.json"
FINAL_STATE_FILENAME = ".final_state.json"
EVENTS_FILENAME = ".events.jsonl"  # 须与 app.core.constants.EVENTS_FILENAME 一致
GENERATED_DIR = os.path.join(DATA_DIR, "llm4ad", "run", "generated")
_GENERATED_SCAN_INTERVAL = 2.0  # 扫描 generated 目录的间隔（秒）


class EventsSink:
    """写 NDJSON 事件文件的 loguru sink。

    实例可调用，直接传给 ``logger.add``；``emit`` 用于手动追加其他事件
    （generated/final_state）。可用作上下文管理器自动关闭文件句柄。
    """

    def __init__(self, events_path: str):
        self._fp = open(events_path, "a", encoding="utf-8")  # noqa: SIM115 - 由 close 释放
        self._lock = threading.Lock()

    def emit(self, event: dict) -> None:
        """线程安全地追加一条事件。"""
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            try:
                self._fp.write(line)
                self._fp.flush()
            except Exception:
                pass

    def __call__(self, message) -> None:
        """loguru sink：把日志记录转为 log 事件。"""
        record = message.record
        self.emit({
            "type": "log",
            "timestamp": record["time"].isoformat(),
            "level": record["level"].name,
            "message": record["message"],
            "module": record["name"],
            "function": record["function"],
            "line": record["line"],
        })

    def close(self) -> None:
        with self._lock:
            try:
                self._fp.close()
            except Exception:
                pass

    def __enter__(self) -> "EventsSink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _scan_generated(generated_dir: str, emit, seen: dict[str, float]) -> None:
    """扫描 generated 目录，对新增/更新的 JSON 解文件追加 generated 事件。"""
    if not os.path.isdir(generated_dir):
        return
    for name in sorted(os.listdir(generated_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(generated_dir, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if seen.get(name) == mtime:
            continue
        seen[name] = mtime
        try:
            with open(path, encoding="utf-8") as f:
                content = json.load(f)
        except Exception:
            continue
        emit({
            "type": "generated",
            "timestamp": datetime.now(UTC).isoformat(),
            "file_name": name,
            "data": content,
        })


def _start_generated_scanner(generated_dir: str, emit, interval: float = _GENERATED_SCAN_INTERVAL):
    """启动后台线程定时扫描 generated 目录，返回停止函数。

    停止函数会停掉线程并做一次最终扫描，确保捕获结束前最后写出的解。
    """
    seen: dict[str, float] = {}
    stop_event = threading.Event()

    def loop() -> None:
        while not stop_event.is_set():
            _scan_generated(generated_dir, emit, seen)
            stop_event.wait(interval)

    thread = threading.Thread(target=loop, name="generated-scan", daemon=True)
    thread.start()

    def stop() -> None:
        stop_event.set()
        thread.join(timeout=5)
        _scan_generated(generated_dir, emit, seen)

    return stop


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

    宿主机将运行目录挂载至 ``DATA_DIR`` 并写入加密配置（``CONFIG_FILENAME``，路径
    已转为容器内路径、敏感字段对称加密）。解密密钥经 ``LLM4AD_CONFIG_KEY`` 环境
    变量传入，运行用户代码前完成解密并立即从环境变量删除。运行结束后将最终 state
    写入 ``FINAL_STATE_FILENAME`` 并追加一条 final_state 事件。
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
        # 关闭 LLM4AD 控制台 handler：日志改走 events.jsonl，stdout/stderr 仅承载
        # 非结构化输出（依赖安装、用户 print、未捕获 traceback），避免重复采集。
        logging_cfg = data.get("logging") or {}
        logging_cfg["console"] = False
        data["logging"] = logging_cfg
        config = AppConfig.from_dict(data)

        # 切换工作目录到挂载的数据目录：配置以内存 AppConfig 对象传入，
        # LLM4AD 的 _config_dir 为 None，dataset/自定义评估器模块等相对路径
        # 会回退到 CWD 解析。若不切换，CWD 为镜像 WORKDIR(/app/backend)，
        # 相对路径将解析到错误位置（FileNotFoundError）。切到 DATA_DIR 后
        # 所有相对路径都落到挂载目录。
        os.chdir(DATA_DIR)

        llm4ad = LLM4AD(config)
        llm4ad.print_run_summary()

        # LLM4AD 初始化会 logger.remove() 重建 handler，故事件 sink 在其后安装。
        with EventsSink(os.path.join(DATA_DIR, EVENTS_FILENAME)) as events:
            sink_id = logger.add(events, level="INFO")
            # 定时扫描 generated 目录推送新解（不依赖具体日志文案）。
            stop_scanner = _start_generated_scanner(GENERATED_DIR, events.emit)
            try:
                result = asyncio.run(llm4ad.run(resume_from_checkpoint=None))
                logger.info(result.state.value)

                try:
                    final_state = llm4ad.export_state()
                    state_path = os.path.join(DATA_DIR, FINAL_STATE_FILENAME)
                    with open(state_path, "w", encoding="utf-8") as f:
                        json.dump(final_state, f, ensure_ascii=False, default=str)
                    events.emit({"type": "final_state", "data": final_state})
                except Exception:
                    pass

                logger.info("Task completed successfully")
            finally:
                stop_scanner()  # 停止扫描并做最终扫描，捕获最后写出的解
                try:
                    logger.remove(sink_id)  # 须早于 __exit__ 关文件，避免向已关闭句柄写
                except ValueError:
                    pass

    except Exception as exc:
        logger.error(f"Task failed: {exc}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
