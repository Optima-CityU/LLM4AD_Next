"""Local monitoring, safe evidence downloads and offline gateway live controls."""

import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from aiohttp import web

from llm4ad.gateway.sdk import gateway as gateway_module
from llm4ad.gateway.sdk.contracts import GatewayEvaluator, load_evaluator
from llm4ad.gateway.sdk.gateway import Gateway
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.lifecycle import execution_control, prepare_execution
from llm4ad.gateway.sdk.monitoring import artifact_path, job_detail, job_messages, list_jobs, summary
from llm4ad.gateway.sdk.runtime import GatewayRuntime
from llm4ad.gateway.sdk.service import GatewayService


def payload(**values):
    """Build a current local attempt without any device or platform dependency."""
    return {
        "id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "algorithm_name": "candidate-a",
        "timeout": 60,
        "project_root": "/private/workspace",
        "state": "accepted",
        **values,
    }


@pytest.fixture
def worker(tmp_path):
    """Exercise the same partial construction supported by existing SDK fixtures."""
    value = Gateway.__new__(Gateway)
    value.journal = Journal(tmp_path / "jobs.sqlite3")
    value.adapter = GatewayEvaluator()
    value.processes = {}
    value.restart_jobs = set()
    value.identity = {"id": "gateway"}
    value.url = "http://unused.invalid"
    value.capacity = 2
    value.cleanup_managed = True
    value.wakeup = asyncio.Event()
    return value


@pytest.fixture
def admission_service(worker, tmp_path):
    """Bind the real service admission and settings methods to a local gateway."""
    spec = "llm4ad.gateway.examples.basic.evaluator:Evaluator"
    runtime = GatewayRuntime(
        {
            "LLM4AD_SERVER_URL": worker.url,
            "LLM4AD_GATEWAY_NAME": "monitoring-fixture",
            "LLM4AD_ALLOWED_USER_IDS": str(uuid.uuid4()),
            "LLM4AD_GATEWAY_STATE_DIR": str(tmp_path),
            "LLM4AD_GATEWAY_CLEANUP_ENABLED": "false",
        },
        spec,
    )
    service = GatewayService(runtime)
    service.bound = runtime.bindings()
    service.gateways[service.bound[0].key] = worker
    worker.name = runtime.options["name"]
    worker.allowed_users = runtime.options["allowed_users"]
    worker.identity["secret"] = "synthetic-test-secret"
    worker.adapter = load_evaluator(spec)
    return service


def test_resume_accepting_wakes_only_when_paused(worker):
    """Repeated admission updates preserve sleep and never discard a real wakeup."""
    worker.resume_accepting()
    assert worker.admission_paused is False
    assert not worker.wakeup.is_set()
    worker.pause_accepting()
    worker.resume_accepting()
    assert worker.wakeup.is_set()
    worker.wakeup.clear()
    worker.resume_accepting()
    worker.resume_accepting()
    assert not worker.wakeup.is_set()
    worker.wakeup.set()
    worker.resume_accepting()
    assert worker.wakeup.is_set()


def test_resume_accepting_supports_partial_fixtures_without_wakeup(worker):
    """The synchronous toggle remains compatible with older partial constructors."""
    del worker.wakeup
    worker.resume_accepting()
    assert worker.admission_paused is False
    worker.pause_accepting()
    worker.resume_accepting()
    assert worker.admission_paused is False


def test_additive_migration_preserves_legacy_fields_and_unknown_dates(tmp_path):
    """Schema upgrades do not invent history or reset delivery and recovery state."""
    path = tmp_path / "legacy.sqlite3"
    job = payload()
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE jobs(id TEXT PRIMARY KEY,job TEXT NOT NULL,state TEXT NOT NULL,report TEXT NOT NULL,"
            "seq INTEGER NOT NULL DEFAULT 0,ack INTEGER NOT NULL DEFAULT -1,started REAL,saved TEXT NOT NULL DEFAULT '{}')"
        )
        db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",
            (job["id"], json.dumps(job), "running", '{"message":"old"}', 9, 4, 123.5, '{"external_id":"keep"}'),
        )
    journal = Journal(path)
    row = journal.get(job["id"])
    assert (row["seq"], row["ack"], row["started"], json.loads(row["saved"])) == (9, 4, 123.5, {"external_id": "keep"})
    assert row["created_at"] is row["updated_at"] is row["finished_at"] is None
    assert Journal(path).get(job["id"]) == row
    assert list_jobs(journal)["items"][0]["created_at"] is None


