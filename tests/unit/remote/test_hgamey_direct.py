"""Verify direct assigned-worker execution and automatic restoration without phones."""

import copy
import os
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from llm4ad.evaluator.models import EvaluationResult
from llm4ad.gateway.examples.hgamey.benchmark import Benchmark
from llm4ad.gateway.examples.hgamey.evaluator import HGameyShaderEvaluator
from llm4ad.gateway.examples.hgamey.execution import jobs
from llm4ad.gateway.examples.hgamey.execution.jobs import JobStore, execute_job, restore_job
from llm4ad.gateway.examples.hgamey.execution.retry import retry_read
from llm4ad.gateway.examples.hgamey.execution.storage import read_json, write_json
from llm4ad.gateway.sdk.errors import EvaluationUnavailableError

pytestmark = [pytest.mark.unit, pytest.mark.skipif(os.name == "nt", reason="Native phonefarm requires POSIX")]


class Reporter:
    """Retain the same submission state shape used by SDK workers."""

    def __init__(self):
        """Create an empty durable-state-shaped observation fixture."""
        self.saved_state, self.messages = {}, []

    def save_state(self, value):
        """Snapshot intent before a native command may execute."""
        self.saved_state = copy.deepcopy(value)

    def progress(self, message, **kwargs):
        """Record a real workflow boundary without invented measurements."""
        self.messages.append(message)


class ReceiptExecutor:
    """Retain public SDK packets without creating a worker or touching a phone."""

    def __init__(self, directory, concurrency):
        """Create one manually completed SDK resource slot."""
        assert concurrency == 1
        self.packets, self.futures, self.acked = {}, {}, []
        self.paused, self.holds, self.polls = False, set(), 0

    def submit(self, key, target, args=(), kwargs=None):
        """Record the exact importable callable and JSON arguments."""
        self.packets[key] = {
            "target": target.__module__ + ":" + target.__qualname__,
            "args": list(args),
            "kwargs": kwargs or {},
            "callable": target,
        }
        return self.futures.setdefault(key, Future())

    def poll(self):
        """Observe dispatch requests while keeping fake work deterministic."""
        self.polls += 1

    def hold(self, reason):
        """Keep restoration ownership independent of the normal admission flag."""
        self.holds.add(reason)

    def release(self, reason):
        """Model the SDK service's explicit restoration fence release."""
        self.holds.discard(reason)

    def entries(self):
        """Return the packets still owned by their original lifecycle."""
        return dict(self.packets)

    def future(self, key):
        """Read the current generation without replacing its Future."""
        return self.futures.get(key)

    def ack(self, key):
        """Release only a durably reconciled receipt."""
        self.acked.append(key)
        self.packets.pop(key, None)
        self.futures.pop(key, None)

    def close(self):
        """Leave fake packets unacknowledged."""


@pytest.fixture
def direct(tmp_path):
    """Configure one synthetic physical identity and a metadata-only store."""
    config = tmp_path / "devices.json"
    write_json(
        config,
        {
            "version": 1,
            "devices": [
                {
                    "id": "phone",
                    "label": "Phone",
                    "host": "192.0.2.1",
                    "port": 5555,
                    "expected_serial": "physical-phone",
                    "enabled": True,
                }
            ],
        },
    )
    store = JobStore(tmp_path / "executor", config)
    stage = tmp_path / "stage"
    stage.mkdir()
    state = {
        "idempotency_key": "a" * 64,
        "request": {"idempotency_key": "a" * 64, "device_id": "phone", "operation": "probe"},
        "evidence_directory": str(stage),
        "remote_job_id": "remote",
        "remote_attempt_id": "attempt",
    }
    adapter = object.__new__(HGameyShaderEvaluator)
    adapter.benchmark = SimpleNamespace(
        settings={"execution_backend": "direct", "executor_data": str(store.data), "executor_config": str(config)},
        game_check=None,
    )
    return SimpleNamespace(store=store, state=state, adapter=adapter, config=config, stage=stage)


