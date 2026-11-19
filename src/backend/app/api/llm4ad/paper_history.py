"""Owned source decisions and complete workspace revision history."""

import uuid
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, SessionDep
from app.schemas.paper import PaperSourceVersionResponse
from app.schemas.paper_history import PaperRevisionRejectResponse, PaperSourceRestoreRequest
from app.schemas.paper_revision_history import (
    PaperHistoryComparison,
    PaperHistoryFile,
    PaperHistoryResponse,
    PaperHistoryRestoreRequest,
    PaperHistoryRevision,
)
from app.services import paper_history, paper_revision_history, paper_service

router = APIRouter(prefix="/papers", tags=["papers"])


@router.post("/source-versions/{source_version_id}/restore", response_model=PaperSourceVersionResponse)
def restore_source_version(
    source_version_id: uuid.UUID,
    request: PaperSourceRestoreRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Copy an owned historical bundle into a new active source version."""
    return paper_service.restore_source_version(
        db,
        current_user,
        source_version_id,
        expected_source_version_id=request.expected_source_version_id,
        expected_content_hash=request.expected_content_hash,
    )


@router.post("/revision-candidates/{candidate_id}/reject", response_model=PaperRevisionRejectResponse)
def reject_revision_candidate(
    candidate_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Discard an owned pending candidate without modifying its source."""
    return paper_history.reject_revision_candidate(db, current_user, candidate_id)


@router.get("/workspaces/{workspace_id}/history", response_model=PaperHistoryResponse)
def get_revision_history(workspace_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    """List complete captures and recoverable prior source and output records."""
    return paper_revision_history.get_history(db, current_user, workspace_id)


@router.get("/workspaces/{workspace_id}/history/compare", response_model=PaperHistoryComparison)
def compare_revision_history(
    workspace_id: uuid.UUID, base_id: str, target_id: str, db: SessionDep, current_user: CurrentUser
):
    """Compare two owned revisions without treating binary files as text."""
    return paper_revision_history.compare_revisions(db, current_user, workspace_id, base_id, target_id)


@router.get("/workspaces/{workspace_id}/history/{revision_id}/file", response_model=PaperHistoryFile)
def get_revision_file(workspace_id: uuid.UUID, revision_id: str, path: str, db: SessionDep, current_user: CurrentUser):
    """Read one exact historical text file or identify an absent comparison side."""
    return paper_revision_history.get_file(db, current_user, workspace_id, revision_id, path)


@router.get("/workspaces/{workspace_id}/history/{revision_id}/download")
def download_revision_file(
    workspace_id: uuid.UUID, revision_id: str, path: str, db: SessionDep, current_user: CurrentUser
):
    """Download an owned immutable file through the authenticated backend."""
    content, mime = paper_revision_history.download_file(db, current_user, workspace_id, revision_id, path)
    filename = quote(PurePosixPath(path).name, safe="")
    return StreamingResponse(
        iter([content]),
        media_type=mime,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/workspaces/{workspace_id}/history/{revision_id}/restore", response_model=PaperHistoryRevision)
def restore_workspace_revision(
    workspace_id: uuid.UUID,
    revision_id: str,
    request: PaperHistoryRestoreRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Copy historical content forward only against the complete observed state."""
    return paper_revision_history.restore_revision(db, current_user, workspace_id, revision_id, request)
