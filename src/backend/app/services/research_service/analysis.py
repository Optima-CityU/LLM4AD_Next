"""结果分析（Analysis）子域：聚合 run_dir 产出 + LLM 生成分析报告。

分两层，边界清晰：

1. **结构化聚合（``get_analysis_data``）**：纯读 ``run_dir`` 下现成 JSON
   （pipeline_summary / checkpoint / degradation_signal / experiment_summary_best /
   decision_history / deliverables/manifest + 各 ``stage-NN*/stage_health|decision``）。
   **零 LLM、毫秒级、可离线**，直接喂前端图表 / 阶段时间线。缺文件降级为空，不报错。

2. **LLM 报告（``generate_analysis_report`` 等）**：把上面的聚合数据作为上下文，
   由用户指定的 provider/model + 语言生成 Markdown 叙述报告。生成过程对齐 chat 端
   ``report_service``：后台协程流式拉取、Redis Stream 推 SSE、协作式取消、落库
   到 ``ResearchSession.analysis_report``。

本模块聚合部分只读文件系统；报告部分读/写 session.analysis_report。
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from loguru import logger
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from app import models
from app.core.redis import (
    clear_report_generation_id,
    delete_report_stream,
    get_report_generation_id,
    push_report_chunk,
    set_report_generation_id,
)
from app.schemas.research import (
    ResearchAnalysisData,
    ResearchAnalysisDecisionItem,
    ResearchAnalysisDetailResponse,
    ResearchAnalysisEntry,
    ResearchAnalysisGenerateRequest,
    ResearchAnalysisGenerateResponse,
    ResearchAnalysisStageItem,
    ResearchAnalysisStopResponse,
)

from ._common import _get_session

# Redis 复用 chat 报告的 gen-id / stream 基建；用 report_type 段区分科研分析。
_ANALYSIS_REPORT_TYPE = "analysis"
# 门控阶段（与前端 tech.tsx GATE_STAGES 对齐）。
_GATE_STAGES = frozenset({5, 9, 20})
# 喂给 LLM 的聚合上下文最大字符数，防超长。
_MAX_CONTEXT_CHARS = 60_000
# 持有后台生成任务引用，避免 fire-and-forget task 被 GC 中途回收。
_BG_TASKS: set[asyncio.Task[None]] = set()


# ============================================================
# 1. 结构化聚合（纯读盘、零 LLM）
# ============================================================


def _read_json(root: Path, rel: str) -> Any | None:
    """读 ``root/rel`` 下的 JSON；不存在/解析失败返回 None（不抛）。"""
    path = root / rel
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.opt(exception=True).debug(f"read json failed: {path}")
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    """把任意 JSON 值宽松转 float；None/非数字字符串等 → default（不抛）。

    stage_health.json 由容器内 LLM 管线写，字段类型不受后端约束——``duration_sec``
    偶尔会是 ``"n/a"`` 之类的字符串，直接 ``float()`` 会 ValueError 崩整个聚合端点。
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    """把任意 JSON 值宽松转 int；None/非数字 → default（不抛）。见 :func:`_safe_float`。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# 回跳版本目录后缀：``stage-10_v2`` / ``stage-10.v1`` / ``stage-10-v3``。
_VERSION_SUFFIX = re.compile(r"[_.\-]v\d+$", re.IGNORECASE)


def _stage_num(dir_name: str) -> int | None:
    """从 ``stage-12`` / ``stage-10_v2`` 目录名抽出阶段号（前导数字）。"""
    if not dir_name.startswith("stage-"):
        return None
    tail = dir_name.removeprefix("stage-")
    for sep in ("_", "-", "."):
        if sep in tail:
            tail = tail.split(sep, 1)[0]
    try:
        return int(tail)
    except ValueError:
        return None


def _collect_stages(root: Path) -> list[ResearchAnalysisStageItem]:
    """遍历所有 ``stage-NN*`` 目录，按阶段号聚合耗时/状态/决策。

    回跳产生的 ``stage-NN_v1/_v2`` 归入同一阶段号：base 目录作为最终态提供
    status/decision，全部版本的 ``duration_sec`` 收进 ``versions`` 并求和。
    """
    # stage_num -> {"versions": {dirname: dur}, "base": {...}}
    buckets: dict[int, dict[str, Any]] = {}
    for child in sorted(root.iterdir() if root.is_dir() else []):
        if not child.is_dir():
            continue
        num = _stage_num(child.name)
        if num is None:
            continue
        health_raw = _read_json(child, "stage_health.json")
        health = health_raw if isinstance(health_raw, dict) else {}
        decision_raw = _read_json(child, "decision.json")
        decision = decision_raw if isinstance(decision_raw, dict) else {}
        bucket = buckets.setdefault(num, {"versions": {}, "base": None})
        dur = _safe_float(health.get("duration_sec"))
        bucket["versions"][child.name] = dur
        entry = {
            "name": (decision.get("stage_id") or health.get("stage_id") or "")
            .split("-", 1)[-1]
            .upper()
            or None,
            "status": health.get("status") or decision.get("status") or "pending",
            "decision": decision.get("decision"),
            "next_stage": decision.get("next_stage"),
            "artifacts_count": _safe_int(health.get("artifacts_count")),
            "error": health.get("error") or decision.get("error"),
        }
        # 无 _vN 版本后缀的目录视为最终态（如 stage-12_EXPERIMENT_RUN）；
        # 否则暂存首个遇到的作兜底。
        is_base = _VERSION_SUFFIX.search(child.name) is None
        if is_base or bucket["base"] is None:
            bucket["base"] = entry

    items: list[ResearchAnalysisStageItem] = []
    for num in sorted(buckets):
        b = buckets[num]
        base = b["base"] or {}
        # versions 按目录名排序：base 在前，_v1/_v2 依次在后
        ordered = sorted(
            b["versions"].items(),
            key=lambda kv: (len(kv[0]), kv[0]),
        )
        durations = [d for _, d in ordered]
        items.append(
            ResearchAnalysisStageItem(
                stage=num,
                name=base.get("name") or f"STAGE_{num}",
                status=base.get("status") or "pending",
                decision=base.get("decision"),
                next_stage=base.get("next_stage"),
                duration_sec=round(sum(durations), 2),
                versions=[round(d, 2) for d in durations],
                artifacts_count=base.get("artifacts_count") or 0,
                error=base.get("error"),
                is_gate=num in _GATE_STAGES,
            )
        )
    return items


def get_analysis_data(
    db: Session, session_id: uuid.UUID, user: models.User
) -> ResearchAnalysisData:
    """聚合会话 ``run_dir`` 下的结果文件为结构化快照（纯读盘、零 LLM）。

    Args:
        db: 数据库会话。
        session_id: 目标会话 ID。
        user: 当前认证用户（归属校验）。

    Returns:
        ``ResearchAnalysisData``：overview / quality_gate / experiment /
        deliverables / decisions / stages。任一来源文件缺失时对应字段降级为
        空或 None，不抛异常。
    """
    session = _get_session(db, session_id, user)
    root = Path(session.run_dir) if session.run_dir else None
    if not root or not root.is_dir():
        return ResearchAnalysisData(session_id=session_id, run_dir=session.run_dir)

    # _read_json 返回的是原样解析结果，可能是 dict / list / 标量。下面按 dict 解构、
    # 按 list 迭代，若文件内容形状不符（如 summary 是 JSON 数组）会 TypeError 崩端点。
    # 故按预期类型做窄化：非 dict 的当空 dict、非 list 的当空 list，静默降级不抛。
    summary_raw = _read_json(root, "pipeline_summary.json")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    checkpoint_raw = _read_json(root, "checkpoint.json")
    checkpoint = checkpoint_raw if isinstance(checkpoint_raw, dict) else {}
    overview: dict[str, Any] = {**summary}
    if checkpoint:
        overview["checkpoint"] = {
            "last_completed_stage": checkpoint.get("last_completed_stage"),
            "last_completed_name": checkpoint.get("last_completed_name"),
            "hitl": checkpoint.get("hitl"),
        }

    decisions_raw = _read_json(root, "decision_history.json")
    decisions_list = decisions_raw if isinstance(decisions_raw, list) else []
    decisions = [
        ResearchAnalysisDecisionItem(
            decision=str(d.get("decision", "")),
            rollback_target=d.get("rollback_target"),
            rollback_stage_num=d.get("rollback_stage_num"),
            attempt=d.get("attempt"),
            timestamp=d.get("timestamp"),
        )
        for d in decisions_list
        if isinstance(d, dict)
    ]

    return ResearchAnalysisData(
        session_id=session_id,
        run_dir=session.run_dir,
        overview=overview,
        quality_gate=_read_json(root, "degradation_signal.json"),
        experiment=_read_json(root, "experiment_summary_best.json"),
        deliverables=_read_json(root, "deliverables/manifest.json"),
        decisions=decisions,
        stages=_collect_stages(root),
    )


# ============================================================
# 2. LLM 分析报告：触发 / 读取 / 停止
# ============================================================


def generate_analysis_report(
    db: Session,
    session_id: uuid.UUID,
    current_user: models.User,
    request: ResearchAnalysisGenerateRequest,
    access_token: str | None = None,
) -> ResearchAnalysisGenerateResponse:
    """校验并后台启动结果分析报告的 LLM 生成。

    入参由用户传入模型与语言（对齐 chat 端 ``generate_report``）。若已有正在
    运行的生成任务，通过协作式取消终止旧任务后立即启动新的。

    Args:
        db: 数据库会话。
        session_id: 目标会话 ID。
        current_user: 当前认证用户。
        request: 生成请求（language / provider_id / model_name / prompt_template）。
        access_token: 当前登录 token，用于替换内置供应商 URL 占位。

    Returns:
        状态为 ``generating`` 的受理响应。

    Raises:
        HTTPException: 会话无 run_dir 或聚合数据为空（无可分析内容）时抛出。
    """
    session = _get_session(db, session_id, current_user)
    if not session.run_dir or not Path(session.run_dir).is_dir():
        raise HTTPException(
            status_code=400, detail="Session has no run_dir to analyze"
        )

    data = get_analysis_data(db, session_id, current_user)
    if not data.stages and not data.overview:
        raise HTTPException(
            status_code=400, detail="No analysis data found in run_dir"
        )

    provider_config = _resolve_provider_config(
        db, current_user, request.provider_id, request.model_name, access_token
    )
    system_prompt, user_prompt = _build_prompts(request, data)

    # 取消旧生成：推 cancelled、删旧流
    sid = str(session_id)
    old_gen_id = get_report_generation_id(sid, _ANALYSIS_REPORT_TYPE)
    if old_gen_id:
        push_report_chunk(sid, _ANALYSIS_REPORT_TYPE, {"type": "cancelled"})
    delete_report_stream(sid, _ANALYSIS_REPORT_TYPE)

    generation_id = uuid.uuid4().hex
    set_report_generation_id(sid, _ANALYSIS_REPORT_TYPE, generation_id)

    now = datetime.now(UTC).isoformat()
    session.analysis_report = {
        "status": "generating",
        "content": None,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "provider_model": provider_config.get("model", ""),
        "language": request.language,
    }
    flag_modified(session, "analysis_report")
    db.add(session)
    db.commit()

    prompt = f"{system_prompt}\n\n{user_prompt}"
    task = asyncio.create_task(
        _run_analysis_generation(
            session_id=session_id,
            provider_config=provider_config,
            prompt=prompt,
            generation_id=generation_id,
        )
    )
    # 持有引用直到完成，防止 task 被 GC 提前回收。
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)

    return ResearchAnalysisGenerateResponse(
        session_id=session_id,
        status="generating",
        message="Analysis report generation started",
    )


def get_analysis(
    db: Session, session_id: uuid.UUID, current_user: models.User
) -> ResearchAnalysisDetailResponse:
    """返回结构化聚合数据 + 最近一次 LLM 报告（可为空）。

    Args:
        db: 数据库会话。
        session_id: 目标会话 ID。
        current_user: 当前认证用户。

    Returns:
        ``ResearchAnalysisDetailResponse``：``data`` 恒有值，``report`` 未生成
        过时为 None。
    """
    session = _get_session(db, session_id, current_user)
    data = get_analysis_data(db, session_id, current_user)
    report_data = session.analysis_report
    report = ResearchAnalysisEntry(**report_data) if report_data else None
    return ResearchAnalysisDetailResponse(
        session_id=session_id, data=data, report=report
    )


def stop_analysis_report(
    db: Session, session_id: uuid.UUID, current_user: models.User
) -> ResearchAnalysisStopResponse:
    """停止正在进行中的分析报告生成。

    清除 generation ID 让后台协程检测到取消信号，并向 SSE 流推送取消事件。

    Args:
        db: 数据库会话。
        session_id: 目标会话 ID。
        current_user: 当前认证用户。

    Returns:
        取消结果响应。

    Raises:
        HTTPException: 当前不存在活跃的生成任务时抛出。
    """
    session = _get_session(db, session_id, current_user)
    sid = str(session_id)

    gen_id = get_report_generation_id(sid, _ANALYSIS_REPORT_TYPE)
    if not gen_id:
        report_data = session.analysis_report
        if report_data and report_data.get("status") == "generating":
            _persist_analysis(session_id, "cancelled", None)
        else:
            raise HTTPException(
                status_code=400, detail="No active analysis generation to stop"
            )

    clear_report_generation_id(sid, _ANALYSIS_REPORT_TYPE)
    push_report_chunk(sid, _ANALYSIS_REPORT_TYPE, {"type": "cancelled"})

    return ResearchAnalysisStopResponse(
        session_id=session_id,
        status="cancelled",
        message="Analysis report generation stopped",
    )


# ---- 后台生成协程 ----


async def _run_analysis_generation(
    session_id: uuid.UUID,
    provider_config: dict[str, Any],
    prompt: str,
    generation_id: str,
) -> None:
    """后台协程：流式拉取 LLM 输出并持久化最终结果。

    每次推 chunk 前校验 generation_id；若发现新生成任务已启动则协作式取消退出。
    """
    from llm4ad.infra.provider.base import BaseProvider

    sid = str(session_id)
    content_buffer = ""
    cancelled = False
    try:
        provider = BaseProvider.create(
            provider_config["type"], config=provider_config
        )
        async for chunk in provider.generate_stream(prompt):
            current_id = get_report_generation_id(sid, _ANALYSIS_REPORT_TYPE)
            if current_id != generation_id:
                cancelled = True
                logger.info(
                    f"Analysis generation cancelled: session_id={sid}, "
                    f"generation_id={generation_id}"
                )
                _persist_analysis(session_id, "cancelled", content_buffer or None)
                return

            content_buffer += chunk
            push_report_chunk(
                sid, _ANALYSIS_REPORT_TYPE, {"type": "chunk", "content": chunk}
            )

        push_report_chunk(sid, _ANALYSIS_REPORT_TYPE, {"type": "done"})
        _persist_analysis(session_id, "completed", content_buffer)

    except Exception as exc:
        if cancelled:
            return
        logger.exception(f"Analysis generation failed: session_id={sid}")
        push_report_chunk(
            sid, _ANALYSIS_REPORT_TYPE, {"type": "error", "error": str(exc)}
        )
        _persist_analysis(
            session_id, "failed", content_buffer, error=str(exc)
        )
    finally:
        if not cancelled:
            clear_report_generation_id(sid, _ANALYSIS_REPORT_TYPE)


def _persist_analysis(
    session_id: uuid.UUID,
    status: str,
    content: str | None,
    error: str | None = None,
) -> None:
    """新开数据库会话，将报告结果持久化到 ``session.analysis_report``。"""
    from app.core.db import engine

    with Session(engine) as db:
        session = db.get(models.ResearchSession, session_id)
        if not session:
            logger.warning(f"Session {session_id} not found when persisting analysis")
            return
        entry = copy.deepcopy(dict(session.analysis_report or {}))
        entry["status"] = status
        entry["content"] = content or None
        entry["updated_at"] = datetime.now(UTC).isoformat()
        entry["error"] = error
        session.analysis_report = entry
        flag_modified(session, "analysis_report")
        db.add(session)
        db.commit()


# ---- Provider 解析 ----


def _resolve_provider_config(
    db: Session,
    current_user: models.User,
    provider_id: str | None,
    model_name: str | None,
    access_token: str | None,
) -> dict[str, Any]:
    """把 provider_id / model_name 解析成 ``BaseProvider.create()`` 配置字典。

    优先级对齐 chat 端 ``report_service._resolve_provider_config``：
    ``"mock"`` → mock；空/``"default"`` → 用户默认报告模型；真实 UUID → 查库。

    Args:
        db: 数据库会话。
        current_user: 当前认证用户。
        provider_id: 供应商标识（"mock"/"default"/空/真实 UUID）。
        model_name: 覆盖 Provider 默认模型的模型名。
        access_token: 内置供应商 URL 占位替换用的登录 token。

    Returns:
        可直接传入 ``BaseProvider.create()`` 的配置字典。

    Raises:
        HTTPException: 无任何可用 Provider 时抛出。
    """
    if provider_id == "mock":
        return {
            "type": "mock",
            "api_key": "",
            "auth_token": "",
            "base_url": None,
            "model": model_name or "mock",
            "temperature": 0.7,
            "max_tokens": 32768,
            "timeout": 60.0,
            "max_retries": 3,
        }

    use_default = not provider_id or provider_id == "default"
    resolved_provider_id: uuid.UUID | None = None

    if use_default:
        from app.services.user_default_model_service import get_user_default_model

        defaults = get_user_default_model(db, current_user.id)
        if defaults.report_provider_id:
            resolved_provider_id = defaults.report_provider_id
            if not model_name and defaults.report_model_name:
                model_name = defaults.report_model_name
    else:
        try:
            resolved_provider_id = uuid.UUID(provider_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid provider_id: {provider_id!r}"
            ) from exc

    if not resolved_provider_id:
        raise HTTPException(
            status_code=400,
            detail="No provider configured: pass provider_id/model_name or set a "
            "default report provider",
        )

    from app.services.provider_service import get_provider_with_auth

    provider_model = get_provider_with_auth(
        db, resolved_provider_id, current_user
    )
    base_url = provider_model.base_url
    if provider_model.is_builtin and base_url and access_token:
        base_url = base_url.replace("{accessToken}", access_token)
    return {
        "type": provider_model.type.value,
        "api_key": provider_model.api_key,
        "auth_token": provider_model.auth_token,
        "base_url": base_url,
        "model": model_name
        or (
            provider_model.model.split(";")[0].strip()
            if provider_model.model
            else ""
        ),
        "temperature": provider_model.temperature,
        "max_tokens": provider_model.max_tokens,
        "timeout": provider_model.timeout,
        "max_retries": provider_model.max_retries,
    }


# ---- Prompt 构建 ----

_ANALYSIS_SYSTEM = """\
You are an expert research-pipeline analyst. The user will provide a JSON \
summary of one automated research run (the "AutoResearch" 23-stage pipeline: \
literature review, hypothesis generation, experiment design/execution, paper \
writing, quality gate, etc.). The summary contains per-stage durations and \
decisions, decision/rollback history, an experiment metrics summary, a \
quality-gate verdict, and the deliverables manifest.

