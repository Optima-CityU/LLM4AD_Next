"""Native AutoDesign sessions, scoped task files and verified Cloud publication."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from fastapi import HTTPException, UploadFile
from llm4ad.config.app import AppConfig
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import models
from app.core.config import settings
from app.core.security import create_access_token
from app.core.storage import storage
from app.schemas import autodesign as schemas
from app.schemas.memory import MemoryCardUpsertRequest, MemoryScope
from app.schemas.task import TaskResponse
from app.services import (
    autodesign_drafts,
    memory_service,
    paper_service,
    project_package_service,
    project_service,
    task_service,
)
from app.services.paper_workspace_runtime import prepare_paper_workspace_path

_EXCLUDED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".cache", ".uv"}
_TEXT_DOCUMENT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".tex", ".typ", ".bib", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml"}
_PACKAGE_IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__MACOSX"}


_SECRET_KEYS = {
    "api_key",
    "auth_token",
    "access_token",
    "refresh_token",
    "password",
    "secret_key",
    "authorization",
    "mindmemos_api_key",
}


def _redact(value: Any) -> Any:
    """Keep model and memory bindings useful without returning credentials."""
    if isinstance(value, dict):
        return {key: "[redacted]" if str(key).lower() in _SECRET_KEYS else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _task_response(task: models.Task) -> TaskResponse:
    """Serialize a task with credential fields removed from its configuration."""
    result = TaskResponse.model_validate(task)
    result.input_args = _redact(copy.deepcopy(task.input_args or {}))
    return result


def _owned(db: Session, current_user: models.User, workspace_id: uuid.UUID) -> tuple[models.AutoDesignSession, models.PaperWorkspace]:
    """Resolve the session using the runtime workspace's persisted owner."""
    session = db.get(models.AutoDesignSession, workspace_id)
    workspace = db.get(models.PaperWorkspace, workspace_id)
    if session is None or workspace is None or workspace.user_id != current_user.id or workspace.mode != "autodesign":
        raise HTTPException(status_code=404, detail="AutoDesign session not found")
    return session, workspace


def _scoped_task(db: Session, current_user: models.User, session: models.AutoDesignSession, task_id: uuid.UUID | None = None) -> models.Task:
    """Authorize the selected version or a related experiment in its task family."""
    selected = task_id or session.task_id
    if selected is None:
        raise HTTPException(status_code=409, detail="This design has not been published as a task")
    task = task_service.get_task_with_auth(db, selected, current_user)
    if session.task_id:
        anchor = task_service.get_task_with_auth(db, session.task_id, current_user)
        root_id = anchor.group_id or anchor.id
        if task.project_id != anchor.project_id or (task.id != root_id and task.group_id != root_id):
            raise HTTPException(status_code=404, detail="Task is outside this experiment family")
    elif task.project_id != session.project_id:
        raise HTTPException(status_code=404, detail="Task is outside this design project")
    return task


def _running_family_task(db: Session, task: models.Task | None) -> models.Task | None:
    """Find an active execution in the design's experiment family."""
    if task is None:
        return None
    root_id = task.group_id or task.id
    return db.exec(select(models.Task).where(
        (models.Task.id == root_id) | (models.Task.group_id == root_id),
        models.Task.status.in_([models.TaskStatus.PENDING, models.TaskStatus.RUNNING]),
    )).first()


def ensure_session_editable(db: Session, current_user: models.User, workspace_id: uuid.UUID, target: models.Task | None = None) -> None:
    """Reject stale browser or Agent mutations while its task is executing."""
    session, _workspace = _owned(db, current_user, workspace_id)
    task = target or (db.get(models.Task, session.task_id) if session.task_id else None)
    if _running_family_task(db, task):
        raise HTTPException(status_code=409, detail="Task is pending or running; use the task workbench until it stops")


def _project_files(project: Path) -> dict[str, Path]:
    """Inventory editable inputs using the portable verifier's cache exclusions."""
    result = {}
    if not project.is_dir():
        return result
    for path in project.rglob("*"):
        relative = path.relative_to(project)
        if any(part in _PACKAGE_IGNORED_DIRS for part in relative.parts) or relative.parts[0] == "runs":
            continue
        if path.name in {".DS_Store", "Thumbs.db", "desktop.ini"} or path.name.startswith("._"):
            continue
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            continue
        if path.suffix in {".pyc", ".pyo"} or relative.as_posix() == "validation.json":
            continue
        if path.is_symlink():
            raise ValueError("Draft inputs cannot contain symbolic links")
        if path.is_file():
            result[relative.as_posix()] = path
    return result


def _source_signature(project: Path) -> str:
    """Detect edited inputs cheaply between validation and a user's launch."""
    entries = [(name, path.stat().st_size, path.stat().st_mtime_ns) for name, path in sorted(_project_files(project).items())]
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def _validation_report(session: models.AutoDesignSession, workspace: models.PaperWorkspace) -> dict:
    """Read live progress without persisting every verifier transition to SQL."""
    report = dict(session.validation_report or {})
    report.pop("package_path", None)
    report.pop("report_path", None)
    progress_path = report.pop("progress_path", None)
    if report.get("status") == "running" and isinstance(progress_path, str):
        try:
            if progress_path.startswith("/workspace/.research/"):
                root = paper_service.paper_workspace_root(workspace.user_id, workspace.id)
                path = _inside(root, progress_path.removeprefix("/workspace/"))
            else:
                path = _inside(_source_root(workspace), progress_path.removeprefix("/workspace/source/"))
            progress = json.loads(path.read_text(encoding="utf-8"))
            report["checks"] = _redact(progress.get("checks", []))
        except (OSError, ValueError, HTTPException):
            pass
    return report


def _validated_project(session: models.AutoDesignSession, workspace: models.PaperWorkspace) -> Path | None:
    """Resolve the verified editable source while rejecting unrelated host paths."""
    report = session.validation_report or {}
    path = report.get("project_path")
    if report.get("status") != "passed" or not isinstance(path, str):
        return None
    try:
        project = _inside(_source_root(workspace), path.removeprefix("/workspace/source/"))
        if project.is_relative_to((_source_root(workspace) / "draft").resolve()):
            return project
    except HTTPException:
        pass
    return None


def _validation_current(db: Session, session: models.AutoDesignSession, workspace: models.PaperWorkspace) -> bool:
    """Only expose Start for the inputs that passed the latest real checks."""
    if session.task_id and not (session.validation_report or {}).get("source_task_id"):
        return False
    project = _validated_project(session, workspace)
    signature = (session.validation_report or {}).get("source_signature")
    try:
        return bool(project and signature and signature == _source_signature(project) and _task_source_is_current(db, session.validation_report or {}))
    except (OSError, ValueError):
        return False


