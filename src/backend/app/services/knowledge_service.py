"""Independent multi-document knowledge-library service.

PostgreSQL stores ownership, versions, parser bindings, and object pointers.
All Markdown bodies and parser results are immutable objects in RustFS.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from fastapi import HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlmodel import Session, func, select

from app import models
from app.core.storage import storage
from app.schemas import knowledge as schemas
from app.services import knowledge_cleanup

MAX_MARKDOWN_BYTES = 20 * 1024 * 1024
MAX_TOPIC_BYTES = 100 * 1024 * 1024
MAX_SOURCE_FILES = 20
_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_UNSAFE_BACKGROUND_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
GLOBAL_MEMORY_CARD_TYPES = Literal[
    "good_algorithm",
    "error_reflection",
    "domain_knowledge",
    "general_insight",
]


class ParserMemoryCard(BaseModel):
    type: GLOBAL_MEMORY_CARD_TYPES
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class MemoryDraftCard(ParserMemoryCard):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    memory_id: str | None = None


class MemoryDraftManifest(BaseModel):
    version: int = 1
    cards: list[MemoryDraftCard] = Field(min_length=1, max_length=100)


class ParserDocument(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=512)


class ParserManifest(BaseModel):
    cards: list[ParserMemoryCard] = Field(default_factory=list, min_length=0, max_length=100)
    documents: list[ParserDocument] = Field(default_factory=list, max_length=100)
    # Legacy fields are accepted so an in-flight parser container from the
    # previous image can still finish safely during a rolling deployment.
    main: ParserDocument | None = None
    children: list[ParserDocument] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def normalize_documents(self):
        if not self.documents and self.main is not None:
            self.documents = [self.main, *self.children]
        if not self.cards and not self.documents:
            raise ValueError("manifest must contain at least one document block")
        return self


class ValidatedMarkdownUpload(BaseModel):
    filename: str
    data: bytes
    content_hash: str


def estimate_knowledge_tokens(content: str) -> int:
    """Return a stable tokenizer-independent estimate for UI budgeting.

    CJK characters are commonly close to one token each. Remaining visible
    characters use the conventional four-characters-per-token estimate. The
    task model may tokenize differently, so API/UI labels this value as an
    estimate rather than an exact provider count.
    """
    cjk_count = len(_CJK_CHARACTER.findall(content))
    non_cjk_count = sum(
        1 for character in content if not character.isspace() and not _CJK_CHARACTER.match(character)
    )
    return max(1, cjk_count + (non_cjk_count + 3) // 4)


def normalize_parse_background(value: str | None) -> str:
    """Keep user prose while removing invisible control characters."""
    if not value:
        return ""
    return _UNSAFE_BACKGROUND_CONTROLS.sub("", value).strip()


def resolve_parse_background(request_value: str | None, topic_value: str | None) -> str:
    """Prefer a compatible per-run override, otherwise use the topic context."""
    return normalize_parse_background(
        request_value if request_value is not None else topic_value
    )


def _safe_manifest_path(raw: str) -> str:
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() not in _MARKDOWN_SUFFIXES
        or any(part in {"", "."} for part in path.parts)
    ):
        raise ValueError(f"unsafe parser output path: {raw}")
    return path.as_posix()


def validate_parser_manifest(payload: dict[str, Any]) -> ParserManifest:
    """Validate document-block output while accepting legacy card manifests."""
    try:
        manifest = ParserManifest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid parser manifest: {exc}") from exc
    paths: set[str] = set()
    for item in manifest.documents:
        item.path = _safe_manifest_path(item.path)
        if item.path in paths:
            raise ValueError(f"duplicate parser output path: {item.path}")
        paths.add(item.path)
    return manifest


def validate_parse_plan_payload(payload: dict[str, Any]) -> schemas.KnowledgeParsePlanPayload:
    """Validate the user-visible extraction plan produced by the parser agent."""
    try:
        return schemas.KnowledgeParsePlanPayload.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid parse plan: {exc}") from exc


def validate_plan_question_answers(
    pending: dict[str, Any],
    request: schemas.KnowledgeParsePlanAnswerRequest,
) -> dict[str, str]:
    """Validate answers against the exact question round currently awaiting input."""
    try:
        question_round = schemas.KnowledgeParsePlanPendingQuestion.model_validate(pending)
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail="当前解析问题已失效，请刷新后重试") from exc
    if request.question_id != question_round.question_id:
        raise HTTPException(status_code=409, detail="当前解析问题已失效，请刷新后重试")
    expected = {item.question for item in question_round.questions}
    if set(request.answers) != expected:
        raise HTTPException(status_code=400, detail="请回答当前方案中的全部问题")
    answers: dict[str, str] = {}
    for question in question_round.questions:
        answer = _UNSAFE_BACKGROUND_CONTROLS.sub("", request.answers.get(question.question, "")).strip()
        if not answer:
            raise HTTPException(status_code=400, detail="方案问题的回答不能为空")
        if len(answer) > 1000:
            raise HTTPException(status_code=400, detail="单个方案回答不能超过 1000 个字符")
        answers[question.question] = answer
    return answers


def _decode_markdown(data: bytes) -> str:
    if not data:
        raise HTTPException(status_code=400, detail="Markdown 文档不能为空")
    if len(data) > MAX_MARKDOWN_BYTES:
        raise HTTPException(status_code=413, detail="单个 Markdown 文档不能超过 20 MiB")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Markdown 文档必须使用 UTF-8 编码") from exc
    if not content.strip():
        raise HTTPException(status_code=400, detail="Markdown 文档不能为空")
    return content


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_filename(filename: str) -> str:
    name = PurePosixPath((filename or "knowledge.md").replace("\\", "/")).name
    if PurePosixPath(name).suffix.lower() not in _MARKDOWN_SUFFIXES:
        raise HTTPException(status_code=400, detail="仅支持 .md 或 .markdown 文件")
    return name[:255]


def validate_markdown_batch(
    files: list[tuple[str, bytes]],
) -> list[ValidatedMarkdownUpload]:
    """Validate one topic upload without splitting or merging its documents."""
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一份 Markdown 文档")
    if len(files) > MAX_SOURCE_FILES:
        raise HTTPException(status_code=413, detail="一个知识主题最多包含 20 份原始文档")
    validated: list[ValidatedMarkdownUpload] = []
    names: set[str] = set()
    total_size = 0
    for raw_name, data in files:
        filename = _safe_filename(raw_name)
        marker = filename.casefold()
        if marker in names:
            raise HTTPException(status_code=400, detail=f"存在同名文档：{filename}")
        names.add(marker)
        _decode_markdown(data)
        total_size += len(data)
        validated.append(
            ValidatedMarkdownUpload(
                filename=filename,
                data=data,
                content_hash=_digest(data),
            )
        )
    if total_size > MAX_TOPIC_BYTES:
        raise HTTPException(status_code=413, detail="一个知识主题的原始文档总计不能超过 100 MiB")
    return validated


async def _read_uploads(files: list[UploadFile]) -> list[ValidatedMarkdownUpload]:
    payloads: list[tuple[str, bytes]] = []
    for file in files:
        try:
            data = await file.read(MAX_MARKDOWN_BYTES + 1)
        finally:
            await file.close()
        payloads.append((file.filename or "knowledge.md", data))
    return validate_markdown_batch(payloads)


def _source_file_key(
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    file_id: uuid.UUID,
    version: int,
) -> str:
    return f"knowledge/{user_id}/{source_id}/sources/{file_id}/v{version}.md"


def _document_key(
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    document_id: uuid.UUID,
    version: int,
) -> str:
    return f"knowledge/{user_id}/{source_id}/parses/{run_id}/documents/{document_id}/v{version}.md"


def _plan_key(user_id: uuid.UUID, source_id: uuid.UUID, plan_id: uuid.UUID) -> str:
    return f"knowledge/{user_id}/{source_id}/plans/{plan_id}/plan.json"


def _plan_candidate_key(user_id: uuid.UUID, source_id: uuid.UUID, plan_id: uuid.UUID) -> str:
    return f"knowledge/{user_id}/{source_id}/plans/{plan_id}/candidate.txt"


def _get_owned_source(db: Session, user_id: uuid.UUID, source_id: uuid.UUID) -> models.KnowledgeSource:
    source = db.exec(
        select(models.KnowledgeSource).where(
            models.KnowledgeSource.id == source_id,
            models.KnowledgeSource.user_id == user_id,
        )
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="知识主题不存在")
    return source


def _get_owned_source_for_update(
    db: Session,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
) -> models.KnowledgeSource:
    source = db.exec(
        select(models.KnowledgeSource)
        .where(
            models.KnowledgeSource.id == source_id,
            models.KnowledgeSource.user_id == user_id,
        )
        .with_for_update()
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="知识主题不存在")
    return source


def _get_owned_source_file(db: Session, user_id: uuid.UUID, file_id: uuid.UUID) -> models.KnowledgeSourceFile:
    source_file = db.exec(
        select(models.KnowledgeSourceFile)
        .join(
            models.KnowledgeSource,
            models.KnowledgeSource.id == models.KnowledgeSourceFile.source_id,
        )
        .where(
            models.KnowledgeSourceFile.id == file_id,
            models.KnowledgeSource.user_id == user_id,
        )
    ).first()
    if source_file is None:
        raise HTTPException(status_code=404, detail="原始文档不存在")
    return source_file


def _get_owned_document(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> models.KnowledgeDocument:
    document = db.exec(
        select(models.KnowledgeDocument)
        .join(
            models.KnowledgeSource,
            models.KnowledgeSource.id == models.KnowledgeDocument.source_id,
        )
        .where(
            models.KnowledgeDocument.id == document_id,
            models.KnowledgeSource.user_id == user_id,
        )
    ).first()
    if document is None:
        raise HTTPException(status_code=404, detail="解析文档不存在")
    return document


def _list_source_files(db: Session, source_id: uuid.UUID) -> list[models.KnowledgeSourceFile]:
    return list(
        db.exec(
            select(models.KnowledgeSourceFile)
            .where(models.KnowledgeSourceFile.source_id == source_id)
            .order_by(models.KnowledgeSourceFile.sort_order)
        ).all()
    )


def _source_summary(
    source: models.KnowledgeSource,
    source_files: list[models.KnowledgeSourceFile],
) -> schemas.KnowledgeSourceSummary:
    return schemas.KnowledgeSourceSummary(
        id=source.id,
        title=source.title,
        background=source.background,
        source_revision=source.source_revision,
        source_file_count=len(source_files),
        source_size=sum(item.content_size for item in source_files),
        parse_status=source.parse_status,
        active_parse_run_id=source.active_parse_run_id,
        last_error_code=source.last_error_code,
        last_error=source.last_error,
        created_time=source.created_time,
        updated_time=source.updated_time,
    )


def _mark_source_changed(source: models.KnowledgeSource) -> None:
    source.source_revision += 1
    if source.parse_status not in {
        models.KnowledgeParseStatus.PENDING.value,
        models.KnowledgeParseStatus.RUNNING.value,
    }:
        source.parse_status = (
            models.KnowledgeParseStatus.STALE.value
            if source.active_parse_run_id
            else models.KnowledgeParseStatus.UNPARSED.value
        )
    source.last_error_code = None
    source.last_error = None
    source.updated_time = datetime.now(UTC)


def create_source(
    db: Session,
    user: models.User,
    request: schemas.KnowledgeSourceCreateRequest,
) -> schemas.KnowledgeSourceSummary:
    topic_title = request.title.strip()
    if not topic_title:
        raise HTTPException(status_code=400, detail="知识主题名称不能为空")
    source = models.KnowledgeSource(user_id=user.id, title=topic_title)
    db.add(source)
    db.commit()
    db.refresh(source)
    return _source_summary(source, [])


async def add_source_files(
    db: Session,
    user: models.User,
    source_id: uuid.UUID,
    files: list[UploadFile],
) -> schemas.KnowledgeSourceDetail:
    source = _get_owned_source(db, user.id, source_id)
    existing = _list_source_files(db, source.id)
    uploads = await _read_uploads(files)
    if len(existing) + len(uploads) > MAX_SOURCE_FILES:
        raise HTTPException(status_code=413, detail="一个知识主题最多包含 20 份原始文档")
    existing_names = {item.original_filename.casefold() for item in existing}
    for upload in uploads:
        if upload.filename.casefold() in existing_names:
            raise HTTPException(status_code=400, detail=f"存在同名文档：{upload.filename}")
    if sum(item.content_size for item in existing) + sum(len(item.data) for item in uploads) > MAX_TOPIC_BYTES:
        raise HTTPException(status_code=413, detail="一个知识主题的原始文档总计不能超过 100 MiB")

    created: list[models.KnowledgeSourceFile] = []
    uploaded_keys: list[str] = []
    try:
        for offset, upload in enumerate(uploads):
            file_id = uuid.uuid4()
            key = _source_file_key(user.id, source.id, file_id, 1)
            storage.upload(key, upload.data, content_type="text/markdown; charset=utf-8")
            uploaded_keys.append(key)
            source_file = models.KnowledgeSourceFile(
                id=file_id,
                source_id=source.id,
                original_filename=upload.filename,
                content_version=1,
                object_key=key,
                content_hash=upload.content_hash,
                content_size=len(upload.data),
                sort_order=len(existing) + offset,
            )
            created.append(source_file)
            db.add(source_file)
        _mark_source_changed(source)
        db.add(source)
        db.commit()
    except Exception:
        db.rollback()
        storage.delete_many(uploaded_keys)
        raise
    return get_source_detail(db, user, source.id)


def list_sources(
    db: Session,
    user: models.User,
    *,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
) -> schemas.KnowledgeSourceListResponse:
    filters = [models.KnowledgeSource.user_id == user.id]
    normalized_search = (search or "").strip()
    if normalized_search:
        filters.append(models.KnowledgeSource.title.ilike(f"%{normalized_search}%"))
    total = db.exec(
        select(func.count()).select_from(models.KnowledgeSource).where(*filters)
    ).one()
    sources = list(
        db.exec(
            select(models.KnowledgeSource)
            .where(*filters)
            .order_by(models.KnowledgeSource.updated_time.desc())
            .offset(skip)
            .limit(limit)
        ).all()
    )
    return schemas.KnowledgeSourceListResponse(
        items=[_source_summary(item, _list_source_files(db, item.id)) for item in sources],
        total=total,
    )


def get_source_detail(db: Session, user: models.User, source_id: uuid.UUID) -> schemas.KnowledgeSourceDetail:
    source = _get_owned_source(db, user.id, source_id)
    source_files = _list_source_files(db, source.id)
    documents: list[models.KnowledgeDocument] = []
    if source.active_parse_run_id:
        documents = list(
            db.exec(
                select(models.KnowledgeDocument)
                .where(models.KnowledgeDocument.parse_run_id == source.active_parse_run_id)
                .order_by(models.KnowledgeDocument.sort_order)
            ).all()
        )
    return schemas.KnowledgeSourceDetail(
        **_source_summary(source, source_files).model_dump(),
        source_files=source_files,
        documents=documents,
    )


def update_source(
    db: Session,
    user: models.User,
    source_id: uuid.UUID,
    request: schemas.KnowledgeSourceUpdateRequest,
) -> schemas.KnowledgeSourceSummary:
    source = _get_owned_source(db, user.id, source_id)
    if request.title is not None:
        title = request.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="知识主题名称不能为空")
        source.title = title
        source.updated_time = datetime.now(UTC)
    if "background" in request.model_fields_set:
        background = normalize_parse_background(request.background) or None
        if source.background != background:
            source.background = background
            _mark_source_changed(source)
    db.add(source)
    db.commit()
    db.refresh(source)
    return _source_summary(source, _list_source_files(db, source.id))


def delete_source(db: Session, user: models.User, source_id: uuid.UUID) -> None:
    source = _get_owned_source_for_update(db, user.id, source_id)
    ensure_source_deletable(db, source)
    cleanup_job = knowledge_cleanup.prepare_source_cleanup_job(db, user.id, source.id)
    db.add(cleanup_job)
    db.delete(source)
    db.commit()
    knowledge_cleanup.run_or_schedule_cleanup(cleanup_job.id)


def get_source_file_content(db: Session, user: models.User, file_id: uuid.UUID) -> schemas.KnowledgeContentResponse:
    source_file = _get_owned_source_file(db, user.id, file_id)
    return schemas.KnowledgeContentResponse(
        content=_decode_markdown(storage.download(source_file.object_key)),
        content_version=source_file.content_version,
        content_hash=source_file.content_hash,
    )


def update_source_file(
    db: Session,
    user: models.User,
    file_id: uuid.UUID,
    request: schemas.KnowledgeSourceFileUpdateRequest,
) -> models.KnowledgeSourceFile:
    source_file = _get_owned_source_file(db, user.id, file_id)
    source = _get_owned_source(db, user.id, source_file.source_id)
    changed = False
    uploaded_key: str | None = None
    if request.original_filename is not None:
        filename = _safe_filename(request.original_filename)
        if filename.casefold() != source_file.original_filename.casefold():
            duplicate = db.exec(
                select(models.KnowledgeSourceFile).where(
                    models.KnowledgeSourceFile.source_id == source.id,
                    func.lower(models.KnowledgeSourceFile.original_filename) == filename.lower(),
                    models.KnowledgeSourceFile.id != source_file.id,
                )
            ).first()
            if duplicate:
                raise HTTPException(status_code=400, detail=f"存在同名文档：{filename}")
            source_file.original_filename = filename
            changed = True
    if request.content is not None:
        data = request.content.encode("utf-8")
        _decode_markdown(data)
        digest = _digest(data)
        if digest != source_file.content_hash:
            topic_size = sum(item.content_size for item in _list_source_files(db, source.id))
            if topic_size - source_file.content_size + len(data) > MAX_TOPIC_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="一个知识主题的原始文档总计不能超过 100 MiB",
                )
            version = source_file.content_version + 1
            uploaded_key = _source_file_key(user.id, source.id, source_file.id, version)
            storage.upload(uploaded_key, data, content_type="text/markdown; charset=utf-8")
            source_file.object_key = uploaded_key
            source_file.content_version = version
            source_file.content_hash = digest
            source_file.content_size = len(data)
            changed = True
    if changed:
        source_file.updated_time = datetime.now(UTC)
        _mark_source_changed(source)
    try:
        db.add(source_file)
        db.add(source)
        db.commit()
        db.refresh(source_file)
    except Exception:
        db.rollback()
        if uploaded_key:
            storage.delete(uploaded_key)
        raise
    return source_file


def delete_source_file(db: Session, user: models.User, file_id: uuid.UUID) -> None:
    source_file = _get_owned_source_file(db, user.id, file_id)
    source = _get_owned_source_for_update(db, user.id, source_file.source_id)
    ensure_source_deletable(db, source)
    cleanup_job = knowledge_cleanup.prepare_file_cleanup_job(user.id, source.id, source_file.id)
    db.add(cleanup_job)
    db.delete(source_file)
    _mark_source_changed(source)
    db.add(source)
    db.commit()
    knowledge_cleanup.run_or_schedule_cleanup(cleanup_job.id)


def ensure_source_deletable(db: Session, source: models.KnowledgeSource) -> None:
    """Prevent deleting parser inputs while any parser job still references them."""
    if source.parse_status in {
        models.KnowledgeParseStatus.PENDING.value,
        models.KnowledgeParseStatus.RUNNING.value,
    }:
        raise HTTPException(status_code=409, detail="知识主题正在解析，暂时不能删除")
    active_plan = db.exec(
        select(models.KnowledgeParsePlan).where(
            models.KnowledgeParsePlan.source_id == source.id,
            models.KnowledgeParsePlan.status.in_(
                {
                    models.KnowledgeParseStatus.PENDING.value,
                    models.KnowledgeParseStatus.RUNNING.value,
                }
            ),
        )
    ).first()
    if active_plan is not None:
        raise HTTPException(status_code=409, detail="知识主题正在生成解析方案，暂时不能删除")


def get_document_content(db: Session, user: models.User, document_id: uuid.UUID) -> schemas.KnowledgeContentResponse:
    document = _get_owned_document(db, user.id, document_id)
    return schemas.KnowledgeContentResponse(
        content=_decode_markdown(storage.download(document.object_key)),
        content_version=document.content_version,
        content_hash=document.content_hash,
    )


def update_document(
    db: Session,
    user: models.User,
    document_id: uuid.UUID,
    request: schemas.KnowledgeDocumentUpdateRequest,
) -> models.KnowledgeDocument:
    document = _get_owned_document(db, user.id, document_id)
    source = _get_owned_source(db, user.id, document.source_id)
    uploaded_key: str | None = None
    changed = False
    if request.title is not None:
        title = request.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="文档块标题不能为空")
        if title != document.title:
            document.title = title
            document.user_modified = True
            changed = True
    if request.content is not None:
        data = request.content.encode("utf-8")
        _decode_markdown(data)
        if _digest(data) != document.content_hash:
            version = document.content_version + 1
            uploaded_key = _document_key(user.id, source.id, document.parse_run_id, document.id, version)
            storage.upload(uploaded_key, data, content_type="text/markdown; charset=utf-8")
            document.object_key = uploaded_key
            document.content_version = version
            document.content_hash = _digest(data)
            document.content_size = len(data)
            document.estimated_tokens = estimate_knowledge_tokens(request.content)
            document.user_modified = True
            changed = True
    document.updated_time = datetime.now(UTC)
    try:
        if changed:
            run = db.get(models.KnowledgeParseRun, document.parse_run_id)
            if run is not None:
                document_key = str(document.id)
                run.inserted_document_ids = [
                    item
                    for item in list(getattr(run, "inserted_document_ids", []) or [])
                    if item != document_key
                ]
                db.add(run)
        db.add(document)
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        if uploaded_key:
            storage.delete(uploaded_key)
        raise
    return document


def _get_selected_run_documents(
    db: Session,
    run: models.KnowledgeParseRun,
    document_ids: list[uuid.UUID],
) -> list[models.KnowledgeDocument]:
    documents = list(
        db.exec(
            select(models.KnowledgeDocument)
            .where(
                models.KnowledgeDocument.parse_run_id == run.id,
                models.KnowledgeDocument.id.in_(document_ids),
            )
            .order_by(models.KnowledgeDocument.sort_order)
        ).all()
    )
    if len(documents) != len(document_ids):
        raise HTTPException(status_code=404, detail="部分预提取文档块不存在")
    return documents


def _memory_events_from_add_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    memories = ((payload.get("data") or {}).get("memories") or [])
    result: list[dict[str, Any]] = []
    for item in memories:
        if not isinstance(item, dict):
            continue
        memory_id = str(item.get("memory_id") or item.get("id") or "").strip()
        if not memory_id:
            continue
        operation = str(item.get("operation") or "add")
        if operation not in {"add", "update", "reinforcement"}:
            operation = "add"
        related_memory_ids = list(
            dict.fromkeys(
                str(value).strip()
                for value in item.get("related_memory_ids") or []
                if str(value).strip()
            )
        )
        result.append(
            {
                "memory_id": memory_id,
                "operation": operation,
                "related_memory_ids": related_memory_ids,
            }
        )
    return result


def _generated_memory_cards(
    user: models.User,
    memory_ids: list[str],
) -> list[schemas.MemoryCardResponse]:
    """Hydrate generated IDs through the canonical LLM4AD card adapter."""
    from app.services import memory_service

    unique_ids = memory_service._unique_ids(memory_ids)
    cards = memory_service._remote_fetch_cards_by_ids(
        user,
        memory_service._base_mindmemos_scope(user, "global", "global"),
        unique_ids,
        include_tags=True,
    )
    return [cards[memory_id] for memory_id in unique_ids if memory_id in cards]


def _generated_memory_cards_with_operations(
    user: models.User,
    run: Any,
) -> list[schemas.MemoryCardResponse]:
    operations = dict(getattr(run, "generated_memory_operations", {}) or {})
    return [
        card.model_copy(update={"operation": operations.get(card.id)})
        for card in _generated_memory_cards(
            user,
            list(getattr(run, "generated_memory_ids", []) or []),
        )
    ]


def _prepare_document_block_insert(
    db: Session,
    user: models.User,
    run_id: uuid.UUID,
    request: schemas.KnowledgeDocumentInsertRequest,
) -> tuple[Any, list[Any], list[str], dict[str, Any] | None]:
    """Validate and build one lossless structured document-batch request."""
    from app.services import memory_service

    run = get_parse_run(db, user, run_id)
    source = _get_owned_source(db, user.id, run.source_id)
    if run.status != models.KnowledgeParseStatus.READY.value:
        raise HTTPException(status_code=409, detail="预提取文档块尚未整理完成")
    if source.source_revision != run.source_revision:
        raise HTTPException(status_code=409, detail="原始文档已更新，请重新整理后再插入")

    already_inserted = list(getattr(run, "inserted_document_ids", []) or [])
    pending_ids = [item for item in request.document_ids if str(item) not in already_inserted]
    if not pending_ids:
        return run, [], already_inserted, None

    documents = _get_selected_run_documents(db, run, pending_ids)
    document_blocks: list[dict[str, Any]] = []
    idempotency_documents: list[dict[str, Any]] = []
    for document in documents:
        content = _decode_markdown(storage.download(document.object_key))
        metadata = {
            "source": "llm4ad",
            "source_type": "knowledge_document_import",
            "knowledge_source_id": str(source.id),
            "knowledge_parse_run_id": str(run.id),
            "knowledge_document_id": str(document.id),
            "content_version": document.content_version,
            "content_hash": document.content_hash,
            "title": document.title,
        }
        document_blocks.append(
            {
                "block_id": str(document.id),
                "document_id": str(document.id),
                "messages": [{"role": "user", "content": content}],
                "locator": {
                    "source_id": str(source.id),
                    "parse_run_id": str(run.id),
                    "title": document.title,
                    "sort_order": document.sort_order,
                },
                "metadata": metadata,
            }
        )
        idempotency_documents.append(
            {
                "id": str(document.id),
                "content_version": document.content_version,
                "content_hash": document.content_hash,
            }
        )

    idempotency_payload = json.dumps(
        {
            "user_id": str(user.id),
            "source_id": str(source.id),
            "run_id": str(run.id),
            "documents": idempotency_documents,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload = {
        **memory_service._base_mindmemos_scope(user, "global", "global"),
        "mode": "sync",
        "document_blocks": document_blocks,
        "metadata": {
            "source": "llm4ad",
            "source_type": "knowledge_document_import",
            "knowledge_source_id": str(source.id),
            "knowledge_parse_run_id": str(run.id),
            "batch_size": len(document_blocks),
        },
        "idempotency_key": (
            "llm4ad-knowledge-batch:"
            + hashlib.sha256(idempotency_payload.encode("utf-8")).hexdigest()
        ),
    }
    return run, documents, already_inserted, payload


def _complete_document_block_insert(
    db: Session,
    user: models.User,
    run: Any,
    documents: list[Any],
    already_inserted: list[str],
    memory_events: list[dict[str, Any]],
) -> schemas.KnowledgeDocumentInsertResponse:
    """Commit local insertion bookkeeping after MindMemOS completes atomically."""

    if not memory_events:
        raise HTTPException(status_code=422, detail="所选文档块没有提取出可保存的记忆")
    inserted_ids = [*already_inserted]
    for document in documents:
        value = str(document.id)
        if value not in inserted_ids:
            inserted_ids.append(value)
    operation_by_id = {
        str(event["memory_id"]): str(event["operation"])
        for event in memory_events
    }
    retired_ids = {
        str(memory_id)
        for event in memory_events
        if event["operation"] in {"update", "reinforcement"}
        for memory_id in event.get("related_memory_ids") or []
    }
    generated_ids = [
        memory_id
        for memory_id in list(getattr(run, "generated_memory_ids", []) or [])
        if memory_id not in retired_ids
    ]
    for memory_id in operation_by_id:
        if memory_id not in generated_ids:
            generated_ids.append(memory_id)
    generated_memories = [
        card.model_copy(update={"operation": operation_by_id.get(card.id)})
        for card in _generated_memory_cards(user, generated_ids)
    ]
    generated_ids = [card.id for card in generated_memories]
    stored_operations = dict(getattr(run, "generated_memory_operations", {}) or {})
    for memory_id in retired_ids:
        stored_operations.pop(memory_id, None)
    stored_operations.update(operation_by_id)
    stored_operations = {
        memory_id: operation
        for memory_id in generated_ids
        if (operation := stored_operations.get(memory_id)) in {"add", "update", "reinforcement"}
    }
    run.inserted_document_ids = inserted_ids
    run.generated_memory_ids = generated_ids
    run.generated_memory_operations = stored_operations
    run.message = f"已批量插入 {len(inserted_ids)} 个文档块"
    run.updated_time = datetime.now(UTC)
    db.add(run)
    db.commit()
    return schemas.KnowledgeDocumentInsertResponse(
        inserted_document_ids=[uuid.UUID(item) for item in inserted_ids],
        generated_memory_ids=generated_ids,
        generated_memories=generated_memories,
    )


def list_generated_memory_cards(
    db: Session,
    user: models.User,
    run_id: uuid.UUID,
) -> list[schemas.MemoryCardResponse]:
    """Return still-existing generated cards for an owned parse run."""
    run = get_parse_run(db, user, run_id)
    return _generated_memory_cards_with_operations(user, run)


def insert_document_blocks(
    db: Session,
    user: models.User,
    run_id: uuid.UUID,
    request: schemas.KnowledgeDocumentInsertRequest,
) -> schemas.KnowledgeDocumentInsertResponse:
    """Insert selected editable blocks through one structured MindMemOS add."""
    from app.services import memory_service

    run, documents, already_inserted, payload = _prepare_document_block_insert(db, user, run_id, request)
    if payload is None:
        return schemas.KnowledgeDocumentInsertResponse(
            inserted_document_ids=[uuid.UUID(item) for item in already_inserted],
            generated_memory_ids=list(getattr(run, "generated_memory_ids", []) or []),
            generated_memories=_generated_memory_cards_with_operations(user, run),
        )
    memory_service._require_mindmemos_memory_enabled()
    memory_service._ensure_mindmemos_provider_binding(db, user)
    result = memory_service._mindmemos_post(
        user,
        "/v1/memory/add",
        payload,
        scopes=["memory:write"],
    )
    memory_events = _memory_events_from_add_response(result)
    return _complete_document_block_insert(db, user, run, documents, already_inserted, memory_events)


async def stream_insert_document_blocks(
    db: Session,
    user: models.User,
    run_id: uuid.UUID,
    request: schemas.KnowledgeDocumentInsertRequest,
) -> AsyncIterator[dict[str, Any]]:
    """Proxy structured Add progress and commit local state only after completion."""

    from app.services import memory_service

    run, documents, already_inserted, payload = _prepare_document_block_insert(db, user, run_id, request)
    if payload is None:
        result = schemas.KnowledgeDocumentInsertResponse(
            inserted_document_ids=[uuid.UUID(item) for item in already_inserted],
            generated_memory_ids=list(getattr(run, "generated_memory_ids", []) or []),
            generated_memories=_generated_memory_cards_with_operations(user, run),
        )
        yield {"event": "completed", "data": result.model_dump(mode="json")}
        return

    memory_service._require_mindmemos_memory_enabled()
    memory_service._ensure_mindmemos_provider_binding(db, user)
    async for event in memory_service._mindmemos_stream_post(
        user,
        "/v1/memory/add/stream",
        payload,
        scopes=["memory:write"],
    ):
        event_name = str(event.get("event") or "progress")
        if event_name != "completed":
            yield event
            if event_name in {"error", "cancelled"}:
                return
            continue

        memory_events = _memory_events_from_add_response(event)
        result = _complete_document_block_insert(db, user, run, documents, already_inserted, memory_events)
        yield {"event": "completed", "data": result.model_dump(mode="json")}
        return


def validate_parser_binding(
    provider: Any,
    user_id: uuid.UUID,
    model_name: str,
) -> Any:
    """Validate ownership/visibility and require a declared provider model."""
    if provider is None or (
        getattr(provider, "user_id", None) != user_id
        and not (getattr(provider, "is_builtin", False) and getattr(provider, "visible_to_all", False))
    ):
        raise HTTPException(status_code=400, detail="绑定的解析模型供应商不存在或不可用")
    models_available = {item.strip() for item in str(getattr(provider, "model", "") or "").split(";") if item.strip()}
    if model_name.strip() not in models_available:
        raise HTTPException(status_code=400, detail="绑定的解析模型已不在供应商模型列表中")
    return provider


def _get_parser_binding(db: Session, user_id: uuid.UUID) -> models.KnowledgeParserBinding | None:
    return db.exec(
        select(models.KnowledgeParserBinding).where(models.KnowledgeParserBinding.user_id == user_id)
    ).first()


def get_parser_binding(db: Session, user: models.User) -> schemas.KnowledgeParserBindingResponse:
    binding = _get_parser_binding(db, user.id)
    if binding is None:
        return schemas.KnowledgeParserBindingResponse(
            configured=False,
            error_code="binding_required",
            message="请先绑定知识库解析模型",
        )
    provider = db.get(models.LLMProvider, binding.provider_id) if binding.provider_id else None
    try:
        validate_parser_binding(provider, user.id, binding.model_name)
    except HTTPException as exc:
        return schemas.KnowledgeParserBindingResponse(
            configured=False,
            provider_id=binding.provider_id,
            provider_name=getattr(provider, "name", None),
            provider_type=(
                provider.type.value
                if provider is not None and hasattr(provider.type, "value")
                else str(provider.type)
                if provider is not None
                else None
            ),
            model_name=binding.model_name,
            context_window_tokens=binding.context_window_tokens,
            max_output_tokens=binding.max_output_tokens,
            error_code=("provider_unavailable" if provider is None else "model_unavailable"),
            message=str(exc.detail),
        )
    return schemas.KnowledgeParserBindingResponse(
        configured=True,
        provider_id=provider.id,
        provider_name=provider.name,
        provider_type=(provider.type.value if hasattr(provider.type, "value") else str(provider.type)),
        model_name=binding.model_name,
        context_window_tokens=binding.context_window_tokens,
        max_output_tokens=binding.max_output_tokens,
        message="知识库解析模型已绑定",
    )


def upsert_parser_binding(
    db: Session,
    user: models.User,
    request: schemas.KnowledgeParserBindingUpdate,
) -> schemas.KnowledgeParserBindingResponse:
    provider = db.get(models.LLMProvider, request.provider_id)
    validate_parser_binding(provider, user.id, request.model_name)
    binding = _get_parser_binding(db, user.id)
    if binding is None:
        binding = models.KnowledgeParserBinding(
            user_id=user.id,
            provider_id=provider.id,
            model_name=request.model_name.strip(),
            context_window_tokens=request.context_window_tokens,
            max_output_tokens=request.max_output_tokens,
        )
    else:
        binding.provider_id = provider.id
        binding.model_name = request.model_name.strip()
        binding.context_window_tokens = request.context_window_tokens
        binding.max_output_tokens = request.max_output_tokens
        binding.updated_time = datetime.now(UTC)
    db.add(binding)
    db.commit()
    return get_parser_binding(db, user)


def _resolve_parser_binding(
    db: Session,
    user: models.User,
) -> tuple[models.LLMProvider, str, int, int]:
    binding = _get_parser_binding(db, user.id)
    if binding is None or binding.provider_id is None:
        raise HTTPException(status_code=409, detail="请先绑定知识库解析模型")
    provider = db.get(models.LLMProvider, binding.provider_id)
    validate_parser_binding(provider, user.id, binding.model_name)
    return (
        provider,
        binding.model_name,
        binding.context_window_tokens,
        binding.max_output_tokens,
    )


def _build_source_snapshot(
    source_files: list[models.KnowledgeSourceFile],
) -> list[dict[str, Any]]:
    return [
        {
            "file_id": str(item.id),
            "filename": item.original_filename,
            "content_version": item.content_version,
            "object_key": item.object_key,
            "content_hash": item.content_hash,
            "content_size": item.content_size,
        }
        for item in source_files
    ]


def _get_owned_parse_plan(
    db: Session,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
) -> models.KnowledgeParsePlan:
    plan = db.exec(
        select(models.KnowledgeParsePlan)
        .join(
            models.KnowledgeSource,
            models.KnowledgeSource.id == models.KnowledgeParsePlan.source_id,
        )
        .where(
            models.KnowledgeParsePlan.id == plan_id,
            models.KnowledgeSource.user_id == user_id,
        )
    ).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="解析方案不存在")
    return plan


def _load_plan_payload(plan: models.KnowledgeParsePlan) -> schemas.KnowledgeParsePlanPayload:
    if not plan.plan_object_key:
        raise HTTPException(status_code=409, detail="解析方案尚未生成完成")
    try:
        payload = json.loads(storage.download(plan.plan_object_key).decode("utf-8"))
        return validate_parse_plan_payload(payload)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="解析方案内容无效") from exc


def _plan_response(
    plan: models.KnowledgeParsePlan,
    source_revision: int,
) -> schemas.KnowledgeParsePlanResponse:
    status = plan.status
    if status == models.KnowledgeParseStatus.READY.value and plan.source_revision != source_revision:
        status = models.KnowledgeParseStatus.STALE.value
    payload = (
        _load_plan_payload(plan)
        if plan.plan_object_key
        and status
        in {
            models.KnowledgeParseStatus.READY.value,
            models.KnowledgeParseStatus.STALE.value,
        }
        else None
    )
    retry_action = parse_plan_retry_action(plan)
    return schemas.KnowledgeParsePlanResponse(
        **schemas.KnowledgeParsePlanResponse.model_validate(plan).model_dump(
            exclude={"payload", "status", "retryable", "retry_action"}
        ),
        status=status,
        payload=payload,
        retryable=retry_action is not None,
        retry_action=retry_action,
    )


def parse_plan_retry_action(plan: models.KnowledgeParsePlan) -> Literal["persist"] | None:
    """Return the model-free continuation available for a failed plan."""
    if plan.status != models.KnowledgeParseStatus.FAILED.value:
        return None
    if plan.error_code in {"plan_checkpoint_failed", "plan_persist_failed"}:
        return "persist"
    return None


def activate_parse_plan_payload(
    db: Session,
    plan: models.KnowledgeParsePlan,
    source: models.KnowledgeSource,
    payload: schemas.KnowledgeParsePlanPayload,
) -> str:
    """Activate an already validated and stored plan without invoking a model."""
    is_stale = source.source_revision != plan.source_revision
    plan.status = models.KnowledgeParseStatus.STALE.value if is_stale else models.KnowledgeParseStatus.READY.value
    plan.progress = 100
    plan.stage = "stale" if is_stale else "completed"
    recommended = next(item for item in payload.strategies if item.id == payload.recommended_strategy_id)
    plan.message = (
        "原文已更新，方案仅供查看"
        if is_stale
        else f"已生成 {len(payload.strategies)} 个方案，推荐产出 {recommended.document_count} 份文档"
    )
    plan.error_code = None
    plan.error = None
    plan.pending_question = None
    plan.updated_time = datetime.now(UTC)
    db.add(plan)
    db.commit()
    return "stale" if is_stale else "ready"


def _issue_parser_token(
    *,
    user: models.User,
    provider: models.LLMProvider,
    model: str,
    task_id: uuid.UUID,
    access_token: str,
) -> tuple[str, str]:
    from app.core.config import settings
    from app.services import credential_broker

    base_url = provider.base_url or ""
    if provider.is_builtin and access_token:
        base_url = base_url.replace("{accessToken}", access_token)
    upstream_api_format = "anthropic" if provider.type == models.ProviderType.ANTHROPIC else "openai_chat"
    proxy_token = credential_broker.issue_token(
        user_id=user.id,
        task_id=task_id,
        ttl=settings.KNOWLEDGE_PARSER_TIMEOUT + 600,
        provider_type=(provider.type.value if hasattr(provider.type, "value") else str(provider.type)),
        base_url=base_url,
        api_key=provider.api_key or "",
        auth_token=provider.auth_token or "",
        model=model,
        timeout=provider.timeout,
    )
    return proxy_token, upstream_api_format


def _parser_session_path(
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    job_kind: Literal["plan", "run"],
) -> Path:
    from app.core.config import settings

    directory = "knowledge_plan" if job_kind == "plan" else "knowledge_parse"
    return (
        Path(settings.DOCKER_PROJECT_HOME)
        / f"code_user-{user_id}"
        / directory
        / str(job_id)
        / ".parser-runtime"
        / "session-id"
    )


def _require_resumable_parser_session(
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    job_kind: Literal["plan", "run"],
) -> None:
    session_path = _parser_session_path(user_id, job_id, job_kind=job_kind)
    try:
        session_id = session_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail="解析会话尚未建立，无法从中断位置继续，请重新开始解析。",
        ) from exc
    if not session_id or len(session_id) > 256 or "\n" in session_id:
        raise HTTPException(status_code=409, detail="解析会话检查点无效，请重新开始解析。")


def _pinned_parser_provider(
    db: Session,
    user: models.User,
    provider_id: uuid.UUID | None,
    model: str | None,
) -> tuple[models.LLMProvider, str, int, int]:
    if provider_id is None or not model:
        raise HTTPException(status_code=409, detail="原解析模型配置不完整，无法继续。")
    provider = db.get(models.LLMProvider, provider_id)
    validate_parser_binding(provider, user.id, model)
    binding = _get_parser_binding(db, user.id)
    if binding and binding.provider_id == provider_id and binding.model_name == model:
        return (
            provider,
            model,
            binding.context_window_tokens,
            binding.max_output_tokens,
        )
    return (
        provider,
        model,
        models.DEFAULT_KNOWLEDGE_CONTEXT_WINDOW_TOKENS,
        models.DEFAULT_KNOWLEDGE_MAX_OUTPUT_TOKENS,
    )


def start_parse_plan(
    db: Session,
    user: models.User,
    source_id: uuid.UUID,
    request: schemas.KnowledgeParsePlanCreateRequest,
    access_token: str,
) -> schemas.KnowledgeParsePlanResponse:
    from app.core.config import settings
    from app.core.redis import delete_knowledge_parse_context, store_knowledge_parse_context
    from app.services import credential_broker

    if not settings.LLM_PROXY_ENABLE or not settings.LLM_PROXY_BASE_URL:
        raise HTTPException(status_code=503, detail="知识解析需要启用统一大模型网关")
    source = _get_owned_source(db, user.id, source_id)
    source_files = _list_source_files(db, source.id)
    if not source_files:
        raise HTTPException(status_code=400, detail="知识主题中没有可规划的原始文档")
    provider, model, context_window_tokens, max_output_tokens = _resolve_parser_binding(db, user)
    plan = models.KnowledgeParsePlan(
        source_id=source.id,
        source_revision=source.source_revision,
        source_snapshot=_build_source_snapshot(source_files),
        parser_provider_id=provider.id,
        parser_provider_name=provider.name,
        parser_model=model,
        interaction_mode=request.interaction_mode,
        message="解析方案已进入队列",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    try:
        proxy_token, upstream_api_format = _issue_parser_token(
            user=user,
            provider=provider,
            model=model,
            task_id=plan.id,
            access_token=access_token,
        )
        background = normalize_parse_background(request.background)
        if background:
            store_knowledge_parse_context(
                plan.id,
                json.dumps({"background": background}, ensure_ascii=False),
                settings.KNOWLEDGE_PARSER_TIMEOUT + 600,
            )
        from app.tasks.knowledge_parser import run_knowledge_parse_plan

        run_knowledge_parse_plan.apply_async(
            args=[
                str(plan.id),
                str(user.id),
                proxy_token,
                model,
                upstream_api_format,
                max_output_tokens,
            ],
            kwargs={"context_window_tokens": context_window_tokens},
            task_id=str(plan.id),
        )
    except Exception as exc:
        delete_knowledge_parse_context(plan.id)
        credential_broker.revoke_task_tokens(plan.id)
        plan.status = models.KnowledgeParseStatus.FAILED.value
        plan.error_code = "dispatch_failed"
        plan.error = "解析方案任务投递失败"
        plan.message = plan.error
        db.add(plan)
        db.commit()
        raise HTTPException(status_code=503, detail=plan.error) from exc
    return _plan_response(plan, source.source_revision)


def get_parse_plan(
    db: Session,
    user: models.User,
    plan_id: uuid.UUID,
) -> schemas.KnowledgeParsePlanResponse:
    plan = _get_owned_parse_plan(db, user.id, plan_id)
    source = _get_owned_source(db, user.id, plan.source_id)
    return _plan_response(plan, source.source_revision)


def get_latest_parse_plan(
    db: Session,
    user: models.User,
    source_id: uuid.UUID,
) -> schemas.KnowledgeParsePlanResponse | None:
    source = _get_owned_source(db, user.id, source_id)
    plan = db.exec(
        select(models.KnowledgeParsePlan)
        .where(models.KnowledgeParsePlan.source_id == source.id)
        .order_by(models.KnowledgeParsePlan.created_time.desc())
    ).first()
    return _plan_response(plan, source.source_revision) if plan else None


def retry_parse_plan(
    db: Session,
    user: models.User,
    plan_id: uuid.UUID,
) -> schemas.KnowledgeParsePlanResponse:
    """Continue checkpoint persistence without reading sources or calling the model."""
    from app.tasks.knowledge_parser import retry_plan_persistence

    plan = _get_owned_parse_plan(db, user.id, plan_id)
    if parse_plan_retry_action(plan) != "persist":
        raise HTTPException(status_code=409, detail="当前解析方案没有可继续的保存检查点")
    try:
        final_status = retry_plan_persistence(plan.id, user.id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="解析方案保存仍未完成，请稍后继续") from exc
    from app.core.redis import push_knowledge_parse_event

    push_knowledge_parse_event(
        plan.id,
        {
            "type": "stale" if final_status == "stale" else "done",
            "progress": 100,
            "stage": final_status,
            "message": "原文已更新，方案仅供查看" if final_status == "stale" else "解析方案已保存",
        },
    )
    db.expire_all()
    return get_parse_plan(db, user, plan.id)


def continue_parse_plan(
    db: Session,
    user: models.User,
    plan_id: uuid.UUID,
    access_token: str,
) -> schemas.KnowledgeParsePlanResponse:
    """Resume the same Agent SDK session after cancellation or a parser failure."""
    from app.core.redis import latest_knowledge_parse_event_id, push_knowledge_parse_event
    from app.services import credential_broker
    from app.tasks.knowledge_parser import run_knowledge_parse_plan

    plan = _get_owned_parse_plan(db, user.id, plan_id)
    if plan.status not in {
        models.KnowledgeParseStatus.CANCELLED.value,
        models.KnowledgeParseStatus.FAILED.value,
    }:
        raise HTTPException(status_code=409, detail="当前解析方案不在可继续状态")
    source = _get_owned_source(db, user.id, plan.source_id)
    if source.source_revision != plan.source_revision:
        raise HTTPException(status_code=409, detail="原始文档已更新，请重新生成解析方案。")
    _require_resumable_parser_session(user.id, plan.id, job_kind="plan")
    provider, model, context_window_tokens, max_output_tokens = _pinned_parser_provider(
        db,
        user,
        plan.parser_provider_id,
        plan.parser_model,
    )
    stream_cursor = latest_knowledge_parse_event_id(plan.id)
    credential_task_id = uuid.uuid4()
    try:
        proxy_token, upstream_api_format = _issue_parser_token(
            user=user,
            provider=provider,
            model=model,
            task_id=credential_task_id,
            access_token=access_token,
        )
        plan.status = models.KnowledgeParseStatus.PENDING.value
        plan.stage = "resuming"
        plan.message = "正在从上次中断位置继续生成解析方案"
        plan.error_code = None
        plan.error = None
        plan.updated_time = datetime.now(UTC)
        db.add(plan)
        db.commit()
        push_knowledge_parse_event(
            plan.id,
            {
                "type": "resume",
                "progress": plan.progress,
                "stage": plan.stage,
                "message": plan.message,
            },
        )
        run_knowledge_parse_plan.apply_async(
            args=[
                str(plan.id),
                str(user.id),
                proxy_token,
                model,
                upstream_api_format,
                max_output_tokens,
                str(credential_task_id),
            ],
            kwargs={"context_window_tokens": context_window_tokens},
            task_id=str(uuid.uuid4()),
        )
    except Exception as exc:
        credential_broker.revoke_task_tokens(credential_task_id)
        plan.status = models.KnowledgeParseStatus.FAILED.value
        plan.stage = "failed"
        plan.error_code = "dispatch_failed"
        plan.error = "解析方案继续任务投递失败"
        plan.message = plan.error
        db.add(plan)
        db.commit()
        raise HTTPException(status_code=503, detail=plan.error) from exc
    response = _plan_response(plan, source.source_revision)
    return response.model_copy(update={"stream_cursor": stream_cursor})


def answer_parse_plan_question(
    db: Session,
    user: models.User,
    plan_id: uuid.UUID,
    request: schemas.KnowledgeParsePlanAnswerRequest,
) -> schemas.KnowledgeParsePlanResponse:
    """Deliver one validated clarification round to the running parser session."""
    from app.core.config import settings
    from app.core.redis import push_knowledge_parse_event

    plan = _get_owned_parse_plan(db, user.id, plan_id)
    if (
        plan.status
        not in {
            models.KnowledgeParseStatus.PENDING.value,
            models.KnowledgeParseStatus.RUNNING.value,
        }
        or not plan.pending_question
    ):
        raise HTTPException(status_code=409, detail="当前解析方案没有等待回答的问题")
    answers = validate_plan_question_answers(plan.pending_question, request)
    work_dir = Path(settings.DOCKER_PROJECT_HOME) / f"code_user-{user.id}" / "knowledge_plan" / str(plan.id)
    control_dir = work_dir / "control"
    answer_path = control_dir / "answer.json"
    temporary_path = control_dir / f".answer-{uuid.uuid4().hex}.tmp"
    try:
        control_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(control_dir, 0o777)
        temporary_path.write_text(
            json.dumps(
                {"question_id": request.question_id, "answers": answers},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.chmod(temporary_path, 0o666)
        os.replace(temporary_path, answer_path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="回答暂时无法送达解析任务，请重试") from exc
    plan.pending_question = None
    plan.progress = max(plan.progress, 48)
    plan.stage = "resuming"
    plan.message = "已收到你的选择，正在继续生成解析方案"
    plan.updated_time = datetime.now(UTC)
    db.add(plan)
    db.commit()
    push_knowledge_parse_event(
        plan.id,
        {
            "type": "resume",
            "progress": plan.progress,
            "stage": plan.stage,
            "message": plan.message,
        },
    )
    source = _get_owned_source(db, user.id, plan.source_id)
    return _plan_response(plan, source.source_revision)


def _stop_knowledge_job_runtime(job_id: uuid.UUID, container_id: str | None) -> None:
    """Best-effort cancellation for both queued Celery work and its parser container."""
    import logging

    from app.core.celery import celery_app
    from app.core.redis import delete_knowledge_parse_context
    from app.services import container_service, credential_broker

    logger = logging.getLogger(__name__)
    try:
        celery_app.control.revoke(str(job_id), terminate=False)
    except Exception:
        logger.warning("Failed to revoke knowledge parser task %s", job_id, exc_info=True)
    container_service.kill_container_by_name(container_id or f"llm4ad-knowledge-{job_id.hex[:16]}")
    try:
        delete_knowledge_parse_context(job_id)
        credential_broker.revoke_task_tokens(job_id)
    except Exception:
        logger.warning("Failed to revoke knowledge parser credentials %s", job_id, exc_info=True)


def cancel_parse_plan(
    db: Session,
    user: models.User,
    plan_id: uuid.UUID,
) -> schemas.KnowledgeParseCancelResponse:
    from app.core.redis import push_knowledge_parse_event

    plan = _get_owned_parse_plan(db, user.id, plan_id)
    if plan.status == models.KnowledgeParseStatus.CANCELLED.value:
        return schemas.KnowledgeParseCancelResponse(
            id=plan.id,
            status="cancelled",
            message="解析方案生成已停止",
        )
    if plan.status not in {
        models.KnowledgeParseStatus.PENDING.value,
        models.KnowledgeParseStatus.RUNNING.value,
    }:
        raise HTTPException(status_code=409, detail="当前解析方案不在运行中")

    plan.status = models.KnowledgeParseStatus.CANCELLED.value
    plan.stage = "cancelled"
    plan.message = "解析方案生成已停止"
    plan.error_code = None
    plan.error = None
    plan.pending_question = None
    plan.updated_time = datetime.now(UTC)
    db.add(plan)
    db.commit()
    push_knowledge_parse_event(
        plan.id,
        {
            "type": "cancelled",
            "progress": plan.progress,
            "stage": "cancelled",
            "message": plan.message,
        },
    )
    _stop_knowledge_job_runtime(plan.id, plan.container_id)
    return schemas.KnowledgeParseCancelResponse(
        id=plan.id,
        status="cancelled",
        message=plan.message,
    )


def start_parse_run(
    db: Session,
    user: models.User,
    source_id: uuid.UUID,
    request: schemas.KnowledgeParseStartRequest,
    access_token: str,
) -> models.KnowledgeParseRun:
    source = _get_owned_source(db, user.id, source_id)
    background = resolve_parse_background(request.background, source.background)
    instruction = normalize_parse_background(request.instruction)
    if source.parse_status in {
        models.KnowledgeParseStatus.PENDING.value,
        models.KnowledgeParseStatus.RUNNING.value,
    }:
        raise HTTPException(status_code=409, detail="该知识主题正在解析")
    source_files = _list_source_files(db, source.id)
    if not source_files:
        raise HTTPException(status_code=400, detail="知识主题中没有可解析的原始文档")

    from app.core.config import settings
    from app.core.redis import (
        delete_knowledge_parse_context,
        store_knowledge_parse_context,
    )
    from app.services import credential_broker

    if not settings.LLM_PROXY_ENABLE or not settings.LLM_PROXY_BASE_URL:
        raise HTTPException(status_code=503, detail="知识解析需要启用统一大模型网关")
    provider, model, context_window_tokens, max_output_tokens = _resolve_parser_binding(db, user)
    run = models.KnowledgeParseRun(
        source_id=source.id,
        source_revision=source.source_revision,
        source_snapshot=_build_source_snapshot(source_files),
        parser_provider_id=provider.id,
        parser_provider_name=provider.name,
        parser_model=model,
        parse_mode="direct",
        plan_id=None,
        plan_strategy_id=None,
        session_owner_kind="run",
        session_owner_id=None,
    )
    run.session_owner_id = run.id
    db.add(run)
    source.parse_status = models.KnowledgeParseStatus.PENDING.value
    source.last_error_code = None
    source.last_error = None
    db.add(source)
    db.commit()
    db.refresh(run)

    try:
        proxy_token, upstream_api_format = _issue_parser_token(
            user=user,
            provider=provider,
            model=model,
            task_id=run.id,
            access_token=access_token,
        )
        from app.tasks.knowledge_parser import run_knowledge_parse

        if background or instruction:
            store_knowledge_parse_context(
                run.id,
                json.dumps(
                    {
                        "background": background,
                        "instruction": instruction,
                    },
                    ensure_ascii=False,
                ),
                settings.KNOWLEDGE_PARSER_TIMEOUT + 600,
            )
        run_knowledge_parse.apply_async(
            args=[
                str(run.id),
                str(user.id),
                proxy_token,
                model,
                upstream_api_format,
                max_output_tokens,
            ],
            kwargs={"context_window_tokens": context_window_tokens},
            task_id=str(run.id),
        )
    except Exception as exc:
        delete_knowledge_parse_context(run.id)
        credential_broker.revoke_task_tokens(run.id)
        run.status = models.KnowledgeParseStatus.FAILED.value
        run.error_code = "dispatch_failed"
        run.error = "解析任务投递失败"
        source.parse_status = (
            models.KnowledgeParseStatus.READY.value
            if run.parse_mode == "refine" and source.active_parse_run_id
            else models.KnowledgeParseStatus.FAILED.value
        )
        source.last_error_code = run.error_code
        source.last_error = run.error
        db.add(run)
        db.add(source)
        db.commit()
        raise HTTPException(status_code=503, detail=run.error) from exc
    return run


def get_parse_run(db: Session, user: models.User, run_id: uuid.UUID) -> models.KnowledgeParseRun:
    run = db.exec(
        select(models.KnowledgeParseRun)
        .join(
            models.KnowledgeSource,
            models.KnowledgeSource.id == models.KnowledgeParseRun.source_id,
        )
        .where(
            models.KnowledgeParseRun.id == run_id,
            models.KnowledgeSource.user_id == user.id,
        )
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="解析任务不存在")
    return run


def _load_memory_draft_manifest(run: models.KnowledgeParseRun) -> MemoryDraftManifest:
    if not run.manifest_object_key:
        raise HTTPException(status_code=409, detail="该解析任务还没有可审核的记忆卡片")
    try:
        payload = json.loads(storage.download(run.manifest_object_key))
        try:
            return MemoryDraftManifest.model_validate(payload)
        except ValidationError:
            # Runs completed before review drafts were introduced stored the
            # parser cards and inserted memory ids directly. Keep them
            # readable, but mark every migrated card as already inserted.
            legacy = validate_parser_manifest(payload)
            memory_ids = [str(value) for value in payload.get("memory_ids", [])]
            if not legacy.cards or len(memory_ids) != len(legacy.cards):
                raise
            return MemoryDraftManifest(
                cards=[
                    MemoryDraftCard(
                        id=uuid.uuid5(run.id, f"legacy-memory-card-{index}"),
                        memory_id=memory_ids[index],
                        **card.model_dump(),
                    )
                    for index, card in enumerate(legacy.cards)
                ]
            )
    except HTTPException:
        raise
    except (OSError, ValueError, ValidationError) as exc:
        raise HTTPException(status_code=409, detail="旧版解析结果数据无效，请重新整理") from exc


def _run_workspace_path(user_id: uuid.UUID, run: models.KnowledgeParseRun) -> Path:
    from app.core.config import settings

    owner_id = getattr(run, "session_owner_id", None) or run.id
    owner_kind = getattr(run, "session_owner_kind", None) or "run"
    directory = "knowledge_plan" if owner_kind == "plan" else "knowledge_parse"
    return (
        Path(settings.DOCKER_PROJECT_HOME)
        / f"code_user-{user_id}"
        / directory
        / str(owner_id)
    )


def _save_memory_draft_manifest(
    user_id: uuid.UUID,
    source: models.KnowledgeSource,
    run: models.KnowledgeParseRun,
    manifest: MemoryDraftManifest,
    *,
    require_workspace: bool = False,
) -> str:
    """Persist review data and keep the resumable agent workspace in sync."""
    manifest_key = run.manifest_object_key or (
        f"knowledge/{user_id}/{source.id}/parses/{run.id}/manifest.json"
    )
    storage.upload(
        manifest_key,
        manifest.model_dump_json(indent=2).encode(),
        content_type="application/json",
    )
    run.manifest_object_key = manifest_key

    # A refinement resumes the same model session. Mirror user edits into its
    # workspace, but omit platform-only draft identifiers from model input.
    output_path = _run_workspace_path(user_id, run) / "output" / "manifest.json"
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "cards": [
                        card.model_dump(exclude={"id", "memory_id"})
                        for card in manifest.cards
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Failed to synchronize knowledge draft workspace for run {}", run.id)
        if require_workspace:
            raise HTTPException(
                status_code=409,
                detail="解析会话工作区暂不可用，无法基于当前候选卡片继续优化",
            ) from exc
    return manifest_key


def _sync_document_blocks_to_workspace(
    db: Session,
    user_id: uuid.UUID,
    run: models.KnowledgeParseRun,
    *,
    require_workspace: bool = False,
) -> bool:
    """Mirror persisted, user-editable blocks into the resumable agent workspace."""
    documents = list(
        db.exec(
            select(models.KnowledgeDocument)
            .where(models.KnowledgeDocument.parse_run_id == run.id)
            .order_by(models.KnowledgeDocument.sort_order)
        ).all()
    )
    if not documents:
        return False

    output_root = _run_workspace_path(user_id, run) / "output"
    document_root = output_root / "documents"
    try:
        document_root.mkdir(parents=True, exist_ok=True)
        manifest_documents: list[dict[str, str]] = []
        for index, document in enumerate(documents, start=1):
            relative_path = f"documents/block-{index:03d}.md"
            (output_root / relative_path).write_bytes(storage.download(document.object_key))
            manifest_documents.append(
                {"title": document.title, "path": relative_path}
            )
        (output_root / "manifest.json").write_text(
            json.dumps(
                {"documents": manifest_documents},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Failed to synchronize knowledge document workspace for run {}", run.id)
        if require_workspace:
            raise HTTPException(
                status_code=409,
                detail="解析会话工作区暂不可用，无法基于当前文档块继续优化",
            ) from exc
    return True


def get_latest_parse_run(
    db: Session,
    user: models.User,
    source_id: uuid.UUID,
) -> models.KnowledgeParseRun | None:
    source = _get_owned_source(db, user.id, source_id)
    return db.exec(
        select(models.KnowledgeParseRun)
        .where(models.KnowledgeParseRun.source_id == source.id)
        .order_by(models.KnowledgeParseRun.created_time.desc())
    ).first()


def _restore_source_status_after_cancel(db: Session, source: models.KnowledgeSource) -> None:
    if not source.active_parse_run_id:
        source.parse_status = models.KnowledgeParseStatus.UNPARSED.value
        return
    active_run = db.get(models.KnowledgeParseRun, source.active_parse_run_id)
    source.parse_status = (
        models.KnowledgeParseStatus.READY.value
        if active_run and active_run.source_revision == source.source_revision
        else models.KnowledgeParseStatus.STALE.value
    )


def cancel_parse_run(
    db: Session,
    user: models.User,
    run_id: uuid.UUID,
) -> schemas.KnowledgeParseCancelResponse:
    from app.core.redis import push_knowledge_parse_event

    run = get_parse_run(db, user, run_id)
    if run.status == models.KnowledgeParseStatus.CANCELLED.value:
        return schemas.KnowledgeParseCancelResponse(
            id=run.id,
            status="cancelled",
            message="知识文档解析已停止",
        )
    if run.status not in {
        models.KnowledgeParseStatus.PENDING.value,
        models.KnowledgeParseStatus.RUNNING.value,
    }:
        raise HTTPException(status_code=409, detail="当前解析任务不在运行中")

    source = _get_owned_source(db, user.id, run.source_id)
    run.status = models.KnowledgeParseStatus.CANCELLED.value
    run.stage = "cancelled"
    run.message = "知识文档解析已停止"
    run.error_code = None
    run.error = None
    run.updated_time = datetime.now(UTC)
    if source.parse_status in {
        models.KnowledgeParseStatus.PENDING.value,
        models.KnowledgeParseStatus.RUNNING.value,
    }:
        _restore_source_status_after_cancel(db, source)
    source.last_error_code = None
    source.last_error = None
    source.updated_time = datetime.now(UTC)
    db.add(run)
    db.add(source)
    db.commit()
    push_knowledge_parse_event(
        run.id,
        {
            "type": "cancelled",
            "progress": run.progress,
            "stage": "cancelled",
            "message": run.message,
        },
    )
    _stop_knowledge_job_runtime(run.id, run.container_id)
    return schemas.KnowledgeParseCancelResponse(
        id=run.id,
        status="cancelled",
        message=run.message,
    )


def continue_parse_run(
    db: Session,
    user: models.User,
    run_id: uuid.UUID,
    access_token: str,
) -> schemas.KnowledgeParseRunResponse:
    """Resume one interrupted parser run without repeating completed agent work."""
    from app.core.redis import latest_knowledge_parse_event_id, push_knowledge_parse_event
    from app.services import credential_broker
    from app.tasks.knowledge_parser import run_knowledge_parse

    run = get_parse_run(db, user, run_id)
    if run.status not in {
        models.KnowledgeParseStatus.CANCELLED.value,
        models.KnowledgeParseStatus.FAILED.value,
    }:
        raise HTTPException(status_code=409, detail="当前解析任务不在可继续状态")
    source = _get_owned_source(db, user.id, run.source_id)
    if source.source_revision != run.source_revision:
        raise HTTPException(status_code=409, detail="原始文档已更新，请重新开始解析。")
    owner_kind = run.session_owner_kind or "run"
    owner_id = run.session_owner_id or run.id
    _require_resumable_parser_session(
        user.id,
        owner_id,
        job_kind="plan" if owner_kind == "plan" else "run",
    )
    provider, model, context_window_tokens, max_output_tokens = _pinned_parser_provider(
        db,
        user,
        run.parser_provider_id,
        run.parser_model,
    )
    stream_cursor = latest_knowledge_parse_event_id(run.id)
    credential_task_id = uuid.uuid4()
    try:
        proxy_token, upstream_api_format = _issue_parser_token(
            user=user,
            provider=provider,
            model=model,
            task_id=credential_task_id,
            access_token=access_token,
        )
        run.status = models.KnowledgeParseStatus.PENDING.value
        run.stage = "resuming"
        run.message = "正在从上次中断位置继续解析"
        run.error_code = None
        run.error = None
        run.updated_time = datetime.now(UTC)
        source.parse_status = models.KnowledgeParseStatus.PENDING.value
        source.last_error_code = None
        source.last_error = None
        source.updated_time = datetime.now(UTC)
        db.add(run)
        db.add(source)
        db.commit()
        push_knowledge_parse_event(
            run.id,
            {
                "type": "resume",
                "progress": run.progress,
                "stage": run.stage,
                "message": run.message,
            },
        )
        run_knowledge_parse.apply_async(
            args=[
                str(run.id),
                str(user.id),
                proxy_token,
                model,
                upstream_api_format,
                max_output_tokens,
                str(credential_task_id),
            ],
            kwargs={"context_window_tokens": context_window_tokens},
            task_id=str(uuid.uuid4()),
        )
    except Exception as exc:
        credential_broker.revoke_task_tokens(credential_task_id)
        run.status = models.KnowledgeParseStatus.FAILED.value
        run.stage = "failed"
        run.error_code = "dispatch_failed"
        run.error = "解析继续任务投递失败"
        run.message = run.error
        source.parse_status = (
            models.KnowledgeParseStatus.READY.value
            if run.parse_mode == "refine" and source.active_parse_run_id
            else models.KnowledgeParseStatus.FAILED.value
        )
        source.last_error_code = run.error_code
        source.last_error = run.error
        db.add(run)
        db.add(source)
        db.commit()
        raise HTTPException(status_code=503, detail=run.error) from exc
    response = schemas.KnowledgeParseRunResponse.model_validate(run)
    return response.model_copy(update={"stream_cursor": stream_cursor})


def refine_parse_run(
    db: Session,
    user: models.User,
    run_id: uuid.UUID,
    request: schemas.KnowledgeParseRefineRequest,
    access_token: str,
) -> models.KnowledgeParseRun:
    """Create a new result by continuing a successful parser session."""
    from app.core.config import settings
    from app.core.redis import delete_knowledge_parse_context, store_knowledge_parse_context
    from app.services import credential_broker
    from app.tasks.knowledge_parser import run_knowledge_parse

    parent = get_parse_run(db, user, run_id)
    if parent.status != models.KnowledgeParseStatus.READY.value:
        raise HTTPException(status_code=409, detail="只有已完成的解析结果可以继续优化")
    if getattr(parent, "generated_memory_ids", []):
        raise HTTPException(status_code=409, detail="已有候选卡片插入记忆，请重新整理后再优化")
    source = _get_owned_source(db, user.id, parent.source_id)
    if source.source_revision != parent.source_revision:
        raise HTTPException(status_code=409, detail="原始文档已更新，请重新开始解析。")
    if source.parse_status in {
        models.KnowledgeParseStatus.PENDING.value,
        models.KnowledgeParseStatus.RUNNING.value,
    }:
        raise HTTPException(status_code=409, detail="该知识主题正在解析")

    owner_kind = parent.session_owner_kind or "run"
    owner_id = parent.session_owner_id or parent.id
    _require_resumable_parser_session(
        user.id,
        owner_id,
        job_kind="plan" if owner_kind == "plan" else "run",
    )
    provider, model, context_window_tokens, max_output_tokens = _pinned_parser_provider(
        db,
        user,
        parent.parser_provider_id,
        parent.parser_model,
    )
    instruction = normalize_parse_background(request.instruction)
    if getattr(parent, "manifest_object_key", None):
        has_document_blocks = _sync_document_blocks_to_workspace(
            db,
            user.id,
            parent,
            require_workspace=True,
        )
        if not has_document_blocks:
            current_manifest = _load_memory_draft_manifest(parent)
            _save_memory_draft_manifest(
                user.id,
                source,
                parent,
                current_manifest,
                require_workspace=True,
            )
    refinement = models.KnowledgeParseRun(
        source_id=source.id,
        source_revision=parent.source_revision,
        source_snapshot=list(parent.source_snapshot or []),
        parser_provider_id=provider.id,
        parser_provider_name=provider.name,
        parser_model=model,
        parse_mode="refine",
        plan_id=parent.plan_id,
        plan_strategy_id=parent.plan_strategy_id,
        parent_run_id=parent.id,
        session_owner_kind=owner_kind,
        session_owner_id=owner_id,
    )
    db.add(refinement)
    source.parse_status = models.KnowledgeParseStatus.PENDING.value
    source.last_error_code = None
    source.last_error = None
    db.add(source)
    db.commit()
    db.refresh(refinement)

    try:
        proxy_token, upstream_api_format = _issue_parser_token(
            user=user,
            provider=provider,
            model=model,
            task_id=refinement.id,
            access_token=access_token,
        )
        store_knowledge_parse_context(
            refinement.id,
            json.dumps({"refinement": instruction}, ensure_ascii=False),
            settings.KNOWLEDGE_PARSER_TIMEOUT + 600,
        )
        run_knowledge_parse.apply_async(
            args=[
                str(refinement.id),
                str(user.id),
                proxy_token,
                model,
                upstream_api_format,
                max_output_tokens,
            ],
            kwargs={"context_window_tokens": context_window_tokens},
            task_id=str(refinement.id),
        )
    except Exception as exc:
        delete_knowledge_parse_context(refinement.id)
        credential_broker.revoke_task_tokens(refinement.id)
        refinement.status = models.KnowledgeParseStatus.FAILED.value
        refinement.error_code = "dispatch_failed"
        refinement.error = "知识文档优化任务投递失败"
        source.parse_status = models.KnowledgeParseStatus.READY.value
        db.add(refinement)
        db.add(source)
        db.commit()
        raise HTTPException(status_code=503, detail=refinement.error) from exc
    return refinement
