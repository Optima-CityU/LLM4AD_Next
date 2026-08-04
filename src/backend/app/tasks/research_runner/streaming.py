"""事件汇流（宿主侧 sink + 翻译）。

宿主侧把容器产出的事件合并到 ``ResearchEventSink``：

1. ``build_stage_progress_callback``：把容器透传的 ARC 原始 progress payload
   翻译成 ``stage_transition`` 事件；
2. ``persist_gate_pause``：命中硬门控时落 form 消息 + emit ``waiting_for_input``。

所有容器→宿主事件都走单一通道：容器写 ``.events.jsonl`` → ``ContainerJob`` tail →
``task.on_event``；取消由 ``ContainerJob`` 的 ``check_cancelled`` 直接轮询 Redis
stop flag，宿主不再起 tail/monitor 线程。

**Sink 双通道**：sink.emit 里 Redis XADD 是同步的（<1ms 本地网络），DB 落库
走后台 flusher 线程批提交。sink 生命周期结束时必须调
:meth:`ResearchEventSink.close`（task.py 的 finally 里做），把 queue 里的残余
drain 干净。
"""

from __future__ import annotations

import queue
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.core.db import get_db_session
from app.core.redis import (
    push_research_event,
)
from app.models.research import (
    ResearchLog,
    ResearchMessage,
    ResearchMessageRole,
    ResearchSession,
    ResearchTurnStatus,
)

# ---- 常量 ----

_FLUSH_BATCH_SIZE = 50         # DB 批提交阈值
_FLUSH_INTERVAL = 0.5          # DB 批提交超时（无新事件时的最长等待）
_DB_QUEUE_MAXSIZE = 10000      # 上限，超了会丢；Redis 那路仍在

# ARC 阶段号(1-based) → 名字。backend 不装 researchclaw（pipeline 在容器内跑，
# 权威 stage 名由容器 __progress__ 事件的 name 字段透传回来），这张表只作 backend
# 侧的兜底显示与 guide 端点校验之用；ARC 升级调整阶段编号时需同步这份 0.5.0 快照。
_STAGE_NAMES: dict[int, str] = dict(
    enumerate((
        "TOPIC_INIT", "PROBLEM_DECOMPOSE", "SEARCH_STRATEGY", "LITERATURE_COLLECT",
        "LITERATURE_SCREEN", "KNOWLEDGE_EXTRACT", "SYNTHESIS", "HYPOTHESIS_GEN",
        "EXPERIMENT_DESIGN", "CODE_GENERATION", "RESOURCE_PLANNING", "EXPERIMENT_RUN",
        "ITERATIVE_REFINE", "RESULT_ANALYSIS", "RESEARCH_DECISION", "PAPER_OUTLINE",
        "PAPER_DRAFT", "PEER_REVIEW", "PAPER_REVISION", "QUALITY_GATE",
        "KNOWLEDGE_ARCHIVE", "EXPORT_PUBLISH", "CITATION_VERIFY",
    ), start=1)
)


def stage_display_name(stage_num: int) -> str:
    """阶段号 → 名字；未知号退回 ``stage-<n>``。"""
    return _STAGE_NAMES.get(stage_num, f"stage-{stage_num}")


# ARC GATE_ROLLBACK 快照(0.5.0)：门控 stage → reject/pivot 默认回退目标（上游重做）。
# 与 _STAGE_NAMES 同为镜像：backend 不装 researchclaw，须随 ARC pipeline/stages.py 的
# GATE_ROLLBACK 同步。5 SCREEN→4 COLLECT / 9 EXP_DESIGN→8 HYPOTHESIS /
# 20 QUALITY→16 OUTLINE / 10 CODE_GEN→9（hep_ph profile 动态门控）。
_GATE_ROLLBACK: dict[int, int] = {5: 4, 9: 8, 20: 16, 10: 9}


def gate_rollback_default(gate_stage: int) -> int | None:
    """门控 stage 的默认回退目标；非映射内 stage 返回 ``None``（回落=重跑本 stage）。"""
    return _GATE_ROLLBACK.get(gate_stage)


# ARC 流水线一句话总览 + 每阶段用途（供协作 agent system prompt）。与 _STAGE_NAMES
# 同为 0.5.0 快照：ARC 升级调整阶段时需同步。描述只求「让 agent 懂这步在大局里干
# 什么、上游给什么、下游要什么」，不追求逐字精确。
ARC_PIPELINE_OVERVIEW = (
    "ARC (researchclaw) is an autonomous research pipeline that takes a topic "
    "through 23 sequential stages: refine topic -> survey literature -> extract "
    "and synthesize knowledge -> form hypotheses -> design and run experiments -> "
    "analyze results -> write, review and revise a paper -> quality-check and "
    "publish. Each stage consumes the previous stages' outputs and produces files "
    "the later stages depend on."
)

