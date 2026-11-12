"""Public evaluator reporting keeps diagnostics and extra data across failures/restarts."""

import json
import uuid
from pathlib import Path

import pytest

from llm4ad.gateway.sdk.check import check_evaluator
from llm4ad.gateway.sdk.journal import Journal, Reporter


def accepted(tmp_path):
    """Create a real isolated journal and a candidate workspace."""
    work = tmp_path / "candidate"
    work.mkdir()
    job = {key: str(uuid.uuid4()) for key in ("id", "attempt_id", "task_id")}
    job.update(project_root=str(work), timeout=60)
    journal = Journal(tmp_path / "jobs.sqlite3")
    journal.accept(job)
    return journal, job, Reporter(journal, job["id"], job["attempt_id"])


def test_messages_and_metadata_are_durable_and_attempt_fenced(tmp_path):
    """Messages do not restart execution timers or contaminate a replacement attempt."""
    journal, job, reporter = accepted(tmp_path)
    reporter.message("Waiting for local resources", level="warning", data={"queue": 2})
    assert journal.get(job["id"])["state"] == "accepted"
    assert journal.get(job["id"])["started"] is None
    reporter.metadata({"device": "fixture", "configuration": {"mode": "standard"}})
    reporter.progress("Compiling", 1, 3)
    restarted = Reporter(Journal(journal.path), job["id"], job["attempt_id"])
    payload = restarted.enrich({"metrics": {}, "metadata": {"final": True}})
    assert payload["metadata"]["device"] == "fixture"
    assert payload["metadata"]["final"] is True
    events = payload["metadata"]["evaluation_messages"]
    assert events[0]["message"] == "Waiting for local resources"
    assert events[0]["data"] == {"queue": 2}
    assert events[0]["level"] == "warning"
    assert events[-1]["kind"] == "progress"
    started = journal.get(job["id"])["started"]
    restarted.message("Device selected")
    live = json.loads(journal.get(job["id"])["report"])
    assert live["completed"] == 1 and live["total"] == 3
    assert journal.get(job["id"])["started"] == started
    journal.accept(job | {"attempt_id": str(uuid.uuid4())})
    reporter.message("stale message")
    reporter.metadata({"stale": True})
    current = Reporter(journal, job["id"]).enrich({})
    assert "evaluation_messages" not in current.get("metadata", {})
    assert "stale" not in current.get("metadata", {})


def test_extra_information_and_attachment_description_survive_real_worker(tmp_path):
    """The public adapter API alone can report messages, extra data and evidence."""
    evaluator = tmp_path / "evaluator.py"
    evaluator.write_text("""
from pathlib import Path
from llm4ad.gateway.sdk import GatewayEvaluator, EvaluationContract, EvaluationResult, Metric
class Evaluator(GatewayEvaluator):
    contract = EvaluationContract(evaluator_id="reporting", version="1", data_version="1", metrics=[Metric(name="value")])
    async def evaluate(self, job, reporter):
        reporter.message("Running baseline", data={"arm": "baseline"})
        reporter.metadata({"dataset_version": "fixture-v1"})
        output = Path(job.artifact_dir) / "report.md"
        output.write_text("# Actual generated fixture report")
        reporter.artifact("report.md", output, description="Evaluation explanation")
        return EvaluationResult(score=3, metrics={"value": 3}, monitor_metrics={"samples": 2}, evolution_feedback={"hint": "measured"})
""")
    candidate = tmp_path / "source"
    candidate.mkdir()
    (candidate / "solution.py").write_text("pass\n")
    result = check_evaluator(f"{evaluator}:Evaluator", candidate, tmp_path / "state", 30)
    assert result["ok"], result
    data = result["result"]
    assert data["monitor_metrics"] == {"samples": 2}
    assert data["evolution_feedback"] == {"hint": "measured"}
    assert data["metadata"]["dataset_version"] == "fixture-v1"
    assert data["metadata"]["artifact_descriptions"]["report.md"] == "Evaluation explanation"
    assert data["duration_ms"] > 0
    assert any(item["message"] == "Running baseline" for item in data["metadata"]["evaluation_messages"])
    assert result["artifacts"][0]["media_type"] == "text/markdown"
    assert result["artifacts"][0]["description"] == "Evaluation explanation"


