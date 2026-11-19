"""Exercise durable SDK retries with real pool workers and ordinary evaluators."""

import asyncio
import json
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from llm4ad.gateway.sdk import RemoteJob
from llm4ad.gateway.sdk.executions import Executions
from llm4ad.gateway.sdk.gateway import Gateway
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.lifecycle import execution_control

pytestmark = pytest.mark.unit


def make_gateway(tmp_path, *, failures=2, hang=False):
    """Build a plain evaluator without custom scheduling or recovery hooks."""
    source = tmp_path / "candidate"
    source.mkdir()
    (source / "solution.py").write_text("pass\n")
    adapter = tmp_path / "adapter.py"
    adapter.write_text("""
import asyncio
from pathlib import Path
from llm4ad.gateway.sdk import GatewayEvaluator, EvaluationContract, EvaluationResult, Metric
class Evaluator(GatewayEvaluator):
    contract = EvaluationContract(evaluator_id="retry",version="1",data_version="1",metrics=[Metric(name="quality")])
    async def evaluate(self, job, reporter):
        counter = Path(job.parameters["counter"])
        count = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(count))
        reporter.save_state({"local_checkpoint": count})
        reporter.progress("entered",metadata={"worker_number":count})
        path = Path(job.artifact_dir) / "evidence.txt"
        path.write_text(str(count))
        reporter.artifact("evidence.txt",path,"text/plain")
        if job.parameters.get("crash") and count == 1:
            __import__("os")._exit(17)
        if job.parameters.get("fatal") and count == 1:
            raise {"SystemExit": SystemExit, "KeyboardInterrupt": KeyboardInterrupt}[job.parameters["fatal"]]("worker stopped")
        if job.parameters["hang"] and count == 1:
            await asyncio.sleep(300)
        if count <= job.parameters["failures"]:
            raise OSError("transient read")
        return EvaluationResult(score=7,metrics={"quality":7},metadata={"worker_number":count})
""")
    job = {
        "id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "timeout": 40,
        "project_root": str(source),
        "state": "accepted",
        "parameters": {"counter": str(tmp_path / "calls"), "failures": failures, "hang": hang},
    }
    gateway = Gateway("http://localhost:9", "test", [str(uuid.uuid4())], f"{adapter}:Evaluator", tmp_path / "state")
    gateway.journal.accept(job)
    return gateway, job


