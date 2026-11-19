"""SDK-owned task recovery, cancellation and optional lifecycle extension hooks."""

import asyncio
import inspect
import json
import time
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

from llm4ad.gateway.sdk import EvaluationContract, EvaluationResult, GatewayEvaluator, Metric
from llm4ad.gateway.sdk.gateway import Gateway, execute
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.lifecycle import owned_worker, request_stop, worker_active

pytestmark = pytest.mark.unit


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
    outcome = execute(journal.path, "fixture:Evaluator", job)
    result = outcome["report"]
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


@pytest.mark.parametrize("external_state", [False, True])
def test_preparation_error_remains_nonretryable_with_saved_state(tmp_path, monkeypatch, external_state):
    """Declared preparation errors finish normally; cleanup remains the controller responsibility."""
    from llm4ad.gateway.sdk import gateway
    from llm4ad.gateway.sdk.errors import EvaluationPreparationError

    class Adapter(GatewayEvaluator):
        contract = EvaluationContract(evaluator_id="preparation", version="1", data_version="1", metrics=[Metric(name="quality")])

        async def before_task(self, job, reporter, operation):
            raise EvaluationPreparationError("Local compiler unavailable")

        async def recover(self, job, reporter):
            raise AssertionError("Failed preparation must not reach recovery execution")

    job = job_payload(tmp_path / "candidate")
    journal = Journal(tmp_path / "jobs.sqlite3")
    journal.accept(job)
    if external_state:
        Reporter(journal, job["id"]).save_state({"external_job_id": "existing-execution"})
    monkeypatch.setattr(gateway, "load_evaluator", lambda _: Adapter())
    monkeypatch.chdir(tmp_path)
    if hasattr(gateway.os, "setsid"):
        monkeypatch.setattr(gateway.os, "setsid", lambda: None)
    outcome = execute(journal.path, "fixture:Evaluator", job, recovery=True)
    assert outcome["retryable"] is False
    assert outcome["report"]["error_type"] == "EvaluationPreparationError"


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
