"""Exercise the common lifecycle and health with ordinary custom evaluators."""

import asyncio
import json
import signal
import os
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from llm4ad.gateway.cli import app
from llm4ad.gateway.sdk.contracts import load_evaluator
from llm4ad.gateway.sdk.health import probe
from llm4ad.gateway.sdk.runtime import GatewayRuntime
from llm4ad.gateway.sdk.service import GatewayService, create_service, run_service

SPEC = "llm4ad.gateway.examples.basic.evaluator:Evaluator"


class IdleGateway:
    """Model an idle, disconnected transport without network or native dependencies."""

    def __init__(self, server, name, users, evaluator, directory, concurrency):
        """Use the real adapter loader and filesystem state directory."""
        self.adapter = load_evaluator(evaluator)
        self.root = directory
        self.root.mkdir(parents=True, exist_ok=True)
        self.processes = {}
        self.started = asyncio.Event()
        self.closed = False

    async def run(self):
        """Remain alive until the common service stops this transport."""
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.closed = True


def settings(root):
    """Supply a valid independent gateway identity without external credentials."""
    return {
        "LLM4AD_SERVER_URL": "https://fixture-platform.invalid",
        "LLM4AD_GATEWAY_NAME": "custom-evaluator",
        "LLM4AD_ALLOWED_USER_IDS": str(uuid.uuid4()),
        "LLM4AD_GATEWAY_STATE_DIR": str(root),
    }


@pytest.mark.asyncio
async def test_custom_evaluator_uses_shared_health_and_graceful_lifecycle(tmp_path):
    """A normal evaluator gets health and shutdown without implementing a service."""
    service = create_service(settings(tmp_path), SPEC, IdleGateway)
    assert type(service.runtime) is GatewayRuntime
    task = asyncio.create_task(service.run())
    try:
        for _ in range(200):
            if service.gateway:
                await asyncio.wait_for(service.gateway.started.wait(), 2)
                break
            await asyncio.sleep(0.01)
        result = await asyncio.to_thread(probe, tmp_path)
        assert result["ready"] is True
        assert result["gateway"]["connected"] is False
        assert "executor" not in result
        response = CliRunner().invoke(app, ["health", "--state-dir", str(tmp_path)])
        assert response.exit_code == 0 and json.loads(response.stdout)["ready"] is True
    finally:
        service.stop.set()
        await asyncio.wait_for(task, 5)
    assert service.gateway.closed is True
    assert probe(tmp_path)["ready"] is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX process signals")
@pytest.mark.asyncio
async def test_common_signal_runner_waits_for_backend_close(tmp_path):
    """SIGTERM reaches the same drain hook used for explicit stops."""
    closed = asyncio.Event()

    class Runtime(GatewayRuntime):
        """Represent a local backend needing an asynchronous drain."""

        async def check(self, gateway):
            """Request shutdown during startup to test the shared signal path."""
            asyncio.get_running_loop().call_soon(os.kill, os.getpid(), signal.SIGTERM)

        async def close(self):
            """Finish a real asynchronous step before marking the backend closed."""
            await asyncio.sleep(0)
            closed.set()

    service = GatewayService(Runtime(settings(tmp_path), SPEC), IdleGateway)
    assert await asyncio.wait_for(run_service(service), 5) == 0
    assert closed.is_set() and service.readiness == "stopped"


@pytest.mark.asyncio
async def test_file_evaluator_can_supply_backend_hooks_through_the_same_loader(tmp_path):
    """A custom module chooses optional resources without a new CLI adapter branch."""
    source = tmp_path / "custom.py"
    source.write_text(
        "from llm4ad.gateway.examples.basic.evaluator import Evaluator as Base\n"
        "from llm4ad.gateway.sdk.runtime import GatewayRuntime\n"
        "class Runtime(GatewayRuntime):\n"
        "    def details(self):\n"
        "        return {'custom_backend': 'configured'}\n"
        "class Evaluator(Base):\n"
        "    @classmethod\n"
        "    def create_runtime(cls, environment, evaluator):\n"
        "        return Runtime(environment, evaluator)\n"
    )
    service = create_service(settings(tmp_path / "state"), f"{source}:Evaluator", IdleGateway)
    assert service.runtime.details() == {"custom_backend": "configured"}
    assert service.runtime.bindings()[0].evaluator == f"{source}:Evaluator"


def test_shared_health_reports_missing_or_stopped_services(tmp_path):
    """Health does not invent readiness before an owned endpoint exists."""
    outcome = CliRunner().invoke(app, ["health", "--state-dir", str(tmp_path)])
    assert outcome.exit_code == 1 and json.loads(outcome.stdout)["ready"] is False
    assert not list(tmp_path.iterdir())
