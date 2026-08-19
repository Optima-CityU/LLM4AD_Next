"""Celery task that runs the Claude Agent SDK in an ephemeral parser container."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from loguru import logger

from app import models
from app.core.celery import celery_app
from app.core.config import settings
from app.core.db import get_db_session
from app.core.redis import (
    delete_knowledge_parse_context,
    pop_knowledge_parse_context,
    push_knowledge_parse_event,
)
from app.core.storage import storage
from app.schemas import knowledge as knowledge_schemas
from app.services import credential_broker, knowledge_cleanup, knowledge_service
from app.services.container_runtime import (
    ContainerJob,
    ContainerJobCallbacks,
    ContainerJobSpec,
    ContainerJobStatus,
)
from app.services.container_service import resolve_host_path

_CONTAINER_WORKSPACE = "/workspace"
_DEFAULT_MAX_OUTPUT_TOKENS = 32000


class KnowledgeParserFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def effective_parser_output_tokens(provider_limit: int | None, *, job_mode: str = "execute") -> int:
    """Use the parser binding's output budget for both planning and execution."""
    del job_mode
    return _DEFAULT_MAX_OUTPUT_TOKENS if provider_limit is None else max(1, int(provider_limit))


def classify_parser_failure(raw: str) -> tuple[str, str]:
    """Map provider/container diagnostics to stable, credential-safe feedback."""
    normalized = (raw or "").lower()
    if "user_response_timeout" in normalized:
        return "user_response_timeout", "等待方案确认超时，请重新生成或改用快速规划。"
    if "reached maximum number of turns" in normalized:
        return (
            "agent_turn_limit_exceeded",
            "解析任务达到了旧版步骤上限，请重新发起；更新后的任务不再限制固定轮次。",
        )
    if "--dangerously-skip-permissions" in normalized and any(
        marker in normalized for marker in ("root", "sudo privileges")
    ):
        return "parser_runtime_failed", "知识解析容器权限配置异常，请联系管理员。"
    if any(marker in normalized for marker in ("is not defined", "enoent", "no such file or directory")):
        return "parser_runtime_failed", "知识解析环境初始化失败，请联系管理员。"
    if any(
        marker in normalized
        for marker in (
            "cc-switch command failed",
            "cc-switch protocol adapter",
            "protocol adapter failed",
        )
    ):
        return "protocol_adapter_failed", "模型协议转换代理启动失败，请稍后重试。"
    if any(marker in normalized for marker in ("credit balance", "insufficient quota", "quota exceeded")):
        return "quota_exceeded", "解析模型额度或余额不足，请检查供应商账户。"
    if any(
        marker in normalized
        for marker in (
            "401",
            "invalid api key",
            "invalid_api_key",
            "authentication failed",
            "unauthorized",
        )
    ):
        return "authentication_failed", "解析模型认证失败，请检查供应商凭据。"
    if any(
        marker in normalized
        for marker in (
            "model_not_found",
            "model not found",
            "model does not exist",
            "model is not available",
        )
    ) or ("model " in normalized and "does not exist" in normalized):
        return "model_unavailable", "绑定的解析模型不存在或当前不可用。"
    if any(marker in normalized for marker in ("429", "rate limit", "too many requests")):
        return "rate_limited", "解析模型请求过于频繁，请稍后重试。"
    if any(
        marker in normalized
        for marker in (
            "memory overloaded",
            "system_memory_overloaded",
            "service unavailable",
            "503",
            "overloaded",
        )
    ):
        return "upstream_overloaded", "解析模型服务当前负载过高，请稍后重试。"
    if any(marker in normalized for marker in ("timed out", "timeout", "deadline exceeded")):
        return "request_timeout", "解析模型请求超时，请重试或调整文档规模。"
    if (
        ("response exceeded" in normalized and "output token maximum" in normalized)
        or "max_output_tokens" in normalized
        or "max_tokens is too large" in normalized
        or ("supports at most" in normalized and "completion tokens" in normalized)
    ):
        return (
            "model_output_limit_exceeded",
            "解析模型输出达到配置上限，请提高最大输出 Tokens 或选择输出能力更强的模型后重试。",
        )
    if "did not generate a complete manifest.json and main.md" in normalized:
        return (
            "invalid_parser_output",
            "模型已完成分析，但没有生成有效的记忆卡片。请重试或选择工具调用能力更强的模型。",
        )
    if "did not generate a complete plan.json" in normalized or "invalid parse plan" in normalized:
        return (
            "invalid_parse_plan",
            "模型已完成分析，但没有生成有效的解析方案。请重试或更换模型。",
        )
    if "extra data:" in normalized or "jsondecodeerror" in normalized:
        return (
            "invalid_parse_plan",
            "模型已完成分析，但解析方案格式不完整。请重试或更换模型。",
        )
    if "no markdown source documents were found" in normalized:
        return (
            "invalid_source_documents",
            "解析器没有找到可读取的 Markdown 原文，请重新上传后重试。",
        )
    return "parser_failed", "知识文档整理失败，请查看模型配置后重试。"