_STAGE_DESCRIPTIONS: dict[int, str] = {
    1: "Initialize and refine the research topic, scope and goals.",
    2: "Decompose the topic into concrete sub-problems / research questions.",
    3: "Plan the literature search strategy (keywords, sources, queries).",
    4: "Collect candidate papers and references for the topic.",
    5: "Screen the collected literature down to the relevant subset.",
    6: "Extract key methods, facts and findings from the screened papers.",
    7: "Synthesize the extracted knowledge into a coherent background.",
    8: "Generate testable hypotheses / research ideas from the synthesis.",
    9: "Design the experiments / algorithms that test the hypotheses.",
    10: "Generate the runnable code implementing the experiment design.",
    11: "Plan compute and data resources needed to run the experiments.",
    12: "Execute the experiments and collect raw results.",
    13: "Iteratively refine the code / experiments based on results.",
    14: "Analyze the experimental results and compute metrics.",
    15: "Decide whether the findings suffice or another research loop is needed.",
    16: "Outline the paper structure and section plan.",
    17: "Draft the full paper from the outline and results.",
    18: "Simulate peer review and critique the draft.",
    19: "Revise the paper to address the review feedback.",
    20: "Final quality gate check before publishing.",
    21: "Archive the knowledge and artifacts for reuse.",
    22: "Export / publish the final paper and artifacts.",
    23: "Verify that all citations are accurate and exist.",
}


def stage_description(stage_num: int) -> str:
    """阶段号 → 用途一句话；未知号返回空串。"""
    return _STAGE_DESCRIPTIONS.get(stage_num, "")


def build_stage_context(stage_num: int) -> str:
    """拼装协作 agent 用的流水线上下文块：总览 + 上一步 / 本步 / 下一步用途。

    只带相邻阶段（而非全 23 步），让 agent 懂本步目标 + 上游可信输入 + 下游消费
    者，从而改产物时保持下游兼容——同时把注入的 token 控制在有界范围。未知阶段
    （如 stage 0 / 非门控续跑）只返回总览。
    """
    lines = [ARC_PIPELINE_OVERVIEW]
    cur = stage_description(stage_num)
    if not cur:
        return lines[0]
    prev = stage_description(stage_num - 1)
    nxt = stage_description(stage_num + 1)
    lines.append("")
    if prev:
        lines.append(
            f"Previous stage {stage_num - 1} "
            f"({stage_display_name(stage_num - 1)}): {prev}"
        )
    lines.append(
        f"Current stage {stage_num} ({stage_display_name(stage_num)}): {cur}"
    )
    if nxt:
        lines.append(
            f"Next stage {stage_num + 1} "
            f"({stage_display_name(stage_num + 1)}): {nxt}"
        )
    return "\n".join(lines)


def is_valid_stage(stage_num: int) -> bool:
    """stage_num 是否为 ARC 合法阶段号（用于 guide 端点校验）。"""
    return stage_num in _STAGE_NAMES

# 内部 sentinel：flusher 收到即 drain 后退出
_FLUSH_STOP = object()


# ---- 事件 sink：Redis 同步 XADD + DB 后台批提交 ----