def _assert_verified_source(db: Session, session: models.AutoDesignSession, workspace: models.PaperWorkspace) -> None:
    """Recheck content hashes at publication so changed drafts cannot be launched."""
    project = _validated_project(session, workspace)
    hashes = (session.validation_report or {}).get("files_sha256")
    if session.task_id and not (session.validation_report or {}).get("source_task_id"):
        raise HTTPException(status_code=409, detail="Verify the task draft to establish its current input version")
    if not _task_source_is_current(db, session.validation_report or {}):
        raise HTTPException(status_code=409, detail="Task inputs changed; synchronize and validate the current draft")
    if project is None or not isinstance(hashes, dict) or not hashes:
        raise HTTPException(status_code=409, detail="Validate the task before starting it")
    try:
        files = _project_files(project)
        current = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Draft changed; validate it again") from exc
    if current != hashes:
        raise HTTPException(status_code=409, detail="Draft changed; validate it again")


def _draft_projects(workspace: models.PaperWorkspace) -> list[schemas.AutoDesignDraftProject]:
    """Describe actual config files without requiring a package-management UI."""
    root = _source_root(workspace)
    draft = root / "draft"
    projects = []
    if not draft.is_dir():
        return projects
    for config_path in sorted(draft.rglob("config.yaml")):
        if not config_path.is_file() or config_path.is_symlink() or not _visible(config_path.relative_to(root).as_posix()):
            continue
        if not config_path.resolve().is_relative_to(draft.resolve()):
            continue
        error = None
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
            if not isinstance(config, dict):
                config, error = {}, "config.yaml must contain a mapping"
        except (OSError, UnicodeError, yaml.YAMLError):
            config, error = {}, "Invalid YAML in config.yaml"
        projects.append(schemas.AutoDesignDraftProject(
            path="/workspace/source/" + config_path.parent.relative_to(root).as_posix(),
            name=str(config.get("project_name") or config_path.parent.name),
            config=_redact(config), config_error=error,
        ))
    return projects


def _response(db: Session, session: models.AutoDesignSession, workspace: models.PaperWorkspace) -> schemas.AutoDesignSessionResponse:
    """Return native settings together with the latest validated package state."""
    task = db.get(models.Task, session.task_id) if session.task_id else None
    return schemas.AutoDesignSessionResponse(
        **schemas.PaperWorkspaceSummary.model_validate(workspace).model_dump(),
        project_id=session.project_id,
        task_id=session.task_id,
        root_task_id=(task.group_id or task.id) if task else None,
        validation_report=_validation_report(session, workspace),
        project_ready=_validation_current(db, session, workspace),
        project_revision=_project_revision(session.validation_report or {}),
        project_name=(session.validation_report or {}).get("project_name"),
        published_task_id=session.published_task_id,
    )


def create_session(db: Session, current_user: models.User, request: schemas.AutoDesignSessionCreate) -> schemas.AutoDesignSessionResponse:
    """Create a conversation before any execution task or paper exists."""
    if request.project_id:
        project_service.get_project_with_auth(db, request.project_id, current_user)
    workspace = models.PaperWorkspace(user_id=current_user.id, mode="autodesign", title=request.title)
    defaults = db.exec(select(models.UserDefaultModel).where(models.UserDefaultModel.user_id == current_user.id)).first()
    if defaults:
        for slot in ("other", "coder", "planner"):
            provider_id = getattr(defaults, f"{slot}_provider_id", None)
            model_name = getattr(defaults, f"{slot}_model_name", None)
            provider = db.get(models.LLMProvider, provider_id) if provider_id else None
            if provider and model_name and (provider.user_id == current_user.id or (provider.is_builtin and provider.visible_to_all)):
                workspace.analysis_provider_id = provider_id
                workspace.analysis_model_name = model_name
                break
    session = models.AutoDesignSession(workspace_id=workspace.id, project_id=request.project_id)
    db.add(workspace)
    db.flush()
    db.add(session)
    db.commit()
    db.refresh(workspace)
    db.refresh(session)
    prepare_paper_workspace_path(_source_root(workspace).parent)
    prepare_paper_workspace_path(_source_root(workspace))
    return _response(db, session, workspace)


def resolve_task_session(db: Session, current_user: models.User, task_id: uuid.UUID) -> schemas.AutoDesignSessionResponse:
    """Reuse one stable conversation for an explicitly selected task version."""
    task = task_service.get_task_with_auth(db, task_id, current_user)
    session = db.exec(select(models.AutoDesignSession).where(models.AutoDesignSession.task_id == task.id)).first()
    if session:
        return get_session(db, current_user, session.workspace_id)
    if _running_family_task(db, task):
        raise HTTPException(status_code=409, detail="Task is pending or running; use the task workbench until it stops")
    result = create_session(db, current_user, schemas.AutoDesignSessionCreate(title=task.name, project_id=task.project_id))
    session = db.get(models.AutoDesignSession, result.id)
    session.task_id = task.id
    db.add(session)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Concurrent browser tabs may resolve the same version simultaneously.
        duplicate_session = db.get(models.AutoDesignSession, result.id)
        if duplicate_session is not None:
            db.delete(duplicate_session)
            db.flush()
        duplicate = db.get(models.PaperWorkspace, result.id)
        if duplicate is not None:
            db.delete(duplicate)
            db.commit()
        session = db.exec(select(models.AutoDesignSession).where(models.AutoDesignSession.task_id == task.id)).one()
    _, workspace = _owned(db, current_user, session.workspace_id)
    _ensure_draft(workspace, task, session)
    return _response(db, session, workspace)


def get_session(db: Session, current_user: models.User, workspace_id: uuid.UUID) -> schemas.AutoDesignSessionResponse:
    """Load a conversation without rebinding or restarting its runtime."""
    session, workspace = _owned(db, current_user, workspace_id)
    return _response(db, session, workspace)


def delete_session(db: Session, current_user: models.User, workspace_id: uuid.UUID) -> None:
    """Discard an owned conversation, including its runtime and reference files."""
    _owned(db, current_user, workspace_id)
    paper_service.delete_workspace(db, current_user, workspace_id)


def update_project_name(db: Session, current_user: models.User, workspace_id: uuid.UUID, name: str) -> dict:
    """Update the current design's display name as its conversational goal develops."""
    session, workspace = _owned(db, current_user, workspace_id)
    ensure_session_editable(db, current_user, workspace_id)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
        raise HTTPException(status_code=422, detail="Project name must contain 1 to 255 characters")
    workspace.title = name.strip()
    workspace.updated_time = datetime.now(UTC)
    if session.task_id:
        task = _scoped_task(db, current_user, session)
        if task.status == models.TaskStatus.UNINITIALIZED:
            task.name = workspace.title
            task.updated_time = workspace.updated_time
            db.add(task)
    db.add(workspace)
    db.commit()
    return {"workspace_id": str(workspace.id), "title": workspace.title}