_SAFE_STEP_KINDS = {"tool", "model", "retry", "context"}
_SAFE_STEP_STATUSES = {"running", "success", "failed", "retrying"}
_SAFE_TOOL_NAMES = {
    "Read",
    "Glob",
    "Grep",
    "Write",
    "Edit",
    "StructuredOutput",
    "SaveSourceAnalysis",
    "SavePlanCandidate",
    "FinalizePlanSet",
    "GetPlanCandidate",
}


def _bounded_int(value: object, *, minimum: int, maximum: int) -> int | None:
    try:
        return max(minimum, min(maximum, round(float(value))))
    except (TypeError, ValueError):
        return None


def parser_progress_payload(event: dict) -> dict[str, object]:
    """Keep progress useful across reconnects without leaking document/tool data."""
    payload: dict[str, object] = {
        "type": str(event.get("type") or "progress")[:32],
        "progress": _bounded_int(event.get("progress"), minimum=0, maximum=100) or 0,
        "stage": str(event.get("stage") or "parsing")[:64],
        "message": str(event.get("message") or "")[:500],
    }
    if payload["type"] != "step":
        return payload

    step_kind = str(event.get("step_kind") or "")
    step_status = str(event.get("step_status") or "")
    if step_kind not in _SAFE_STEP_KINDS or step_status not in _SAFE_STEP_STATUSES:
        return payload
    payload.update(
        {
            "step_id": str(event.get("step_id") or "step")[:128],
            "step_kind": step_kind,
            "step_status": step_status,
        }
    )
    tool_name = str(event.get("tool_name") or "")
    if step_kind == "tool" and tool_name in _SAFE_TOOL_NAMES:
        payload["tool_name"] = tool_name
        step_detail = " ".join(str(event.get("step_detail") or "").split())[:500]
        if step_detail.startswith(
            ("input/documents/", "input/selected-plan.json", "output/"),
        ):
            payload["step_detail"] = step_detail
    for field, maximum in (
        ("elapsed_seconds", 86400),
        ("attempt", 100),
        ("max_retries", 100),
        ("retry_delay_ms", 3600000),
    ):
        value = _bounded_int(event.get(field), minimum=0, maximum=maximum)
        if value is not None:
            payload[field] = value
    return payload


def _update_run(
    run_id: uuid.UUID,
    *,
    status: str | None = None,
    progress: int | None = None,
    stage: str | None = None,
    message: str | None = None,
    container_id: str | None = None,
    error: str | None = None,
) -> bool:
    with get_db_session() as db:
        run = db.get(models.KnowledgeParseRun, run_id)
        if run is None:
            return False
        if run.status == models.KnowledgeParseStatus.CANCELLED.value:
            return False
        if status is not None:
            run.status = status
        if progress is not None:
            run.progress = max(0, min(100, progress))
        if stage is not None:
            run.stage = stage[:64]
        if message is not None:
            run.message = message[:500]
        if container_id is not None:
            run.container_id = container_id
        if error is not None:
            run.error = error
        run.updated_time = datetime.now(UTC)
        db.add(run)
        db.commit()
        return True


def _progress(run_id: uuid.UUID, event: dict) -> None:
    event_payload = parser_progress_payload(event)
    progress = int(event_payload["progress"])
    stage = str(event_payload["stage"])
    message = str(event_payload["message"])
    updated = _update_run(
        run_id,
        status=models.KnowledgeParseStatus.RUNNING.value,
        progress=progress,
        stage=stage,
        message=message,
    )
    if not updated:
        return
    push_knowledge_parse_event(run_id, event_payload)


def source_status_after_run_failure(run: object, source: object) -> str:
    if getattr(run, "parse_mode", None) == "refine" and getattr(source, "active_parse_run_id", None):
        return models.KnowledgeParseStatus.READY.value
    return models.KnowledgeParseStatus.FAILED.value


def prepare_parser_event_stream(work_dir: Path) -> None:
    """Start a fresh local relay file while Redis keeps cross-phase history."""
    events_path = work_dir / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    os.chmod(events_path, 0o666)


def _fail(
    run_id: uuid.UUID,
    source_id: uuid.UUID,
    error_code: str,
    message: str,
) -> None:
    message = message[:4000]
    with get_db_session() as db:
        run = db.get(models.KnowledgeParseRun, run_id)
        source = db.get(models.KnowledgeSource, source_id)
        if run and run.status == models.KnowledgeParseStatus.CANCELLED.value:
            return
        if run:
            run.status = models.KnowledgeParseStatus.FAILED.value
            run.stage = "failed"
            run.message = message[:500]
            run.error_code = error_code
            run.error = message
            run.updated_time = datetime.now(UTC)
            db.add(run)
        if (
            source
            and run
            and source.source_revision == run.source_revision
            and source.parse_status
            in {
                models.KnowledgeParseStatus.PENDING.value,
                models.KnowledgeParseStatus.RUNNING.value,
            }
        ):
            source.parse_status = source_status_after_run_failure(run, source)
            source.last_error_code = error_code
            source.last_error = message
            source.updated_time = datetime.now(UTC)
            db.add(source)
        db.commit()
    push_knowledge_parse_event(
        run_id,
        {
            "type": "error",
            "progress": 100,
            "stage": "failed",
            "error_code": error_code,
            "message": message,
        },
    )


