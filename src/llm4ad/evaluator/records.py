"""Capture evaluation records before disposable candidate worktrees disappear."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from llm4ad.evaluator.models import EvaluationArtifact, EvaluationFile, EvaluationRecord, artifact_media_type


def json_safe(value: Any) -> Any:
    """Return strict JSON data, replacing non-finite measurements with null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


@contextmanager
def owned_directory(path: Path, root: Path):
    """Pin an owned directory without following mutable child symlinks."""
    relative = path.absolute().relative_to(root.absolute())
    if any(part in {".", ".."} for part in relative.parts) or root.is_symlink():
        raise ValueError("Invalid owned directory")
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ValueError("Owned directories must not contain symlinks")
        yield None
        return
    descriptors = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors.append(os.open(root, flags))
        for part in relative.parts:
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
        yield descriptors[-1]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@contextmanager
def open_owned_file(path: Path, root: Path):
    """Read one regular file using pinned directories and a non-following open."""
    with owned_directory(path.parent, root) as directory:
        if directory is None:
            with regular_file(path, root).open("rb") as stream:
                yield stream
            return
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Evidence must be a regular file")
            yield stream


def read_owned_file(path: Path, root: Path) -> bytes:
    """Read manifest or receipt data without following child symlinks."""
    with open_owned_file(path, root) as stream:
        return stream.read()


def prune_owned_bulk(path: Path, root: Path) -> None:
    """Remove acknowledged output directories relative to a pinned record root."""
    with owned_directory(path, root) as directory:
        for name in ("files", "output"):
            if directory is None:
                shutil.rmtree(path / name, ignore_errors=True)
            else:
                shutil.rmtree(name, dir_fd=directory, ignore_errors=True)


def atomic_json(path: Path, value: Any, *, owned_root: Path | None = None) -> None:
    """Publish complete JSON using a same-directory atomic replacement."""
    if owned_root is not None:
        with owned_directory(path.parent, owned_root) as directory:
            if directory is not None:
                temporary = f".{uuid.uuid4().hex}.tmp"
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                        json.dump(json_safe(value), stream, ensure_ascii=False, allow_nan=False)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
                finally:
                    with suppress(FileNotFoundError):
                        os.unlink(temporary, dir_fd=directory)
                return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(json_safe(value), stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


def regular_file(path: Path, root: Path) -> Path:
    """Resolve a regular file under its owned root without following symlinks."""
    root = root.absolute()
    path = path.absolute()
    if not path.is_relative_to(root):
        raise ValueError("Artifact is outside its evaluation directory")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Artifact paths must not contain symlinks")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError("Artifact must be a regular file inside its evaluation directory")
    return resolved


def snapshot_file(root: Path, artifact: EvaluationArtifact) -> EvaluationFile:
    """Copy a declared output to a content-addressed immutable snapshot."""
    source = Path(artifact.path)
    if not source.is_absolute():
        source = root / "output" / source
    source = regular_file(source, root / "output")
    name = artifact.name.replace("\\", "/")
    if not name or name.startswith("/") or any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError("Artifact name must be a relative logical filename")
    files = root / "files"
    files.mkdir(exist_ok=True)
    hasher = hashlib.sha256()
    size = 0
    with source.open("rb") as incoming, tempfile.NamedTemporaryFile(dir=files, delete=False) as outgoing:
        temporary = Path(outgoing.name)
        try:
            while chunk := incoming.read(1024 * 1024):
                outgoing.write(chunk)
                hasher.update(chunk)
                size += len(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    digest = hasher.hexdigest()
    temporary.replace(files / digest)
    return EvaluationFile(
        id=hashlib.sha256(f"{name}|{digest}".encode()).hexdigest(),
        name=name,
        sha256=digest,
        size=size,
        media_type=artifact_media_type(name, artifact.media_type),
        description=artifact.description,
    )


def behavior_artifacts(root: Path, result) -> list[EvaluationArtifact]:
    """Materialize explicitly returned behavior images and raw visualization data."""
    declarations = []
    behaviors = result.metadata.get("all_behavior") or ([result.behavior] if result.behavior else [])
    for behavior_index, behavior in enumerate(behaviors):
        if isinstance(behavior, dict):
            from llm4ad.evaluator.behavior import BehaviorData

            behavior = BehaviorData.model_validate(behavior)
        for index, visualization in enumerate(behavior.visualizations):
            prefix = f"behavior/{behavior_index}-{index}"
            if visualization.data_base64:
                extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(visualization.media_type, "bin")
                name = f"{prefix}.{extension}"
                destination = root / "output" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(base64.b64decode(visualization.data_base64, validate=True))
                declarations.append(
                    EvaluationArtifact(
                        name=name,
                        path=name,
                        media_type=visualization.media_type,
                        description=visualization.label,
                    )
                )
            if visualization.raw_data is not None:
                name = f"{prefix}.json"
                atomic_json(
                    root / "output" / name,
                    {
                        "renderer": visualization.renderer,
                        "data": visualization.model_dump(mode="json")["raw_data"],
                    },
                )
                declarations.append(
                    EvaluationArtifact(
                        name=name,
                        path=name,
                        media_type="application/json",
                        description=visualization.label,
                    )
                )
    return declarations


def new_record(cfg, *, task_id: str = "", origin: str = "local") -> EvaluationRecord:
    """Build a small running record using the dispatcher-owned identity."""
    return EvaluationRecord(
        id=cfg.evaluation_id,
        task_id=task_id,
        execution_id=cfg.execution_id,
        algorithm_id=cfg.algorithm_id,
        algorithm_name=cfg.algorithm_name,
        source_fingerprint=cfg.source_fingerprint,
        evaluation_profile=cfg.evaluation_profile,
        instance_id=cfg.instance_id,
        origin=origin,
        state="running",
        created_at=datetime.now(UTC).isoformat(),
    )


def complete_record(root: Path, record: EvaluationRecord, result) -> EvaluationRecord:
    """Retain declared outputs and details without changing the evaluator result."""
    declarations = list(result.artifacts)
    try:
        declarations.extend(behavior_artifacts(root, result))
    except (OSError, ValueError) as exc:
        record.storage_errors.append(f"Behavior: {exc}")
    names = set()
    for artifact in declarations:
        try:
            if artifact.name in names:
                raise ValueError("Duplicate artifact name")
            names.add(artifact.name)
            record.artifacts.append(snapshot_file(root, artifact))
        except (OSError, ValueError) as exc:
            record.storage_errors.append(f"{artifact.name}: {exc}")
    record.state = "completed" if result.success else "failed"
    try:
        record.result = result.model_dump(mode="json", exclude={"artifacts", "behavior"})
    except Exception as error:
        record.storage_errors.append(f"Structured evaluation details could not be serialized ({type(error).__name__})")
        record.result = result.model_dump(
            mode="json",
            include={
                "score",
                "metrics",
                "monitor_metrics",
                "success",
                "error_message",
                "duration_ms",
            },
        )
        record.result["metadata"] = {}
    record.result["metadata"].pop("all_behavior", None)
    if result.behavior:
        record.result["observation"] = result.behavior.observation
    record.storage_state = "error" if record.storage_errors else "pending"
    atomic_json(root / "record.json", record.model_dump(mode="json"))
    # The immutable files now own retention; disposable output copies are redundant.
    if not record.storage_errors:
        shutil.rmtree(root / "output", ignore_errors=True)
    return record