def test_initialization_errors_are_reported_with_original_traceback(tmp_path, monkeypatch):
    """Worker initialization failures must not silently escape the SDK error boundary."""
    from llm4ad.gateway.sdk import gateway

    journal, job, _ = accepted(tmp_path)
    if hasattr(gateway.os, "setsid"):
        monkeypatch.setattr(gateway.os, "setsid", lambda: None)
    monkeypatch.chdir(tmp_path)

    def broken(_):
        raise RuntimeError("fixture initialization failed")

    monkeypatch.setattr(gateway, "load_evaluator", broken)
    gateway.execute(journal.path, "fixture:Evaluator", job)
    row = journal.get(job["id"])
    assert row["state"] == "failed"
    result = json.loads(row["report"])
    assert result["error_type"] == "RuntimeError"
    assert result["success"] is False
    assert result["duration_ms"] >= 0
    files = journal.artifacts(job["id"], job["attempt_id"])
    trace = next(item for item in files if item["name"].startswith("diagnostics/exception"))
    assert "fixture initialization failed" in Path(trace["path"]).read_text()


def test_offline_check_retains_constructor_failure(tmp_path):
    """The local check must not construct an adapter outside the reporting worker."""
    evaluator = tmp_path / "evaluator.py"
    evaluator.write_text('''
from llm4ad.gateway.sdk import GatewayEvaluator, EvaluationContract, Metric
class Evaluator(GatewayEvaluator):
    contract = EvaluationContract(evaluator_id="initialization", version="1", data_version="1", metrics=[Metric(name="value")])
    def __init__(self):
        raise RuntimeError("fixture device initialization failed")
''')
    candidate = tmp_path / "source"
    candidate.mkdir()
    (candidate / "solution.py").write_text("pass\n")
    result = check_evaluator(f"{evaluator}:Evaluator", candidate, tmp_path / "state", 30)
    assert result["state"] == "failed", result
    assert result["result"]["error_message"] == "fixture device initialization failed"
    assert any(item["name"].startswith("diagnostics/exception-") for item in result["artifacts"])


def test_known_failure_preserves_feedback_and_declared_diagnostics(tmp_path):
    """A structured evaluation error can retain domain feedback and output files."""
    evaluator = tmp_path / "evaluator.py"
    evaluator.write_text("""
from pathlib import Path
from llm4ad.gateway.sdk import GatewayEvaluator, EvaluationContract, EvaluationError, EvaluationArtifact, Metric
class Evaluator(GatewayEvaluator):
    contract = EvaluationContract(evaluator_id="failure", version="1", data_version="1", metrics=[Metric(name="value")])
    async def evaluate(self, job, reporter):
        reporter.metadata({"dataset_version": "v1"})
        path = Path(job.artifact_dir) / "compiler.log"
        path.write_text("fixture compiler error")
        raise EvaluationError("Compilation failed", metadata={"stage": "compile"},
            evolution_feedback={"hint": "fix candidate syntax"},
            artifacts=[EvaluationArtifact(name="compiler.log", path="compiler.log", description="Compiler stderr")])
""")
    candidate = tmp_path / "source"
    candidate.mkdir()
    (candidate / "solution.py").write_text("pass\n")
    result = check_evaluator(f"{evaluator}:Evaluator", candidate, tmp_path / "state", 30)
    assert result["state"] == "failed", result
    data = result["result"]
    assert data["metadata"]["dataset_version"] == "v1"
    assert data["metadata"]["stage"] == "compile"
    assert data["evolution_feedback"]["hint"] == "fix candidate syntax"
    assert data["error_message"] == "Compilation failed"
    assert any(item["name"] == "compiler.log" for item in result["artifacts"])


@pytest.mark.parametrize("failure", ["unavailable", "subprocess", "timeout", "reporting"])
def test_generic_errors_preserve_recovery_and_subprocess_diagnostics(tmp_path, monkeypatch, failure):
    """Unavailable work waits; process errors retain output; reporting errors retain the cause."""
    import subprocess
    from types import SimpleNamespace

    from llm4ad.gateway.sdk import EvaluationUnavailableError, gateway

    journal, job, _ = accepted(tmp_path)
    monkeypatch.chdir(tmp_path)
    if hasattr(gateway.os, "setsid"):
        monkeypatch.setattr(gateway.os, "setsid", lambda: None)

    async def evaluate(job, reporter):
        reporter.metadata({"phase": "device"})
        if failure == "unavailable":
            raise EvaluationUnavailableError("Device disconnected", metadata={"device": "fixture"})
        if failure == "subprocess":
            raise subprocess.CalledProcessError(2, ["fixture-compiler"], output=b"compiler output", stderr=b"syntax error")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["fixture-compiler"], 10, output=b"compiler output", stderr=b"syntax error")
        raise RuntimeError("original failure")

    monkeypatch.setattr(gateway, "load_evaluator", lambda _: SimpleNamespace(evaluate=evaluate))
    if failure == "reporting":

        def broken(*args):
            raise OSError("diagnostic disk unavailable")

        monkeypatch.setattr(gateway, "exception_report", broken)
    gateway.execute(journal.path, "fixture:Evaluator", job)
    row = journal.get(job["id"])
    data = json.loads(row["report"])
    assert row["state"] == ("recovery_required" if failure == "unavailable" else "failed")
    if failure == "unavailable":
        assert data["metadata"]["device"] == "fixture"
        assert data["metadata"]["phase"] == "device"
    elif failure in {"subprocess", "timeout"}:
        assert data["metadata"].get("returncode", data["metadata"].get("timeout")) == (2 if failure == "subprocess" else 10)
        outputs = {
            Path(item["name"]).name.split("-")[0]: Path(item["path"]).read_bytes()
            for item in journal.artifacts(job["id"], job["attempt_id"])
        }
        assert outputs["stdout"] == b"compiler output"
        assert outputs["stderr"] == b"syntax error"
    else:
        assert data["error_message"] == "original failure"
        assert data["error_type"] == "RuntimeError"
        assert data["metadata"]["reporting_error"] == "OSError"


