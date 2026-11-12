"""SDK-owned task recovery, cancellation and optional lifecycle extension hooks."""

import asyncio
import inspect
import json
import multiprocessing
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from llm4ad.gateway.sdk import EvaluationContract, EvaluationResult, GatewayEvaluator, Metric
from llm4ad.gateway.sdk.gateway import Gateway, execute, stop_process
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.lifecycle import execution_control, owned_worker, prepare_execution, request_stop, worker_active


def job_payload(root):
    """Create an isolated managed candidate with no platform connection."""
    root.mkdir(parents=True)
    return {
        "id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "project_root": str(root),
        "timeout": 60,
        "state": "accepted",
    }


def adapter_file(tmp_path, *, hanging=False):
    """Write an ordinary evaluator with hooks and no scheduling implementation."""
    path = tmp_path / "adapter.py"
    path.write_text(
        """
import asyncio
from pathlib import Path
from llm4ad.gateway.sdk import GatewayEvaluator, EvaluationContract, EvaluationResult, Metric
class Evaluator(GatewayEvaluator):
    contract = EvaluationContract(evaluator_id="lifecycle", version="1", data_version="1", metrics=[Metric(name="quality")])
    async def before_task(self, job, reporter, operation):
        reporter.message("before:" + operation)
    async def after_task(self, job, reporter, operation, *, result=None, error=None):
        reporter.message("after:" + operation, data={"error": type(error).__name__ if error else None})
    async def evaluate(self, job, reporter):
        marker = Path(job.project_root) / "begun"
        first = not marker.exists()
        marker.write_text("executed")
        reporter.progress("Inside evaluator")
        if first and HANGING:
            while True:
                await asyncio.sleep(0.05)
        return EvaluationResult(score=7, metrics={"quality": 7})
""".replace("HANGING", repr(hanging))
    )
    return f"{path}:Evaluator"


async def wait_for(predicate, timeout=10):
    """Wait for an observable worker transition rather than an arbitrary delay."""
    deadline = time.monotonic() + timeout
    while True:
        observed = predicate()
        if await observed if inspect.isawaitable(observed) else observed:
            return
        assert time.monotonic() < deadline, "Worker transition timed out"
        await asyncio.sleep(0.03)


async def test_ordinary_adapter_cancels_pending_local_work(tmp_path):
    """A user implementing evaluate alone does not need a cancel method."""
    spec = adapter_file(tmp_path)
    worker = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, tmp_path / "state")
    job = job_payload(tmp_path / "candidate")
    worker.journal.accept(job)
    await worker.reconcile(None, job | {"state": "cancel_requested"})
    assert worker.journal.get(job["id"])["state"] == "cancelled"
    messages = Reporter(worker.journal, job["id"]).enrich({})["metadata"]["evaluation_messages"]
    assert [item["message"] for item in messages] == ["before:cancel", "after:cancel"]


async def test_restart_of_never_started_work_does_not_need_recover_override(tmp_path):
    """The SDK resumes accepted work rather than asking users to implement recovery."""
    spec = adapter_file(tmp_path)
    state = tmp_path / "state"
    first = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, state)
    job = job_payload(tmp_path / "candidate")
    first.journal.accept(job)
    worker = Gateway("http://localhost:9", "fixture", first.allowed_users, spec, state)
    worker.start_process = Mock()

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        async def read(self):
            from llm4ad.gateway.sdk.bundles import pack_candidate

            return pack_candidate(job["project_root"])

    class Session:
        def get(self, *args):
            return Response()

    (Path(job["project_root"]) / "solution.py").write_text("pass\n")
    await worker.reconcile(Session(), job)
    worker.start_process.assert_called_once()


async def test_interrupted_local_worker_is_recovered_by_sdk(tmp_path):
    """A stopped local computation restarts without custom recover/cancel methods."""
    spec = adapter_file(tmp_path, hanging=True)
    state = tmp_path / "state"
    first = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, state)
    job = job_payload(tmp_path / "candidate")
    first.journal.accept(job)
    first.start_process(job)
    process = first.processes[job["id"]]
    try:
        await wait_for(lambda: (Path(job["project_root"]) / "begun").exists())
        first.stop_workers()
        worker = Gateway("http://localhost:9", "fixture", first.allowed_users, spec, state)

        async def recovered():
            await worker.reconcile(None, job)
            return worker.journal.get(job["id"])["state"] == "completed"

        await wait_for(recovered)
        result = json.loads(worker.journal.get(job["id"])["report"])
        assert result["score"] == 7
        messages = [item["message"] for item in result["metadata"]["evaluation_messages"]]
        assert "before:recover" in messages and "after:recover" in messages
    finally:
        stop_process(process)
        if "worker" in locals():
            for child in worker.processes.values():
                stop_process(child)


