"""
演化算法任务模块。

定义 LLM4AD 演化算法的 Celery 异步任务，
通过 Celery 信号自动同步任务状态到数据库。
日志和指标通过 Redis 实时推送，任务结束后持久化到数据库。
"""

import asyncio
import io
import json
import os
import re
import sys
import threading
import traceback
from datetime import UTC, datetime

import celery.contrib.abortable
from celery import signals
from llm4ad import LLM4AD
from llm4ad.config import AppConfig
from loguru import logger
from sqlalchemy import update
from sqlmodel import select

from app.core.celery import celery_app
from app.core.config import settings
from app.core.db import get_db_session
from app.core.redis import push_log_entry, read_all_logs
from app.models import Task, TaskStatus

# ---- 并发安全的 stdout/stderr 捕获 ----
# 使用 threading.local 存储当前线程/greenlet 的 task_id，
# 全局只安装一次代理流，多任务并发时互不干扰。

_task_local = threading.local()


class _TaskAwareStream(io.TextIOBase):
    """并发安全的 stdout/stderr 代理。

    始终写入原始流；当 _task_local.task_id 存在时，
    额外将输出推送到对应任务的 Redis 队列。
    gevent 会 monkey-patch threading.local 为 greenlet-local，
    因此在 gevent 并发下同样安全。
    """

    def __init__(self, original: io.TextIOBase, stream_name: str):
        self._original = original
        self._stream_name = stream_name

    def write(self, text: str) -> int:
        result = self._original.write(text)
        task_id = getattr(_task_local, "task_id", None)
        if task_id and text.strip():
            entry = {
                "type": "print",
                "timestamp": datetime.now(UTC).isoformat(),
                "stream": self._stream_name,
                "message": text.strip(),
            }
            push_log_entry(task_id, entry)
        return result

    def flush(self):
        self._original.flush()

    def fileno(self):
        return self._original.fileno()

    def isatty(self):
        return self._original.isatty()

    @property
    def encoding(self):
        return getattr(self._original, "encoding", "utf-8")


_stream_installed = False


def _install_stream_capture():
    """安装全局 stdout/stderr 代理（仅执行一次）。"""
    global _stream_installed
    if _stream_installed:
        return
    sys.stdout = _TaskAwareStream(sys.stdout, "stdout")
    sys.stderr = _TaskAwareStream(sys.stderr, "stderr")
    _stream_installed = True


@signals.worker_init.connect
def _on_worker_init(**kwargs):  # noqa: ARG001
    """Celery worker 启动时安装 stdout/stderr 代理并清理孤儿容器。"""
    _install_stream_capture()
    # 清理上次 worker 崩溃后可能遗留的任务容器
    if settings.TASK_CONTAINER_ENABLE:
        try:
            from app.services.container_service import cleanup_orphaned_containers

            cleanup_orphaned_containers()
        except Exception as e:
            logger.error(f"清理孤儿容器失败: {e}")


# ---- 工具函数 ----


def _sanitize_for_json(obj):
    """将非有限浮点数（inf、-inf、nan）替换为 None，便于严格 JSON 序列化。

    .. deprecated::
        请使用 :func:`app.utils.log_persist.sanitize_for_json`。
    """
    from app.utils.log_persist import sanitize_for_json

    return sanitize_for_json(obj)