def _persist_parser_output(
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    work_dir: Path,
) -> str:
    manifest_path = work_dir / "output" / "manifest.json"
    manifest = knowledge_service.validate_parser_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    output_root = (work_dir / "output").resolve()

    with get_db_session() as db:
        run = db.get(models.KnowledgeParseRun, run_id)
        if run is None:
            raise RuntimeError("解析任务不存在")
        if run.status == models.KnowledgeParseStatus.CANCELLED.value:
            return "cancelled"
        source = db.get(models.KnowledgeSource, run.source_id)
        if source is None or source.user_id != user_id:
            raise RuntimeError("知识源不存在或无权访问")
        if source.source_revision != run.source_revision:
            run.status = models.KnowledgeParseStatus.STALE.value
            run.progress = 100
            run.stage = "stale"
            run.message = "原文已更新，本次解析结果未激活"
            source.parse_status = models.KnowledgeParseStatus.STALE.value
            db.add(run)
            db.add(source)
            db.commit()
            return "stale"

        items = manifest.documents
        if should_enforce_plan_document_count(run):
            plan = db.get(models.KnowledgeParsePlan, run.plan_id)
            if plan is None:
                raise ValueError("选定的解析方案不存在")
            payload = knowledge_service._load_plan_payload(plan)
            strategy = next(
                (item for item in payload.strategies if item.id == run.plan_strategy_id),
                None,
            )
            if strategy is None or strategy.document_count != len(items):
                raise ValueError("解析结果数量与用户确认的方案不一致")
        documents: list[models.KnowledgeDocument] = []
        uploaded_keys: list[str] = []
        try:
            output_items: list[tuple[str, bytes]] = []
            if items:
                for item in items:
                    path = (output_root / item.path).resolve()
                    if not path.is_relative_to(output_root) or not path.is_file():
                        raise ValueError(f"解析结果文件不存在: {item.path}")
                    output_items.append((item.title, path.read_bytes()))
            else:
                # An older parser image may still finish with the previous
                # card contract during a rolling deployment. Preserve its
                # content as ordinary editable blocks; never insert it into
                # memory from this document-organization flow.
                output_items = [
                    (card.title, card.content.encode("utf-8"))
                    for card in manifest.cards
                ]

            for order, (title, data) in enumerate(output_items):
                content = knowledge_service._decode_markdown(data)
                document_id = uuid.uuid4()
                key = knowledge_service._document_key(user_id, source.id, run.id, document_id, 1)
                storage.upload(key, data, content_type="text/markdown; charset=utf-8")
                uploaded_keys.append(key)
                documents.append(
                    models.KnowledgeDocument(
                        id=document_id,
                        source_id=source.id,
                        parse_run_id=run.id,
                        parent_id=None,
                        document_type=models.KnowledgeDocumentType.DOCUMENT.value,
                        title=title,
                        object_key=key,
                        content_version=1,
                        content_hash=knowledge_service._digest(data),
                        content_size=len(data),
                        estimated_tokens=knowledge_service.estimate_knowledge_tokens(content),
                        sort_order=order,
                    )
                )
            manifest_key = f"knowledge/{user_id}/{source.id}/parses/{run.id}/manifest.json"
            storage.upload(
                manifest_key,
                json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2).encode(),
                content_type="application/json",
            )
            uploaded_keys.append(manifest_key)
            for document in documents:
                db.add(document)
            run.status = models.KnowledgeParseStatus.READY.value
            run.progress = 100
            run.stage = "review"
            run.message = f"已整理 {len(documents)} 个预提取文档块，可继续编辑"
            run.manifest_object_key = manifest_key
            run.generated_memory_ids = []
            run.error_code = None
            run.error = None
            source.active_parse_run_id = run.id
            source.parse_status = models.KnowledgeParseStatus.READY.value
            source.last_error_code = None
            source.last_error = None
            source.updated_time = datetime.now(UTC)
            db.add(run)
            db.add(source)
            db.commit()
        except Exception:
            db.rollback()
            storage.delete_many(uploaded_keys)
            raise
    return "ready"


def _consume_context(job_id: uuid.UUID) -> dict[str, str]:
    raw = pop_knowledge_parse_context(job_id)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return {str(key): str(value or "") for key, value in payload.items()}
    except json.JSONDecodeError:
        pass
    return {"background": raw}


