"""Translate Python Agent SDK messages into credential-safe progress events."""

from __future__ import annotations

import json
import posixpath
import time
from dataclasses import dataclass, field
from typing import Any

_TOOL_COPY = {
    "Read": ("正在读取原始文档", "原始文档读取完成"),
    "Glob": ("正在检查文档清单", "文档清单检查完成"),
    "Grep": ("正在检索文档结构", "文档结构检索完成"),
    "Write": ("正在生成结构化文档", "结构化文档生成完成"),
    "Edit": ("正在更新结构化文档", "结构化文档更新完成"),
    "StructuredOutput": ("正在整理结构化解析方案", "结构化方案已生成，正在校验并保存"),
    "SaveSourceAnalysis": ("正在保存文档结构分析", "文档结构分析已保存"),
    "SavePlanCandidate": ("正在保存候选解析方案", "候选解析方案已保存"),
    "FinalizePlanSet": ("正在完成解析方案", "解析方案已完成并保存"),
    "GetPlanCandidate": ("正在读取选定解析方案", "选定解析方案已载入"),
}

_TOOL_ALIASES = {
    "mcp__knowledge_plan__save_source_analysis": "SaveSourceAnalysis",
    "mcp__knowledge_plan__upsert_plan_candidate": "SavePlanCandidate",
    "mcp__knowledge_plan__finalize_plan_set": "FinalizePlanSet",
    "mcp__knowledge_plan__get_plan_candidate": "GetPlanCandidate",
}


