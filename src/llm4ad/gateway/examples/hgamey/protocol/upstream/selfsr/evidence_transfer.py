"""Download immutable device evidence with bounded reads and durable progress."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path


def _save(path: Path, value: dict) -> None:
    """Persist a download checkpoint before acknowledging its bytes."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _source(dev, remote: str) -> dict:
    """Read the exact device, boot, file length and digest without changing it."""
    command = (
        "getprop ro.serialno; cat /proc/sys/kernel/random/boot_id; "
        f"stat -c '%s' {shlex.quote(remote)}; sha256sum {shlex.quote(remote)}"
    )
    lines = dev.su(command, timeout=30).strip().splitlines()
    if (len(lines) != 4 or not lines[0] or not lines[1] or not lines[2].isdigit()
            or not re.fullmatch(r"[a-f0-9]{64}\s+.+", lines[3])):
        raise ConnectionError("Complete device/file identity is unavailable; original evidence retained")
    return {"remote": remote, "serial": lines[0], "boot_id": lines[1],
            "size": int(lines[2]), "sha256": lines[3].split()[0]}


def download_root_file(dev, remote: str, destination: str | Path, *, timeout: float = 600,
                       chunk_size: int = 1024 * 1024, retries: int = 3) -> dict:
    """Fetch the same recording after interruption without replaying acquisition.

    Args:
        dev: Existing leased Dev transport; its ADB calls must check exit status.
        remote: Unique immutable recording whose writer has already exited.
        destination: Owned local evidence file; published only after verification.
        timeout: Total download budget, separate from the measurement window.
        chunk_size: Bytes in each bounded ADB read.
        retries: Maximum attempts for an individual chunk.

    Returns:
        Durable receipt binding the completed local bytes to the original file.
        The caller owns remote removal, after this function returns successfully.
    """
    if timeout <= 0 or chunk_size <= 0 or retries < 1:
        raise ValueError("Download budgets must be positive")
    path = Path(destination)
    receipt = path.with_suffix(path.suffix + ".transfer.json")
    partial = path.with_suffix(path.suffix + ".part")
    chunk = path.with_suffix(path.suffix + ".chunk")
    source = _source(dev, remote)
    state = json.loads(receipt.read_text()) if receipt.exists() else {}
    if state.get("remote", remote) != remote or state.get("source", source) != source:
        raise ValueError("Recording identity changed; refusing to combine evidence")
    if not state.get("source"):
        if partial.exists() or path.exists():
            raise FileExistsError("Existing evidence has no matching transfer receipt")
        state = {"schema": "llm4ad/evidence-transfer@1", "remote": remote, "source": source,
                 "bytes_complete": 0, "prefix_sha256": hashlib.sha256(b"").hexdigest(),
                 "chunk_size": chunk_size, "complete": False}
        _save(receipt, state)
    if state["chunk_size"] != chunk_size:
        raise ValueError("Chunk layout changed; refusing to combine evidence")
    offset = state["bytes_complete"]
    if not isinstance(offset, int) or not 0 <= offset <= source["size"]:
        raise ValueError("Invalid persisted evidence offset")
    if path.exists():
        if (offset != source["size"] or path.stat().st_size != source["size"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]):
            raise ValueError("Published local evidence changed")
        state["complete"] = True
        _save(receipt, state)
        return state
    if not partial.exists():
        if offset:
            raise ValueError("Acknowledged local evidence is missing")
        partial.touch()
    with partial.open("r+b") as stream:
        prefix = stream.read(offset)
        if len(prefix) != offset or hashlib.sha256(prefix).hexdigest() != state["prefix_sha256"]:
            raise ValueError("Acknowledged local evidence changed")
        # A crash may append bytes before their checkpoint was committed.
        stream.truncate(offset)
    digest = hashlib.sha256(prefix)
    deadline = time.monotonic() + timeout
    while offset < source["size"]:
        wanted = min(chunk_size, source["size"] - offset)
        for attempt in range(retries):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Evidence download budget exhausted; original recording retained")
            try:
                command = f"dd if={shlex.quote(remote)} bs={chunk_size} skip={offset // chunk_size} count=1 2>/dev/null"
                dev.adb(f"exec-out su -c {shlex.quote(command)} > {shlex.quote(str(chunk))}",
                        timeout=min(60, remaining), check=True)
                data = chunk.read_bytes()
                if len(data) != wanted:
                    raise ValueError("Incomplete evidence chunk")
                break
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                state["last_error"] = type(error).__name__
                _save(receipt, state)
                if attempt + 1 == retries:
                    raise
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        with partial.open("ab") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        digest.update(data)
        offset += len(data)
        state.update(bytes_complete=offset, prefix_sha256=digest.hexdigest())
        _save(receipt, state)
    if digest.hexdigest() != source["sha256"] or _source(dev, remote) != source:
        raise ValueError("Recording checksum/identity mismatch; original retained")
    partial.replace(path)
    chunk.unlink(missing_ok=True)
    state["complete"] = True
    state.pop("last_error", None)
    _save(receipt, state)
    return state
