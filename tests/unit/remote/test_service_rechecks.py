"""Exercise SDK readiness recovery without devices or platform connections."""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from llm4ad.gateway.sdk.gateway import Gateway
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.runtime import GatewayBinding, GatewayRuntime
from llm4ad.gateway.sdk.service import GatewayService

pytestmark = pytest.mark.unit


class LocalGateway:
    """Record transport and admission state without submitting any work."""

    def __init__(self, server, name, users, evaluator, directory, capacity):
        """Create only local state needed by the common service."""
        self.root = directory
        self.root.mkdir(parents=True, exist_ok=True)
        self.paused = False
        self.started = asyncio.Event()

    def pause_accepting(self):
        """Fence new work while preserving the transport."""
        self.paused = True

    def resume_accepting(self):
        """Allow new work after readiness is verified."""
        self.paused = False

    async def run(self):
        """Remain alive until the owning service cancels the transport."""
        self.started.set()
        await asyncio.Event().wait()


class LocalRuntime(GatewayRuntime):
    """Expose independent resources with controllable readiness checks."""

    def __init__(self, root, keys=("a", "b")):
        """Keep all service state inside the test's temporary directory."""
        super().__init__(
            {
                "LLM4AD_SERVER_URL": "https://fixture.invalid",
                "LLM4AD_GATEWAY_NAME": "service-rechecks",
                "LLM4AD_ALLOWED_USER_IDS": str(uuid.uuid4()),
                "LLM4AD_GATEWAY_STATE_DIR": str(root),
                "LLM4AD_GATEWAY_CLEANUP_ENABLED": "false",
            },
            "llm4ad.gateway.examples.basic.evaluator:Evaluator",
        )
        self.keys = keys
        self.available = dict.fromkeys(keys, True)
        self.calls = dict.fromkeys(keys, 0)
        self.entered = {key: asyncio.Event() for key in keys}
        self.release = {key: asyncio.Event() for key in keys}
        for event in self.release.values():
            event.set()

    def bindings(self):
        """Give every resource a separate local journal directory."""
        return [
            GatewayBinding(key, key, self.evaluator, self.options["root"] / key)
            for key in self.keys
        ]

    def binding_ready(self, binding):
        """Report resource availability without performing restoration."""
        return self.available[binding.key]

    async def check(self, gateway):
        """Fail unavailable resources and let tests hold verification in flight."""
        key = gateway.root.name
        self.calls[key] += 1
        self.entered[key].set()
        if not self.available[key]:
            raise RuntimeError("Restoration remains unconfirmed")
        await self.release[key].wait()


