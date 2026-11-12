"""Process-tree guards exercised in sessions isolated from the test runner."""

import json
import multiprocessing
import os
import signal
import sqlite3
import time
import uuid
from contextlib import contextmanager, suppress
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from llm4ad.gateway.sdk import processes
from llm4ad.gateway.sdk.journal import Journal, gateway_lease
from llm4ad.gateway.sdk.lifecycle import execution_control, request_stop, worker_directory
from llm4ad.gateway.sdk.processes import guard_worker, tree_active


def _stubborn_child(connection):
    """Ignore polite termination and keep a pipe open until actually killed."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    connection.send("child-ready")
    while True:
        time.sleep(0.1)


def _isolated_worker(path, job, connection, child_connection, stubborn):
    """Enter a dedicated session before starting any process that may be killed."""
    os.setsid()
    journal = Journal(path)
    with gateway_lease(worker_directory(journal, job["id"], job["attempt_id"])):
        with guard_worker(journal, job["id"], job["attempt_id"]) as value:
            assert value is None
            if stubborn:
                child = multiprocessing.get_context("spawn").Process(target=_stubborn_child, args=(child_connection,))
                child.start()
            child_connection.close()
            connection.send({"group": os.getpgrp(), "leased": tree_active(journal, job["id"], job["attempt_id"])})
            connection.recv()
        connection.send("guard-closed")
        connection.recv()


def _wait_until(predicate, timeout=10):
    """Bound waits by observed process or lease state."""
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Process guard transition timed out"
        time.sleep(0.02)


@pytest.fixture
def attempt(tmp_path):
    """Create only a synthetic journal and an accepted local job."""
    journal = Journal(tmp_path / "jobs.sqlite3")
    job = {"id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4())}
    journal.accept(job)
    return journal, job


@contextmanager
def _worker(attempt, *, stubborn=False):
    """Clean up an isolated group while its live leader still pins its identity."""
    journal, job = attempt
    context = multiprocessing.get_context("spawn")
    connection, worker_connection = context.Pipe()
    child_reader, child_writer = context.Pipe(duplex=False)
    worker = context.Process(
        target=_isolated_worker,
        args=(journal.path, job, worker_connection, child_writer, stubborn),
    )
    worker.start()
    worker_connection.close()
    child_writer.close()
    isolated = False
    try:
        assert connection.poll(15), "Isolated worker did not establish its guardian"
        ready = connection.recv()
        assert ready["group"] == worker.pid
        assert ready["group"] != os.getpgrp()
        isolated = True
        assert ready["leased"] is True
        if stubborn:
            assert child_reader.poll(10)
            assert child_reader.recv() == "child-ready"
        yield worker, connection, child_reader
    finally:
        if worker.is_alive():
            if isolated:
                # Signal only a still-live, verified session leader's group.
                with suppress(ProcessLookupError):
                    os.killpg(worker.pid, signal.SIGKILL)
            else:
                worker.kill()
        worker.join(timeout=10)
        connection.close()
        child_reader.close()
        if not worker.is_alive():
            worker.close()


@pytest.mark.skipif(os.name == "nt", reason="Requires a POSIX session and process-group signals")
def test_guard_readiness_and_normal_release(attempt):
    """Ready means tree ownership; normal closure finishes before the primary lease."""
    journal, job = attempt
    with _worker(attempt) as (worker, connection, _):
        assert tree_active(journal, job["id"], job["attempt_id"])
        connection.send("done")
        assert connection.poll(10)
        assert connection.recv() == "guard-closed"
        assert not tree_active(journal, job["id"], job["attempt_id"])
        with pytest.raises(sqlite3.OperationalError), gateway_lease(worker_directory(journal, job["id"], job["attempt_id"])):
            pass
        connection.send("release-primary")
        worker.join(timeout=10)
        assert worker.exitcode == 0


@pytest.mark.skipif(os.name == "nt", reason="Requires a POSIX session and process-group signals")
@pytest.mark.parametrize("stop", ["sigterm", "sigkill", "cancel", "timeout"])
def test_guard_kills_stubborn_child_after_root_death_or_stop(attempt, stop):
    """A child ignoring SIGTERM cannot outlive guarded cancellation or root death."""
    journal, job = attempt
    with _worker(attempt, stubborn=True) as (worker, _, child_reader):
        if stop == "sigterm":
            worker.terminate()
        elif stop == "sigkill":
            worker.kill()
        else:
            request_stop(journal, job["id"], job["attempt_id"], stop)
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert child_reader.poll(10), "Stubborn child retained its live pipe"
        with pytest.raises(EOFError):
            child_reader.recv()
        _wait_until(lambda: not tree_active(journal, job["id"], job["attempt_id"]))


@pytest.mark.skipif(os.name == "nt", reason="POSIX fixtures without their own group must never spawn a guardian")
def test_in_process_fixture_does_not_spawn(attempt, monkeypatch):
    """An ordinary test process cannot accidentally signal its caller's group."""
    journal, job = attempt
    monkeypatch.setattr(processes.os, "getpgrp", lambda: -1)
    spawn = Mock(side_effect=AssertionError("In-process fixture must not spawn"))
    monkeypatch.setattr(processes.multiprocessing, "get_context", spawn)
    with guard_worker(journal, job["id"], job["attempt_id"]) as value:
        assert value is None
    spawn.assert_not_called()


def test_unknown_tree_is_attempt_fenced_and_preserves_terminal_result(attempt):
    """Windows uncertainty cannot overwrite a replacement attempt or terminal result."""
    journal, job = attempt
    processes._record_unknown(journal, job["id"], job["attempt_id"])
    assert journal.get(job["id"])["state"] == "recovery_required"
    replacement = {**job, "attempt_id": str(uuid.uuid4())}
    journal.accept(replacement)
    processes._record_unknown(journal, job["id"], job["attempt_id"])
    assert journal.get(job["id"])["state"] == "accepted"
    journal.update(job["id"], "completed", {"score": 1}, replacement["attempt_id"])
    processes._record_unknown(journal, job["id"], replacement["attempt_id"])
    assert json.loads(journal.get(job["id"])["report"]) == {"score": 1}


def test_windows_parent_death_preserves_uncertainty(attempt, monkeypatch):
    """An unexpectedly dead Windows root is never assumed to have no descendants."""
    journal, job = attempt
    with journal.connect() as db:
        db.execute(
            "INSERT INTO execution_controls VALUES(?,?,?,?)", (job["id"], job["attempt_id"], "running", "")
        )
    parent = SimpleNamespace(is_alive=lambda: False)
    monkeypatch.setattr(processes, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(processes.multiprocessing, "parent_process", lambda: parent)
    connection = SimpleNamespace(poll=lambda _: False)
    kill = Mock(side_effect=AssertionError("A dead root PID must not be used for taskkill"))
    monkeypatch.setattr(processes.subprocess, "run", kill)
    processes._watch_tree(connection, journal, job["id"], job["attempt_id"], 123)
    assert journal.get(job["id"])["state"] == "recovery_required"
    assert execution_control(journal, job["id"], job["attempt_id"])["status"] == "unknown"
    kill.assert_not_called()
