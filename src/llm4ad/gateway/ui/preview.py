"""Bounded, plain-text artifact previews without executable HTML or local routes."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

PREVIEW_LIMIT = 2 * 1024 * 1024
_TEXT_SUFFIXES = {
    ".txt",
    ".log",
    ".json",
    ".jsonl",
    ".md",
    ".markdown",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".xml",
    ".py",
    ".comp",
    ".frag",
    ".vert",
    ".glsl",
    ".patch",
}


def can_preview(name: str, media_type: str = "") -> bool:
    """Offer previews only for declared text-like content."""
    return (
        Path(name).suffix.lower() in _TEXT_SUFFIXES
        or media_type.startswith("text/")
        or media_type.split(";", 1)[0] in {"application/json", "application/ld+json", "application/x-ndjson"}
    )


async def read_preview(path: Path, *, limit: int = PREVIEW_LIMIT) -> tuple[str, bool]:
    """Read at most a bounded prefix from a controller-authorized artifact.

    Args:
        path: Immutable artifact path already checked by controller.artifact_path.
        limit: Maximum preview size in bytes, independent of original file size.

    Returns:
        Decoded plain text and whether the original file is larger than the preview.

    Raises:
        ValueError: If the artifact is binary or the limit is invalid.
        OSError: If the artifact became unavailable.
    """
    if not 0 < limit <= PREVIEW_LIMIT:
        raise ValueError("Invalid preview limit")

    def read() -> tuple[str, bool]:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(limit + 1)
        if b"\x00" in data:
            raise ValueError("Binary artifact preview unavailable")
        return data[:limit].decode("utf-8", errors="replace"), len(data) > limit

    return await asyncio.to_thread(read)
