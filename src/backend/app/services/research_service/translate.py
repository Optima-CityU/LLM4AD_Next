"""产物翻译（Translate）子域：把单个产物文件译成目标语言，带磁盘缓存 + SSE。

- 路径解析复用 :func:`resolve_artifact_path`（归属校验 + 防穿越）。
- 译文按源文件 sha256 键控，落盘 ``run_dir/.translations/``（dot 前缀天然被
  产物列表 / zip 排除）；源文件没变直接回读缓存，``force=True`` 跳过并覆盖。
- 未命中缓存时后台协程流式调 LLM、推 Redis Stream（复用 chat 报告基建），
  前端连 ``/artifacts/translate/stream?source_hash=...`` 接收增量。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from loguru import logger
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
    ResearchArtifactTranslateRequest,
    ResearchArtifactTranslateResponse,
    ResearchArtifactTranslateStopResponse,
)

from ._common import _get_session
from .analysis import _resolve_provider_config
from .artifacts import resolve_artifact_path

# 缓存目录（dot 前缀 → 被 list_artifacts / archive / tree 的 "." 过滤天然排除）。
_CACHE_DIRNAME = ".translations"
# 持有后台翻译任务引用，避免 fire-and-forget task 被 GC 中途回收。
_BG_TASKS: set[asyncio.Task[None]] = set()
# 单次翻译喂给 LLM 的源文本最大字符数，防超长。
_MAX_SOURCE_CHARS = 100_000


def _report_type(source_hash: str, target_language: str) -> str:
    """构造报告流的 report_type 段：按源哈希 + 目标语言键控，避免相互覆盖。"""
    return f"translate:{source_hash[:16]}:{target_language}"


def _hash_bytes(data: bytes) -> str:
    """源文件内容的 sha256 十六进制摘要（缓存键）。"""
    return hashlib.sha256(data).hexdigest()


def _cache_path(root: Path, relative: str, target_language: str) -> Path:
    """译文缓存路径：相对路径打平（``/`` → ``__``）+ 语言后缀，一文件一缓存。"""
    flat = relative.replace("\\", "/").replace("/", "__")
    return root / _CACHE_DIRNAME / f"{flat}.{target_language}.json"


def _read_cache(
    cache_file: Path, source_hash: str
) -> str | None:
    """读缓存译文：文件存在且记录的源哈希与当前一致才返回，否则 None。"""
    try:
        if not cache_file.is_file():
            return None
        obj = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("source_hash") != source_hash:
        return None  # 源文件已变，缓存失效
    content = obj.get("content")
    return content if isinstance(content, str) and content else None


def _write_cache(
    cache_file: Path,
    *,
    relative: str,
    source_hash: str,
    target_language: str,
    content: str,
) -> None:
    """把译文落盘缓存；失败仅记日志、不抛（缓存尽力而为）。"""
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "path": relative,
                    "source_hash": source_hash,
                    "target_language": target_language,
                    "content": content,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        logger.opt(exception=True).warning(f"write translation cache failed: {relative}")


def translate_artifact(
    db: Session,
    session_id: uuid.UUID,
    current_user: models.User,
    relative: str,
    request: ResearchArtifactTranslateRequest,
    access_token: str | None = None,
) -> ResearchArtifactTranslateResponse:
    """翻译单个产物文件：附着在跑任务 / 回缓存 / 后台启动流式翻译。

    Args:
        db: 数据库会话。
        session_id: 目标会话 ID。
        current_user: 当前认证用户（归属校验）。
        relative: 相对 run_dir 的产物路径。
        request: 翻译请求（target_language / provider_id / model_name / force）。
        access_token: 内置供应商 URL 占位替换用的登录 token。

    Returns:
        ``cached`` 时带 ``content``；``translating`` 时需连 SSE 按
        ``source_hash`` 拉增量。

    Raises:
        HTTPException: 会话无 run_dir、路径越界 / 不存在、源文件无法读取。
    """
    session = _get_session(db, session_id, current_user)
    target = resolve_artifact_path(db, session_id, current_user, relative)
    root = Path(session.run_dir).resolve()

    try:
        source_bytes = target.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="cannot read artifact") from exc
    source_text = source_bytes.decode("utf-8", errors="replace")
    source_hash = _hash_bytes(source_bytes)

    lang = request.target_language
    cache_file = _cache_path(root, relative, lang)
    sid = str(session_id)
    rtype = _report_type(source_hash, lang)

    # 幂等附着（须先于读缓存，否则 force 重译进行中会命中旧缓存）：非 force 且
    # 同源翻译在跑时直接返回 translating，前端凭 source_hash 重连现有流即可。
    if not request.force and get_report_generation_id(sid, rtype):
        return ResearchArtifactTranslateResponse(
            session_id=session_id,
            path=relative,
            status="translating",
            source_hash=source_hash,
            target_language=lang,
            content=None,
        )

    # 源文件没变就直接回缓存；force 跳过。
    if not request.force:
        cached = _read_cache(cache_file, source_hash)
        if cached is not None:
            return ResearchArtifactTranslateResponse(
                session_id=session_id,
                path=relative,
                status="cached",
                source_hash=source_hash,
                target_language=lang,
                content=cached,
            )

    provider_config = _resolve_provider_config(
        db, current_user, request.provider_id, request.model_name, access_token
    )
    prompt = _build_prompt(source_text, lang)

    # force 或无在跑任务：取消旧生成（若有）、删旧流，再起新任务。
    if get_report_generation_id(sid, rtype):
        push_report_chunk(sid, rtype, {"type": "cancelled"})
    delete_report_stream(sid, rtype)

    generation_id = uuid.uuid4().hex
    set_report_generation_id(sid, rtype, generation_id)

    task = asyncio.create_task(
        _run_translation(
            session_id=session_id,
            report_type=rtype,
            provider_config=provider_config,
            prompt=prompt,
            generation_id=generation_id,
            cache_file=cache_file,
            relative=relative,
            source_hash=source_hash,
            target_language=lang,
        )
    )
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)

    return ResearchArtifactTranslateResponse(
        session_id=session_id,
        path=relative,
        status="translating",
        source_hash=source_hash,
        target_language=lang,
        content=None,
    )


def get_translate_stream_type(
    db: Session,
    session_id: uuid.UUID,
    current_user: models.User,
    source_hash: str,
    target_language: str,
) -> str:
    """校验归属并返回翻译流的 ``report_type``（供 SSE 路由构造 Redis key）。"""
    _get_session(db, session_id, current_user)
    return _report_type(source_hash, target_language)


def stop_translation(
    db: Session,
    session_id: uuid.UUID,
    current_user: models.User,
    source_hash: str,
    target_language: str,
) -> ResearchArtifactTranslateStopResponse:
    """停止在跑的产物翻译：清 generation_id 让后台协程协作式退出。

    复用 :func:`_run_translation` 已有的取消机制——每推 chunk 前比对 generation_id，
    这里清掉后下一个 chunk 即 ``!= generation_id`` → 协程 return，且因 ``cancelled``
    为真不落缓存。同时推一条 ``cancelled`` 帧让仍连着的 SSE 收到终止事件。

    Args:
        db: 数据库会话。
        session_id: 目标会话 ID。
        current_user: 当前认证用户（归属校验）。
        source_hash: POST /translate 返回的源文件哈希。
        target_language: 目标语言（'zh' / 'en'）。

    Returns:
        ``stopped`` 有在跑任务被中断；``idle`` 无任务可停（幂等，重复停不报错）。
    """
    _get_session(db, session_id, current_user)
    sid = str(session_id)
    rtype = _report_type(source_hash, target_language)

    if not get_report_generation_id(sid, rtype):
        return ResearchArtifactTranslateStopResponse(
            session_id=session_id,
            source_hash=source_hash,
            target_language=target_language,
            status="idle",
        )

    # 先通知 SSE（cancelled 是终止帧），再清 generation_id 触发协程退出。
    push_report_chunk(sid, rtype, {"type": "cancelled"})
    clear_report_generation_id(sid, rtype)
    return ResearchArtifactTranslateStopResponse(
        session_id=session_id,
        source_hash=source_hash,
        target_language=target_language,
        status="stopped",
    )


async def _run_translation(
    session_id: uuid.UUID,
    report_type: str,
    provider_config: dict[str, Any],
    prompt: str,
    generation_id: str,
    cache_file: Path,
    relative: str,
    source_hash: str,
    target_language: str,
) -> None:
    """后台协程：流式拉取译文、推 SSE，完成后落盘缓存。

    每次推 chunk 前校验 generation_id；发现新生成任务已启动则协作式取消退出，
    取消 / 失败时不写缓存（只缓存完整成功的译文）。
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
            current_id = get_report_generation_id(sid, report_type)
            if current_id != generation_id:
                cancelled = True
                logger.info(
                    f"Translation cancelled: session_id={sid}, path={relative}, "
                    f"generation_id={generation_id}"
                )
                return

            content_buffer += chunk
            push_report_chunk(
                sid, report_type, {"type": "chunk", "content": chunk}
            )

        push_report_chunk(sid, report_type, {"type": "done"})
        # 只缓存完整成功的译文。
        _write_cache(
            cache_file,
            relative=relative,
            source_hash=source_hash,
            target_language=target_language,
            content=content_buffer,
        )

    except Exception as exc:
        if cancelled:
            return
        logger.exception(f"Translation failed: session_id={sid}, path={relative}")
        push_report_chunk(sid, report_type, {"type": "error", "error": str(exc)})
    finally:
        if not cancelled:
            clear_report_generation_id(sid, report_type)