def list_sessions(db: Session, current_user: models.User, project_id: uuid.UUID | None = None) -> schemas.AutoDesignSessionList:
    """List owned AutoDesign conversations, optionally in an existing project."""
    if project_id:
        project_service.get_project_with_auth(db, project_id, current_user)
    query = select(models.AutoDesignSession, models.PaperWorkspace).join(models.PaperWorkspace, models.PaperWorkspace.id == models.AutoDesignSession.workspace_id).where(models.PaperWorkspace.user_id == current_user.id)
    if project_id:
        query = query.where(models.AutoDesignSession.project_id == project_id)
    rows = db.exec(query.order_by(models.PaperWorkspace.updated_time.desc())).all()
    return schemas.AutoDesignSessionList(items=[_response(db, session, workspace) for session, workspace in rows], total=len(rows))


def _source_root(workspace: models.PaperWorkspace) -> Path:
    """Return the same source root used by the native runtime bind mount."""
    return paper_service.paper_workspace_root(workspace.user_id, workspace.id) / "source"


def _input_entries(task: models.Task) -> dict[str, bytes]:
    """Read canonical RustFS inputs and strip their single upload-root folder."""
    if not task.input_data_path:
        return {}
    prefix = task.input_data_path.rstrip("/") + "/"
    keys = storage.list_objects(task.input_data_path)
    names = [key[len(prefix):] for key in keys if key.startswith(prefix) and not key.endswith("/")]
    roots = {name.split("/", 1)[0] for name in names}
    strip_root = len(roots) == 1 and all("/" in name for name in names)
    result = {}
    for key in keys:
        if not key.startswith(prefix) or key.endswith("/"):
            continue
        name = key[len(prefix):]
        if strip_root:
            name = name.split("/", 1)[1]
        _relative(name)
        if not name.endswith(".gitkeep") and not name.endswith(".placeholder"):
            result[name] = storage.download(key)
    return result


def _relative(value: str) -> PurePosixPath:
    """Require a workspace-relative path rather than an arbitrary host path."""
    path = PurePosixPath(value)
    if not value or not path.parts or "\\" in value or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise HTTPException(status_code=400, detail="Use a relative task file path")
    return path


def _portable_config(task: models.Task) -> dict:
    """Prepare a Cloud draft with bindings selected later by the platform."""
    config = copy.deepcopy(task.input_args or {})
    config["providers"] = []
    config.pop("run_id", None)
    for section in ("planner", "coder", "evaluator"):
        if isinstance(config.get(section), dict):
            config[section]["provider"] = "default"
            config[section].pop("provider_model", None)
    memory = config.get("memory")
    if isinstance(memory, dict):
        for field in tuple(memory):
            if field in _SECRET_KEYS or field in {"mindmemos_base_url", "mindmemos_user_id", "mindmemos_app_id", "mindmemos_agent_id", "mindmemos_session_id", "mindmemos_project_id", "pinned_card_ids"}:
                memory.pop(field, None)
    config["path_resolution"] = "config_dir"
    return config


def _task_source_revision(task: models.Task) -> str:
    """Track canonical parameter and file revisions without fetching object bytes."""
    version = task.version_time.replace(tzinfo=None).isoformat() if task.version_time else None
    data = {"config": _portable_config(task), "input_data_path": task.input_data_path, "version_time": version}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _draft_state_path(workspace: models.PaperWorkspace, task: models.Task) -> Path:
    """Keep canonical baselines outside runnable and user-facing project files."""
    return _source_root(workspace).parent / ".research" / "autodesign" / f"{task.id}.json"


