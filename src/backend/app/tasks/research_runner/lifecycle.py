"""轮次终态原子写入 + 目录清理 + 孤儿 turn 清理。

**原子 finalize（终态守卫）**：``run_research_turn`` 主循环、``except`` 分支、以及
API 层同步 stop 都可能竞争把同一 turn 推到终态。这里用
``UPDATE ... WHERE status NOT IN (terminal)`` 的 rowcount 实现 test-and-set：
第一个到达终态的写入生效，后续调用无副作用（避免"取消后又被 FAILED 覆写"）。
所有终态写入统一走 :func:`finalize_turn`（借鉴 ``evolution.py:_finalize_task``）。

**Cancellation（本模块的取消模型，各处引用此处）**：用户 POST /stop 时 API 进程
直接 abort Celery 任务并 SIGKILL 研究容器、随即 ``finalize_turn`` 写 CANCELLED，
返回即终态（不用 Redis stop flag，无轮询延迟）。worker 侧 ``ContainerJob`` 看到
容器退出而返回，其 finally 再调 ``finalize_turn`` 被终态守卫短路；worker 轮询
Celery ``is_aborted`` 仅作兜底（如跨机 kill 不到容器时在 stage 边界自行 kill）。
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import update
from sqlmodel import select

from app.core.db import get_db_session
from app.models.research import (
    ResearchSession,
    ResearchSessionStatus,
    ResearchTurn,
    ResearchTurnStatus,
)

# ---- 终态原子写入 ----

_TERMINAL_TURN_STATUSES: tuple[str, ...] = (
    ResearchTurnStatus.COMPLETED.value,
    ResearchTurnStatus.FAILED.value,
    ResearchTurnStatus.CANCELLED.value,
    # PAUSED_GATE 是"命中门控、任务已结束等恢复"的落地态：一旦写入就不该再被
    # 后续兜底 finalize（如 except 分支的 FAILED）覆写，故计入终态守卫。
    ResearchTurnStatus.PAUSED_GATE.value,
)


def finalize_turn(
    *,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    turn_status: ResearchTurnStatus,
    session_status: ResearchSessionStatus,
    error: str | None = None,
) -> bool:
    """把 turn 与 session 原子推到终态；同一 turn 只有第一次调用生效。

    使用 ``UPDATE ... WHERE status NOT IN (terminal)`` 的 rowcount 判断：
    - rowcount==1：本次调用是"第一个到达终态"的，session 也随之写终态；
    - rowcount==0：其他路径已经把 turn 终态化了，本次调用不写任何东西
      （尤其是不写 session——避免 CANCELLED 之后又被 FAILED 覆写）。

    Returns:
        True = 本次调用生效；False = 已经是终态、本次是 no-op。
    """
    now = datetime.now(UTC)
    turn_values: dict = {
        "status": turn_status.value,
        "ended_at": now,
        "updated_time": now,
    }
    if error is not None:
        turn_values["error"] = error

    session_values: dict = {
        "status": session_status.value,
        "ended_time": now,
        "updated_time": now,
    }
    if error is not None:
        session_values["error"] = error

    try:
        with get_db_session() as db:
            stmt = (
                update(ResearchTurn)
                .where(ResearchTurn.id == turn_id)
                .where(ResearchTurn.status.notin_(_TERMINAL_TURN_STATUSES))
                .values(**turn_values)
            )
            result = db.execute(stmt)
            won = (result.rowcount or 0) > 0
            if won:
                db.execute(
                    update(ResearchSession)
                    .where(ResearchSession.id == session_id)
                    .values(**session_values)
                )
            db.commit()
            return won
    except Exception:
        # DB 挂了也不能让 finally 崩溃；worker 层还有兜底日志。
        logger.opt(exception=True).error(
            f"finalize_turn failed turn={turn_id} → {turn_status.value}"
        )
        return False


# ---- 磁盘清理（由 session 删除路径调用）----


def cleanup_run_dir(run_dir: str | None) -> None:
    """删除 run_dir（谨慎调用；仅在 session 删除时使用）。"""
    if not run_dir:
        return
    path = Path(run_dir)
    if not path.exists() or not path.is_dir():
        return
    try:
        shutil.rmtree(path)
    except OSError:
        logger.opt(exception=True).warning(f"cleanup_run_dir failed: {run_dir}")


# ---- Worker 启动时孤儿 turn 清理 ----


def _live_celery_task_ids() -> set[str] | None:
    """查询所有活 worker 上正在执行/预留的 Celery task_id 集合。

    多 worker / 滚动重启场景下，一个新 worker 启动时，别的活 worker 可能正跑着
    RUNNING 的 turn。若无差别地把所有 RUNNING turn 标 FAILED、删所有研究容器，
    就会误杀这些健康的在跑任务。这里用 Celery ``inspect`` 汇总全集群在跑的
    task_id，调用方据此把「有活 worker 在跑」的 turn 排除在清理之外。

    Returns:
        - task_id 集合（可能为空集，表示确实没有任何在跑任务）；
        - ``None``：inspect 不可用（broker 不支持 / 超时 / 异常）。调用方遇 None
          应退回「不排除」的旧行为，保证单 worker 部署下清理仍然生效。
    """
    try:
        from app.core.celery import celery_app

        inspector = celery_app.control.inspect(timeout=2.0)
        live: set[str] = set()
        found_any = False
        for source in (inspector.active(), inspector.reserved()):
            if not source:
                continue
            found_any = True
            for _worker, tasks in source.items():
                for task in tasks or []:
                    tid = task.get("id")
                    if tid:
                        live.add(str(tid))
        if not found_any:
            # 所有 inspect 回包都为 None：无 worker 应答（或 broker 不支持）。
            # 无法区分「真没任务」与「问不到」，保守返回 None 走旧行为。
            return None
        return live
    except Exception:
        logger.opt(exception=True).warning("inspect live celery tasks failed")
        return None


def sweep_orphan_running_turns() -> int:
    """Worker 启动时把**孤儿** RUNNING 的 turn 标记为 FAILED。

    场景：worker 上次因 SIGKILL/OOM 崩溃时，正在跑的 turn 状态卡在 RUNNING
    再没人推进；新 worker 起来后这些行会永远漂着。此函数由
    :func:`@signals.worker_init` 触发，把它们收敛到 FAILED，同时同步 session 状态。

    **多 worker 安全**：先经 :func:`_live_celery_task_ids` 查全集群在跑的 task_id，
    把「仍有活 worker 在跑」的 turn（其 ``celery_task_id`` 命中）排除，只清真正的
    孤儿——避免新 worker 启动误杀别的 worker 正跑的健康任务。inspect 不可用时退回
    「全部 RUNNING 视为孤儿」的旧行为（单 worker 部署无回归）。

    因为 :func:`finalize_turn` 是幂等的，多个 worker 同时启动也不会互相干扰
    （SQL 层 ``WHERE status NOT IN (terminal)`` 只让第一个胜出）。

    Returns:
        清理的 turn 数量。
    """
    cleaned = 0
    try:
        live_task_ids = _live_celery_task_ids()
        with get_db_session() as db:
            stmt = select(ResearchTurn).where(
                ResearchTurn.status == ResearchTurnStatus.RUNNING.value,
            )
            orphans = db.exec(stmt).all()
        for turn in orphans:
            # 有活 worker 在跑这一轮 → 不是孤儿，跳过。
            if live_task_ids is not None and turn.celery_task_id in live_task_ids:
                continue
            if finalize_turn(
                session_id=turn.session_id,
                turn_id=turn.id,
                turn_status=ResearchTurnStatus.FAILED,
                session_status=ResearchSessionStatus.FAILED,
                error="worker restarted while turn was in-flight",
            ):
                cleaned += 1
        if cleaned:
            logger.info(f"sweep_orphan_running_turns: cleaned {cleaned} turn(s)")

        # 协作孤儿单独收敛：worker 崩溃时在跑的 COLLABORATING turn 标 FAILED，但
        # **不把 session 标 FAILED**——协作断了不代表会话失败，会话仍暂停在 gate，
        # 故把 session 复位回 PAUSED（若它当时是 RUNNING）。
        cleaned += _sweep_orphan_collab_turns(live_task_ids)
    except Exception:
        logger.opt(exception=True).warning("sweep_orphan_running_turns failed")
    return cleaned


def _sweep_orphan_collab_turns(live_task_ids: set[str] | None = None) -> int:
    """把孤儿 COLLABORATING turn 标 FAILED。

    协作是叠加层、从不改 session 主状态，故这里只需收敛 turn 自身——session 状态
    （pending/paused/终态）本就没被协作动过，无需复位。

    ``live_task_ids`` 非 None 时，命中的 turn 表示仍有活 worker 在跑，跳过不清。
    """
    cleaned = 0
    now = datetime.now(UTC)
    try:
        with get_db_session() as db:
            orphans = db.exec(
                select(ResearchTurn).where(
                    ResearchTurn.status == ResearchTurnStatus.COLLABORATING.value
                )
            ).all()
            for turn in orphans:
                if (
                    live_task_ids is not None
                    and turn.celery_task_id in live_task_ids
                ):
                    continue
                won = (db.execute(
                    update(ResearchTurn)
                    .where(ResearchTurn.id == turn.id)
                    .where(ResearchTurn.status.notin_(_TERMINAL_TURN_STATUSES))
                    .values(
                        status=ResearchTurnStatus.FAILED.value,
                        error="worker restarted while collaboration was in-flight",
                        ended_at=now,
                        updated_time=now,
                    )
                ).rowcount or 0) > 0
                if won:
                    cleaned += 1
            db.commit()
    except Exception:
        logger.opt(exception=True).warning("_sweep_orphan_collab_turns failed")
    return cleaned
