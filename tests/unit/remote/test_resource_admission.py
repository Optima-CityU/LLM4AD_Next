"""Independent backend resources retain admission and recovery isolation."""

import asyncio
import uuid

import pytest

from llm4ad.gateway.sdk.runtime import GatewayBinding, GatewayRuntime
from llm4ad.gateway.sdk.service import GatewayService

pytestmark = pytest.mark.unit


class ResourceGateway:
    """Record admission without launching platform jobs or device operations."""

    def __init__(self, server, name, users, evaluator, directory, capacity):
        """Retain only the service resources needed by this lifecycle fixture."""
        self.root = directory
        self.root.mkdir(parents=True, exist_ok=True)
        self.closed = False
        self.paused = False

    def pause_accepting(self):
        """Pause new claims without stopping an accepted assignment."""
        self.paused = True

    def resume_accepting(self):
        """Permit new claims on this resource."""
        self.paused = False

    async def run(self):
        """Keep reconciliation alive while admission is paused."""
        try:
            await asyncio.Event().wait()
        finally:
            self.closed = True


class ResourceRuntime(GatewayRuntime):
    """Represent two independently quarantinable physical resources."""

    blocked = False

    def bindings(self):
        """Expose separate journals and independent resource identities."""
        return [GatewayBinding(key, key, self.evaluator, self.options["root"] / key) for key in ("a", "b")]

    def binding_ready(self, binding):
        """Keep the second resource available when the first is quarantined."""
        return not (binding.key == "a" and self.blocked)


def runtime(tmp_path, runtime_type=ResourceRuntime):
    """Configure an isolated service without network credentials."""
    return runtime_type(
        {
            "LLM4AD_SERVER_URL": "https://fixture.invalid",
            "LLM4AD_GATEWAY_NAME": "resource-fixture",
            "LLM4AD_ALLOWED_USER_IDS": str(uuid.uuid4()),
            "LLM4AD_GATEWAY_STATE_DIR": str(tmp_path),
        },
        "llm4ad.gateway.examples.basic.evaluator:Evaluator",
    )


@pytest.mark.asyncio
async def test_quarantine_and_operator_pause_do_not_interrupt_other_resources(tmp_path):
    """Only failed resources pause; operator resume cannot bypass their quarantine."""
    backend = runtime(tmp_path)
    service = GatewayService(backend, ResourceGateway)
    task = asyncio.create_task(service.run())
    try:
        async with asyncio.timeout(5):
            while service.readiness != "ready":
                await asyncio.sleep(0)
        backend.blocked = True
        service.sync_admission()
        assert service.gateways["a"].paused
        assert not service.gateways["b"].paused
        assert service.health()["gateways"]["a"]["status"] == "unready"
        service.set_admission(True)
        assert all(gateway.paused for gateway in service.gateways.values())
        service.set_admission(False)
        assert service.gateways["a"].paused and not service.gateways["b"].paused
        assert not any(gateway.closed for gateway in service.gateways.values())
        backend.blocked = False
        service.sync_admission()
        assert not any(gateway.paused for gateway in service.gateways.values())
    finally:
        service.stop.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_failed_startup_does_not_disable_a_healthy_binding(tmp_path):
    """The SDK keeps failed-resource reconciliation alive without admitting new work."""
    class StartupFailure(ResourceRuntime):
        """Fail one resource's readiness check before transport startup."""

        async def check(self, gateway):
            """Represent a real restoration failure on one resource."""
            if gateway.root.name == "a":
                raise RuntimeError("device restoration required")

    service = GatewayService(runtime(tmp_path, StartupFailure), ResourceGateway)
    task = asyncio.create_task(service.run())
    try:
        async with asyncio.timeout(5):
            while service.readiness != "ready":
                await asyncio.sleep(0)
        assert service.gateways["a"].paused and not service.gateways["b"].paused
        assert service.health()["gateways"]["a"]["startup_error"] == "device restoration required"
        assert not task.done()
    finally:
        service.stop.set()
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_readiness_overlaps_independent_resources_but_serializes_shared_ones(tmp_path):
    """Readiness tests use independent phones concurrently without overlapping their modes."""
    entered = {key: asyncio.Event() for key in ("a-static", "a-game", "b-static", "b-game")}
    release = {key: asyncio.Event() for key in entered}

    class GroupedRuntime(ResourceRuntime):
        """Expose two evaluator modes backed by each physical resource."""

        def bindings(self):
            """Associate evaluator readiness with the resource that owns it."""
            return [
                GatewayBinding(key, key, self.evaluator, self.options["root"] / key, resource_key=key[0])
                for key in entered
            ]

        async def check(self, gateway):
            """Hold a readiness operation until the test explicitly permits completion."""
            entered[gateway.root.name].set()
            await release[gateway.root.name].wait()

    service = GatewayService(runtime(tmp_path, GroupedRuntime), ResourceGateway)
    service.bound = service.runtime.bindings()
    service.gateways = {
        binding.key: ResourceGateway(None, None, None, None, binding.directory, 1)
        for binding in service.bound
    }
    task = asyncio.create_task(service.check_bindings())
    try:
        await asyncio.wait_for(asyncio.gather(entered["a-static"].wait(), entered["b-static"].wait()), 5)
        assert not entered["a-game"].is_set() and not entered["b-game"].is_set()
        release["a-static"].set()
        await asyncio.wait_for(entered["a-game"].wait(), 5)
        assert not entered["b-game"].is_set()
        release["b-static"].set()
        await asyncio.wait_for(entered["b-game"].wait(), 5)
    finally:
        for event in release.values():
            event.set()
        await asyncio.wait_for(task, 5)
    assert all(value == "ready" for value in service.binding_status.values())