def _ensure_draft(workspace: models.PaperWorkspace, task: models.Task, session: models.AutoDesignSession | None = None, *, resolution: str | None = None) -> Path:
    """Merge external changes into untouched draft inputs and retain conflicts."""
    draft = _source_root(workspace) / "draft" / str(task.id)
    if session is not None and session.published_task_id == task.id:
        project_path = (session.validation_report or {}).get("project_path")
        if isinstance(project_path, str):
            existing = _inside(_source_root(workspace), project_path.removeprefix("/workspace/source/"))
            if existing.is_relative_to((_source_root(workspace) / "draft").resolve()) and existing.is_dir():
                draft = existing
    cached_inputs = None

    def inputs():
        nonlocal cached_inputs
        if cached_inputs is None:
            cached_inputs = _input_entries(task)
        return cached_inputs

    created = not draft.exists()
    if created:
        draft.mkdir(parents=True, exist_ok=True)
        for name, content in inputs().items():
            path = _inside(draft, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (draft / "config.yaml").write_text(yaml.safe_dump(_portable_config(task), allow_unicode=True), encoding="utf-8")
        prepare_paper_workspace_path(draft, recursive=True)
        prepare_paper_workspace_path(draft.parent)
    autodesign_drafts.synchronize_draft(draft, _draft_state_path(workspace, task), _portable_config(task), _task_source_revision(task), inputs, initialize=created, resolution=resolution)
    return draft


def _project_task(db: Session, current_user: models.User, session: models.AutoDesignSession, workspace: models.PaperWorkspace, project: Path) -> models.Task | None:
    """Resolve the exact task whose canonical inputs seeded a verified draft."""
    relative = project.relative_to((_source_root(workspace) / "draft").resolve())
    if relative.parts:
        try:
            selected = uuid.UUID(relative.parts[0])
        except ValueError:
            selected = None
        if selected is not None and db.get(models.Task, selected) is not None:
            return _scoped_task(db, current_user, session, selected)
    if session.published_task_id and (session.validation_report or {}).get("project_path") == "/workspace/source/" + project.relative_to(_source_root(workspace)).as_posix():
        return _scoped_task(db, current_user, session, session.published_task_id)
    return _scoped_task(db, current_user, session) if session.task_id else None


def _task_source_is_current(db: Session, report: dict) -> bool:
    """Detect canonical edits made after draft verification."""
    task_id = report.get("source_task_id")
    if not task_id:
        return True
    task = db.get(models.Task, uuid.UUID(task_id))
    return bool(task and report.get("source_task_revision") == _task_source_revision(task))


def _run_root(db: Session, task: models.Task) -> Path:
    """Locate actual execution files using the existing task workspace convention."""
    project = db.get(models.Project, task.project_id)
    return Path(settings.DOCKER_PROJECT_HOME) / f"code_user-{project.user_id}" / str(task.id)


def get_context(db: Session, current_user: models.User, workspace_id: uuid.UUID, task_id: uuid.UUID | None = None) -> schemas.AutoDesignTaskContext:
    """Expose current parameters, real validation progress and execution locks."""
    session, workspace = _owned(db, current_user, workspace_id)
    task = _scoped_task(db, current_user, session, task_id) if session.task_id or task_id else None
    family, stats, root_id = [], {}, None
    if task:
        root_id = task.group_id or task.id
        family = db.exec(select(models.Task).where((models.Task.id == root_id) | (models.Task.group_id == root_id)).order_by(models.Task.created_time)).all()
        draft = _ensure_draft(workspace, task, session)
        draft_path = "/workspace/source/" + draft.relative_to(_source_root(workspace)).as_posix()
        if task.status != models.TaskStatus.UNINITIALIZED:
            stats = task_service.get_task_stats(db, task.id, current_user).model_dump(mode="json")
    else:
        draft_path = "/workspace/source/draft"
    projects = _draft_projects(workspace)
    if task:
        projects = [project for project in projects if project.path == draft_path]
    paths = {project.path for project in projects}
    previous_path = (session.validation_report or {}).get("project_path")
    selected = draft_path if task and draft_path in paths else previous_path if previous_path in paths else projects[0].path if len(projects) == 1 else None
    selected_config = next((project.config for project in projects if project.path == selected), {})
    running = _running_family_task(db, task)
    report = _validation_report(session, workspace)
    return schemas.AutoDesignTaskContext(
        title=workspace.title,
        task=_task_response(task) if task else None, root_task_id=root_id,
        family=[_task_response(item) for item in family],
        config=_redact(task.input_args or {}) if task else selected_config,
        stats=_redact(stats), draft_path=draft_path, validation_report=report,
        project_ready=_validation_current(db, session, workspace),
        projects=projects, selected_project_path=selected,
        validation_current=_validation_current(db, session, workspace),
        editing_locked=running is not None, running_task_id=running.id if running else None,
        draft_conflicts=autodesign_drafts.draft_state(_draft_state_path(workspace, task)).get("conflicts", []) if task else [],
    )


def _visible(name: str) -> bool:
    """Omit environments, VCS internals and local secret files from file discovery."""
    parts = PurePosixPath(name).parts
    return not any(part in _EXCLUDED or part.startswith(".env") or part in {".npmrc", ".pypirc"} for part in parts)


def _local_root(
    db: Session, workspace: models.PaperWorkspace, task: models.Task | None, area: schemas.FileArea, session: models.AutoDesignSession | None = None
) -> Path:
    """Resolve a supported source without accepting a client-supplied host root."""
    if area in {"references", "analysis"}:
        return _inside(_source_root(workspace), area)
    if area == "draft":
        if task:
            _ensure_draft(workspace, task, session)
        return _source_root(workspace) / "draft"
    if task is None:
        raise HTTPException(status_code=409, detail="Select a published task to inspect its run")
    return _run_root(db, task)


def _inside(root: Path, name: str) -> Path:
    """Resolve a file and reject links escaping the selected task workspace."""
    relative = _relative(name)
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise HTTPException(status_code=400, detail="File is outside the task workspace")
    if not _visible(relative.as_posix()):
        raise HTTPException(status_code=403, detail="This file is not part of the task artifacts")
    return resolved


def upload_materials(db: Session, current_user: models.User, workspace_id: uuid.UUID, files: list[UploadFile], relative_paths: list[str] | None = None) -> schemas.AutoDesignFileList:
    """Accept UTF-8 text documents only and validate a batch before replacing files."""
    _session, workspace = _owned(db, current_user, workspace_id)
    ensure_session_editable(db, current_user, workspace_id)
    if relative_paths is not None and len(relative_paths) != len(files):
        raise HTTPException(status_code=400, detail="File path metadata does not match uploaded files")
    staged = []
    try:
        for index, upload in enumerate(files):
            name = relative_paths[index] if relative_paths is not None else upload.filename or "reference.txt"
            relative = _relative(name)
            if relative.suffix.lower() not in _TEXT_DOCUMENT_SUFFIXES:
                raise HTTPException(status_code=415, detail="Only UTF-8 text documents are supported")
            data = upload.file.read(settings.TASK_MAX_FILE_SIZE_MB * 1024 * 1024 + 1)
            if len(data) > settings.TASK_MAX_FILE_SIZE_MB * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Reference exceeds the configured task file upload limit")
            try:
                text = data.decode("utf-8-sig")
            except UnicodeError as exc:
                raise HTTPException(status_code=415, detail="Only UTF-8 text documents are supported") from exc
            if data.lstrip().startswith(b"%PDF-"):
                raise HTTPException(status_code=415, detail="PDF is not a text document")
            if any(ord(character) < 32 and character not in "\t\n\r\f" for character in text):
                raise HTTPException(status_code=415, detail="Binary content is not a text document")
            target = _inside(_source_root(workspace), "references/" + relative.as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".upload-{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(data)
            staged.append((temporary, target))
        for temporary, target in staged:
            temporary.replace(target)
    finally:
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)
    return list_files(db, current_user, workspace_id, "references")


def delete_material(db: Session, current_user: models.User, workspace_id: uuid.UUID, path: str) -> schemas.AutoDesignFileList:
    """Delete one reference file in the owned workspace, including older uploads."""
    _session, workspace = _owned(db, current_user, workspace_id)
    ensure_session_editable(db, current_user, workspace_id)
    root = _local_root(db, workspace, None, "references")
    target = _inside(root, path)
    if (root / _relative(path)).is_symlink() or not target.is_file():
        raise HTTPException(status_code=404, detail="Reference document not found")
    target.unlink()
    return list_files(db, current_user, workspace_id, "references")


def list_files(db: Session, current_user: models.User, workspace_id: uuid.UUID, area: schemas.FileArea = "run", task_id: uuid.UUID | None = None, directory: str = "") -> schemas.AutoDesignFileList:
    """List scoped task files, references, or read-only design records."""
    session, workspace = _owned(db, current_user, workspace_id)
    task = _scoped_task(db, current_user, session, task_id) if task_id or session.task_id else None
    if directory:
        _relative(directory)
    if area == "input":
        if task is None:
            return schemas.AutoDesignFileList(area=area, files=[])
        files = [schemas.AutoDesignFile(path=name, size=len(content)) for name, content in _input_entries(task).items() if _visible(name) and (not directory or name.startswith(directory.rstrip("/") + "/"))]
    else:
        root = _local_root(db, workspace, task, area, session)
        subtree = _inside(root, directory) if directory else _ensure_draft(workspace, task, session) if area == "draft" and task else root
        files = []
        if subtree.is_dir():
            for current, directories, names in os.walk(subtree, followlinks=False):
                directories[:] = [name for name in directories if _visible(name) and not (Path(current) / name).is_symlink()]
                for name in names:
                    path = Path(current) / name
                    relative = path.relative_to(root).as_posix()
                    if _visible(relative) and path.is_file() and path.resolve().is_relative_to(root.resolve()):
                        stat = path.stat()
                        title = None
                        if area == "analysis" and path.suffix.lower() in {".md", ".markdown"}:
                            try:
                                with path.open(encoding="utf-8-sig") as document:
                                    heading = document.readline().strip()
                                if heading.startswith("# "):
                                    title = _redact_text(heading[2:].strip()) or None
                            except (OSError, UnicodeError):
                                pass
                        files.append(schemas.AutoDesignFile(
                            path=relative, size=stat.st_size, modified_time=datetime.fromtimestamp(stat.st_mtime, tz=UTC), title=title
                        ))
    return schemas.AutoDesignFileList(area=area, task_id=task.id if task else None, files=sorted(files, key=lambda item: item.path))


def _redact_text(text: str) -> str:
    """Mask credential values while retaining source layout for file citations."""
    keys = "|".join(sorted(_SECRET_KEYS, key=len, reverse=True))
    text = re.sub(rf'(?im)(["\']?(?:{keys})["\']?\s*[:=]\s*)([^\n,}}]+)', r'\1"[redacted]"', text)
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._-]+", r"\1[redacted]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[redacted-token]", text)
    text = re.sub(r"(https?://)[^\s/@:]+:[^\s/@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(?i)([?&](?:token|api_key|access_token|key)=)[^\s&\"']+", r"\1[redacted]", text)
    return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[redacted-key]", text)


