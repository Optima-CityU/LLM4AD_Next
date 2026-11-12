"""Atomic job records and immutable candidate input snapshots."""

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path

from .contracts import BackendNotStartedError


def read_json(path):
    """Read one persisted JSON job or configuration record."""
    return json.loads(Path(path).read_text())


def sync_directory(path):
    """Persist directory entries after an atomic metadata publication.

    Args:
        path: Directory containing the newly published entries.
    """
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def write_json(path, value):
    """Atomically replace and sync a JSON record before acknowledging it."""
    path = Path(path)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
        sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def freeze_shader_inputs(folder, frozen):
    """Snapshot shader files and reference images before publishing a job.

    Args:
        folder: New job directory retaining the immutable copies.
        frozen: Prepared submission whose backend paths can be rewritten.

    Raises:
        ValueError: An input is not stable or references conflict.
        OSError: An input cannot be copied and synced.
    """
    request = frozen["submission"].get("request", {})
    if request.get("kind") != "shader":
        return
    payload = request.get("payload", {})
    conditions = request.get("conditions", {})
    if not isinstance(payload, dict) or not isinstance(conditions, dict):
        raise ValueError("shader payload and conditions must be objects")
    manifest = []

    def snapshot(source, destination):
        source = Path(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream, destination.open("xb") as output:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("shader inputs must be regular files")
            while chunk := stream.read(1048576):
                output.write(chunk)
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("shader input changed while being frozen")
            output.flush()
            os.fchmod(output.fileno(), 0o444)
            os.fsync(output.fileno())
        parent = destination.parent
        while parent != folder:
            sync_directory(parent)
            parent = parent.parent
        manifest.append(
            {
                "source": str(source),
                "path": str(destination.relative_to(folder)),
                "sha256": digest.hexdigest(),
                "bytes": before.st_size,
            }
        )
        return str(destination)

    assets = folder / "inputs" / "shader"
    if payload.get("shader_path") is not None:
        payload["shader_path"] = snapshot(payload["shader_path"], assets / "candidate.comp")
    if payload.get("spirv_path") is not None:
        original = Path(payload["spirv_path"])
        payload["spirv_path"] = snapshot(original, assets / "candidate.spv")
        snapshot(original.parent / "baseline.spv", assets / "baseline.spv")
    references = [
        section["quality_reference"] for section in (payload, conditions) if section.get("quality_reference") is not None
    ]
    if references:
        if any(value != references[0] for value in references):
            raise ValueError("quality_reference conflicting payload/conditions")
        reference = Path(references[0])
        destination = folder / "inputs" / "reference"
        if reference.is_dir():
            images = sorted(p for p in reference.iterdir() if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg"))
            if not images:
                raise ValueError("quality_reference contains no images")
            if len(images) > 32:
                images = [images[(i * (len(images) - 1) + 15) // 31] for i in range(32)]
            for image in images:
                snapshot(image, destination / image.name)
            frozen_reference = str(destination)
        else:
            frozen_reference = snapshot(reference, destination / reference.name)
        for section in (payload, conditions):
            if section.get("quality_reference") is not None:
                section["quality_reference"] = frozen_reference
    frozen["input_files"] = manifest


def verify_frozen_inputs(folder, frozen):
    """Verify the staged file manifest before starting a device worker.

    Args:
        folder: Job directory retaining the staged files.
        frozen: Persisted submission and its file manifest.

    Raises:
        BackendNotStartedError: A staged file no longer matches its recorded bytes.
    """
    manifest = frozen.get("input_files", [])
    if "input_files" in frozen:
        expected = {item["path"] for item in manifest}
        actual = {str(path.relative_to(folder)) for path in (folder / "inputs").rglob("*") if path.is_file()}
        if actual != expected:
            raise BackendNotStartedError("frozen shader input file set changed; device worker not started")
    for item in manifest:
        digest = hashlib.sha256()
        with (folder / item["path"]).open("rb") as stream:
            while chunk := stream.read(1048576):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise BackendNotStartedError("frozen shader input changed; device worker not started")
