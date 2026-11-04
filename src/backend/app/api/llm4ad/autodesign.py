"""AutoDesign conversations and task-scoped operations for the native Agent."""

import asyncio
import hmac
import uuid

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Response, UploadFile, status
from pydantic import ValidationError

from app import models
from app.api.deps import CurrentUser, SessionDep
from app.schemas import autodesign as schemas
from app.services import autodesign_service as service
from app.services.paper_workspace_runtime import paper_workspace_runtime_token

router = APIRouter(prefix="/autodesign", tags=["llm4ad.autodesign"])


@router.post("/sessions", response_model=schemas.AutoDesignSessionResponse, status_code=201)
def create_session(request: schemas.AutoDesignSessionCreate, db: SessionDep, current_user: CurrentUser):
    """Start a design conversation with optional project context."""
    return service.create_session(db, current_user, request)


@router.get("/sessions", response_model=schemas.AutoDesignSessionList)
def list_sessions(db: SessionDep, current_user: CurrentUser, project_id: uuid.UUID | None = None):
    """List the signed-in user's independent and task-bound conversations."""
    return service.list_sessions(db, current_user, project_id)


@router.post("/tasks/{task_id}/session", response_model=schemas.AutoDesignSessionResponse)
def get_task_session(task_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    """Resolve the same session whenever a task version switches to Agent view."""
    return service.resolve_task_session(db, current_user, task_id)


@router.get("/sessions/{session_id}", response_model=schemas.AutoDesignSessionResponse)
def get_session(session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser):
    """Read model settings, associations and verified package status."""
    return service.get_session(db, current_user, session_id)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser) -> Response:
    """Delete the owned Agent conversation and destroy its workspace runtime."""
    service.delete_session(db, current_user, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions/{session_id}/context", response_model=schemas.AutoDesignTaskContext)
def get_session_context(session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser, task_id: uuid.UUID | None = None):
    """Read live task/family state rather than a stale conversation summary."""
    return service.get_context(db, current_user, session_id, task_id)


@router.get("/sessions/{session_id}/files", response_model=schemas.AutoDesignFileList)
def list_files(session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser, area: schemas.FileArea = "run", task_id: uuid.UUID | None = None, directory: str = ""):
    """Discover scoped task files, references, or read-only design records."""
    return service.list_files(db, current_user, session_id, area, task_id, directory)


@router.get("/sessions/{session_id}/file", response_model=schemas.AutoDesignFileResponse)
def read_file(session_id: uuid.UUID, db: SessionDep, current_user: CurrentUser, path: str, area: schemas.FileArea = "run", task_id: uuid.UUID | None = None, start_line: int | None = Query(default=None, ge=1), end_line: int | None = Query(default=None, ge=1)):
    """Read file contents with optional explicit line selection."""
    return service.read_file(db, current_user, session_id, area, path, task_id, start_line, end_line)


@router.put("/sessions/{session_id}/file", response_model=schemas.AutoDesignFileResponse)
def update_draft_file(session_id: uuid.UUID, request: schemas.AutoDesignFileUpdate, db: SessionDep, current_user: CurrentUser):
    """Save an owned draft while keeping published inputs and runs unchanged."""
    return service.update_draft_file(db, current_user, session_id, request)


@router.post("/sessions/{session_id}/publish", response_model=schemas.AutoDesignPublishResponse)
def prepare_task(session_id: uuid.UUID, request: schemas.AutoDesignPublishRequest, db: SessionDep, current_user: CurrentUser):
    """Open task setup directly from the verified project directory."""
    return service.prepare_task(db, current_user, session_id, request)


@router.post("/sessions/{session_id}/launch", response_model=schemas.AutoDesignPublishResponse)
def launch_session(
    session_id: uuid.UUID, request: schemas.AutoDesignPublishRequest, db: SessionDep, current_user: CurrentUser
):
    """Create the workbench from verified inputs and submit its first run."""
    return service.launch_session(db, current_user, session_id, request)


@router.post("/sessions/{session_id}/materials", response_model=schemas.AutoDesignFileList)
def upload_materials(
    session_id: uuid.UUID,
    db: SessionDep,
    current_user: CurrentUser,
    files: list[UploadFile] = File(...),
    relative_paths: list[str] | None = Form(default=None),
):
    """Add optional reference files directly to the Agent's persistent workspace."""
    try:
        return service.upload_materials(db, current_user, session_id, files, relative_paths)
    finally:
        for upload in files:
            upload.file.close()


@router.delete("/sessions/{session_id}/materials", response_model=schemas.AutoDesignFileList)
def delete_material(session_id: uuid.UUID, path: str, db: SessionDep, current_user: CurrentUser):
    """Delete one owned reference document without touching task source files."""
    return service.delete_material(db, current_user, session_id, path)


@router.post("/sessions/{session_id}/operations")
def operate(session_id: uuid.UUID, request: schemas.AutoDesignOperationRequest, db: SessionDep, current_user: CurrentUser) -> dict:
    """Use the same scoped actions from a signed-in UI or integration test."""
    try:
        return service.operate(db, current_user, session_id, request)
    except (ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid AutoDesign operation arguments") from exc


@router.post("/runtime/operations")
async def runtime_operation(request: schemas.AutoDesignRuntimeRequest, db: SessionDep, x_llm4ad_runtime_token: str | None = Header(default=None)) -> dict:
    """Authorize one workspace-local MCP request without exposing user tokens."""
    expected = paper_workspace_runtime_token(request.workspace_id)
    if not x_llm4ad_runtime_token or not hmac.compare_digest(expected, x_llm4ad_runtime_token):
        raise HTTPException(status_code=401, detail="Invalid AutoDesign runtime token")
    workspace = db.get(models.PaperWorkspace, request.workspace_id)
    current_user = db.get(models.User, workspace.user_id) if workspace else None
    if current_user is None or not current_user.is_active:
        raise HTTPException(status_code=404, detail="AutoDesign owner is unavailable")
    public_request = schemas.AutoDesignOperationRequest.model_validate(request.model_dump(exclude={"workspace_id"}))
    return await asyncio.to_thread(operate, request.workspace_id, public_request, db, current_user)