@pytest.mark.asyncio
async def test_integrated_native_execution_stays_in_assigned_worker_without_http_or_engine(direct, monkeypatch):
    """The native runner executes in this process and idempotent recovery does not replay."""
    calls = []

    def runner(folder):
        calls.append(os.getpid())
        assert direct.reporter.saved_state["external_job_id"] == folder.name
        write_json(folder / "result.json", {"status": "PASS", "fixture_pid": os.getpid()})
        return 0

    monkeypatch.setattr(jobs, "run_backend", runner)
    direct.adapter.rpc = AsyncMock(side_effect=AssertionError("integrated execution used HTTP"))
    direct.reporter = Reporter()
    first = await direct.adapter.follow_direct(direct.state, direct.reporter)
    second = await direct.adapter.follow_direct(direct.state, direct.reporter)
    assert first == second == {"status": "PASS", "fixture_pid": os.getpid()}
    assert calls == [os.getpid()]
    assert direct.store.get(direct.state["external_job_id"])["dispatch"] == "direct"
    assert not (direct.store.data / "scheduling").exists()
    assert await direct.adapter.cancel(SimpleNamespace(id="remote", attempt_id="attempt"), direct.state)
    assert calls == [os.getpid()]


def test_semantic_candidate_failure_is_a_result_and_infrastructure_is_retryable(direct):
    """Infrastructure absence cannot become an invented candidate fitness value."""
    candidate = EvaluationResult(success=False, metrics={"psnr_db": 12}, error_message="Poor quality")
    assert direct.adapter.require_result(candidate, direct.state) is candidate
    for result in (
        None,
        EvaluationResult(success=False, metrics={}, metadata={"infrastructure_failure": True}, error_message="ADB offline"),
    ):
        with pytest.raises(EvaluationUnavailableError):
            direct.adapter.require_result(result, direct.state)


def test_local_execution_identity_changes_native_key_without_changing_server_attempt():
    """A fresh local retry retains external provenance and receives distinct native paths."""
    benchmark = object.__new__(Benchmark)
    benchmark.version, benchmark.evaluation_profiles = "frozen", None
    first = SimpleNamespace(id="job", attempt_id="attempt", execution_id="execution-1")
    second = SimpleNamespace(id="job", attempt_id="attempt", execution_id="execution-2")
    assert benchmark.attempt_key(first) != benchmark.attempt_key(second)
    assert first.id == second.id and first.attempt_id == second.attempt_id


def test_retry_reads_are_three_attempts_and_share_remaining_deadline():
    """Offline reads recover, while an expired original budget prevents another read."""
    now, limits, sleeps = [0.0], [], []

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    def read(remaining):
        limits.append(remaining)
        if len(limits) < 3:
            raise RuntimeError("ADB: device offline")
        return "actual read"

    assert retry_read(read, 10, clock=lambda: now[0], sleep=sleep) == "actual read"
    assert sleeps == [1, 2] and limits == [10, 9, 7]
    limits.clear()

    def expired(remaining):
        limits.append(remaining)
        now[0] += remaining
        raise TimeoutError("read timeout")

    with pytest.raises(TimeoutError):
        retry_read(expired, 1, clock=lambda: now[0], sleep=sleep)
    assert limits == [1]


def test_unstarted_submission_can_be_cancelled_without_native_commands(direct, monkeypatch):
    """Cancellation of a queued operation proves a no-op rather than touching a phone."""
    from llm4ad.gateway.examples.hgamey.execution import recovery

    item = direct.store.prepare(direct.state["request"], direct=True)
    monkeypatch.setattr(recovery, "command", lambda *a, **k: pytest.fail("queued cancellation launched native code"))
    assert restore_job(direct.store.jobs / item["id"])
    assert direct.store.get(item["id"])["cleanup_status"] == "completed"


