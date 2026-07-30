"""演化分析报告 Service。

负责报告生成的入口校验、后台 LLM 流式输出、Provider 配置解析以及
四类报告的日志数据抽取与持久化。
"""

import asyncio
import copy
import json
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
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
from app.models import TaskStatus
from app.schemas.report import (
    ReportDetailResponse,
    ReportEntry,
    ReportGenerateRequest,
    ReportGenerateResponse,
    ReportStatus,
    ReportStopResponse,
    ReportType,
)
from app.services.report_prompts import build_prompts
from app.services.task_service import get_task_with_auth

SUMMARY_CHUNK_MAX_CHARS = 40_000
INTERMEDIATE_SUMMARY_MAX_TOKENS = 4_096
INTERMEDIATE_SUMMARY_MAX_CHARS = 16_000


class _ReportGenerationCancelled(Exception):
    """Raised when a report generation is superseded or explicitly stopped."""


def generate_report(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    request: ReportGenerateRequest,
    access_token: str | None = None,
) -> ReportGenerateResponse:
    """校验输入并启动后台报告生成任务。

    若同一报告类型已有正在运行的生成任务，会通过协作式取消方式终止旧任务，
    随后立即启动新的生成。

    Args:
        db: 数据库会话。
        task_id: 目标任务 ID。
        current_user: 当前认证用户。
        request: 报告生成请求。
        access_token: 当前登录 token，用于替换内置供应商 URL 中的占位。

    Returns:
        状态为 ``generating`` 的 ``ReportGenerateResponse``。

    Raises:
        HTTPException: 输入校验失败时抛出。
    """
    task = get_task_with_auth(db, task_id, current_user)
    report_type = request.report_type.value

    if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail="Only completed or failed tasks with logs can generate reports",
        )

    logs = _get_task_logs(task)
    if not logs:
        raise HTTPException(status_code=400, detail="Task has no logs to analyze")

    provider_config = _resolve_provider_config(
        db, current_user, task, request.provider_id, request.model_name, access_token,
    )

    log_data = _extract_log_data(logs, request)
    background = (task.input_args or {}).get("background", "")
    prompt: str | None = None
    if request.report_type != ReportType.TECH_CHANGE:
        prompt = _build_report_prompt(request, log_data, background)

    # Cancel previous generation: push cancelled event, delete old stream
    old_gen_id = get_report_generation_id(str(task_id), report_type)
    if old_gen_id:
        push_report_chunk(
            str(task_id),
            report_type,
            {"type": "cancelled"},
        )
    delete_report_stream(str(task_id), report_type)

    # Set new generation ID
    generation_id = uuid.uuid4().hex
    set_report_generation_id(str(task_id), report_type, generation_id)

    now = datetime.now(UTC).isoformat()
    report_entry = {
        "status": ReportStatus.GENERATING.value,
        "content": None,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "provider_model": provider_config.get("model", ""),
        "params": _build_params_dict(request),
    }

    if task.reports is None:
        task.reports = {}
    task.reports = {**task.reports, report_type: report_entry}
    db.add(task)
    db.commit()

    asyncio.get_event_loop().create_task(
        _run_report_generation(
            task_id=task_id,
            report_type=report_type,
            provider_config=provider_config,
            prompt=prompt,
            generation_id=generation_id,
            request=request,
            log_data=log_data,
            background=background,
        )
    )

    return ReportGenerateResponse(
        task_id=task_id,
        report_type=request.report_type,
        status=ReportStatus.GENERATING,
        message="Report generation started",
    )


def get_report(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    report_type: str,
) -> ReportDetailResponse:
    """从任务的 reports 字典中读取指定类型的报告。

    Args:
        db: 数据库会话。
        task_id: 目标任务 ID。
        current_user: 当前认证用户。
        report_type: 报告类型字符串。

    Returns:
        包含报告条目（或 None）的 ``ReportDetailResponse``。
    """
    task = get_task_with_auth(db, task_id, current_user)
    report_data = (task.reports or {}).get(report_type)
    report_entry = ReportEntry(**report_data) if report_data else None
    return ReportDetailResponse(
        task_id=task_id,
        report_type=ReportType(report_type),
        report=report_entry,
    )


