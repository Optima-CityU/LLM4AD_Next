"""Persistent process ownership for loky slots and ordinary command invocations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import psutil

OWNER_ENV = "LLM4AD_PROCESS_OWNER"
DIRECTORY_ENV = "LLM4AD_PROCESS_DIRECTORY"
COMMAND_ENV = "LLM4AD_PROCESS_COMMAND"


class ProcessOwnershipError(RuntimeError):
    """Process absence or cleanup could not be established for an owned slot."""


def persist_record(path: Path, value: dict) -> None:
    """Atomically sync an ownership record before acknowledging an operation.

    Args:
        path: Owned destination file.
        value: JSON-compatible ownership receipt.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        if sys.platform != "win32":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def process_identity(process: psutil.Process | None = None) -> dict:
    """Identify a process without trusting a reusable PID alone.

    Args:
        process: Process to identify, defaulting to this process.

    Returns:
        Its PID and creation time.
    """
    process = process or psutil.Process()
    return {"pid": process.pid, "create_time": process.create_time()}


def create_owner(directory: Path, *, key: str = "", slot: int = 0) -> dict:
    """Persist a fresh random slot generation before any worker is spawned.

    Args:
        directory: New generation's ownership directory.
        key: Durable delivery key assigned to the generation.
        slot: Controller-local slot index.

    Returns:
        The persisted ownership record.
    """
    directory = Path(directory).absolute()
    directory.mkdir(parents=True, exist_ok=False)
    record = {
        "schema": "llm4ad/process-owner@1",
        "nonce": uuid.uuid4().hex,
        "created_at": time.time(),
        "controller": process_identity(),
        "username": psutil.Process().username(),
        "key": key,
        "slot": slot,
        "directory": str(directory),
        "clean": False,
    }
    persist_record(directory / "owner.json", record)
    return record


def _owner(directory: Path) -> dict:
    record: dict = json.loads((directory / "owner.json").read_text(encoding="utf-8"))
    if (
        record.get("schema") != "llm4ad/process-owner@1"
        or not isinstance(record.get("nonce"), str)
        or len(record["nonce"]) != 32
        or record.get("directory") != str(directory.absolute())
    ):
        raise ProcessOwnershipError(f"Invalid process ownership receipt: {directory}")
    return record


def owner_environment(directory: Path) -> dict[str, str]:
    """Build environment markers inherited by a slot and all its descendants.

    Args:
        directory: Persisted slot generation directory.

    Returns:
        Environment additions for loky's public executor API.
    """
    directory = Path(directory).absolute()
    return {OWNER_ENV: _owner(directory)["nonce"], DIRECTORY_ENV: str(directory)}


def _record_process(directory: Path, process: psutil.Process, *, role: str = "descendant") -> dict:
    record = {**process_identity(process), "nonce": _owner(directory)["nonce"], "role": role}
    filename = f"{record['pid']}-{record['create_time']:.9f}.json"
    path = directory / "processes" / filename
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        if previous.get("role") == "worker":
            record["role"] = "worker"
    persist_record(path, record)
    return record


def initialize_worker(directory: str) -> None:
    """Record a loky worker before executing a submitted callable.

    Args:
        directory: Slot generation persisted before pool creation.
    """
    path = Path(directory).absolute()
    os.environ.update(owner_environment(path))
    _record_process(path, psutil.Process(), role="worker")


def worker_identity(directory: Path) -> dict | None:
    """Read worker identity without accessing private executor internals.

    Args:
        directory: Persisted slot generation directory.

    Returns:
        Worker identity, or None before its initializer finishes.
    """
    for path in sorted((Path(directory) / "processes").glob("*.json")):
        record: dict = json.loads(path.read_text(encoding="utf-8"))
        if record.get("role") == "worker":
            return record
    return None


def _matches_identity(process: psutil.Process, identity: dict) -> bool:
    return bool(process.is_running() and process.create_time() == identity.get("create_time"))


def _scan_owner(directory: Path, *, command: str | None = None) -> tuple[dict[int, psutil.Process], list[str]]:
    owner = _owner(directory)
    known = {}
    if command is None:
        for path in (directory / "processes").glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("nonce") != owner["nonce"]:
                raise ProcessOwnershipError(f"Foreign process identity: {path}")
            known[record["pid"]] = record
    found, uncertain = {}, []
    for process in psutil.process_iter():
        try:
            if process.pid == os.getpid():
                continue
            created_at = process.create_time()
            recorded = known.get(process.pid)
            if recorded and _matches_identity(process, recorded):
                if process.status() != psutil.STATUS_ZOMBIE:
                    found[process.pid] = process
                continue
            # Process timestamps can be rounded down to an OS clock tick.
            # Only exclude processes that certainly predate this generation.
            if created_at < owner.get("created_at", 0) - 1.0:
                continue
            if process.status() == psutil.STATUS_ZOMBIE:
                continue
            if process.username() != owner["username"]:
                continue
            environment = process.environ()
            if environment.get(OWNER_ENV) == owner["nonce"]:
                if environment.get(DIRECTORY_ENV) != str(directory):
                    uncertain.append(f"PID {process.pid}: ownership directory mismatch")
                elif command is None or environment.get(COMMAND_ENV) == command:
                    found[process.pid] = process
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except (psutil.AccessDenied, OSError) as error:
            try:
                if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                    continue
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (psutil.AccessDenied, OSError):
                pass
            uncertain.append(f"PID {process.pid}: {type(error).__name__}")
    return found, uncertain


