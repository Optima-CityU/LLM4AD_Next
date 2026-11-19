"""Keep execution budgets and historical start times across graceful gateway outages."""

import sqlite3
import uuid

import pytest

from llm4ad.gateway.sdk import gateway, lifecycle
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.lifecycle import execution_control, owned_worker, prepare_execution, request_stop

pytestmark = pytest.mark.unit


@pytest.fixture
def running(tmp_path, monkeypatch):
    """Provide a real isolated journal and a controlled clock for an executing attempt."""
    clock = {"now": 100.0}
    monkeypatch.setattr(lifecycle.time, "time", lambda: clock["now"])
    journal = Journal(tmp_path / "jobs.sqlite3")
    job = {"id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4()), "timeout": 10}
    journal.accept(job)
    assert prepare_execution(journal, job["id"], job["attempt_id"])
    journal.update(job["id"], "running", {"message": "Measured work started"}, job["attempt_id"], execution_status="running")
    return journal, job, clock


@pytest.mark.parametrize("had_pause_marker", [False, True])
def test_existing_journal_migrates_pause_fields_without_resetting_work(running, had_pause_marker):
    """Upgrade both older schemas while preserving attempts, reports and known pause markers."""
    journal, job, _ = running
    Reporter(journal, job["id"], job["attempt_id"]).save_state({"external_id": "synthetic-owned-job"})
    before = journal.get(job["id"])
    with sqlite3.connect(journal.path) as db:
        db.execute("DROP TABLE execution_controls")
        extra = ", paused_at REAL" if had_pause_marker else ""
        db.execute(
            "CREATE TABLE execution_controls (job_id TEXT, attempt_id TEXT, status TEXT NOT NULL, "
            "stop_requested TEXT NOT NULL DEFAULT ''" + extra + ", PRIMARY KEY(job_id,attempt_id))"
        )
        db.execute(
            "INSERT INTO execution_controls(job_id,attempt_id,status,stop_requested) VALUES(?,?,'running','interrupt')",
            (job["id"], job["attempt_id"]),
        )
        if had_pause_marker:
            db.execute("UPDATE execution_controls SET paused_at=104")
    migrated = Journal(journal.path)
    assert migrated.get(job["id"]) == before
    expected = {
        "status": "running",
        "stop_requested": "interrupt",
        "paused_at": 104 if had_pause_marker else None,
        "paused_seconds": 0,
        "controller_seen_at": None,
    }
    assert execution_control(migrated, job["id"], job["attempt_id"]) == expected
    assert execution_control(Journal(journal.path), job["id"], job["attempt_id"]) == expected


def test_repeated_interrupts_and_resumes_account_for_each_outage_once(running):
    """Retain the first pause time, accumulate later outages, and preserve the original start."""
    journal, job, clock = running
    clock["now"] = 104
    request_stop(journal, job["id"], job["attempt_id"], "interrupt")
    clock["now"] = 108
    request_stop(journal, job["id"], job["attempt_id"], "interrupt")
    assert execution_control(journal, job["id"], job["attempt_id"])["paused_at"] == 104
    journal = Journal(journal.path)
    clock["now"] = 1104
    assert prepare_execution(journal, job["id"], job["attempt_id"])
    assert execution_control(journal, job["id"], job["attempt_id"]) == {
        "status": "pending",
        "stop_requested": "",
        "paused_at": None,
        "paused_seconds": 1000,
        "controller_seen_at": None,
    }
    clock["now"] = 1105
    assert prepare_execution(journal, job["id"], job["attempt_id"])
    assert execution_control(journal, job["id"], job["attempt_id"])["paused_seconds"] == 1000
    clock["now"] = 1107
    request_stop(journal, job["id"], job["attempt_id"], "interrupt")
    clock["now"] = 1207
    assert prepare_execution(journal, job["id"], job["attempt_id"])
    assert execution_control(journal, job["id"], job["attempt_id"])["paused_seconds"] == 1100
    assert journal.get(job["id"])["started"] == 100


def test_stale_attempt_cannot_credit_a_replacement_execution(running):
    """An old pause receipt cannot change the replacement attempt's start or budget."""
    journal, job, clock = running
    clock["now"] = 104
    request_stop(journal, job["id"], job["attempt_id"], "interrupt")
    replacement = {**job, "attempt_id": str(uuid.uuid4())}
    journal.accept(replacement)
    clock["now"] = 106
    journal.update(job["id"], "running", {}, replacement["attempt_id"])
    clock["now"] = 1104
    assert not prepare_execution(journal, job["id"], job["attempt_id"])
    assert prepare_execution(journal, job["id"], replacement["attempt_id"])
    assert journal.get(job["id"])["started"] == 106
    assert execution_control(journal, job["id"], replacement["attempt_id"])["paused_seconds"] == 0
    assert execution_control(journal, job["id"], job["attempt_id"])["paused_at"] == 104


@pytest.mark.parametrize("reason", ["cancel", "timeout", "superseded"])
def test_interrupt_cannot_override_nonresumable_stop(running, reason):
    """Shutdown cannot downgrade a definitive stop into a resumable interruption."""
    journal, job, clock = running
    clock["now"] = 104
    request_stop(journal, job["id"], job["attempt_id"], "interrupt")
    request_stop(journal, job["id"], job["attempt_id"], reason)
    clock["now"] = 1104
    request_stop(journal, job["id"], job["attempt_id"], "interrupt")
    assert not prepare_execution(journal, job["id"], job["attempt_id"])
    control = execution_control(journal, job["id"], job["attempt_id"])
    assert control["stop_requested"] == reason
    assert control["paused_at"] == 104 and control["paused_seconds"] == 0
    assert journal.get(job["id"])["started"] == 100


def test_backward_clock_cannot_create_negative_paused_time(running):
    """A backwards wall clock clears the pause without subtracting consumed execution."""
    journal, job, clock = running
    clock["now"] = 104
    request_stop(journal, job["id"], job["attempt_id"], "interrupt")
    clock["now"] = 103
    assert prepare_execution(journal, job["id"], job["attempt_id"])
    control = execution_control(journal, job["id"], job["attempt_id"])
    assert control["paused_at"] is None and control["paused_seconds"] == 0
    assert journal.get(job["id"])["started"] == 100


def test_never_started_job_does_not_gain_extra_execution_budget(running):
    """Queue waiting before the first execution cannot become credit against future work."""
    journal, original, clock = running
    job = {**original, "id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4())}
    journal.accept(job)
    request_stop(journal, job["id"], job["attempt_id"], "interrupt")
    clock["now"] = 1100
    assert prepare_execution(journal, job["id"], job["attempt_id"])
    assert journal.get(job["id"])["started"] is None
    assert execution_control(journal, job["id"], job["attempt_id"])["paused_seconds"] == 0
    journal.update(job["id"], "running", {}, job["attempt_id"])
    assert journal.get(job["id"])["started"] == 1100


@pytest.mark.parametrize("external", [False, True])
def test_recovery_preserves_original_start_with_and_without_external_state(running, monkeypatch, external):
    """Entering a recovered worker cannot reset consumed execution or its historical start."""
    journal, job, clock = running
    if external:
        Reporter(journal, job["id"], job["attempt_id"]).save_state({"external_id": "synthetic-owned-job"})
    clock["now"] = 104
    request_stop(journal, job["id"], job["attempt_id"], "interrupt")
    clock["now"] = 1104
    assert prepare_execution(journal, job["id"], job["attempt_id"])
    with owned_worker(journal, job["id"], job["attempt_id"], recovery=True) as accepted:
        assert accepted
        assert journal.get(job["id"])["started"] == 100
        assert execution_control(journal, job["id"], job["attempt_id"])["paused_seconds"] == 1000


@pytest.mark.parametrize("external", [False, True])
def test_long_outage_preserves_remaining_budget_and_real_execution_still_times_out(running, monkeypatch, external):
    """Exclude outage time while retaining the four seconds already spent executing."""
    journal, job, clock = running
    if external:
        Reporter(journal, job["id"], job["attempt_id"]).save_state({"external_id": "synthetic-owned-job"})
    clock["now"] = 104
    request_stop(journal, job["id"], job["attempt_id"], "interrupt")
    clock["now"] = 10004
    assert prepare_execution(Journal(journal.path), job["id"], job["attempt_id"])
    worker = gateway.Gateway.__new__(gateway.Gateway)
    worker.journal, worker.processes = journal, {}
    active = {"value": True}
    monkeypatch.setattr(gateway, "stop_process", lambda _: active.update(value=False))
    worker.enforce_deadlines()
    assert journal.get(job["id"])["state"] == "running"
    clock["now"] = 10009
    worker.enforce_deadlines()
    assert journal.get(job["id"])["state"] == "running"
    clock["now"] = 10011
    worker.enforce_deadlines()
    row = journal.get(job["id"])
    # Timeout remains active until the controller confirms process and resource cleanup.
    assert row["state"] == "running"
    assert execution_control(journal, job["id"], job["attempt_id"])["stop_requested"] == "timeout"
    assert row["started"] == 100


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_terminal_result_cannot_be_resumed_or_rebudgeted(running, state):
    """A late resume cannot change a terminal attempt or its accounting."""
    journal, job, clock = running
    request_stop(journal, job["id"], job["attempt_id"], "interrupt")
    journal.update(job["id"], state, {"score": 7}, job["attempt_id"])
    before = journal.get(job["id"])
    clock["now"] = 1104
    assert not prepare_execution(journal, job["id"], job["attempt_id"])
    assert journal.get(job["id"]) == before
    assert execution_control(journal, job["id"], job["attempt_id"])["paused_seconds"] == 0
