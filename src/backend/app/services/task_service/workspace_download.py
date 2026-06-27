"""Task IDE workspace archive download helpers."""

from __future__ import annotations

import fnmatch
import re
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from sqlmodel import Session, select

from app import models
from app.core.config import settings

from .auth import get_task_with_auth

EXCLUDED_DIR_NAMES = {
    ".cache",
    ".git",
    ".ipynb_checkpoints",
    ".mypy_cache",
    ".next",
    ".pnpm-store",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".uv",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
}

EXCLUDED_FILE_PATTERNS = {
    ".DS_Store",
    "*.pyc",
    "*.pyo",
    "*.swo",
    "*.swp",
    "*.temp",
    "*.tmp",
}

DEFAULT_MAX_FILES = 10_000
DEFAULT_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


def download_task_workspace(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
) -> tuple[Path, str]:
    """Authorize and build a ZIP archive for a task IDE workspace."""
    task = get_task_with_auth(db, task_id, current_user)
    project = db.get(models.Project, task.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    workspace_root = Path(settings.DOCKER_PROJECT_HOME) / f"code_user-{project.user_id}" / str(task.id)
    filename = _workspace_download_filename(db, project, task)
    return build_workspace_archive(workspace_root, filename)


def workspace_attachment_disposition(filename: str) -> str:
    """Build a safe attachment header value for workspace archive downloads."""
    cleaned = re.sub(r'[\r\n"\\]', "", filename) or "workspace.zip"
    ascii_safe = cleaned.encode("ascii", errors="ignore").decode("ascii") or "workspace.zip"
    encoded = quote(cleaned, safe="")
    return f'attachment; filename="{ascii_safe}"; filename*=UTF-8\'\'{encoded}'


def build_workspace_archive(
    workspace_root: Path,
    download_filename: str,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
) -> tuple[Path, str]:
    """Build a temporary ZIP archive for a workspace.

    The caller owns the returned file and must remove it after the response is sent.
    """
    root = workspace_root.resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(
            status_code=404,
            detail="任务工作区不存在，请先运行任务",
        )

    tmp = tempfile.NamedTemporaryFile(
        prefix="llm4ad-workspace-",
        suffix=".zip",
        delete=False,
    )
    tmp_path = Path(tmp.name)
    tmp.close()

    file_count = 0
    total_size = 0
    try:
        with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in _iter_archive_files(root):
                resolved = path.resolve()
                if not _is_relative_to(resolved, root):
                    continue

                relative = path.relative_to(root)
                arcname = relative.as_posix()
                if not _is_safe_archive_name(arcname):
                    continue

                size = resolved.stat().st_size
                file_count += 1
                total_size += size
                if file_count > max_files:
                    raise HTTPException(status_code=413, detail="任务工作区文件数量过多，无法打包下载")
                if total_size > max_uncompressed_bytes:
                    raise HTTPException(status_code=413, detail="任务工作区过大，无法打包下载")

                archive.write(resolved, arcname)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return tmp_path, download_filename


def _workspace_download_filename(
    db: Session,
    project: models.Project,
    task: models.Task,
) -> str:
    project_name = _safe_filename_part(project.name)
    task_name = _safe_filename_part(task.name)
    version = _task_version_index(db, task)
    exported_at = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    parts = [
        "LLM4AD",
        project_name,
        task_name,
        f"v{version}",
        exported_at,
        "workspace",
    ]
    stem = "-".join(part for part in parts if part)
    return f"{stem or 'LLM4AD-workspace'}.zip"


def _task_version_index(db: Session, task: models.Task) -> int:
    root_id = task.group_id or task.id
    root = db.get(models.Task, root_id)
    versions = []
    if root is not None:
        versions.append(root)
    versions.extend(
        db.exec(select(models.Task).where(models.Task.group_id == root_id)).all()
    )
    versions.sort(key=lambda item: item.created_time)
    for index, item in enumerate(versions):
        if item.id == task.id:
            return index
    return 0


def _safe_filename_part(value: str | None) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[\x00-\x1f\x7f/\\:\"'<>|?*]+", "-", value)
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-. ")
    return cleaned


def _iter_archive_files(root: Path):
    stack = [root]
    while stack:
        current = stack.pop()
        for child in sorted(current.iterdir(), key=lambda p: p.name):
            if child.is_dir() and not child.is_symlink():
                if _should_exclude_dir(child.name):
                    continue
                stack.append(child)
                continue
            if child.is_file() or child.is_symlink():
                if _should_exclude_file(child.name):
                    continue
                yield child


def _should_exclude_dir(name: str) -> bool:
    return name in EXCLUDED_DIR_NAMES


def _should_exclude_file(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_FILE_PATTERNS)


def _is_safe_archive_name(name: str) -> bool:
    path = Path(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