async def advance(gateway, job, predicate, timeout=25):
    """Advance local scheduling while deliberately disconnected from the platform."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await gateway.reconcile(None, job)
        row = gateway.journal.get(job["id"])
        if predicate(row):
            return row
        await asyncio.sleep(0.03)
    pytest.fail("Local execution did not reach its expected durable state")


async def test_infrastructure_retry_finishes_without_network_or_custom_recover(tmp_path):
    """Two failed reads retry, with fresh evidence and exactly three executions."""
    gateway, job = make_gateway(tmp_path)
    try:
        row = await advance(gateway, job, lambda row: row["state"] == "completed")
        report = json.loads(row["report"])
        assert report["score"] == 7
        assert report["metadata"]["worker_number"] == 3
        record = gateway.executions.get(job["id"], job["attempt_id"])
        assert record["executions"] == 3
        assert record["state"] == "completed"
        artifacts = gateway.journal.artifacts(job["id"], job["attempt_id"])
        assert len([a for a in artifacts if a["name"] == "evidence.txt"]) == 1
        assert Path(next(a["path"] for a in artifacts if a["name"] == "evidence.txt")).read_text() == "3"
        assert row["seq"] > row["ack"]
        assert not gateway.executor.entries()
        with gateway.journal.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM execution_history").fetchone()[0] == 2
    finally:
        gateway.stop_workers()


async def test_persistent_infrastructure_failure_is_bounded(tmp_path):
    """Retry exhaustion produces a final failed result and releases capacity."""
    gateway, job = make_gateway(tmp_path, failures=100)
    try:
        row = await advance(gateway, job, lambda row: row["state"] == "failed")
        assert gateway.executions.get(job["id"], job["attempt_id"])["executions"] == 3
        assert json.loads(row["report"])["metadata"]["infrastructure_failure"] is True
        assert not gateway.executor.entries()
    finally:
        gateway.stop_workers()


async def test_retry_evidence_cleanup_waits_for_ack_and_removes_all_executions(tmp_path):
    """Final delivery releases all retry workspaces without deleting private logs."""
    from llm4ad.gateway.sdk.retention import EvidencePolicy, collect_finished

    class Policy(EvidencePolicy):
        clear_worktree = True

    gateway, job = make_gateway(tmp_path)
    try:
        row = await advance(gateway, job, lambda row: row["state"] == "completed")
        base = gateway.root / "work" / job["id"] / job["attempt_id"]
        assert len(list(base.iterdir())) == 3
        assert collect_finished(gateway, policy=Policy())["cleaned"] == 0
        with gateway.journal.connect() as db:
            db.execute("UPDATE artifacts SET object_key='saved'")
        gateway.journal.acknowledge(job["id"], row["seq"])
        result = collect_finished(gateway, policy=Policy())
        assert result["errors"] == 0 and result["cleaned"] == 1
        assert not base.exists()
        assert not (gateway.root / "candidates" / job["id"] / job["attempt_id"]).exists()
        assert (gateway.root / "local-logs").is_dir()
    finally:
        gateway.stop_workers()


async def test_cancel_ordinary_evaluator_with_checkpoint_never_retries(tmp_path):
    """Saving local state does not require users to implement cancellation."""
    gateway, job = make_gateway(tmp_path, hang=True)
    try:
        await advance(gateway, job, lambda row: Path(job["parameters"]["counter"]).exists())
        response = await gateway.request_cancel(job["id"], job["attempt_id"])
        assert response["status"] == "cancelled"
        await gateway.reconcile(None, job)
        assert Path(job["parameters"]["counter"]).read_text() == "1"
        assert execution_control(gateway.journal, job["id"], job["attempt_id"])["stop_requested"] == "cancel"
        assert not gateway.executor.entries()
    finally:
        gateway.stop_workers()


async def test_abrupt_worker_exit_retries_without_recovery_confirmation(tmp_path):
    """A native-style process exit is bounded by the same durable retry counter."""
    gateway, job = make_gateway(tmp_path, failures=0)
    job["parameters"]["crash"] = True
    # This is an unstarted fixture; replace its accepted payload before any execution.
    with gateway.journal.connect() as db:
        db.execute("UPDATE jobs SET job=? WHERE id=?", (json.dumps(job), job["id"]))
    try:
        await advance(gateway, job, lambda row: row["state"] == "completed")
        assert gateway.executions.get(job["id"], job["attempt_id"])["executions"] == 2
        assert Path(job["parameters"]["counter"]).read_text() == "2"
        assert not gateway.executor.entries()
    finally:
        gateway.stop_workers()


@pytest.mark.parametrize("fatal", ["SystemExit", "KeyboardInterrupt"])
async def test_worker_base_exception_cannot_exit_controller(tmp_path, fatal):
    """User evaluator exit signals become bounded outcomes inside the worker."""
    gateway, job = make_gateway(tmp_path, failures=0)
    job["parameters"]["fatal"] = fatal
    with gateway.journal.connect() as db:
        db.execute("UPDATE jobs SET job=? WHERE id=?", (json.dumps(job), job["id"]))
    try:
        await advance(gateway, job, lambda row: row["state"] == "completed")
        assert gateway.executions.get(job["id"], job["attempt_id"])["executions"] == 2
        assert Path(job["parameters"]["counter"]).read_text() == "2"
    finally:
        gateway.stop_workers()


async def test_restart_retries_saved_local_checkpoint_automatically(tmp_path):
    """A restarted SDK needs no evaluator-specific recovery implementation."""
    first, job = make_gateway(tmp_path, failures=0, hang=True)
    second = None
    try:
        await advance(first, job, lambda row: bool(json.loads(row["saved"])))
        first.stop_workers()
        second = Gateway("http://localhost:9", "test", first.allowed_users, first.spec, first.root)
        await advance(second, job, lambda row: row["state"] == "completed")
        assert second.executions.get(job["id"], job["attempt_id"])["executions"] == 2
        assert Path(job["parameters"]["counter"]).read_text() == "2"
        assert not second.executor.entries()
        assert execution_control(second.journal, job["id"], job["attempt_id"])["paused_seconds"] >= 0
    finally:
        if second:
            second.stop_workers()
        else:
            first.stop_workers()


async def test_cleanup_failure_finishes_job_and_probes_resource_without_releasing_early(tmp_path):
    """A cleanup debt pauses its resource and remains automatically recoverable."""
    gateway, job = make_gateway(tmp_path, failures=0)
    gateway.adapter.cancel = AsyncMock(return_value=False)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            await gateway.reconcile(None, job)
            with gateway.journal.connect() as db:
                db.execute("UPDATE cleanup_debts SET next_at=0")
            row = gateway.journal.get(job["id"])
            if row["state"] == "failed":
                break
            await asyncio.sleep(0.03)
        assert row["state"] == "failed"
        assert json.loads(row["report"])["error_type"] == "ResourceCleanupError"
        assert gateway.executor.paused
        assert gateway.executor.entries()
        await gateway.reconcile(None, job)
        assert gateway.executor.entries()
        gateway.adapter.cancel = AsyncMock(return_value=True)
        await gateway.probe_cleanup()
        assert not gateway.executor.paused
        assert not gateway.executor.entries()
        assert gateway.journal.get(job["id"])["state"] == "failed"
    finally:
        gateway.stop_workers()


async def test_shared_resource_serializes_cleanup_and_holds_dispatch(tmp_path):
    """Two evaluator bindings cannot restore one device or admit work concurrently."""
    from llm4ad.gateway.sdk.scheduling import DurableExecutor

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    first, first_job = make_gateway(a, failures=0)
    second, second_job = make_gateway(b, failures=0)
    executor = DurableExecutor(tmp_path / "shared")
    for gateway in (first, second):
        gateway.executor, gateway.executor_owned = executor, False
    entered, release = asyncio.Event(), asyncio.Event()
    active, maximum = 0, 0

    async def cleanup(job, saved):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        entered.set()
        await release.wait()
        active -= 1
        return True

    first.adapter.cancel = second.adapter.cancel = cleanup
    tasks = []
    try:
        tasks.append(asyncio.create_task(first.request_cancel(first_job["id"], first_job["attempt_id"])))
        await asyncio.wait_for(entered.wait(), 3)
        tasks.append(asyncio.create_task(second.request_cancel(second_job["id"], second_job["attempt_id"])))
        await asyncio.sleep(0)
        canary = executor.submit("canary", len, args=([1, 2],))
        executor.poll()
        assert executor.paused and not executor.active_keys()
        release.set()
        results = await asyncio.gather(*tasks)
        assert all(item["status"] == "cancelled" for item in results)
        assert maximum == 1 and not executor.paused
        executor.poll()
        assert await asyncio.wait_for(asyncio.wrap_future(canary), 10) == 2
        executor.ack("canary")
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        first.stop_workers()
        second.stop_workers()
        executor.close()


async def test_generic_parallel_slots_finish_independently(tmp_path):
    """A local result must not wait for a different parallel slot's long evaluation."""
    gateway, fast = make_gateway(tmp_path, failures=0)
    gateway.capacity = 2
    slow = {
        **fast,
        "id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "parameters": {**fast["parameters"], "counter": str(tmp_path / "slow-calls"), "hang": True},
    }
    gateway.journal.accept(slow)
    try:
        gateway.start_process(fast)
        gateway.start_process(slow)
        deadline = time.monotonic() + 10
        while not Path(slow["parameters"]["counter"]).exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.03)
        gateway.capacity = gateway.executor.concurrency = 1
        await advance(gateway, fast, lambda row: row["state"] == "completed", timeout=5)
        assert gateway.processes[slow["id"]].is_alive()
        assert (await gateway.request_cancel(slow["id"], slow["attempt_id"]))["status"] == "cancelled"
    finally:
        gateway.stop_workers()