def test_restored_abort_is_cleanup_complete_without_repeating_native_recovery(direct, monkeypatch):
    """An ABORT with truthful restoration is immediately safe for the SDK retry hook."""
    from llm4ad.gateway.examples.hgamey.execution import recovery

    item = direct.store.prepare(direct.state["request"], direct=True)
    folder = direct.store.jobs / item["id"]
    item.update(operation="eval", state="failed", cleanup_status="completed")
    write_json(folder / "job.json", item)
    write_json(folder / "result.json", {"status": "ABORT", "conditions_observed": {"restored": True}})
    write_json(folder / "foreground.json", {"restore_required": False, "headless": True, "paused": False})
    monkeypatch.setattr(recovery, "command", lambda *a, **k: pytest.fail("restored ABORT repeated physical cleanup"))
    assert restore_job(folder)
    assert read_json(folder / "result.json")["status"] == "ABORT"


def test_private_engine_delegates_to_sdk_executor_without_threads(direct):
    """Standalone compatibility uses the SDK's importable durable packet API."""
    from llm4ad.gateway.examples.hgamey.execution.queue import Engine

    engine = Engine(direct.store.data, direct.config, executor_factory=ReceiptExecutor)
    try:
        item = engine.submit(direct.state["request"])
        packet = engine.executors["phone"].packets[item["id"]]
        assert packet["callable"] is execute_job
        assert packet["args"] == [str(engine.jobs / item["id"])]
        assert not hasattr(engine, "threads") and not hasattr(engine, "pending")
    finally:
        engine.close()


@pytest.mark.parametrize("failure", ["read", "ack", "write"])
def test_poll_revisits_finished_receipts_after_callback_failure(direct, monkeypatch, failure):
    """A callback error never makes an observed Future invisible to future polls."""
    from llm4ad.gateway.examples.hgamey.execution import queue

    engine = queue.Engine(direct.store.data, direct.config, executor_factory=ReceiptExecutor)
    try:
        item = engine.submit(direct.state["request"])
        pool, key = engine.executors["phone"], item["id"]
        item.update(state="completed", cleanup_status="completed")
        write_json(engine.jobs / key / "job.json", item)
        get, ack, write = engine.store.get, pool.ack, queue.write_json
        broken = [True]

        def read(key):
            if failure == "read" and broken[0]:
                raise OSError("temporary receipt read failure")
            return get(key)

        def acknowledge(key):
            if failure == "ack" and broken[0]:
                raise OSError("temporary receipt ack failure")
            return ack(key)

        def persist(path, value):
            if failure in {"read", "write"} and broken[0]:
                raise OSError("temporary receipt write failure")
            return write(path, value)

        monkeypatch.setattr(engine.store, "get", read)
        monkeypatch.setattr(pool, "ack", acknowledge)
        monkeypatch.setattr(queue, "write_json", persist)
        future = pool.future(key)
        if failure == "write":
            future.set_exception(RuntimeError("worker stopped"))
        else:
            future.set_result(item)
        assert key in pool.entries() and key in engine.errors
        broken[0] = False
        engine.poll()
        assert key not in pool.entries() and key not in engine.errors
        assert key in pool.acked
    finally:
        engine.close()


def test_exhausted_receipt_reads_save_explicit_failure_and_schedule_cleanup(direct, monkeypatch):
    """Failed metadata reads cannot silently consume completion or fabricate results."""
    from llm4ad.gateway.examples.hgamey.execution.queue import Engine

    engine = Engine(direct.store.data, direct.config, executor_factory=ReceiptExecutor)
    try:
        item = engine.submit(direct.state["request"])
        key, pool = item["id"], engine.executors["phone"]
        monkeypatch.setattr(engine.store, "get", lambda key: (_ for _ in ()).throw(OSError("disk read failed")))
        pool.future(key).set_result(None)
        committed = read_json(engine.jobs / key / "job.json")
        assert committed["state"] == "failed" and committed["error_type"] == "NativeReceiptFailure"
        assert committed["cleanup_status"] == "uncertain"
        assert key in pool.acked and "restore-" + key in pool.entries()
        assert not (engine.jobs / key / "result.json").exists()
    finally:
        engine.close()