Analyze the run and produce a Markdown report with exactly the following \
sections. Cite concrete numbers from the data — stage names, durations, the \
quality-gate score/verdict, rollback targets, and metric values — whenever \
possible. Be candid: if the quality gate rejected the run or flagged the \
results as degraded/fabricated, say so clearly and explain the evidence.

IMPORTANT: Output the Markdown content directly as plain text. Do NOT wrap it \
in ```markdown``` or any other code fences.

## 1. Executive Summary
Overall outcome, final status, and whether the deliverable is trustworthy.

## 2. Pipeline Execution & Bottlenecks
Which stages dominated wall-clock time; where reruns happened and why.

## 3. Decision & Rollback Analysis
Interpret each pivot/refine rollback: what triggered it, whether it helped.

## 4. Experiment Results & Quality Gate
Interpret the metrics vs. the quality-gate verdict; call out contradictions \
(e.g. figures/scripts count of 0 vs. claimed figures in the paper).

## 5. Recommendations
Concrete next steps to improve reliability of a future run.
"""


def _build_prompts(
    request: ResearchAnalysisGenerateRequest,
    data: ResearchAnalysisData,
) -> tuple[str, str]:
    """构建 (system, user) prompt；自定义模板走 ``{analysis_data}`` 占位。"""
    context = json.dumps(
        data.model_dump(mode="json"), ensure_ascii=False, indent=1, default=str
    )
    if len(context) > _MAX_CONTEXT_CHARS:
        context = context[:_MAX_CONTEXT_CHARS] + "\n\n... [truncated] ..."

    system = _ANALYSIS_SYSTEM
    if request.language == "zh":
        system += "\n\nIMPORTANT: You MUST write the entire report in Chinese (简体中文)."
    else:
        system += "\n\nIMPORTANT: You MUST write the entire report in English."

    if request.prompt_template:
        from collections import defaultdict

        user = request.prompt_template.format_map(
            defaultdict(str, {"analysis_data": context})
        )
    else:
        user = (
            "Here is the JSON summary of the research run to analyze:\n\n"
            f"{context}"
        )
    return system, user
