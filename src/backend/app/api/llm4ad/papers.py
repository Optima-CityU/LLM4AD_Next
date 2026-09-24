"""Paper optimization workspace API."""

import uuid

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from app.api.deps import CurrentUser, SessionDep
from app.schemas import paper as schemas
from app.services import paper_service

router = APIRouter(prefix="/papers", tags=["llm4ad.papers"])


@router.post(
    "/workspaces",
    response_model=schemas.PaperWorkspaceSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace(
    request: schemas.PaperWorkspaceCreate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Create an independent research workspace."""
    return paper_service.create_workspace(db, current_user, request)


@router.get("/workspaces", response_model=schemas.PaperWorkspaceList)
def list_workspaces(
    db: SessionDep,
    current_user: CurrentUser,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    search: str | None = Query(default=None, max_length=200),
):
    """List current user's paper workspaces."""
    return paper_service.list_workspaces(
        db,
        current_user,
        skip=skip,
        limit=limit,
        search=search,
    )


@router.get("/workspaces/{workspace_id}", response_model=schemas.PaperWorkspaceDetail)
def get_workspace(
    workspace_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Return the current paper page snapshot."""
    return paper_service.get_workspace_detail(db, current_user, workspace_id)


@router.patch("/workspaces/{workspace_id}", response_model=schemas.PaperWorkspaceSummary)
def update_workspace(
    workspace_id: uuid.UUID,
    request: schemas.PaperWorkspaceUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Update paper workspace metadata."""
    return paper_service.update_workspace(db, current_user, workspace_id, request)


@router.post(
    "/workspaces/{workspace_id}/workflow-stage/invalidate",
    response_model=schemas.PaperWorkspaceSummary,
)
def invalidate_workflow_stage(
    workspace_id: uuid.UUID,
    request: schemas.PaperStageInvalidateRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Mark the stage revision replaced by an edited conversation as stale."""
    return paper_service.invalidate_workflow_stage(
        db,
        current_user,
        workspace_id,
        request,
    )


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(
    workspace_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
) -> Response:
    """Delete a paper workspace and schedule RustFS cleanup."""
    paper_service.delete_workspace(db, current_user, workspace_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workspaces/{workspace_id}/sources",
    response_model=schemas.PaperSourceVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_source(
    workspace_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    files: list[UploadFile] = File(...),
    relative_paths: list[str] | None = Form(default=None),
):
    """Upload an immutable Markdown/LaTeX source tree while preserving paths."""
    return await paper_service.upload_source_version(
        db,
        current_user,
        workspace_id,
        files,
        relative_paths,
    )


@router.put(
    "/workspaces/{workspace_id}/model-binding",
    response_model=schemas.PaperModelBindingResponse,
)
def update_model_binding(
    workspace_id: uuid.UUID,
    request: schemas.PaperModelBindingUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Persist the analysis model for this paper workspace."""
    return paper_service.update_model_binding(db, current_user, workspace_id, request)


@router.post(
    "/source-versions/{source_version_id}/reviews",
    response_model=schemas.PaperReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def attach_reviewer_feedback(
    source_version_id: uuid.UUID,
    request: schemas.PaperReviewCreate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Attach generic reviewer Markdown to an owned source version."""
    return paper_service.attach_reviewer_feedback(db, current_user, source_version_id, request)


@router.get("/reviews/{review_id}", response_model=schemas.PaperReviewContentResponse)
def get_reviewer_feedback(
    review_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Return owned reviewer Markdown."""
    return paper_service.get_reviewer_feedback(db, current_user, review_id)


@router.patch("/reviews/{review_id}", response_model=schemas.PaperReviewResponse)
def update_reviewer_feedback(
    review_id: uuid.UUID,
    request: schemas.PaperReviewUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Replace owned reviewer Markdown and refresh its derived baseline."""
    return paper_service.update_reviewer_feedback(db, current_user, review_id, request)


@router.delete("/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reviewer_feedback(
    review_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
) -> Response:
    """Delete owned reviewer feedback and invalidate dependent stages."""
    paper_service.delete_reviewer_feedback(db, current_user, review_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/source-versions/{source_version_id}/path",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_source_path(
    source_version_id: uuid.UUID,
    request: schemas.PaperSourcePathDeleteRequest,
    db: SessionDep,
    current_user: CurrentUser,
) -> Response:
    """Remove a file or directory prefix from the current paper source."""
    paper_service.delete_source_path(db, current_user, source_version_id, request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/source-versions/{source_version_id}/file",
    response_model=schemas.PaperSourceFileResponse,
)
def get_source_file(
    source_version_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    path: str = Query(min_length=1, max_length=1_024),
):
    """Return one owned UTF-8 source file."""
    return paper_service.get_source_file(db, current_user, source_version_id, path)


@router.put(
    "/source-versions/{source_version_id}/file",
    response_model=schemas.PaperSourceVersionResponse,
)
def update_source_file(
    source_version_id: uuid.UUID,
    request: schemas.PaperSourceFileUpdateRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Save one file in the current paper source."""
    return paper_service.update_source_file(
        db,
        current_user,
        source_version_id,
        request,
    )


@router.get(
    "/source-versions/{source_version_id}/export",
    response_model=schemas.PaperExportResponse,
)
def export_source_version(
    source_version_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Export the complete current paper source."""
    return paper_service.export_source_version(db, current_user, source_version_id)


@router.get(
    "/source-versions/{source_version_id}/download",
    response_class=Response,
    responses={
        200: {
            "content": {"application/octet-stream": {}},
            "description": "Complete paper source bundle",
        }
    },
)
def download_source_version(
    source_version_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
) -> Response:
    """Download the complete source without exposing object-store addresses."""
    content, _filename = paper_service.download_source_version(
        db,
        current_user,
        source_version_id,
    )
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put(
    "/workspaces/{workspace_id}/judge-bindings",
    response_model=schemas.PaperJudgeBindingsResponse,
)
def update_judge_bindings(
    workspace_id: uuid.UUID,
    request: schemas.PaperJudgeBindingsUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Persist Reviewer A and Reviewer B model bindings."""
    return paper_service.update_judge_bindings(db, current_user, workspace_id, request)


@router.patch(
    "/optimization-targets/{target_id}",
    response_model=schemas.PaperOptimizationTargetResponse,
)
def update_optimization_target(
    target_id: uuid.UUID,
    request: schemas.PaperOptimizationTargetUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Edit and select a paper optimization target."""
    target = paper_service.update_optimization_target(db, current_user, target_id, request)
    return schemas.PaperOptimizationTargetResponse.model_validate(target)


@router.post(
    "/source-versions/{source_version_id}/metric-suggestions",
    response_model=list[schemas.PaperMetricResponse],
    status_code=status.HTTP_201_CREATED,
)
def add_metric_suggestions(
    source_version_id: uuid.UUID,
    request: schemas.PaperMetricSuggestionRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Persist model suggestions after schema validation."""
    return paper_service.add_metric_suggestions(
        db,
        current_user,
        source_version_id,
        request.metrics,
    )


@router.put(
    "/source-versions/{source_version_id}/metrics",
    response_model=list[schemas.PaperMetricResponse],
)
def select_metrics(
    source_version_id: uuid.UUID,
    request: schemas.PaperMetricSelectionRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Save user-selected optional metrics and locked baseline weights."""
    return paper_service.replace_metric_selection(db, current_user, source_version_id, request)


@router.post(
    "/workspaces/{workspace_id}/source-versions/{source_version_id}/proposals",
    response_model=schemas.PaperAlgorithmProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_proposal(
    workspace_id: uuid.UUID,
    source_version_id: uuid.UUID,
    request: schemas.PaperAlgorithmProposalCreate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Store a structured proposal for user review."""
    return paper_service.create_proposal(
        db,
        current_user,
        workspace_id,
        source_version_id,
        request,
    )


@router.patch(
    "/proposals/{proposal_id}",
    response_model=schemas.PaperAlgorithmProposalResponse,
)
def update_proposal(
    proposal_id: uuid.UUID,
    request: schemas.PaperAlgorithmProposalUpdate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Edit a draft proposal before task creation."""
    return paper_service.update_proposal(db, current_user, proposal_id, request)


@router.post(
    "/workspaces/{workspace_id}/proposal-tasks",
    response_model=list[schemas.PaperProposalTaskLinkResponse],
)
def create_proposal_tasks(
    workspace_id: uuid.UUID,
    request: schemas.PaperProposalTaskCreateRequest,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Confirm proposals and initialize their explicit evolution tasks."""
    return paper_service.create_proposal_tasks(db, current_user, workspace_id, request)


@router.get(
    "/revision-candidates/{candidate_id}/patch",
    response_model=schemas.PaperRevisionPatchResponse,
)
def get_revision_patch(
    candidate_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Return an owned candidate patch for user review."""
    return paper_service.get_revision_patch(db, current_user, candidate_id)


@router.post(
    "/optimization-targets/{target_id}/revision-candidates",
    response_model=schemas.PaperRevisionCandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_revision_candidate(
    target_id: uuid.UUID,
    request: schemas.PaperRevisionCandidateCreate,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Import an evolved manuscript replacement for review."""
    candidate = paper_service.create_revision_candidate(
        db,
        current_user,
        target_id,
        request,
    )
    return schemas.PaperRevisionCandidateResponse.model_validate(candidate)


@router.post(
    "/revision-candidates/{candidate_id}/accept",
    response_model=schemas.PaperRevisionApplyResponse,
)
def accept_revision_candidate(
    candidate_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
):
    """Apply one accepted candidate to the current paper source."""
    return paper_service.accept_revision_candidate(db, current_user, candidate_id)
