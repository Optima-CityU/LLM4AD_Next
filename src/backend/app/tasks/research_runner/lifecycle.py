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


def sweep_orphan_running_turns() -> int:
    """Worker 启动时把所有 RUNNING 的 turn 一次性标记为 FAILED。

    场景：worker 上次因 SIGKILL/OOM 崩溃时，正在跑的 turn 状态卡在 RUNNING
    再没人推进；新 worker 起来后这些行会永远漂着。此函数由
    :func:`@signals.worker_init` 触发，把它们收敛到 FAILED，同时同步 session 状态。

    因为 :func:`finalize_turn` 是幂等的，多个 worker 同时启动也不会互相干扰
    （SQL 层 ``WHERE status NOT IN (terminal)`` 只让第一个胜出）。

    Returns:
        清理的 turn 数量。
    """
    cleaned = 0
    try:
        with get_db_session() as db:
            stmt = select(ResearchTurn).where(
                ResearchTurn.status == ResearchTurnStatus.RUNNING.value,
            )
            orphans = db.exec(stmt).all()
        for turn in orphans:
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
        cleaned += _sweep_orphan_collab_turns()
    except Exception:
        logger.opt(exception=True).warning("sweep_orphan_running_turns failed")
    return cleaned


def _sweep_orphan_collab_turns() -> int:
    """把孤儿 COLLABORATING turn 标 FAILED。

    协作是叠加层、从不改 session 主状态，故这里只需收敛 turn 自身——session 状态
    （pending/paused/终态）本就没被协作动过，无需复位。
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
