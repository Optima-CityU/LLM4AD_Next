"""Transactional execution outcomes and independent resource admission fences."""

import json
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from llm4ad.gateway.sdk import executions as execution_module
from llm4ad.gateway.sdk.executions import Executions
from llm4ad.gateway.sdk.journal import Journal
from llm4ad.gateway.sdk.lifecycle import execution_control
from llm4ad.gateway.sdk.scheduling import DurableExecutor

pytestmark = pytest.mark.unit


@pytest.fixture
def execution(tmp_path):
    """Prepare a current local generation without starting any evaluator or command."""
    journal = Journal(tmp_path / "jobs.sqlite3")
    original = {"id": "job", "attempt_id": "platform-attempt"}
    journal.accept(original)
    store = Executions(journal)
    store.ensure(original, journal.get(original["id"]))
    job = store.begin(original)
    with journal.connect() as db:
        db.execute(
            "INSERT INTO execution_controls(job_id,attempt_id,status,stop_requested) VALUES(?,?,'running','')",
            (job["id"], job["attempt_id"]),
        )
    journal.update(job["id"], "running", {"message": "entered"}, job["attempt_id"], execution_id=job["execution_id"])
    return SimpleNamespace(journal=journal, store=store, job=job)


def snapshot(execution):
    """Read all records whose changes must be atomic for the current generation."""
    job, journal, store = execution.job, execution.journal, execution.store
    return {
        "job": journal.get(job["id"]),
        "execution": store.get(job["id"], job["attempt_id"]),
        "control": execution_control(journal, job["id"], job["attempt_id"]),
    }


def test_terminal_report_and_fsm_survive_reopen_as_one_outcome(execution, monkeypatch):
    """A committed terminal FSM state always has its report, sequence and control status."""
    before = snapshot(execution)
    now = before["job"]["updated_at"] + 10
    monkeypatch.setattr(execution_module, "time", SimpleNamespace(time=lambda: now))
    report = {"success": True, "metrics": {"quality": 7}, "message": "finished"}
    assert execution.store.commit(execution.job, ["succeed"], "completed", report)
    reopened = Journal(execution.journal.path)
    record = Executions(reopened).get(execution.job["id"], execution.job["attempt_id"])
    row = reopened.get(execution.job["id"])
    assert row["state"] == record["state"] == "completed"
    assert json.loads(row["report"]) == report
    assert row["seq"] == before["job"]["seq"] + 1
    assert row["ack"] == before["job"]["ack"]
    assert row["started"] == before["job"]["started"]
    assert row["updated_at"] == row["finished_at"] == now
    assert row["created_at"] == before["job"]["created_at"]
    assert record["executions"] == before["execution"]["executions"]
    assert record["retry_at"] == 0
    assert execution_control(reopened, execution.job["id"], execution.job["attempt_id"])["status"] == "finished"
    terminal = snapshot(execution)
    assert not execution.store.commit(execution.job, ["fail"], "failed", {"success": False})
    assert snapshot(execution) == terminal


@pytest.mark.parametrize("failure_table", ["jobs", "execution_controls"])
def test_update_exception_rolls_back_fsm_report_and_control_together(execution, failure_table):
    """A real SQLite failure after an FSM write cannot recreate the terminal crash gap."""
    column = "report" if failure_table == "jobs" else "status"
    with execution.journal.connect() as db:
        db.execute(
            f"CREATE TRIGGER fail_commit BEFORE UPDATE OF {column} ON {failure_table} "
            "BEGIN SELECT RAISE(ABORT, 'synthetic commit interruption'); END"
        )
    before = snapshot(execution)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic commit interruption"):
        execution.store.commit(execution.job, ["succeed"], "completed", {"success": True})
    assert snapshot(execution) == before
    with execution.journal.connect() as db:
        db.execute("DROP TRIGGER fail_commit")
    assert execution.store.commit(execution.job, ["succeed"], "completed", {"success": True})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), object()])
def test_invalid_json_cannot_leave_a_terminal_execution_without_a_report(execution, value):
    """Nonfinite or unserializable fields change neither table nor outbox sequence."""
    before = snapshot(execution)
    with pytest.raises((ValueError, TypeError)):
        execution.store.commit(execution.job, ["succeed"], "completed", {"metrics": {"quality": value}})
    assert snapshot(execution) == before