def _update_plan(
    plan_id: uuid.UUID,
    *,
    status: str | None = None,
    progress: int | None = None,
    stage: str | None = None,
    message: str | None = None,
    container_id: str | None = None,
) -> bool:
    with get_db_session() as db:
        plan = db.get(models.KnowledgeParsePlan, plan_id)
        if plan is None:
            return False
        if plan.status == models.KnowledgeParseStatus.CANCELLED.value:
            return False
        if status is not None:
            plan.status = status
        if progress is not None:
            plan.progress = max(0, min(100, progress))
        if stage is not None:
            plan.stage = stage[:64]
        if message is not None:
            plan.message = message[:500]
        if container_id is not None:
            plan.container_id = container_id
        plan.updated_time = datetime.now(UTC)
        db.add(plan)
        db.commit()
        return True


def _plan_progress(plan_id: uuid.UUID, event: dict) -> None:
    event_payload = parser_progress_payload(event)
    progress = int(event_payload["progress"])
    stage = str(event_payload["stage"])
    message = str(event_payload["message"])
    event_type = str(event_payload["type"])
    pending_question: dict | None = None
    if event_type == "question":
        pending_question = knowledge_schemas.KnowledgeParsePlanPendingQuestion.model_validate(
            {
                "question_id": event.get("question_id"),
                "questions": event.get("questions"),
            }
        ).model_dump()
    with get_db_session() as db:
        plan = db.get(models.KnowledgeParsePlan, plan_id)
        if plan is None or plan.status == models.KnowledgeParseStatus.CANCELLED.value:
            return
        plan.status = models.KnowledgeParseStatus.RUNNING.value
        plan.progress = max(plan.progress, max(0, min(100, progress)))
        plan.stage = stage[:64]
        plan.message = message[:500]
        if pending_question is not None:
            plan.pending_question = pending_question
        elif event_type == "resume":
            plan.pending_question = None
        plan.updated_time = datetime.now(UTC)
        db.add(plan)
        db.commit()
    if pending_question is not None:
        event_payload.update(pending_question)
    push_knowledge_parse_event(
        plan_id,
        event_payload,
    )


def _fail_plan(
    plan_id: uuid.UUID,
    error_code: str,
    message: str,
    *,
    stage: str = "failed",
    progress: int = 100,
) -> None:
    with get_db_session() as db:
        plan = db.get(models.KnowledgeParsePlan, plan_id)
        if plan:
            if plan.status == models.KnowledgeParseStatus.CANCELLED.value:
                return
            plan.status = models.KnowledgeParseStatus.FAILED.value
            plan.progress = max(0, min(100, progress))
            plan.stage = stage[:64]
            plan.message = message[:500]
            plan.error_code = error_code
            plan.error = message[:4000]
            plan.pending_question = None
            plan.updated_time = datetime.now(UTC)
            db.add(plan)
            db.commit()
    push_knowledge_parse_event(
        plan_id,
        {
            "type": "error",
            "progress": max(0, min(100, progress)),
            "stage": stage,
            "error_code": error_code,
            "message": message,
        },
    )


def _parse_plan_cancelled(plan_id: uuid.UUID) -> bool:
    with get_db_session() as db:
        plan = db.get(models.KnowledgeParsePlan, plan_id)
        return plan is None or plan.status == models.KnowledgeParseStatus.CANCELLED.value


def _parse_run_cancelled(run_id: uuid.UUID) -> bool:
    with get_db_session() as db:
        run = db.get(models.KnowledgeParseRun, run_id)
        return run is None or run.status == models.KnowledgeParseStatus.CANCELLED.value


def _write_source_snapshot(work_dir: Path, source_snapshot: list[dict]) -> Path:
    input_documents = work_dir / "input" / "documents"
    input_documents.mkdir(parents=True, exist_ok=True)
    (work_dir / "output").mkdir(parents=True, exist_ok=True)
    for order, item in enumerate(source_snapshot):
        object_key = str(item.get("object_key") or "")
        if not object_key:
            raise RuntimeError("原始文档快照不完整")
        filename = Path(str(item.get("filename") or "document.md")).name
        filename = (
            "".join(character if character.isalnum() or character in "._- " else "_" for character in filename)
            or "document.md"
        )
        (input_documents / f"{order + 1:03d}-{filename}").write_bytes(storage.download(object_key))
    return input_documents


class ParseRunWorkspaceRef(Protocol):
    id: object
    session_owner_id: object | None
    session_owner_kind: object | None


def should_preserve_parser_workspace(work_dir: Path) -> bool:
    """Keep resumable SDK sessions, including completed runs that can be refined."""
    return (work_dir / ".parser-runtime" / "session-id").is_file()


def parse_run_workspace(user_id: uuid.UUID, run: ParseRunWorkspaceRef) -> Path:
    """Resolve the stable workspace shared by planning, parsing, and refinements."""
    run_id = uuid.UUID(str(run.id))
    owner_id = run.session_owner_id or run_id
    owner_kind = run.session_owner_kind or "run"
    directory = "knowledge_plan" if owner_kind == "plan" else "knowledge_parse"
    return Path(settings.DOCKER_PROJECT_HOME) / f"code_user-{user_id}" / directory / str(owner_id)