def _value(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _message_type(message: object) -> str:
    explicit = _value(message, "type")
    if explicit:
        return str(explicit)
    return {
        "SystemMessage": "system",
        "StreamEvent": "stream_event",
        "UserMessage": "user",
        "AssistantMessage": "assistant",
        "ResultMessage": "result",
        "RateLimitEvent": "rate_limit_event",
    }.get(type(message).__name__, "")


def _block_type(block: object) -> str:
    explicit = _value(block, "type")
    if explicit:
        return str(explicit)
    return {
        "TextBlock": "text",
        "ToolUseBlock": "tool_use",
        "ToolResultBlock": "tool_result",
    }.get(type(block).__name__, "")


def _rounded(value: object) -> int | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return max(0, round(float(value)))
    except (TypeError, ValueError):
        return None


@dataclass
class EventState:
    """Mutable progress state for one parser agent invocation."""

    last_progress: int = 18
    result_error: str = ""
    tools: dict[str, str] = field(default_factory=dict)
    tool_details: dict[str, str] = field(default_factory=dict)
    model_started_at: int | None = None
    last_model_heartbeat_at: int | None = None
    compaction_count: int = 0
    active_compaction_id: str | None = None

    def compaction_started(self) -> dict[str, object]:
        """Create the safe event emitted immediately before native compaction."""
        self.compaction_count += 1
        self.active_compaction_id = f"context-compaction-{self.compaction_count}"
        return _step_event(
            self,
            "上下文接近模型上限，正在压缩后继续解析",
            stage="compacting",
            step_id=self.active_compaction_id,
            step_kind="context",
            step_status="running",
        )

    def compaction_finished(self) -> dict[str, object]:
        """Create the safe event emitted for the compact boundary message."""
        step_id = self.active_compaction_id or f"context-compaction-{max(1, self.compaction_count)}"
        self.active_compaction_id = None
        return _step_event(
            self,
            "上下文压缩完成，正在继续解析",
            step_id=step_id,
            step_kind="context",
            step_status="success",
        )


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Recover the largest complete JSON object embedded in model prose."""
    decoder = json.JSONDecoder()
    best: dict[str, Any] | None = None
    best_length = 0
    for start, character in enumerate(text):
        if character != "{":
            continue
        try:
            candidate, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and end > best_length:
            best = candidate
            best_length = end
    return best


def recover_json_object(primary_text: str, fallback_text: str = "") -> dict[str, Any] | None:
    """Recover a structured payload from the preferred or fallback response."""
    for text in (primary_text, fallback_text):
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = extract_json_object(text)
        if isinstance(payload, dict):
            return payload
    return None


def _progress_event(state: EventState, progress: int, stage: str, message: str) -> dict[str, object]:
    state.last_progress = max(state.last_progress, progress)
    return {
        "type": "progress",
        "progress": state.last_progress,
        "stage": stage,
        "message": message,
    }


def _step_event(
    state: EventState,
    message: str,
    *,
    step_id: str,
    step_kind: str,
    step_status: str,
    stage: str = "analyzing",
    tool_name: str = "",
    elapsed_seconds: object = None,
    attempt: object = None,
    max_retries: object = None,
    retry_delay_ms: object = None,
    step_detail: str = "",
) -> dict[str, object]:
    event: dict[str, object] = {
        "type": "step",
        "progress": state.last_progress,
        "stage": stage,
        "message": message,
        "step_id": step_id,
        "step_kind": step_kind,
        "step_status": step_status,
    }
    if tool_name:
        event["tool_name"] = tool_name
    if step_detail:
        event["step_detail"] = step_detail[:500]
    for name, value in (
        ("elapsed_seconds", elapsed_seconds),
        ("attempt", attempt),
        ("max_retries", max_retries),
        ("retry_delay_ms", retry_delay_ms),
    ):
        rounded = _rounded(value)
        if rounded is not None:
            event[name] = rounded
    return event


def _visible_tool_name(value: object) -> str:
    name = str(value or "")
    visible = _TOOL_ALIASES.get(name, name)
    return visible if visible in _TOOL_COPY else ""


def _safe_text(value: object, maximum: int = 160) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _safe_workspace_path(value: object) -> str:
    raw = str(value or "").replace("\\", "/")
    normalized = posixpath.normpath(raw)
    visible_roots = (
        "/workspace/input/documents",
        "/workspace/input/selected-plan.json",
        "/workspace/output",
    )
    if not any(normalized == root or normalized.startswith(f"{root}/") for root in visible_roots):
        return ""
    return normalized.removeprefix("/workspace/")[:400]


def _safe_tool_detail(tool_name: str, input_data: object) -> str:
    if not isinstance(input_data, dict):
        return ""
    path = _safe_workspace_path(input_data.get("file_path") or input_data.get("path"))
    if tool_name in {"Read", "Write", "Edit"}:
        return path
    if not path:
        return ""
    if tool_name == "Glob":
        pattern = _safe_text(input_data.get("pattern"))
        return " · ".join(part for part in (path, pattern) if part)
    if tool_name == "Grep":
        pattern = _safe_text(input_data.get("pattern"))
        return " · ".join(part for part in (path, pattern) if part)
    return ""


def _tool_stage(tool_name: str) -> str:
    if tool_name == "StructuredOutput":
        return "verifying"
    if tool_name in {"SaveSourceAnalysis", "SavePlanCandidate", "FinalizePlanSet", "GetPlanCandidate"}:
        return "planning"
    return "generating" if tool_name in {"Write", "Edit"} else "analyzing"


def _advance_for_tool(state: EventState, tool_name: str) -> None:
    if tool_name in {"StructuredOutput", "FinalizePlanSet"}:
        next_progress = 84
    elif tool_name in {"SaveSourceAnalysis", "SavePlanCandidate", "GetPlanCandidate"}:
        next_progress = min(80, state.last_progress + 5)
    elif tool_name in {"Write", "Edit"}:
        next_progress = max(72, state.last_progress + 4)
    else:
        next_progress = min(68, state.last_progress + 4)
    state.last_progress = max(state.last_progress, next_progress)


def _system_data(message: object) -> dict[str, Any]:
    data = _value(message, "data", {})
    return data if isinstance(data, dict) else {}


def translate_sdk_message(message: object, state: EventState, *, now_ms: int | None = None) -> list[dict[str, object]]:
    """Translate one SDK dataclass or test dictionary without leaking tool data."""
    message_type = _message_type(message)
    now_ms = round(time.time() * 1000) if now_ms is None else now_ms

    if message_type == "assistant" and (bool(_value(message, "isApiErrorMessage", False)) or _value(message, "error")):
        content = _value(message, "content", [])
        error_text = (
            "; ".join(
                str(_value(block, "text", "")).strip()
                for block in content
                if _block_type(block) == "text" and str(_value(block, "text", "")).strip()
            )
            if isinstance(content, list)
            else ""
        )
        state.result_error = error_text or str(_value(message, "error") or "provider_request_failed")
        return [
            _step_event(
                state,
                "模型响应失败",
                stage="failed",
                step_id="model-response",
                step_kind="model",
                step_status="failed",
            )
        ]

    if message_type == "assistant":
        content = _value(message, "content", [])
        if not isinstance(content, list):
            return []
        events = []
        for block in content:
            if _block_type(block) != "tool_use":
                continue
            tool_name = _visible_tool_name(_value(block, "name"))
            step_id = str(_value(block, "id", ""))
            if not tool_name or not step_id:
                continue
            if step_id not in state.tools:
                _advance_for_tool(state, tool_name)
            state.tools[step_id] = tool_name
            detail = _safe_tool_detail(tool_name, _value(block, "input", {}))
            if detail:
                state.tool_details[step_id] = detail
            events.append(
                _step_event(
                    state,
                    _TOOL_COPY[tool_name][0],
                    stage=_tool_stage(tool_name),
                    step_id=step_id,
                    step_kind="tool",
                    step_status="running",
                    tool_name=tool_name,
                    step_detail=state.tool_details.get(step_id, ""),
                )
            )
        return events

    if message_type == "system":
        subtype = str(_value(message, "subtype", ""))
        data = _system_data(message)
        if subtype == "init":
            return [_progress_event(state, 20, "initializing", "智能解析环境已就绪")]
        if subtype == "tool_progress":
            step_id = str(data.get("tool_use_id") or "")
            tool_name = state.tools.get(step_id) or _visible_tool_name(data.get("tool_name"))
            if not step_id or not tool_name:
                return []
            return [
                _step_event(
                    state,
                    _TOOL_COPY[tool_name][0],
                    stage=_tool_stage(tool_name),
                    step_id=step_id,
                    step_kind="tool",
                    step_status="running",
                    tool_name=tool_name,
                    elapsed_seconds=data.get("elapsed_time_seconds"),
                    step_detail=state.tool_details.get(step_id, ""),
                )
            ]
        if subtype == "api_retry":
            attempt = data.get("attempt", 1)
            return [
                _step_event(
                    state,
                    "模型服务暂时不可用，正在重试",
                    step_id=f"api-retry-{attempt}",
                    step_kind="retry",
                    step_status="retrying",
                    attempt=attempt,
                    max_retries=data.get("max_retries", 0),
                    retry_delay_ms=data.get("retry_delay_ms", 0),
                )
            ]
        if subtype == "permission_denied":
            step_id = str(data.get("tool_use_id") or "permission-denied")
            tool_name = state.tools.get(step_id) or _visible_tool_name(data.get("tool_name"))
            return [
                _step_event(
                    state,
                    "解析工具没有获得执行权限",
                    stage="failed",
                    step_id=step_id,
                    step_kind="tool",
                    step_status="failed",
                    tool_name=tool_name,
                    step_detail=state.tool_details.get(step_id, ""),
                )
            ]
        if subtype == "compact_boundary":
            return [state.compaction_finished()]
        return []

    if message_type == "stream_event":
        event = _value(message, "event", {})
        if not isinstance(event, dict):
            return []
        if event.get("type") == "content_block_delta":
            delta = event.get("delta") or {}
            if isinstance(delta, dict) and delta.get("type") == "text_delta" and delta.get("text"):
                if state.model_started_at is None:
                    state.model_started_at = now_ms
                events: list[dict[str, object]] = [
                    {
                        "type": "output",
                        "progress": state.last_progress,
                        "stage": "agent_output",
                        "message": str(delta["text"]),
                    }
                ]
                if state.last_model_heartbeat_at is None or now_ms - state.last_model_heartbeat_at >= 2000:
                    state.last_model_heartbeat_at = now_ms
                    events.append(
                        _step_event(
                            state,
                            "模型正在生成解析方案",
                            step_id="model-response",
                            step_kind="model",
                            step_status="running",
                            elapsed_seconds=(now_ms - state.model_started_at) / 1000,
                        )
                    )
                return events
        if event.get("type") == "content_block_start":
            block = event.get("content_block") or {}
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                return []
            tool_name = _visible_tool_name(block.get("name"))
            if not tool_name:
                return []
            step_id = str(block.get("id") or f"tool-{len(state.tools) + 1}")
            state.tools[step_id] = tool_name
            detail = _safe_tool_detail(tool_name, block.get("input"))
            if detail:
                state.tool_details[step_id] = detail
            _advance_for_tool(state, tool_name)
            return [
                _step_event(
                    state,
                    _TOOL_COPY[tool_name][0],
                    stage=_tool_stage(tool_name),
                    step_id=step_id,
                    step_kind="tool",
                    step_status="running",
                    tool_name=tool_name,
                    step_detail=state.tool_details.get(step_id, ""),
                )
            ]
        return []

    if message_type == "user":
        content = _value(message, "content")
        if content is None:
            nested = _value(message, "message", {})
            content = _value(nested, "content", [])
        if not isinstance(content, list):
            return []
        events = []
        for block in content:
            if _block_type(block) != "tool_result":
                continue
            step_id = str(_value(block, "tool_use_id", ""))
            completed_tool_name = state.tools.get(step_id)
            if not step_id or not completed_tool_name:
                continue
            failed = bool(_value(block, "is_error", False))
            events.append(
                _step_event(
                    state,
                    "解析工具执行失败" if failed else _TOOL_COPY[completed_tool_name][1],
                    stage="failed" if failed else _tool_stage(completed_tool_name),
                    step_id=step_id,
                    step_kind="tool",
                    step_status="failed" if failed else "success",
                    tool_name=completed_tool_name,
                    step_detail=state.tool_details.get(step_id, ""),
                )
            )
        return events

    if message_type == "result":
        subtype = str(_value(message, "subtype", ""))
        if bool(_value(message, "is_error", False)) or subtype != "success":
            if not state.result_error:
                errors = _value(message, "errors")
                if isinstance(errors, list) and errors:
                    state.result_error = "; ".join(map(str, errors))
                else:
                    state.result_error = str(
                        _value(message, "result") or _value(message, "error") or _value(message, "message") or ""
                    )
            return [
                _step_event(
                    state,
                    "模型响应失败",
                    stage="failed",
                    step_id="model-response",
                    step_kind="model",
                    step_status="failed",
                )
            ]
        return [
            _step_event(
                state,
                "模型响应完成",
                step_id="model-response",
                step_kind="model",
                step_status="success",
            ),
            _progress_event(state, 88, "verifying", "模型已完成整理，正在校验文档结构"),
        ]

    return []
