"""Owner-authenticated rendering and immutable build artifact downloads."""

import uuid

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, SessionDep
from app.schemas.paper_rendering import PaperRenderRequest, PaperRenderResponse
from app.services import paper_rendering

router = APIRouter(prefix="/papers", tags=["papers"])


@router.post("/source-versions/{source_version_id}/render", response_model=PaperRenderResponse)
def render_source_version(
    source_version_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    body: PaperRenderRequest | None = None,
    entry_path: str | None = Query(default=None),
) -> PaperRenderResponse:
    """Build PDF and Typst for the selected owned source revision."""
    return paper_rendering.render_source_version(
        db,
        current_user,
        source_version_id,
        entry_path=entry_path,
        entry_paths=body.entry_paths if body else None,
        expected_source_hash=body.expected_source_hash if body else None,
    )


@router.get("/source-versions/{source_version_id}/render", response_model=PaperRenderResponse | None)
def get_source_version_render(
    source_version_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    entry_path: str | None = Query(default=None),
    entry_paths: list[str] | None = Query(default=None),
) -> PaperRenderResponse | None:
    """Read an existing matching render without creating outputs or history."""
    return paper_rendering.get_source_version_render(
        db, current_user, source_version_id, entry_path=entry_path, entry_paths=entry_paths
    )


@router.get("/source-versions/{source_version_id}/render/{filename}", response_class=StreamingResponse)
def rendered_source_file(
    source_version_id: uuid.UUID,
    filename: str,
    db: SessionDep,
    current_user: CurrentUser,
) -> StreamingResponse:
    """Stream a persisted build without exposing object-store addresses."""
    body, mime_type = paper_rendering.rendered_source_file(db, current_user, source_version_id, filename)
    return StreamingResponse(
        body,
        media_type=mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