def _update_task_status(celery_task_id: str, status: TaskStatus) -> str | None:
    """根据 Celery 任务 ID 更新数据库中对应 Task 的状态，并推送状态事件到 Redis 队列。

    Returns:
        业务 task_id（字符串），未找到时返回 None。
    """
    try:
        with get_db_session() as session:
            query = select(Task).where(Task.celery_task_id == celery_task_id)
            task = session.exec(query).first()
            if task:
                task.status = status
                task.updated_time = datetime.now(UTC)
                session.add(task)
                session.commit()
                push_log_entry(
                    task.id,
                    {
                        "type": "status",
                        "status": status.value,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )
                logger.info(f"任务 {task.id} 状态已更新为 {status}")
                return str(task.id)
    except Exception as e:
        logger.error(f"更新任务状态失败，celery_task_id={celery_task_id}: {e}")
    return None


def _finalize_task(celery_task_id: str, status: TaskStatus) -> None:
    """任务终态收尾的唯一入口：将 Redis 日志持久化到 DB、写入终态、推送 end 事件。

    无论任务是成功完成、还是因手动中止/容器中断/代码异常等任意原因失败，
    都必须经过本函数收尾，避免其它代码路径重复持久化日志。

    幂等性（并发安全）：API 端的停止逻辑与 worker 的 finally 可能几乎同时
    调用本函数。通过单条原子 ``UPDATE Task SET status=... WHERE status NOT
    IN (终态)`` 作 test-and-set：数据库保证只有一个调用者的 ``rowcount``
    为 1，由它独占地读 Redis、写 ``TaskLog``；其余调用者 ``rowcount`` 为
    0，直接跳过日志持久化。这样不依赖显式行锁，也不受事务隔离级别影响。
    所有异常仅记录日志、不重新抛出，以保证 Celery 任务能正常对外传播原
    始异常。

    Args:
        celery_task_id: Celery 任务 ID，用于反查业务 Task。
        status: 终态（COMPLETED 或 FAILED）。
    """
    try:
        with get_db_session() as session:
            now = datetime.now(UTC)
            # 原子 test-and-set：只有一个并发调用者能把状态从非终态翻为终态
            stmt = (
                update(Task)
                .where(Task.celery_task_id == celery_task_id)
                .where(Task.status.not_in([TaskStatus.COMPLETED, TaskStatus.FAILED]))
                .values(status=status, updated_time=now)
            )
            result = session.execute(stmt)
            session.commit()

            task = session.exec(
                select(Task).where(Task.celery_task_id == celery_task_id)
            ).first()
            if not task:
                logger.warning(
                    f"_finalize_task: 未找到 celery_task_id={celery_task_id} 对应的任务"
                )
                return

            biz_task_id = str(task.id)

            if result.rowcount == 0:
                # 另一个调用者已完成 finalize，跳过日志持久化
                logger.info(f"任务 {biz_task_id} 已被其他调用者 finalize，跳过")
                return

            from app.utils.log_persist import bulk_insert_task_logs, sanitize_for_json

            logs = read_all_logs(task.id)
            log_count = 0
            if logs:
                logs = sanitize_for_json(logs)
                bulk_insert_task_logs(session, task.id, logs)
                session.commit()
                log_count = len(logs)

            push_log_entry(
                biz_task_id,
                {
                    "type": "status",
                    "status": status.value,
                    "timestamp": now.isoformat(),
                },
            )
            push_log_entry(
                biz_task_id,
                {"type": "end", "timestamp": now.isoformat()},
            )
            logger.info(
                f"任务 {biz_task_id} 已完成 finalize: status={status.value}, 持久化日志 {log_count} 条"
            )

            # 吊销本任务发放的 LLM 代理 token，避免任务结束后仍可经代理调用大模型。
            # TTL 也会兜底失效；此处主动吊销以尽早收紧权限。
            try:
                from app.services.credential_broker import revoke_task_tokens

                revoked = revoke_task_tokens(biz_task_id)
                if revoked:
                    logger.info(f"任务 {biz_task_id} 吊销 {revoked} 个 LLM 代理 token")
            except Exception:
                logger.error(f"吊销任务 {biz_task_id} 的 LLM 代理 token 失败", exc_info=True)
    except Exception as e:
        logger.error(f"_finalize_task 失败，celery_task_id={celery_task_id}: {e}")


def _make_redis_log_sink(task_id: str, run_dir: str):
    """创建 loguru sink 工厂，将日志条目推送到 Redis。

    当检测到 "Generation ... successful" 日志时，
    扫描 generated 目录推送新产生的 JSON 解文件。
    """
    # 记录已推送文件：key = 文件名, value = 修改时间
    seen_files: dict[str, float] = {}
    generated_dir = os.path.join(run_dir, "llm4ad", "run", "generated")

    def _push_new_generated_files():
        """扫描 generated 目录，推送新产生的 JSON 文件。"""
        if not os.path.isdir(generated_dir):
            return
        for filename in os.listdir(generated_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(generated_dir, filename)
            try:
                mtime = os.path.getmtime(filepath)
            except OSError:
                continue
            if seen_files.get(filename) == mtime:
                continue
            # 新文件或已更新的文件
            seen_files[filename] = mtime
            try:
                with open(filepath, encoding="utf-8") as f:
                    content = json.load(f)
                entry = {
                    "type": "generated",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "file_name": filename,
                    "data": content,
                }
                push_log_entry(task_id, entry)
            except Exception:
                pass

    def sink(message):
        record = message.record
        entry = {
            "type": "log",
            "timestamp": record["time"].isoformat(),
            "level": record["level"].name,
            "message": record["message"],
            "module": record["name"],
            "function": record["function"],
            "line": record["line"],
        }
        push_log_entry(task_id, entry)

        # 检测到新解产生时，推送 generated 文件
        msg = record["message"]
        # 日志中触发刷新的标识，island_ga和dyca不同
        if "Successfully written to the Generated directory" in msg:
            _push_new_generated_files()

    return sink


def _start_metrics_pusher(
    task_id: str,  # noqa: ARG001
    llm4ad_instance: LLM4AD,  # noqa: ARG001
    stop_event: threading.Event,
):
    """启动后台线程，定期将 StateTracker 的 metrics 推送到 Redis。"""

    def _push_loop():
        while not stop_event.is_set():
            stop_event.wait(2.0)
            try:
                # TODO 后续可能支持直接从llm4ad_instance获取算法结果
                pass
            except Exception:
                pass

    thread = threading.Thread(target=_push_loop, daemon=True)
    thread.start()
    return thread


@signals.task_prerun.connect
def on_task_prerun(sender=None, task_id=None, **kwargs):  # noqa: ARG001
    """任务开始执行时，状态 → RUNNING"""
    if sender and sender.name == "app.tasks.evolution.run_evolution":
        _update_task_status(task_id, TaskStatus.RUNNING)


@signals.task_retry.connect
def on_task_retry(sender=None, **kwargs):
    """任务重试时，保持 RUNNING 状态"""
    if sender and sender.name == "app.tasks.evolution.run_evolution":
        request = kwargs.get("request")
        if request:
            _update_task_status(request.id, TaskStatus.RUNNING)


def _prepare_run_environment(data: dict) -> None:
    """清理上次运行产物并准备输入数据，保证幂等执行。

    Redis 日志和数据库日志已在提交 Celery 任务前由 run_task() 清除。
    本函数负责：
    1. 删除整个运行目录，确保干净环境。
    2. 从 S3 重新下载输入数据到运行目录。

    Args:
        data: 任务数据字典，包含 task_id、run_dir 和 input_data_path。
    """
    import shutil

    from app.core.storage import storage

    task_id = str(data["task_id"])
    run_dir = data["run_dir"]

    # 1. 删除整个运行目录
    push_log_entry(
        task_id, {"type": "system", "message": "正在初始化运行目录...", "timestamp": datetime.now(UTC).isoformat()}
    )
    if os.path.isdir(run_dir):
        try:
            shutil.rmtree(run_dir)
            logger.info(f"已清理上次运行目录: {run_dir}")
        except Exception as e:
            logger.warning(f"清理运行目录失败 {run_dir}: {e}")

    # 2. 从 S3 重新下载输入数据
    push_log_entry(
        task_id, {"type": "system", "message": "正在初始化输入数据...", "timestamp": datetime.now(UTC).isoformat()}
    )
    input_data_path = data.get("input_data_path")
    if input_data_path:
        if not storage.download_files_local(input_data_path, local_path=run_dir, strip_first_level=True):
            raise RuntimeError(f"从 {input_data_path} 下载输入数据失败")

    # 3. 写入说明文件，提示本地编辑不会同步回任务参数
    push_log_entry(
        task_id, {"type": "system", "message": "正在初始化运行环境...", "timestamp": datetime.now(UTC).isoformat()}
    )
    os.makedirs(run_dir, exist_ok=True)
    notice_path = os.path.join(run_dir, "README.txt")
    try:
        with open(notice_path, "w", encoding="utf-8") as f:
            f.write(
                "注意事项\n"
                "========\n"
                "本目录中的文件在任务启动时从存储复制而来，"
                "每次重新运行都会被覆盖。\n\n"
                "在此处编辑文件不会更新任务参数。\n"
                "如需修改参数并重新运行，请使用任务面板中的"
                "参数调整功能。\n"
            )
    except Exception as e:
        logger.warning(f"写入说明文件失败 {run_dir}: {e}")
    push_log_entry(
        task_id, {"type": "system", "message": "任务运行环境准备完成...", "timestamp": datetime.now(UTC).isoformat()}
    )


@celery_app.task(bind=True, base=celery.contrib.abortable.AbortableTask)
def run_evolution(self, data: dict):
    """执行 LLM4AD 演化算法的 Celery 任务。

    每次调用都是幂等的：执行前会清理上次运行的产物（日志、输出）。

    任务的终态写入与日志持久化集中在本函数的 try/except/finally 中，
    通过 :func:`_finalize_task` 唯一负责，避免与 API 端的停止逻辑产生
    并发写入导致重复日志。

    Args:
        data: 包含 run_args（运行参数）和 run_dir（运行目录）的字典。
    """
    task_id = str(data["task_id"])
    final_status = TaskStatus.FAILED

    try:
        try:
            # 保证幂等：清理旧产物并重新下载输入数据
            _prepare_run_environment(data)

            if settings.TASK_CONTAINER_ENABLE:
                _run_in_container(self, data, task_id)
            else:
                _run_in_process(data, task_id)
        except BaseException as exc:
            tb_str = traceback.format_exc()
            push_log_entry(
                task_id,
                {
                    "type": "error",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback": tb_str,
                },
            )
            raise
        else:
            final_status = TaskStatus.COMPLETED
    finally:
        _finalize_task(self.request.id, final_status)


def _run_in_container(celery_task, data: dict, task_id: str):
    """在隔离的 Docker 容器中执行任务。

    容器**不直接访问** Redis 或数据库；Celery worker 实时读取容器
    stdout/stderr，并将日志转发到 Redis。

    异常在外层 :func:`run_evolution` 的 except 中统一记录，本函数仅负责
    资源清理（finally 块），避免重复推送 error 日志。

    Args:
        celery_task: 当前 Celery AbortableTask 实例，用于检查取消信号。
        data: 任务参数字典，包含 run_dir、run_args 等。
        task_id: 业务任务 ID。
    """
    from app.services import container_service

    container_id = None
    log_stop = threading.Event()
    log_thread = None
    seen_generated: dict[str, float] = {}
    generated_dir = os.path.join(data["run_dir"], "llm4ad", "run", "generated")

    start_info = f"[独立容器模式] 启动任务容器: task_id={task_id}"
    push_log_entry(task_id, {"type": "system", "message": start_info, "timestamp": datetime.now(UTC).isoformat()})
    try:
        logger.info(start_info)

        container_id = container_service.create_and_start_task_container(data)

        log_thread = threading.Thread(
            target=_relay_container_logs,
            args=(container_id, task_id, data["run_dir"], log_stop, seen_generated),
            daemon=True,
        )
        log_thread.start()

        result = container_service.wait_for_container(
            container_id,
            check_cancelled=lambda: celery_task.is_aborted(),
        )

        if result.get("cancelled"):
            raise RuntimeError("任务已被取消")
        if result.get("timed_out"):
            raise TimeoutError("任务容器执行超时")
        if result.get("oom_killed"):
            raise MemoryError(f"任务容器因内存超限被终止（限制: {settings.TASK_CONTAINER_MEMORY_LIMIT}）")
        if result["exit_code"] != 0:
            raise RuntimeError(f"任务容器异常退出，exit_code={result['exit_code']}")

        logger.info(f"[容器模式] 任务完成: task_id={task_id}")

    finally:
        log_stop.set()
        if log_thread is not None:
            log_thread.join(timeout=10)
        _push_generated_files(task_id, generated_dir, seen_generated)
        _push_container_final_state(task_id, data["run_dir"])
        if container_id:
            container_service.cleanup_container(container_id)


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

_LOGURU_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})"
    r" \| (\w+)\s*"
    r"\| ([\w.<>]+):([\w.<>]+):(\d+)"
    r" -\s?(.*)",
    re.DOTALL,
)


