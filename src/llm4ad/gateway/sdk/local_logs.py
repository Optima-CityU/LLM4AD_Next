"""Private, attempt-scoped execution logs independent of platform reporting."""

from __future__ import annotations

import codecs
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext, suppress
from pathlib import Path, PurePosixPath

from .storage import persist

READ_SIZE = 64 * 1024


def _regular(path: Path):
    """Inspect a regular file without accepting symlink components."""
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError("Local log path contains a symlink")
    if not path.is_file():
        raise FileNotFoundError("Local log is unavailable")
    return path.stat()


def _no_links(path: Path) -> None:
    """Reject symlinks before creating or addressing private storage."""
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError("Local log path contains a symlink")


class LocalLogStore:
    """Manage local files without registering artifacts or changing report sequences."""

    def __init__(self, journal):
        """Derive a private namespace from the SDK-owned journal."""
        self.journal = journal
        self.root = Path(journal.path).absolute().parent / "local-logs" / Path(journal.path).stem

    def directory(self, job_id: str, attempt_id: str) -> Path:
        """Address one execution without putting caller-controlled IDs into paths."""
        identity = hashlib.sha256(f"{job_id}|{attempt_id}".encode()).hexdigest()
        return self.root / identity

    def _current(self, job_id, attempt_id):
        row = self.journal.get(job_id)
        if row is None:
            raise LookupError("Task is not present locally")
        if json.loads(row["job"])["attempt_id"] != attempt_id:
            raise ValueError("Execution attempt is no longer current")
        return row

    def _retired(self, job_id, attempt_id):
        """Read cleanup-only context without making superseded logs browser-readable."""
        current = self.journal.get(job_id)
        if current and json.loads(current["job"])["attempt_id"] == attempt_id:
            raise ValueError("Execution attempt is current again")
        with self.journal.connect() as db:
            row = db.execute(
                "SELECT snapshot FROM local_log_attempts WHERE job_id=? AND attempt_id=? "
                "UNION ALL SELECT snapshot FROM retired_attempts WHERE job_id=? AND attempt_id=? LIMIT 1",
                (job_id, attempt_id, job_id, attempt_id),
            ).fetchone()
        if row is None:
            raise LookupError("Superseded execution context is unavailable")
        return json.loads(row[0])

    @contextmanager
    def _manifest(self, job_id, attempt_id, *, retired=False):
        directory = self.directory(job_id, attempt_id)
        _no_links(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        _path = directory / "manifest.json"
        _no_links(_path)
        _no_links(directory / "catalog-lock.sqlite3")
        with sqlite3.connect(directory / "catalog-lock.sqlite3", timeout=10) as lock:
            lock.execute("BEGIN IMMEDIATE")
            (self._retired if retired else self._current)(job_id, attempt_id)
            data = json.loads(_path.read_text()) if _path.exists() else {"sources": {}}
            previous = copy.deepcopy(data)
            yield data
            if data != previous or not _path.exists():
                persist(_path, data)

    @staticmethod
    def _name(name):
        if (
            not isinstance(name, str)
            or not name
            or "\\" in name
            or "\x00" in name
            or PurePosixPath(name).is_absolute()
            or any(p in {"..", "."} for p in name.split("/"))
        ):
            raise ValueError("Local log name must be a relative source name")
        return str(PurePosixPath(name))

    def create(self, job_id: str, attempt_id: str, name: str, description: str = "") -> Path:
        """Create an appendable managed log path, including initially empty files."""
        name = self._name(name)
        path = self.directory(job_id, attempt_id) / "files" / name
        with self._manifest(job_id, attempt_id) as data:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            for component in (*reversed(path.parents), path):
                if component.is_symlink():
                    raise ValueError("Local log path contains a symlink")
            fd = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
            os.close(fd)
            data["sources"][name] = {"path": str(path), "managed": True, "description": description}
            data.pop("expired_at", None)
        return path

    def register_external(self, job_id, attempt_id, name, path, description="", *, retired=False) -> None:
        """Register a backend-declared log; browser callers cannot supply paths."""
        name, path = self._name(name), Path(path).absolute()
        if path.exists():
            _regular(path)
        with self._manifest(job_id, attempt_id, retired=retired) as data:
            previous = data["sources"].get(name, {})
            if previous.get("managed") or data.get("expired_at"):
                return
            data["sources"][name] = {
                "path": str(path),
                "managed": False,
                "description": description,
            }

    def _load(self, job_id, attempt_id, *, retired=False):
        (self._retired if retired else self._current)(job_id, attempt_id)
        path = self.directory(job_id, attempt_id) / "manifest.json"
        _no_links(path)
        return json.loads(path.read_text()) if path.exists() else {"sources": {}}

    def _path(self, job_id, attempt_id, name, source):
        """Fence managed sources to their exact attempt-owned location."""
        path = Path(source["path"]).absolute()
        name = self._name(name)
        if source.get("managed") and path != self.directory(job_id, attempt_id) / "files" / name:
            raise ValueError("Local log belongs to another execution")
        _no_links(path)
        return path

    def lifecycle(self, job_id, attempt_id) -> dict:
        """Read local lifecycle observations even after a terminal report was fenced."""
        data = self._load(job_id, attempt_id)
        return {key: data[key] for key in ("lifecycle", "lifecycle_stages") if key in data}

    def has_managed(self, job_id, attempt_id) -> bool:
        """Check for a valid private copy when optional backend discovery is unavailable."""
        data = self._load(job_id, attempt_id)
        if data.get("expired_at"):
            return False
        for name, source in data["sources"].items():
            if source.get("managed"):
                try:
                    _regular(self._path(job_id, attempt_id, name, source))
                    return True
                except (OSError, ValueError):
                    continue
        return False

    def record_phase(self, job_id, attempt_id, observation: dict) -> dict:
        """Save operation observations without changing evaluation state or fitness."""
        with self._manifest(job_id, attempt_id) as data:
            stages = data.setdefault("lifecycle_stages", [])
            key = (observation["operation_id"], observation["stage"])
            stages[:] = [item for item in stages if (item["operation_id"], item["stage"]) != key]
            stages.append(observation)
            data["lifecycle"] = observation
            return {"lifecycle": observation, "lifecycle_stages": stages}

    def list(self, job_id, attempt_id) -> list[dict]:
        """Return public names and availability without paths or recovery handles."""
        data = self._load(job_id, attempt_id)
        result = []
        for name, source in sorted(data["sources"].items()):
            try:
                if data.get("expired_at"):
                    raise FileNotFoundError("Local log retention has expired")
                info = _regular(self._path(job_id, attempt_id, name, source))
                available, size, updated = True, info.st_size, info.st_mtime
            except (OSError, ValueError):
                available, size, updated = False, None, None
            result.append(
                {
                    "name": name,
                    "description": source.get("description", ""),
                    "available": available,
                    "size": size,
                    "updated_at": updated,
                    "expired": bool(data.get("expired_at")),
                }
            )
        return result

    def _source(self, job_id, attempt_id, name):
        data = self._load(job_id, attempt_id)
        source = data["sources"].get(name)
        if source is None:
            raise LookupError("Local log source is not registered")
        if data.get("expired_at"):
            raise FileNotFoundError("Local log retention has expired")
        path = self._path(job_id, attempt_id, name, source)
        _regular(path)
        return path

    def read(self, job_id, attempt_id, name, *, offset=None, limit=READ_SIZE) -> dict:
        """Read a bounded byte window; offsets never truncate persisted logs.

        Args:
            job_id: Current task ID.
            attempt_id: Current execution attempt.
            name: Registered log source name.
            offset: Byte offset, or None for the current tail.
            limit: Maximum response size, at most 64 KiB.

        Returns:
            Text, byte cursors, current size and availability. Incomplete trailing
            UTF-8 bytes are reread on the next request rather than discarded.
        """
        if type(limit) is not int or not 0 < limit <= READ_SIZE:
            raise ValueError("Invalid local log read size")
        if offset is not None and (type(offset) is not int or offset < 0):
            raise ValueError("Invalid local log offset")
        try:
            path = self._source(job_id, attempt_id, name)
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return {
                "text": "",
                "offset": 0,
                "next_offset": 0,
                "size": 0,
                "eof": True,
                "reset": False,
                "available": False,
            }
        with os.fdopen(fd, "rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            reset = offset is not None and offset > size
            start = max(0, size - limit) if offset is None or reset else offset
            stream.seek(start)
            content = stream.read(limit)
        if start:
            while content and content[0] & 0xC0 == 0x80:
                start += 1
                content = content[1:]
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        text = decoder.decode(content, final=False)
        pending, _ = decoder.getstate()
        self._current(job_id, attempt_id)
        return {
            "text": text,
            "offset": start,
            "next_offset": start + len(content) - len(pending),
            "size": size,
            "eof": start + len(content) >= size,
            "reset": reset,
            "available": True,
        }

    def snapshot(self, job_id, attempt_id, name) -> Path:
        """Export a fixed-size private download, independently of continuing writes."""
        path = self._source(job_id, attempt_id, name)
        directory = self.root / "downloads"
        _no_links(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, target = tempfile.mkstemp(prefix="runtime-", suffix=".log", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as output:
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as source:
                    remaining = os.fstat(source.fileno()).st_size
                    while remaining:
                        chunk = source.read(min(READ_SIZE, remaining))
                        if not chunk:
                            break
                        output.write(chunk)
                        remaining -= len(chunk)
            self._current(job_id, attempt_id)
            return Path(target)
        except BaseException:
            Path(target).unlink(missing_ok=True)
            raise

    def archive(self, job_id, attempt_id, sources=None, *, retired=False) -> None:
        """Preserve declared external files before SDK evidence collection removes them."""
        for name, source in (sources or {}).items():
            path = source["path"] if isinstance(source, dict) else source
            description = source.get("description", "") if isinstance(source, dict) else ""
            self.register_external(job_id, attempt_id, name, path, description, retired=retired)
        with self._manifest(job_id, attempt_id, retired=retired) as data:
            if data.get("expired_at"):
                return
            for name, source in data["sources"].items():
                name = self._name(name)
                if source.get("managed"):
                    continue
                original = self._path(job_id, attempt_id, name, source)
                try:
                    before = _regular(original)
                except FileNotFoundError:
                    continue
                target = self.directory(job_id, attempt_id) / "files" / name
                _no_links(target)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor, temporary = tempfile.mkstemp(dir=target.parent)
                try:
                    with os.fdopen(descriptor, "wb") as output, original.open("rb") as stream:
                        shutil.copyfileobj(stream, output)
                    after = _regular(original)
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError("External log changed while archiving")
                    Path(temporary).replace(target)
                    source.update(path=str(target), managed=True)
                finally:
                    Path(temporary).unlink(missing_ok=True)

    def collect(self, *, keep_seconds=604800, now=None, guard=None) -> dict:
        """Expire inactive logs under an optional backend restoration guard.

        Args:
            keep_seconds: Independent retention after task completion or last write.
            now: Optional epoch for deterministic collection.
            guard: Optional context factory taking a current journal row. It holds
                backend ownership checks while deletion proceeds and may yield
                False to defer cleanup.

        Returns:
            Counts of expired attempts, removed bytes and deferred errors.
        """
        from .lifecycle import worker_active

        now = time.time() if now is None else now
        result = {"cleaned": 0, "errors": 0, "removed_bytes": 0}
        rows = [(row, False) for row in self.journal.rows()]
        with self.journal.connect() as db:
            rows += [
                (json.loads(row[0]), True)
                for row in db.execute("SELECT snapshot FROM local_log_attempts")
            ]
        for row, retired in rows:
            job = json.loads(row["job"])
            if not retired and row["state"] not in {"completed", "failed", "cancelled"}:
                continue
            if (
                retired
                and guard is None
                and row["state"] not in {"completed", "failed", "cancelled"}
                and json.loads(row["saved"])
            ):
                continue
            try:
                data = self._load(row["id"], job["attempt_id"], retired=retired)
                if data.get("expired_at"):
                    if retired:
                        self._forget(row["id"], job["attempt_id"])
                    continue
                if not data["sources"] and not retired:
                    continue
                if worker_active(self.journal, row["id"], job["attempt_id"]):
                    continue
                paths = [
                    self._path(row["id"], job["attempt_id"], name, item)
                    for name, item in data["sources"].items()
                    if item.get("managed")
                ]
                times = [_regular(path).st_mtime for path in paths if path.is_file()]
                anchor = max([row.get("finished_at") or 0, row.get("superseded_at") or 0, *times])
                if not anchor or now - anchor < keep_seconds:
                    continue
                with guard(row) if guard else nullcontext(True) as permitted:
                    if not permitted:
                        continue
                    with self._manifest(row["id"], job["attempt_id"], retired=retired) as current:
                        if not retired and self._current(row["id"], job["attempt_id"])[
                            "state"
                        ] not in {
                            "completed",
                            "failed",
                            "cancelled",
                        }:
                            continue
                        for name, source in current["sources"].items():
                            if source.get("managed"):
                                path = self._path(row["id"], job["attempt_id"], name, source)
                                if path.is_file():
                                    result["removed_bytes"] += _regular(path).st_size
                                    path.unlink()
                        current["expired_at"] = now
                        result["cleaned"] += 1
                if retired:
                    self._forget(row["id"], job["attempt_id"])
            except (OSError, ValueError, LookupError, sqlite3.Error):
                result["errors"] += 1
        return result

    def _forget(self, job_id, attempt_id):
        """Discard private restoration context after its archived logs have expired."""
        with self.journal.connect() as db:
            db.execute(
                "DELETE FROM local_log_attempts WHERE job_id=? AND attempt_id=?",
                (job_id, attempt_id),
            )


def _windows_stdio() -> None:
    """Expose redirected CRT descriptors to child process standard handles."""
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        setter = ctypes.windll.kernel32.SetStdHandle
        setter.argtypes = [wintypes.DWORD, wintypes.HANDLE]
        setter.restype = wintypes.BOOL
        setter(-11, msvcrt.get_osfhandle(1))
        setter(-12, msvcrt.get_osfhandle(2))


@contextmanager
def capture_worker_logs(journal, payload):
    """Capture worker and inherited child output without changing evaluator results."""
    saved, handles, stream_settings = {}, [], []
    try:
        store = LocalLogStore(journal)
        paths = [
            store.create(payload["id"], payload["attempt_id"], f"worker.{name}")
            for name in ("stdout", "stderr")
        ]
        for stream in (sys.stdout, sys.stderr):
            with suppress(OSError, ValueError):
                stream.flush()
        for descriptor, path in zip((1, 2), paths, strict=True):
            saved[descriptor] = os.dup(descriptor)
            handles.append(os.open(path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)))
        for descriptor, handle in zip((1, 2), handles, strict=True):
            os.dup2(handle, descriptor, inheritable=True)
        _windows_stdio()
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream_settings.append((stream, stream.line_buffering, stream.write_through))
                stream.reconfigure(line_buffering=True, write_through=True)
    except (OSError, ValueError, TypeError, LookupError, sqlite3.Error):
        for descriptor, original in saved.items():
            os.dup2(original, descriptor)
        _windows_stdio()
    try:
        yield
    finally:
        for stream in (sys.stdout, sys.stderr):
            with suppress(OSError, ValueError):
                stream.flush()
        for descriptor, original in saved.items():
            os.dup2(original, descriptor)
            os.close(original)
        _windows_stdio()
        for handle in handles:
            os.close(handle)
        for stream, line_buffering, write_through in stream_settings:
            with suppress(OSError, ValueError, TypeError):
                stream.reconfigure(line_buffering=line_buffering, write_through=write_through)
