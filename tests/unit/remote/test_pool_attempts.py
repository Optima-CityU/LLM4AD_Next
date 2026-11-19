"""Dynamic pooled attempts use one execution and expire safely while offline."""

import asyncio
import json
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from llm4ad.gateway.sdk import gateway as gateway_module
from llm4ad.gateway.sdk.gateway import AttemptLeaseExpiredError, Gateway
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.lifecycle import execution_control

pytestmark = pytest.mark.unit


def make_gateway(tmp_path, *, managed=True, hang=False, fail=False):
    """Create an ordinary evaluator with observable calls and synthetic external state."""
    source = tmp_path / "candidate"
    source.mkdir()
    (source / "solution.py").write_text("pass\n")
    adapter = tmp_path / "adapter.py"
    adapter.write_text('''
import asyncio
from pathlib import Path
from llm4ad.gateway.sdk import GatewayEvaluator, EvaluationContract, EvaluationResult, Metric
class Evaluator(GatewayEvaluator):
    contract = EvaluationContract(evaluator_id="pool", version="1", data_version="1", metrics=[Metric(name="quality")])
    async def evaluate(self, job, reporter):
        counter = Path(job.parameters["counter"])
        counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
        reporter.save_state({"external_id": "synthetic-owned-resource"})
        if job.parameters["hang"]:
            await asyncio.sleep(300)
        if job.parameters["fail"]:
            raise OSError("transient fixture error")
        return EvaluationResult(metrics={"quality": 7}, score=7)
''')
    worker = Gateway("http://offline-fixture", "pool", [], f"{adapter}:Evaluator", tmp_path / "state")
    job = {
        "id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4()),
        "timeout": 300, "project_root": str(source), "state": "accepted",
        "parameters": {"counter": str(tmp_path / "calls"), "hang": hang, "fail": fail},
    }
    if managed:
        job.update(managed_attempt=True, dispatch_attempt=1, dispatch_max_attempts=3, lease_remaining=60,
                   lease_expires_at="1990-01-01T00:00:00Z")
    worker.journal.accept(job)
    return worker, job