def test_invalid_later_event_rolls_back_the_whole_event_sequence(execution):
    """Earlier valid events do not escape when a later event is invalid."""
    before = snapshot(execution)
    with pytest.raises(ValueError, match="transition"):
        execution.store.commit(execution.job, ["retry", "succeed", "fail"], "failed", {})
    assert snapshot(execution) == before


def test_retry_preserves_start_time_and_counter_while_committing_retry_deadline(execution, monkeypatch):
    """Retry scheduling and its outbound running report are one journal outcome."""
    before = snapshot(execution)
    now = before["job"]["updated_at"] + 20
    monkeypatch.setattr(execution_module, "time", SimpleNamespace(time=lambda: now))
    report = {"message": "retrying transient failure"}
    assert execution.store.commit(execution.job, ["retry"], "running", report, execution_status="retrying", delay=5)
    after = snapshot(execution)
    assert after["execution"]["state"] == "retrying"
    assert after["execution"]["retry_at"] == now + 5
    assert after["execution"]["executions"] == before["execution"]["executions"]
    assert after["job"]["state"] == "running" and after["job"]["finished_at"] is None
    assert after["job"]["started"] == before["job"]["started"]
    assert json.loads(after["job"]["report"]) == report
    assert after["control"]["status"] == "retrying"


@pytest.mark.parametrize("stop", ["cancel", "timeout", "superseded"])
def test_stop_fence_requires_explicit_allowance_and_preserves_stop_reason(execution, stop):
    """Cleanup failure can finish stopped work without allowing stale successes."""
    with execution.journal.connect() as db:
        db.execute("UPDATE execution_controls SET stop_requested=?", (stop,))
    before = snapshot(execution)
    assert not execution.store.commit(execution.job, ["succeed"], "completed", {"success": True})
    assert snapshot(execution) == before
    assert execution.store.commit(execution.job, ["fail"], "failed", {"success": False}, allow_stopped=True)
    after = snapshot(execution)
    assert after["job"]["state"] == after["execution"]["state"] == "failed"
    assert after["control"]["status"] == "finished"
    assert after["control"]["stop_requested"] == stop


def test_cancellation_events_commit_together_and_reject_a_late_success(execution):
    """Two explicit cancellation events produce one durable final report sequence."""
    with execution.journal.connect() as db:
        db.execute("UPDATE execution_controls SET stop_requested='cancel'")
    before = snapshot(execution)
    assert execution.store.commit(
        execution.job,
        ["cancel", "finish_cancel"],
        "cancelled",
        {"message": "cancelled"},
        allow_stopped=True,
    )
    after = snapshot(execution)
    assert after["job"]["seq"] == before["job"]["seq"] + 1
    assert after["job"]["state"] == after["execution"]["state"] == "cancelled"
    assert not execution.store.commit(execution.job, ["succeed"], "completed", {}, allow_stopped=True)
    assert snapshot(execution) == after


def test_concurrent_terminal_decisions_increment_sequence_once(execution):
    """Competing outcome callbacks have one atomic winner and one fenced rejection."""
    before = snapshot(execution)
    ready = Barrier(2)

    def finish(label):
        ready.wait(timeout=5)
        return execution.store.commit(execution.job, ["succeed"], "completed", {"winner": label})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finish, ["first", "second"]))
    assert sorted(results) == [False, True]
    after = snapshot(execution)
    assert after["job"]["seq"] == before["job"]["seq"] + 1
    assert after["job"]["state"] == after["execution"]["state"] == "completed"
    assert json.loads(after["job"]["report"])["winner"] == ("first" if results[0] else "second")


def test_cancellation_committed_while_outcome_waits_for_writer_lock_wins(execution):
    """The stop request is re-read inside BEGIN IMMEDIATE after writer contention."""
    before = snapshot(execution)
    entered = Event()

    def finish():
        entered.set()
        return execution.store.commit(execution.job, ["succeed"], "completed", {"success": True})

    with ThreadPoolExecutor(max_workers=1) as pool:
        with execution.journal.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE execution_controls SET stop_requested='cancel'")
            pending = pool.submit(finish)
            assert entered.wait(timeout=5)
        assert pending.result(timeout=5) is False
    after = snapshot(execution)
    assert after["job"] == before["job"]
    assert after["execution"] == before["execution"]
    assert after["control"]["stop_requested"] == "cancel"