def should_enforce_plan_document_count(run: object) -> bool:
    return (
        getattr(run, "parse_mode", None) != "refine"
        and bool(getattr(run, "plan_id", None))
        and bool(getattr(run, "plan_strategy_id", None))
    )


def _make_container_spec(
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    work_dir: Path,
    proxy_token: str,
    model: str,
    upstream_api_format: str,
    context_window_tokens: int | None,
    max_output_tokens: int | None,
    job_mode: str,
    interaction_mode: str = "quick",
) -> ContainerJobSpec:
    return ContainerJobSpec(
        name=f"llm4ad-knowledge-{job_id.hex[:16]}",
        image=settings.KNOWLEDGE_PARSER_IMAGE,
        command=["python", "/app/knowledge-parser/runner.py"],
        user="65534:65534",
        mounts={resolve_host_path(str(work_dir)): _CONTAINER_WORKSPACE},
        env={
            "LLM4AD_UPSTREAM_BASE_URL": settings.LLM_PROXY_BASE_URL.rstrip("/"),
            "LLM4AD_UPSTREAM_API_KEY": proxy_token,
            "LLM4AD_UPSTREAM_MODEL": model,
            "LLM4AD_UPSTREAM_API_FORMAT": upstream_api_format,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(effective_parser_output_tokens(max_output_tokens, job_mode=job_mode)),
            "KNOWLEDGE_MODEL_CONTEXT_TOKENS": str(
                max(1, context_window_tokens or models.DEFAULT_KNOWLEDGE_CONTEXT_WINDOW_TOKENS)
            ),
            "KNOWLEDGE_JOB_MODE": job_mode,
            "KNOWLEDGE_PLAN_INTERACTION_MODE": interaction_mode,
            "KNOWLEDGE_PLAN_QUESTION_TIMEOUT": str(max(1, settings.KNOWLEDGE_PLAN_QUESTION_TIMEOUT)),
        },
        mem_limit=settings.KNOWLEDGE_PARSER_MEMORY_LIMIT,
        nano_cpus=int(settings.KNOWLEDGE_PARSER_CPU_LIMIT * 1e9),
        timeout=settings.KNOWLEDGE_PARSER_TIMEOUT,
        events_file=str(work_dir / "events.jsonl"),
        labels={"knowledge_job_id": str(job_id), "user_id": str(user_id), "job_mode": job_mode},
    )