@pytest.mark.parametrize("terminal", [False, True])
async def test_outbox_delivers_messages_and_artifact_description(tmp_path, terminal):
    """Live messages survive HTTP reporting, and final evidence keeps its description."""
    from unittest.mock import AsyncMock

    from llm4ad.gateway.sdk.gateway import Gateway

    journal, job, reporter = accepted(tmp_path)
    reporter.progress("Compiling", 1, 2)
    reporter.message("Using local compiler", data={"compiler": "fixture"})
    if terminal:
        output = tmp_path / "compiler.txt"
        output.write_text("compiler diagnostics")
        reporter.artifact("compiler.txt", output, description="Compilation output")
        journal.update(job["id"], "failed", {"message": "Compile failed", "error_type": "CompilerError"})
    worker = Gateway.__new__(Gateway)
    worker.journal, worker.identity = journal, {"id": str(uuid.uuid4())}
    worker.processes, worker.capacity, worker.cleanup_managed = {}, 0, True
    sent = []

    async def request(session, method, suffix, **kwargs):
        if suffix.endswith("heartbeat"):
            return {"jobs": []}
        if "/artifacts/" in suffix:
            assert kwargs["data"].read() == b"compiler diagnostics"
            return {"object_key": "test-object"}
        body = kwargs["json"]
        sent.append(body)
        return {"ack": body["sequence"], "state": body["state"]}

    worker.request = AsyncMock(side_effect=request)
    await worker.tick(None)
    assert journal.get(job["id"])["ack"] == journal.get(job["id"])["seq"]
    report = sent[0]["report"]
    assert len(report["metadata"]["evaluation_messages"]) == 2
    if terminal:
        assert report["metadata"]["artifacts"][0]["description"] == "Compilation output"
    else:
        assert report["completed"] == 1 and report["total"] == 2


@pytest.mark.parametrize("rich", [False, True])
async def test_remote_client_preserves_full_failure_result(tmp_path, monkeypatch, rich):
    """A failed remote evaluation keeps all rich fields and still accepts old reports."""
    from aiohttp import web

    from llm4ad.config.evaluator import EvalContext, RemoteEvaluatorConfig
    from llm4ad.gateway.sdk.client import evaluate_remote

    report = {"message": "Candidate failed", "error_type": "CompilerError"}
    if rich:
        report.update(
            metrics={"errors": 1},
            monitor_metrics={"samples": 2},
            duration_ms=145,
            evolution_feedback={"hint": "Fix syntax"},
            metadata={"failure_type": "syntax", "evaluation_messages": [{"message": "Compiling"}]},
        )

    async def submit(request):
        return web.json_response({"state": "failed", "attempt_id": "attempt-1", "report": report})

    app = web.Application()
    app.router.add_post("/tasks/task-1/jobs", submit)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("LLM4AD_REMOTE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("LLM4AD_REMOTE_TOKEN", "test-token")
    monkeypatch.setenv("LLM4AD_REMOTE_TASK_ID", "task-1")
    (tmp_path / "solution.py").write_text("pass\n")
    try:
        config = RemoteEvaluatorConfig(gateway_ids=[str(uuid.uuid4())], contract={})
        result = await evaluate_remote(config, EvalContext(project_root=str(tmp_path)))
    finally:
        await runner.cleanup()
    assert result.success is False and result.error_message == "Candidate failed"
    assert result.metadata["remote_attempt_id"] == "attempt-1"
    if rich:
        assert result.duration_ms == 145
        assert result.monitor_metrics == {"samples": 2}
        assert result.evolution_feedback == {"hint": "Fix syntax"}
        assert result.metadata["evaluation_messages"] == [{"message": "Compiling"}]
        assert result.metadata["failure_type"] == "syntax"
    else:
        assert result.metadata["failure_type"] == "CompilerError"
