"""Knowledge-library metadata models.

Markdown bodies are stored in RustFS.  These tables only keep ownership,
version pointers, parse state, and object keys.
"""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import Index, Text, UniqueConstraint
from sqlmodel import JSON, Column, Field, SQLModel

from app.models.base import TimeMixin

DEFAULT_KNOWLEDGE_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_KNOWLEDGE_MAX_OUTPUT_TOKENS = 16_384


class KnowledgeParseStatus(StrEnum):
    UNPARSED = "unparsed"
    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"
    CANCELLED = "cancelled"


class KnowledgeDocumentType(StrEnum):
    DOCUMENT = "document"
    MAIN = "main"
    CHILD = "child"


class KnowledgeCleanupJob(SQLModel, TimeMixin, table=True):
    """Durable outbox entry for resources that live outside PostgreSQL."""

    __tablename__ = "knowledge_cleanup_job"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # Deliberately not a foreign key: the cleanup entry must survive user deletion.
    user_id: uuid.UUID = Field(index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    status: str = Field(default="pending", max_length=16, index=True)
    attempts: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class KnowledgeParserBinding(SQLModel, TimeMixin, table=True):
    """One user-level provider/model binding used by all knowledge topics."""

    __tablename__ = "knowledge_parser_binding"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", unique=True, index=True)
    provider_id: uuid.UUID | None = Field(default=None, foreign_key="llmprovider.id", ondelete="SET NULL", index=True)
    model_name: str = Field(max_length=255)
    context_window_tokens: int = Field(
        default=DEFAULT_KNOWLEDGE_CONTEXT_WINDOW_TOKENS,
        ge=4_096,
        le=2_000_000,
    )
    max_output_tokens: int = Field(
        default=DEFAULT_KNOWLEDGE_MAX_OUTPUT_TOKENS,
        ge=256,
        le=256_000,
    )


class KnowledgeSource(SQLModel, TimeMixin, table=True):
    """One user-owned topic containing one or more Markdown source files."""

    __tablename__ = "knowledge_source"
    __table_args__ = (Index("ix_knowledge_source_user_updated", "user_id", "updated_time"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    title: str = Field(max_length=255)
    background: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    source_revision: int = Field(default=1, ge=1)
    parse_status: str = Field(default=KnowledgeParseStatus.UNPARSED.value, max_length=24)
    active_parse_run_id: uuid.UUID | None = Field(default=None, index=True)
    last_error_code: str | None = Field(default=None, max_length=64)
    last_error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class KnowledgeSourceFile(SQLModel, TimeMixin, table=True):
    """One independently versioned Markdown source file inside a topic."""

    __tablename__ = "knowledge_source_file"
    __table_args__ = (
        UniqueConstraint("source_id", "original_filename", name="uq_knowledge_source_file_name"),
        Index("ix_knowledge_source_file_source_order", "source_id", "sort_order"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_id: uuid.UUID = Field(foreign_key="knowledge_source.id", ondelete="CASCADE", index=True)
    original_filename: str = Field(max_length=255)
    content_version: int = Field(default=1, ge=1)
    object_key: str = Field(max_length=1024)
    content_hash: str = Field(max_length=64)
    content_size: int = Field(ge=1)
    sort_order: int = Field(default=0, ge=0)


class KnowledgeParsePlan(SQLModel, TimeMixin, table=True):
    """One visible extraction plan against an immutable topic revision."""

    __tablename__ = "knowledge_parse_plan"
    __table_args__ = (Index("ix_knowledge_parse_plan_source_created", "source_id", "created_time"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_id: uuid.UUID = Field(foreign_key="knowledge_source.id", ondelete="CASCADE", index=True)
    source_revision: int = Field(ge=1)
    source_snapshot: list[dict] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    status: str = Field(default=KnowledgeParseStatus.PENDING.value, max_length=24)
    progress: int = Field(default=0, ge=0, le=100)
    stage: str = Field(default="queued", max_length=64)
    message: str = Field(default="", max_length=500)
    parser_provider_id: uuid.UUID | None = Field(default=None)
    parser_provider_name: str | None = Field(default=None, max_length=255)
    parser_model: str | None = Field(default=None, max_length=255)
    interaction_mode: str = Field(default="collaborative", max_length=16)
    pending_question: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    plan_object_key: str | None = Field(default=None, max_length=1024)
    container_id: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class KnowledgeParseRun(SQLModel, TimeMixin, table=True):
    """One immutable parser attempt against a pinned multi-file topic revision."""

    __tablename__ = "knowledge_parse_run"
    __table_args__ = (Index("ix_knowledge_parse_run_source_created", "source_id", "created_time"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_id: uuid.UUID = Field(foreign_key="knowledge_source.id", ondelete="CASCADE", index=True)
    source_revision: int = Field(ge=1)
    source_snapshot: list[dict] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    status: str = Field(default=KnowledgeParseStatus.PENDING.value, max_length=24)
    progress: int = Field(default=0, ge=0, le=100)
    stage: str = Field(default="queued", max_length=64)
    message: str = Field(default="", max_length=500)
    parser_name: str = Field(default="claude-agent-sdk", max_length=64)
    parser_provider_id: uuid.UUID | None = Field(default=None)
    parser_provider_name: str | None = Field(default=None, max_length=255)
    parser_model: str | None = Field(default=None, max_length=255)
    parse_mode: str = Field(default="direct", max_length=16)
    plan_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="knowledge_parse_plan.id",
        ondelete="SET NULL",
        index=True,
    )
    plan_strategy_id: str | None = Field(default=None, max_length=128)
    parent_run_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="knowledge_parse_run.id",
        ondelete="SET NULL",
        index=True,
    )
    session_owner_kind: str = Field(default="run", max_length=8)
    session_owner_id: uuid.UUID | None = Field(default=None, index=True)
    skill_name: str = Field(default="document-knowledge-organizer", max_length=128)
    skill_version: str = Field(default="1", max_length=64)
    manifest_object_key: str | None = Field(default=None, max_length=1024)
    generated_memory_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    generated_memory_operations: dict[str, str] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    inserted_document_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    container_id: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    @property
    def can_refine(self) -> bool:
        return (
            self.status == KnowledgeParseStatus.READY.value
            and bool(self.manifest_object_key)
            and not self.inserted_document_ids
            and not self.generated_memory_ids
        )


class KnowledgeDocument(SQLModel, TimeMixin, table=True):
    """Metadata for one parsed Markdown document.

    ``main``/``child`` remain valid for already parsed topics. New parser runs
    store flat ``document`` rows and leave ``parent_id`` empty.
    """

    __tablename__ = "knowledge_document"
    __table_args__ = (
        UniqueConstraint("parse_run_id", "sort_order", name="uq_knowledge_document_run_order"),
        Index("ix_knowledge_document_source_order", "source_id", "sort_order"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_id: uuid.UUID = Field(foreign_key="knowledge_source.id", ondelete="CASCADE", index=True)
    parse_run_id: uuid.UUID = Field(foreign_key="knowledge_parse_run.id", ondelete="CASCADE", index=True)
    parent_id: uuid.UUID | None = Field(default=None, foreign_key="knowledge_document.id", ondelete="CASCADE")
    document_type: str = Field(max_length=16)
    title: str = Field(max_length=255)
    object_key: str = Field(max_length=1024)
    content_version: int = Field(default=1, ge=1)
    content_hash: str = Field(max_length=64)
    content_size: int = Field(ge=1)
    estimated_tokens: int = Field(default=1, ge=1)
    sort_order: int = Field(default=0, ge=0)
    user_modified: bool = Field(default=False)