def stop_report_generation(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    report_type: str,
) -> ReportStopResponse:
    """停止正在进行中的报告生成。

    通过清除 generation ID 让后台协程检测到取消信号，并向 SSE 流推送
    取消事件，同时更新数据库中的状态。

    Args:
        db: 数据库会话。
        task_id: 目标任务 ID。
        current_user: 当前认证用户。
        report_type: 报告类型字符串。

    Returns:
        包含取消结果的 ``ReportStopResponse``。

    Raises:
        HTTPException: 当前不存在活跃的报告生成任务时抛出。
    """
    task = get_task_with_auth(db, task_id, current_user)

    gen_id = get_report_generation_id(str(task_id), report_type)
    if not gen_id:
        report_data = (task.reports or {}).get(report_type)
        if not report_data or report_data.get("status") != ReportStatus.GENERATING.value:
            raise HTTPException(
                status_code=400,
                detail="No active report generation to stop",
            )
    elif not clear_report_generation_id(str(task_id), report_type, gen_id):
        raise HTTPException(
            status_code=409,
            detail="Report generation changed while stopping; please retry",
        )

    _persist_report(task_id, report_type, ReportStatus.CANCELLED, None)
    push_report_chunk(
        str(task_id),
        report_type,
        {"type": "cancelled"},
    )

    return ReportStopResponse(
        task_id=task_id,
        report_type=ReportType(report_type),
        status=ReportStatus.CANCELLED,
        message="Report generation stopped",
    )


# ---- Background generation coroutine ----


async def _run_report_generation(
    task_id: uuid.UUID,
    report_type: str,
    provider_config: dict[str, Any],
    prompt: str | None,
    generation_id: str,
    request: ReportGenerateRequest,
    log_data: list[dict[str, Any]] | str,
    background: str,
) -> None:
    """以协程方式后台流式拉取 LLM 输出并持久化最终结果。

    每次推送 chunk 前都会校验 generation_id；若发现新的生成任务已被启动，
    则以协作式取消方式优雅退出。

    Args:
        task_id: 目标任务 ID。
        report_type: 报告类型字符串。
        provider_config: Provider 配置字典。
        prompt: 拼接后的完整 Prompt（system + user）。
        generation_id: 本次生成任务的唯一 ID。
    """
    from llm4ad.infra.provider.base import BaseProvider

    content_buffer = ""
    try:
        provider = BaseProvider.create(
            provider_config["type"],
            config=provider_config,
        )
        if request.report_type == ReportType.TECH_CHANGE:
            push_report_chunk(
                str(task_id),
                report_type,
                {"type": "progress", "stage": "prepare"},
            )

            def _is_cancelled() -> bool:
                return get_report_generation_id(str(task_id), report_type) != generation_id

            def _on_progress(progress: dict[str, Any]) -> None:
                push_report_chunk(
                    str(task_id),
                    report_type,
                    {"type": "progress", **progress},
                )

            summary = await _summarize_evolution_evidence(
                provider,
                log_data if isinstance(log_data, list) else [],
                on_progress=_on_progress,
                is_cancelled=_is_cancelled,
                background=background,
            )
            if _is_cancelled():
                raise _ReportGenerationCancelled
            push_report_chunk(
                str(task_id),
                report_type,
                {"type": "progress", "stage": "generate"},
            )
            prompt = _build_report_prompt(request, summary, background)

        if prompt is None:
            raise RuntimeError("Report prompt was not initialized")
        async for chunk in provider.generate_stream(prompt):
            current_id = get_report_generation_id(str(task_id), report_type)
            if current_id != generation_id:
                logger.info(
                    f"Report generation cancelled: task_id={task_id}, type={report_type}, generation_id={generation_id}"
                )
                return

            content_buffer += chunk
            push_report_chunk(
                str(task_id),
                report_type,
                {"type": "chunk", "content": chunk},
            )

        if get_report_generation_id(str(task_id), report_type) != generation_id:
            logger.info(
                f"Report generation cancelled before completion: task_id={task_id}, type={report_type}, generation_id={generation_id}"
            )
            return

        push_report_chunk(
            str(task_id),
            report_type,
            {"type": "done"},
        )

        _persist_report(task_id, report_type, ReportStatus.COMPLETED, content_buffer)

    except _ReportGenerationCancelled:
        logger.info(
            f"Report generation cancelled: task_id={task_id}, type={report_type}, generation_id={generation_id}"
        )

    except Exception as exc:
        if get_report_generation_id(str(task_id), report_type) != generation_id:
            return
        logger.exception(f"Report generation failed: task_id={task_id}, type={report_type}")
        push_report_chunk(
            str(task_id),
            report_type,
            {"type": "error", "error": str(exc)},
        )
        _persist_report(task_id, report_type, ReportStatus.FAILED, content_buffer, error=str(exc))
    finally:
        clear_report_generation_id(str(task_id), report_type, generation_id)