class ResearchEventSink:
    """三源事件的统一出口。

    每个 :meth:`emit` 双写：**同步** XADD 到 Redis Stream（SSE 用，<1ms）+
    **异步** 入 queue 由后台 flusher 批提交到 DB。唯一键
    ``uq_research_message_turn_role_event`` 保证重放幂等；log 类无 event_key，
    由 sink 自动分配 ``<type>:<seq>``（per-turn 递增，锁保护跨线程写）。

    调用端可用 ``persist_content`` / ``persist_payload`` / ``persist_role``
    覆盖对应 DB 字段。task 结束须在 finally 调 :meth:`close` drain 残余 queue，
    防止最后一批 log 丢失。
    """

    def __init__(
        self,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self._seq = 0
        self._seq_lock = threading.Lock()
        # DB 写入队列：分别用于 research_message 和 research_log 表
        self._db_queue: queue.Queue = queue.Queue(maxsize=_DB_QUEUE_MAXSIZE)
        self._log_queue: queue.Queue = queue.Queue(maxsize=_DB_QUEUE_MAXSIZE)
        self._closed = False
        # 启动两个 flusher 线程：一个处理消息，一个处理日志
        self._flusher = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name=f"research-sink-flush-{turn_id.hex[:8]}",
        )
        self._log_flusher = threading.Thread(
            target=self._flush_log_loop,
            daemon=True,
            name=f"research-sink-log-flush-{turn_id.hex[:8]}",
        )
        self._flusher.start()
        self._log_flusher.start()

    def next_seq(self) -> int:
        """分配一个 per-turn 递增 seq（锁保护，跨线程安全）。

        供**同步直接落库**的路径（如 gate form / guidance）取号，与 :meth:`emit`
        共享同一计数器——否则同步写默认 seq=0，在 created_time 撞车时会被 tiebreaker
        排到该时刻所有事件的最前面，造成「最新状态反而显示在前」的乱序。
        """
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def emit(
        self,
        event: dict[str, Any],
        *,
        event_key: str | None = None,
        persist: bool = True,
        persist_role: ResearchMessageRole = ResearchMessageRole.SYSTEM,
        persist_content: str | None = None,
        persist_payload: dict[str, Any] | None = None,
        message_id: uuid.UUID | None = None,
    ) -> None:
        """Redis 同步推 + DB 异步入队。

        Redis 推送异常在此吞掉（仅 warning）：``emit`` 对调用方是 total 的，
        绝不冒泡。否则某次 XADD 失败会把整轮 turn 拖进 except → 误标 FAILED
        （尤其 gate 停返分支：form 消息已落库、状态却被覆写成 FAILED）。
        SSE 漏掉的事件客户端可经 ``/messages`` 从 DB 回放。

        ``persist=False``：只走 Redis/SSE，不落 DB。用于高频、纯 UI、且已有权威
        DB 记录兜底的流式增量（如 collab 逐 chunk 文本——完整文本另由
        ``collab-reply:<turn_id>`` 那条落库）。避免每个小 delta 独占一行造成写放大。

        ``message_id``：可选的预分配消息 ID。提供时会回填到 event payload 的
        ``message_id`` 字段，推送到 SSE；前端可据此做精确去重（优于 event_key，
        因为 message_id 是 DB 主键，messages 接口返回的历史数据也带这个 ID）。
        """
        # 发射时刻：一次定死，同时用作 SSE 帧的 ts 和 DB 行的 created_time。
        # 这是根治乱序的关键——DB 的排序键 created_time 若沿用 TimeMixin 的落库
        # 时刻，异步 flusher 批量延迟 commit 会让「逻辑更早」的事件拿到「更晚」的
        # created_time（尤其和 gate form / guidance 那种同步直接 commit 混排时），
        # 造成时间倒挂。改为在此处一次性定死发射时刻并透传到 row，让 REST 快照的
        # 排序 == 事件真实发射顺序，且与 SSE live 帧（用同一个 ts）落到同一时钟。
        emitted_at = datetime.now(UTC)
        event.setdefault("ts", emitted_at.isoformat())

        # 落 DB 的事件：先解析出最终 event_key 和 message_id，回填进 event payload，
        # 让 SSE 帧与 DB 行携带**同一个** event_key 和 message_id。前端刷新时
        # /messages 快照与 SSE 重放的重叠区可据此去重（按 message_id 或 event_key）。
        # 瞬态事件（persist=False）无 DB 行、无幂等键语义，不回填。
        resolved_key: str | None = None
        resolved_id: uuid.UUID | None = None
        resolved_seq: int = 0
        if persist:
            resolved_key = self._resolve_event_key(event, event_key)
            event["event_key"] = resolved_key
            # 预分配 message_id（如 gate form）或自动生成，回填到 event 供 SSE 推送
            resolved_id = message_id if message_id is not None else uuid.uuid4()
            event["message_id"] = str(resolved_id)
            # 分配 seq：per-turn 单调递增，保证事件严格顺序
            with self._seq_lock:
                self._seq += 1
                resolved_seq = self._seq
            # 回填 seq 到 event，SSE live 帧也带上，前端可与 REST 用同一排序键
            event["seq"] = resolved_seq

        # XADD 返回的 Redis Stream entry id（<ms>-<seq>），持久化到 DB 行，
        # 让前端刷新时能从「已拉取历史的末端」精确续传 SSE（免全量重放），
        # 并作精确去重键。push 失败或非持久事件时保持 None。
        stream_id: str | None = None
        try:
            stream_id = push_research_event(
                self.session_id, self.turn_id, event
            )
        except Exception:
            logger.warning(
                f"sink redis push failed turn={self.turn_id} "
                f"etype={event.get('type')}",
                exc_info=True,
            )

        # 只走 Redis 的瞬态事件：不落 DB，直接返回。
        if not persist:
            return

        # 根据 event type 路由到不同的表/队列
        event_type = event.get("type")
        if event_type == "log":
            # 日志事件：构建 log row 并入队到 research_log 表
            row = self._build_log_row(
                event,
                event_key=resolved_key,
                message_id=resolved_id,
                seq=resolved_seq,
                created_time=emitted_at,
                stream_id=stream_id,
            )
            target_queue = self._log_queue
        else:
            # 其他事件：构建 message row 并入队到 research_message 表
            row = self._build_row(
                event,
                event_key=resolved_key,
                role=persist_role,
                content=persist_content,
                payload=persist_payload,
                message_id=resolved_id,
                seq=resolved_seq,
                created_time=emitted_at,
                stream_id=stream_id,
            )
            target_queue = self._db_queue

        # close() 后 flusher 已退出，再入队的 row 无人 drain → 只丢 DB 一路
        # （Redis 上面已推）。显式跳过并 warning，避免静默丢失误判为已落库。
        if self._closed:
            logger.warning(
                f"sink emit after close turn={self.turn_id} "
                f"etype={row.get('event_type', event_type)}—db drop (redis ok)"
            )
            return
        try:
            target_queue.put_nowait(row)
        except queue.Full:
            # 队列满了直接丢；Redis Stream 那路仍在，SSE 不受影响。
            logger.warning(
                f"sink db queue full turn={self.turn_id} etype={row.get('event_type', event_type)}—dropping"
            )

    def close(self, timeout: float = 10.0) -> None:
        """通知 flusher 收尾：drain queue，退出线程。task.py finally 里调。"""
        if self._closed:
            return
        self._closed = True
        try:
            # sentinel：通知 message flusher drain 后退出。1s 超时 + queue.Full 兜底。
            self._db_queue.put(_FLUSH_STOP, timeout=1.0)
        except queue.Full:
            # 极端情况：队列已经堵满。仍等 flusher 消化到位。
            pass
        try:
            # 同样通知 log flusher
            self._log_queue.put(_FLUSH_STOP, timeout=1.0)
        except queue.Full:
            pass

        # 等待两个 flusher 线程都结束
        self._flusher.join(timeout=timeout / 2)
        self._log_flusher.join(timeout=timeout / 2)

    # ---- internal ----

    def _resolve_event_key(
        self, event: dict[str, Any], event_key: str | None
    ) -> str:
        """解析事件的最终 event_key（DB 幂等键 + SSE 去重键）。

        显式传入则原样用；否则按 ``<type>:<seq>`` 分配（per-turn 递增，锁保护跨
        线程写）。硬截到 varchar(128) 上限，与 DB 列一致。
        """
        if event_key is not None:
            return event_key[:128]
        etype = str(event.get("type") or "unknown")[:32]
        with self._seq_lock:
            self._seq += 1
            return f"{etype}:{self._seq}"[:128]

    def _build_row(
        self,
        event: dict[str, Any],
        *,
        event_key: str | None,
        role: ResearchMessageRole,
        content: str | None,
        payload: dict[str, Any] | None,
        message_id: uuid.UUID | None = None,
        seq: int = 0,
        created_time: datetime | None = None,
        stream_id: str | None = None,
    ) -> dict[str, Any]:
        """把 event 打包成待插入的 row dict（不入库，由 flusher 反序列化）。

        ``event_key`` 由 :meth:`emit` 经 :meth:`_resolve_event_key` 预解析后传入
        （已回填进 event payload），此处不再自增 seq，避免 SSE 帧与 DB 行的键错位。

        ``message_id`` 若提供则使用（如 gate form 预分配的 UUID），否则自动生成。
        ``seq`` 为 per-turn 递增序列号，保证事件严格顺序。
        ``created_time`` 为发射时刻；显式写入以覆盖 TimeMixin 的落库时刻，避免异步
        flusher 批量延迟 commit 造成的时间倒挂（详见 :meth:`emit`）。
        """
        etype = str(event.get("type") or "unknown")[:32]
        key = event_key if event_key is not None else self._resolve_event_key(event, None)
        raw_stage = event.get("stage")
        msg_id = message_id if message_id is not None else uuid.uuid4()
        row = {
            "id": msg_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "role": role,
            "content": content if content is not None else str(event.get("message") or ""),
            "payload": payload if payload is not None else event,
            "event_type": etype,
            "event_key": key[:128],  # varchar(128) 硬截
            "stage": raw_stage if isinstance(raw_stage, int) else None,
            "turn_status": ResearchTurnStatus.RUNNING.value,
            "seq": seq,
            "stream_id": stream_id,
        }
        if created_time is not None:
            row["created_time"] = created_time
        return row

    def _build_log_row(
        self,
        event: dict[str, Any],
        *,
        event_key: str | None,
        message_id: uuid.UUID | None = None,
        seq: int = 0,
        created_time: datetime | None = None,
        stream_id: str | None = None,
    ) -> dict[str, Any]:
        """把 log event 打包成待插入 research_log 表的 row dict。

        log 事件结构：
        - level: INFO/WARNING/ERROR/DEBUG
        - message: 日志消息文本
        - source: arc/container/bridge/collab
        - module: 可选的模块名
        - stage: 可选的阶段号
        - ts: 可选的原始时间戳
        - seq: per-turn 递增序列号，保证事件严格顺序
        - created_time: 发射时刻，显式覆盖 TimeMixin 落库时刻（见 :meth:`emit`）
        """
        key = event_key if event_key is not None else self._resolve_event_key(event, None)
        raw_stage = event.get("stage")
        msg_id = message_id if message_id is not None else uuid.uuid4()

        row = {
            "id": msg_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "level": str(event.get("level", "INFO"))[:16],
            "message": str(event.get("message") or ""),
            "source": str(event.get("source", "unknown"))[:32],
            "module": str(event.get("module"))[:128] if event.get("module") else None,
            "event_key": key[:128],
            "turn_status": ResearchTurnStatus.RUNNING.value,
            "stage": raw_stage if isinstance(raw_stage, int) else None,
            "ts": event.get("ts"),  # ISO8601 字符串或 None，flusher 会转 datetime
            "seq": seq,
            "stream_id": stream_id,
        }
        if created_time is not None:
            row["created_time"] = created_time
        return row

    def _flush_loop(self) -> None:
        """后台线程：拉 queue，批量 commit ResearchMessage。

        触发提交的两个条件：batch 满 ``_FLUSH_BATCH_SIZE`` 条 / 等待
        ``_FLUSH_INTERVAL`` 秒没有新事件。收到 sentinel 后先 flush 再退出。
        """
        batch: list[dict[str, Any]] = []
        while True:
            # 空 batch 无限等；有 batch 则最多等 _FLUSH_INTERVAL 秒
            timeout = _FLUSH_INTERVAL if batch else None
            try:
                row = self._db_queue.get(timeout=timeout)
            except queue.Empty:
                self._commit_batch(batch)
                batch.clear()
                continue
            if row is _FLUSH_STOP:
                self._commit_batch(batch)
                # drain 任何未消费完的（sentinel 之后仍可能有 put）
                remaining: list[dict[str, Any]] = []
                while True:
                    try:
                        r = self._db_queue.get_nowait()
                    except queue.Empty:
                        break
                    if r is _FLUSH_STOP:
                        continue
                    remaining.append(r)
                self._commit_batch(remaining)
                return
            batch.append(row)
            if len(batch) >= _FLUSH_BATCH_SIZE:
                self._commit_batch(batch)
                batch.clear()

    def _commit_batch(self, batch: list[dict[str, Any]]) -> None:
        """把一批 row 提交到 DB；先尝试整批一次 commit，冲突则退回逐条。"""
        if not batch:
            return
        try:
            with get_db_session() as db:
                for row in batch:
                    db.add(ResearchMessage(**row))
                try:
                    db.commit()
                except IntegrityError:
                    # 整批里有 event_key 冲突（罕见但合法：幂等重放场景）。
                    # rollback 后逐条重试，把冲突行单独跳过。
                    db.rollback()
                    self._commit_one_by_one(batch)
                except Exception:
                    # 意外失败——不是幂等冲突。把错误显式冒泡：Redis 那路留 log。
                    db.rollback()
                    logger.warning(
                        f"sink._commit_batch failed turn={self.turn_id} size={len(batch)}",
                        exc_info=True,
                    )
        except Exception:
            # 连 session 都开不出——多半是 DB 挂了；不阻塞流程。
            logger.warning(
                f"sink._commit_batch: cannot open db session turn={self.turn_id}",
                exc_info=True,
            )

    def _commit_one_by_one(self, batch: list[dict[str, Any]]) -> None:
        """整批 commit 失败后的兜底：逐条 commit，跳过 IntegrityError。"""
        with get_db_session() as db:
            for row in batch:
                try:
                    db.add(ResearchMessage(**row))
                    db.commit()
                except IntegrityError:
                    # 期望的幂等：重复 event_key，跳过即可。
                    db.rollback()
                    logger.debug(f"sink dup event_key={row.get('event_key')} — skip")
                except Exception:
                    db.rollback()
                    logger.warning(
                        f"sink row commit failed etype={row.get('event_type')}",
                        exc_info=True,
                    )

    def _flush_log_loop(self) -> None:
        """后台线程：拉 log queue，批量 commit ResearchLog。

        与 _flush_loop 类似的批量提交逻辑，但写入 research_log 表。
        """
        batch: list[dict[str, Any]] = []
        while True:
            timeout = _FLUSH_INTERVAL if batch else None
            try:
                row = self._log_queue.get(timeout=timeout)
            except queue.Empty:
                self._commit_log_batch(batch)
                batch.clear()
                continue
            if row is _FLUSH_STOP:
                self._commit_log_batch(batch)
                # drain 剩余的
                remaining: list[dict[str, Any]] = []
                while True:
                    try:
                        r = self._log_queue.get_nowait()
                    except queue.Empty:
                        break
                    if r is _FLUSH_STOP:
                        continue
                    remaining.append(r)
                self._commit_log_batch(remaining)
                return
            batch.append(row)
            if len(batch) >= _FLUSH_BATCH_SIZE:
                self._commit_log_batch(batch)
                batch.clear()

    def _commit_log_batch(self, batch: list[dict[str, Any]]) -> None:
        """把一批 log row 提交到 research_log 表。"""
        if not batch:
            return
        try:
            with get_db_session() as db:
                try:
                    for row in batch:
                        # 转换 ts 字段从 ISO8601 字符串到 datetime
                        if row.get("ts") and isinstance(row["ts"], str):
                            try:
                                row["ts"] = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
                            except Exception:
                                row["ts"] = None
                        db.add(ResearchLog(**row))
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    self._commit_log_one_by_one(batch)
                except Exception:
                    db.rollback()
                    logger.warning(
                        f"sink._commit_log_batch failed turn={self.turn_id} size={len(batch)}",
                        exc_info=True,
                    )
        except Exception:
            logger.warning(
                f"sink._commit_log_batch: cannot open db session turn={self.turn_id}",
                exc_info=True,
            )

    def _commit_log_one_by_one(self, batch: list[dict[str, Any]]) -> None:
        """整批 commit 失败后的兜底：逐条 commit log，跳过 IntegrityError。"""
        with get_db_session() as db:
            for row in batch:
                try:
                    # 转换 ts 字段
                    if row.get("ts") and isinstance(row["ts"], str):
                        try:
                            row["ts"] = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
                        except Exception:
                            row["ts"] = None
                    db.add(ResearchLog(**row))
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    logger.debug(f"sink log dup event_key={row.get('event_key')} — skip")
                except Exception:
                    db.rollback()
                    logger.warning(
                        f"sink log row commit failed level={row.get('level')}",
                        exc_info=True,
                    )


