"""Collaborate Agent 的 Celery 任务 ``run_collab_turn`` + 事件翻译 + 排队辅助。

协作子会话的一轮执行：在 gate 暂停期间，用户发一条协作消息 → 本任务起一个短命
agent 容器（AgentScope ReAct，见 :mod:`app.tasks.research_collab_container_runner`）
→ tail 容器事件翻译成 SSE / DB 消息 → 容器跑完把 collab turn 落终态、session 回到
``PAUSED``（原 gate 表单重新可操作）。

**与 pipeline turn 的区别**：
- 协作**不推进 pipeline**、不碰 stage 进度；只在 ``stage-NN/`` 里改产物。
- 终态不设 ``session.ended_time``（会话仍暂停在 gate，未结束）。
- 事件类型是 ``collab_message`` / ``collab_tool``（前端协作面板渲染），非
  ``stage_transition`` / ``evolution_step``。

**取消**：与 pipeline 同款，见 :mod:`.lifecycle` 模块 docstring。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import celery.contrib.abortable
from loguru import logger
from sqlmodel import select

from app.core.celery import celery_app
from app.core.db import get_db_session
from app.models.research import (
    ResearchMessage,
    ResearchMessageRole,
    ResearchSession,
    ResearchTurn,
    ResearchTurnStatus,
)

from .config_builder import proxy_provider_for_arc, resolve_provider_for_arc
from .snapshots import SessionSnapshot, resolve_run_dir, snap_session
from .streaming import ResearchEventSink

_COLLAB_TASK_NAME = "research.run_collab_turn"


def _load_collab_context(
    session_id: uuid.UUID, turn_id: uuid.UUID
) -> tuple[SessionSnapshot, int, str, uuid.UUID | None]:
    """读 session 快照 + 本轮协作 turn 的 stage 号、用户消息、待回复门控消息 id。

    协作 turn 的 ``from_stage`` 存 stage 号、``user_input`` 存本轮消息（见
    :func:`services.research_service.collab.start_collab_turn`）。若会话正处于门控
    暂停（存在 ``paused_gate`` turn + 其 gate form 消息未锁定），返回该 gate 消息 id
    —— 供 agent 的 ``run_pipeline`` 派发到 :func:`_reply_to_gate`。否则返回 None。
    """
    with get_db_session() as db:
        session = db.get(ResearchSession, session_id)
        turn = db.get(ResearchTurn, turn_id)
        if not session or not turn:
            raise RuntimeError(f"collab session/turn not found: {session_id} / {turn_id}")
        session_snap = snap_session(session)
        stage_num = int(turn.from_stage or 0) if turn.from_stage else 0
        user_message = (turn.user_input or "").strip()

        # 找当前待回复的 gate form 消息（paused_gate turn 下、未锁定的 waiting_for_input）。
        gate_msg_id: uuid.UUID | None = None
        paused = db.exec(
            select(ResearchTurn)
            .where(ResearchTurn.session_id == session_id)
            .where(ResearchTurn.status == ResearchTurnStatus.PAUSED_GATE.value)
        ).first()
        if paused is not None:
            gate_msg = db.exec(
                select(ResearchMessage)
                .where(ResearchMessage.turn_id == paused.id)
                .where(ResearchMessage.event_type == "waiting_for_input")
                .where(ResearchMessage.payload_locked == False)  # noqa: E712
                .order_by(ResearchMessage.created_time.desc())
            ).first()
            if gate_msg is not None:
                gate_msg_id = gate_msg.id
    return session_snap, stage_num, user_message, gate_msg_id


# agent run_pipeline 允许的 target（stage:N 另行解析）。
_ALLOWED_TARGETS = frozenset({"next", "previous", "current", "restart", "abort"})


def _resolve_target_stage(base_stage: int, target: str) -> int | None:
    """把 run_pipeline target 解析成"从哪个 stage 起跑"的 1-based 号（None=abort/非法）。

    ``base_stage`` 为当前 stage N（门控 stage 或 session.active_stage）。
    """
    if target == "next":
        return base_stage + 1
    if target == "current":
        return base_stage
    if target == "previous":
        return max(1, base_stage - 1)
    if target == "restart":
        return 1
    if target.startswith("stage:"):
        try:
            return int(target.split(":", 1)[1])
        except (ValueError, IndexError):
            return None
    return None  # abort / unknown


def _dispatch_pipeline_intent(
    session_id: uuid.UUID,
    gate_msg_id: uuid.UUID | None,
    target: str,
    message: str,
    sink: ResearchEventSink,
) -> None:
    """把 agent 的 run_pipeline 意图推进成一轮 pipeline turn。

    门控中（有待回复 gate）→ 翻译成 submission 复用 :func:`_reply_to_gate`（与用户手点
    门控按钮完全同路）。非门控 → 走 :func:`start_pipeline_turn` 从算出的 stage 起跑。
    鉴权 / 建轮 / 入队全在服务层，agent 只提供 target + message。非法 target 忽略并 log。
    """
    from app.models.research import ResearchSession
    from app.schemas.research import ResearchTurnStartRequest
    from app.services.research_service.turns import (
        _find_paused_gate_turn,
        _reply_to_gate,
        start_pipeline_turn,
    )
    from app.tasks.research_runner.streaming import is_valid_stage

    tgt = (target or "").strip().lower()
    if tgt not in _ALLOWED_TARGETS and not tgt.startswith("stage:"):
        logger.warning(f"run_pipeline ignored: unknown target {tgt!r}")
        return

    def _log(msg: str) -> None:
        sink.emit({"type": "log", "level": "INFO", "source": "collab", "message": msg})

    try:
        with get_db_session() as db:
            session = db.get(ResearchSession, session_id)
            if session is None:
                return
            paused_turn = _find_paused_gate_turn(db, session_id)

            # 门控中：翻译成门控 submission，复用 _reply_to_gate。
            if paused_turn is not None and gate_msg_id is not None:
                if tgt == "abort":
                    submission: dict = {"action": "abort", "message": message}
                elif tgt == "next":
                    submission = {"action": "approve", "message": message}
                else:
                    gate_msg = db.get(ResearchMessage, gate_msg_id)
                    base = int(gate_msg.stage or 0) if gate_msg else 0
                    stage = _resolve_target_stage(base, tgt)
                    if stage is None or not is_valid_stage(stage):
                        _log(f"run_pipeline: invalid target '{tgt}' at gate")
                        return
                    submission = {
                        "action": "reject",
                        "rollback_to_stage": stage,
                        "message": message,
                    }
                request = ResearchTurnStartRequest(
                    respond_to_message_id=gate_msg_id, submission=submission
                )
                _reply_to_gate(db, session, paused_turn, request)
                _log(f"pipeline '{tgt}' applied via agent (gate)")
                return

            # 非门控：从算出的 stage 起跑。
            if tgt == "abort":
                _log("run_pipeline: 'abort' has no effect when no run is active")
                return
            base = int(session.active_stage or 0)
            # 会话从未跑过（active_stage 为空）时：相对 target（current/next/previous/
            # restart）无"当前"可参照，一律从头（stage 1）起跑；绝对定位 stage:N 仍按
            # 用户指定。有 active_stage 时按 target 相对解析。
            if base <= 0 and not tgt.startswith("stage:"):
                stage: int | None = 1
            else:
                stage = _resolve_target_stage(base, tgt)
            if stage is None or not is_valid_stage(stage):
                _log(f"run_pipeline: cannot resolve target '{tgt}' (stage {stage})")
                return
            turn_id = start_pipeline_turn(
                db, session, from_stage=str(stage), guidance=message or None
            )
            if turn_id is None:
                _log(f"run_pipeline '{tgt}' skipped (a run is active or rate-limited)")
            else:
                _log(f"pipeline '{tgt}' started from stage {stage} via agent")
    except Exception:
        logger.opt(exception=True).warning(f"run_pipeline dispatch failed target={tgt}")


def _finalize_collab(
    *,
    turn_id: uuid.UUID,
    turn_status: ResearchTurnStatus,
    error: str | None = None,
) -> None:
    """把 collab turn 落终态；**不动 session**（协作是叠加层，会话主状态自始不变）。

    与 :func:`lifecycle.finalize_turn` 的差别：协作不推进也不结束会话，故只写 turn
    终态、完全不碰 session。turn 侧沿用同款 ``WHERE status NOT IN (terminal)`` 终态
    守卫（见 :mod:`.lifecycle`）。
    """
    from sqlmodel import update

    from .lifecycle import _TERMINAL_TURN_STATUSES

    now = datetime.now(UTC)
    turn_values: dict[str, Any] = {
        "status": turn_status.value,
        "ended_at": now,
        "updated_time": now,
    }
    if error is not None:
        turn_values["error"] = error
    try:
        with get_db_session() as db:
            db.execute(
                update(ResearchTurn)
                .where(ResearchTurn.id == turn_id)
                .where(ResearchTurn.status.notin_(_TERMINAL_TURN_STATUSES))
                .values(**turn_values)
            )
            db.commit()
    except Exception:
        logger.opt(exception=True).error(
            f"finalize collab failed turn={turn_id} → {turn_status.value}"
        )


def _build_collab_event_handler(sink: ResearchEventSink, stage_num: int):
    """返回容器事件回调：翻译 ``__collab_*__`` → SSE/DB，捕获 ``__result__`` marker。

    返回 ``(on_event, on_stdout, result_holder)``。
    - ``__collab_text__`` → ``collab_message`` 事件（agent 流式文本，前端拼接）；
    - ``__collab_tool__`` → ``collab_tool`` 事件（agent 正在调用哪个工具）；
    - ``__result__`` → 存进 holder（含 agent 最终文本），供任务体落终态消息。
    """
    result_holder: dict[str, Any] = {}

    def on_event(ev: dict[str, Any]) -> None:
        try:
            etype = ev.get("type")
            if etype == "__collab_text__":
                # 逐 chunk 流式文本：只走 Redis/SSE，不落 DB（每 delta 一行是纯写
                # 放大）。完整文本由 _persist_collab_reply 的 collab-reply:<turn_id>
                # 那条权威消息兜底，刷新/回放取它即可。
                sink.emit({
                    "type": "collab_message",
                    "stage": stage_num,
                    "delta": ev.get("delta") or "",
                }, persist=False)

            elif etype == "__collab_tool__":
                sink.emit({
                    "type": "collab_tool",
                    "stage": stage_num,
                    "tool": ev.get("name") or "",
                })
            elif etype == "__result__":
                marker = ev.get("marker")
                if isinstance(marker, dict):
                    result_holder["marker"] = marker
            else:
                sink.emit(ev)
        except Exception:
            logger.debug("collab on_event error", exc_info=True)

    def on_stdout(line: str) -> None:
        sink.emit({
            "type": "log", "level": "INFO", "message": line, "source": "collab",
        })

    return on_event, on_stdout, result_holder


@celery_app.task(
    bind=True,
    base=celery.contrib.abortable.AbortableTask,
    name=_COLLAB_TASK_NAME,
)
def run_collab_turn(self, data: dict) -> dict:
    """执行一轮协作（AgentScope ReAct）。

    ``data``：``{"session_id": "<uuid>", "turn_id": "<uuid>"}``。
    provider 凭证、stage、协作消息由 turn / session 行提供，不写进 broker。
    """
    from app.services.container_runtime import ContainerJobStatus
    from app.services.research_collab_runner import run_collab_turn_container
    from app.tasks.research_runner.streaming import (
        build_stage_context,
        stage_display_name,
    )

    session_id = uuid.UUID(data["session_id"])
    turn_id = uuid.UUID(data["turn_id"])
    sink = ResearchEventSink(session_id, turn_id)

    def check_cancelled() -> bool:
        return self.is_aborted()

    stage_num = 0
    outcome = "failed"  # 兜底；每条正常路径覆盖。finally 据它 emit done 关流。
    try:
        session_snap, stage_num, user_message, gate_msg_id = _load_collab_context(
            session_id, turn_id
        )
        run_dir = resolve_run_dir(session_snap)
        run_dir.mkdir(parents=True, exist_ok=True)

        provider_config = resolve_provider_for_arc(
            session_snap.provider_id,
            session_snap.model_name,
            user_id=session_snap.user_id,
        )
        proxy_provider_for_arc(provider_config, user_id=session_snap.user_id, task_id=turn_id)

        on_event, on_stdout, result_holder = _build_collab_event_handler(sink, stage_num)

        result = run_collab_turn_container(
            run_dir=str(run_dir),
            turn_id=str(turn_id),
            stage_num=stage_num,
            stage_name=stage_display_name(stage_num),
            topic=session_snap.topic,
            user_message=user_message,
            provider_config=provider_config,
            is_gate=gate_msg_id is not None,
            pipeline_context=build_stage_context(stage_num),
            on_event=on_event,
            on_stdout=on_stdout,
            check_cancelled=check_cancelled,
        )

        cancelled = check_cancelled() or result.status is ContainerJobStatus.CANCELLED
        marker = result_holder.get("marker") or {}

        if cancelled:
            _finalize_collab(
                turn_id=turn_id,
                turn_status=ResearchTurnStatus.CANCELLED,
            )
            outcome = "cancelled"
            return {"outcome": outcome}

        if marker.get("outcome") != "collab_done":
            # 容器崩溃 / 被 kill，未发 __result__。
            _finalize_collab(
                turn_id=turn_id,
                turn_status=ResearchTurnStatus.FAILED,
                error=f"collab container exit status={getattr(result.status, 'value', result.status)}",
            )
            outcome = "failed"
            return {"outcome": outcome}

        # 成功：落一条 assistant 协作消息（agent 最终文本），collab turn COMPLETED。
        # session 主状态不变（协作是叠加层）。
        final_text = str(marker.get("final_text") or "")
        _persist_collab_reply(session_id, turn_id, stage_num, final_text)
        _finalize_collab(
            turn_id=turn_id,
            turn_status=ResearchTurnStatus.COMPLETED,
        )
        outcome = "done"

        # agent 用 run_pipeline 表达了推进流水线的意图 → 宿主据 target 推进
        # （门控中经 _reply_to_gate，非门控经 start_pipeline_turn）。agent 只提供
        # target + message，鉴权/建轮全在服务层。
        pipeline_target = marker.get("pipeline_target")
        if pipeline_target:
            _dispatch_pipeline_intent(
                session_id, gate_msg_id, str(pipeline_target),
                str(marker.get("pipeline_message") or ""), sink,
            )
        return {"outcome": outcome}

    except Exception as exc:
        logger.opt(exception=True).error(f"run_collab_turn failed: {exc}")
        _finalize_collab(
            turn_id=turn_id,
            turn_status=ResearchTurnStatus.FAILED,
            error=str(exc),
        )
        outcome = "failed"
        return {"outcome": outcome, "error": str(exc)}
    finally:
        # emit done 让 SSE 关流（前端据 type==done 关 EventSource）；再 close sink。
        try:
            sink.emit({"type": "done", "status": outcome, "stage": stage_num})
        except Exception:
            logger.debug("collab done emit failed", exc_info=True)
        sink.close()


def _persist_collab_reply(
    session_id: uuid.UUID, turn_id: uuid.UUID, stage_num: int, text: str
) -> None:
    """落一条 assistant 协作消息（payload.kind=collab_turn），供刷新回放。"""
    try:
        with get_db_session() as db:
            db.add(ResearchMessage(
                session_id=session_id,
                turn_id=turn_id,
                role=ResearchMessageRole.ASSISTANT,
                content=text,
                turn_status=ResearchTurnStatus.COMPLETED.value,
                event_type="collab_message",
                event_key=f"collab-reply:{turn_id}",
                stage=stage_num or None,
                payload={"kind": "collab_turn", "stage": stage_num, "text": text},
            ))
            db.commit()
    except Exception:
        logger.opt(exception=True).warning(f"persist collab reply failed turn={turn_id}")


def enqueue_collab_turn(
    session_id: uuid.UUID, turn_id: uuid.UUID, celery_task_id: str
) -> str:
    """按预生成的 ``celery_task_id`` 派发一轮协作，返回该 ID。

    与 :func:`enqueue_research_turn` 同款：turn 行 commit 之后再调，避免孤儿任务。
    """
    result = run_collab_turn.apply_async(
        args=[{"session_id": str(session_id), "turn_id": str(turn_id)}],
        task_id=celery_task_id,
    )
    return result.id