def _parse_loguru_line(line: str) -> dict | None:
    """将一行 loguru 格式的日志解析为结构化字典。

    Args:
        line: 单行日志文本。

    Returns:
        若匹配 loguru 格式则返回结构化字典，否则返回 None。
    """
    m = _LOGURU_PATTERN.match(line)
    if not m:
        return None
    timestamp_str, level, module, function, lineno, message = m.groups()
    try:
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
        iso_ts = dt.replace(tzinfo=UTC).isoformat()
    except ValueError:
        iso_ts = timestamp_str
    return {
        "type": "log",
        "timestamp": iso_ts,
        "level": level.strip(),
        "message": message,
        "module": module,
        "function": function,
        "line": int(lineno),
    }


def _relay_container_logs(
    container_id: str,
    task_id: str,
    run_dir: str,
    stop_event: threading.Event,
    seen_generated: dict[str, float],
) -> None:
    """实时拉取 Docker 容器 stdout/stderr 并转发到 Redis。

    所有未匹配 loguru 格式的行以 ``"print"`` 类型推送；同时监听
    ``generated`` 目录，发现新解文件时推送其内容。

    Args:
        container_id: 容器 ID。
        task_id: 业务任务 ID。
        run_dir: 任务运行目录（宿主机路径）。
        stop_event: 用于外部通知停止读取的事件。
        seen_generated: 已推送的 generated 文件名到修改时间的映射。
    """
    from app.core.docker import get_docker_client

    generated_dir = os.path.join(run_dir, "llm4ad", "run", "generated")

    try:
        container = get_docker_client().containers.get(container_id)

        line_buffer = ""
        pending_entry: dict | None = None

        for chunk in container.logs(stream=True, follow=True, timestamps=False):
            if stop_event.is_set():
                break

            text = chunk.decode("utf-8", errors="replace")
            text = _ANSI_ESCAPE.sub("", text)
            line_buffer += text

            while "\n" in line_buffer:
                line, line_buffer = line_buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                parsed = _parse_loguru_line(line)
                if parsed:
                    if pending_entry:
                        push_log_entry(task_id, pending_entry)
                        msg = pending_entry.get("message", "")
                        if "Successfully written to the Generated directory" in msg:
                            _push_generated_files(task_id, generated_dir, seen_generated)
                    pending_entry = parsed
                elif pending_entry:
                    pending_entry["message"] += "\n" + line
                else:
                    push_log_entry(
                        task_id,
                        {
                            "type": "print",
                            "timestamp": datetime.now(UTC).isoformat(),
                            "stream": "stdout",
                            "message": line,
                        },
                    )

        if line_buffer.strip():
            tail = line_buffer.strip()
            parsed = _parse_loguru_line(tail)
            if parsed:
                if pending_entry:
                    push_log_entry(task_id, pending_entry)
                pending_entry = parsed
            elif pending_entry:
                pending_entry["message"] += "\n" + tail
            else:
                push_log_entry(
                    task_id,
                    {
                        "type": "print",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "stream": "stdout",
                        "message": tail,
                    },
                )

        if pending_entry:
            push_log_entry(task_id, pending_entry)

        _push_generated_files(task_id, generated_dir, seen_generated)

    except Exception:
        logger.debug(f"Container log relay stopped for {container_id[:12]}", exc_info=True)