def read_file(db: Session, current_user: models.User, workspace_id: uuid.UUID, area: schemas.FileArea, path: str, task_id: uuid.UUID | None = None, start_line: int | None = None, end_line: int | None = None) -> schemas.AutoDesignFileResponse:
    """Read a real file completely unless the caller explicitly selects lines."""
    session, workspace = _owned(db, current_user, workspace_id)
    task = _scoped_task(db, current_user, session, task_id) if task_id or session.task_id else None
    _relative(path)
    if not _visible(path):
        raise HTTPException(status_code=403, detail="This file is not part of the task artifacts")
    if area == "input":
        entries = _input_entries(task) if task else {}
        if path not in entries:
            raise HTTPException(status_code=404, detail="Task input file not found")
        content = entries[path]
    else:
        file = _inside(_local_root(db, workspace, task, area, session), path)
        if not file.is_file():
            raise HTTPException(status_code=404, detail="Task file not found")
        content = file.read_bytes()
    try:
        text = content.decode("utf-8-sig")
        if "\x00" in text:
            raise UnicodeDecodeError("utf-8", content, 0, 1, "binary content")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="This artifact is not a UTF-8 text file") from exc
    redacted = _redact_text(text)
    lines = redacted.splitlines(keepends=True)
    first = start_line if start_line is not None else 1
    last = end_line if end_line is not None else len(lines)
    if first < 1 or last < 0 or (last < first and lines):
        raise HTTPException(status_code=422, detail="Invalid requested line range")
    return schemas.AutoDesignFileResponse(
        area=area, path=path, content="".join(lines[first - 1:last]), total_lines=len(lines),
        start_line=first, end_line=min(last, len(lines)), revision=hashlib.sha256(content).hexdigest(),
        editable=area == "draft" and text == redacted and start_line is None and end_line is None,
    )


def _save_draft(db: Session, current_user: models.User, session: models.AutoDesignSession, workspace: models.PaperWorkspace, path: Path, revision: str, content: bytes) -> None:
    """Atomically save a matching revision and expire its validated package.

    Args:
        db: Database session containing the design association.
        current_user: Owner requesting the edit.
        session: Owned design association whose validation must expire.
        workspace: Runtime workspace shared with the native Agent.
        path: Existing file inside the editable draft directory.
        revision: Digest of the file originally loaded by the editor.
        content: Replacement UTF-8 source bytes.
    """
    ensure_session_editable(db, current_user, workspace.id)
    if (session.validation_report or {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="Wait for task validation to finish before editing")
    root = _source_root(workspace) / "draft"
    if not path.is_relative_to(root) or any(part.is_symlink() for part in [path, *path.parents] if part.is_relative_to(root)):
        raise HTTPException(status_code=400, detail="Only regular draft files can be edited")
    if hashlib.sha256(path.read_bytes()).hexdigest() != revision:
        raise HTTPException(status_code=409, detail="Draft changed; reload it before saving")
    previous = path.stat()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".draft-save-", delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        temporary.chmod(previous.st_mode & 0o777)
        try:
            os.chown(temporary, previous.st_uid, previous.st_gid)
        except PermissionError:
            pass
        if hashlib.sha256(path.read_bytes()).hexdigest() != revision:
            raise HTTPException(status_code=409, detail="Draft changed; reload it before saving")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    session.package_object_key = None
    session.package_sha256 = None
    db.add(session)
    db.commit()


def update_draft_file(db: Session, current_user: models.User, workspace_id: uuid.UUID, request: schemas.AutoDesignFileUpdate) -> schemas.AutoDesignFileResponse:
    """Save complete text without overwriting a concurrent Agent edit or secrets."""
    session, workspace = _owned(db, current_user, workspace_id)
    loaded = read_file(db, current_user, workspace_id, "draft", request.path)
    if not loaded.editable or "\x00" in request.content:
        raise HTTPException(status_code=400, detail="This file cannot be edited as plain text")
    path = _inside(_source_root(workspace) / "draft", request.path)
    _save_draft(db, current_user, session, workspace, path, request.revision, request.content.encode("utf-8"))
    return read_file(db, current_user, workspace_id, "draft", request.path)


def _patch_config(original: dict, patch: dict) -> tuple[dict, dict]:
    """Apply nested task configuration edits and validate the runnable schema."""
    result = copy.deepcopy(original)
    changes = {}
    if not isinstance(patch, dict):
        raise HTTPException(status_code=422, detail="Configuration patch must be a nested object")

    def merge(target: dict, supplied: dict, prefix: str = "") -> None:
        for key, value in supplied.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value, path)
            elif target.get(key) != value:
                changes[path] = {"before": _redact(target.get(key)), "after": _redact(value)}
                target[key] = copy.deepcopy(value)

    merge(result, patch)
    try:
        AppConfig.model_validate(result)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid task configuration: {exc.errors()[0]['msg']}") from exc
    return result, changes


def _replace_input_entries(task: models.Task, entries: dict[str, bytes]) -> None:
    """Upload new canonical inputs before replacing the task's previous prefix."""
    prefix = f"tasks/{task.id}/{uuid.uuid4().hex}"
    uploaded = []
    try:
        for name, content in entries.items():
            _relative(name)
            key = f"{prefix}/project/{name}"
            uploaded.append(key)
            storage.upload(key, io.BytesIO(content))
    except Exception:
        storage.delete_many(uploaded)
        raise
    task.input_data_path = prefix