def test_timestamps_track_observations_without_resetting_timers(worker, monkeypatch):
    """Acceptance, observations and completion retain distinct meaningful dates."""
    now = [100.0]
    monkeypatch.setattr("llm4ad.gateway.sdk.journal.time.time", lambda: now[0])
    job = payload()
    journal = worker.journal
    journal.accept(job)
    first = journal.get(job["id"])
    now[0] = 110.0
    journal.accept(job)
    assert journal.get(job["id"]) == first
    reporter = Reporter(journal, job["id"])
    reporter.progress("Started", 1, 4)
    assert journal.get(job["id"])["started"] == 110.0
    now[0] = 120.0
    reporter.metadata({"phase": "measure"})
    reporter.message("Measured")
    journal.update(job["id"], "running", {"message": "Still running"}, job["attempt_id"])
    row = journal.get(job["id"])
    assert (row["created_at"], row["updated_at"], row["started"], row["finished_at"]) == (100.0, 120.0, 110.0, None)
    now[0] = 130.0
    journal.update(job["id"], "completed", {"score": 4}, job["attempt_id"])
    terminal = journal.get(job["id"])
    assert terminal["finished_at"] == terminal["updated_at"] == 130.0
    now[0] = 140.0
    reporter.progress("Too late")
    journal.update(job["id"], "failed", {}, job["attempt_id"])
    assert journal.get(job["id"]) == terminal
    next_job = {**job, "attempt_id": str(uuid.uuid4())}
    journal.accept(next_job)
    reporter.message("Old attempt")
    row = journal.get(job["id"])
    assert row["created_at"] == row["updated_at"] == 140.0
    assert row["finished_at"] is row["started"] is None


def test_summary_counts_and_filtered_current_attempts(worker):
    """State counts and pagination reflect current attempts rather than message history."""
    journal = worker.journal
    jobs = [payload(algorithm_name=f"algorithm-{index}") for index in range(9)]
    for job in jobs:
        journal.accept(job)
    for job, state in zip(
        jobs[1:7], ["running", "recovery_required", "completed", "failed", "cancelled", "cancel_requested"], strict=True
    ):
        journal.update(job["id"], state, {"score": 3}, job["attempt_id"])
    journal.update(jobs[7]["id"], "completed", {"success": False}, jobs[7]["attempt_id"])
    journal.acknowledge(jobs[3]["id"], journal.get(jobs[3]["id"])["seq"])
    journal.accept({**jobs[8], "attempt_id": str(uuid.uuid4()), "algorithm_name": "new_%_candidate"})
    counts = summary(journal)
    assert counts == {
        "pending": 2,
        "running": 1,
        "recovery_required": 1,
        "success": 1,
        "failed": 2,
        "cancelled": 1,
        "cancelling": 1,
        "pending_upload": 3,
        "total": 9,
    }
    assert list_jobs(journal, state="success")["items"][0]["job_id"] == jobs[3]["id"]
    assert list_jobs(journal, state="pending_upload")["total"] == 3
    assert list_jobs(journal, search="_%_")["total"] == 1
    assert list_jobs(journal, search=jobs[2]["task_id"])["total"] == 1
    assert list_jobs(journal, search=jobs[2]["attempt_id"])["total"] == 1
    first, second = list_jobs(journal, page_size=4), list_jobs(journal, page=2, page_size=4)
    assert first["total"] == second["total"] == 9
    assert not ({row["job_id"] for row in first["items"]} & {row["job_id"] for row in second["items"]})
    assert list_jobs(journal, page=10)["items"] == []
    assert list_jobs(journal, page_size=2000)["total"] == 9
    assert set(first["items"][0]) == {
        "job_id",
        "attempt_id",
        "task_id",
        "algorithm_name",
        "state",
        "message",
        "completed",
        "total",
        "score",
        "duration_ms",
        "elapsed_ms",
        "started_at",
        "finished_at",
        "phase",
        "lifecycle_stages",
        "created_at",
        "updated_at",
        "started",
        "pending_upload",
        "can_cancel",
    }