async def test_restart_does_not_duplicate_live_worker_and_can_cancel_it(tmp_path):
    """A replacement gateway adopts an orphaned worker's result/cancellation control."""
    spec = adapter_file(tmp_path, hanging=True)
    state = tmp_path / "state"
    first = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, state)
    job = job_payload(tmp_path / "candidate")
    first.journal.accept(job)
    process = multiprocessing.get_context("spawn").Process(target=execute, args=(first.journal.path, spec, job))
    process.start()
    try:
        await wait_for(lambda: (Path(job["project_root"]) / "begun").exists())
        worker = Gateway("http://localhost:9", "fixture", first.allowed_users, spec, state)
        worker.start_process = Mock()
        await worker.reconcile(None, job)
        worker.start_process.assert_not_called()
        for _ in range(100):
            await worker.reconcile(None, job | {"state": "cancel_requested"})
            if worker.journal.get(job["id"])["state"] == "cancelled":
                break
            await asyncio.sleep(0.03)
        assert worker.journal.get(job["id"])["state"] == "cancelled"
        await wait_for(lambda: not process.is_alive())
    finally:
        stop_process(process)


@pytest.mark.parametrize("failing_hook", ["before", "after", "evaluate"])
def test_post_hook_gets_failures_and_preserves_original_error(tmp_path, monkeypatch, failing_hook):
    """Hook errors must not conceal the original evaluation error or skip finalization."""
    from llm4ad.gateway.sdk import gateway

    class Adapter(GatewayEvaluator):
        contract = EvaluationContract(evaluator_id="hooks", version="1", data_version="1", metrics=[Metric(name="quality")])

        async def before_task(self, job, reporter, operation):
            if failing_hook == "before":
                raise ValueError("before failed")

        async def evaluate(self, job, reporter):
            if failing_hook == "evaluate":
                raise ValueError("evaluation failed")
            return EvaluationResult(score=1, metrics={"quality": 1})

        async def after_task(self, job, reporter, operation, *, result=None, error=None):
            reporter.metadata({"post_called": True, "original_error": str(error) if error else None})
            if failing_hook in {"after", "evaluate"}:
                raise RuntimeError("after failed")

    job = job_payload(tmp_path / "candidate")
    journal = Journal(tmp_path / "jobs.sqlite3")
    journal.accept(job)
    monkeypatch.chdir(tmp_path)
    if hasattr(gateway.os, "setsid"):
        monkeypatch.setattr(gateway.os, "setsid", lambda: None)
    monkeypatch.setattr(gateway, "load_evaluator", lambda _: Adapter())
    execute(journal.path, "fixture:Evaluator", job)
    result = json.loads(journal.get(job["id"])["report"])
    assert result["metadata"]["post_called"] is True
    expected = {"before": "before failed", "after": "after failed", "evaluate": "evaluation failed"}
    assert result["error_message"] == expected[failing_hook]


def test_execution_lease_fences_duplicates_and_terminal_attempts(tmp_path):
    """The same attempt cannot run concurrently or restart after its result is durable."""
    job = job_payload(tmp_path / "candidate")
    journal = Journal(tmp_path / "jobs.sqlite3")
    journal.accept(job)
    with owned_worker(journal, job["id"], job["attempt_id"], recovery=False) as accepted:
        assert accepted
        assert worker_active(journal, job["id"], job["attempt_id"])
        journal.update(job["id"], "completed", {"metrics": {}}, job["attempt_id"], execution_status="finished")
    assert not worker_active(journal, job["id"], job["attempt_id"])
    with owned_worker(journal, job["id"], job["attempt_id"], recovery=True) as accepted:
        assert not accepted