def create_child(db: Session, current_user: models.User, workspace_id: uuid.UUID, parent_id: uuid.UUID | None = None, config_patch: dict | None = None, seed: str = "input", name: str | None = None) -> dict:
    """Create an experiment with actual input or best-code seeding."""
    session, workspace = _owned(db, current_user, workspace_id)
    ensure_session_editable(db, current_user, workspace_id)
    parent = _scoped_task(db, current_user, session, parent_id)
    ensure_session_editable(db, current_user, workspace_id, parent)
    updated, changes = _patch_config(parent.input_args or {}, config_patch or {})
    entries = None
    if seed == "best":
        best = _run_root(db, parent) / "llm4ad" / "run" / "best" / "code"
        if not best.is_dir():
            raise HTTPException(status_code=409, detail="This run has no best-code snapshot yet")
        candidate = str((updated.get("version_control") or {}).get("local_path") or "")
        _relative(candidate)
        entries = _input_entries(parent)
        entries = {key: value for key, value in entries.items() if not key.startswith(candidate.rstrip("/") + "/")}
        copied = 0
        for file in best.rglob("*"):
            if file.is_symlink():
                raise HTTPException(status_code=409, detail="Best snapshot contains a link; select a regular candidate")
            if file.is_file() and _visible(file.relative_to(best).as_posix()):
                entries[f"{candidate}/{file.relative_to(best).as_posix()}"] = file.read_bytes()
                copied += 1
        if not copied:
            raise HTTPException(status_code=409, detail="The best-code snapshot is empty")
    elif seed != "input":
        raise HTTPException(status_code=422, detail="Seed must be input or best")
    child = task_service.copy_task(db, parent.id, current_user, is_child=True, activate_child=False, copy_logs=False, inherit_reports=False)
    child.input_args = updated
    if name:
        child.name = name
    if entries is not None:
        _replace_input_entries(child, entries)
    db.add(child)
    db.commit()
    db.refresh(child)
    draft = _ensure_draft(workspace, child)
    return {"task_id": str(child.id), "task": _task_response(child).model_dump(mode="json"), "changes": changes, "seed": seed, "draft_path": "/workspace/source/" + draft.relative_to(_source_root(workspace)).as_posix()}


def patch_child(db: Session, current_user: models.User, workspace_id: uuid.UUID, task_id: uuid.UUID, config_patch: dict) -> dict:
    """Modify an explicit unstarted version without changing related experiments."""
    session, workspace = _owned(db, current_user, workspace_id)
    ensure_session_editable(db, current_user, workspace_id)
    task = _scoped_task(db, current_user, session, task_id)
    db.refresh(task, with_for_update=True)
    ensure_session_editable(db, current_user, workspace_id, task)
    if task.status != models.TaskStatus.UNINITIALIZED or task.celery_task_id:
        raise HTTPException(status_code=409, detail="Select an unstarted version before applying modifications")
    draft = _ensure_draft(workspace, task, session)
    updated, changes = _patch_config(task.input_args or {}, config_patch)
    draft_config = yaml.safe_load((draft / "config.yaml").read_text(encoding="utf-8-sig"))
    if not isinstance(draft_config, dict):
        raise HTTPException(status_code=409, detail="Repair the draft YAML or synchronize with use_task before editing parameters")
    merged_draft, _ = _patch_config(draft_config, config_patch)
    task.input_args = updated
    task.version_time = datetime.now(UTC)
    db.add(task)
    db.commit()
    (draft / "config.yaml").write_text(yaml.safe_dump(merged_draft, allow_unicode=True), encoding="utf-8")
    _ensure_draft(workspace, task, session)
    return {"task_id": str(task.id), "task": _task_response(task).model_dump(mode="json"), "changes": changes}


def _run_validation(workspace: models.PaperWorkspace, project: Path, output: Path, timeout_seconds: int) -> dict:
    """Execute the canonical Cloud verifier in a fresh ordinary task-runner image."""
    from app.services.container_runtime import ContainerJob, ContainerJobCallbacks, ContainerJobSpec
    from app.services.container_service import resolve_host_path

    root = paper_service.paper_workspace_root(workspace.user_id, workspace.id)
    output.parent.mkdir(parents=True, exist_ok=True)
    messages = []
    spec = ContainerJobSpec(
        image=settings.TASK_RUNNER_IMAGE,
        command=["/app/backend/.venv/bin/python", "/app/paper-agent/validate_cloud_task.py",
                 "--project", "/workspace/" + project.relative_to(root).as_posix(),
                 "--output", "/workspace/" + output.relative_to(root).as_posix(),
                 "--timeout", str(timeout_seconds)],
        mounts={resolve_host_path(str(root)): "/workspace"},
        working_dir="/workspace",
        env={key: os.environ[key] for key in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "TASK_DEP_INSTALL_TIMEOUT") if os.environ.get(key)},
        timeout=timeout_seconds + int(os.environ.get("TASK_DEP_INSTALL_TIMEOUT", "600")) + 30,
    )
    result = ContainerJob(spec, ContainerJobCallbacks(on_stdout=messages.append)).run()
    report_path = output
    if result.exit_code == 0 and report_path.is_file():
        return json.loads(report_path.read_text(encoding="utf-8"))
    diagnostic = output.with_suffix(".error.json")
    if diagnostic.is_file():
        return json.loads(diagnostic.read_text(encoding="utf-8"))
    return {"status": "failed", "error": _redact_text("\n".join(messages) or result.error or "Validation container failed"), "exit_code": result.exit_code}


def _project_revision(report: dict) -> str | None:
    """Identify a verified directory from its input manifest, including old reports."""
    hashes = report.get("files_sha256")
    if not isinstance(hashes, dict) or not hashes:
        return None
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def validate_project(db: Session, current_user: models.User, workspace_id: uuid.UUID, project_path: str, timeout_seconds: int = 120) -> dict:
    """Run real checks and retain their state without publishing report files."""
    session, workspace = _owned(db, current_user, workspace_id)
    ensure_session_editable(db, current_user, workspace_id)
    if not isinstance(project_path, str) or not project_path:
        raise HTTPException(status_code=422, detail="Select a draft project to verify")
    source = _source_root(workspace)
    project = _inside(source, project_path.removeprefix("/workspace/source/"))
    if not project.is_relative_to((source / "draft").resolve()) or not project.is_dir():
        raise HTTPException(status_code=400, detail="Select a project under /workspace/source/draft/")
    if timeout_seconds <= 0:
        raise HTTPException(status_code=422, detail="Validation timeout must be positive")
    if session.published_package_sha256 and session.published_package_sha256 == session.package_sha256:
        session.published_package_sha256 = _project_revision(session.validation_report or {})
    session.package_object_key = None
    session.package_sha256 = None
    session.package_name = None
    source_task = _project_task(db, current_user, session, workspace, project)
    if source_task is not None:
        ensure_session_editable(db, current_user, workspace_id, source_task)
        _ensure_draft(workspace, source_task, session)
        conflicts = autodesign_drafts.draft_state(_draft_state_path(workspace, source_task)).get("conflicts", [])
        if conflicts:
            raise HTTPException(status_code=409, detail={"message": "Resolve draft conflicts before validation", "conflicts": conflicts})
    source_revision = _task_source_revision(source_task) if source_task else None
    root = paper_service.paper_workspace_root(workspace.user_id, workspace.id)
    temporary_root = _inside(root, ".research")
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="validation-", dir=temporary_root) as temporary:
        output = Path(temporary) / "result.json"
        session.validation_report = {"status": "running", "project_path": project_path, "progress_path": "/workspace/" + output.with_suffix(".progress.json").relative_to(root).as_posix()}
        db.add(session)
        db.commit()
        try:
            report = _run_validation(workspace, project, output, timeout_seconds)
        except Exception as exc:
            report = {"status": "failed", "error": _redact_text(str(exc))}
    report = _redact(report)
    if report.get("status") == "passed":
        try:
            current_hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in _project_files(project).items()}
            if current_hashes != report.get("files_sha256"):
                report.update(status="failed", error="Draft changed during verification; check the latest files again")
            else:
                report["source_signature"] = _source_signature(project)
                report["project_revision"] = _project_revision(report)
        except (OSError, ValueError):
            report.update(status="failed", error="Draft changed during verification; check the latest files again")
    if source_task is not None:
        report.update(source_task_id=str(source_task.id), source_task_revision=source_revision)
        db.refresh(source_task)
        if source_revision != _task_source_revision(source_task):
            report.update(status="failed", error="Task inputs changed during verification; synchronize and validate again")
    report["project_path"] = "/workspace/source/" + project.relative_to(source).as_posix()
    session.validation_report = report
    db.add(session)
    db.commit()
    ready = _validation_current(db, session, workspace)
    return {"validation_report": report, "project_ready": ready, "project_revision": _project_revision(report)}