def test_three_cleanup_failures_are_terminal_and_automatic_probes_continue(direct, monkeypatch):
    """Only this phone stays unavailable; cleanup resumes after the bounded attempt limit."""
    from llm4ad.gateway.examples.hgamey.execution import queue

    now = [100.0]
    monkeypatch.setattr(queue.time, "time", lambda: now[0])
    engine = queue.Engine(direct.store.data, direct.config, executor_factory=ReceiptExecutor)
    try:
        item = engine.submit(direct.state["request"])
        key, pool = item["id"], engine.executors["phone"]
        item.update(state="uncertain", cleanup_status="uncertain")
        write_json(engine.jobs / key / "job.json", item)
        pool.future(key).set_result(item)
        for attempt in range(1, 4):
            assert read_json(engine.jobs / key / "job.json")["cleanup_attempts"] == attempt
            pool.future("restore-" + key).set_result(False)
            engine.poll()
        terminal = read_json(engine.jobs / key / "job.json")
        assert terminal["state"] == "failed" and terminal["error_type"] == "NativeCleanupExhausted"
        assert not pool.entries()
        assert engine.health()["devices"]["phone"]["status"] == "recovering"
        now[0] += 5
        engine.poll()
        assert "restore-" + key in pool.entries()
        assert read_json(engine.jobs / key / "job.json")["cleanup_probes"] == 1
        terminal.update(state="cancelled", cleanup_status="completed")
        write_json(engine.jobs / key / "job.json", terminal)
        pool.future("restore-" + key).set_result(True)
        assert engine.health()["devices"]["phone"]["status"] == "ok"
    finally:
        engine.close()


def test_recovered_gateway_packets_hold_dispatch_until_the_sdk_service_releases(direct):
    """Filtering completion callbacks also fences foreign work before any dispatch."""
    from llm4ad.gateway.examples.hgamey.execution.queue import Engine

    class RecoveredExecutor(ReceiptExecutor):
        def __init__(self, directory, concurrency):
            super().__init__(directory, concurrency)
            self.packets["gateway-job"] = {"target": "llm4ad.gateway.sdk.gateway:execute"}
            self.futures["gateway-job"] = Future()

        def poll(self):
            assert "sdk-restoration" in self.holds
            super().poll()

    engine = Engine(direct.store.data, direct.config, executor_factory=RecoveredExecutor)
    try:
        pool = engine.executors["phone"]
        pool.future("gateway-job").set_result(None)
        engine.poll()
        assert "gateway-job" in pool.entries() and not pool.acked
        assert "sdk-restoration" in pool.holds
    finally:
        engine.close()


def test_integrated_health_polling_cannot_dispatch_before_all_sdk_bindings_restore(direct):
    """Health reads cannot bypass the service's cross-binding restoration fence."""
    from llm4ad.gateway.examples.hgamey.execution.queue import Engine

    engine = Engine(direct.store.data, direct.config, executor_factory=ReceiptExecutor, defer_dispatch=True)
    try:
        pool = engine.executors["phone"]
        engine.submit(direct.state["request"])
        engine.health()
        assert pool.polls == 0 and "sdk-restoration" in pool.holds
        pool.release("sdk-restoration")
        pool.paused = True
        engine.activate()
        assert pool.polls > 0 and pool.paused is True
    finally:
        engine.close()


@pytest.mark.asyncio
async def test_cleanup_accepts_persisted_native_identity_after_benchmark_version_changes(direct, monkeypatch):
    """An upgrade may alter the profile hash without invalidating old cleanup ownership."""
    item = direct.store.prepare(direct.state["request"], direct=True)
    direct.state.update(external_job_id=item["id"], data_version="old-version", request_sha256="old-profile")
    direct.adapter.benchmark.version = "new-version"
    monkeypatch.setattr(direct.adapter, "validate_frozen", lambda *a: pytest.fail("cleanup validated current profile"))
    assert await direct.adapter.cancel(SimpleNamespace(id="remote", attempt_id="attempt"), direct.state)


