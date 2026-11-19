"""Optimistic source history restoration requests."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class PaperSourceRestoreRequest(BaseModel):
    """Restore a snapshot only against the source currently shown to its owner."""

    expected_source_version_id: uuid.UUID | None = Field(...)
    expected_content_hash: str | None = None


class PaperRevisionRejectResponse(BaseModel):
    """Terminal decision for an owned, unapplied source revision candidate."""

    candidate_id: uuid.UUID
    status: Literal["rejected"]