def test_stale_generation_and_missing_generation_cannot_commit(execution):
    """An omitted execution ID must not disable the current local-generation fence."""
    original = dict(execution.job)
    execution.store.move(original, "retry")
    current = execution.store.begin(original)
    before = snapshot(execution)
    for stale in [original, {"id": original["id"], "attempt_id": original["attempt_id"]}]:
        assert not execution.store.commit(stale, ["fail"], "failed", {}, allow_stopped=True)
        assert snapshot(execution) == before
    assert current["execution_id"] != original["execution_id"]


def test_stale_platform_attempt_cannot_commit(execution):
    """Platform supersession fences both the job report and the old execution row."""
    execution.journal.accept({"id": execution.job["id"], "attempt_id": "new-platform-attempt"})
    before = snapshot(execution)
    assert not execution.store.commit(execution.job, ["fail"], "failed", {}, allow_stopped=True)
    assert snapshot(execution) == before


def test_terminal_wire_state_cannot_be_overwritten_even_when_local_fsm_is_running(execution):
    """A terminal wire result wins over a late callback from the current generation."""
    execution.journal.update(execution.job["id"], "cancelled", {"message": "already cancelled"}, execution.job["attempt_id"])
    before = snapshot(execution)
    assert not execution.store.commit(execution.job, ["succeed"], "completed", {}, allow_stopped=True)
    assert snapshot(execution) == before


def test_execution_and_job_generation_must_both_match(execution):
    """A matching jobs row cannot authorize another local execution generation."""
    with execution.journal.connect() as db:
        db.execute("UPDATE local_executions SET execution_id='different-generation'")
    before = snapshot(execution)
    assert not execution.store.commit(execution.job, ["fail"], "failed", {})
    assert snapshot(execution) == before


def test_unstarted_legacy_generation_gets_start_and_control_status_atomically(tmp_path):
    """A matching empty generation is supported without weakening nonempty fences."""
    journal = Journal(tmp_path / "jobs.sqlite3")
    job = {"id": "legacy", "attempt_id": "attempt"}
    journal.accept(job)
    store = Executions(journal)
    store.ensure(job, journal.get(job["id"]))
    assert store.commit(job, ["launch"], "running", {}, execution_status="running")
    row = journal.get(job["id"])
    assert row["started"] == row["updated_at"] and row["finished_at"] is None
    assert execution_control(journal, job["id"], job["attempt_id"])["status"] == "running"


@pytest.mark.parametrize("delay", [-1, float("nan"), float("inf")])
def test_invalid_retry_delay_is_not_persisted(execution, delay):
    """Invalid deadlines cannot enter otherwise valid retry outcomes."""
    before = snapshot(execution)
    with pytest.raises(ValueError):
        execution.store.commit(execution.job, ["retry"], "running", {}, delay=delay)
    assert snapshot(execution) == before


def test_terminal_fsm_and_wire_state_must_agree(execution):
    """An incorrect caller-supplied wire state cannot manufacture divergent records."""
    before = snapshot(execution)
    with pytest.raises(ValueError, match="wire state"):
        execution.store.commit(execution.job, ["succeed"], "failed", {})
    assert snapshot(execution) == before


def test_independent_holds_cannot_be_cleared_by_manual_pause_changes(tmp_path):
    """Startup and cleanup owners release only their own admission fences."""
    executor = DurableExecutor(tmp_path / "queue")
    try:
        executor.hold("sdk-restoration")
        executor.hold("cleanup:job")
        executor.paused = False
        assert executor.paused
        executor.release("sdk-restoration")
        assert executor.paused
        executor.paused = True
        executor.release("cleanup:job")
        assert executor.paused
        executor.paused = False
        assert not executor.paused
        executor.release("cleanup:job")
        assert executor.active_keys() == ()
    finally:
        executor.close()


def test_hold_drains_active_work_and_preserves_completed_slot_until_ack(tmp_path):
    """A resource fence stops new dispatch while existing Futures still finish."""
    executor = DurableExecutor(tmp_path / "queue")
    try:
        first = executor.submit("first", math.sqrt, (4,))
        second = executor.submit("second", math.sqrt, (9,))
        executor.poll()
        executor.hold("cleanup:resource")
        assert first.result(timeout=15) == 2
        assert executor.active_keys() == ("first",)
        assert executor.future("first").done()
        executor.ack("first")
        executor.poll()
        assert not second.done() and executor.active_keys() == ()
        executor.release("cleanup:resource")
        executor.poll()
        assert second.result(timeout=15) == 3
        executor.ack("second")
    finally:
        executor.close()
