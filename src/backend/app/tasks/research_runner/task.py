"""Celery 任务入口 ``run_research_turn`` + 排队辅助 + Celery signal hooks。

**流程分层**（详见包 docstring）：

1. 挂 signals（``@task_prerun`` 转 RUNNING、``@worker_init`` 扫孤儿）；
2. ``run_research_turn`` 主体只做流程编排，具体阶段拆到 5 个 phase helper。

取消模型 + 终态守卫见 :mod:`.lifecycle` 模块 docstring。
"""

from __future__ import annotations

import traceback
import uuid
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import celery.contrib.abortable
from celery import signals
from loguru import logger

from app.core.celery import celery_app
from app.core.db import get_db_session
from app.core.redis import (
    clear_research_generation_id,
    set_research_generation_id,
)
from app.models.research import (
    ResearchSession,
    ResearchSessionStatus,
    ResearchTurn,
    ResearchTurnStatus,
)

from .config_builder import (
    _DEFAULT_HITL_MODE,
    build_arc_config,
    proxy_provider_for_arc,
    resolve_provider_for_arc,
)
from .lifecycle import (
    finalize_turn,
    sweep_orphan_running_turns,
)
from .snapshots import (
    SessionSnapshot,
    TurnSnapshot,
    resolve_run_dir,
    snap_session,
    snap_turn,
)
from .streaming import (
    ResearchEventSink,
    build_stage_progress_callback,
    persist_gate_pause,
)

_TASK_NAME = "research.run_turn"


# ---- Celery signals ----


@signals.task_prerun.connect
def _on_research_task_prerun(sender=None, task_id=None, args=None, **kwargs):  # noqa: ARG001
    """任务开始执行前：把 turn 状态推到 RUNNING、记录 started_at。

    放 task_prerun 而非任务体：worker 在任务体首行前 crash（如 import 失败）时，
    仍能靠 :func:`sweep_orphan_running_turns` 在下次 worker_init 兜底。session 指针
    在 server 层已设，此处重复设只作幂等兜底。
    """
    if sender is None or getattr(sender, "name", None) != _TASK_NAME:
        return
    if not args or not isinstance(args[0], dict):
        return
    data = args[0]
    try:
        session_id = uuid.UUID(data["session_id"])
        turn_id = uuid.UUID(data["turn_id"])
    except (KeyError, ValueError, TypeError):
        return
    try:
        with get_db_session() as db:
            turn = db.get(ResearchTurn, turn_id)
            session = db.get(ResearchSession, session_id)
            if not turn or not session:
                return
            turn.started_at = datetime.now(UTC)
            turn.status = ResearchTurnStatus.RUNNING.value
            turn.updated_time = datetime.now(UTC)
            session.status = ResearchSessionStatus.RUNNING.value
            session.active_turn_id = turn_id
            session.updated_time = datetime.now(UTC)
            db.add(turn)
            db.add(session)
            db.commit()
    except Exception:
        logger.opt(exception=True).warning(
            f"task_prerun RUNNING transition failed turn={turn_id}"
        )


@signals.worker_init.connect
def _on_research_worker_init(**kwargs):  # noqa: ARG001
    """Worker 启动时清理孤儿 RUNNING turn + 孤儿研究容器。

    多 worker 并发启动也无害：finalize_turn 是幂等的。容器清理只按 ``turn_id``
    标签过滤，只回收本模块起的研究容器（不误伤演化/调参容器）。
    """
    try:
        sweep_orphan_running_turns()
    except Exception:
        logger.opt(exception=True).warning("research worker_init sweep failed")
    try:
        from app.services.container_runtime import cleanup_orphaned_containers
        cleanup_orphaned_containers(extra_filters={"label": "turn_id"})
    except Exception:
        logger.opt(exception=True).warning(
            "research worker_init container cleanup failed"
        )


# ---- Phase helpers（把 run_research_turn 主体拆开）----


def _bootstrap(
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
) -> tuple[SessionSnapshot, TurnSnapshot, Path]:
    """Phase 1：加载 session+turn 快照，准备 run_dir。

    RUNNING 状态由 :func:`_on_research_task_prerun` 提前写入，本函数
    只读快照，不再动 session/turn 状态。
    """
    with get_db_session() as db:
        session = db.get(ResearchSession, session_id)
        turn = db.get(ResearchTurn, turn_id)
        if not session or not turn:
            raise RuntimeError(f"session or turn not found: {session_id} / {turn_id}")
        session_snap = snap_session(session)
        turn_snap = snap_turn(turn)
    run_dir = resolve_run_dir(session_snap)
    run_dir.mkdir(parents=True, exist_ok=True)
    return session_snap, turn_snap, run_dir