def test_changed_boot_refuses_recovery_before_any_mutation(direct, monkeypatch):
    """Fresh identity reads cannot authorize restoration across a device reboot."""
    from llm4ad.gateway.examples.hgamey.execution import recovery

    item = direct.store.prepare(direct.state["request"], direct=True)
    folder = direct.store.jobs / item["id"]
    write_json(folder / "connection.json", {"boot_id": "original-boot"})
    monkeypatch.setattr(recovery, "inherited_fds", lambda: (123,))

    def adb(*args, **kwargs):
        if "ro.serialno" in args:
            return "physical-phone"
        if "/proc/sys/kernel/random/boot_id" in args:
            return "new-boot"
        assert args[0] == "connect" or "wait-for-device" in args
        return ""

    monkeypatch.setattr(recovery, "adb", adb)
    monkeypatch.setattr(recovery, "command", lambda *a, **k: pytest.fail("changed boot caused a write"))
    with pytest.raises(RuntimeError, match="boot changed"):
        recovery.restore_on_device(Path(folder))


@pytest.mark.parametrize(
    "state,status",
    [
        ("uncertain", "uncertain"),
        ("running", "pending"),
        ("cancel_requested", "pending"),
        ("interrupted", "pending"),
    ],
)
def test_new_native_work_reconciles_prior_same_device_cleanup_before_running(direct, monkeypatch, state, status):
    """A queued private packet cannot overtake its device's restoration obligation."""
    previous = direct.store.prepare(direct.state["request"], direct=True)
    previous.update(state=state, cleanup_status=status)
    write_json(direct.store.jobs / previous["id"] / "job.json", previous)
    following = direct.store.prepare({**direct.state["request"], "idempotency_key": "b" * 64}, direct=True)
    restored = []
    monkeypatch.setattr(jobs, "restore_job", lambda folder: restored.append(folder.name) or False)
    result = execute_job(direct.store.jobs / following["id"], runner=lambda folder: pytest.fail("unclean device was used"))
    assert restored == [previous["id"]]
    assert result["state"] == "failed" and result["cleanup_status"] == "completed"
    assert read_json(direct.store.jobs / following["id"] / "result.json")["preparation"]["device_started"] is False


@pytest.mark.asyncio
async def test_recovered_startup_receipt_requires_cleanup_before_ack_or_dispatch(direct):
    """SDK restoration cannot accidentally replay HGamey's private readiness packet."""
    from llm4ad.gateway.examples.hgamey.runtime import HGameyRuntime, run_startup_operation
    from llm4ad.gateway.sdk.contracts import RemoteJob
    from llm4ad.gateway.sdk.journal import Journal
    from llm4ad.gateway.sdk.journal import Reporter as SDKReporter

    root = direct.store.data.parent
    journal = Journal(root / "startup.sqlite3")
    job = RemoteJob(id="startup", attempt_id="attempt", task_id="task", project_root=str(root), timeout=30)
    journal.accept(job.model_dump(mode="json"))
    reporter = SDKReporter(journal, job.id, job.attempt_id)
    reporter.save_state({"original_native_key": "a" * 64})
    pool = ReceiptExecutor(root, 1)
    pool.cancel = lambda key: True
    pool.submit(
        "startup-receipt",
        run_startup_operation,
        args=("settings.json", None, job.model_dump(mode="json"), str(journal.path), "evaluate", 1, 1),
    )
    runtime = object.__new__(HGameyRuntime)
    runtime.engine = SimpleNamespace(executors={"phone": pool})
    runtime.options = {"root": root}
    adapter = SimpleNamespace(cancel=AsyncMock(return_value=False))
    with pytest.raises(RuntimeError, match="restoration remains unconfirmed"):
        await runtime.restore_startup_packets(adapter, "phone")
    assert "startup-receipt" in pool.entries() and "hgamey-startup-restoration" in pool.holds
    assert pool.polls == 0
    adapter.cancel.return_value = True
    await runtime.restore_startup_packets(adapter, "phone")
    assert not pool.entries() and "hgamey-startup-restoration" not in pool.holds
    assert journal.get(job.id)["state"] == "cancelled"
    adapter.cancel.assert_awaited_with(job, {"original_native_key": "a" * 64})
