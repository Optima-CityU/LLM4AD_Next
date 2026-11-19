"""Verify complete service shutdown without workers, sockets or physical devices."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from llm4ad.gateway.sdk.processes import ProcessOwnershipError
from llm4ad.gateway.sdk.runtime import GatewayBinding
from llm4ad.gateway.sdk.service import GatewayService

pytestmark = pytest.mark.unit


@pytest.fixture
def service(tmp_path, monkeypatch):
    """Prepare an already stopping service with entirely synthetic resources."""
    runtime = SimpleNamespace(
        options={
            "root": tmp_path,
            "server": "http://fixture.invalid",
            "name": "shutdown",
            "allowed_users": [],
            "port": 0,
            "cleanup_enabled": False,
            "cleanup_interval": 100,
            "cleanup_keep_seconds": 0,
        },
        lease_directories=lambda: [],
        start=AsyncMock(),
        api_handler=lambda: None,
        bindings=lambda: [GatewayBinding("a", "a", "fixture:Evaluator", tmp_path / "a")],
        stop_accepting=Mock(),
        close=AsyncMock(),
    )
    health = SimpleNamespace(close=Mock())
    monkeypatch.setattr("llm4ad.gateway.sdk.service.HealthServer", lambda *args, **kwargs: health)
    worker = GatewayService(runtime, lambda *args: SimpleNamespace())
    worker.stop.set()
    return worker, runtime, health


@pytest.mark.asyncio
async def test_shutdown_attempts_every_resource_and_reports_all_failures(service):
    """Uncertain process ownership cannot skip a peer, backend or health cleanup."""
    worker, runtime, health = service
    ownership_error = ProcessOwnershipError("First pool still has unconfirmed ownership")
    backend_error = RuntimeError("Backend restoration remains unconfirmed")
    health_error = OSError("Health server close failed")
    first = SimpleNamespace(close=Mock(side_effect=ownership_error), release=Mock())
    second = SimpleNamespace(close=Mock(), release=Mock())
    worker.executors = {"first": first, "second": second}
    runtime.close.side_effect = backend_error
    health.close.side_effect = health_error

    with pytest.raises(ExceptionGroup) as caught:
        await worker.run()

    assert caught.value.exceptions == (ownership_error, backend_error, health_error)
    first.close.assert_called_once_with()
    second.close.assert_called_once_with()
    runtime.close.assert_awaited_once_with()
    health.close.assert_called_once_with()
    assert worker.readiness == "unready"
    assert "unconfirmed ownership" in worker.error


@pytest.mark.asyncio
async def test_stop_accepting_failure_still_closes_every_resource(service):
    """A failed admission fence remains visible after all cleanup has been attempted."""
    worker, runtime, health = service
    failure = RuntimeError("Admission fence failed")
    runtime.stop_accepting.side_effect = failure
    executor = SimpleNamespace(close=Mock(), release=Mock())
    worker.executors["resource"] = executor

    with pytest.raises(RuntimeError, match="Admission fence failed"):
        await worker.run()

    executor.close.assert_called_once_with()
    runtime.close.assert_awaited_once_with()
    health.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_shutdown_keeps_the_startup_error_alongside_cleanup_failures(service):
    """A failed startup remains actionable when its shutdown also fails."""
    worker, runtime, health = service
    startup_error = ValueError("Invalid runtime configuration")
    shutdown_error = ProcessOwnershipError("Cleanup remains unconfirmed")
    runtime.start.side_effect = startup_error
    runtime.close.side_effect = shutdown_error

    with pytest.raises(ExceptionGroup) as caught:
        await worker.run()

    assert caught.value.exceptions == (startup_error, shutdown_error)
    runtime.close.assert_awaited_once_with()
    health.close.assert_not_called()


@pytest.mark.asyncio
async def test_clean_shutdown_preserves_the_service_return_code(service):
    """Successful shutdown preserves the existing public return value."""
    worker, runtime, health = service
    assert await worker.run() == 0
    runtime.close.assert_awaited_once_with()
    health.close.assert_called_once_with()
    assert worker.readiness == "stopped"


@pytest.mark.asyncio
async def test_task_finalization_failure_is_reported_after_remaining_cleanup(service):
    """A gateway's failed cancellation finalizer is preserved after resources close."""
    worker, runtime, health = service
    entered = asyncio.Event()
    failure = ProcessOwnershipError("Worker termination remains unconfirmed")

    async def accepted_work():
        """Fail finalization only after the service requests cancellation."""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            raise failure

    task = asyncio.create_task(accepted_work())
    worker.gateway_tasks["accepted"] = task
    await entered.wait()
    with pytest.raises(ProcessOwnershipError, match="termination remains unconfirmed"):
        await worker.run()
    runtime.close.assert_awaited_once_with()
    health.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_cancellation_during_shutdown_waits_for_owned_thread_cleanup(service):
    """Caller cancellation cannot abandon a pool close already executing in a thread."""
    from threading import Event

    worker, runtime, health = service
    entered, release, finished = Event(), Event(), Event()

    def close():
        """Keep termination in flight until its test-owned acknowledgement arrives."""
        entered.set()
        assert release.wait(3), "The test must release its own cleanup thread"
        finished.set()

    worker.executors["resource"] = SimpleNamespace(close=close, release=Mock())
    task = asyncio.create_task(worker.run())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        runtime.close.assert_not_awaited()
        assert not finished.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
    assert finished.is_set()
    runtime.close.assert_awaited_once_with()
    health.close.assert_called_once_with()
