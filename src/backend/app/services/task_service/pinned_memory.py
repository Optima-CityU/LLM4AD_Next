"""Read/write the runtime pinned shared-memory file for a task.

Manual retrieval mode injects a fixed, user-selected set of shared (global/
project) memories. The selection lives in ``{run_dir}/memory/pinned_memory.json``
under the task's run directory — the same file the evolution engine re-reads on
each injection, so edits here take effect on the next injection without
restarting the task.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlmodel import Session

from app import models
from app.core.config import settings

from .auth import get_task_with_auth

# Must match llm4ad.planner.mindmemos_memory.PINNED_MEMORY_FILENAME and the run
# directory layout produced by task_service.execution.run_task.
PINNED_MEMORY_FILENAME = "pinned_memory.json"
_RUN_MEMORY_SUBPATH = ("llm4ad", "run", "memory")


def _pinned_memory_path(task: models.Task, current_user: models.User) -> Path:
    """Resolve the pinned-memory file path for a task's run directory.

    Args:
        task: The task whose run directory to target.
        current_user: The task owner (run dirs are namespaced per user).

    Returns:
        Absolute path to the task's ``pinned_memory.json``.
    """
    container_name = f"code_user-{current_user.id}"
    base = Path(settings.DOCKER_PROJECT_HOME) / container_name / str(task.id)
    return base.joinpath(*_RUN_MEMORY_SUBPATH, PINNED_MEMORY_FILENAME)


def _normalize_pinned_ids(raw: object) -> list[str]:
    """Normalize a JSON/API pinned-id list without accepting other shapes."""
    if not isinstance(raw, list):
        return []
    return [str(cid) for cid in raw if str(cid)]


def _read_runtime_pinned_ids(path: Path) -> list[str] | None:
    """Read a valid runtime selection, or ``None`` when it is unavailable.

    An empty, valid JSON selection is deliberately distinct from an unavailable
    file: the former represents a user clearing all pins while the latter can
    occur in the short interval while a task run directory is being recreated.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pinned_card_ids"), list):
        return None
    return _normalize_pinned_ids(data["pinned_card_ids"])


def _configured_pinned_ids(task: models.Task) -> list[str]:
    """Return the saved manual-mode selection embedded in task input args."""
    input_args = task.input_args
    memory_config = input_args.get("memory") if isinstance(input_args, dict) else None
    if not isinstance(memory_config, dict) or memory_config.get("retrieval_mode") != "manual":
        return []
    return _normalize_pinned_ids(memory_config.get("pinned_card_ids"))


def _current_or_configured_pinned_ids(task: models.Task, path: Path) -> list[str]:
    """Prefer the live runtime selection, with config as a startup fallback."""
    runtime_ids = _read_runtime_pinned_ids(path)
    return _configured_pinned_ids(task) if runtime_ids is None else runtime_ids


def get_task_pinned_memory(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
) -> list[str]:
    """Return the current pinned shared-memory ids for a task.

    Args:
        db: Database session.
        task_id: Target task id.
        current_user: Authenticated user (must own the task).

    Returns:
        The current runtime pinned memory card ids. When a manual-mode task is
        starting and its runtime file is not available yet, returns the saved
        task configuration so the UI does not transiently display no selection.
    """
    task = get_task_with_auth(db, task_id, current_user)
    path = _pinned_memory_path(task, current_user)
    return _current_or_configured_pinned_ids(task, path)


def set_task_pinned_memory(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    pinned_card_ids: list[str],
) -> list[str]:
    """Replace the pinned shared-memory id set for a task (atomic write).

    Writes to a temporary file then renames it, so the evolution engine never
    reads a half-written file mid-injection.

    Args:
        db: Database session.
        task_id: Target task id.
        current_user: Authenticated user (must own the task).
        pinned_card_ids: New pinned memory card ids to persist.

    Returns:
        The normalized pinned memory card ids that were written.
    """
    task = get_task_with_auth(db, task_id, current_user)
    path = _pinned_memory_path(task, current_user)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_pinned_ids(pinned_card_ids)
    payload = {"pinned_card_ids": normalized}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    tmp_path.replace(path)
    return normalized


def seed_task_pinned_memory(
    task: models.Task,
    current_user: models.User,
    pinned_card_ids: list[str],
) -> None:
    """Seed the pinned-memory file from task config at run submission.

    Called synchronously when a task is (re)submitted so the runtime file
    reflects the latest wizard selection immediately — before the frontend
    refetches and before the worker starts. The worker re-seeds the same file at
    run start too; both write the same config value, so they are consistent.

    Args:
        task: The task being submitted (already authorized by the caller).
        current_user: The task owner.
        pinned_card_ids: Pinned memory card ids from the task's memory config.
    """
    path = _pinned_memory_path(task, current_user)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_pinned_ids(pinned_card_ids)
    payload = {"pinned_card_ids": normalized}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    tmp_path.replace(path)