async def until(predicate):
    """Wait for an observable service transition with a finite deadline."""
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_recovered_binding_is_rechecked_before_admission(tmp_path):
    """A recovery observation triggers verification without rechecking healthy peers."""
    runtime = LocalRuntime(tmp_path)
    runtime.available["a"] = False
    service = GatewayService(runtime, LocalGateway)
    task = asyncio.create_task(service.run())
    try:
        await until(lambda: service.readiness == "ready")
        assert service.gateways["a"].paused and not service.gateways["b"].paused
        runtime.entered["a"].clear()
        runtime.release["a"].clear()
        runtime.available["a"] = True
        await asyncio.wait_for(runtime.entered["a"].wait(), 3)
        assert service.gateways["a"].paused and not service.gateways["b"].paused
        assert service.binding_status["a"] == "checking"
        service.set_admission(True)
        runtime.release["a"].set()
        await until(lambda: service.binding_status["a"] == "ready")
        assert all(gateway.paused for gateway in service.gateways.values())
        assert "a" not in service.binding_errors
        service.set_admission(False)
        assert not any(gateway.paused for gateway in service.gateways.values())
        assert runtime.calls == {"a": 2, "b": 1}
    finally:
        service.stop.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_all_failed_bindings_can_recover_without_a_service_restart(tmp_path):
    """An unavailable service retains ownership until readiness can be verified."""
    runtime = LocalRuntime(tmp_path, keys=("a",))
    runtime.available["a"] = False
    service = GatewayService(runtime, LocalGateway)
    task = asyncio.create_task(service.run())
    try:
        await until(lambda: service.binding_status.get("a") == "unready")
        await until(lambda: service.startup_task.done())
        assert not task.done()
        assert not service.health()["ready"]
        runtime.available["a"] = True
        await until(lambda: service.readiness == "ready")
        await asyncio.wait_for(service.gateways["a"].started.wait(), 3)
        assert not service.gateways["a"].paused
        assert service.error is None
        assert runtime.calls == {"a": 2}
    finally:
        service.stop.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_slow_independent_check_does_not_block_a_verified_binding(tmp_path):
    """One independent transport becomes usable while another check is pending."""
    runtime = LocalRuntime(tmp_path)
    runtime.release["b"].clear()
    service = GatewayService(runtime, LocalGateway)
    task = asyncio.create_task(service.run())
    try:
        await asyncio.wait_for(runtime.entered["b"].wait(), 3)
        await until(lambda: service.readiness == "ready")
        await asyncio.wait_for(service.gateways["a"].started.wait(), 3)
        assert not service.gateways["a"].paused
        assert service.gateways["b"].paused
        assert service.binding_status["b"] == "checking"
    finally:
        service.stop.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_health_is_unready_when_every_resource_is_blocked(tmp_path):
    """Running transports do not prove that any binding can accept new work."""
    runtime = LocalRuntime(tmp_path)
    service = GatewayService(runtime, LocalGateway)
    task = asyncio.create_task(service.run())
    try:
        await until(lambda: service.readiness == "ready")
        await until(lambda: all(gateway.started.is_set() for gateway in service.gateways.values()))
        assert service.health()["ready"]
        runtime.available.update(a=False, b=False)
        service.sync_admission()
        assert not service.health()["ready"]
        runtime.available["b"] = True
        service.sync_admission()
        assert service.health()["ready"]
        assert service.gateways["a"].paused and not service.gateways["b"].paused
    finally:
        service.stop.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_failed_verification_is_not_repeated_without_an_availability_transition(tmp_path):
    """A permanently failed baseline cannot turn the service loop into a work retry."""
    runtime = LocalRuntime(tmp_path)
    original_check = runtime.check
    ticks = 0

    async def check(gateway):
        """Fail one available resource without claiming it needs restoration."""
        await original_check(gateway)
        if gateway.root.name == "a":
            raise RuntimeError("Baseline verification failed")

    def alive():
        """Count service observations without changing backend availability."""
        nonlocal ticks
        ticks += 1
        return True

    runtime.check, runtime.alive = check, alive
    service = GatewayService(runtime, LocalGateway)
    task = asyncio.create_task(service.run())
    try:
        await until(lambda: ticks >= 4)
        assert runtime.calls == {"a": 1, "b": 1}
        assert service.gateways["a"].paused and not service.gateways["b"].paused
        runtime.available["a"] = False
        await until(lambda: service.binding_available.get("a") is False)
        runtime.available["a"] = True
        await until(lambda: runtime.calls["a"] == 2)
        await until(lambda: service.startup_task.done())
        last_tick = ticks
        await until(lambda: ticks >= last_tick + 2)
        assert runtime.calls == {"a": 2, "b": 1}
        assert service.gateways["a"].paused
        assert service.binding_errors["a"] == "Baseline verification failed"
    finally:
        service.stop.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_shared_resource_stays_paused_during_a_sibling_recheck(tmp_path):
    """A readiness operation fences every evaluator using its physical resource."""
    runtime = LocalRuntime(tmp_path)
    original_bindings = runtime.bindings

    def bindings():
        """Put both evaluator modes on the same resource queue."""
        values = original_bindings()
        for binding in values:
            binding.resource_key = "shared"
        return values

    runtime.bindings = bindings
    runtime.available["b"] = False
    service = GatewayService(runtime, LocalGateway)
    task = asyncio.create_task(service.run())
    try:
        await until(lambda: service.readiness == "ready")
        await until(lambda: all(gateway.started.is_set() for gateway in service.gateways.values()))
        assert not service.gateways["a"].paused and service.gateways["b"].paused
        runtime.release["b"].clear()
        runtime.entered["b"].clear()
        runtime.available["b"] = True
        await asyncio.wait_for(runtime.entered["b"].wait(), 3)
        assert all(gateway.paused for gateway in service.gateways.values())
        assert not service.health()["ready"]
        runtime.release["b"].set()
        await until(lambda: service.binding_status["b"] == "ready")
        assert not any(gateway.paused for gateway in service.gateways.values())
        assert runtime.calls == {"a": 1, "b": 2}
    finally:
        service.stop.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_shared_startup_checks_finish_before_their_transports_start(tmp_path):
    """A completed independent group cannot admit work into another group's checks."""
    runtime = LocalRuntime(tmp_path, keys=("a", "b", "c"))
    original_bindings = runtime.bindings

    def bindings():
        """Share the first two evaluator modes while leaving the third independent."""
        values = original_bindings()
        for binding in values[:2]:
            binding.resource_key = "shared"
        return values

    runtime.bindings = bindings
    runtime.release["b"].clear()
    service = GatewayService(runtime, LocalGateway)
    task = asyncio.create_task(service.run())
    try:
        await asyncio.wait_for(runtime.entered["b"].wait(), 3)
        await until(lambda: service.readiness == "ready")
        await asyncio.wait_for(service.gateways["c"].started.wait(), 3)
        assert not service.gateways["a"].started.is_set()
        assert not service.gateways["b"].started.is_set()
        assert service.gateways["a"].paused and service.gateways["b"].paused
        assert not service.gateways["c"].paused
        assert service.health()["ready"]
        runtime.release["b"].set()
        await until(lambda: all(gateway.started.is_set() for gateway in service.gateways.values()))
        assert not any(gateway.paused for gateway in service.gateways.values())
        assert runtime.calls == {"a": 1, "b": 1, "c": 1}
    finally:
        service.stop.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancellation"])
