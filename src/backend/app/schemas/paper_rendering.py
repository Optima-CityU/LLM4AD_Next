"""Immutable proposal render results and owner-authenticated artifact links."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class PaperRenderDiagnostic(BaseModel):
    """An actual conversion or compilation diagnostic, never an editorial gate."""

    message: str
    path: str | None = None
    line: int | None = Field(default=None, ge=1)
    severity: Literal["error", "warning", "info"] = "error"


class PaperRenderFile(BaseModel):
    """One persisted generated artifact served through an authenticated route."""

    path: str
    mime_type: str
    url: str


class PaperRenderRequest(BaseModel):
    """Author-confirmed ordered inputs and the source snapshot being approved."""

    entry_paths: list[str] | None = None
    expected_source_hash: str | None = None


class PaperRenderResponse(BaseModel):
    """Build outputs tied to one immutable source revision and converter build."""

    status: Literal["ready", "failed"]
    source_version_id: uuid.UUID
    source_hash: str
    entry_path: str
    entry_paths: list[str] = Field(default_factory=list)
    diagnostics: list[PaperRenderDiagnostic] = Field(default_factory=list)
    files: list[PaperRenderFile] = Field(default_factory=list)
    pdf_url: str | None = None
    typst_url: str | None = None
