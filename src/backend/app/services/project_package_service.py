"""Import portable task archives without executing uploaded code."""

import hashlib
import json
import shutil
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import TemporaryDirectory
from typing import BinaryIO

import yaml
from fastapi import HTTPException
from llm4ad.config import AppConfig
from loguru import logger
from pydantic import ValidationError
from sqlmodel import Session

from app import models
from app.core.config import settings
from app.core.storage import storage
from app.schemas.project import ProjectPackageImportResponse
from app.services.project_service import get_project_with_auth


def _relative_path(value: str) -> PurePosixPath:
    """Validate an archive or configuration path inside the task root."""
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or path.is_absolute()
        or PureWindowsPath(value).drive
        or ".." in path.parts
        or path == PurePosixPath(".")
    ):
        raise ValueError(f"Use a package-relative path: {value!r}")
    return path


def _extract_package(source: BinaryIO, destination: Path) -> tuple[Path, list[Path]]:
    """Extract one task root, enforcing the existing task storage quotas."""
    max_total = settings.TASK_MAX_STORAGE_MB * 1024 * 1024
    max_file = settings.TASK_MAX_FILE_SIZE_MB * 1024 * 1024
    source.seek(0, 2)
    if source.tell() > max_total:
        raise ValueError(f"Archive exceeds the {settings.TASK_MAX_STORAGE_MB} MB task storage limit")
    source.seek(0)
    with zipfile.ZipFile(source) as archive:
        files: dict[PurePosixPath, zipfile.ZipInfo] = {}
        total = 0
        seen: set[PurePosixPath] = set()
        for info in archive.infolist():
            path = _relative_path(info.filename)
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
                raise ValueError("Encrypted archives, links and special files are not supported")
            if path in seen:
                raise ValueError(f"Duplicate archive path: {path}")
            seen.add(path)
            if (
                "__MACOSX" in path.parts
                or path.name in {".DS_Store", "Thumbs.db", "desktop.ini"}
                or path.name.startswith("._")
            ):
                continue
            if info.is_dir():
                continue
            if info.file_size > max_file:
                raise ValueError(f"{path} exceeds the {settings.TASK_MAX_FILE_SIZE_MB} MB file limit")
            total += info.file_size
            if total > max_total:
                raise ValueError(f"Expanded archive exceeds the {settings.TASK_MAX_STORAGE_MB} MB task storage limit")
            files[path] = info
        roots = {path.parts[0] for path in files}
        if len(roots) != 1 or any(len(path.parts) < 2 for path in files):
            raise ValueError("The ZIP must contain one task folder with config.yaml inside it")
        root = destination / next(iter(roots))
        if PurePosixPath(root.name, "config.yaml") not in files:
            raise ValueError("The task folder must contain config.yaml")
        for path, info in files.items():
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as incoming, target.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
    return root, [destination.joinpath(*path.parts) for path in files]


def _read_config(root: Path) -> dict:
    """Parse portable settings while preserving explicit tuning parameters."""
    config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("config.yaml must contain a mapping")
    # Task parameters are JSON in the database; reject YAML-only values early.
    config = json.loads(json.dumps(config, allow_nan=False))
    task_name = config.get("project_name")
    if not isinstance(task_name, str) or not task_name.strip() or len(task_name) > 255:
        raise ValueError("project_name must contain 1 to 255 characters")
    for section in ("planner", "coder", "evaluator", "evolution", "version_control"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"Missing configuration section: {section}")
    if config.get("providers"):
        raise ValueError("Cloud packages must use providers: []; select a platform model after import")
    for section in ("planner", "coder", "evaluator"):
        config[section]["provider"] = "default"
        config[section]["provider_model"] = ""
    config["providers"] = []
    config.pop("run_id", None)
    config["path_resolution"] = "config_dir"
    parsed = AppConfig.from_dict(config)
    candidate = root.joinpath(*_relative_path(parsed.version_control.local_path or "").parts)
    if not parsed.version_control.enabled or not candidate.is_dir():
        raise ValueError("version_control must enable an included candidate directory")
    if parsed.evaluator.type == "custom":
        module = parsed.evaluator.module or ""
        filename, separator, class_name = module.partition(":")
        if not separator or not class_name or not filename.endswith(".py"):
            raise ValueError("Use an included evaluator module such as evaluator.py:Evaluator")
        if not root.joinpath(*_relative_path(filename).parts).is_file():
            raise ValueError(f"Evaluator file is missing: {filename}")
    dataset = parsed.evaluator.dataset
    if dataset is not None:
        if dataset.mode == "files":
            paths = [root.joinpath(*_relative_path(value).parts) for value in (dataset.files or [])]
            if not paths or not all(path.is_file() for path in paths):
                raise ValueError("Configured dataset files are missing from the package")
        elif dataset.mode == "directory":
            directory = root if dataset.path == "." else root.joinpath(*_relative_path(dataset.path or "").parts)
            if not directory.is_dir():
                raise ValueError("Configured dataset directory is missing from the package")
            entries = directory.rglob("*") if dataset.recursive else directory.iterdir()
            if not any(path.is_file() for path in entries):
                raise ValueError("Configured dataset directory has no files")
        elif dataset.mode == "glob":
            pattern = _relative_path(dataset.pattern or "").as_posix()
            directory = root
            if dataset.path and dataset.path != ".":
                directory = root.joinpath(*_relative_path(dataset.path).parts)
            matches = list(directory.glob(pattern))
            if not matches:
                raise ValueError("Dataset pattern does not match any packaged files")
            if not all(path.is_file() for path in matches):
                raise ValueError("Dataset pattern must match files, not directories")
    return config


