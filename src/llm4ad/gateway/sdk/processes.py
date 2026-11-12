"""Keep process-tree ownership until forced termination has been signalled."""

from __future__ import annotations

import multiprocessing
import os
import signal
import sqlite3
import subprocess
from contextlib import closing, contextmanager, suppress

__all__ = ["guard_worker", "tree_active"]


def tree_active(journal, job_id: str, attempt_id: str) -> bool:
    """Check the independent tree lease without waiting for its owner.

    Args:
        journal: Journal containing the accepted attempt.
        job_id: Accepted job identity.
        attempt_id: Fenced execution attempt identity.

    Returns:
        Whether the guardian still owns the attempt's process tree.
    """
    from .journal import gateway_lease
    from .lifecycle import worker_directory

    try:
        with gateway_lease(worker_directory(journal, job_id, attempt_id) / "tree"):
            return False
    except sqlite3.OperationalError as error:
        if error.sqlite_errorcode in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return True
        raise


def _stop_requested(path: str, job_id: str, attempt_id: str) -> bool | None:
    """Read stop controls without blocking parent-death detection on a writer."""
    try:
        with closing(sqlite3.connect(path, timeout=0)) as db:
            row = db.execute(
                "SELECT json_extract(j.job,'$.attempt_id'),c.stop_requested FROM jobs j "
                "LEFT JOIN execution_controls c ON c.job_id=j.id AND c.attempt_id=? WHERE j.id=?",
                (attempt_id, job_id),
            ).fetchone()
            return row is None or row[0] != attempt_id or bool(row[1])
    except sqlite3.OperationalError as error:
        if error.sqlite_errorcode in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return None
        raise


def _record_unknown(journal, job_id: str, attempt_id: str) -> None:
    """Fence an unproven Windows tree so it cannot become automatic replay."""
    journal.update(
        job_id,
        "recovery_required",
        {"message": "Worker exited without confirmed process-tree termination"},
        attempt_id,
        execution_status="unknown",
    )


def _watch_tree(connection, journal, job_id: str, attempt_id: str, parent_id: int) -> None:
    """Watch the root while the caller continuously holds the independent lease."""
    parent = multiprocessing.parent_process()
    done = False
    while True:
        stopped = not parent.is_alive()
        if connection.poll(0.05):
            try:
                done = connection.recv() == "done"
            except EOFError:
                stopped = True
        requested = _stop_requested(journal.path, job_id, attempt_id)
        if stopped or requested:
            if os.name != "nt":
                # Membership pins this group identity even after the root dies.
                os.killpg(os.getpgrp(), signal.SIGKILL)
            else:
                if parent.is_alive():
                    with suppress(OSError, subprocess.TimeoutExpired):
                        subprocess.run(
                            ["taskkill", "/PID", str(parent_id), "/T", "/F"],
                            capture_output=True, check=False, timeout=5,
                        )
                _record_unknown(journal, job_id, attempt_id)
                return
        if done and requested is False:
            return


def _guardian(connection, path: str, job_id: str, attempt_id: str, parent_id: int) -> None:
    """Hold the tree lease independently and stop descendants before releasing it."""
    from .journal import Journal, gateway_lease
    from .lifecycle import worker_directory

    try:
        if os.name != "nt":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            if os.getpgrp() != parent_id:
                raise RuntimeError("Process guardian must share its isolated worker's process group")
        journal = Journal(path)
        with gateway_lease(worker_directory(journal, job_id, attempt_id) / "tree"):
            try:
                connection.send("ready")
                _watch_tree(connection, journal, job_id, attempt_id, parent_id)
            except BaseException:
                if os.name != "nt":
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                _record_unknown(journal, job_id, attempt_id)
                raise
    except BaseException:
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send("failed")
        raise
    finally:
        connection.close()


@contextmanager
def guard_worker(journal, job_id: str, attempt_id: str):
    """Guard an isolated worker while its caller holds the primary worker lease.

    Args:
        journal: Journal containing the accepted attempt.
        job_id: Accepted job identity.
        attempt_id: Fenced execution attempt identity.

    Yields:
        None, after the spawned guardian has acquired its independent tree lease.

    Raises:
        RuntimeError: The guardian could not establish process-tree ownership.
    """
    if os.name != "nt" and os.getpgrp() != os.getpid():
        yield None
        return

    context = multiprocessing.get_context("spawn")
    connection, child_connection = context.Pipe()
    guardian = context.Process(
        target=_guardian,
        args=(child_connection, journal.path, job_id, attempt_id, os.getpid()),
        name="llm4ad-process-guardian",
    )
    started = False
    try:
        guardian.start()
        started = True
        child_connection.close()
        if not connection.poll(15) or connection.recv() != "ready":
            raise RuntimeError("Process guardian did not acquire its tree lease")
        yield None
    finally:
        child_connection.close()
        if started:
            with suppress(BrokenPipeError, EOFError, OSError):
                connection.send("done")
            guardian.join(timeout=10)
            if guardian.is_alive():
                if os.name != "nt":
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                _record_unknown(journal, job_id, attempt_id)
                guardian.kill()
                guardian.join(timeout=5)
            guardian.close()
        connection.close()