def test_detail_is_safe_rich_paginated_and_read_only(worker, tmp_path, monkeypatch):
    """Browser projections retain measurements while hiding operational state and local paths."""
    journal = worker.journal
    job = payload(environment={"KEY": "private-env"}, parameters={"token": "private-param"}, identity={"secret": "private-id"})
    journal.accept(job)
    reporter = Reporter(journal, job["id"])
    reporter.save_state({"external_id": "private-recovery"})
    reporter.metadata({"device": "fixture", "phase": {"samples": 3}, "environment": {"KEY": "private-meta-env"}})
    for index in range(5):
        reporter.message(f"message-{index}", data={"index": index, "saved_state": {"key": "private-message"}})
    evidence = tmp_path / "report.txt"
    evidence.write_text("immutable evidence")
    reporter.artifact("reports/report.txt", evidence, "text/plain", description="Measurements")
    journal.update(
        job["id"],
        "completed",
        {
            "score": 7,
            "success": True,
            "duration_ms": 300,
            "metadata": {
                "result_details": {"samples": [1, 2, 3], "saved": {"handle": "private-report"}},
                "artifacts": [{"name": "reports/report.txt", "object_key": "private-object", "path": "/private/artifact"}],
                "local_path": "/private/metadata",
                "environment": {"KEY": "private-result-env"},
                "evaluation_messages": [{"message": "Should use paginated table"}],
            },
        },
        job["attempt_id"],
    )
    before = journal.get(job["id"])
    monkeypatch.setattr(gateway_module, "load_evaluator", Mock(side_effect=AssertionError("Polling constructed adapter")))
    monkeypatch.setattr(gateway_module, "worker_active", Mock(side_effect=AssertionError("Polling inspected native process")))
    detail = job_detail(journal, job["id"], job["attempt_id"], messages_page=2, messages_page_size=2)
    assert detail["algorithm_name"] == "candidate-a"
    assert detail["report"]["metadata"]["result_details"] == {"samples": [1, 2, 3]}
    assert detail["report"]["metadata"]["device"] == "fixture"
    assert detail["metadata"] == detail["report"]["metadata"]
    assert detail["messages_total"] == 5 and detail["messages_pages"] == 3
    assert isinstance(detail["messages"][0]["created_at"], float)
    assert [message["message"] for message in detail["messages"]] == ["message-2", "message-3"]
    assert detail["messages"][0]["data"] == {"index": 2}
    assert detail["artifacts"][0]["available"] is True
    assert detail["artifacts"][0]["uploaded"] is False
    assert detail["artifacts"][0]["description"] == "Measurements"
    assert detail["can_cancel"] is False
    encoded = json.dumps(detail)
    for private in (
        "private-",
        "/private/",
        "object_key",
        "evaluation_messages",
        "environment",
        "parameters",
        "identity",
        "saved",
    ):
        assert private not in encoded
    assert journal.get(job["id"]) == before
    summary(journal)
    list_jobs(journal)
    assert journal.get(job["id"]) == before
    with journal.connect() as db:
        db.execute("UPDATE artifacts SET object_key='private-receipt'")
    path = artifact_path(journal, job["id"], job["attempt_id"], "reports/report.txt")
    path.unlink()
    detail = job_detail(journal, job["id"], job["attempt_id"])
    assert detail["artifacts"][0]["available"] is False
    assert detail["artifacts"][0]["uploaded"] is True
    assert detail["report"]["score"] == 7


@pytest.mark.parametrize("page,size", [(0, 20), (1, 0), (-1, 20), (True, 20), (1, 1.5)])
def test_invalid_pagination_is_rejected(worker, page, size):
    """Invalid pages cannot silently bypass the expected pagination contract."""
    with pytest.raises(ValueError):
        list_jobs(worker.journal, page=page, page_size=size)
    with pytest.raises(ValueError):
        job_detail(worker.journal, "missing", "attempt", messages_page=page, messages_page_size=size)


def test_stale_detail_and_evidence_are_rejected(worker, tmp_path):
    """An old browser selection cannot inspect or download a replacement attempt."""
    job = payload()
    worker.journal.accept(job)
    file = tmp_path / "evidence"
    file.write_text("original")
    Reporter(worker.journal, job["id"]).artifact("evidence.txt", file)
    worker.journal.accept({**job, "attempt_id": str(uuid.uuid4())})
    with pytest.raises(ValueError, match="current"):
        job_detail(worker.journal, job["id"], job["attempt_id"])
    with pytest.raises(ValueError, match="current"):
        artifact_path(worker.journal, job["id"], job["attempt_id"], "evidence.txt")
    with pytest.raises(LookupError):
        job_detail(worker.journal, "missing", "attempt")


@pytest.mark.parametrize("tampering", ["content", "size", "path", "hash", "symlink", "parent_symlink", "root_symlink"])
def test_artifacts_reject_tampering_and_symlinks(worker, tmp_path, tampering):
    """A database entry alone never authorizes arbitrary local file downloads."""
    job = payload()
    worker.journal.accept(job)
    source = tmp_path / "original"
    source.write_text("original")
    Reporter(worker.journal, job["id"]).artifact("evidence.txt", source)
    item = worker.journal.artifacts(job["id"], job["attempt_id"])[0]
    path = Path(item["path"])
    assert artifact_path(worker.journal, job["id"], job["attempt_id"], "evidence.txt") == path
    if tampering == "content":
        path.write_text("modified")
    elif tampering == "size":
        path.write_text("longer changed content")
    elif tampering in {"path", "hash"}:
        with worker.journal.connect() as db:
            if tampering == "path":
                db.execute("UPDATE artifacts SET path=?", (str(source),))
            else:
                db.execute("UPDATE artifacts SET sha256='../unsafe'")
    elif tampering == "symlink":
        path.unlink()
        path.symlink_to(source)
    else:
        directory = path.parent if tampering == "parent_symlink" else path.parent.parent
        moved = directory.with_name("moved-artifacts")
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError):
        artifact_path(worker.journal, job["id"], job["attempt_id"], "evidence.txt")


@pytest.mark.parametrize("name", ["../original", "/original", "x\\original", "", "unknown.txt"])
def test_artifact_name_must_be_registered_and_relative(worker, name):
    """Only explicit immutable artifact names can be served."""
    job = payload()
    worker.journal.accept(job)
    with pytest.raises((ValueError, FileNotFoundError)):
        artifact_path(worker.journal, job["id"], job["attempt_id"], name)