def owner_active(directory: Path) -> bool:
    """Check ownership without treating unreadable processes as absent.

    Args:
        directory: Persisted slot generation directory.

    Returns:
        Whether a matching live process remains.

    Raises:
        ProcessOwnershipError: A potentially owned process could not be inspected.
    """
    found, uncertain = _scan_owner(Path(directory).absolute())
    if uncertain:
        raise ProcessOwnershipError("Cannot establish process absence: " + "; ".join(uncertain))
    return bool(found)


def cleanup_owner(directory: Path, *, timeout: float = 5.0) -> None:
    """Kill only processes matching a nonce or exact PID/create-time receipt.

    Args:
        directory: Slot generation whose pool has already been shut down.
        timeout: Bounded time to reconcile detached descendants.

    Raises:
        ProcessOwnershipError: Cleanup or process absence could not be confirmed.
    """
    directory = Path(directory).absolute()
    deadline = time.monotonic() + timeout
    while True:
        found, uncertain = _scan_owner(directory)
        identities = []
        for process in found.values():
            try:
                identities.append((process, _record_process(directory, process)))
                for child in process.children(recursive=True):
                    if child.status() != psutil.STATUS_ZOMBIE:
                        identities.append((child, _record_process(directory, child)))
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (psutil.AccessDenied, OSError) as error:
                uncertain.append(f"PID {process.pid}: {type(error).__name__}")
        for process, identity in identities:
            try:
                if _matches_identity(process, identity):
                    process.kill()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (psutil.AccessDenied, OSError) as error:
                uncertain.append(f"PID {process.pid}: {type(error).__name__}")
        if not found and not uncertain:
            persist_record(directory / "owner.json", {**_owner(directory), "clean": True})
            return
        if time.monotonic() >= deadline:
            if uncertain:
                raise ProcessOwnershipError("Process cleanup remains uncertain: " + "; ".join(uncertain))
            raise ProcessOwnershipError(f"Owned processes remain after cleanup: {directory}")
        remaining = max(0.0, deadline - time.monotonic())
        if found:
            psutil.wait_procs(list(found.values()), timeout=min(0.05, remaining))
        else:
            time.sleep(min(0.02, remaining))


def owned_command(command, *, owner_directory: str | Path | None = None, **kwargs) -> subprocess.CompletedProcess:
    """Run an ordinary command with durable descendant markers and PID receipts.

    Args:
        command: Command accepted by subprocess.run.
        owner_directory: Existing slot generation, defaulting to the inherited
            worker directory. Outside a worker, an explicit new directory is required.
        **kwargs: Arguments forwarded to subprocess.run, including timeout and check.

    Returns:
        The subprocess.run result.

    Raises:
        ProcessOwnershipError: No durable ownership context was supplied.
        subprocess.SubprocessError: Failure under subprocess.run semantics.
    """
    selected = owner_directory or os.environ.get(DIRECTORY_ENV)
    if selected is None:
        raise ProcessOwnershipError("owned_command requires a slot or explicit owner_directory")
    directory = Path(selected).absolute()
    if not (directory / "owner.json").exists():
        create_owner(directory)
    owner = _owner(directory)
    token = uuid.uuid4().hex
    receipt = directory / "commands" / f"{token}.json"
    record = {"nonce": owner["nonce"], "command": token, "caller": process_identity(), "processes": [], "finished": False}
    persist_record(receipt, record)
    supplied = kwargs.pop("env", None)
    environment = dict(os.environ if supplied is None else supplied)
    environment.update(owner_environment(directory))
    environment[COMMAND_ENV] = token
    stopped = threading.Event()

    def observe():
        seen = set()
        try:
            while True:
                found, uncertain = _scan_owner(directory, command=token)
                changed = False
                for process in found.values():
                    try:
                        identity = process_identity(process)
                        key = (identity["pid"], identity["create_time"])
                        if key not in seen:
                            _record_process(directory, process, role="command")
                            record["processes"].append(identity)
                            seen.add(key)
                            changed = True
                    except (psutil.NoSuchProcess, psutil.ZombieProcess):
                        continue
                if uncertain:
                    record["inspection_errors"] = uncertain
                    changed = True
                if changed:
                    persist_record(receipt, record)
                if stopped.wait(0.02):
                    return
        except Exception as error:
            record["inspection_errors"] = [f"{type(error).__name__}: {error}"]

    observer = threading.Thread(target=observe, name="llm4ad-command-receipts", daemon=True)
    observer.start()
    try:
        return subprocess.run(command, env=environment, **kwargs)
    finally:
        stopped.set()
        observer.join()
        persist_record(receipt, {**record, "finished": True})


def tree_active(journal, job_id: str, attempt_id: str) -> bool:
    """Locate durable owner receipts for legacy lifecycle callers.

    Args:
        journal: Journal locating its local state directory.
        job_id: Accepted job identity.
        attempt_id: Execution attempt identity.

    Returns:
        Whether a matching owner has a live or uninspectable process.
    """
    keys = {job_id, f"{job_id}:{attempt_id}", f"{job_id}|{attempt_id}"}
    for path in Path(journal.path).parent.rglob("owner.json"):
        try:
            record = _owner(path.parent)
            key = record.get("key", "")
            matches = key in keys or key.startswith(f"{job_id}:{attempt_id}:")
            if matches and owner_active(path.parent):
                return True
        except (ProcessOwnershipError, OSError, ValueError):
            return True
    return False