async def test_interrupted_check_preserves_receipt_and_unready_admission(tmp_path, cancel):
    """Interruptions retain startup ownership and never imply verified restoration."""
    runtime = LocalRuntime(tmp_path, keys=("a",))
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    (tmp_path / "a").mkdir()
    journal = Journal(tmp_path / "a" / "startup.sqlite3")
    job = {
        "id": "startup",
        "attempt_id": "attempt",
        "task_id": "task",
        "project_root": str(tmp_path),
        "timeout": 60,
    }

    async def check(gateway):
        """Publish an external receipt and wait for the SDK's cancellation."""
        journal.accept(job)
        Reporter(journal, job["id"], job["attempt_id"]).save_state(
            {"external_id": "unverified-receipt"}
        )
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    runtime.check = check
    if not cancel:
        runtime.options["startup_timeout"] = 0.01
    service = GatewayService(runtime, LocalGateway)
    service.bound = runtime.bindings()
    service.gateways = {
        binding.key: LocalGateway(None, None, None, None, binding.directory, 1)
        for binding in service.bound
    }
    task = asyncio.create_task(service.check_bindings())
    await asyncio.wait_for(entered.wait(), 3)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await asyncio.wait_for(task, 3)
    assert cancelled.is_set()
    assert service.binding_status["a"] == "unready"
    assert service.binding_errors["a"]
    assert service.gateways["a"].paused
    row = journal.get(job["id"])
    assert json.loads(row["saved"]) == {"external_id": "unverified-receipt"}
    assert row["state"] not in {"completed", "failed", "cancelled"}
    assert row["ack"] < row["seq"]
    assert len(journal.rows()) == 1


class CleanupExecutor:
    """Retain a synthetic cleanup debt without creating workers or device handles."""

    def __init__(self):
        """Start blocked until a test permits restoration."""
        self.cleanup_debts = {"owned-attempt"}
        self.restorable = False
        self.entered = asyncio.Event()
        self.probe_release = asyncio.Event()
        self.probe_release.set()
        self.active_probes = 0
        self.maximum_probes = 0
        self.calls = 0

    def release(self, reason):
        """Accept the service's initial restoration fence release."""

    def close(self):
        """Close without creating any operating system resources."""