def _build_arc_config_dict(
    session_snap: SessionSnapshot,
    turn_snap: TurnSnapshot,
    run_dir: Path,
) -> dict[str, Any]:
    """解析 provider + 构造 arc_config dict（含 inline api_key）+ 脱敏快照落库。

    该 dict 会被加密后交给隔离容器解密运行（api_key 走加密信道，绝不走 env）。
    """
    provider_config = resolve_provider_for_arc(
        turn_snap.provider_id or session_snap.provider_id,
        turn_snap.model_name or session_snap.model_name,
        user_id=session_snap.user_id,
    )
    proxy_provider_for_arc(provider_config, user_id=session_snap.user_id, task_id=turn_snap.id)
    arc_config_dict = build_arc_config(
        session=session_snap,
        turn=turn_snap,
        run_dir=run_dir,
        provider_config=provider_config,
    )
    # API key 走 config.llm.api_key（per-turn 隔离），绝不走 os.environ
    if api_key := provider_config.get("api_key", ""):
        arc_config_dict["llm"]["api_key"] = api_key
    # 脱敏快照（deepcopy 去掉 api_key）落 session.latest_config；原 dict 保留 key
    _persist_config_snapshot(session_snap.id, run_dir, arc_config_dict)
    return arc_config_dict


def _persist_config_snapshot(
    session_id: uuid.UUID,
    run_dir: Path,
    arc_config_dict: dict[str, Any],
) -> None:
    """脱敏后把 arc_config 快照到 session.latest_config，同步 run_dir 字段。"""
    redacted = deepcopy(arc_config_dict)
    redacted.get("llm", {}).pop("api_key", None)
    try:
        with get_db_session() as db:
            session = db.get(ResearchSession, session_id)
            if session:
                session.run_dir = str(run_dir)
                session.latest_config = redacted
                session.updated_time = datetime.now(UTC)
                db.add(session)
                db.commit()
    except Exception:
        logger.opt(exception=True).warning("persist_config_snapshot failed")


def _marker_from_container_status(result: Any, status_enum: Any) -> dict[str, Any]:
    """容器未发 ``__result__``（崩溃 / 被 kill）时，据容器退出状态兜底合成 marker。

    与演化 ``run_evolution_container`` 同款按 ``result.status`` 判定，并对 OOM /
    超时给出明确 error 文案（否则用户只看到笼统的 exit_code）。CANCELLED 保留为
    独立 outcome（用户主动停不算失败）。
    """
    from app.core.config import settings

    if result.status is status_enum.CANCELLED:
        return {"outcome": "cancelled"}
    if result.status is status_enum.COMPLETED and result.exit_code == 0:
        return {"outcome": "done", "stages_done": 0, "stages_failed": 0}
    if result.status is status_enum.OOM:
        return {
            "outcome": "failed",
            "error": f"研究容器因内存超限被终止（限制: {settings.RESEARCH_CONTAINER_MEMORY_LIMIT}）",
        }
    if result.status is status_enum.TIMED_OUT:
        return {"outcome": "failed", "error": "研究容器执行超时"}
    return {
        "outcome": "failed",
        "error": result.error or f"container exit_code={result.exit_code}",
    }