_TRANSLATE_SYSTEM_ZH = """\
你是一名专业的技术翻译。用户会给你一份自动科研流水线的产物文件内容（可能是 \
Markdown、JSON、YAML、日志或纯文本）。请把其中的**自然语言文本**忠实翻译成\
简体中文。

严格遵守：
- 保留原有的文件结构与格式：Markdown 标记、JSON/YAML 的键与语法、代码块、\
表格、缩进、换行一律原样保留，只翻译其中的自然语言内容。
- 代码、变量名、函数名、路径、URL、命令、数字、专有名词与标识符保持原文不译。
- JSON/YAML 只翻译字符串**值**里的自然语言，键名保持原文。
- 不要添加解释、前言或结语，直接输出翻译后的完整内容。
- 不要用 ```markdown``` 或任何代码围栏包裹整个输出。
"""

_TRANSLATE_SYSTEM_EN = """\
You are a professional technical translator. The user will give you the \
content of an artifact file from an automated research pipeline (it may be \
Markdown, JSON, YAML, a log, or plain text). Faithfully translate the \
**natural-language text** into English.

Strict rules:
- Preserve the original file structure and formatting: keep Markdown markup, \
JSON/YAML keys and syntax, code blocks, tables, indentation, and line breaks \
exactly as-is; translate only the natural-language content.
- Leave code, variable/function names, paths, URLs, commands, numbers, proper \
nouns, and identifiers untranslated.
- For JSON/YAML, translate only natural-language text inside string **values**; \
keep keys unchanged.
- Do not add explanations, preamble, or closing remarks; output the full \
translated content directly.
- Do NOT wrap the whole output in ```markdown``` or any code fence.
"""


def _build_prompt(source_text: str, target_language: str) -> str:
    """构建翻译 prompt（system + 源文本）；超长截断。"""
    if len(source_text) > _MAX_SOURCE_CHARS:
        source_text = source_text[:_MAX_SOURCE_CHARS] + "\n\n... [truncated] ..."
    system = _TRANSLATE_SYSTEM_ZH if target_language == "zh" else _TRANSLATE_SYSTEM_EN
    return f"{system}\n\n---\n\n{source_text}"
