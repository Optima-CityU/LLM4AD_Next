"""Keep verified read-only device failures separate from uncertain execution."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from llm4ad.gateway.examples.hgamey.evaluator import HGameyShaderEvaluator
from llm4ad.gateway.examples.hgamey.evaluators import HGameyGameEvaluator, HGameyStaticEvaluator

pytestmark = pytest.mark.unit


@pytest.fixture
def preparation(tmp_path):
    """Provide a failed external job whose durable preparation never started execution."""
    state = {
        "evidence_directory": str(tmp_path), "external_job_id": "native-job",
        "request": {"device_id": "phone"}, "idempotency_key": "a" * 64,
        "remote_job_id": "remote-job", "remote_attempt_id": "attempt",
    }
    job = {"id": "native-job", "state": "failed", "cleanup_status": "completed"}
    report = {"status": "ERROR", "error": "ADB transport unavailable", "external_cleanup_confirmed": True,
              "preparation": {"schema": "hgamey/device-preparation@1", "job_id": "native-job",
                              "device_id": "phone", "device_started": False}}
    return state, job, report


def follow(cls, state, job, report):
    """Use normal receipt validation and conversion with only local HTTP replaced."""
    adapter = object.__new__(cls)
    adapter.benchmark = SimpleNamespace(version="frozen", game_check=None)
    adapter.retry_rpc = AsyncMock(side_effect=[job, report])
    found = asyncio.run(adapter.follow_job(None, Mock(), state))
    return adapter, found


@pytest.mark.parametrize("cls", [HGameyShaderEvaluator, HGameyStaticEvaluator, HGameyGameEvaluator])
def test_verified_preparation_failure_is_unscored_and_not_unknown(preparation, cls):
    """All evaluator modes finish with an explicit infrastructure error and no invented fitness."""
    state, job, report = preparation
    adapter, found = follow(cls, state, job, report)
    assert found["preparation_failure_verified"] is True
    result = adapter.convert_report(found, state)
    assert result is not None and result.success is False
    assert result.metrics == {} and result.error_message == "ADB transport unavailable"
    assert result.metadata["infrastructure_failure"] is True
    assert result.metadata["external_execution_started"] is False
    assert result.metadata["external_cleanup_confirmed"] is True
    assert result.metadata["remote_job_id"] == "remote-job"


@pytest.mark.parametrize("field,value", [
    ("job_id", "other-job"), ("device_id", "other-phone"),
    ("device_started", True), ("device_started", 0), ("schema", "unknown"),
])
def test_foreign_or_started_preparation_never_bypasses_recovery(preparation, field, value):
    """A report assertion alone cannot turn an executed or mismatched job into a safe failure."""
    state, job, report = preparation
    report["preparation"][field] = value
    adapter, found = follow(HGameyGameEvaluator, state, job, report)
    assert not found.get("preparation_failure_verified")
    assert adapter.preparation_failure(found, state) is None


@pytest.mark.parametrize("change", ["cleanup", "unconfirmed_report", "completed"])
def test_terminal_cleanup_and_failed_state_are_required(preparation, change):
    """The executor's terminal receipt remains authoritative for known no-op failures."""
    state, job, report = preparation
    if change == "cleanup":
        job["cleanup_status"] = "uncertain"
    elif change == "unconfirmed_report":
        report["external_cleanup_confirmed"] = False
    else:
        job["state"] = "completed"
    adapter, found = follow(HGameyGameEvaluator, state, job, report)
    assert found is None or not found.get("preparation_failure_verified")