def _run_pipeline(
    session_snap: SessionSnapshot,
    turn_snap: TurnSnapshot,
    run_dir: Path,
    sink: ResearchEventSink,
    *,
    turn_id: uuid.UUID,
    hitl_mode: str | None,
    from_stage: str | None,
    to_stage: str | None,
    is_aborted: Callable[[], bool],
) -> dict[str, Any]:
    """在隔离容器内跑一次 ``execute_pipeline``，返回统一终态 marker。

    容器把 ARC 日志 / stage 进度 / 终态都写进 ``.events.jsonl``，本函数经
    ``ContainerJob`` tail 后转发到 sink（Redis + DB）。终态 marker 从容器的
    ``__result__`` 事件捕获；容器崩溃 / 被 kill 未发 ``__result__`` 时据
    ``result.status`` 兜底合成。取消模型见 :mod:`.lifecycle` 模块 docstring。
    """
    from app.services.container_runtime import ContainerJobStatus
    from app.services.research_pipeline_runner import run_research_pipeline_container

    arc_config_dict = _build_arc_config_dict(session_snap, turn_snap, run_dir)
    stage_cb = build_stage_progress_callback(sink)

    # 容器经 __result__ 事件回传终态 marker（gate/done/failed）。用 holder 承接：
    # 容器正常跑完必发一条；被 kill/崩溃时 holder 为空，下面据 result.status 兜底。
    result_holder: dict[str, Any] = {}

    def on_event(ev: dict[str, Any]) -> None:
        try:
            etype = ev.get("type")
            if etype == "__progress__":
                # 容器透传的 ARC 原始 progress payload → 翻译成 stage_transition
                stage_cb(ev.get("payload") or {})
            elif etype == "__result__":
                # 容器自报终态 marker：本轮实时产出，不落盘（无跨轮残留问题）
                marker = ev.get("marker")
                if isinstance(marker, dict):
                    result_holder["marker"] = marker
            else:
                sink.emit(ev)
        except Exception:
            logger.debug("research container on_event error", exc_info=True)

    def on_stdout(line: str) -> None:
        sink.emit({
            "type": "log", "level": "INFO", "message": line, "source": "container",
        })

    stop_logged = {"done": False}

    def check_cancelled() -> bool:
        if not is_aborted():
            return False
        if not stop_logged["done"]:
            stop_logged["done"] = True
            sink.emit({
                "type": "log",
                "level": "WARNING",
                "message": "stop requested — killing research container",
                "source": "bridge",
            })
        return True

    try:
        result = run_research_pipeline_container(
            arc_config_dict=arc_config_dict,
            run_dir=str(run_dir),
            turn_id=str(turn_id),
            from_stage=from_stage,
            to_stage=to_stage,
            hitl_mode=hitl_mode or _DEFAULT_HITL_MODE,
            guidance=(turn_snap.user_input or "").strip() or None,
            on_event=on_event,
            on_stdout=on_stdout,
            check_cancelled=check_cancelled,
        )
    except Exception as exc:
        logger.opt(exception=True).error(f"research container run failed: {exc}")
        return {"outcome": "failed", "error": f"container run error: {exc}"}

    # 优先用容器经 __result__ 回传的 marker（含 gate / stage 计数）；容器崩溃 / 被
    # kill 时未发 __result__，据容器退出状态兜底（演化同款 result.status 判定）。
    marker = result_holder.get("marker")
    if marker is not None:
        return marker
    return _marker_from_container_status(result, ContainerJobStatus)


def _finalize(
    *,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    marker: dict[str, Any],
    cancelled: bool,
    sink: ResearchEventSink,
) -> tuple[str, int, int]:
    """Phase 5：据统一终态标记 ``marker`` 按 (cancelled → gate → failed → success)
    优先级经 :func:`finalize_turn` 原子写终态（终态守卫见 :mod:`.lifecycle`）。

    **cancelled 优先**：用户 stop 时可能同时有异常/部分产物，先判 cancelled 避免
    错标为 FAILED。**gate 次之**：命中硬门控（marker.outcome=="gate"）**不是失败**：
    落 form 消息、turn → ``PAUSED_GATE``、session → ``PAUSED``，任务结束释放 worker；
    等用户回复后由 :func:`services.research_service._reply_to_gate` 新建一轮续跑。
    """
    done_count = int(marker.get("stages_done") or 0)
    failed_count = int(marker.get("stages_failed") or 0)
    outcome = str(marker.get("outcome") or "done")

    if cancelled or outcome == "cancelled":
        status_str = "cancelled"
        finalize_turn(
            session_id=session_id, turn_id=turn_id,
            turn_status=ResearchTurnStatus.CANCELLED,
            session_status=ResearchSessionStatus.CANCELLED,
            error=marker.get("error"),
        )
    elif outcome == "gate":
        status_str = "gate"
        # 落 gate form 消息 + emit waiting_for_input（供前端渲染表单）。
        # available_actions / context_summary 直接透传原生 WaitingState，
        # 与 CLI --mode 在该 stage 给出的动作一一对应。
        persist_gate_pause(
            sink=sink,
            session_id=session_id,
            turn_id=turn_id,
            stage_num=int(marker.get("gate_stage") or 0),
            stage_name=str(marker.get("gate_stage_name") or ""),
            reason=str(marker.get("gate_reason") or ""),
            output_files=list(marker.get("gate_output_files") or []),
            available_actions=list(marker.get("gate_available_actions") or []) or None,
            context_summary=str(marker.get("gate_context_summary") or ""),
        )
        # turn → PAUSED_GATE（终态守卫），session → PAUSED（非终态，可恢复）
        finalize_turn(
            session_id=session_id, turn_id=turn_id,
            turn_status=ResearchTurnStatus.PAUSED_GATE,
            session_status=ResearchSessionStatus.PAUSED,
        )
    elif outcome == "failed":
        status_str = "failed"
        finalize_turn(
            session_id=session_id, turn_id=turn_id,
            turn_status=ResearchTurnStatus.FAILED,
            session_status=ResearchSessionStatus.FAILED,
            error=str(marker.get("error") or "pipeline failed"),
        )
    else:
        status_str = "success"
        finalize_turn(
            session_id=session_id, turn_id=turn_id,
            turn_status=ResearchTurnStatus.COMPLETED,
            session_status=ResearchSessionStatus.COMPLETED,
        )
    return status_str, done_count, failed_count


