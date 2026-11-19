"""Public contracts for complete paper workspace revision history."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PaperHistoryRevision(BaseModel):
    """One immutable capture or an identifiable recovered legacy record."""

    id: str
    label: str
    created_time: datetime
    origin: str
    summary: str
    source_version_id: uuid.UUID | None = None
    state_hash: str
    is_current: bool = False


class PaperHistoryResponse(BaseModel):
    """Owned revisions and the optimistic concurrency token for current content."""

    revisions: list[PaperHistoryRevision]
    current_revision_id: str | None
    current_state_hash: str


class PaperHistoryComparedFile(BaseModel):
    """Byte-level comparison metadata for one namespaced file."""

    path: str
    status: Literal["added", "modified", "deleted", "unchanged"]
    mime_type: str
    binary: bool
    before_size: int = 0
    after_size: int = 0


class PaperHistoryComparison(BaseModel):
    """A complete file inventory suitable for text and binary comparisons."""

    base_id: str
    target_id: str
    files: list[PaperHistoryComparedFile]
    counts: dict[str, int]


class PaperHistoryFile(BaseModel):
    """Exact UTF-8 text or an authenticated download for binary content."""

    path: str
    content: str | None
    binary: bool
    mime_type: str
    download_url: str | None
    exists: bool


class PaperHistoryRestoreRequest(BaseModel):
    """Restore only against the complete content state the author observed."""

    expected_state_hash: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    expected_revision_id: str | None = Field(default=None, min_length=1, max_length=255)
