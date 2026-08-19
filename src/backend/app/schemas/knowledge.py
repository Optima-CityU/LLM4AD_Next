"""Request and response schemas for the multi-document knowledge library."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.knowledge import (
    DEFAULT_KNOWLEDGE_CONTEXT_WINDOW_TOKENS,
    DEFAULT_KNOWLEDGE_MAX_OUTPUT_TOKENS,
)
from app.schemas.memory import MemoryCardResponse


class KnowledgeSourceFileSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    original_filename: str
    content_version: int
    content_size: int
    sort_order: int
    updated_time: datetime


class KnowledgeDocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    parse_run_id: uuid.UUID
    parent_id: uuid.UUID | None
    document_type: Literal["document", "main", "child"]
    title: str
    content_version: int
    content_size: int
    estimated_tokens: int
    sort_order: int
    user_modified: bool
    updated_time: datetime


class KnowledgeSourceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    background: str | None = None
    source_revision: int
    source_file_count: int
    source_size: int
    parse_status: Literal["unparsed", "pending", "running", "ready", "stale", "failed"]
    active_parse_run_id: uuid.UUID | None
    last_error_code: str | None
    last_error: str | None
    created_time: datetime
    updated_time: datetime


class KnowledgeSourceDetail(KnowledgeSourceSummary):
    source_files: list[KnowledgeSourceFileSummary] = Field(default_factory=list)
    documents: list[KnowledgeDocumentSummary] = Field(default_factory=list)


class KnowledgeSourceListResponse(BaseModel):
    items: list[KnowledgeSourceSummary]
    total: int


class KnowledgeContentResponse(BaseModel):
    content: str
    content_version: int
    content_hash: str


class KnowledgeSourceCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class KnowledgeSourceUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    background: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def require_a_change(self):
        if not ({"title", "background"} & self.model_fields_set):
            raise ValueError("title or background is required")
        return self


class KnowledgeSourceFileUpdateRequest(BaseModel):
    original_filename: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=20 * 1024 * 1024)


class KnowledgeDocumentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1, max_length=20 * 1024 * 1024)


class KnowledgeDocumentInsertRequest(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def require_unique_document_ids(self):
        if len(self.document_ids) != len(set(self.document_ids)):
            raise ValueError("document_ids must be unique")
        return self


class KnowledgeDocumentInsertResponse(BaseModel):
    inserted_document_ids: list[uuid.UUID] = Field(default_factory=list)
    generated_memory_ids: list[str] = Field(default_factory=list)
    generated_memories: list[MemoryCardResponse] = Field(default_factory=list)


class KnowledgeParseStartRequest(BaseModel):
    background: str | None = Field(default=None, max_length=8000)
    instruction: str | None = Field(default=None, max_length=8000)
    mode: Literal["direct"] = "direct"


class KnowledgeParseRefineRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=8000)


class KnowledgeParsePlanCreateRequest(BaseModel):
    background: str | None = Field(default=None, max_length=8000)
    interaction_mode: Literal["collaborative"] = "collaborative"


class KnowledgeParsePlanQuestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=400)


class KnowledgeParsePlanQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    header: str = Field(min_length=1, max_length=40)
    options: list[KnowledgeParsePlanQuestionOption] = Field(min_length=2, max_length=4)
    multiSelect: bool = False


class KnowledgeParsePlanPendingQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=128)
    questions: list[KnowledgeParsePlanQuestion] = Field(min_length=1, max_length=3)


class KnowledgeParsePlanAnswerRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=128)
    answers: dict[str, str] = Field(min_length=1, max_length=3)


class KnowledgeParsePlanSourceOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=120)
    key_sections: list[str] = Field(default_factory=list, max_length=5)


class KnowledgeParsePlanDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    # Kept as an optional compatibility field for plans generated before the
    # knowledge library switched to flat documents.
    document_type: Literal["main", "child"] | None = None
    purpose: str = Field(min_length=1, max_length=100)
    source_coverage: list[str] = Field(min_length=1, max_length=5)
    must_preserve: list[str] = Field(default_factory=list, max_length=3)


class KnowledgeParsePlanStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=200)
    loss_level: Literal["lossless", "light", "lossy"]
    document_count: int = Field(ge=1, le=20)
    documents: list[KnowledgeParsePlanDocument] = Field(min_length=1, max_length=20)
    deduplication_policy: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_document_count(self):
        if self.document_count != len(self.documents):
            raise ValueError("document_count must match documents length")
        return self


class KnowledgeParsePlanPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_summary: str = Field(min_length=1, max_length=300)
    source_overview: list[KnowledgeParsePlanSourceOverview] = Field(default_factory=list, max_length=20)
    recommended_strategy_id: str = Field(min_length=1, max_length=128)
    strategies: list[KnowledgeParsePlanStrategy] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_recommended_strategy(self):
        ids = [item.id for item in self.strategies]
        if len(ids) != len(set(ids)):
            raise ValueError("strategy ids must be unique")
        if self.recommended_strategy_id not in ids:
            raise ValueError("recommended_strategy_id must reference a strategy")
        return self


class KnowledgeParsePlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    source_revision: int
    status: Literal["pending", "running", "ready", "stale", "failed", "cancelled"]
    progress: int
    stage: str
    message: str
    parser_provider_name: str | None
    parser_model: str | None
    interaction_mode: Literal["quick", "collaborative"]
    pending_question: KnowledgeParsePlanPendingQuestion | None = None
    payload: KnowledgeParsePlanPayload | None = None
    error_code: str | None
    error: str | None
    retryable: bool = False
    retry_action: Literal["persist"] | None = None
    stream_cursor: str | None = None
    created_time: datetime
    updated_time: datetime


class KnowledgeParseRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    source_revision: int
    status: Literal["pending", "running", "ready", "stale", "failed", "cancelled"]
    progress: int
    stage: str
    message: str
    parser_name: str
    parser_provider_name: str | None
    parser_model: str | None
    parse_mode: Literal["direct", "planned", "refine"]
    plan_id: uuid.UUID | None
    plan_strategy_id: str | None
    parent_run_id: uuid.UUID | None
    session_owner_kind: Literal["plan", "run"]
    session_owner_id: uuid.UUID | None
    can_refine: bool = False
    generated_memory_ids: list[str] = Field(default_factory=list)
    inserted_document_ids: list[str] = Field(default_factory=list)
    skill_name: str
    skill_version: str
    error_code: str | None
    error: str | None
    stream_cursor: str | None = None
    created_time: datetime
    updated_time: datetime


class KnowledgeParseCancelResponse(BaseModel):
    id: uuid.UUID
    status: Literal["cancelled"]
    message: str


class KnowledgeParserBindingUpdate(BaseModel):
    provider_id: uuid.UUID
    model_name: str = Field(min_length=1, max_length=255)
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

    @model_validator(mode="after")
    def validate_token_limits(self):
        if self.max_output_tokens > self.context_window_tokens:
            raise ValueError("max_output_tokens must not exceed context_window_tokens")
        return self


class KnowledgeParserBindingResponse(BaseModel):
    configured: bool
    provider_id: uuid.UUID | None = None
    provider_name: str | None = None
    provider_type: str | None = None
    model_name: str | None = None
    context_window_tokens: int = DEFAULT_KNOWLEDGE_CONTEXT_WINDOW_TOKENS
    max_output_tokens: int = DEFAULT_KNOWLEDGE_MAX_OUTPUT_TOKENS
    error_code: str | None = None
    message: str = ""
