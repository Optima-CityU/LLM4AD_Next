"""Versioned regional editing contracts shared by the UI and runtime bridge."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

PaperRegionRequestState = Literal["queued", "dispatched", "processing", "candidate", "applied", "rejected", "stale"]


class PaperRegionSourceRef(BaseModel):
    """Owned original evidence, with explicit verification for legacy references."""

    review_id: UUID | None = None
    version: str | None = None
    quote: str | None = None
    path: str | None = None
    status: str = "unverified"


class PaperRegion(BaseModel):
    """One independently editable textual block and its optimistic revision."""

    id: str
    kind: str
    title: str
    content: str
    revision: str
    review_id: UUID | None = None
    source_refs: list[PaperRegionSourceRef] = Field(default_factory=list)


class PaperRegionSelection(BaseModel):
    """Exact selected substring, using browser UTF-16 code-unit offsets."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    original_text: str = Field(min_length=1, max_length=1_000_000)

    @model_validator(mode="after")
    def validate_range(self) -> "PaperRegionSelection":
        """Reject empty and reversed selection ranges."""
        if self.end <= self.start:
            raise ValueError("Selection end must follow its start")
        return self


class PaperRegionPatch(BaseModel):
    """Author edit guarded by the exact region revision."""

    model_config = ConfigDict(extra="forbid")

    base_revision: str = Field(min_length=1, max_length=128)
    content: str = Field(max_length=1_000_000)


class PaperRegionRequestCreate(BaseModel):
    """Durable request to the existing CloudCLI conversation."""

    model_config = ConfigDict(extra="forbid")

    base_revision: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=50_000)
    answer: str | None = Field(default=None, max_length=100_000)
    selection: PaperRegionSelection | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)


class PaperRegionCandidate(BaseModel):
    """Runtime-only candidate publication that never applies its replacement."""

    model_config = ConfigDict(extra="forbid")

    replacement_text: str = Field(max_length=1_000_000)
    reason: str | None = Field(default=None, max_length=20_000)


class PaperRegionApply(BaseModel):
    """Author approval guarded by the candidate's original region revision."""

    model_config = ConfigDict(extra="forbid")

    base_revision: str = Field(min_length=1, max_length=128)


class PaperRegionRequest(BaseModel):
    """Persisted request, including its diff and precise reusable agent prompt."""

    id: UUID
    region_id: str
    state: PaperRegionRequestState
    instruction: str
    answer: str | None = None
    base_revision: str
    original_text: str
    replacement_text: str | None = None
    message: str | None = None
    created_at: datetime
    prompt: str


class PaperRegionSnapshot(BaseModel):
    """Current editable regions and durable request queue/history."""

    regions: list[PaperRegion]
    requests: list[PaperRegionRequest]
