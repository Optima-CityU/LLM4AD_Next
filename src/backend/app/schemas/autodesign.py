"""Public AutoDesign sessions and the workspace-scoped Agent tool envelope."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.paper import PaperWorkspaceSummary
from app.schemas.task import TaskResponse

FileArea = Literal["input", "run", "draft", "references", "analysis"]
Operation = Literal[
    "context", "rename", "list_files", "read_file", "create_child", "patch_child", "validate", "publish", "start", "stop",
    "memory_context", "memory_list", "memory_save", "sync_draft",
]


class AutoDesignSessionCreate(BaseModel):
    """Start an independent design without requiring uploaded materials."""

    title: str = Field(default="AutoDesign", min_length=1, max_length=255)
    project_id: uuid.UUID | None = None


class AutoDesignSessionResponse(PaperWorkspaceSummary):
    """Native model settings plus the associated task and verified project."""

    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    root_task_id: uuid.UUID | None = None
    validation_report: dict[str, Any] = Field(default_factory=dict)
    project_ready: bool = False
    project_revision: str | None = None
    project_name: str | None = None
    published_task_id: uuid.UUID | None = None


class AutoDesignSessionList(BaseModel):
    """Current user's design conversations."""

    items: list[AutoDesignSessionResponse]
    total: int


class AutoDesignDraftProject(BaseModel):
    """One real editable project and its redacted configuration preview."""

    path: str
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    config_error: str | None = None


class AutoDesignTaskContext(BaseModel):
    """Live, sanitized task state and independently runnable family members."""

    title: str = "AutoDesign"
    task: TaskResponse | None = None
    root_task_id: uuid.UUID | None = None
    family: list[TaskResponse] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)
    draft_path: str = "/workspace/source/draft"
    validation_report: dict[str, Any] = Field(default_factory=dict)
    project_ready: bool = False
    projects: list[AutoDesignDraftProject] = Field(default_factory=list)
    selected_project_path: str | None = None
    validation_current: bool = False
    editing_locked: bool = False
    running_task_id: uuid.UUID | None = None
    draft_conflicts: list[str] = Field(default_factory=list)


class AutoDesignFile(BaseModel):
    """One relative path available to a scoped file reader."""

    path: str
    size: int = 0
    modified_time: datetime | None = None
    title: str | None = None


class AutoDesignFileList(BaseModel):
    """Files from task inputs, runs, drafts, references, or design records."""

    area: FileArea
    task_id: uuid.UUID | None = None
    files: list[AutoDesignFile]


class AutoDesignFileResponse(BaseModel):
    """File text with an explicit optional line range rather than silent truncation."""

    area: FileArea
    path: str
    content: str
    total_lines: int
    start_line: int
    end_line: int
    revision: str | None = None
    editable: bool = False


class AutoDesignFileUpdate(BaseModel):
    """Save an existing draft only if its source revision still matches."""

    path: str
    content: str
    revision: str


class AutoDesignPublishRequest(BaseModel):
    """Prepare the current verified project or update an unstarted version."""

    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=255)
    enable_memory: bool | None = None


class AutoDesignPublishResponse(BaseModel):
    """The prepared task and its associated conversation."""

    session: AutoDesignSessionResponse
    task: TaskResponse


class AutoDesignOperationRequest(BaseModel):
    """Task action explicitly requested from the task page or Agent."""

    operation: Operation
    task_id: uuid.UUID | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class AutoDesignRuntimeRequest(AutoDesignOperationRequest):
    """Internal MCP envelope authorized using the existing runtime token."""

    workspace_id: uuid.UUID