class CleanupGateway(LocalGateway, Gateway):
    """Exercise service ownership with synthetic probes instead of a live gateway."""

    def restore_pending(self):
        """Leave the test's preexisting cleanup debt intact."""

    async def probe_cleanup(self):
        """Restore the debt only after the test supplies a positive acknowledgement."""
        executor = self.executor
        if not executor.cleanup_debts:
            return
        executor.calls += 1
        executor.active_probes += 1
        executor.maximum_probes = max(executor.maximum_probes, executor.active_probes)
        try:
            if executor.calls > 1:
                executor.entered.set()
                await executor.probe_release.wait()
            if executor.restorable:
                executor.cleanup_debts.clear()
        finally:
            executor.active_probes -= 1

    async def run(self):
        """Exercise probe handoff without contacting a platform or starting workers."""
        await self.probe_cleanup()
        await super().run()


@pytest.mark.asyncio
async def test_cleanup_debt_recovers_before_any_transport_can_start(tmp_path, monkeypatch):
    """Retry restoration after every binding fails its initial readiness check."""
    runtime = LocalRuntime(tmp_path, keys=("a",))
    executor = CleanupExecutor()
    runtime.executor_for = lambda binding: executor
    runtime.binding_ready = lambda binding: not executor.cleanup_debts

    async def check(gateway):
        """Require verified cleanup before accepting the resource."""
        runtime.calls["a"] += 1
        if executor.cleanup_debts:
            raise RuntimeError("Restoration remains unconfirmed")

    runtime.check = check
    monkeypatch.setattr(
        "llm4ad.gateway.sdk.service.HealthServer", lambda *args, **kwargs: SimpleNamespace(close=lambda: None)
    )
    service = GatewayService(runtime, CleanupGateway)
    task = asyncio.create_task(service.run())
    try:
        await until(lambda: service.binding_status.get("a") == "unready")
        assert not service.gateway_tasks
        executor.restorable = True
        await until(lambda: service.readiness == "ready")
        await asyncio.wait_for(service.gateways["a"].started.wait(), 3)
        assert not executor.cleanup_debts
        assert executor.calls >= 2
        assert executor.maximum_probes == 1
        assert runtime.calls == {"a": 2}
    finally:
        service.stop.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_startup_probe_handoff_waits_without_blocking_another_resource(tmp_path, monkeypatch):
    """One slow restoration neither overlaps its transport probe nor blocks a peer."""
    runtime = LocalRuntime(tmp_path)
    executors = {key: CleanupExecutor() for key in runtime.keys}
    executors["b"].cleanup_debts.clear()
    executors["a"].probe_release.clear()
    runtime.executor_for = lambda binding: executors[binding.key]
    runtime.binding_ready = lambda binding: not executors[binding.key].cleanup_debts

    async def check(gateway):
        """Reject only the resource that still has an unconfirmed debt."""
        key = gateway.root.name
        runtime.calls[key] += 1
        if executors[key].cleanup_debts:
            raise RuntimeError("Restoration remains unconfirmed")

    runtime.check = check
    monkeypatch.setattr(
        "llm4ad.gateway.sdk.service.HealthServer", lambda *args, **kwargs: SimpleNamespace(close=lambda: None)
    )
    service = GatewayService(runtime, CleanupGateway)
    task = asyncio.create_task(service.run())
    try:
        await asyncio.wait_for(executors["a"].entered.wait(), 3)
        await asyncio.wait_for(service.gateways["b"].started.wait(), 3)
        assert "a" not in service.gateway_tasks
        assert not service.gateways["a"].started.is_set()
        assert executors["a"].calls == 2
        assert executors["a"].maximum_probes == 1
        executors["a"].restorable = True
        executors["a"].probe_release.set()
        await asyncio.wait_for(service.gateways["a"].started.wait(), 3)
        assert executors["a"].maximum_probes == 1
        assert not executors["a"].cleanup_debts
    finally:
        executors["a"].probe_release.set()
        service.stop.set()
        await asyncio.wait_for(task, 5)