async def test_local_capacity_also_limits_recovered_work(tmp_path):
    """Previously accepted/recovering jobs cannot bypass the configured local capacity."""
    spec = adapter_file(tmp_path, hanging=True)
    worker = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, tmp_path / "state")
    first = job_payload(tmp_path / "candidate-a")
    second = job_payload(tmp_path / "candidate-b")
    worker.journal.accept(first)
    worker.journal.accept(second)
    worker.start_process(first)
    process = worker.processes[first["id"]]
    try:
        await wait_for(lambda: (Path(first["project_root"]) / "begun").exists())
        worker.start_process(second, recovery=True)
        assert second["id"] not in worker.processes
        assert worker.journal.get(second["id"])["state"] == "accepted"
    finally:
        worker.stop_workers()
        stop_process(process)


async def test_explicitly_suspended_local_work_is_not_replayed(tmp_path):
    """An evaluator-requested wait is not an unclean worker interruption."""
    spec = adapter_file(tmp_path)
    worker = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, tmp_path / "state")
    job = job_payload(tmp_path / "candidate")
    worker.journal.accept(job)
    with owned_worker(worker.journal, job["id"], job["attempt_id"], recovery=False):
        worker.journal.update(
            job["id"], "recovery_required", {"message": "Resource unavailable"}, job["attempt_id"], execution_status="suspended"
        )
    restarted = Gateway("http://localhost:9", "fixture", worker.allowed_users, spec, tmp_path / "state")
    restarted.start_process = Mock()
    await restarted.reconcile(None, job)
    restarted.start_process.assert_not_called()
    assert execution_control(restarted.journal, job["id"], job["attempt_id"])["status"] == "suspended"


async def test_recorded_cancellation_survives_gateway_restart(tmp_path):
    """A stop received before a gateway crash cannot become replayed work."""
    spec = adapter_file(tmp_path)
    state = tmp_path / "state"
    worker = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, state)
    job = job_payload(tmp_path / "candidate")
    worker.journal.accept(job)
    request_stop(worker.journal, job["id"], job["attempt_id"], "cancel")
    restarted = Gateway("http://localhost:9", "fixture", worker.allowed_users, spec, state)
    await restarted.reconcile(None, job)
    assert restarted.journal.get(job["id"])["state"] == "cancelled"
    assert not (Path(job["project_root"]) / "begun").exists()


async def test_missing_journal_cannot_falsely_confirm_external_cancellation(tmp_path):
    """An old assignment cannot become known-local work merely by recording its status."""
    spec = adapter_file(tmp_path)
    worker = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, tmp_path / "state")
    job = job_payload(tmp_path / "candidate")
    await worker.reconcile(None, job)
    await worker.reconcile(None, job | {"state": "cancel_requested"})
    assert worker.journal.get(job["id"])["state"] == "recovery_required"
    assert execution_control(worker.journal, job["id"], job["attempt_id"])["status"] == "unknown"


async def test_operator_retry_waits_for_worker_and_precedes_automatic_replay(tmp_path):
    """Explicit retry cannot duplicate active execution or be overwritten by local replay."""
    from typer.testing import CliRunner

    from llm4ad.gateway.cli import app

    spec = adapter_file(tmp_path)
    state = tmp_path / "state"
    worker = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, state)
    job = job_payload(tmp_path / "candidate")
    worker.journal.accept(job)
    args = ["resolve", job["id"], "--action", "retry", "--state-dir", str(state)]
    with owned_worker(worker.journal, job["id"], job["attempt_id"], recovery=False):
        worker.journal.update(job["id"], "recovery_required", {"message": "Reconciling"})
        result = CliRunner().invoke(app, args)
        assert result.exit_code != 0 and "still active" in result.output
        assert "retry_confirmed" not in json.loads(worker.journal.get(job["id"])["report"])
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    worker.start_process = Mock()
    await worker.reconcile(None, job)
    worker.start_process.assert_not_called()
    assert json.loads(worker.journal.get(job["id"])["report"])["retry_confirmed"] is True


async def test_legacy_cancellation_as_first_reconnect_preserves_uncertainty(tmp_path):
    """The first server stop cannot bypass legacy execution-state reconciliation."""
    spec = adapter_file(tmp_path)
    state = tmp_path / "state"
    first = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, state)
    job = job_payload(tmp_path / "candidate")
    first.journal.accept(job)
    Reporter(first.journal, job["id"]).progress("Legacy worker started")
    worker = Gateway("http://localhost:9", "fixture", first.allowed_users, spec, state)
    await worker.reconcile(None, job | {"state": "cancel_requested"})
    assert worker.journal.get(job["id"])["state"] == "recovery_required"


