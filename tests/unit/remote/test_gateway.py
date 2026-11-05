"""Remote evaluation durability, isolation and adapter regression coverage."""

import asyncio
import io
import json
import sqlite3
import time
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

from llm4ad.config.evaluator import EvalContext, RemoteEvaluatorConfig
from llm4ad.evaluator.base import EvaluationResult
from llm4ad.evaluator.dispatcher import EvaluationDispatcher
from llm4ad.remote.bundles import extract_candidate, pack_candidate
from llm4ad.remote.client import evaluate_remote
from llm4ad.remote.gateway import Gateway
from llm4ad.remote.journal import Journal, Reporter, gateway_lease

EXAMPLE = Path(__file__).resolve().parents[3] / "examples/remote_evaluation/evaluator.py"
USER = str(uuid.uuid4())


def payload(**overrides):
    """Return a fresh, whole-candidate job."""
    return {
        "id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "timeout": 60,
        "state": "accepted",
        **overrides,
    }


def gateway(tmp_path):
    """Build a gateway without connecting to any platform."""
    return Gateway("http://localhost:8000", "local", [USER], f"{EXAMPLE}:Evaluator", tmp_path)


def test_bundle_contains_sources_not_runtime_data(tmp_path):
    """Candidate upload excludes credentials, local datasets and runtime state."""
    (tmp_path / "solution.py").write_text("pass")
    for filename in ("config.yaml", ".env", "samples.csv", "secret.json"):
        (tmp_path / filename).write_text("SECRET")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/private.py").write_text("SECRET")
    target = tmp_path / "out"
    extract_candidate(pack_candidate(str(tmp_path)), target)
    assert [p.name for p in target.iterdir()] == ["solution.py"]
    with pytest.raises(ValueError):
        pack_candidate(str(tmp_path), ["../*.py"])