# ---- stage 进度事件（由 ARC progress_callback 驱动，见 build_stage_progress_callback）----


def _emit_stage_event(
    sink: ResearchEventSink,
    stage_num: int,
    name: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    """发一条 stage_transition 事件（Redis + DB 一次 emit 双落）。

    ``name`` 是 ARC 透传的权威 stage.name；为空时退回 :func:`stage_display_name`。
    ``error`` 失败时落进 event/payload，供 ``/state`` 透出到 stage 快照。
    """
    display = name or stage_display_name(stage_num)
    event = {
        "type": "stage_transition",
        "stage": stage_num,
        "stage_name": display,
        "status": status,
    }
    payload = {
        "kind": "stage_progress",
        "stage": stage_num,
        "name": display,
        "status": status,
    }
    if error:
        event["error"] = error
        payload["error"] = error
    sink.emit(
        event,
        # 不传固定 event_key：让 sink 兜底分配 per-turn 唯一键 ``stage_transition:{seq}``，
        # 使同一 (stage, status) 每次都作为独立行落库（不再被 uq_turn_role_event 折叠）。
        # REFINE/回跳导致 stage 多次进入 running、或 ARC 重复回调，都会各存一行，完整
        # 保留执行轨迹。get_state 仍按 event_type 折叠出快照，不受影响；前端 collapseTurn
        # 合并相邻同态条目，渲染无重复。
        persist_content=f"[stage-{stage_num}] {display} {status}",
        persist_payload=payload,
    )
    if status == "running":
        _update_session_stage(sink.session_id, stage_num, display)


def _update_session_stage(session_id: uuid.UUID, stage: int, name: str) -> None:
    """观察到 stage 推进时同步 session.active_stage。"""
    try:
        with get_db_session() as db:
            session = db.get(ResearchSession, session_id)
            if session:
                session.active_stage = stage
                session.active_stage_name = name
                session.updated_time = datetime.now(UTC)
                db.add(session)
                db.commit()
    except Exception:
        logger.debug("update_session_stage failed", exc_info=True)


def build_stage_progress_callback(sink: ResearchEventSink):
    """构造 ARC ``execute_pipeline(progress_callback=...)`` 用的回调。

    ARC 主线程在每个 stage 边界同步调用它，payload 形如
    ``{"type":"stage_start|stage_end", "stage", "name", "status", "error"}``，
    翻译成 ``stage_transition`` 事件（复用 :func:`_emit_stage_event`）。进程内
    push、非轮询，ms 级且带每阶段真实 status。异常在此吞掉，绝不冒泡回 pipeline。
    """

    def _callback(payload: dict[str, Any]) -> None:
        try:
            etype = payload.get("type")
            stage_num = int(payload.get("stage") or 0)
            name = str(payload.get("name") or "")
            if etype == "stage_start":
                _emit_stage_event(sink, stage_num, name, status="running")
            elif etype == "stage_end":
                raw = str(payload.get("status") or "done").lower()
                # DONE → 进度条标完成；其余（failed/paused/blocked_approval...）原样透传
                status = "done" if raw == "done" else raw
                err = payload.get("error")
                _emit_stage_event(
                    sink, stage_num, name, status=status,
                    error=str(err) if err else None,
                )
        except Exception:
            logger.debug("stage progress callback error", exc_info=True)

    return _callback


# ---- 门控停返：命中硬门控落 form 消息 + emit waiting_for_input ----


# 门控表单默认可用动作（容器未回传原生 available_actions 时的兜底）。
# 精简为 4 个去重后的行为：approve(→N+1) / reject(→回退重跑) / edit(带反馈重跑 N)
# / abort(中止)。skip 与 approve 同效、collaborate/inject 与 edit 同效，故不列。
_DEFAULT_HITL_ACTIONS = ("approve", "reject", "edit", "abort")

# 原生 ARC 每次回传 6 个动作（approve/reject/edit/collaborate/skip/abort），但后端
# _compute_gate_resume 只有 4 种落点行为。这张表把同效动作归并，前端只渲染 4 个：
# skip→approve（同为 N+1）、collaborate/inject→edit（同为带 guidance 重跑 N）。
_ACTION_CANONICAL = {
    "approve": "approve",
    "skip": "approve",
    "reject": "reject",
    "pivot": "reject",
    "rollback": "reject",
    "edit": "edit",
    "collaborate": "edit",
    "inject": "edit",
    "abort": "abort",
}


def _canonical_actions(actions: list[str]) -> list[str]:
    """把门控动作列表归并到 4 个规范动作，保持 approve/reject/edit/abort 顺序去重。"""
    seen = {_ACTION_CANONICAL.get(a.lower().strip(), a) for a in actions}
    return [a for a in _DEFAULT_HITL_ACTIONS if a in seen]


def persist_gate_pause(
    *,
    sink: ResearchEventSink,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    stage_num: int,
    stage_name: str = "",
    reason: str = "",
    output_files: list[str] | None = None,
    available_actions: list[str] | None = None,
    context_summary: str = "",
) -> uuid.UUID:
    """命中门控停返：落 form 消息 + emit ``waiting_for_input``，返回 message_id。

    新链路下 pipeline 命中 HITL 门控会**干净返回**而非阻塞（容器内原生
    ``HITLSession`` 的非阻塞 callback 抛信号结束容器）。本函数把门控信息落成与旧
    :class:`RedisHITLAdapter` 完全一致的 ``payload.kind="form"`` 消息，前端 gate
    表单判据无需改动。字段由调用方从容器回传的 gate 终态标记提取。

    ``available_actions`` 直接透传原生 ``WaitingState.available_actions``——在哪停、
    给哪些动作与 CLI ``--mode`` 一一对应；为空时回落 :data:`_DEFAULT_HITL_ACTIONS`。

    与旧路径的差别：``turn_status`` 落 ``PAUSED_GATE``（任务已结束、等恢复），
    turn/session 的状态落地由调用方（``_finalize`` → ``finalize_turn``）负责。

    Args:
        sink: 事件 sink（同时写 Redis Stream + research_message 表）。
        session_id: 会话 ID。
        turn_id: 命中门控的 turn ID。
        stage_num: 门控 stage 号 N。
        stage_name: stage 名（小写），供前端展示。
        reason: 门控原因/说明。
        output_files: 该 stage 产出的待审文件列表。
        available_actions: 原生可用动作列表；空则用默认兜底。无论哪种，都会经
            :func:`_canonical_actions` 归并到 4 个规范动作（approve/reject/edit/abort）。
        context_summary: 原生 WaitingState 的上下文速览（阶段状态 + 产物截断）。

    Returns:
        新建 form 消息的 ``message_id``（前端回填时作 respond_to_message_id）。
    """
    reason = reason or f"approval required for {stage_name or stage_num}"
    files = list(output_files or ())
    # 原生列表（6 个）或兜底（4 个）统一归并到 4 个规范动作，前端只渲染这些。
    raw_actions = list(available_actions) if available_actions else list(_DEFAULT_HITL_ACTIONS)
    actions = _canonical_actions(raw_actions) or list(_DEFAULT_HITL_ACTIONS)
    message_id = uuid.uuid4()

    # 补发一条该 stage 的 stage_transition（status=blocked_approval）：门控命中时 ARC
    # 抛暂停信号中断 execute_pipeline，该 stage 只发过 stage_start(running)、没有
    # stage_end，故 get_state 回放会把它卡在 running。补这条让 /state 把它标为
    # waiting（见 sessions.get_state 的 blocked_approval → waiting 分支）。
    if stage_num > 0:
        _emit_stage_event(sink, stage_num, stage_name, status="blocked_approval")

    # reject/pivot 的默认回退目标（对齐 ARC GATE_ROLLBACK）：下发给前端展示
    # 「打回将回到哪一步」。None 表示无映射（回落=重跑本 stage）。
    rollback_default = gate_rollback_default(stage_num)
    form_payload = {
        "kind": "form",
        "message_id": str(message_id),
        "stage": stage_num,
        "stage_name": stage_name,
        "reason": reason,
        "available_actions": actions,
        "context_summary": context_summary,
        "output_files": files,
        "rollback_default": rollback_default,
        "rollback_default_name": (
            stage_display_name(rollback_default) if rollback_default else ""
        ),
    }

    # 1. DB: 落 form 消息（turn_status = PAUSED_GATE），event_key 用 message_id 幂等。
    # 显式补 seq（与 sink emit 共享计数器）+ created_time（发射时刻）：同步直接 commit
    # 若不补，seq 默认 0、created_time=落库时刻，与异步 flusher 的事件混排时会乱序。
    # 本条逻辑上排在上面 _emit_stage_event(blocked_approval) 之后，故 seq/时刻都更大。
    try:
        with get_db_session() as db:
            db.add(ResearchMessage(
                id=message_id,
                session_id=session_id,
                turn_id=turn_id,
                role=ResearchMessageRole.ASSISTANT,
                content=reason,
                payload=form_payload,
                event_type="waiting_for_input",
                event_key=f"gate:{message_id}",
                stage=stage_num,
                turn_status=ResearchTurnStatus.PAUSED_GATE.value,
                seq=sink.next_seq(),
                created_time=datetime.now(UTC),
            ))
            db.commit()
    except IntegrityError:
        logger.debug(f"gate form message dup, skip msg={message_id}")
    except Exception:
        logger.opt(exception=True).warning(
            f"persist gate form message failed msg={message_id}"
        )

    # 2. SSE: 实时推 waiting_for_input（随后主循环会再 emit done 关流）
    sink.emit(
        {
            "type": "waiting_for_input",
            "stage": stage_num,
            "stage_name": stage_name,
            "reason": reason,
            "message_id": str(message_id),
            "available_actions": actions,
            "output_files": files,
        },
        event_key=f"waiting:{message_id}",
        persist_content=f"[stage-{stage_num}] awaiting user input: {reason}",
        persist_payload=form_payload,
    )
    return message_id