async def test_offline_cancel_is_durable_before_hooks_and_uses_authoritative_job(worker):
    """A local stop uses accepted job data and requires no HTTP session or adapter reload."""
    job = payload(algorithm_name="source-algorithm")
    worker.journal.accept(job)
    seen = []

    async def before(actual, reporter, operation):
        assert execution_control(worker.journal, job["id"], job["attempt_id"])["stop_requested"] == "cancel"
        assert actual.project_root == job["project_root"]
        seen.append(operation)

    worker.adapter.before_task = before
    worker.request = AsyncMock(side_effect=AssertionError("Cancellation used HTTP"))
    result = await worker.request_cancel(job["id"], job["attempt_id"])
    assert result == {"job_id": job["id"], "attempt_id": job["attempt_id"], "state": "cancelled", "status": "cancelled"}
    assert seen == ["cancel"]
    worker.request.assert_not_awaited()
    assert worker.journal.get(job["id"])["finished_at"] is not None
    assert list_jobs(worker.journal)["items"][0]["algorithm_name"] == "source-algorithm"


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
async def test_cancel_terminal_and_stale_attempt_are_noops(worker, terminal):
    """Finished results remain intact and stale stop requests never affect a replacement."""
    job = payload()
    worker.journal.accept(job)
    worker.journal.update(job["id"], terminal, {"score": 42}, job["attempt_id"])
    before = worker.journal.get(job["id"])
    result = await worker.request_cancel(job["id"], job["attempt_id"])
    assert result["status"] == "already_finished"
    await worker.reconcile(None, {**job, "state": "cancel_requested"})
    assert worker.journal.get(job["id"]) == before
    assert not execution_control(worker.journal, job["id"], job["attempt_id"])
    replacement = {**job, "attempt_id": str(uuid.uuid4())}
    worker.journal.accept(replacement)
    before = worker.journal.get(job["id"])
    with pytest.raises(ValueError, match="current"):
        await worker.request_cancel(job["id"], job["attempt_id"])
    assert worker.journal.get(job["id"]) == before
    assert not execution_control(worker.journal, job["id"], replacement["attempt_id"])
    with pytest.raises(LookupError):
        await worker.request_cancel("missing", "attempt")


@pytest.mark.parametrize("confirmed", [True, False])
async def test_concurrent_cancel_and_reconcile_share_hooks(worker, confirmed):
    """Competing platform and browser stops share one external cancellation operation."""
    job = payload()
    worker.journal.accept(job)
    Reporter(worker.journal, job["id"]).save_state({"external_id": "scheduler-1"})
    entered, release = asyncio.Event(), asyncio.Event()

    async def cancel(actual, saved):
        assert actual.id == job["id"] and saved == {"external_id": "scheduler-1"}
        entered.set()
        await release.wait()
        return confirmed

    worker.adapter.cancel = AsyncMock(side_effect=cancel)
    worker.adapter.before_task = AsyncMock()
    worker.adapter.after_task = AsyncMock()
    first = asyncio.create_task(worker.request_cancel(job["id"], job["attempt_id"]))
    await asyncio.wait_for(entered.wait(), 2)
    assert summary(worker.journal)["cancelling"] == 1
    assert list_jobs(worker.journal)["items"][0]["can_cancel"] is False
    second = asyncio.create_task(worker.request_cancel(job["id"], job["attempt_id"]))
    remote = asyncio.create_task(worker.reconcile(None, job))
    await asyncio.sleep(0)
    release.set()
    result, duplicate, _ = await asyncio.gather(first, second, remote)
    assert result == duplicate
    assert result["status"] == ("cancelled" if confirmed else "cancelling")
    worker.adapter.cancel.assert_awaited_once()
    worker.adapter.before_task.assert_awaited_once()
    worker.adapter.after_task.assert_awaited_once()


async def test_outbox_cleanup_and_browser_cancel_share_external_hooks(worker):
    """An outbox supersession cleanup cannot duplicate a simultaneous dashboard stop."""
    job = payload()
    worker.journal.accept(job)
    Reporter(worker.journal, job["id"]).save_state({"external_id": "scheduler-1"})
    entered, release = asyncio.Event(), asyncio.Event()

    async def cancel(actual, saved):
        entered.set()
        await release.wait()
        return True

    worker.adapter.cancel = AsyncMock(side_effect=cancel)
    worker.adapter.before_task = AsyncMock()
    worker.adapter.after_task = AsyncMock()
    outbox = asyncio.create_task(worker.cancel_execution(job))
    await asyncio.wait_for(entered.wait(), 2)
    browser = asyncio.create_task(worker.request_cancel(job["id"], job["attempt_id"]))
    await asyncio.sleep(0)
    release.set()
    stopped, result = await asyncio.gather(outbox, browser)
    assert stopped and result["status"] == "cancelled"
    worker.adapter.cancel.assert_awaited_once()
    worker.adapter.before_task.assert_awaited_once()
    worker.adapter.after_task.assert_awaited_once()


async def test_external_handle_published_after_cleanup_requires_confirmation(worker):
    """Finalizers cannot create an unconfirmed external handle behind a positive stop receipt."""
    job = payload()
    worker.journal.accept(job)

    async def after(actual, reporter, operation, **kwargs):
        reporter.save_state({"external_id": "late-handle"})

    worker.adapter.after_task = after
    result = await worker.request_cancel(job["id"], job["attempt_id"])
    assert result["status"] == "cancelling"
    assert worker.journal.get(job["id"])["finished_at"] is None
    worker.adapter.cancel = AsyncMock(return_value=True)
    await worker.reconcile(None, job)
    worker.adapter.cancel.assert_awaited_once()
    assert worker.journal.get(job["id"])["state"] == "cancelled"