def _persist_report(
    task_id: uuid.UUID,
    report_type: str,
    status: ReportStatus,
    content: str,
    error: str | None = None,
) -> None:
    """新开一个数据库会话，将报告结果持久化到 task.reports。

    Args:
        task_id: 目标任务 ID。
        report_type: 报告类型字符串。
        status: 报告最终状态。
        content: 已生成的 Markdown 内容。
        error: 失败时的错误信息。
    """
    from app.core.db import engine

    with Session(engine) as db:
        task = db.get(models.Task, task_id)
        if not task:
            logger.warning(f"Task {task_id} not found when persisting report")
            return

        reports = copy.deepcopy(dict(task.reports or {}))
        entry = reports.get(report_type, {})
        entry["status"] = status.value
        entry["content"] = content or None
        entry["updated_at"] = datetime.now(UTC).isoformat()
        entry["error"] = error
        reports[report_type] = entry
        task.reports = reports
        flag_modified(task, "reports")
        db.add(task)
        db.commit()


# ---- Helper functions ----


def _get_task_logs(task: models.Task) -> list[dict]:
    """获取任务日志：优先从 task_log 表读取，回退到 Redis。"""
    from sqlmodel import Session, select

    from app.core.db import engine
    from app.models import TaskLog
    from app.utils.log_persist import task_log_to_dict

    with Session(engine) as db:
        rows = db.exec(select(TaskLog).where(TaskLog.task_id == task.id).order_by(TaskLog.timestamp.asc())).all()

    if rows:
        return [task_log_to_dict(row) for row in rows]

    from app.core.redis import read_all_logs

    return read_all_logs(task.id)