# ---- Celery 任务入口 ----


@celery_app.task(
    bind=True,
    base=celery.contrib.abortable.AbortableTask,
    name=_TASK_NAME,
)
def run_research_turn(self, data: dict) -> dict:
    """执行一轮科研生成。

    ``data`` 结构::

        {"session_id": "<uuid>", "turn_id": "<uuid>"}

    Provider 密钥、mode 等敏感字段由 turn / session 行提供，不写进 broker。
    """
    session_id = uuid.UUID(data["session_id"])
    turn_id = uuid.UUID(data["turn_id"])

    sink = ResearchEventSink(session_id, turn_id)
    set_research_generation_id(session_id, turn_id, self.request.id)

    try:
        # 1. Bootstrap
        session_snap, turn_snap, run_dir = _bootstrap(session_id, turn_id)
        hitl_mode = turn_snap.mode or session_snap.mode

        sink.emit({
            "type": "log",
            "level": "INFO",
            "message": f"executing pipeline in container (run_dir={run_dir})",
            "source": "bridge",
        })
        logger.info(
            f"research.run_turn start turn={turn_id} run_dir={run_dir} mode={hitl_mode}"
        )

        # 3. 隔离容器内跑 pipeline，收敛成统一 marker。ARC 日志 / stage 进度由容器
        #    合流进 events 文件，经 ContainerJob tail 转发，宿主不再起 tail / monitor 线程。
        marker = _run_pipeline(
            session_snap, turn_snap, run_dir, sink,
            turn_id=turn_id, hitl_mode=hitl_mode,
            from_stage=turn_snap.from_stage, to_stage=turn_snap.to_stage,
            is_aborted=self.is_aborted,
        )

        # 4. 汇总 cancelled 状态（Celery is_aborted：API stop 端点已 abort）+ 原子写终态。
        #    API 侧同步 stop 时通常已把 turn 写成 CANCELLED，这里 finalize 会被终态守卫
        #    短路；此路径主要覆盖"worker 在 stage 边界自行响应 abort"的兜底场景。
        try:
            cancelled = bool(self.is_aborted())
        except Exception:
            cancelled = False

        status_str, done_count, failed_count = _finalize(
            session_id=session_id, turn_id=turn_id,
            marker=marker,
            cancelled=cancelled,
            sink=sink,
        )

        sink.emit({
            "type": "done",
            "status": status_str,
            "stages_done": done_count,
            "stages_failed": failed_count,
        })
        return {
            "status": status_str,
            "stages_done": done_count,
            "stages_failed": failed_count,
        }

    except Exception as exc:
        tb = traceback.format_exc()
        logger.error(f"run_research_turn failed turn={turn_id}: {exc}\n{tb}")
        # 幂等 finalize：正常路径写过就是 no-op；异常发生在正常终态之前才实际生效。
        finalize_turn(
            session_id=session_id, turn_id=turn_id,
            turn_status=ResearchTurnStatus.FAILED,
            session_status=ResearchSessionStatus.FAILED,
            error=str(exc),
        )
        sink.emit({"type": "error", "message": str(exc)})
        raise
    finally:
        # sink.close 会 drain flusher queue，把最后一批 log 落库
        sink.close(timeout=10.0)
        clear_research_generation_id(session_id, turn_id)
        # Redis Stream 保留一段时间由 TTL 兜底清理，不在这里删除以便 SSE 端点做 replay


# ---- 辅助：给 API 层调用 ----


def enqueue_research_turn(
    session_id: uuid.UUID, turn_id: uuid.UUID, celery_task_id: str
) -> str:
    """按 **预生成的** ``celery_task_id`` 派发一轮科研生成，返回该 ID。

    用 ``apply_async(task_id=...)`` 而非 ``.delay()``：调用方（server 层）在建
    turn 时就生成好 ID 并与 turn 行同一事务落库，入队前 DB 已有 ID。这样彻底消除
    ".delay() 返回后回写与 worker 执行赛跑"的竞态——快任务下 worker 可能在回写
    commit 前就拉起，导致"worker 已在跑但 DB celery_task_id 仍为空"的窗口。

    **必须在 turn 行 commit 之后调用**：否则 commit 失败会留下"已入队但无对应行"
    的孤儿任务。
    """
    result = run_research_turn.apply_async(
        args=[{
            "session_id": str(session_id),
            "turn_id": str(turn_id),
        }],
        task_id=celery_task_id,
    )
    return result.id
