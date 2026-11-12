"""SDK-owned worker leases, interruption controls and optional operation hooks."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path

from .journal import Reporter, gateway_lease


def worker_directory(journal, job_id: str, attempt_id: str) -> Path:
    """Locate the SDK-owned lease without trusting workspace path strings."""
    identity = hashlib.sha256(f"{job_id}|{attempt_id}".encode()).hexdigest()
    return Path(journal.path).parent / "workers" / identity


def execution_control(journal, job_id: str, attempt_id: str) -> dict:
    """Read execution ownership independently of external recovery handles."""
    with journal.connect() as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT status,stop_requested FROM execution_controls WHERE job_id=? AND attempt_id=?",
            (job_id, attempt_id),
        ).fetchone()
        return dict(row) if row else {}


def worker_active(journal, job_id: str, attempt_id: str) -> bool:
    """Detect live local execution even when its original gateway process exited."""
    from .processes import tree_active

    if tree_active(journal, job_id, attempt_id):
        return True
    try:
        with gateway_lease(worker_directory(journal, job_id, attempt_id)):
            return False
    except sqlite3.OperationalError as error:
        if error.sqlite_errorcode in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return True
        raise


def request_stop(journal, job_id: str, attempt_id: str, reason: str) -> None:
    """Persist a stop request consumed by an owned worker after gateway restarts."""
    with journal.connect() as db:
        db.execute(
            "INSERT INTO execution_controls(job_id,attempt_id,status,stop_requested) VALUES(?,?,'idle',?) "
            "ON CONFLICT(job_id,attempt_id) DO UPDATE SET stop_requested=excluded.stop_requested",
            (job_id, attempt_id, reason),
        )


def finish_execution(journal, job_id: str, attempt_id: str, status: str) -> None:
    """Keep intentional suspension distinct from a worker interrupted mid-function."""
    with journal.connect() as db:
        db.execute(
            "INSERT INTO execution_controls(job_id,attempt_id,status,stop_requested) VALUES(?,?,?,'') "
            "ON CONFLICT(job_id,attempt_id) DO UPDATE SET status=excluded.status",
            (job_id, attempt_id, status),
        )


def prepare_execution(journal, job_id: str, attempt_id: str) -> bool:
    """Record a pending spawn before running code, so a startup crash is recoverable."""
    with journal.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT job,state,report FROM jobs WHERE id=?", (job_id,)).fetchone()
        control = execution_control(journal, job_id, attempt_id)
        if (
            row is None
            or json.loads(row[0])["attempt_id"] != attempt_id
            or row[1] in {"completed", "failed", "cancelled"}
            or json.loads(row[2]).get("retry_confirmed") is True
            or control.get("stop_requested") in {"cancel", "timeout", "superseded"}
        ):
            return False
        db.execute(
            "INSERT INTO execution_controls(job_id,attempt_id,status,stop_requested) VALUES(?,?,'pending','') "
            "ON CONFLICT(job_id,attempt_id) DO UPDATE SET status='pending',stop_requested=''",
            (job_id, attempt_id),
        )
        return True


def resolve_execution(journal, job_id: str, action: str) -> None:
    """Resolve uncertain work under its execution lease and an atomic state check."""
    from .processes import tree_active

    row = journal.get(job_id)
    if row is None:
        raise ValueError("Only uncertain jobs can be explicitly resolved")
    attempt_id = json.loads(row["job"])["attempt_id"]
    try:
        with gateway_lease(worker_directory(journal, job_id, attempt_id)):
            if tree_active(journal, job_id, attempt_id):
                raise ValueError(
                    "The local worker is still active; stop it before confirming a retry or cancellation"
                )
            with journal.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT job,state FROM jobs WHERE id=?", (job_id,)).fetchone()
                if (
                    current is None
                    or json.loads(current[0])["attempt_id"] != attempt_id
                    or current[1] != "recovery_required"
                ):
                    raise ValueError(
                        "Only the current uncertain execution can be explicitly resolved"
                    )
                report = (
                    {"message": "Operator confirmed safe retry", "retry_confirmed": True}
                    if action == "retry"
                    else {"message": "Operator confirmed cancellation"}
                )
                db.execute(
                    "UPDATE jobs SET state=?,report=?,seq=seq+1 WHERE id=?",
                    (
                        "recovery_required" if action == "retry" else "cancelled",
                        json.dumps(report),
                        job_id,
                    ),
                )
                db.execute(
                    "INSERT INTO execution_controls(job_id,attempt_id,status,stop_requested) VALUES(?,?,?,?) "
                    "ON CONFLICT(job_id,attempt_id) DO UPDATE SET status=excluded.status,stop_requested=excluded.stop_requested",
                    (
                        job_id,
                        attempt_id,
                        "suspended" if action == "retry" else "finished",
                        "superseded" if action == "retry" else "cancel",
                    ),
                )
    except sqlite3.OperationalError as error:
        if error.sqlite_errorcode not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise
        raise ValueError(
            "The local worker is still active; stop it before confirming a retry or cancellation"
        ) from error


def terminate_current_worker() -> None:
    """Stop an orphaned evaluator and its local child processes without PID reuse."""
    if os.name == "nt":
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                ["taskkill", "/PID", str(os.getpid()), "/T", "/F"],
                capture_output=True,
                timeout=5,
                check=False,
            )
    elif os.getpgrp() == os.getpid():
        os.killpg(os.getpid(), signal.SIGKILL)
    os._exit(1)


@contextmanager
def owned_worker(journal, job_id: str, attempt_id: str, *, recovery: bool):
    """Fence duplicate workers and monitor durable stop requests for their lifetime."""
    from .processes import guard_worker, tree_active

    with gateway_lease(worker_directory(journal, job_id, attempt_id)):
        if tree_active(journal, job_id, attempt_id):
            yield False
            return
        with journal.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT job,state FROM jobs WHERE id=?", (job_id,)).fetchone()
            control = execution_control(journal, job_id, attempt_id)
            if (
                row is None
                or json.loads(row[0])["attempt_id"] != attempt_id
                or row[1] in {"completed", "failed", "cancelled"}
                or control.get("stop_requested") in {"cancel", "timeout", "superseded"}
            ):
                accepted = False
            else:
                accepted = True
                db.execute(
                    "INSERT INTO execution_controls(job_id,attempt_id,status,stop_requested) VALUES(?,?,'running','') "
                    "ON CONFLICT(job_id,attempt_id) DO UPDATE SET status='running',stop_requested=''",
                    (job_id, attempt_id),
                )
                if recovery and not Reporter(journal, job_id, attempt_id).saved_state:
                    db.execute("UPDATE jobs SET started=? WHERE id=?", (time.time(), job_id))
        if not accepted:
            yield False
            return
        stop = threading.Event()

        def monitor():
            while not stop.wait(0.2):
                try:
                    row = journal.get(job_id)
                    control = execution_control(journal, job_id, attempt_id)
                    if (
                        row is None
                        or json.loads(row["job"])["attempt_id"] != attempt_id
                        or control.get("stop_requested")
                    ):
                        terminate_current_worker()
                except (OSError, sqlite3.Error):
                    continue

        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            with guard_worker(journal, job_id, attempt_id):
                yield True
        finally:
            stop.set()
            watcher.join()


def reset_local_outputs(job, reporter: Reporter) -> None:
    """Clear unfinished SDK outputs before replaying a stopped local computation."""
    with reporter.journal.connect() as db:
        rows = db.execute(
            "SELECT object_key FROM artifacts WHERE job_id=? AND attempt_id=?",
            (job.id, job.attempt_id),
        ).fetchall()
        if any(row[0] for row in rows):
            raise ValueError("Uploaded outputs cannot be replaced by local recovery")
        db.execute(
            "DELETE FROM artifacts WHERE job_id=? AND attempt_id=?", (job.id, job.attempt_id)
        )
    identity = hashlib.sha256(f"{job.id}|{job.attempt_id}".encode()).hexdigest()
    paths = [
        Path(reporter.journal.path).parent / "artifacts" / identity,
        Path(job.artifact_dir),
        Path(job.project_root) / ".behavior-artifacts",
    ]
    for path in paths:
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    Path(job.artifact_dir).mkdir(exist_ok=True)


async def invoke(operation):
    """Accept existing synchronous adapters as well as asynchronous implementations."""
    return await operation if inspect.isawaitable(operation) else operation


async def run_operation(adapter, operation: str, job, reporter: Reporter, action):
    """Run optional pre/post hooks while preserving the original operation error.

    Args:
        adapter: Evaluator implementing only its problem-specific work.
        operation: SDK-selected evaluate, recover or cancel operation.
        job: Authoritative accepted job.
        reporter: Attempt-scoped observations and external execution state.
        action: SDK-selected operation callable.

    Returns:
        The action's unchanged result; hooks do not replace SDK state transitions.
    """
    result, error = None, None
    operation_id = str(uuid.uuid4())
    operation_started = time.monotonic()
    stage_started, failure_stage = {}, None

    def observe(stage, status, caught=None):
        nonlocal failure_stage
        if status == "running":
            stage_started[stage] = (time.time(), time.monotonic())
        if status == "failed" and failure_stage is None:
            failure_stage = stage
        epoch, monotonic = stage_started.get(stage, (time.time(), time.monotonic()))
        observation = {
            "operation_id": operation_id,
            "operation": operation,
            "stage": stage,
            "status": status,
            "started_at": epoch,
            "finished_at": None if status == "running" else time.time(),
            "duration_ms": (time.monotonic() - monotonic) * 1000,
            "elapsed_ms": (time.monotonic() - operation_started) * 1000,
            "failed_stage": failure_stage,
        }
        if caught is not None:
            observation["error_type"] = type(caught).__name__
        if callback := getattr(reporter, "record_phase", None):
            with suppress(Exception):
                callback(observation)

    stage = "preparing"
    observe(stage, "running")
    try:
        if hook := getattr(adapter, "before_task", None):
            await invoke(hook(job, reporter, operation))
        observe(stage, "completed")
        stage = "evaluating"
        observe(stage, "running")
        result = await invoke(action())
        observe(stage, "completed")
        return result
    except BaseException as caught:
        error = caught
        observe(stage, "failed", caught)
        raise
    finally:
        observe("finalizing", "running")
        try:
            if hook := getattr(adapter, "after_task", None):
                await invoke(hook(job, reporter, operation, result=result, error=error))
            observe("finalizing", "completed")
        except BaseException as hook_error:
            observe("finalizing", "failed", hook_error)
            if error is None:
                raise
            with suppress(Exception):
                reporter.message(
                    f"Post-operation hook failed: {hook_error}",
                    level="error",
                    data={"operation": operation, "error_type": type(hook_error).__name__},
                )
