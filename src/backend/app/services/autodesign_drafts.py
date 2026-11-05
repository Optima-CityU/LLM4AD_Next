"""Reconcile canonical task inputs with editable drafts without losing either side."""

import copy
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import yaml
from fastapi import HTTPException

from app.services.paper_workspace_runtime import prepare_paper_workspace_path

_MISSING = object()


def _write(path: Path, content: bytes, owner: Path, expected=_MISSING) -> None:
    """Atomically replace one file while retaining the runtime's write access."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        raise HTTPException(status_code=409, detail="Resolve the conflicting draft directory before synchronization")
    previous = path.stat() if path.exists() else owner.stat()
    mode = previous.st_mode & 0o777 if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(content)
    try:
        temporary.chmod(mode)
        try:
            os.chown(temporary, previous.st_uid, previous.st_gid)
        except PermissionError:
            pass
        if expected is not _MISSING:
            current = _hash(path.read_bytes()) if path.is_file() else None
            if current != expected:
                raise HTTPException(
                    status_code=409, detail="Draft changed during synchronization; refresh its current conflicts"
                )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _hash(content: bytes) -> str:
    """Fingerprint input bytes without persisting a second copy of user files."""
    return hashlib.sha256(content).hexdigest()


def _merge(base, current, draft, path: str, conflicts: list[str], resolution: str | None):
    """Merge independent field changes and retain conflicting values for resolution."""
    if current == base:
        return copy.deepcopy(draft) if draft is not _MISSING else _MISSING
    if draft == base or draft == current:
        return copy.deepcopy(current) if current is not _MISSING else _MISSING
    if isinstance(current, dict) and isinstance(draft, dict) and (isinstance(base, dict) or base is _MISSING):
        original = base if isinstance(base, dict) else {}
        result = {}
        for key in original.keys() | current.keys() | draft.keys():
            value = _merge(
                original.get(key, _MISSING),
                current.get(key, _MISSING),
                draft.get(key, _MISSING),
                f"{path}.{key}",
                conflicts,
                resolution,
            )
            if value is not _MISSING:
                result[key] = value
        return result
    if resolution is None:
        conflicts.append(path)
    value = current if resolution == "use_task" else draft
    return copy.deepcopy(value) if value is not _MISSING else _MISSING


def draft_state(path: Path) -> dict:
    """Read private synchronization state stored outside published project inputs."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def synchronize_draft(
    draft: Path,
    state_path: Path,
    config: dict,
    revision: str,
    input_loader: Callable[[], dict[str, bytes]],
    *,
    initialize: bool = False,
    resolution: str | None = None,
) -> list[str]:
    """Update untouched inputs and report concurrent edits without discarding them.

    Args:
        draft: Editable project directory owned by the workspace runtime.
        state_path: Private canonical baseline, outside the published draft.
        config: Current portable task parameters.
        revision: Current canonical task input revision.
        input_loader: Lazy reader for canonical object-storage inputs.
        initialize: Whether this is a newly seeded draft.
        resolution: Explicit keep_draft or use_task conflict resolution.

    Returns:
        Conflicting configuration fields or input paths requiring resolution.
    """
    if resolution not in {None, "keep_draft", "use_task"}:
        raise HTTPException(status_code=422, detail="Choose keep_draft or use_task")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(".lock")
    with lock_path.open("a") as lock:
        # Native workspaces run in Linux containers with multiple API workers.
        if os.name == "posix":
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        original = draft_state(state_path)
        if (
            not initialize
            and original.get("revision") == revision
            and not original.get("conflicts")
            and resolution is None
        ):
            return []
        current_files = {name: value for name, value in input_loader().items() if name != "config.yaml"}
        hashes = {name: _hash(value) for name, value in current_files.items()}
        conflicts = []
        if not initialize:
            if (draft / "config.yaml").is_symlink():
                raise HTTPException(status_code=400, detail="Draft configuration cannot be a symbolic link")
            local_bytes = None
            try:
                local_bytes = (draft / "config.yaml").read_bytes()
                local_config = yaml.safe_load(local_bytes.decode("utf-8-sig"))
            except (OSError, ValueError, yaml.YAMLError):
                local_config = None
            if not isinstance(local_config, dict):
                if resolution == "use_task":
                    _write(
                        draft / "config.yaml",
                        yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode(),
                        draft,
                        _hash(local_bytes) if local_bytes is not None else None,
                    )
                    local_config = config
                else:
                    conflicts.append("config")
            if isinstance(local_config, dict):
                merged = _merge(original.get("config", {}), config, local_config, "config", conflicts, resolution)
                if merged != local_config:
                    _write(
                        draft / "config.yaml",
                        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False).encode(),
                        draft,
                        _hash(local_bytes) if local_bytes is not None else None,
                    )
            old_files = original.get("files", hashes)
            for name in old_files.keys() | hashes.keys():
                target = (draft / name).resolve()
                if not target.is_relative_to(draft.resolve()) or (draft / name).is_symlink():
                    raise HTTPException(status_code=400, detail="Draft input is outside its project")
                local = _hash(target.read_bytes()) if target.is_file() else None
                old, current = old_files.get(name), hashes.get(name)
                legacy_conflict = (
                    (not original or "file:" + name in original.get("conflicts", []))
                    and name in hashes
                    and local != current
                    and old == current
                )
                if old == current and not legacy_conflict:
                    continue
                if local not in {old, current} or legacy_conflict:
                    if resolution is None:
                        conflicts.append("file:" + name)
                        continue
                    if resolution == "keep_draft":
                        continue
                if local != current:
                    if name in current_files:
                        _write(target, current_files[name], draft, local)
                    else:
                        current_local = _hash(target.read_bytes()) if target.is_file() else None
                        if current_local != local:
                            raise HTTPException(
                                status_code=409, detail="Draft changed during synchronization; refresh its conflicts"
                            )
                        target.unlink(missing_ok=True)
        next_state = (
            (original or {"config": {}, "files": hashes, "revision": ""})
            if conflicts
            else {"config": config, "files": hashes, "revision": revision}
        )
        next_state = {**next_state, "conflicts": sorted(conflicts), "project_path": str(draft)}
        _write(state_path, json.dumps(next_state, ensure_ascii=False).encode(), state_path.parent)
        prepare_paper_workspace_path(draft, recursive=True)
        return sorted(conflicts)