def _resolve_provider_config(
    db: Session,
    current_user: models.User,
    task: models.Task,
    provider_id: str | None,
    model_name: str | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    """根据 provider_id 或用户默认报告模型配置解析 Provider 配置。

    优先级：
    1. provider_id 为 "mock" 时，返回 mock 配置。
    2. provider_id 为真实 UUID 时，从数据库查询对应供应商。
    3. provider_id 为空/"default" 时，使用用户默认模型配置中的 report_provider_id。
    4. 以上均无，回退到 task.input_args 中的第一个 Provider。

    若传入了 model_name，则覆盖 Provider 自身的 model 字段。
    内置供应商的 ``base_url`` 中的 ``{accessToken}`` 占位会被替换为当前登录
    token；其他占位（如 ``{teamId}``）保留原样由上游处理。

    Args:
        db: 数据库会话。
        current_user: 当前认证用户。
        task: 目标任务。
        provider_id: 可选的 Provider ID 字符串，支持 "default"/"mock"/真实UUID。
        model_name: 可选的模型名称，用于覆盖 Provider 默认模型。
        access_token: 当前登录 token，用于替换内置供应商 URL 中的占位。

    Returns:
        可直接传入 ``BaseProvider.create()`` 的配置字典。

    Raises:
        HTTPException: Provider 不存在或缺少 API Key 时抛出。
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

    # "default" or empty string → use user default
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
        resolved_provider_id = uuid.UUID(provider_id)

    if resolved_provider_id:
        from app.services.provider_service import get_provider_with_auth

        provider_model = get_provider_with_auth(db, resolved_provider_id, current_user)
        base_url = provider_model.base_url
        if provider_model.is_builtin and base_url and access_token:
            base_url = base_url.replace("{accessToken}", access_token)
        config = {
            "type": provider_model.type.value,
            "api_key": provider_model.api_key,
            "auth_token": provider_model.auth_token,
            "base_url": base_url,
            "model": model_name or (provider_model.model.split(";")[0].strip() if provider_model.model else ""),
            "temperature": provider_model.temperature,
            "max_tokens": provider_model.max_tokens,
            "timeout": provider_model.timeout,
            "max_retries": provider_model.max_retries,
        }
    else:
        providers = (task.input_args or {}).get("providers", [])
        if not providers:
            raise HTTPException(
                status_code=400,
                detail="No provider configured: no provider_id specified, no default report provider, and no provider in task input_args",
            )
        p = providers[0]
        config = {
            "type": p.get("type", "openai"),
            "api_key": p.get("api_key", ""),
            "auth_token": p.get("auth_token", ""),
            "base_url": p.get("base_url"),
            "model": model_name or p.get("model", "gpt-4"),
            "temperature": p.get("temperature", 0.7),
            "max_tokens": p.get("max_tokens", 32768),
            "timeout": p.get("timeout", 60.0),
            "max_retries": p.get("max_retries", 3),
        }

    return config


def _extract_log_data(logs: list[dict], request: ReportGenerateRequest) -> list[dict] | str:
    """根据报告类型抽取相关日志数据。

    Returns:
        ``tech_change``：用于分层总结的紧凑节点证据；
        其他类型：节点数据字典列表。
    """
    if request.report_type == ReportType.TECH_CHANGE:
        return _build_evolution_evidence(logs)

    if request.report_type == ReportType.NODE_COMPARISON:
        nodes = _extract_nodes_from_logs(logs, request.node_ids or [])
        if not nodes:
            raise HTTPException(
                status_code=400,
                detail="Specified node(s) not found in task logs",
            )
        return nodes

    if request.report_type == ReportType.CHAIN_ANALYSIS:
        nodes = _extract_nodes_from_logs(logs, request.node_ids or [])
        if not nodes:
            raise HTTPException(
                status_code=400,
                detail="Specified node(s) not found in task logs",
            )
        return nodes

    if request.report_type == ReportType.CHAMPION_BIRTH:
        return _extract_lineage(logs, request.best_node_id or "")

    return []


def _build_report_prompt(
    request: ReportGenerateRequest,
    log_data: list[dict] | str,
    background: str,
) -> str:
    """构建最终报告 Prompt。"""

    system, user = build_prompts(request.report_type, log_data, background)

    if request.language == "zh":
        system += "\n\nIMPORTANT: You MUST write the entire report in Chinese (简体中文)."
    else:
        system += "\n\nIMPORTANT: You MUST write the entire report in English."

    if request.prompt_template:
        user = _render_custom_template(
            request.prompt_template, request.report_type, log_data, background
        )

    return f"{system}\n\n{user}"


def _render_custom_template(
    template: str,
    report_type: ReportType,
    log_data: list[dict] | str,
    background: str,
) -> str:
    """将自定义模板中的 {variable} 占位符替换为实际值。

    未匹配的占位符保持原样不报错。

    Args:
        template: 前端传入的自定义 user prompt 模板。
        report_type: 报告类型，决定数据变量名。
        log_data: 抽取后的日志数据。
        task: 目标任务对象。

    Returns:
        填充变量后的 user prompt 字符串。
    """
    from app.services.report_prompts import _serialize_nodes, _truncate

    background = background or "No additional background provided."

    variables: dict[str, str] = {"background": background}

    if report_type == ReportType.TECH_CHANGE:
        variables["logs_summary"] = _truncate(str(log_data))
    elif report_type == ReportType.NODE_COMPARISON:
        variables["nodes_data"] = _truncate(_serialize_nodes(log_data))
    elif report_type == ReportType.CHAIN_ANALYSIS:
        variables["chain_data"] = _truncate(_serialize_nodes(log_data))
    elif report_type == ReportType.CHAMPION_BIRTH:
        variables["lineage_data"] = _truncate(_serialize_nodes(log_data))

    safe_map = defaultdict(str, variables)
    return template.format_map(safe_map)


def _build_params_dict(request: ReportGenerateRequest) -> dict:
    """构建与报告一同存储的参数快照字典。"""
    params: dict[str, Any] = {
        "report_type": request.report_type.value,
        "language": request.language,
    }
    if request.node_ids:
        params["node_ids"] = request.node_ids
    if request.best_node_id:
        params["best_node_id"] = request.best_node_id
    if request.provider_id:
        params["provider_id"] = str(request.provider_id)
    if request.prompt_template:
        params["prompt_template"] = request.prompt_template
    return params


def _limit_text(value: Any, max_chars: int) -> str:
    """Convert a value to bounded text while making truncation explicit."""
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    marker = "… [truncated]"
    if max_chars <= len(marker):
        return marker[:max_chars]
    return f"{text[: max_chars - len(marker)]}{marker}"


def _limit_json(value: Any, max_chars: int) -> Any:
    """Keep structured values when compact, otherwise retain a bounded representation."""
    if value is None:
        return None
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if len(serialized) <= max_chars:
        return value
    return _limit_text(serialized, max_chars)


def _build_evolution_evidence(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract compact, report-relevant evidence from generated evolution events."""
    evidence: list[dict[str, Any]] = []
    for entry in logs:
        if entry.get("type") != "generated":
            continue
        data = entry.get("data")
        if not isinstance(data, dict):
            continue

        evaluation = data.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        generation_meta = data.get("generation_meta")
        generation_meta = generation_meta if isinstance(generation_meta, dict) else {}

        item: dict[str, Any] = {
            "node_id": str(data.get("id") or ""),
            "generation": data.get("generation"),
            "parent_ids": list(data.get("parent_ids") or []),
            "name": _limit_text(data.get("name"), 400),
            "description": _limit_text(data.get("description"), 1_200),
            "key_innovations": [
                _limit_text(innovation, 400)
                for innovation in (data.get("key_innovations") or [])[:8]
            ],
            "score": evaluation.get("score", data.get("score")),
            "metrics": _limit_json(
                evaluation.get("metrics", data.get("metrics")), 1_200
            ),
            "operator": generation_meta.get(
                "operator_name", generation_meta.get("operator", "")
            ),
            "change_description": _limit_text(
                generation_meta.get("change_description", data.get("design_notes", "")),
                1_000,
            ),
        }
        error = evaluation.get("error", data.get("error"))
        if error:
            item["error"] = _limit_text(error, 800)
        evidence.append(item)
    if evidence:
        return evidence

    for entry in logs:
        if entry.get("type") not in ("log", "print"):
            continue
        message = entry.get("message") or entry.get("data")
        if not message:
            continue
        evidence.append(
            {
                "event_type": entry.get("type"),
                "timestamp": entry.get("timestamp"),
                "message": _limit_text(message, 1_200),
            }
        )
    return evidence


def _pack_summary_items(items: list[str], max_chars: int) -> list[str]:
    """Pack ordered summary inputs into bounded chunks without splitting normal entries."""
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    separator_length = 2

    for item in items:
        bounded_item = _limit_text(item, max_chars)
        addition = len(bounded_item) + (separator_length if current else 0)
        if current and current_length + addition > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_length = 0
        current.append(bounded_item)
        current_length += len(bounded_item) + (separator_length if len(current) > 1 else 0)

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _build_intermediate_summary_prompt(
    chunk: str,
    round_number: int,
    background: str,
) -> str:
    """Build the fixed prompt used to compress one hierarchy level of evidence."""
    task_background = background or "No additional background provided."
    return f"""You are compressing algorithm-evolution evidence for a later final report.

Task background:
{task_background}

Evidence or prior summaries (round {round_number}):
{chunk}

Produce a high-fidelity factual summary. Preserve concrete node IDs, generations,
scores, score changes, parent relationships, key techniques, failures, and trade-offs.
Do not invent evidence. Focus on trends and representative changes that a later
analyst needs; do not include source code or a preamble."""


async def _summarize_evolution_evidence(
    provider: Any,
    evidence: list[dict[str, Any]],
    *,
    on_progress: Callable[[dict[str, Any]], None],
    is_cancelled: Callable[[], bool] | None = None,
    background: str = "",
    max_chars: int = SUMMARY_CHUNK_MAX_CHARS,
    max_tokens: int = INTERMEDIATE_SUMMARY_MAX_TOKENS,
) -> str:
    """Recursively summarize compact node evidence until one final summary remains."""
    items = [json.dumps(item, ensure_ascii=False, default=str) for item in evidence]
    if not items:
        return "No generated evolution nodes were available for analysis."

    round_number = 1
    while True:
        chunks = _pack_summary_items(items, max_chars)
        summaries: list[str] = []
        stage = "summarize" if round_number == 1 else "merge"

        for index, chunk in enumerate(chunks, start=1):
            if is_cancelled and is_cancelled():
                raise _ReportGenerationCancelled
            on_progress(
                {
                    "stage": stage,
                    "round": round_number,
                    "completed": index,
                    "total": len(chunks),
                }
            )
            result = await provider.generate(
                _build_intermediate_summary_prompt(chunk, round_number, background),
                max_tokens=max_tokens,
            )
            if is_cancelled and is_cancelled():
                raise _ReportGenerationCancelled
            text = _limit_text(result.text.strip(), INTERMEDIATE_SUMMARY_MAX_CHARS)
            if not text:
                raise RuntimeError("Intermediate evolution summary was empty")
            summaries.append(text)

        if len(summaries) == 1:
            return summaries[0]
        items = summaries
        round_number += 1


def _extract_nodes_from_logs(logs: list[dict], node_ids: list[str]) -> list[dict]:
    """从日志中抽取 data.id 与请求节点 ID 列表匹配的 generated 条目。"""
    node_id_set = set(node_ids)
    results = []
    for entry in logs:
        if entry.get("type") != "generated":
            continue
        data = entry.get("data")
        if not isinstance(data, dict):
            continue
        entry_id = str(data.get("id", ""))
        if entry_id in node_id_set:
            results.append(data)
    return results


def _extract_lineage(logs: list[dict], best_node_id: str) -> list[dict]:
    """回溯最佳节点的演化血脉，直至初代祖先。

    节点通过 parent_ids 引用多个父代（支持 N 父代交叉），血脉是一个 DAG。
    从冠军节点出发沿所有父代回溯，对祖先去重后按日志出现顺序
    （即演化时间顺序）返回从初代祖先到冠军的有序列表。
    """
    nodes_by_id: dict[str, dict] = {}
    ordered_ids: list[str] = []
    for entry in logs:
        if entry.get("type") != "generated":
            continue
        data = entry.get("data")
        if not isinstance(data, dict):
            continue
        nid = str(data.get("id", ""))
        if nid and nid not in nodes_by_id:
            nodes_by_id[nid] = data
            ordered_ids.append(nid)

    if best_node_id not in nodes_by_id:
        raise HTTPException(
            status_code=400,
            detail=f"Best node '{best_node_id}' not found in task logs",
        )

    visited: set[str] = set()
    stack: list[str] = [best_node_id]
    while stack:
        current_id = stack.pop()
        if current_id in visited or current_id not in nodes_by_id:
            continue
        visited.add(current_id)
        node = nodes_by_id[current_id]
        parent_ids = node.get("parent_ids") or []
        # parent_ids 可能包含重复 id，去重后再回溯
        for pid in dict.fromkeys(str(p) for p in parent_ids if p):
            if pid not in visited:
                stack.append(pid)

    return [nodes_by_id[nid] for nid in ordered_ids if nid in visited]
