"""Author-facing regional editing routes; candidate publication stays internal."""

import uuid

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.api.deps import CurrentUser, SessionDep
from app.schemas import paper_regions as schemas
from app.services import paper_regions

router = APIRouter(prefix="/papers/workspaces/{workspace_id}", tags=["paper-regions"])


@router.get("/regions", response_model=schemas.PaperRegionSnapshot)
def get_regions(workspace_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    """Return canonical regions and durable requests for the owned workspace."""
    return paper_regions.get_snapshot(db, current_user, workspace_id)


@router.patch("/regions/{region_id:path}", response_model=schemas.PaperRegion)
def patch_region(
    workspace_id: uuid.UUID,
    region_id: str,
    request: schemas.PaperRegionPatch,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Save a direct author edit against its exact base revision."""
    return paper_regions.patch_region(db, current_user, workspace_id, region_id, request)


@router.post("/regions/{region_id:path}/requests", response_model=schemas.PaperRegionRequest)
def create_request(
    workspace_id: uuid.UUID,
    region_id: str,
    request: schemas.PaperRegionRequestCreate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Queue an idempotent instruction for the existing CloudCLI conversation."""
    return paper_regions.create_request(db, current_user, workspace_id, region_id, request)


@router.post("/region-requests/{request_id}/dispatch", response_model=schemas.PaperRegionRequest)
def dispatch_request(workspace_id: uuid.UUID, request_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    """Record acceptance by the embedded conversation without dispatching twice."""
    return paper_regions.dispatch_request(db, current_user, workspace_id, request_id)


@router.post("/region-requests/{request_id}/apply", response_model=schemas.PaperRegionRequest)
def apply_request(
    workspace_id: uuid.UUID,
    request_id: uuid.UUID,
    request: schemas.PaperRegionApply,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Apply only the author's explicitly approved current regional diff."""
    return paper_regions.apply_request(db, current_user, workspace_id, request_id, request)


@router.post("/region-requests/{request_id}/reject", response_model=schemas.PaperRegionRequest)
def reject_request(workspace_id: uuid.UUID, request_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    """Keep the target unchanged while retaining the rejected request history."""
    return paper_regions.reject_request(db, current_user, workspace_id, request_id)


@router.get("/region-review-sources/{review_id}/{version}", response_class=PlainTextResponse)
def get_review_source(
    workspace_id: uuid.UUID, review_id: uuid.UUID, version: str, db: SessionDep, current_user: CurrentUser
):
    """Serve an owned immutable original for a clickable evidence reference."""
    return PlainTextResponse(
        paper_regions.get_review_source(db, current_user, workspace_id, review_id, version),
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
