"""External restoration gets a coherent bounded deadline across SDK wrappers."""

import asyncio
import uuid

import pytest

from llm4ad.evaluator.models import Metric
from llm4ad.gateway.sdk import gateway as gateway_module
from llm4ad.gateway.sdk.check import _OfflineAdapter
from llm4ad.gateway.sdk.contracts import EvaluationContract, GatewayEvaluator
from llm4ad.gateway.sdk.gateway import Gateway
from llm4ad.gateway.sdk.journal import Reporter

pytestmark = pytest.mark.unit


class ExternalEvaluator(GatewayEvaluator):
    """Restore a synthetic resource without spawning work or contacting a platform."""

    contract = EvaluationContract(
        evaluator_id="cleanup-budget", version="1", data_version="1", metrics=[Metric(name="quality")],
    )
    cleanup_timeout = 90.0

    async def cancel(self, job, saved_state):
        """Return a positively observed restoration result."""
        return True


def make_gateway(tmp_path):
    """Create an accepted job and its existing external ownership handle."""
    gateway = Gateway(
        "http://localhost:9", "cleanup", [str(uuid.uuid4())],
        "llm4ad.gateway.examples.basic.evaluator:Evaluator", tmp_path / "state", adapter=ExternalEvaluator(),
    )
    job = {"id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4()),
           "timeout": 40, "project_root": str(tmp_path)}
    gateway.journal.accept(job)
    Reporter(gateway.journal, job["id"], job["attempt_id"]).save_state({"external_id": "owned-resource"})
    return gateway, job


@pytest.mark.asyncio
async def test_external_restoration_can_outlast_default_cleanup_deadline(tmp_path, monkeypatch):
    """A valid external cleanup longer than 30 seconds is not perpetually cut short."""
    gateway, job = make_gateway(tmp_path)
    actual_wait = asyncio.wait_for
    budgets = []

    async def elapsed_restoration(awaitable, timeout):
        budgets.append(timeout)
        if timeout < 60:
            awaitable.close()
            raise TimeoutError("The resource is still restoring inside its declared 90-second budget")
        return await actual_wait(awaitable, timeout=2)

    monkeypatch.setattr(gateway_module.asyncio, "wait_for", elapsed_restoration)
    try:
        assert await gateway._cleanup(job) is True
        assert budgets == [90.0]
        with gateway.journal.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM cleanup_debts").fetchone()[0] == 0
        assert not gateway.executor.paused
    finally:
        gateway.stop_workers()


@pytest.mark.asyncio
async def test_cleanup_still_expires_without_claiming_restoration(tmp_path):
    """An over-budget external hook retains its debt and cannot release the slot."""
    gateway, job = make_gateway(tmp_path)
    gateway.adapter.cleanup_timeout = 0.01

    async def blocked_cleanup(job, saved_state):
        await asyncio.Event().wait()

    gateway.adapter.cancel = blocked_cleanup
    try:
        assert await gateway._cleanup(job) is False
        assert gateway.executor.paused
        with gateway.journal.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM cleanup_debts").fetchone()[0] == 1
    finally:
        gateway.stop_workers()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "90"])
def test_invalid_cleanup_deadline_is_rejected_before_startup(tmp_path, timeout):
    """Invalid limits cannot become an unbounded or permanently failing cleanup loop."""
    adapter = ExternalEvaluator()
    adapter.cleanup_timeout = timeout
    with pytest.raises(ValueError, match="cleanup_timeout"):
        Gateway("http://localhost:9", "cleanup", [], "fixture:Evaluator", tmp_path / "state", adapter=adapter)
    assert not (tmp_path / "state").exists()


def test_offline_check_keeps_external_cleanup_deadline(tmp_path):
    """The offline wrapper must not silently restore the SDK's shorter default."""
    source = tmp_path / "adapter.py"
    source.write_text(
        "from llm4ad.gateway.sdk import GatewayEvaluator\n"
        "class Evaluator(GatewayEvaluator):\n"
        "    cleanup_timeout = 90\n"
    )
    wrapper = _OfflineAdapter(f"{source}:Evaluator", ExternalEvaluator.contract)
    assert wrapper.cleanup_timeout == 90


def test_hgamey_parent_deadlines_cover_native_restoration():
    """Lease waits and native cleanup steps fit inside the caller's finite budget."""
    from llm4ad.gateway.examples.hgamey.configuration import NATIVE_RESTORATION_TIMEOUT
    from llm4ad.gateway.examples.hgamey.evaluator import HGameyShaderEvaluator

    assert 90 < NATIVE_RESTORATION_TIMEOUT < HGameyShaderEvaluator.cleanup_timeout