def _push_generated_files(
    task_id: str,
    generated_dir: str,
    seen_files: dict[str, float],
) -> None:
    """扫描 generated 目录，将新增/更新的 JSON 解文件推送到 Redis。

    Args:
        task_id: 业务任务 ID。
        generated_dir: 任务生成目录（宿主机路径）。
        seen_files: 已推送文件名到修改时间的映射，原地更新。
    """
    if not os.path.isdir(generated_dir):
        return
    for filename in os.listdir(generated_dir):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(generated_dir, filename)
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            continue
        if seen_files.get(filename) == mtime:
            continue
        seen_files[filename] = mtime
        try:
            with open(filepath, encoding="utf-8") as f:
                content = json.load(f)
            push_log_entry(
                task_id,
                {
                    "type": "generated",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "file_name": filename,
                    "data": content,
                },
            )
        except Exception:
            pass


def _push_container_final_state(task_id: str, run_dir: str) -> None:
    """读取容器写入的 ``.final_state.json`` 并推送到 Redis。"""
    state_path = os.path.join(run_dir, ".final_state.json")
    if not os.path.isfile(state_path):
        return
    try:
        with open(state_path, encoding="utf-8") as f:
            final_state = json.load(f)
        push_log_entry(task_id, {"type": "final_state", "data": final_state})
    except Exception:
        logger.debug(f"Failed to read final state from {state_path}", exc_info=True)


