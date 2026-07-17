"""Memory backend request/response schemas."""

from typing import Any
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryTestRequest(BaseModel):
    """Connectivity test request for the configured memory backend."""

    type: str = "local_yaml"
    mindmemos_base_url: str = ""
    mindmemos_api_key: str = ""
    mindmemos_user_id: str = ""
    mindmemos_app_id: str = "llm4ad"
    mindmemos_agent_id: str = "planner"
    mindmemos_session_id: str = ""
    mindmemos_project_id: str = ""
    run_search_probe: bool = False


class MemoryTestResponse(BaseModel):
    """Connectivity test response for memory backend checks."""

    ok: bool
    message: str
    backend_type: str
    base_url: str | None = None
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MemoryHealthResponse(BaseModel):
    """System MindMemOS health response for the frontend."""

    ok: bool
    message: str
    system_runtime_available: bool
    system_enabled: bool
    system_chat_configured: bool
    system_embedding_configured: bool
    system_api_key_configured: bool
    system_rerank_enabled: bool = False
    system_rerank_configured: bool = False
    service_reachable: bool = False
    auth_ok: bool = False
    error_code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MemoryProviderBindingUpdate(BaseModel):
    """Bind the current user memory space to existing provider configs."""

    chat_provider_id: uuid.UUID
    chat_model: str
    embedding_provider_id: uuid.UUID


class MemoryProviderBindingResponse(BaseModel):
    """Current MindMemOS provider binding state for a user memory space."""

    configured: bool = False
    binding_id: str | None = None
    project_id: str
    user_id: uuid.UUID
    chat_provider_id: uuid.UUID | None = None
    chat_model: str | None = None
    embedding_provider_id: uuid.UUID | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None
    embedding_locked: bool = False
    message: str = ""


class MemoryCardReadonlyInfo(BaseModel):
    """MindMemOS-managed fields shown as read-only details in the UI."""

    source: str = "mindmemos"
    status: str = "active"
    entity_name: str | None = None
    property_name: str | None = None
    property_time: str | None = None
    last_update_at: str | None = None
    event_time: str | None = None
    source_timestamp: str | None = None


class MemoryCardResponse(BaseModel):
    """MindMemOS memory item mapped for the LLM4AD memory UI."""

    id: str
    type: str
    title: str
    content: str
    enabled: bool = True
    source: str = "static"
    tags: list[str] = Field(default_factory=list)
    score: float | None = None
    generation: int | None = None
    algorithm_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    readonly: MemoryCardReadonlyInfo = Field(default_factory=MemoryCardReadonlyInfo)


class MemoryCardUpsertRequest(BaseModel):
    """Create or update a MindMemOS memory from user-provided text."""

    id: str | None = None
    type: str = "general_insight"
    title: str = ""
    content: str
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    score: float | None = None
    generation: int | None = None
    algorithm_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryCardStatusUpdate(BaseModel):
    """Update whether a MindMemOS memory can be injected."""

    enabled: bool


class MemoryCardExtractionRequest(BaseModel):
    """Generate MindMemOS memory previews from a raw user description."""

    content: str = Field(min_length=1, max_length=20000)
    prompt_language: Literal["ZH", "EN"] | None = None


class MemoryCardExtractionResponse(BaseModel):
    """Preview memories extracted by MindMemOS before the user confirms them."""

    preview_id: str
    items: list[MemoryCardResponse]
    message: str = ""


class MemoryCardExtractionCommitRequest(BaseModel):
    """Confirm which extracted preview memories should become active."""

    selected_ids: list[str] = Field(default_factory=list)
    all_ids: list[str] = Field(default_factory=list)


class MemoryCardExtractionDiscardRequest(BaseModel):
    """Discard temporary extracted preview memories."""

    memory_ids: list[str] = Field(default_factory=list)


class TaskMemoryPromotionRequest(BaseModel):
    """Promote selected task-memory cards into project-memory previews."""

    project_id: uuid.UUID
    task_id: uuid.UUID
    memory_ids: list[str] = Field(min_length=1, max_length=50)
    prompt_language: Literal["ZH", "EN"] | None = None


class MemoryCardPageResponse(BaseModel):
    """Paged MindMemOS memory list response."""

    items: list[MemoryCardResponse]
    page: int
    page_size: int
    total: int | None = None
    has_more: bool = False


MemoryScope = Literal["user", "project", "task"]


class PinnedMemoryResponse(BaseModel):
    """Current pinned shared-memory ids for a running task (manual mode)."""

    task_id: uuid.UUID
    pinned_card_ids: list[str] = Field(default_factory=list)


class PinnedMemoryUpdate(BaseModel):
    """Replace the pinned shared-memory id set for a task."""

    pinned_card_ids: list[str] = Field(default_factory=list)


class MemoryConfigUpdate(BaseModel):
    """Update request for user/project memory defaults."""

    enabled: bool | None = None
    include_user_memory: bool | None = None
    include_project_memory: bool | None = None
    include_task_memory: bool | None = None
    user_memory_limit: int | None = Field(default=None, ge=0)
    project_memory_limit: int | None = Field(default=None, ge=0)
    task_memory_limit: int | None = Field(default=None, ge=0)
    retrieval_mode: Literal["auto", "manual"] | None = None
    pinned_card_ids: list[str] | None = None
    task_injection_mode: Literal["topk", "weight", "random"] | None = None
    mindmemos_search_strategy: str | None = None
    mindmemos_rerank: bool | None = None
    mindmemos_score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    mindmemos_fail_open: bool | None = None


class MemoryConfigResponse(BaseModel):
    """User/project memory defaults response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_time: datetime
    updated_time: datetime
    enabled: bool
    include_user_memory: bool
    include_project_memory: bool
    include_task_memory: bool
    user_memory_limit: int
    project_memory_limit: int
    task_memory_limit: int
    retrieval_mode: str = "auto"
    pinned_card_ids: list[str] = Field(default_factory=list)
    task_injection_mode: str = "topk"
    mindmemos_search_strategy: str
    mindmemos_rerank: bool
    mindmemos_score_threshold: float | None
    mindmemos_fail_open: bool
    mindmemos_binding_id: str | None = None
    mindmemos_chat_provider_id: uuid.UUID | None = None
    mindmemos_chat_model: str | None = None
    mindmemos_embedding_provider_id: uuid.UUID | None = None
    mindmemos_embedding_model: str | None = None
    mindmemos_embedding_dim: int | None = None
    system_enabled: bool = False
    system_base_url: str = ""
    system_api_key_configured: bool = False
    system_chat_configured: bool = False
    system_embedding_configured: bool = False
    system_embedding_dimensions: int | None = None
    system_rerank_enabled: bool = False
    system_rerank_configured: bool = False
    system_runtime_available: bool = False


class UserMemoryConfigResponse(MemoryConfigResponse):
    """User-level memory defaults response."""

    user_id: uuid.UUID


class ProjectMemoryConfigResponse(MemoryConfigResponse):
    """Project-level memory defaults response."""

    project_id: uuid.UUID