def parse_plan_candidate_text(raw: str) -> knowledge_schemas.KnowledgeParsePlanPayload:
    """Recover the largest complete JSON object, then validate the plan contract."""
    decoder = json.JSONDecoder()
    best: dict | None = None
    best_length = 0
    for start, character in enumerate(raw):
        if character != "{":
            continue
        try:
            candidate, end = decoder.raw_decode(raw[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and end > best_length:
            best = candidate
            best_length = end
    if best is None:
        raise KnowledgeParserFailure(
            "invalid_parse_plan",
            "模型已完成分析，但没有生成有效的解析方案。请重试或更换模型。",
        )
    try:
        return knowledge_service.validate_parse_plan_payload(best)
    except ValueError as exc:
        raise KnowledgeParserFailure(
            "invalid_parse_plan",
            "模型已完成分析，但解析方案字段不完整。请重试或更换模型。",
        ) from exc


def _checkpoint_plan_output(
    plan_id: uuid.UUID,
    user_id: uuid.UUID,
    work_dir: Path,
) -> knowledge_schemas.KnowledgeParsePlanPayload | None:
    plan_path = work_dir / "output" / "plan.json"
    raw = plan_path.read_text(encoding="utf-8")
    with get_db_session() as db:
        plan = db.get(models.KnowledgeParsePlan, plan_id)
        if plan is None:
            raise RuntimeError("解析方案不存在")
        if plan.status == models.KnowledgeParseStatus.CANCELLED.value:
            return None
        source = db.get(models.KnowledgeSource, plan.source_id)
        if source is None or source.user_id != user_id:
            raise RuntimeError("知识源不存在或无权访问")
        candidate_key = knowledge_service._plan_candidate_key(user_id, source.id, plan.id)
        try:
            storage.upload(
                candidate_key,
                raw.encode("utf-8"),
                content_type="text/plain; charset=utf-8",
            )
        except Exception as exc:
            raise KnowledgeParserFailure(
                "plan_checkpoint_failed",
                "解析方案已经生成，但检查点暂时无法保存。请稍后继续保存。",
            ) from exc
        payload = parse_plan_candidate_text(raw)
        key = knowledge_service._plan_key(user_id, source.id, plan.id)
        data = json.dumps(payload.model_dump(), ensure_ascii=False, indent=2).encode("utf-8")
        try:
            storage.upload(key, data, content_type="application/json")
            plan.plan_object_key = key
            plan.progress = 92
            plan.stage = "persisting"
            plan.message = "解析方案检查点已保存，正在完成入库"
            plan.updated_time = datetime.now(UTC)
            db.add(plan)
            db.commit()
        except Exception as exc:
            raise KnowledgeParserFailure(
                "plan_checkpoint_failed",
                "解析方案已经生成，但检查点暂时无法保存。请稍后继续保存。",
            ) from exc
    return payload


def _persist_plan_output(plan_id: uuid.UUID, user_id: uuid.UUID, work_dir: Path) -> str:
    _checkpoint_plan_output(plan_id, user_id, work_dir)
    return retry_plan_persistence(plan_id, user_id)


def retry_plan_persistence(plan_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Finish plan activation from a local or RustFS checkpoint without a model call."""
    work_dir = Path(settings.DOCKER_PROJECT_HOME) / f"code_user-{user_id}" / "knowledge_plan" / str(plan_id)
    plan_path = work_dir / "output" / "plan.json"
    with get_db_session() as db:
        plan = db.get(models.KnowledgeParsePlan, plan_id)
        if plan is None:
            raise RuntimeError("解析方案不存在")
        if plan.status == models.KnowledgeParseStatus.CANCELLED.value:
            return "cancelled"
        source = db.get(models.KnowledgeSource, plan.source_id)
        if source is None or source.user_id != user_id:
            raise RuntimeError("知识源不存在或无权访问")
        key = plan.plan_object_key or knowledge_service._plan_key(user_id, source.id, plan.id)
        try:
            raw = storage.download(key).decode("utf-8")
        except Exception:
            if not plan_path.is_file():
                raise
            raw = plan_path.read_text(encoding="utf-8")
        payload = parse_plan_candidate_text(raw)
        if not plan.plan_object_key:
            data = json.dumps(payload.model_dump(), ensure_ascii=False, indent=2).encode("utf-8")
            storage.upload(key, data, content_type="application/json")
            plan.plan_object_key = key
            db.add(plan)
            db.commit()
        try:
            final_status = knowledge_service.activate_parse_plan_payload(db, plan, source, payload)
        except Exception as exc:
            raise KnowledgeParserFailure(
                "plan_persist_failed",
                "解析方案检查点已保留，但入库暂未完成。请稍后继续保存。",
            ) from exc
    return final_status


@celery_app.task(name="knowledge.plan", bind=True, max_retries=0)
def run_knowledge_parse_plan(
    _task,
    plan_id_raw: str,
    user_id_raw: str,
    proxy_token: str,
    model: str,
    upstream_api_format: str,
    max_output_tokens: int | None = None,
    credential_task_id_raw: str | None = None,
    context_window_tokens: int | None = None,
) -> None:
    plan_id = uuid.UUID(plan_id_raw)
    user_id = uuid.UUID(user_id_raw)
    work_dir = Path(settings.DOCKER_PROJECT_HOME) / f"code_user-{user_id}" / "knowledge_plan" / str(plan_id)
    preserve_work_dir = False
    credential_task_id = credential_task_id_raw or plan_id_raw
    try:
        context = _consume_context(plan_id)
        with get_db_session() as db:
            plan = db.get(models.KnowledgeParsePlan, plan_id)
            if plan is None:
                raise RuntimeError("解析方案不存在")
            if plan.status == models.KnowledgeParseStatus.CANCELLED.value:
                return
            source = db.get(models.KnowledgeSource, plan.source_id)
            if source is None or source.user_id != user_id:
                raise RuntimeError("知识源不存在或无权访问")
            source_snapshot = list(plan.source_snapshot or [])
            interaction_mode = plan.interaction_mode or "quick"
            if not source_snapshot:
                raise RuntimeError("知识主题中没有可规划的原始文档")
            plan.status = models.KnowledgeParseStatus.RUNNING.value
            plan.stage = "preparing"
            plan.progress = 5
            plan.message = "正在准备解析方案生成环境"
            db.add(plan)
            db.commit()

        input_documents = _write_source_snapshot(work_dir, source_snapshot)
        control_dir = work_dir / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        if context.get("background"):
            (work_dir / "input" / "background.txt").write_text(context["background"], encoding="utf-8")
        for path in (
            work_dir,
            work_dir / "input",
            input_documents,
            work_dir / "output",
            control_dir,
        ):
            os.chmod(path, 0o777)
        _plan_progress(plan_id, {"progress": 8, "stage": "prepared", "message": "文档已载入，正在规划"})
        prepare_parser_event_stream(work_dir)

        parser_error: dict[str, str] = {}

        def handle_event(event: dict) -> None:
            if str(event.get("type") or "") == "error":
                parser_error["raw"] = str(event.get("message") or "")
            else:
                _plan_progress(plan_id, event)

        callbacks = ContainerJobCallbacks(
            on_started=lambda handle: _update_plan(plan_id, container_id=handle.container_id),
            on_event=handle_event,
            on_stdout=lambda line: logger.info("knowledge plan {}: {}", plan_id, line[:1000]),
            check_cancelled=lambda: _parse_plan_cancelled(plan_id),
        )
        result = ContainerJob(
            _make_container_spec(
                job_id=plan_id,
                user_id=user_id,
                work_dir=work_dir,
                proxy_token=proxy_token,
                model=model,
                upstream_api_format=upstream_api_format,
                context_window_tokens=context_window_tokens,
                max_output_tokens=max_output_tokens,
                job_mode="plan",
                interaction_mode=interaction_mode,
            ),
            callbacks,
        ).run()
        if result.status == ContainerJobStatus.CANCELLED or _parse_plan_cancelled(plan_id):
            return
        if result.status != ContainerJobStatus.COMPLETED or result.exit_code != 0:
            raw_error = parser_error.get("raw") or result.error or ""
            code, message = (
                ("request_timeout", "解析方案生成超时，请重试或调整文档规模。")
                if result.status == ContainerJobStatus.TIMED_OUT
                else classify_parser_failure(raw_error)
            )
            raise KnowledgeParserFailure(code, message)
        _plan_progress(plan_id, {"progress": 90, "stage": "verifying", "message": "正在校验并保存方案检查点"})
        if _parse_plan_cancelled(plan_id):
            return
        checkpoint = _checkpoint_plan_output(plan_id, user_id, work_dir)
        if checkpoint is None or _parse_plan_cancelled(plan_id):
            return
        _plan_progress(plan_id, {"progress": 92, "stage": "persisting", "message": "检查点已保存，正在完成方案入库"})
        final_status = retry_plan_persistence(plan_id, user_id)
        if final_status == "cancelled":
            return
        push_knowledge_parse_event(
            plan_id,
            {
                "type": "stale" if final_status == "stale" else "done",
                "progress": 100,
                "stage": final_status,
                "message": "原文已更新，方案仅供查看" if final_status == "stale" else "解析方案已生成",
            },
        )
    except Exception as exc:  # noqa: BLE001
        if _parse_plan_cancelled(plan_id):
            logger.info("Knowledge parse plan cancelled: plan_id={}", plan_id)
            return
        logger.exception("Knowledge parse plan failed: plan_id={}", plan_id)
        code, message = (
            (exc.code, exc.message) if isinstance(exc, KnowledgeParserFailure) else classify_parser_failure(str(exc))
        )
        failure_stage, failure_progress = {
            "invalid_parse_plan": ("validation_failed", 90),
            "plan_checkpoint_failed": ("checkpoint_failed", 90),
            "plan_persist_failed": ("persist_failed", 92),
        }.get(code, ("failed", 100))
        preserve_work_dir = code == "plan_checkpoint_failed"
        _fail_plan(
            plan_id,
            code,
            message,
            stage=failure_stage,
            progress=failure_progress,
        )
        raise
    finally:
        delete_knowledge_parse_context(plan_id)
        credential_broker.revoke_task_tokens(credential_task_id)
        preserve_work_dir = preserve_work_dir or should_preserve_parser_workspace(work_dir)
        if not preserve_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


@celery_app.task(name="knowledge.parse", bind=True, max_retries=0)
def run_knowledge_parse(
    _task,
    run_id_raw: str,
    user_id_raw: str,
    proxy_token: str,
    model: str,
    upstream_api_format: str,
    max_output_tokens: int | None = None,
    credential_task_id_raw: str | None = None,
    context_window_tokens: int | None = None,
) -> None:
    run_id = uuid.UUID(run_id_raw)
    user_id = uuid.UUID(user_id_raw)
    source_id: uuid.UUID | None = None
    work_dir = Path(settings.DOCKER_PROJECT_HOME) / f"code_user-{user_id}" / "knowledge_parse" / str(run_id)
    parser_job_mode = "execute"
    credential_task_id = credential_task_id_raw or run_id_raw
    try:
        context = _consume_context(run_id)
        selected_plan: dict | None = None
        with get_db_session() as db:
            run = db.get(models.KnowledgeParseRun, run_id)
            if run is None:
                raise RuntimeError("解析任务不存在")
            if run.status == models.KnowledgeParseStatus.CANCELLED.value:
                return
            work_dir = parse_run_workspace(user_id, run)
            parser_job_mode = "refine" if run.parse_mode == "refine" else "execute"
            source = db.get(models.KnowledgeSource, run.source_id)
            if source is None or source.user_id != user_id:
                raise RuntimeError("知识源不存在或无权访问")
            source_id = source.id
            source_snapshot = list(run.source_snapshot or [])
            if not source_snapshot:
                raise RuntimeError("知识主题中没有可解析的原始文档")
            if run.plan_id and run.plan_strategy_id:
                plan = db.get(models.KnowledgeParsePlan, run.plan_id)
                if (
                    plan is None
                    or plan.source_id != source.id
                    or plan.source_revision != run.source_revision
                    or plan.status != models.KnowledgeParseStatus.READY.value
                ):
                    raise RuntimeError("选定的解析方案已过期")
                payload = knowledge_service._load_plan_payload(plan)
                strategy = next(
                    (item for item in payload.strategies if item.id == run.plan_strategy_id),
                    None,
                )
                if strategy is None:
                    raise RuntimeError("选定的解析策略不存在")
                selected_plan = {
                    "plan_id": str(plan.id),
                    "topic_summary": payload.topic_summary,
                    "selected_strategy": strategy.model_dump(),
                    "user_adjustment": context.get("adjustment", ""),
                }
            source.parse_status = models.KnowledgeParseStatus.RUNNING.value
            run.status = models.KnowledgeParseStatus.RUNNING.value
            run.stage = "preparing"
            run.progress = 5
            run.message = (
                "正在恢复会话并准备优化文档"
                if parser_job_mode == "refine"
                else "正在准备文档块整理环境"
            )
            db.add(source)
            db.add(run)
            db.commit()

        input_documents = _write_source_snapshot(work_dir, source_snapshot)
        if context.get("background"):
            (work_dir / "input" / "background.txt").write_text(
                context["background"],
                encoding="utf-8",
            )
        if context.get("instruction"):
            (work_dir / "input" / "instruction.txt").write_text(
                context["instruction"],
                encoding="utf-8",
            )
        if context.get("refinement"):
            (work_dir / "input" / "refinement.txt").write_text(
                context["refinement"],
                encoding="utf-8",
            )
        if selected_plan:
            (work_dir / "input" / "selected-plan.json").write_text(
                json.dumps(selected_plan, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        # Ephemeral directory only; the parser image runs as an unprivileged uid.
        os.chmod(work_dir, 0o777)
        os.chmod(work_dir / "input", 0o777)
        os.chmod(input_documents, 0o777)
        os.chmod(work_dir / "output", 0o777)
        _progress(
            run_id,
            {"progress": 8, "stage": "prepared", "message": "文档已载入，准备分析"},
        )
        prepare_parser_event_stream(work_dir)

        parser_error: dict[str, str] = {}

        def handle_event(event: dict) -> None:
            if str(event.get("type") or "") == "error":
                parser_error["raw"] = str(event.get("message") or "")
                return
            _progress(run_id, event)

        callbacks = ContainerJobCallbacks(
            on_started=lambda handle: _update_run(run_id, container_id=handle.container_id),
            on_event=handle_event,
            on_stdout=lambda line: logger.info("knowledge parser {}: {}", run_id, line[:1000]),
            check_cancelled=lambda: _parse_run_cancelled(run_id),
        )
        spec = _make_container_spec(
            job_id=run_id,
            user_id=user_id,
            work_dir=work_dir,
            proxy_token=proxy_token,
            model=model,
            upstream_api_format=upstream_api_format,
            context_window_tokens=context_window_tokens,
            max_output_tokens=max_output_tokens,
            job_mode=parser_job_mode,
        )
        result = ContainerJob(spec, callbacks).run()
        if result.status == ContainerJobStatus.CANCELLED or _parse_run_cancelled(run_id):
            return
        if result.status != ContainerJobStatus.COMPLETED or result.exit_code != 0:
            raw_error = parser_error.get("raw") or result.error or ""
            if result.status == ContainerJobStatus.TIMED_OUT:
                code, message = (
                    "request_timeout",
                    "解析模型请求超时，请重试或调整文档规模。",
                )
            else:
                code, message = classify_parser_failure(raw_error)
            raise KnowledgeParserFailure(code, message)
        _progress(
            run_id,
            {"progress": 92, "stage": "persisting", "message": "正在保存预提取文档块"},
        )
        if _parse_run_cancelled(run_id):
            return
        final_status = _persist_parser_output(run_id, user_id, work_dir)
        if final_status == "cancelled":
            return
        if final_status == "stale":
            push_knowledge_parse_event(
                run_id,
                {
                    "type": "stale",
                    "progress": 100,
                    "stage": "stale",
                    "message": "原文已更新，本次解析结果未激活",
                },
            )
        else:
            push_knowledge_parse_event(
                run_id,
                {
                    "type": "done",
                    "progress": 100,
                    "stage": "review",
                    "message": "预提取文档块已生成，可继续查看和编辑",
                },
            )
    except Exception as exc:  # noqa: BLE001
        if _parse_run_cancelled(run_id):
            logger.info("Knowledge parse cancelled: run_id={}", run_id)
            return
        logger.exception("Knowledge parse failed: run_id={}", run_id)
        if source_id is not None:
            if isinstance(exc, KnowledgeParserFailure):
                error_code, message = exc.code, exc.message
            else:
                error_code, message = classify_parser_failure(str(exc))
            _fail(run_id, source_id, error_code, message)
        raise
    finally:
        delete_knowledge_parse_context(run_id)
        credential_broker.revoke_task_tokens(credential_task_id)
        if not should_preserve_parser_workspace(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


@celery_app.task(
    name="knowledge.cleanup",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def run_knowledge_cleanup(_task, cleanup_job_id_raw: str) -> None:
    """Retry external knowledge cleanup from its durable PostgreSQL outbox row."""
    cleanup_job_id = uuid.UUID(cleanup_job_id_raw)
    with get_db_session() as db:
        knowledge_cleanup.run_cleanup_job(db, cleanup_job_id)
