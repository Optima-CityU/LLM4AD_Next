"""Pool configuration compatibility and client wire/result contracts."""

import copy
import uuid

import pytest

from llm4ad.config.evaluator import EvalContext, RemoteEvaluatorConfig
from llm4ad.gateway.sdk import client

pytestmark = pytest.mark.unit


def test_normalizes_only_an_unambiguous_legacy_target():
    """Keep one explicit legacy target and discard case-specific dispatch settings."""
    gateway_id = str(uuid.uuid4())
    raw = {"gateway_ids": [gateway_id], "lease_timeout": 10, "max_attempts": 2}
    config = RemoteEvaluatorConfig.model_validate(raw)
    assert config.gateway_id == gateway_id
    assert config.model_dump().keys().isdisjoint(raw)
    assert raw["gateway_ids"] == [gateway_id]
    assert RemoteEvaluatorConfig(**raw, selection_mode="auto").gateway_id is None
    assert RemoteEvaluatorConfig(gateway_ids=[gateway_id, str(uuid.uuid4())]).gateway_id is None
    assert RemoteEvaluatorConfig().gateway_id is None


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
    result = await client.evaluate_remote(RemoteEvaluatorConfig(gateway_id=str(uuid.uuid4())), EvalContext(project_root=str(tmp_path)))
    assert result.success is False
    assert result.error_message == "evaluation failed"
    assert result.metadata["failure_category"] == category
    assert not list((tmp_path / ".remote-evaluation").iterdir())


@pytest.mark.parametrize("category", ["candidate", "infrastructure", "evaluator", "configuration"])
async def test_client_sends_an_exact_target_and_keeps_truthful_final_failure(tmp_path, monkeypatch, category):
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
    config = RemoteEvaluatorConfig(gateway_id=str(uuid.uuid4()))
    result = await client.evaluate_remote(config, EvalContext(project_root=str(tmp_path)))
    assert result.success is False
    assert result.metrics == ({"quality": 7} if category == "candidate" else {})
    assert result.score == (99 if category == "candidate" else 0)
    assert result.metadata["failure_category"] == category
    assert result.metadata["attempts_exhausted"] == (category != "candidate")
    assert result.metadata["attempt_history"] == report["metadata"]["attempt_history"]
    assert result.metadata["remote_attempt_id"] == "attempt-3"
    body = session.requests[0][2]["json"]
    assert body["gateway_id"] == config.gateway_id and body["dispatch_version"] == 2
    assert body.keys().isdisjoint({"gateway_ids", "selection_mode", "max_attempts", "lease_timeout"})
    assert [request[0] for request in session.requests] == ["POST", "GET", "GET", "GET"]
    assert all(request[1].endswith(body["id"]) for request in session.requests[1:])
    assert not list((tmp_path / ".remote-evaluation").iterdir())