def validate_package(db: Session, current_user: models.User, workspace_id: uuid.UUID, project_path: str, timeout_seconds: int = 120) -> dict:
    """Keep old integrations compatible while verifying directories without archives."""
    return validate_project(db, current_user, workspace_id, project_path, timeout_seconds)


def _inherit_bindings(imported: dict, parent: models.Task | None) -> dict:
    """Retain the selected task's server-owned model bindings."""
    if parent is not None:
        for key in ("planner", "coder", "evaluator"):
            current = (parent.input_args or {}).get(key) or {}
            for field in ("provider", "provider_model"):
                if field in current:
                    imported.setdefault(key, {})[field] = current[field]
    return imported


def prepare_task(db: Session, current_user: models.User, workspace_id: uuid.UUID, request: schemas.AutoDesignPublishRequest) -> schemas.AutoDesignPublishResponse:
    """Prepare task setup directly from the verified working directory."""
    from app.services.task_service.crud import _apply_memory_defaults

    session, workspace = _owned(db, current_user, workspace_id)
    db.refresh(session, with_for_update=True)
    _assert_verified_source(db, session, workspace)
    report = session.validation_report or {}
    revision = _project_revision(report)
    previous = session.published_package_sha256
    if session.published_task_id and (previous == revision or (session.package_sha256 and previous == session.package_sha256)):
        published = db.get(models.Task, session.published_task_id)
        if published is not None and published.status in {models.TaskStatus.UNINITIALIZED, models.TaskStatus.PENDING, models.TaskStatus.RUNNING} and (published.status != models.TaskStatus.UNINITIALIZED or not published.celery_task_id) and (request.task_id is None or request.task_id == published.id):
            return schemas.AutoDesignPublishResponse(session=_response(db, session, workspace), task=_task_response(published))
    ensure_session_editable(db, current_user, workspace_id)
    project = _validated_project(session, workspace)
    if project is None:
        raise HTTPException(status_code=409, detail="The verified project directory is unavailable")
    entries = {name: path.read_bytes() for name, path in _project_files(project).items()}
    if {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()} != report.get("files_sha256"):
        raise HTTPException(status_code=409, detail="Project changed; verify the latest files before task setup")
    source_id = uuid.UUID(report["source_task_id"]) if report.get("source_task_id") else session.task_id
    parent = _scoped_task(db, current_user, session, source_id) if source_id else None
    target = _scoped_task(db, current_user, session, request.task_id) if request.task_id else parent if parent and parent.status == models.TaskStatus.UNINITIALIZED and not parent.celery_task_id else None
    if target is not None:
        db.refresh(target, with_for_update=True)
        ensure_session_editable(db, current_user, workspace_id, target)
        if not _task_source_is_current(db, report):
            raise HTTPException(status_code=409, detail="Task changed before publication; validate again")
    if target is not None and (target.status != models.TaskStatus.UNINITIALIZED or target.celery_task_id):
        raise HTTPException(status_code=409, detail="Apply modifications to an unstarted version")
    if target is not None and report.get("source_task_id") not in {None, str(target.id)}:
        raise HTTPException(status_code=409, detail="Verify the selected target task draft before publishing")
    try:
        config = _inherit_bindings(project_package_service._read_config(project, contents=entries["config.yaml"]), target or parent)
    except (ValueError, TypeError, OSError, yaml.YAMLError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid project configuration: {exc}") from exc
    memory = dict(config.get("memory") or {})
    for field in ("type", "module", "pinned_card_ids", "mindmemos_base_url", "mindmemos_api_key", "mindmemos_user_id", "mindmemos_app_id", "mindmemos_agent_id", "mindmemos_session_id", "mindmemos_project_id"):
        memory.pop(field, None)
    inherited = target or parent
    inherited_memory = ((inherited.input_args or {}).get("memory") or {}) if inherited else {}
    memory["enabled"] = request.enable_memory if request.enable_memory is not None else bool(inherited_memory.get("enabled", True))
    if "pinned_card_ids" in inherited_memory:
        memory["pinned_card_ids"] = copy.deepcopy(inherited_memory["pinned_card_ids"])
    config["memory"] = memory
    name = request.name or (workspace.title if workspace.title != "AutoDesign" else str(config["project_name"]))
    project_id = target.project_id if target else parent.project_id if parent else request.project_id or session.project_id
    created_project = None
    if project_id is None:
        created_project = project_service.create_project(db, name, workspace.description, current_user.id)
        project_id = created_project.id
    else:
        project_service.get_project_with_auth(db, project_id, current_user)
    config = _apply_memory_defaults(db, config, current_user=current_user, project_id=project_id)
    config["project_name"] = name
    task = target or models.Task(name=name, project_id=project_id, status=models.TaskStatus.UNINITIALIZED, ai_built=False)
    if request.name or target is None:
        task.name = name
    task.input_args = config
    if target is None and parent is not None:
        task.parent_id = parent.id
        task.group_id = parent.group_id or parent.id
    if target is not None:
        task.version_time = datetime.now(UTC)
    uploaded_prefix = None
    try:
        _replace_input_entries(task, entries)
        uploaded_prefix = task.input_data_path
        db.add(task)
        db.flush()
        if session.task_id is None:
            session.task_id = task.id
            session.project_id = project_id
        session.published_package_sha256 = revision
        session.published_task_id = task.id
        db.add(session)
        db.commit()
    except Exception:
        db.rollback()
        if uploaded_prefix:
            storage.delete_many(storage.list_objects(uploaded_prefix))
        if created_project is not None:
            db.delete(created_project)
            db.commit()
        raise
    db.refresh(task)
    report["source_task_id"] = str(task.id)
    report["source_task_revision"] = _task_source_revision(task)
    session.validation_report = dict(report)
    db.add(session)
    db.commit()
    autodesign_drafts.synchronize_draft(project, _draft_state_path(workspace, task), _portable_config(task), _task_source_revision(task), lambda: _input_entries(task), initialize=True)
    return schemas.AutoDesignPublishResponse(session=_response(db, session, workspace), task=_task_response(task))


def publish_package(db: Session, current_user: models.User, workspace_id: uuid.UUID, request: schemas.AutoDesignPublishRequest) -> schemas.AutoDesignPublishResponse:
    """Support older callers using the direct verified-directory handoff."""
    return prepare_task(db, current_user, workspace_id, request)


def launch_session(
    db: Session, current_user: models.User, workspace_id: uuid.UUID, request: schemas.AutoDesignPublishRequest
) -> schemas.AutoDesignPublishResponse:
    """Import the verified snapshot and start it once after the user's action."""
    published = publish_package(db, current_user, workspace_id, request)
    task = db.get(models.Task, published.task.id)
    db.refresh(task, with_for_update=True)
    if task.status == models.TaskStatus.UNINITIALIZED:
        token = create_access_token(current_user.id, expires_delta=timedelta(seconds=settings.TASK_TIME_LIMIT + 3600))
        task_service.run_task(db, task.id, current_user, token)
    db.refresh(task)
    return schemas.AutoDesignPublishResponse(session=published.session, task=_task_response(task))


def _memory_target(
    db: Session, current_user: models.User, session: models.AutoDesignSession, task_id: uuid.UUID | None, scope: MemoryScope,
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Resolve card scopes from the conversation rather than model-supplied owners."""
    task = _scoped_task(db, current_user, session, task_id) if task_id or session.task_id else None
    project_id = task.project_id if task else session.project_id
    if scope == "project" and project_id is None:
        raise HTTPException(status_code=409, detail="This design has no project yet; use user memory")
    if scope == "task" and task is None:
        raise HTTPException(status_code=409, detail="This design has no task yet; use user or project memory")
    if scope not in {"user", "project", "task"}:
        raise HTTPException(status_code=422, detail="Choose user, project or task memory")
    return project_id, task.id if task else None


def _memory_operation(db: Session, current_user: models.User, workspace_id: uuid.UUID, request: schemas.AutoDesignOperationRequest) -> dict:
    """Reuse the existing memory service for native Agent reads and authored cards."""
    session, _workspace = _owned(db, current_user, workspace_id)
    args = request.arguments
    scope = args.get("scope", "user")
    project_id, task_id = _memory_target(db, current_user, session, request.task_id, scope)
    if request.operation == "memory_context":
        config = memory_service.get_project_memory_config(db, project_id, current_user) if project_id else memory_service.get_user_memory_config(db, current_user)
        task = db.get(models.Task, task_id) if task_id else None
        return {
            "available": bool(config.system_runtime_available and config.mindmemos_binding_id),
            "scopes": ["user"] + (["project"] if project_id else []) + (["task"] if task_id else []),
            "project_id": str(project_id) if project_id else None,
            "task_id": str(task_id) if task_id else None,
            "root_task_id": str(task.group_id or task.id) if task else None,
            "config": _redact(config.model_dump(mode="json")),
            "task_memory": _redact((task.input_args or {}).get("memory", {})) if task else {},
        }
    if request.operation == "memory_list":
        return _redact(memory_service.list_memory_cards_page(
            db, current_user, scope=scope, project_id=project_id, task_id=task_id,
            page=args.get("page", 1), page_size=args.get("page_size", 20),
        ).model_dump(mode="json"))
    ensure_session_editable(db, current_user, workspace_id)
    return _redact(memory_service.upsert_memory_card(
        db, current_user=current_user, scope=scope, project_id=project_id, task_id=task_id,
        request=MemoryCardUpsertRequest.model_validate(args.get("card")),
    ).model_dump(mode="json"))


def sync_task_draft(db: Session, current_user: models.User, workspace_id: uuid.UUID, task_id: uuid.UUID, resolution: str | None = None) -> dict:
    """Resolve explicitly selected draft conflicts without publishing task inputs."""
    session, workspace = _owned(db, current_user, workspace_id)
    ensure_session_editable(db, current_user, workspace_id)
    task = _scoped_task(db, current_user, session, task_id)
    draft = _ensure_draft(workspace, task, session, resolution=resolution)
    return {"task_id": str(task.id), "draft_path": "/workspace/source/" + draft.relative_to(_source_root(workspace)).as_posix(),
            "conflicts": autodesign_drafts.draft_state(_draft_state_path(workspace, task)).get("conflicts", [])}


def operate(db: Session, current_user: models.User, workspace_id: uuid.UUID, request: schemas.AutoDesignOperationRequest) -> dict:
    """Dispatch a user-initiated action against the currently authorized context."""
    args = request.arguments
    if request.operation in {"start", "stop", "create_child", "patch_child", "sync_draft"} and request.task_id is None:
        _owned(db, current_user, workspace_id)
        raise HTTPException(status_code=422, detail="Pass the explicit task ID returned by current task context")
    if request.operation == "sync_draft":
        return sync_task_draft(db, current_user, workspace_id, request.task_id, args.get("resolution"))
    if request.operation in {"memory_context", "memory_list", "memory_save"}:
        return _memory_operation(db, current_user, workspace_id, request)
    if request.operation == "context":
        return get_context(db, current_user, workspace_id, request.task_id).model_dump(mode="json")
    if request.operation == "rename":
        return update_project_name(db, current_user, workspace_id, args.get("name"))
    if request.operation == "list_files":
        return list_files(db, current_user, workspace_id, args.get("area", "run"), request.task_id, args.get("directory", "")).model_dump(mode="json")
    if request.operation == "read_file":
        return read_file(db, current_user, workspace_id, args.get("area", "run"), args.get("path", ""), request.task_id, args.get("start_line"), args.get("end_line")).model_dump(mode="json")
    if request.operation == "create_child":
        return create_child(db, current_user, workspace_id, request.task_id, args.get("config_patch"), args.get("seed", "input"), args.get("name"))
    if request.operation == "patch_child":
        if request.task_id is None:
            raise HTTPException(status_code=422, detail="Choose the child task to modify")
        return patch_child(db, current_user, workspace_id, request.task_id, args.get("config_patch") or {})
    if request.operation == "validate":
        return validate_package(db, current_user, workspace_id, args.get("project_path", ""), args.get("timeout_seconds", 120))
    if request.operation == "publish":
        return publish_package(db, current_user, workspace_id, schemas.AutoDesignPublishRequest(task_id=request.task_id, **args)).model_dump(mode="json")
    session, _workspace = _owned(db, current_user, workspace_id)
    task = _scoped_task(db, current_user, session, request.task_id)
    if request.operation == "start":
        from app.services.task_service.auth import require_unstarted_task

        ensure_session_editable(db, current_user, workspace_id, task)
        require_unstarted_task(task)
        token = create_access_token(current_user.id, expires_delta=timedelta(seconds=settings.TASK_TIME_LIMIT + 3600))
        return task_service.run_task(db, task.id, current_user, token).model_dump(mode="json")
    if request.operation == "stop":
        return _task_response(task_service.stop_task(db, task.id, current_user)).model_dump(mode="json")
    raise HTTPException(status_code=422, detail="Unsupported AutoDesign operation")