def _verify_report(root: Path, files: list[Path]) -> None:
    """Check report hashes when present, without trusting a local execution claim."""
    report_path = root / "validation.json"
    if not report_path.is_file():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or not isinstance(report.get("files_sha256"), dict):
        raise ValueError("validation.json must contain the packaged files_sha256 mapping")
    actual = {}
    for path in files:
        if path != report_path:
            with path.open("rb") as stream:
                actual[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    if report["files_sha256"] != actual:
        raise ValueError("Package files differ from validation.json; validate and pack the project again")


def _import_package(
    db: Session,
    current_user: models.User,
    source: BinaryIO,
    *,
    name: str | None = None,
    description: str | None = None,
    language: str = "en",
    project_id: uuid.UUID | None = None,
) -> ProjectPackageImportResponse:
    """Import a task into an authorized project or create its project.

    Args:
        db: Request-scoped database session.
        current_user: Authenticated owner of the new project.
        source: Seekable uploaded ZIP stream.
        name: Optional task name for an existing project, or a new project name.
        description: Optional project description.
        language: Preferred language for the package description.
        project_id: Existing project receiving the task, when supplied.

    Returns:
        Created project and task identifiers, ready for model selection.
    """
    project = get_project_with_auth(db, project_id, current_user) if project_id is not None else None
    with TemporaryDirectory(prefix="llm4ad-cloud-import-") as temporary:
        try:
            root, files = _extract_package(source, Path(temporary))
            _verify_report(root, files)
            config = _read_config(root)
        except ValidationError as exc:
            errors = "; ".join(f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors())
            raise HTTPException(status_code=400, detail=f"Invalid task configuration: {errors}") from exc
        except (ValueError, TypeError, OSError, RuntimeError, zipfile.BadZipFile, yaml.YAMLError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid project package: {exc}") from exc

        requested_name = (name or "").strip() or config["project_name"]
        if len(requested_name) > 255:
            raise HTTPException(status_code=400, detail="Name must not exceed 255 characters")
        task_name = requested_name if project is not None else config["project_name"]
        config["project_name"] = task_name
        localized_description = config.get("description_zh" if language == "zh" else "description_en", "")
        project_description = description if description is not None else localized_description
        if not isinstance(project_description, str):
            project_description = ""
        project_description = project_description[:255]
        config.pop("description_en", None)
        config.pop("description_zh", None)
        if project is None:
            project = models.Project(name=requested_name, description=project_description, user_id=current_user.id)
        task = models.Task(
            name=task_name,
            description=project_description,
            project_id=project.id,
            input_args=config,
            status=models.TaskStatus.UNINITIALIZED,
            ai_built=False,
        )
        prefix = f"tasks/{task.id}/{uuid.uuid4().hex}"
        task.input_data_path = prefix
        uploaded: list[str] = []
        try:
            db.add(project)
            db.flush()
            db.add(task)
            db.flush()
            for path in files:
                key = f"{prefix}/{root.name}/{path.relative_to(root).as_posix()}"
                uploaded.append(key)
                with path.open("rb") as stream:
                    storage.upload(key, stream)
            db.commit()
        except Exception:
            db.rollback()
            try:
                storage.delete_many(uploaded)
            except Exception:
                logger.exception("Failed to clean up an interrupted project package import: {}", prefix)
            raise
        return ProjectPackageImportResponse(
            project_id=project.id,
            task_id=task.id,
            task_name=task_name,
            evolution_type=config["evolution"]["type"],
            file_count=len(files),
        )


def import_project_package(
    db: Session,
    current_user: models.User,
    source: BinaryIO,
    *,
    name: str | None = None,
    description: str | None = None,
    language: str = "en",
) -> ProjectPackageImportResponse:
    """Import a package with a new project for existing API clients."""
    return _import_package(db, current_user, source, name=name, description=description, language=language)


def import_task_package(
    db: Session,
    current_user: models.User,
    project_id: uuid.UUID,
    source: BinaryIO,
    *,
    name: str | None = None,
    language: str = "en",
) -> models.Task:
    """Create a task in an existing authorized project from a portable ZIP.

    Args:
        db: Request-scoped database session.
        current_user: Authenticated user importing the task.
        project_id: Existing project receiving the new task.
        source: Seekable uploaded ZIP stream.
        name: Optional task name overriding the package configuration.
        language: Preferred language for the task description.

    Returns:
        The imported task, ready for selection and model configuration.
    """
    result = _import_package(db, current_user, source, project_id=project_id, name=name, language=language)
    return db.get_one(models.Task, result.task_id)
