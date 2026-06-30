"""演化算法任务模块。

定义 LLM4AD 演化算法的 Celery 异步任务，通过 Celery 信号自动同步任务状态到
数据库。任务在隔离容器中执行（见 :mod:`app.services.evolution_runner`）；日志与
指标经 Redis 实时推送，任务结束后持久化到数据库。
"""

import os
import traceback
from datetime import UTC, datetime

import celery.contrib.abortable
from celery import signals
from loguru import logger
from sqlalchemy import update
from sqlmodel import select

from app.core.celery import celery_app
from app.core.db import get_db_session
from app.core.redis import push_log_entry, read_all_logs
from app.models import Task, TaskStatus


@signals.worker_init.connect
def _on_worker_init(**kwargs):  # noqa: ARG001
    """Celery worker 启动时清理上次崩溃可能遗留的任务容器。"""
    try:
        from app.services.container_service import cleanup_orphaned_containers

        cleanup_orphaned_containers()
    except Exception as e:
        logger.error(f"清理孤儿容器失败: {e}")


# ---- 任务状态与终态收尾 ----


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


# ---- Celery 信号 ----


@signals.task_prerun.connect
def on_task_prerun(sender=None, task_id=None, **kwargs):  # noqa: ARG001
    """任务开始执行时，状态 → RUNNING"""
    if sender and sender.name == "app.tasks.evolution.run_evolution":
        _update_task_status(task_id, TaskStatus.RUNNING)


# ---- 运行环境准备与任务入口 ----


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
    """执行 LLM4AD 演化算法的 Celery 任务（容器隔离运行）。

    每次调用都是幂等的：执行前会清理上次运行的产物并重新下载输入数据。
    任务的终态写入与日志持久化集中在本函数的 try/except/finally 中，由
    :func:`_finalize_task` 唯一负责，避免与 API 端的停止逻辑产生并发写入。

    Args:
        data: 包含 run_args、run_dir、input_data_path 等的任务参数字典。
    """
    task_id = str(data["task_id"])
    final_status = TaskStatus.FAILED

    try:
        try:
            # 保证幂等：清理旧产物并重新下载输入数据
            _prepare_run_environment(data)

            from app.services.evolution_runner import run_evolution_container

            run_evolution_container(data, check_cancelled=self.is_aborted)
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