def _run_in_process(data: dict, task_id: str):
    """在 Celery worker 进程内直接执行任务（旧模式）。

    异常在外层 :func:`run_evolution` 的 except 中统一记录，本函数仅负责
    资源清理（finally 块）。
    """
    # 设置当前线程/greenlet 的 task_id，_TaskAwareStream 据此路由输出
    _task_local.task_id = task_id

    sink_id = None
    metrics_stop = None
    metrics_thread = None
    start_info = f"[worker模式] task_id={task_id}"
    push_log_entry(task_id, {"type": "system", "message": start_info, "timestamp": datetime.now(UTC).isoformat()})
    try:
        # 构建应用配置，覆盖 base_dir 为实际运行目录
        config = AppConfig(**data["run_args"]).model_copy(update={"base_dir": data["run_dir"], "run_id": "run"})

        # LLM4AD 初始化（内部调用 logger.remove() 重建 handlers）
        llm4ad = LLM4AD(config)
        llm4ad.print_run_summary()

        # 在 LLM4AD 初始化之后添加 Redis sink（避免被 logger.remove() 清除）
        sink_id = logger.add(_make_redis_log_sink(task_id, data["run_dir"]), level="INFO")
        logger.info(f"开始处理演化任务: {data}")

        # 启动 metrics 推送后台线程【尚未启用】
        metrics_stop = threading.Event()
        metrics_thread = _start_metrics_pusher(task_id, llm4ad, metrics_stop)

        # 运行演化算法（不用 asyncio.run，避免与已有事件循环冲突）
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(llm4ad.run(resume_from_checkpoint=None))
        finally:
            loop.close()
        logger.info(result.state.value)

        # 推送最终的完整 state 数据
        try:
            final_state = llm4ad.export_state()
            push_log_entry(task_id, {"type": "final_state", "data": final_state})
        except Exception:
            pass

        return {"status": "success", "processed": data}

    finally:
        # 清除当前线程的 task_id
        _task_local.task_id = None
        # 停止 metrics 线程
        if metrics_stop is not None:
            metrics_stop.set()
        if metrics_thread is not None:
            metrics_thread.join(timeout=5)
        # 移除 Redis sink（并发任务中 LLM4AD 初始化可能已清除该 sink）
        if sink_id is not None:
            try:
                logger.remove(sink_id)
            except ValueError:
                pass