@pytest.mark.parametrize("filename", ["../escaped.py", "/escaped.py", "C:/escaped.py", "x\\escaped.py"])
def test_archive_rejects_unsafe_paths(tmp_path, filename):
    """Cross-platform extraction refuses paths outside the assigned worktree."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(filename, "pass")
    with pytest.raises(ValueError):
        extract_candidate(buf.getvalue(), tmp_path)


def test_journal_outbox_and_old_attempt_fencing(tmp_path):
    """Late progress/results cannot overwrite a new attempt or terminal result."""
    journal = Journal(tmp_path / "jobs.db")
    job = payload()
    journal.accept(job)
    old = Reporter(journal, job["id"])
    old.progress("one", 1, 3)
    seq = journal.get(job["id"])["seq"]
    old.progress("two", 2, 3)
    journal.acknowledge(job["id"], seq)
    assert journal.rows(pending_only=True)[0]["seq"] > seq
    job["attempt_id"] = str(uuid.uuid4())
    journal.accept(job)
    old.progress("stale")
    old.save_state({"external_id": "old"})
    journal.update(job["id"], "completed", {"score": 1}, job["attempt_id"])
    Reporter(journal, job["id"]).progress("too late")
    assert journal.get(job["id"])["state"] == "completed"
    assert json.loads(journal.get(job["id"])["saved"]) == {}


def test_identity_and_single_instance_lock(tmp_path):
    """Restart keeps the identity; a second CLI process cannot share its journal."""
    first = gateway(tmp_path)
    assert gateway(tmp_path).identity == first.identity
    with pytest.raises(ValueError):
        Gateway("http://another-platform", "local", [USER], f"{EXAMPLE}:Evaluator", tmp_path)
    with gateway_lease(tmp_path), pytest.raises(sqlite3.OperationalError), gateway_lease(tmp_path):
        pass
    with gateway_lease(tmp_path):
        pass


async def test_unknown_assignment_never_repeats_work(tmp_path):
    """A missing journal never turns an old server assignment into a fresh run."""
    worker = gateway(tmp_path)
    worker.start_process = Mock()
    job = payload()
    await worker.reconcile(None, job)
    assert worker.journal.get(job["id"])["state"] == "recovery_required"
    worker.start_process.assert_not_called()


async def test_restart_preserves_result_and_recovers_unknown_work(tmp_path):
    """Only the adapter recovery hook is eligible after restarting an active job."""
    first = gateway(tmp_path)
    active, completed = payload(), payload()
    for job in (active, completed):
        first.journal.accept(job)
    first.journal.update(completed["id"], "completed", {"metrics": {"mse": 1}})
    worker = gateway(tmp_path)
    worker.start_process = Mock()
    await worker.reconcile(None, active)
    await worker.reconcile(None, completed)
    worker.start_process.assert_not_called()
    assert worker.journal.get(active["id"])["state"] == "recovery_required"
    assert worker.journal.get(completed["id"])["state"] == "completed"


async def test_external_cancellation_requires_confirmation(tmp_path):
    """A platform stop does not claim an external scheduler job was cancelled."""
    worker = gateway(tmp_path)
    job = payload(state="cancel_requested")
    worker.journal.accept(job)
    Reporter(worker.journal, job["id"]).save_state({"external_id": "scheduler-1"})
    await worker.reconcile(None, job)
    assert worker.journal.get(job["id"])["state"] == "recovery_required"
    worker.adapter.cancel = AsyncMock(return_value=True)
    await worker.reconcile(None, job)
    assert worker.journal.get(job["id"])["state"] == "cancelled"


def test_offline_wait_does_not_consume_execution_timeout(tmp_path):
    """Only actual execution is timed; progress does not reset the budget."""
    worker = gateway(tmp_path)
    job = payload(timeout=0.01)
    worker.journal.accept(job)
    worker.enforce_deadlines()
    assert worker.journal.get(job["id"])["state"] == "accepted"
    reporter = Reporter(worker.journal, job["id"])
    reporter.progress("running")
    with worker.journal.connect() as db:
        db.execute("UPDATE jobs SET started=? WHERE id=?", (time.time() - 10, job["id"]))
    reporter.progress("still running")
    worker.enforce_deadlines()
    assert worker.journal.get(job["id"])["state"] == "failed"
    reporter.progress("late update")
    assert worker.journal.get(job["id"])["state"] == "failed"


async def test_dispatcher_sends_one_job_per_candidate(tmp_path, monkeypatch):
    """The local dataset loop is bypassed for whole-candidate remote evaluation."""
    cfg = RemoteEvaluatorConfig(gateway_ids=[str(uuid.uuid4())], contract=gateway(tmp_path).adapter.contract.model_dump())
    dispatcher = EvaluationDispatcher(cfg)
    assert set(dispatcher.get_metric_definitions()) == {"mse"}
    evaluate = AsyncMock(return_value=EvaluationResult(metrics={"mse": 2}, score=-2))
    monkeypatch.setattr("llm4ad.remote.client.evaluate_remote", evaluate)
    result = await dispatcher.dispatch_batch([SimpleNamespace(worktree=SimpleNamespace(path=str(tmp_path)))])
    assert result[0].score == -2
    evaluate.assert_awaited_once()


async def test_client_retries_same_id_and_waits_through_transport_error(tmp_path, monkeypatch):
    """Lost submit/status responses neither duplicate a job nor fail a candidate."""
    ids = []
    polls = 0

    async def submit(request):
        ids.append((await request.json())["id"])
        return web.json_response({"state": "queued"}, status=503 if len(ids) == 1 else 200)

    async def status(request):
        nonlocal polls
        polls += 1
        if polls == 1:
            return web.Response(status=503)
        return web.json_response({"state": "completed", "report": {"metrics": {"mse": 1}, "score": -1}})

    app = web.Application()
    app.router.add_post("/tasks/task-1/jobs", submit)
    app.router.add_get("/tasks/task-1/jobs/{job_id}", status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("LLM4AD_REMOTE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("LLM4AD_REMOTE_TOKEN", "test-token")
    monkeypatch.setenv("LLM4AD_REMOTE_TASK_ID", "task-1")
    (tmp_path / "solution.py").write_text("pass")
    config = RemoteEvaluatorConfig(gateway_ids=[str(uuid.uuid4())], contract={})
    real_sleep = asyncio.sleep
    monkeypatch.setattr("llm4ad.remote.client.asyncio.sleep", lambda _: real_sleep(0))
    try:
        result = await evaluate_remote(config, EvalContext(project_root=str(tmp_path)))
    finally:
        await runner.cleanup()
    assert result.score == -1
    assert len(ids) == 2 and len(set(ids)) == 1
    assert polls == 2
    assert not list((tmp_path / ".remote-evaluation").iterdir())


async def test_real_adapter_subprocess(tmp_path):
    """The shipping example executes real candidate source in a spawned worker."""
    worker = gateway(tmp_path)
    worktree = tmp_path / "candidate"
    worktree.mkdir()
    (worktree / "solution.py").write_text("def solve(x): return x*x\n")
    job = payload(project_root=str(worktree))
    worker.journal.accept(job)
    worker.start_process(job)
    try:
        for _ in range(100):
            if worker.journal.get(job["id"])["state"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.1)
        row = worker.journal.get(job["id"])
        assert row["state"] == "completed", row
        assert json.loads(row["report"])["score"] == 0
    finally:
        from llm4ad.remote.gateway import stop_process

        stop_process(worker.processes[job["id"]])
