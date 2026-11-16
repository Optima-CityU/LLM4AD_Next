"""Pool configuration compatibility and client wire/result contracts."""

import copy
import uuid

import pytest
from pydantic import ValidationError

from llm4ad.config.evaluator import EvalContext, RemoteEvaluatorConfig
from llm4ad.gateway.sdk import client

pytestmark = pytest.mark.unit


def test_auto_defaults_and_legacy_selection_do_not_mutate_raw_configuration():
    """Legacy IDs select a fixed pool while new empty configurations use auto."""
    config = RemoteEvaluatorConfig()
    assert config.selection_mode == "auto" and config.gateway_ids == []
    assert config.max_attempts == 3 and config.lease_timeout == 60
    raw = {"gateway_ids": [str(uuid.uuid4())]}
    original = copy.deepcopy(raw)
    selected = RemoteEvaluatorConfig.model_validate(raw)
    assert selected.selection_mode == "selected" and raw == original
    assert RemoteEvaluatorConfig(**raw, selection_mode="auto").selection_mode == "auto"
    assert RemoteEvaluatorConfig(gateway_ids=[]).selection_mode == "auto"
    assert RemoteEvaluatorConfig.model_validate(selected.model_dump()).selection_mode == "selected"


@pytest.mark.parametrize("values", [
    {"selection_mode": "selected"}, {"selection_mode": "selected", "gateway_ids": []},
    {"selection_mode": "unknown"}, {"max_attempts": 0}, {"max_attempts": -1},
    {"lease_timeout": 0}, {"lease_timeout": -1}, {"lease_timeout": float("inf")},
])
def test_pool_configuration_rejects_invalid_admission_and_budgets(values):
    """Invalid pool selection or limits fail before any submission."""
    with pytest.raises(ValidationError):
        RemoteEvaluatorConfig(**values)


class Response:
    """Serve deterministic platform responses without opening a network socket."""

    status = 200

    def __init__(self, payload):
        """Retain one synthetic API response."""
        self.payload = payload

    async def __aenter__(self):
        """Expose the same response through aiohttp's context protocol."""
        return self

    async def __aexit__(self, *args):
        """Release the synthetic response."""

    def raise_for_status(self):
        """Accept successful HTTP status."""

    async def json(self):
        """Return a fresh copy so client normalization cannot alter the fixture."""
        return copy.deepcopy(self.payload)


class Session(Response):
    """Record client requests and return a sequence of fake job states."""

    def __init__(self, payloads):
        """Initialize a deterministic response stream."""
        self.payloads = iter(payloads)
        self.requests = []

    def request(self, method, url, **kwargs):
        """Record the exact submission and poll bodies."""
        self.requests.append((method, url, kwargs))
        return Response(next(self.payloads))


@pytest.mark.parametrize("category", [{}, [], None, 1])
async def test_malformed_failure_category_does_not_interrupt_remote_result(tmp_path, monkeypatch, category):
    """Untrusted optional metadata cannot turn a terminal result into a client crash."""
    (tmp_path / "solution.py").write_text("pass\n")
    monkeypatch.setenv("LLM4AD_REMOTE_URL", "http://offline-fixture")
    monkeypatch.setenv("LLM4AD_REMOTE_TOKEN", "fixture")
    monkeypatch.setenv("LLM4AD_REMOTE_TASK_ID", "task")
    session = Session([{
        "state": "failed", "attempt_id": "attempt",
        "report": {"success": False, "metrics": {}, "error_message": "evaluation failed",
                   "metadata": {"failure_category": category}},
    }])
    monkeypatch.setattr(client.aiohttp, "ClientSession", lambda **kwargs: session)
    result = await client.evaluate_remote(RemoteEvaluatorConfig(), EvalContext(project_root=str(tmp_path)))
    assert result.success is False
    assert result.error_message == "evaluation failed"
    assert result.metadata["failure_category"] == category
    assert not list((tmp_path / ".remote-evaluation").iterdir())


@pytest.mark.parametrize("category", ["candidate", "infrastructure", "evaluator", "configuration"])
async def test_client_sends_v2_pool_budget_and_keeps_truthful_final_failure(tmp_path, monkeypatch, category):
    """Server retries consume one submission; terminal noncandidate metrics stay empty."""
    (tmp_path / "solution.py").write_text("pass\n")
    monkeypatch.setenv("LLM4AD_REMOTE_URL", "http://offline-fixture")
    monkeypatch.setenv("LLM4AD_REMOTE_TOKEN", "fixture")
    monkeypatch.setenv("LLM4AD_REMOTE_TASK_ID", "task")
    report = {"success": False, "score": 99, "metrics": {"quality": 7}, "error_message": "last failure",
              "metadata": {"failure_category": category, "attempts_exhausted": category != "candidate",
                           "attempt_history": [{"attempt": 1, "failure_category": category}, {"attempt": 3}]}}
    session = Session([
        {"state": "queued"}, {"state": "running", "attempt_id": "attempt-1"},
        {"state": "queued"}, {"state": "failed", "attempt_id": "attempt-3", "report": report},
    ])
    monkeypatch.setattr(client.aiohttp, "ClientSession", lambda **kwargs: session)

    async def no_wait(*args):
        """Keep this transport-only test deterministic."""

    monkeypatch.setattr(client.asyncio, "sleep", no_wait)
    config = RemoteEvaluatorConfig(max_attempts=4, lease_timeout=12)
    result = await client.evaluate_remote(config, EvalContext(project_root=str(tmp_path)))
    assert result.success is False
    assert result.metrics == ({"quality": 7} if category == "candidate" else {})
    assert result.score == (99 if category == "candidate" else 0)
    assert result.metadata["failure_category"] == category
    assert result.metadata["attempts_exhausted"] == (category != "candidate")
    assert result.metadata["attempt_history"] == report["metadata"]["attempt_history"]
    assert result.metadata["remote_attempt_id"] == "attempt-3"
    body = session.requests[0][2]["json"]
    assert {key: body[key] for key in ("selection_mode", "gateway_ids", "dispatch_version", "max_attempts", "lease_timeout")} == {
        "selection_mode": "auto", "gateway_ids": [], "dispatch_version": 2, "max_attempts": 4, "lease_timeout": 12,
    }
    assert [request[0] for request in session.requests] == ["POST", "GET", "GET", "GET"]
    assert all(request[1].endswith(body["id"]) for request in session.requests[1:])
    assert not list((tmp_path / ".remote-evaluation").iterdir())
