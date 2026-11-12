"""Install the official SDK's pinned processor during the image build."""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import urllib.request
from pathlib import Path


def install(target: Path, cache: Path, url: str | None = None) -> None:
    """Fetch an architecture-matched binary and verify the official SDK checksum.

    Args:
        target: Executable destination in the image.
        cache: BuildKit cache for verified downloads.
        url: Optional mirror URL serving the identical pinned binary.
    """
    from perfetto.trace_processor.platform import TRACE_PROCESSOR_SHELL_MANIFEST

    entry = next(
        (
            item
            for item in TRACE_PROCESSOR_SHELL_MANIFEST
            if item.get("platform") == platform.system().lower() and platform.machine().lower() in item.get("machine", [])
        ),
        None,
    )
    if entry is None:
        raise RuntimeError("The pinned official Trace Processor does not support this image architecture")
    cache.mkdir(parents=True, exist_ok=True)
    downloaded = cache / entry["sha256"]
    if not downloaded.is_file() or hashlib.sha256(downloaded.read_bytes()).hexdigest() != entry["sha256"]:
        temporary = downloaded.with_suffix(".partial")
        with urllib.request.urlopen(url or entry["url"], timeout=120) as response, temporary.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != entry["sha256"]:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Official Trace Processor checksum mismatch")
        temporary.replace(downloaded)
    shutil.copyfile(downloaded, target)
    target.chmod(0o755)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--cache", type=Path, default=Path("/var/cache/trace-processor"))
    parser.add_argument("--url")
    arguments = parser.parse_args()
    install(arguments.target, arguments.cache, arguments.url or None)