async def advance(worker, job, predicate, timeout=15):
    """Run only local reconciliation until a persisted observable condition holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await worker.reconcile(None, job)
        row = worker.journal.get(job["id"])
        if predicate(row):
            return row
        await asyncio.sleep(0.02)
    pytest.fail("Local pool attempt did not reach its expected state")


def expire(worker, job):
    """Advance just this persisted lease without changing unrelated clocks or timeouts."""
    with worker.journal.connect() as db:
        db.execute("UPDATE attempt_leases SET deadline=? WHERE job_id=? AND attempt_id=?",
                   (time.time() - 1, job["id"], job["attempt_id"]))


def test_lease_uses_receipt_clock_survives_restart_and_cannot_revive(tmp_path, monkeypatch):
    """Absolute server timestamps and offline reconciliation cannot extend a lease."""
    monkeypatch.setattr(gateway_module.time, "time", lambda: 1000.0)
    worker, job = make_gateway(tmp_path)
    assert worker.journal.lease_remaining(job) == 60
    monkeypatch.setattr(gateway_module.time, "time", lambda: 1030.0)
    worker.journal.renew_lease({**job, "lease_remaining": 45, "lease_expires_at": "2999-01-01T00:00:00Z"})
    assert Journal(worker.journal.path).lease_remaining(job) == 45
    worker.journal.accept(job)
    assert worker.journal.lease_remaining(job) == 45
    monkeypatch.setattr(gateway_module.time, "time", lambda: 1076.0)
    worker.journal.renew_lease({**job, "lease_remaining": 60})
    assert Journal(worker.journal.path).lease_remaining(job) == 0
    monkeypatch.setattr(gateway_module.time, "time", lambda: 1070.0)
    worker.journal.renew_lease(job)
    assert worker.journal.lease_remaining(job) == 0
    replacement = {**job, "attempt_id": str(uuid.uuid4())}
    worker.journal.accept(replacement)
    assert worker.journal.lease_remaining(replacement) == 60
    worker.journal.renew_lease({**job, "lease_remaining": 900})
    assert worker.journal.lease_remaining(replacement) == 60


@pytest.mark.parametrize("remaining", [None, -1, float("nan"), float("inf"), True])
async def test_invalid_or_missing_managed_lease_never_starts_work(tmp_path, remaining):
    """Malformed v2 assignments fail closed without entering an evaluator."""
    worker, job = make_gateway(tmp_path)
    job = {**job, "id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()), "lease_remaining": remaining}
    worker.journal.accept(job)
    worker.start_process = Mock()
    try:
        await worker.reconcile(None, job)
        worker.start_process.assert_not_called()
        assert worker.journal.lease_remaining(job) == 0
        assert worker.journal.get(job["id"])["state"] == "failed"
    finally:
        worker.stop_workers()


async def test_offline_expiry_stops_loky_and_quarantines_until_external_cleanup(tmp_path):
    """Platform absence cannot keep a worker alive or release uncertain external state."""
    worker, job = make_gateway(tmp_path, hang=True)
    worker.adapter.cancel = AsyncMock(return_value=False)
    try:
        await advance(worker, job, lambda row: bool(json.loads(row["saved"])))
        handle = worker.processes[job["id"]]
        expire(worker, job)
        worker.enforce_deadlines()
        assert execution_control(worker.journal, job["id"], job["attempt_id"])["stop_requested"] == "superseded"
        await worker.reconcile(None, job)
        assert handle.future.done()
        assert worker.executor.paused and not worker.ready()
        assert worker.executor.entries()
        assert Path(job["parameters"]["counter"]).read_text() == "1"
        with worker.journal.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM cleanup_debts").fetchone()[0] == 1
            db.execute("UPDATE cleanup_debts SET next_at=0")
        worker.adapter.cancel = AsyncMock(return_value=True)
        await worker.reconcile(None, job)
        row = worker.journal.get(job["id"])
        assert row["state"] == "failed"
        assert json.loads(row["report"])["metadata"]["external_cleanup_confirmed"] is True
        assert json.loads(row["report"])["metadata"]["failure_category"] == "infrastructure"
        assert not worker.executor.entries() and worker.ready()
        await worker.reconcile(None, job)
        assert Path(job["parameters"]["counter"]).read_text() == "1"
    finally:
        worker.stop_workers()


async def test_server_budget_is_three_attempts_with_exactly_one_execution_each(tmp_path):
    """Three platform attempt UUIDs cause three executions rather than nine."""
    worker, original = make_gateway(tmp_path, fail=True)
    previous = None
    try:
        for attempt in range(1, 4):
            job = {**original, "attempt_id": str(uuid.uuid4()), "dispatch_attempt": attempt}
            if previous:
                # Server fencing remains authoritative; old local callbacks stay fenced.
                await worker._cleanup(previous)
                worker._ack_execution(previous)
            worker.journal.accept(job)
            row = await advance(worker, job, lambda row: row["state"] == "failed")
            report = json.loads(row["report"])
            assert report["metadata"]["failure_category"] == "infrastructure"
            assert report["metadata"]["external_cleanup_confirmed"] is True
            assert worker.executions.get(job["id"], job["attempt_id"])["executions"] == 1
            assert worker.journal.observations(job["id"], job["attempt_id"])[0]["execution_limit"] == 1
            for _ in range(3):
                await worker.reconcile(None, job)
                worker.start_process(json.loads(worker.journal.get(job["id"])["job"]))
            assert int(Path(job["parameters"]["counter"]).read_text()) == attempt
            if previous:
                Reporter(worker.journal, previous["id"], previous["attempt_id"]).progress("stale")
                assert worker.journal.get(job["id"])["state"] == "failed"
            previous = json.loads(row["job"])
        assert not worker.executor.entries()
    finally:
        worker.stop_workers()


@pytest.mark.parametrize("managed", [True, False])
async def test_restart_does_not_repeat_a_pooled_execution_but_legacy_retries(tmp_path, managed):
    """A restart uses the persisted local count and retains legacy outage behavior."""
    first, job = make_gateway(tmp_path, managed=managed, hang=True)
    second = None
    try:
        await advance(first, job, lambda row: bool(json.loads(row["saved"])))
        first.stop_workers()
        # A legacy retry finishes instead of hanging a second time.
        with first.journal.connect() as db:
            live = json.loads(db.execute("SELECT job FROM jobs WHERE id=?", (job["id"],)).fetchone()[0])
            live["parameters"]["hang"] = False
            db.execute("UPDATE jobs SET job=? WHERE id=?", (json.dumps(live), job["id"]))
        second = Gateway("http://offline-fixture", "pool", [], first.spec, first.root)
        terminal = "failed" if managed else "completed"
        await advance(second, job, lambda row: row["state"] == terminal)
        assert int(Path(job["parameters"]["counter"]).read_text()) == (1 if managed else 2)
    finally:
        (second or first).stop_workers()


@pytest.mark.parametrize("terminal", [False, True])
async def test_restart_with_expired_lease_cannot_resume_or_deliver_result(tmp_path, terminal):
    """An unstarted or completed assignment is fenced before recovery or upload."""
    first, job = make_gateway(tmp_path)
    if terminal:
        first.journal.update(job["id"], "completed", {"success": True, "metrics": {"quality": 7}})
    expire(first, job)
    second = Gateway("http://offline-fixture", "pool", [], first.spec, first.root)
    second.start_process = Mock()
    try:
        second._executor()
        second.enforce_deadlines()
        await second.reconcile(None, job)
        second.start_process.assert_not_called()
        row = second.journal.get(job["id"])
        assert row["ack"] == row["seq"]
        assert not Path(job["parameters"]["counter"]).exists()
        second.request = AsyncMock(return_value={"jobs": []})
        second.capacity = 0
        await second.tick(None)
        assert len(second.request.call_args_list) == 1
        assert second.request.call_args.args[2].endswith("/heartbeat")
    finally:
        second.stop_workers()


async def test_upload_is_cancelled_at_local_lease_expiry_even_after_local_completion(tmp_path):
    """An in-flight artifact upload cannot outlive the lease during an outage."""
    worker, job = make_gateway(tmp_path)
    worker.journal.update(job["id"], "completed", {"success": True, "metrics": {"quality": 7}})
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def upload(*args, **kwargs):
        """Model a network request that never responds."""
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    worker.request = upload
    task = asyncio.create_task(worker._leased_request(job, None, "PUT", "/artifact"))
    await asyncio.wait_for(entered.wait(), 2)
    expire(worker, job)
    with pytest.raises(AttemptLeaseExpiredError):
        await asyncio.wait_for(task, 2)
    assert cancelled.is_set()


async def test_transport_heartbeat_and_claim_renew_but_reports_and_offline_reconcile_do_not(tmp_path):
    """Only assignments read from the server extend the current attempt's deadline."""
    worker, job = make_gateway(tmp_path)
    worker.start_process = Mock()
    worker.cleanup_managed = True
    worker.request = AsyncMock(side_effect=[
        {"jobs": [{**job, "lease_remaining": 120}]}, {"ack": 0, "state": "accepted"}, None,
    ])
    try:
        await worker.tick(None)
        remaining = worker.journal.lease_remaining(job)
        assert 119 < remaining <= 120
        assert worker.request.call_args_list[0].kwargs["json"] == {"ready": False}
        worker.request = AsyncMock(return_value={"ack": 1, "state": "running", "lease_remaining": 900})
        await worker._leased_request(job, None, "POST", "/report")
        assert worker.journal.lease_remaining(job) <= remaining
        await worker.reconcile(None, job)
        assert worker.journal.lease_remaining(job) <= remaining
        claimed = {**job, "id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()), "lease_remaining": 90}
        worker.request = AsyncMock(side_effect=[{"jobs": []}, claimed])
        worker.journal.update(job["id"], "completed", {"success": True, "metrics": {}})
        row = worker.journal.get(job["id"])
        worker.journal.acknowledge(job["id"], row["seq"])
        await worker.tick(None)
        assert 89 < worker.journal.lease_remaining(claimed) <= 90
    finally:
        worker.stop_workers()


@pytest.mark.parametrize("artifact", [False, True])
async def test_stale_report_or_artifact_409_keeps_cleanup_debt_and_cannot_touch_replacement(tmp_path, artifact):
    """Backend fencing discards old delivery while uncertain resources stay quarantined."""
    worker, job = make_gateway(tmp_path)
    reporter = Reporter(worker.journal, job["id"], job["attempt_id"])
    if artifact:
        output = tmp_path / "evidence.txt"
        output.write_text("old")
        reporter.artifact("evidence.txt", output)
    reporter.save_state({"external_id": "synthetic"})
    worker.journal.update(job["id"], "failed", {"success": False, "metrics": {}, "metadata": {"failure_category": "evaluator"}})
    worker.adapter.cancel = AsyncMock(return_value=False)
    stale = aiohttp.ClientResponseError(Mock(real_url="fixture"), (), status=409)
    worker.request = AsyncMock(side_effect=[{"jobs": []}, stale])
    worker.cleanup_managed = True
    try:
        await worker.tick(None)
        assert worker.executor.paused and not worker.ready()
        row = worker.journal.get(job["id"])
        assert row["ack"] == row["seq"]
        replacement = {**job, "attempt_id": str(uuid.uuid4()), "dispatch_attempt": 2}
        await worker.reconcile(None, replacement)
        assert json.loads(worker.journal.get(job["id"])["job"])["attempt_id"] == job["attempt_id"]
        with worker.journal.connect() as db:
            db.execute("UPDATE cleanup_debts SET next_at=0")
        worker.adapter.cancel = AsyncMock(return_value=True)
        worker.start_process = Mock()
        await worker.reconcile(None, replacement)
        Reporter(worker.journal, job["id"], job["attempt_id"]).progress("stale")
        assert json.loads(worker.journal.get(job["id"])["job"])["attempt_id"] == replacement["attempt_id"]
        await worker.reconcile(None, job)
        worker.journal.accept(job)
        assert json.loads(worker.journal.get(job["id"])["job"])["attempt_id"] == replacement["attempt_id"]
        assert not worker.executor.paused
    finally:
        worker.stop_workers()


async def test_registration_and_heartbeat_advertise_protocol_and_local_readiness(tmp_path):
    """Admission pauses are visible to v2 dispatch before claiming any work."""
    worker, job = make_gateway(tmp_path)
    worker.pause_accepting()
    registered = asyncio.Event()
    bodies = []

    async def request(session, method, suffix, **kwargs):
        """Capture registration, then stop the run without network I/O."""
        if suffix == "/register":
            bodies.append(kwargs["json"])
            registered.set()
            await asyncio.Event().wait()
        return {"jobs": []}

    worker.request = request
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(registered.wait(), 2)
        assert bodies[0]["protocol_version"] == 2 and bodies[0]["ready"] is False
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    worker.request = AsyncMock(return_value={"jobs": []})
    await worker.heartbeat(None)
    assert worker.request.call_args.kwargs["json"] == {"ready": False}
    worker.journal.update(job["id"], "completed", {"success": True, "metrics": {}})
    worker.resume_accepting()
    assert worker.ready()


async def wait_until(predicate, *, timeout=15):
    """Wait for real asynchronous controller activity without advancing it manually."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    pytest.fail("Background controller did not reach the expected state")


async def test_real_monitor_expires_during_total_transport_outage(tmp_path):
    """The running SDK stops its worker while all platform calls fail independently."""
    worker, job = make_gateway(tmp_path, hang=True)
    worker.request = AsyncMock(side_effect=OSError("platform offline"))
    worker.adapter.cancel = AsyncMock(return_value=False)
    task = asyncio.create_task(worker.run())
    try:
        await wait_until(lambda: bool(json.loads(worker.journal.get(job["id"])["saved"])))
        handle = worker.processes[job["id"]]
        expire(worker, job)
        await wait_until(lambda: handle.future.done() and worker.executor.paused)
        assert worker.journal.lease_remaining(job) == 0
        assert not worker.ready()
        assert worker.executor.entries()
        assert Path(job["parameters"]["counter"]).read_text() == "1"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_slow_cleanup_does_not_delay_other_slots_lease_expiry(tmp_path):
    """Independent loky slots stop even while one external restoration awaits confirmation."""
    worker, first = make_gateway(tmp_path, hang=True)
    worker.capacity = 2
    second = {**first, "id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()),
              "parameters": {**first["parameters"], "counter": str(tmp_path / "second-calls")}}
    worker.journal.accept(second)
    entered, release = asyncio.Event(), asyncio.Event()

    async def cleanup(job, saved):
        """Block one resource's external hook without blocking other local stops."""
        if job.id == first["id"]:
            entered.set()
            await release.wait()
        return True

    worker.adapter.cancel = cleanup
    worker.request = AsyncMock(side_effect=OSError("platform offline"))
    task = asyncio.create_task(worker.run())
    try:
        await wait_until(lambda: all(json.loads(worker.journal.get(job["id"])["saved"]) for job in (first, second)))
        handles = [worker.processes[job["id"]] for job in (first, second)]
        expire(worker, first)
        await asyncio.wait_for(entered.wait(), 3)
        expire(worker, second)
        await wait_until(lambda: all(handle.future.done() for handle in handles), timeout=3)
        assert not release.is_set()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("renew", [True, False])
async def test_slow_terminal_upload_requires_independent_successful_http_heartbeats(tmp_path, renew):
    """A PUT longer than the initial lease survives only actual server renewals."""
    worker, job = make_gateway(tmp_path)
    output = tmp_path / "large.txt"
    output.write_text("synthetic large upload")
    Reporter(worker.journal, job["id"], job["attempt_id"]).artifact("large.txt", output)
    worker.journal.update(job["id"], "completed", {"success": True, "metrics": {"quality": 7}})
    lease = 0.9
    with worker.journal.connect() as db:
        db.execute("UPDATE attempt_leases SET deadline=?", (time.time() + lease,))
    started, finished, cancelled, delivered = (asyncio.Event() for _ in range(4))
    heartbeats, reports = [], []
    uploading = [False]
    worker.cleanup_managed = True

    async def request(session, method, suffix, **kwargs):
        """Serve renewal responses separately while the upload HTTP operation waits."""
        if suffix == "/register":
            return {}
        if suffix.endswith("/heartbeat"):
            if uploading[0]:
                heartbeats.append(time.monotonic())
                if not renew:
                    raise OSError("heartbeat unavailable during upload")
            return {"jobs": [{**job, "lease_remaining": lease}]}
        if method == "PUT":
            uploading[0] = True
            started.set()
            try:
                await asyncio.sleep(lease * 2.5)
                finished.set()
                return {"object_key": "uploaded-evidence"}
            finally:
                cancelled.set()
        if suffix.endswith("/report"):
            reports.append(kwargs["json"])
            delivered.set()
            return {"ack": kwargs["json"]["sequence"], "state": kwargs["json"]["state"]}
        return None

    async def notifications(session):
        """Leave SSE idle so only the HTTP lease loop can extend authority."""
        await asyncio.Event().wait()

    worker.request = request
    worker.notifications = notifications
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(started.wait(), 2)
        if renew:
            await asyncio.wait_for(delivered.wait(), 5)
            assert finished.is_set() and len(heartbeats) >= 3
            assert worker.journal.lease_remaining(job) > 0
            assert len(reports) == 1 and reports[0]["state"] == "completed"
            assert reports[0]["report"]["metadata"]["artifacts"][0]["object_key"] == "uploaded-evidence"
        else:
            await asyncio.wait_for(cancelled.wait(), 3)
            await wait_until(lambda: worker.journal.lease_remaining(job) == 0, timeout=2)
            assert not finished.is_set() and not reports
            row = worker.journal.get(job["id"])
            await wait_until(lambda: worker.journal.get(job["id"])["ack"] == worker.journal.get(job["id"])["seq"], timeout=2)
            assert row["state"] == "completed"
            worker.journal.renew_lease({**job, "lease_remaining": 60})
            assert worker.journal.lease_remaining(job) == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_shared_physical_resource_claims_only_once_and_keeps_busy_binding_online(tmp_path):
    """Concurrent capabilities reserve one physical device before any download or enqueue."""
    from llm4ad.gateway.sdk.scheduling import DurableExecutor

    directories = [tmp_path / name for name in ("a", "b")]
    for directory in directories:
        directory.mkdir()
    bindings = [make_gateway(directory) for directory in directories]
    executor = DurableExecutor(tmp_path / "shared")
    executor.resource_exclusive = True
    for worker, job in bindings:
        worker.executor, worker.executor_owned = executor, False
        worker.cleanup_managed = True
        worker.journal.update(job["id"], "completed", {"success": True, "metrics": {}})
        worker.journal.acknowledge(job["id"], worker.journal.get(job["id"])["seq"])
    first, claimed = bindings[0]
    second, _ = bindings[1]
    claimed = {**claimed, "id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4())}
    claims, readiness = [], []
    entered, release = asyncio.Event(), asyncio.Event()

    async def first_request(session, method, suffix, **kwargs):
        """Let another capability race this delayed claim response."""
        if suffix.endswith("/heartbeat"):
            return {"jobs": []}
        if suffix.endswith("/claim"):
            claims.append(first)
            entered.set()
            await release.wait()
            return claimed
        return {"ack": kwargs["json"]["sequence"], "state": kwargs["json"]["state"]}

    async def second_request(session, method, suffix, **kwargs):
        """Keep HTTP live while rejecting any accidental second physical claim."""
        if suffix.endswith("/heartbeat"):
            readiness.append(kwargs["json"]["ready"])
            return {"jobs": []}
        claims.append(second)
        pytest.fail("The occupied device must not claim work for a sibling capability")

    first.request, second.request = first_request, second_request
    tasks = [asyncio.create_task(first.tick(None))]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        tasks.append(asyncio.create_task(second.tick(None)))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(*tasks)
        assert claims == [first]
        assert not first.ready() and not second.ready()
        assert not executor.entries(), "An accepted device reservation precedes candidate download and enqueue"
        await second.tick(None)
        assert readiness[-1] is False and len(claims) == 1
        assert first.executions.get(claimed["id"], claimed["attempt_id"])["executions"] == 0
        await first.request_cancel(claimed["id"], claimed["attempt_id"])
        assert first.ready() and second.ready()
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for worker, _ in bindings:
            worker.stop_workers()
        executor.close()


async def test_queued_ack_preserves_failed_attempt_category_and_does_not_start_local_retry(tmp_path):
    """Pool requeue retires one UUID while keeping its real failed outcome in the journal."""
    worker, job = make_gateway(tmp_path, fail=True)
    try:
        row = await advance(worker, job, lambda row: row["state"] == "failed")
        live = json.loads(row["job"])
        worker.cleanup_managed = True

        async def request(session, method, suffix, **kwargs):
            """ACK the failed attempt after accepting its SDK diagnostic artifacts."""
            if suffix.endswith("/heartbeat"):
                return {"jobs": []}
            if method == "PUT":
                return {"object_key": "diagnostic"}
            if suffix.endswith("/report"):
                return {"state": "queued", "ack": kwargs["json"]["sequence"]}
            return None

        worker.request = request
        await worker.tick(None)
        current = worker.journal.get(job["id"])
        assert current["state"] == "failed" and current["ack"] == current["seq"]
        assert json.loads(current["report"])["metadata"]["failure_category"] == "infrastructure"
        assert worker.executions.get(job["id"], job["attempt_id"])["executions"] == 1
        assert execution_control(worker.journal, job["id"], job["attempt_id"])["stop_requested"] == "superseded"
        worker.start_process(live)
        assert Path(job["parameters"]["counter"]).read_text() == "1"
    finally:
        worker.stop_workers()


async def test_queued_ack_cannot_release_uncertain_external_cleanup_debt(tmp_path):
    """A platform transfer confirms receipt, never restoration of the old device."""
    worker, job = make_gateway(tmp_path)
    Reporter(worker.journal, job["id"], job["attempt_id"]).save_state({"external_id": "uncertain-owned-resource"})
    worker.adapter.cancel = AsyncMock(return_value=False)
    worker.cleanup_managed = True
    worker.executions.ensure(job, worker.journal.get(job["id"]))
    try:
        for _ in range(3):
            with worker.journal.connect() as db:
                db.execute("UPDATE cleanup_debts SET next_at=0")
            assert not await worker._cleanup(job)
        worker._fail_cleanup(job)
        row = worker.journal.get(job["id"])
        assert row["state"] == "failed"
        worker.request = AsyncMock(side_effect=[{"jobs": []}, {"state": "queued", "ack": row["seq"]}])
        await worker.tick(None)
        current = worker.journal.get(job["id"])
        assert current["ack"] == current["seq"]
        assert worker.executor.paused and not worker.ready()
        assert json.loads(current["report"])["metadata"]["external_cleanup_confirmed"] is False
        with worker.journal.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM cleanup_debts").fetchone()[0] == 1
            db.execute("UPDATE cleanup_debts SET next_at=0")
        worker.adapter.cancel = AsyncMock(return_value=True)
        await worker.probe_cleanup()
        assert worker.ready()
        confirmed = worker.journal.get(job["id"])
        assert json.loads(confirmed["report"])["metadata"]["external_cleanup_confirmed"] is True
    finally:
        worker.stop_workers()