def test_local_generation_fences_every_reporter_write(tmp_path):
    """An old execution cannot overwrite progress, checkpoints, phases or evidence."""
    gateway, job = make_gateway(tmp_path)
    store = gateway.executions
    store.ensure(job, gateway.journal.get(job["id"]))
    first = store.begin(job)
    old = Reporter(gateway.journal, job["id"], job["attempt_id"], first["execution_id"])
    store.move(first, "retry")
    second = store.begin(first)
    current = Reporter(gateway.journal, job["id"], job["attempt_id"], second["execution_id"])
    current.progress("current")
    old.progress("stale")
    old.metadata({"stale": True})
    old.save_state({"stale": True})
    old.record_phase({"stage": "stale"})
    assert json.loads(gateway.journal.get(job["id"])["report"])["message"] == "current"
    assert current.saved_state == {}
    assert "stale" not in current.enrich({}).get("metadata", {})
    evidence = tmp_path / "old.txt"
    evidence.write_text("old")
    with pytest.raises(ValueError, match="no longer accepts"):
        old.artifact("old.txt", evidence)


def test_legacy_recovery_is_migrated_with_attempt_count(tmp_path):
    """A completed physical cleanup does not remain a manual recovery gate."""
    gateway, job = make_gateway(tmp_path)
    gateway.journal.update(job["id"], "recovery_required", {"message": "old unknown state"})
    record = Executions(gateway.journal).ensure(job, gateway.journal.get(job["id"]))
    assert record["state"] == "retrying"
    assert record["executions"] == 1
    assert RemoteJob.model_validate(job).execution_id == ""
    assert Journal(gateway.journal.path).get(job["id"])["state"] == "recovery_required"