async def test_lost_caller_does_not_abort_durable_cancellation(worker):
    """Closing the dashboard during a stop cannot cancel the shared cleanup task."""
    job = payload()
    worker.journal.accept(job)
    Reporter(worker.journal, job["id"]).save_state({"external_id": "scheduler-1"})
    entered, release = asyncio.Event(), asyncio.Event()

    async def cancel(actual, saved):
        entered.set()
        await release.wait()
        return True

    worker.adapter.cancel = AsyncMock(side_effect=cancel)
    task = asyncio.create_task(worker.request_cancel(job["id"], job["attempt_id"]))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert execution_control(worker.journal, job["id"], job["attempt_id"])["stop_requested"] == "cancel"
    shared = worker._cancellations[(job["id"], job["attempt_id"])]
    release.set()
    await asyncio.wait_for(asyncio.shield(shared), 2)
    assert worker.journal.get(job["id"])["state"] == "cancelled"


async def test_failed_hook_preserves_stop_and_external_uncertainty(worker):
    """A cleanup failure remains visible and retryable rather than falsely final."""
    job = payload()
    worker.journal.accept(job)
    Reporter(worker.journal, job["id"]).save_state({"external_id": "scheduler-1"})
    worker.adapter.before_task = AsyncMock(side_effect=RuntimeError("Backend unavailable"))
    result = await worker.request_cancel(job["id"], job["attempt_id"])
    assert result["status"] == "cancelling"
    assert worker.journal.get(job["id"])["state"] == "recovery_required"
    assert worker.journal.get(job["id"])["finished_at"] is None
    assert execution_control(worker.journal, job["id"], job["attempt_id"])["stop_requested"] == "cancel"
    worker.adapter.before_task = AsyncMock()
    worker.adapter.cancel = AsyncMock(return_value=True)
    await worker.reconcile(None, job)
    assert worker.journal.get(job["id"])["state"] == "cancelled"


async def test_unknown_legacy_execution_never_falsely_confirms_cancel(worker):
    """An unknown old worker with no recovery handle requires explicit resolution."""
    job = payload()
    worker.journal.accept(job)
    worker.journal.update(job["id"], "running", {})
    worker.adapter.before_task = AsyncMock()
    result = await worker.request_cancel(job["id"], job["attempt_id"])
    assert result["status"] == "cancelling"
    assert execution_control(worker.journal, job["id"], job["attempt_id"])["status"] == "unknown"
    worker.adapter.before_task.assert_not_awaited()


async def test_alive_process_prevents_final_confirmation(worker, monkeypatch):
    """Even a positive external receipt cannot hide a local process that remains alive."""
    job = payload()
    worker.journal.accept(job)
    process = Mock()
    process.is_alive.return_value = True
    worker.processes[job["id"]] = process
    worker.cancel_execution = AsyncMock(return_value=True)
    monkeypatch.setattr(gateway_module, "worker_active", lambda *_: False)
    result = await worker.request_cancel(job["id"], job["attempt_id"])
    assert result["status"] == "cancelling"
    assert worker.journal.get(job["id"])["finished_at"] is None


async def test_result_committed_during_cancellation_is_not_overwritten(worker):
    """A result that wins a concurrent transaction remains authoritative after hooks."""
    job = payload()
    worker.journal.accept(job)

    async def before(actual, reporter, operation):
        worker.journal.update(job["id"], "completed", {"score": 19}, job["attempt_id"])

    worker.adapter.before_task = before
    result = await worker.request_cancel(job["id"], job["attempt_id"])
    assert result["status"] == "already_finished"
    assert json.loads(worker.journal.get(job["id"])["report"])["score"] == 19


async def test_replacement_during_cancellation_is_fenced(worker):
    """Late cancellation completion never writes into a replacement job attempt."""
    job = payload()
    worker.journal.accept(job)
    replacement = {**job, "attempt_id": str(uuid.uuid4())}

    async def before(actual, reporter, operation):
        worker.journal.accept(replacement)

    worker.adapter.before_task = before
    with pytest.raises(ValueError, match="current"):
        await worker.request_cancel(job["id"], job["attempt_id"])
    assert worker.journal.get(job["id"])["state"] == "accepted"
    assert not execution_control(worker.journal, job["id"], replacement["attempt_id"])


def test_deadline_does_not_replace_a_cancellation_stop(worker):
    """The timeout watchdog cannot change the durable reason while cancellation awaits."""
    job = payload(timeout=0.01)
    worker.journal.accept(job)
    assert prepare_execution(worker.journal, job["id"], job["attempt_id"])
    worker.journal.update(job["id"], "running", {}, job["attempt_id"])
    with worker.journal.connect() as db:
        db.execute("UPDATE jobs SET started=?", (time.time() - 10,))
        db.execute("UPDATE execution_controls SET stop_requested='cancel'")
    worker.enforce_deadlines()
    assert worker.journal.get(job["id"])["state"] == "running"
    assert execution_control(worker.journal, job["id"], job["attempt_id"])["stop_requested"] == "cancel"