async def test_cancellation_reads_external_state_after_preparation_and_stop(tmp_path):
    """A handle published during a cancellation race must still be confirmed."""
    spec = adapter_file(tmp_path)
    worker = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, tmp_path / "state")
    job = job_payload(tmp_path / "candidate")
    worker.journal.accept(job)

    async def before(job, reporter, operation):
        reporter.save_state({"external_id": "submitted-during-stop"})

    worker.adapter.before_task = before
    worker.adapter.cancel = AsyncMock(return_value=False)
    await worker.reconcile(None, job | {"state": "cancel_requested"})
    worker.adapter.cancel.assert_awaited_once()
    assert worker.adapter.cancel.call_args.args[1] == {"external_id": "submitted-during-stop"}
    assert worker.journal.get(job["id"])["state"] == "recovery_required"


def test_deadline_reloads_external_state_after_stopping(tmp_path, monkeypatch):
    """Timeouts retain an external handle persisted after their initial snapshot."""
    from llm4ad.gateway.sdk import gateway

    spec = adapter_file(tmp_path)
    worker = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, tmp_path / "state")
    job = job_payload(tmp_path / "candidate")
    worker.journal.accept(job)
    reporter = Reporter(worker.journal, job["id"])
    reporter.progress("Running")
    with worker.journal.connect() as db:
        db.execute("UPDATE jobs SET started=?", (time.time() - 120,))
    monkeypatch.setattr(gateway, "stop_process", lambda _: reporter.save_state({"external_id": "late-handle"}))
    worker.enforce_deadlines()
    assert worker.journal.get(job["id"])["state"] == "recovery_required"


async def test_adopted_external_worker_is_recovered_after_it_exits(tmp_path):
    """Liveness adoption must not permanently consume the external recovery trigger."""
    spec = adapter_file(tmp_path)
    worker = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, tmp_path / "state")
    job = job_payload(tmp_path / "candidate")
    worker.journal.accept(job)
    Reporter(worker.journal, job["id"]).save_state({"external_id": "fixture"})
    worker.adapter = type("Adapter", (GatewayEvaluator,), {"recover": AsyncMock(return_value=None)})()
    worker.start_process = Mock()
    with owned_worker(worker.journal, job["id"], job["attempt_id"], recovery=False):
        await worker.reconcile(None, job)
        worker.start_process.assert_not_called()
    await worker.reconcile(None, job)
    worker.start_process.assert_called_once_with(job, recovery=True)


async def test_pending_spawn_survives_gateway_startup_failure(tmp_path):
    """A crash before worker startup is known pending work, rather than legacy ambiguity."""
    spec = adapter_file(tmp_path)
    state = tmp_path / "state"
    first = Gateway("http://localhost:9", "fixture", [str(uuid.uuid4())], spec, state)
    job = job_payload(tmp_path / "candidate")
    first.journal.accept(job)
    assert prepare_execution(first.journal, job["id"], job["attempt_id"])
    first.journal.update(job["id"], "running", {"message": "Starting worker"})
    worker = Gateway("http://localhost:9", "fixture", first.allowed_users, spec, state)
    worker.start_process = Mock()
    await worker.reconcile(None, job)
    worker.start_process.assert_called_once_with(job, recovery=True)


def test_explicit_resolution_does_not_overwrite_a_completed_worker(tmp_path, monkeypatch):
    """A result committed during resolution's lease check remains authoritative."""
    from contextlib import contextmanager

    from llm4ad.gateway.sdk import lifecycle

    job = job_payload(tmp_path / "candidate")
    journal = Journal(tmp_path / "jobs.sqlite3")
    journal.accept(job)
    journal.update(job["id"], "recovery_required", {})
    original = lifecycle.gateway_lease

    @contextmanager
    def complete_before_lease(path):
        journal.update(job["id"], "completed", {"score": 9, "metrics": {"quality": 9}}, job["attempt_id"])
        with original(path):
            yield

    monkeypatch.setattr(lifecycle, "gateway_lease", complete_before_lease)
    with pytest.raises(ValueError, match="uncertain"):
        lifecycle.resolve_execution(journal, job["id"], "retry")
    assert journal.get(job["id"])["state"] == "completed"
    assert json.loads(journal.get(job["id"])["report"])["score"] == 9
