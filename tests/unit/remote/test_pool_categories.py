"""Pool failure routing preserves candidate verdicts and truthful retry categories."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from llm4ad.evaluator.models import EvaluationResult
from llm4ad.gateway.sdk import gateway
from llm4ad.gateway.sdk.errors import EvaluationConfigurationError, EvaluationError, EvaluationUnavailableError
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.retries import retry_read

pytestmark = pytest.mark.unit


def test_configuration_failure_is_available_through_public_sdk_imports():
    """Custom evaluators can declare nonretryable settings errors without private imports."""
    from llm4ad.gateway.sdk import EvaluationConfigurationError as CurrentError
    from llm4ad.remote import EvaluationConfigurationError as CompatibleError

    assert CurrentError is CompatibleError is EvaluationConfigurationError


@pytest.mark.parametrize("error,category,retryable", [
    (OSError("device gone"), "infrastructure", True),
    (TimeoutError("read timeout"), "infrastructure", True),
    (EvaluationUnavailableError("device busy"), "infrastructure", True),
    (RuntimeError("unknown evaluator fault"), "evaluator", True),
    (EvaluationError("unknown structured fault"), "evaluator", True),
    (EvaluationConfigurationError("invalid parameters"), "configuration", False),
    (ValueError("unexpected evaluator error"), "evaluator", True),
    (EvaluationUnavailableError("compile failed", metadata={"candidate_failure": True}), "candidate", False),
    (EvaluationError("configuration", metadata={"failure_category": "configuration"}), "configuration", False),
    (OSError("candidate failed"), "infrastructure", True),
    (EvaluationError("malformed category", metadata={"failure_category": {}}), "evaluator", True),
    (EvaluationError("malformed category", metadata={"failure_category": []}), "evaluator", True),
])
@pytest.mark.parametrize("reporting_fails", [False, True])
def test_worker_exception_category_and_retryability_agree(tmp_path, monkeypatch, error, category, retryable, reporting_fails):
    """Even broken diagnostics cannot overwrite an explicit candidate/configuration verdict."""
    monkeypatch.chdir(tmp_path)
    job = {"id": "job", "attempt_id": "attempt", "task_id": "task", "project_root": str(tmp_path),
           "timeout": 30, "managed_attempt": True, "lease_remaining": 60}
    journal = Journal(tmp_path / "jobs.sqlite3")
    journal.accept(job)

    async def evaluate(*args):
        """Raise the original evaluator error inside the SDK worker boundary."""
        raise error

    monkeypatch.setattr(gateway, "load_evaluator", lambda _: SimpleNamespace(evaluate=evaluate))
    if reporting_fails:
        def broken(*args):
            """Simulate a disk failure while collecting diagnostic artifacts."""
            raise OSError("diagnostics unavailable")
        monkeypatch.setattr(gateway, "exception_report", broken)
    outcome = gateway.execute(journal.path, "fixture:Evaluator", job)
    metadata = outcome["report"]["metadata"]
    assert outcome["retryable"] is retryable
    assert metadata["failure_category"] == category
    assert metadata["infrastructure_failure"] == (category == "infrastructure")
    assert metadata["candidate_failure"] == (category == "candidate")
    assert outcome["report"]["metrics"] == {}


@pytest.mark.parametrize("metadata,category,retryable", [
    ({}, "candidate", False),
    ({"candidate_failure": True}, "candidate", False),
    ({"infrastructure_failure": True}, "infrastructure", True),
    ({"failure_category": "evaluator", "infrastructure_failure": True}, "evaluator", True),
    ({"failure_category": "configuration"}, "configuration", False),
    ({"failure_category": "candidate", "infrastructure_failure": True}, "candidate", False),
])
def test_returned_failure_routes_explicit_verdict_without_inventing_metrics(tmp_path, monkeypatch, metadata, category, retryable):
    """Returned candidate failures stay terminal; noncandidate outcomes request pool retry."""
    monkeypatch.chdir(tmp_path)
    job = {"id": "job", "attempt_id": "attempt", "task_id": "task", "project_root": str(tmp_path),
           "timeout": 30, "managed_attempt": True, "lease_remaining": 60}
    journal = Journal(tmp_path / "jobs.sqlite3")
    journal.accept(job)

    async def evaluate(*args):
        """Return a structured failed measurement."""
        return EvaluationResult(success=False, score=42, metrics={"quality": 4}, metadata=metadata)

    monkeypatch.setattr(gateway, "load_evaluator", lambda _: SimpleNamespace(evaluate=evaluate))
    outcome = gateway.execute(journal.path, "fixture:Evaluator", job)
    report = outcome["report"]
    assert outcome["retryable"] is retryable
    assert report["metadata"]["failure_category"] == category
    assert report["metadata"]["infrastructure_failure"] == (category == "infrastructure")
    assert report["metrics"] == ({"quality": 4} if category == "candidate" else {})
    assert "score" not in report


def test_explicit_candidate_exception_preserves_feedback_and_cannot_be_reclassified(tmp_path, monkeypatch):
    """Candidate compiler errors retain domain context while blocking device failover."""
    monkeypatch.chdir(tmp_path)
    job = {"id": "job", "attempt_id": "attempt", "task_id": "task", "project_root": str(tmp_path),
           "timeout": 30, "managed_attempt": True, "lease_remaining": 60}
    journal = Journal(tmp_path / "jobs.sqlite3")
    journal.accept(job)
    Reporter(journal, "job", "attempt").metadata({"infrastructure_failure": True})

    async def evaluate(*args):
        """Attach an explicit verdict to a subprocess compilation failure."""
        raise EvaluationError("syntax error", metadata={"failure_category": "candidate", "stage": "compile"},
                              evolution_feedback={"hint": "repair the syntax"})

    monkeypatch.setattr(gateway, "load_evaluator", lambda _: SimpleNamespace(evaluate=evaluate))
    outcome = gateway.execute(journal.path, "fixture:Evaluator", job)
    assert outcome["retryable"] is False
    assert outcome["report"]["metadata"]["failure_category"] == "candidate"
    assert outcome["report"]["metadata"]["infrastructure_failure"] is False
    assert outcome["report"]["evolution_feedback"] == {"hint": "repair the syntax"}
    assert "syntax error" in json.dumps(outcome)


def test_read_retries_remain_independent_of_the_pooled_execution_limit():
    """Three safe native reads can occur within one platform-owned execution attempt."""
    calls = []

    def read():
        """Model a read-only native operation that succeeds on its third call."""
        calls.append(1)
        if len(calls) < 3:
            raise EvaluationUnavailableError("read unavailable")
        return "ready"

    assert retry_read(read) == "ready" and len(calls) == 3

    def failed_read():
        """Keep nonretryable native errors intact without executing any programs."""
        raise subprocess.CalledProcessError(1, ["fixture"])

    with pytest.raises(subprocess.CalledProcessError):
        retry_read(failed_read)