@asynccontextmanager
async def platform(handler):
    """Serve an ephemeral fake platform over real aiohttp connections."""
    app = web.Application()
    app.router.add_route("*", "/{suffix:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    finally:
        await runner.cleanup()


async def test_duplicate_resume_preserves_offline_retry_and_live_settings_wake(worker, admission_service, monkeypatch):
    """Controller admission refreshes preserve the ten-second retry; changed settings wake it."""
    requests = []
    retry_waits = asyncio.Queue()
    original_wait = worker.wakeup.wait

    async def observed_wait():
        retry_waits.put_nowait(None)
        return await original_wait()

    async def refresh_admission():
        while True:
            admission_service.set_admission(False)
            await asyncio.sleep(0.5)

    async def handler(request):
        assert request.path == "/register"
        requests.append((time.monotonic(), await request.json()))
        return web.Response(status=503)

    monkeypatch.setattr(worker.wakeup, "wait", observed_wait)
    async with platform(handler) as url:
        worker.url = url
        transport = asyncio.create_task(worker.run())
        refresh = None
        try:
            await asyncio.wait_for(retry_waits.get(), 2)
            refresh = asyncio.create_task(refresh_admission())
            await asyncio.wait_for(retry_waits.get(), 13)
            assert len(requests) == 2
            assert requests[1][0] - requests[0][0] >= 9.8
            assert not worker.wakeup.is_set()

            users = [str(uuid.uuid4())]
            await admission_service.apply_live_settings(
                name="updated-fixture", allowed_users=users, concurrency=3,
                cleanup_enabled=False, cleanup_interval=60, cleanup_keep_seconds=30,
            )
            assert worker.wakeup.is_set()
            await asyncio.wait_for(retry_waits.get(), 2)
            assert len(requests) == 3
            assert requests[2][0] - requests[1][0] < 2
            assert requests[2][1]["name"] == "updated-fixture"
            assert requests[2][1]["allowed_user_ids"] == users
            assert requests[2][1]["capacity"] == 3
        finally:
            transport.cancel()
            if refresh:
                refresh.cancel()
            await asyncio.gather(transport, *([refresh] if refresh else []), return_exceptions=True)


async def test_paused_tick_still_heartbeats_reconciles_and_flushes_outbox(worker):
    """Admission pause affects claims while existing work still reaches the platform."""
    calls = []
    job = payload()
    worker.journal.accept(job)
    worker.journal.update(job["id"], "completed", {"score": 3}, job["attempt_id"])

    async def handler(request):
        calls.append(request.path)
        if request.path.endswith("/heartbeat"):
            return web.json_response({"jobs": [job]})
        if request.path.endswith("/report"):
            report = await request.json()
            return web.json_response({"ack": report["sequence"], "state": "completed"})
        if request.path.endswith("/claim"):
            return web.json_response(None)
        raise AssertionError(request.path)

    worker.pause_accepting()
    worker.reconcile = AsyncMock()
    async with platform(handler) as url, aiohttp.ClientSession() as session:
        worker.url = url
        await worker.tick(session)
        assert not any(path.endswith("/claim") for path in calls)
        assert any(path.endswith("/heartbeat") for path in calls)
        assert any(path.endswith("/report") for path in calls)
        worker.reconcile.assert_awaited_once_with(session, job)
        assert not worker.journal.rows(pending_only=True)
        worker.resume_accepting()
        assert worker.wakeup.is_set()
        await worker.tick(session)
        assert any(path.endswith("/claim") for path in calls)
    assert worker.transport_snapshot()["heartbeat"]["last_success"] is not None


async def test_transport_records_safe_channel_errors_and_expected_conflicts(worker):
    """HTTP conflicts describe attempt supersession without marking platform disconnection."""
    status = [200]

    async def handler(request):
        return web.json_response({}, status=status[0], headers={"X-Private": "do-not-expose"})

    async with platform(handler) as url, aiohttp.ClientSession() as session:
        worker.url = url
        await worker.request(session, "POST", "/register")
        registration = worker.transport_snapshot()["registration"]["last_success"]
        status[0] = 503
        with pytest.raises(aiohttp.ClientResponseError):
            await worker.request(session, "POST", "/register")
        snapshot = worker.transport_snapshot()
        assert worker.transport == snapshot
        assert snapshot["registration"] == {
            "last_success": registration,
            "last_error": {"type": "ClientResponseError", "status": 503},
        }
        assert snapshot["last_error"] == "ClientResponseError"
        assert "do-not-expose" not in json.dumps(snapshot) and url not in json.dumps(snapshot)
        status[0] = 200
        await worker.request(session, "POST", "/gateway/heartbeat")
        assert worker.transport_snapshot()["heartbeat"]["last_error"] is None
        status[0] = 409
        with pytest.raises(aiohttp.ClientResponseError):
            await worker.request(session, "POST", "/gateway/jobs/job/report")
        assert worker.transport_snapshot()["last_error"] is None
        assert worker.transport_snapshot()["heartbeat"]["last_error"] is None


async def test_transport_connection_error_has_no_endpoint_or_credentials(worker):
    """Exception type is sufficient for browser transport diagnostics."""
    session = Mock()
    session.request.side_effect = OSError("http://user:password@private-host SECRET")
    with pytest.raises(OSError):
        await worker.request(session, "POST", "/gateway/heartbeat")
    snapshot = worker.transport_snapshot()
    assert snapshot["heartbeat"]["last_error"] == {"type": "OSError"}
    assert "SECRET" not in json.dumps(snapshot) and "password" not in json.dumps(snapshot)


async def test_sse_snapshot_tracks_reconnection_and_shutdown(worker, monkeypatch):
    """SSE failure and recovery remain independent from successful heartbeat transport."""
    connected, finish, reconnect = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requests = []
    sleep = asyncio.sleep

    async def backoff(delay):
        if delay == 2:
            reconnect.set()
            await sleep(0.01)
        else:
            await sleep(delay)

    async def handler(request):
        requests.append(request.path)
        if len(requests) == 1:
            return web.Response(status=503)
        stream = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await stream.prepare(request)
        await stream.write(b"event: poll\ndata: {}\n\n")
        connected.set()
        await finish.wait()
        return stream

    monkeypatch.setattr(gateway_module.asyncio, "sleep", backoff)
    async with platform(handler) as url, aiohttp.ClientSession() as session:
        worker.url = url
        task = asyncio.create_task(worker.notifications(session))
        try:
            await asyncio.wait_for(reconnect.wait(), 2)
            snapshot = worker.transport_snapshot()["notifications"]
            assert snapshot["reconnecting"] and not snapshot["connected"]
            assert snapshot["last_error"] == {"type": "ClientResponseError", "status": 503}
            await asyncio.wait_for(connected.wait(), 2)
            await asyncio.wait_for(worker.wakeup.wait(), 2)
            assert worker.transport_snapshot()["notifications"] == {
                "connected": True,
                "reconnecting": False,
                "last_error": None,
            }
            assert worker.transport_snapshot()["last_error"] is None
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            finish.set()
        assert worker.transport_snapshot()["notifications"]["connected"] is False
        assert worker.transport_snapshot()["notifications"]["reconnecting"] is False


def test_monitoring_projects_actual_times_and_observed_lifecycle(worker, monkeypatch):
    """Running time uses actual execution start while absent legacy dates stay unknown."""
    job = payload()
    journal = worker.journal
    journal.accept(job)
    phase = {"operation": "evaluate", "stage": "preparing", "status": "running", "started_at": 20, "finished_at": None}
    Reporter(journal, job["id"]).metadata({"lifecycle": phase, "lifecycle_stages": [phase]})
    with journal.connect() as db:
        db.execute("UPDATE jobs SET state='running',created_at=10,started=20,updated_at=24")
    monkeypatch.setattr("llm4ad.gateway.sdk.monitoring.time.time", lambda: 25)
    item = list_jobs(journal)["items"][0]
    assert item["started_at"] == item["started"] == 20
    assert item["finished_at"] is item["duration_ms"] is None
    assert item["elapsed_ms"] == 5000
    assert item["phase"] == phase and item["lifecycle_stages"] == [phase]
    journal.update(job["id"], "completed", {"duration_ms": 750}, job["attempt_id"])
    detail = job_detail(journal, job["id"], job["attempt_id"])
    assert detail["started_at"] == 20 and detail["finished_at"] == 25
    assert detail["elapsed_ms"] == 5000 and detail["duration_ms"] == 750
    assert detail["phase"] == phase
    with journal.connect() as db:
        db.execute("UPDATE jobs SET started=NULL,finished_at=NULL,report='{}'")
        db.execute("DELETE FROM observations")
    legacy = job_detail(journal, job["id"], job["attempt_id"])
    assert legacy["started_at"] is legacy["finished_at"] is legacy["elapsed_ms"] is legacy["duration_ms"] is None
    assert legacy["created_at"] == 10 and legacy["updated_at"] == 25


def test_message_tail_cursors_filters_and_attempt_fences(worker, monkeypatch):
    """SQLite sequence windows never repeat records or scan an unbounded event history."""
    journal = worker.journal
    job, other = payload(), payload()
    journal.accept(job)
    journal.accept(other)
    with journal.connect() as db:
        for index in range(250):
            message = "literal_%\\ message" if index == 10 else f"event-{index}"
            db.execute(
                "INSERT INTO messages(job_id,attempt_id,created_at,level,kind,message,data) VALUES(?,?,?,?,?,?,?)",
                (job["id"], job["attempt_id"], index, "error" if index % 10 == 0 else "info", "message", message,
                 json.dumps({"index": index, "saved_state": {"handle": "PRIVATE"}, "path": "/private/log"})),
            )
            db.execute(
                "INSERT INTO messages(job_id,attempt_id,created_at,level,kind,message,data) VALUES(?,?,?,?,?,?,?)",
                (other["id"], other["attempt_id"], index, "info", "message", "OTHER", "{}"),
            )
    monkeypatch.setattr(journal, "observations", Mock(side_effect=AssertionError("Read the message table with bounded SQL")))
    tail = job_messages(journal, job["id"], job["attempt_id"])
    assert [item["data"]["index"] for item in tail["items"]] == list(range(150, 250))
    assert tail["total"] == 250 and tail["has_older"] and not tail["has_newer"]
    assert tail["first_sequence"] == tail["items"][0]["sequence"]
    assert tail["last_sequence"] == tail["items"][-1]["sequence"]
    history = job_messages(journal, job["id"], job["attempt_id"], before_sequence=tail["first_sequence"])
    assert [item["data"]["index"] for item in history["items"]] == list(range(50, 150))
    first = job_messages(journal, job["id"], job["attempt_id"], before_sequence=history["first_sequence"])
    assert [item["data"]["index"] for item in first["items"]] == list(range(50))
    assert not first["has_older"] and first["has_newer"]
    assert not {item["sequence"] for item in history["items"]} & {item["sequence"] for item in tail["items"]}
    Reporter(journal, job["id"]).message("next")
    incremental = job_messages(journal, job["id"], job["attempt_id"], after_sequence=tail["last_sequence"])
    assert [item["message"] for item in incremental["items"]] == ["next"]
    empty = job_messages(journal, job["id"], job["attempt_id"], after_sequence=incremental["last_sequence"])
    assert empty["items"] == [] and empty["has_older"] and not empty["has_newer"]
    assert empty["first_sequence"] is empty["last_sequence"] is None
    errors = job_messages(journal, job["id"], job["attempt_id"], level="ERROR", limit=3)
    assert errors["total"] == 25 and [item["data"]["index"] for item in errors["items"]] == [220, 230, 240]
    literal = job_messages(journal, job["id"], job["attempt_id"], search="LITERAL_%\\")
    assert literal["total"] == 1 and literal["items"][0]["data"] == {"index": 10}
    assert "PRIVATE" not in json.dumps(tail) and "/private/" not in json.dumps(tail)
    replacement = {**job, "attempt_id": str(uuid.uuid4())}
    journal.accept(replacement)
    assert job_messages(journal, job["id"], replacement["attempt_id"])["total"] == 0
    with pytest.raises(ValueError, match="current"):
        job_messages(journal, job["id"], job["attempt_id"])


@pytest.mark.parametrize("options", [
    {"after_sequence": -1}, {"before_sequence": True}, {"after_sequence": 1, "before_sequence": 2},
    {"limit": 0}, {"limit": 1001}, {"limit": 1.5},
])
def test_message_cursor_validation_precedes_queries(worker, options):
    """Malformed cursor requests cannot weaken the attempt fence or widen reads."""
    with pytest.raises(ValueError):
        job_messages(worker.journal, "missing", "attempt", **options)


@pytest.mark.parametrize("known_start", [False, True])
def test_private_lifecycle_finishes_after_terminal_report_without_rewriting_result(worker, monkeypatch, known_start):
    """Post-cancellation cleanup and total hook time remain visible after report fencing."""
    from llm4ad.gateway.sdk.local_logs import LocalLogStore

    job = payload()
    journal = worker.journal
    journal.accept(job)
    journal.update(job["id"], "cancelled", {"duration_ms": 500, "metadata": {"lifecycle": {"stage": "evaluating"}}}, job["attempt_id"])
    with journal.connect() as db:
        db.execute("UPDATE jobs SET started=?,finished_at=110,updated_at=999", (100 if known_start else None,))
    before = journal.get(job["id"])
    phase = {
        "operation_id": "cancel-1", "operation": "cancel", "stage": "finalizing", "status": "completed",
        "started_at": 110, "finished_at": 120, "duration_ms": 10000, "elapsed_ms": 20000,
        "failed_stage": None,
    }
    store = LocalLogStore(journal)
    store.record_phase(job["id"], job["attempt_id"], {
        **phase, "status": "running", "finished_at": None, "duration_ms": 0, "elapsed_ms": 10000,
    })
    monkeypatch.setattr("llm4ad.gateway.sdk.monitoring.time.time", lambda: 115)
    finalizing = job_detail(journal, job["id"], job["attempt_id"])
    assert finalizing["finished_at"] is None and finalizing["elapsed_ms"] == 15000
    assert finalizing["duration_ms"] == 500
    store.record_phase(job["id"], job["attempt_id"], phase)
    detail = job_detail(journal, job["id"], job["attempt_id"])
    assert detail["phase"] == phase
    assert detail["metadata"]["lifecycle"] == phase
    assert detail["report"]["metadata"]["lifecycle_stages"] == [phase]
    assert detail["duration_ms"] == 500 and detail["elapsed_ms"] == 20000
    assert detail["started_at"] == (100 if known_start else None)
    assert detail["finished_at"] == 120
    assert journal.get(job["id"]) == before
    listed = list_jobs(journal)["items"][0]
    assert listed["phase"] == phase and listed["elapsed_ms"] == 20000
