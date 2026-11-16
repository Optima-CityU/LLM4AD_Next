"""Authorized gateway dispatch, fencing and cancellation integration tests."""

import asyncio
import base64
import hashlib
import io
import json
import os
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_current_user, get_db
from app.api.llm4ad import evaluation_gateways as api
from app.models import EvaluationGateway, Project, RemoteEvaluationJob, Task, TaskLog, TaskStatus, User
from app.services import credential_broker
from app.services import remote_evaluation as service

PREFIX = "/evaluation-gateways"
CONTRACT = {
    "evaluator_id": "test",
    "version": "1",
    "data_version": "local-1",
    "metrics": [{"name": "accuracy", "type": "maximize", "weight": 1.0}],
}


@pytest.fixture()
def platform(tmp_path, monkeypatch):
    """Start an isolated API backed by a real SQL database and in-memory objects."""
    from sqlalchemy import text

    url = os.environ.get("REMOTE_TEST_DATABASE_URL")
    schema = "remote_test_" + uuid.uuid4().hex
    admin = None
    if url:
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'platform.db'}", connect_args={"check_same_thread": False})
    tables = [
        User.__table__,
        Project.__table__,
        Task.__table__,
        TaskLog.__table__,
        EvaluationGateway.__table__,
        RemoteEvaluationJob.__table__,
    ]
    SQLModel.metadata.create_all(engine, tables=tables)
    owner = User(email="owner@example.com", hashed_password="unused")
    other = User(email="other@example.com", hashed_password="unused")
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([owner, other])
        db.commit()
        db.refresh(owner)
        db.refresh(other)
        project = Project(name="test", user_id=owner.id)
        db.add(project)
        db.commit()
        db.refresh(project)
        task = Task(name="remote", project_id=project.id, status=TaskStatus.RUNNING)
        db.add(task)
        db.commit()
        db.refresh(task)
    monkeypatch.setattr(api, "engine", engine)
    objects = {}
    monkeypatch.setattr(api.storage, "upload", lambda key, data, **_: objects.__setitem__(
        key, data.read() if hasattr(data, "read") else data
    ))
    monkeypatch.setattr(api.storage, "list_objects", lambda prefix, **_: sorted(key for key in objects if key.startswith(prefix)))
    monkeypatch.setattr(api.storage, "iter_download", lambda key: iter([objects[key]]))
    monkeypatch.setattr(api.storage, "delete", lambda key: objects.pop(key, None))
    from app.api.llm4ad import evaluations
    from app.core.config import settings

    monkeypatch.setattr(settings, "HOST_PROJECT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(settings, "DOCKER_PROJECT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(api.storage, "download", lambda key: objects[key])
    monkeypatch.setattr(credential_broker, "extend_task_tokens", lambda *args, **kwargs: 1)
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(evaluations.router)
    app.dependency_overrides[get_current_user] = lambda: owner

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, app=app, engine=engine, owner=owner, other=other, task=task, objects=objects
        )
    engine.dispose()
    if admin:
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def register(platform, users=None, contract=None):
    """Register one independent CLI-generated gateway identity."""
    identity = {"id": str(uuid.uuid4()), "secret": uuid.uuid4().hex + uuid.uuid4().hex}
    response = platform.client.post(
        PREFIX + "/register",
        json={
            **identity,
            "name": "Local evaluator",
            "allowed_user_ids": users if users is not None else [str(platform.owner.id)],
            "capacity": 1,
            "contract": contract or CONTRACT,
        },
    )
    assert response.status_code == 200, response.text
    return identity


def gateway_headers(gateway):
    """Use the local opaque identity, never a platform login token."""
    return {"Authorization": "Bearer " + gateway["secret"]}


def runtime_headers(platform):
    """Authenticate only the task-scoped evaluation channel."""
    return {"Authorization": "Bearer " + service.runtime_token(platform.task.id)}


def configure(platform, gateways):
    """Persist the same selected pool as the actual running task."""
    with Session(platform.engine) as db:
        task = db.get(Task, platform.task.id)
        task.input_args = {"evaluator": {"type": "remote", "gateway_ids": [g["id"] for g in gateways]}}
        db.add(task)
        db.commit()


def submit(platform, gateways, job_id=None):
    """Upload only a candidate, just as the runtime SDK does."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("solution.py", "def solve(): return 1\n")
    response = platform.client.post(
        PREFIX + f"/tasks/{platform.task.id}/jobs",
        headers=runtime_headers(platform),
        json={
            "id": job_id or str(uuid.uuid4()),
            "gateway_ids": [g["id"] for g in gateways],
            "contract": CONTRACT,
            "timeout": 5,
            "bundle": base64.b64encode(archive.getvalue()).decode(),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def claim(platform, gateway):
    """Claim without testing ownership by heartbeat expiry."""
    response = platform.client.post(PREFIX + f"/{gateway['id']}/claim", headers=gateway_headers(gateway))
    assert response.status_code == 200, response.text
    return response.json()


def report(platform, gateway, job, state, seq, data=None):
    """Post a monotonic report with its authoritative attempt ID."""
    return platform.client.post(
        PREFIX + f"/{gateway['id']}/jobs/{job['id']}/report",
        headers=gateway_headers(gateway),
        json={"attempt_id": job["attempt_id"], "sequence": seq, "state": state, "report": data or {}},
    )


def test_binary_evidence_upload_fencing_and_owner_download(platform):
    """Store raw bytes separately and expose only an authoritative result manifest."""
    gateway = register(platform)
    configure(platform, [gateway])
    submit(platform, [gateway])
    job = claim(platform, gateway)
    content = b"synthetic trace for API transport test"
    digest = hashlib.sha256(content).hexdigest()
    path = PREFIX + f"/{gateway['id']}/jobs/{job['id']}/artifacts/{digest}"
    headers = gateway_headers(gateway) | {"X-Attempt-Id": job["attempt_id"]}
    wrong = platform.client.put(path, headers=headers | {"X-Attempt-Id": str(uuid.uuid4())}, content=content)
    assert wrong.status_code == 409
    assert platform.client.put(path, headers=headers, content=b"wrong bytes").status_code == 422
    uploaded = platform.client.put(path, headers=headers, content=content)
    assert uploaded.status_code == 200, uploaded.text
    assert platform.client.put(path, headers=headers, content=content).json() == uploaded.json()
    artifact = uploaded.json() | {"name": "trace.pftrace", "media_type": "application/octet-stream"}
    assert platform.objects[artifact["object_key"]] == content
    done = report(
        platform,
        gateway,
        job,
        "completed",
        1,
        {
            "score": 1,
            "metrics": {"accuracy": 1},
            "metadata": {"artifacts": [artifact]},
        },
    )
    assert done.status_code == 200, done.text
    download = PREFIX + f"/tasks/{platform.task.id}/jobs/{job['id']}/artifacts/{digest}"
    assert platform.client.get(download).content == content
    platform.app.dependency_overrides[get_current_user] = lambda: platform.other
    assert platform.client.get(download).status_code == 404


def test_unreferenced_binary_evidence_is_not_downloadable(platform):
    """An object outside the final result manifest cannot be retrieved by guessing."""
    gateway = register(platform)
    configure(platform, [gateway])
    submit(platform, [gateway])
    job = claim(platform, gateway)
    content = b"synthetic unreferenced evidence"
    digest = hashlib.sha256(content).hexdigest()
    uploaded = platform.client.put(
        PREFIX + f"/{gateway['id']}/jobs/{job['id']}/artifacts/{digest}",
        headers=gateway_headers(gateway) | {"X-Attempt-Id": job["attempt_id"]},
        content=content,
    )
    assert uploaded.status_code == 200
    assert report(platform, gateway, job, "completed", 1, {"metrics": {"accuracy": 1}}).status_code == 200
    assert (
        platform.client.get(PREFIX + f"/tasks/{platform.task.id}/jobs/{job['id']}/artifacts/{digest}").status_code
        == 404
    )


def test_visibility_contract_and_identity_authentication(platform):
    """Discovery is user-scoped and an existing identity cannot be taken over."""
    own = register(platform)
    register(platform, [str(platform.other.id)])
    visible = platform.client.get(PREFIX).json()
    assert [entry["id"] for entry in visible] == [own["id"]]
    assert "secret" not in json.dumps(visible)
    assert "secret_hash" not in json.dumps(visible)
    stolen = platform.client.post(PREFIX + f"/{own['id']}/heartbeat", headers={"Authorization": "Bearer wrong"})
    assert stolen.status_code == 401
    incompatible = register(platform, contract=CONTRACT | {"data_version": "different"})
    with Session(platform.engine) as db, pytest.raises(Exception) as error:
        service.validate_pool(db, platform.owner.id, {"gateway_ids": [own["id"], incompatible["id"]]})
    assert error.value.status_code == 422


def test_notification_stream_requires_gateway_secret(platform):
    """Reject invalid identities before opening the SSE response."""
    own = register(platform)
    path = PREFIX + f"/{own['id']}/events"
    assert platform.client.get(path).status_code == 422
    assert platform.client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert platform.client.get(PREFIX + f"/{uuid.uuid4()}/events", headers=gateway_headers(own)).status_code == 401


async def test_notification_stream_releases_database_before_streaming(platform, monkeypatch):
    """Stream repeated poll events without retaining a database connection."""
    own = register(platform)
    response = api.notifications(uuid.UUID(own["id"]), gateway_headers(own)["Authorization"])
    assert response.media_type == "text/event-stream"
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.headers["X-Accel-Buffering"] == "no"
    assert platform.engine.pool.checkedout() == 0
    iterator = response.body_iterator
    try:
        assert await anext(iterator) == 'event: poll\ndata: {"type":"poll"}\n\n'
        sleep = asyncio.sleep

        async def next_tick(_delay):
            await sleep(0)

        monkeypatch.setattr(api.asyncio, "sleep", next_tick)
        assert await anext(iterator) == 'event: poll\ndata: {"type":"poll"}\n\n'
        assert platform.engine.pool.checkedout() == 0
    finally:
        await iterator.aclose()


async def test_notification_http_stream_closes_on_disconnect(platform):
    """Authenticate a real ASGI stream and stop promptly when its client leaves."""
    own = register(platform)
    path = PREFIX + f"/{own['id']}/events"
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})
    sent = []

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            incoming.put_nowait({"type": "http.disconnect"})

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"authorization", gateway_headers(own)["Authorization"].encode())],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    await asyncio.wait_for(platform.app(scope, incoming.get, send), 2)
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 200
    assert dict(start["headers"])[b"content-type"].startswith(b"text/event-stream")
    assert any(message.get("body") == b'event: poll\ndata: {"type":"poll"}\n\n' for message in sent)
    assert platform.engine.pool.checkedout() == 0


def test_pool_distribution_offline_wait_and_idempotent_results(platform):
    """Accepted jobs are not reassigned; final result and duplicate delivery count once."""
    first, second = register(platform), register(platform)
    configure(platform, [first, second])
    jobs = [submit(platform, [first, second]) for _ in range(3)]
    one, two = claim(platform, first), claim(platform, second)
    assert {one["id"], two["id"]} == {jobs[0]["id"], jobs[1]["id"]}
    assert claim(platform, second) is None
    with Session(platform.engine) as db:
        for g in db.exec(select(EvaluationGateway)):
            g.last_seen = datetime.now(UTC) - timedelta(days=20)
            db.add(g)
        db.commit()
    progress = platform.client.get(PREFIX + f"/tasks/{platform.task.id}").json()
    assert any(job["state"] == "waiting_gateway" for job in progress)
    assert claim(platform, second) is None
    duplicate = submit(platform, [first, second], one["id"])
    assert duplicate["attempt_id"] == one["attempt_id"]
    result = {"score": 9, "metrics": {"accuracy": 9}, "metadata": {"local_details": [1, 2]}}
    assert report(platform, first, one, "completed", 3, result).status_code == 200
    assert report(platform, first, one, "completed", 4, {"score": 10, "metrics": {}}).status_code == 200
    status = platform.client.get(
        PREFIX + f"/tasks/{platform.task.id}/jobs/{one['id']}", headers=runtime_headers(platform)
    ).json()
    assert status["report"]["score"] == 9
    assert status["report"]["metadata"]["local_details"] == [1, 2]
    with Session(platform.engine) as db:
        stored = db.get(RemoteEvaluationJob, uuid.UUID(one["id"]))
        assert "metadata" not in stored.report
        assert stored.sequence == 3
    assert len([key for key in platform.objects if "/result-" in key]) == 1
    assert claim(platform, first)["id"] == jobs[2]["id"]


def test_fenced_retry_requires_operator_confirmation(platform):
    """Recovery preserves ownership until an explicit safe retry creates a new attempt."""
    first, second = register(platform), register(platform)
    configure(platform, [first, second])
    submit(platform, [first, second])
    old = claim(platform, first)
    assert report(platform, first, old, "recovery_required", 1).status_code == 200
    assert claim(platform, second) is None
    assert report(platform, first, old, "recovery_required", 2, {"retry_confirmed": True}).status_code == 200
    new = claim(platform, second)
    assert new["id"] == old["id"] and new["attempt_id"] != old["attempt_id"]
    assert report(platform, first, old, "completed", 3, {"metrics": {}}).status_code == 409


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_terminal_failure_preserves_result_fields(platform, state):
    """Keep rich failure results and authoritative references on runtime retrieval."""
    gateway = register(platform)
    configure(platform, [gateway])
    submit(platform, [gateway])
    job = claim(platform, gateway)
    result = {
        "score": 2,
        "metrics": {"accuracy": 2},
        "monitor_metrics": {"samples": 3},
        "duration_ms": 123.5,
        "evolution_feedback": {"hint": "Check the candidate input"},
        "success": False,
        "message": "Latest diagnostic",
        "error_message": "Candidate input is invalid",
        "error_type": "ValueError",
        "metadata": {
            "evaluation_messages": [{"message": "Starting"}, {"message": "Latest diagnostic"}],
            "diagnostic": {"sample": 3},
            "evaluation_records": [{"id": "untrusted"}],
            "failure_type": "candidate_input_invalid",
        },
    }
    response = report(platform, gateway, job, state, 1, result)
    assert response.status_code == 200, response.text
    response = platform.client.get(
        PREFIX + f"/tasks/{platform.task.id}/jobs/{job['id']}", headers=runtime_headers(platform)
    )
    assert response.status_code == 200, response.text
    full = response.json()["report"]
    for name in ("score", "metrics", "monitor_metrics", "duration_ms", "evolution_feedback", "success", "error_message"):
        assert full[name] == result[name]
    assert full["message"] == result["message"]
    assert full["error_type"] == "ValueError"
    assert full["metadata"]["evaluation_messages"] == result["metadata"]["evaluation_messages"]
    assert full["metadata"]["diagnostic"] == {"sample": 3}
    assert full["metadata"]["failure_type"] == "candidate_input_invalid"
    assert full["metadata"]["evaluation_records"] == [
        {
            "id": job["attempt_id"],
            "algorithm_id": "",
            "evaluation_profile": "standard",
            "instance_id": "",
            "source_fingerprint": "",
        }
    ]
    assert submit(platform, [gateway], job["id"])["report"] == full
    with Session(platform.engine) as db:
        stored = db.get(RemoteEvaluationJob, uuid.UUID(job["id"]))
        assert "metadata" not in stored.report
        assert "evolution_feedback" not in stored.report
        assert stored.report["error_message"] == result["error_message"]


@pytest.mark.parametrize("state", ["failed", "cancelled"])
@pytest.mark.parametrize(
    "data", [{}, {"message": "Legacy failure", "error_type": "ValueError"}, {"error_type": "TypeError"}]
)
def test_minimal_terminal_failure_remains_compatible(platform, state, data):
    """Accept legacy message-only failures and cancellation acknowledgements."""
    gateway = register(platform)
    configure(platform, [gateway])
    submit(platform, [gateway])
    job = claim(platform, gateway)
    response = report(platform, gateway, job, state, 1, data)
    assert response.status_code == 200, response.text
    response = platform.client.get(
        PREFIX + f"/tasks/{platform.task.id}/jobs/{job['id']}", headers=runtime_headers(platform)
    )
    assert response.status_code == 200, response.text
    result = response.json()["report"]
    assert result["success"] is False
    assert result["metrics"] == result["monitor_metrics"] == {}
    assert result["error_message"] == data.get("message", state)
    assert result["metadata"]["failure_type"] == data.get("error_type", "RemoteExecutionError")


def test_completed_unsuccessful_report_keeps_concise_error(platform):
    """Expose a rejected candidate's error without copying full metadata into SQL."""
    gateway = register(platform)
    configure(platform, [gateway])
    submit(platform, [gateway])
    job = claim(platform, gateway)
    result = {
        "metrics": {"accuracy": 0},
        "success": False,
        "error_message": "Candidate output could not be parsed",
        "metadata": {"diagnostic": "details" * 1024},
        "evolution_feedback": {"hint": "Return valid output"},
    }
    response = report(platform, gateway, job, "completed", 1, result)
    assert response.status_code == 200, response.text
    summary = platform.client.get(PREFIX + f"/tasks/{platform.task.id}").json()[0]
    assert summary["report"]["message"] == summary["report"]["error_message"] == result["error_message"]
    assert "metadata" not in summary["report"]
    with Session(platform.engine) as db:
        stored = db.get(RemoteEvaluationJob, uuid.UUID(job["id"]))
        assert set(stored.report) == {
            "score", "metrics", "success", "duration_ms", "message", "error_message", "result_key",
        }
        assert stored.state == "completed"
    full = platform.client.get(
        PREFIX + f"/tasks/{platform.task.id}/jobs/{job['id']}", headers=runtime_headers(platform)
    ).json()["report"]
    assert full["metadata"]["diagnostic"] == result["metadata"]["diagnostic"]
    assert full["evolution_feedback"] == result["evolution_feedback"]


def test_stop_requires_cancel_ack_and_preserves_final_state(platform):
    """Stopped tasks cannot accept late success; queued jobs cancel immediately."""
    gateway = register(platform)
    configure(platform, [gateway])
    submit(platform, [gateway])
    job = claim(platform, gateway)
    queued = submit(platform, [gateway])
    with Session(platform.engine) as db:
        task = db.get(Task, platform.task.id)
        task.status = TaskStatus.FAILED
        db.add(task)
        db.commit()
        service.cancel_task_jobs(db, task.id)
        assert db.get(RemoteEvaluationJob, uuid.UUID(queued["id"])).state == "cancelled"
    assert report(platform, gateway, job, "completed", 1, {"metrics": {}}).json()["state"] == "cancel_requested"
    assert report(platform, gateway, job, "cancelled", 2).json()["state"] == "cancelled"
    assert claim(platform, gateway) is None


def test_other_gateway_and_runtime_cannot_access_candidate(platform):
    """Only the assigned gateway and canonical task can read runtime artifacts."""
    first, second = register(platform), register(platform)
    configure(platform, [first])
    submit(platform, [first])
    job = claim(platform, first)
    denied = platform.client.get(PREFIX + f"/{second['id']}/jobs/{job['id']}/bundle", headers=gateway_headers(second))
    assert denied.status_code == 403
    allowed = platform.client.get(PREFIX + f"/{first['id']}/jobs/{job['id']}/bundle", headers=gateway_headers(first))
    assert allowed.status_code == 200
    platform.app.dependency_overrides[get_current_user] = lambda: platform.other
    assert platform.client.get(PREFIX + f"/tasks/{platform.task.id}").status_code == 403


@pytest.mark.parametrize("with_evidence", [False, True])
def test_real_gateways_reconnect_and_complete_without_duplicate_execution(platform, tmp_path, monkeypatch, with_evidence):
    """Run two actual SDK gateways and candidate subprocesses against the API."""
    import asyncio
    import socket
    import threading
    from pathlib import Path

    import aiohttp
    import uvicorn
    from llm4ad.config.evaluator import EvalContext, RemoteEvaluatorConfig
    from llm4ad.remote.client import evaluate_remote
    from llm4ad.remote.gateway import Gateway

    monkeypatch.setattr(api, "_NOTIFICATION_INTERVAL", 0.05)
    host = FastAPI()
    host.mount("/api/v1/llm4ad", platform.app)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(host, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    example = Path(__file__).resolve().parents[4] / "examples/remote_evaluation/evaluator.py"
    evidence_bytes = b"synthetic end-to-end trace transport fixture"
    if with_evidence:
        source = example.read_text().replace(
            "        return EvaluationResult(",
            "        trace = Path(job.project_root) / 'trace.pftrace'\n"
            f"        trace.write_bytes({evidence_bytes!r})\n"
            "        reporter.artifact('trace.pftrace', trace)\n"
            "        return EvaluationResult(",
        )
        (tmp_path / "runner.py").write_text(example.with_name("runner.py").read_text())
        example = tmp_path / "evidence_adapter.py"
        example.write_text(source)
    first = Gateway(address, "one", [str(platform.owner.id)], f"{example}:Evaluator", tmp_path / "first")
    second = Gateway(address, "two", [str(platform.owner.id)], f"{example}:Evaluator", tmp_path / "second")
    monkeypatch.setenv("LLM4AD_REMOTE_URL", address + "/api/v1/llm4ad/evaluation-gateways")
    monkeypatch.setenv("LLM4AD_REMOTE_TOKEN", service.runtime_token(platform.task.id))
    monkeypatch.setenv("LLM4AD_REMOTE_TASK_ID", str(platform.task.id))
    configure(platform, [first.identity, second.identity])
    blocked = False
    real_request = first.request

    async def network(*args, **kwargs):
        if blocked:
            raise aiohttp.ClientConnectionError("simulated disconnection")
        return await real_request(*args, **kwargs)

    monkeypatch.setattr(first, "request", network)

    async def wait_until(condition):
        for _ in range(500):
            if condition():
                return
            await asyncio.sleep(0.02)
        states = [(row["state"], json.loads(row["report"])) for row in first.journal.rows()]
        raise AssertionError(f"Timed out waiting for test state: {states}")

    async def exercise():
        nonlocal blocked
        await wait_until(lambda: server.started)
        runners = [asyncio.create_task(first.run())]
        calls = []
        try:
            await wait_until(lambda: len(platform.client.get(PREFIX).json()) == 1)
            # Register the second pool member before starting its transport.
            response = platform.client.post(
                PREFIX + "/register",
                json={
                    **second.identity,
                    "name": "two",
                    "protocol_version": 2,
                    "allowed_user_ids": [str(platform.owner.id)],
                    "capacity": 1,
                    "contract": second.adapter.contract.model_dump(mode="json"),
                },
            )
            assert response.status_code == 200, response.text
            config = RemoteEvaluatorConfig(
                gateway_ids=[first.identity["id"], second.identity["id"]],
                contract=first.adapter.contract.model_dump(mode="json"),
            )
            for name, code in (("original", "abs(x)"), ("improved", "x*x")):
                directory = tmp_path / name
                directory.mkdir()
                (directory / "solution.py").write_text(f"def solve(x): return {code}\n")
            from llm4ad.remote.bundles import pack_candidate

            legacy_context = {"project_root": str(tmp_path / "original"), "data_path": "", "timeout": 60.0,
                              "behavior_storage": "none", "evaluation_profile": "standard"}
            legacy_key = hashlib.sha256(pack_candidate(tmp_path / "original", config.candidate_paths) + json.dumps({
                "task_id": str(platform.task.id), "config": config.model_dump(mode="json"), "context": legacy_context,
            }, sort_keys=True).encode()).hexdigest()
            recovered_id = str(uuid.uuid4())
            receipts = tmp_path / "original" / ".remote-evaluation"
            receipts.mkdir()
            (receipts / f"{legacy_key}.json").write_text(json.dumps({"id": recovered_id}))
            calls.append(
                asyncio.create_task(evaluate_remote(config, EvalContext(project_root=str(tmp_path / "original"))))
            )
            await wait_until(lambda: bool(first.processes))
            blocked = True
            runners.append(asyncio.create_task(second.run()))
            calls.append(
                asyncio.create_task(evaluate_remote(config, EvalContext(project_root=str(tmp_path / "improved"))))
            )
            await wait_until(lambda: first.journal.rows()[0]["state"] == "completed")
            improved = await asyncio.wait_for(calls[1], timeout=15)
            assert improved.score == 0
            assert not calls[0].done()
            with Session(platform.engine) as db:
                rows = db.exec(select(RemoteEvaluationJob)).all()
                assert len(rows) == 2
                assert recovered_id in {str(row.id) for row in rows}
                assert {str(row.gateway_id) for row in rows} == {first.identity["id"], second.identity["id"]}
            blocked = False
            first.wakeup.set()
            original = await asyncio.wait_for(calls[0], timeout=15)
            assert original.score == pytest.approx(-1.6)
            if with_evidence:
                artifacts = original.metadata["artifacts"]
                assert len(artifacts) == 1
                assert platform.objects[artifacts[0]["object_key"]] == evidence_bytes
                assert artifacts[0]["sha256"] == hashlib.sha256(evidence_bytes).hexdigest()
            with Session(platform.engine) as db:
                assert all(row.state == "completed" for row in db.exec(select(RemoteEvaluationJob)))
        finally:
            for task in calls + runners:
                task.cancel()
            await asyncio.gather(*calls, *runners, return_exceptions=True)

    try:
        asyncio.run(exercise())
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_concurrent_claims_respect_ownership_and_capacity(platform):
    """Real database row locks prevent duplicate claims and capacity oversubscription."""
    from concurrent.futures import ThreadPoolExecutor

    if platform.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row-lock regression")
    first, second = register(platform), register(platform)
    configure(platform, [first, second])
    job = submit(platform, [first, second])
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda gateway: claim(platform, gateway), [first, second]))
    assert [reply["id"] for reply in replies if reply] == [job["id"]]
    winner = first if replies[0] else second
    submit(platform, [first, second])
    submit(platform, [first, second])
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: claim(platform, winner), range(2)))
    assert replies == [None, None]


def test_builtin_proxy_token_is_refreshed_after_long_wait(monkeypatch):
    """Renew the builtin access JWT without exposing it to the task container."""
    from unittest.mock import Mock

    import jwt

    user_id = str(uuid.uuid4())
    blob = json.dumps(
        {
            "user_id": user_id,
            "base_url": "https://upstream/expired/v1",
            "upstream_url_template": "https://upstream/{accessToken}/v1",
        }
    )
    monkeypatch.setattr(
        credential_broker, "get_sync_redis", lambda: SimpleNamespace(get=Mock(return_value="encrypted"))
    )
    monkeypatch.setattr(credential_broker, "decrypt_secret", lambda _: blob)
    result = credential_broker.resolve_token("opaque-proxy-token")
    token = result["base_url"].removeprefix("https://upstream/").removesuffix("/v1")
    assert jwt.decode(token, options={"verify_signature": False})["sub"] == user_id
    assert "expired" not in result["base_url"]


def test_remote_task_uses_dedicated_queue_without_wall_clock_timeout(monkeypatch):
    """Forward the Celery identity while retaining the shared task lifecycle."""
    from unittest.mock import Mock

    from app.core.celery import celery_app
    from app.tasks import evolution

    seen = []
    run = Mock(side_effect=lambda data: seen.append(evolution.run_evolution.request.id))
    monkeypatch.setattr(evolution.run_evolution, "run", run)
    evolution.run_remote_evolution.push_request(id="remote-celery-id")
    try:
        evolution.run_remote_evolution.run({"task_id": "test"})
    finally:
        evolution.run_remote_evolution.pop_request()
    assert seen == ["remote-celery-id"]
    assert evolution.run_remote_evolution.time_limit == 0
    assert evolution.run_remote_evolution.soft_time_limit == 0
    assert celery_app.amqp.router.route({}, evolution.run_remote_evolution.name)["queue"].name == "remote_evaluation"


def test_accepted_job_survives_sharing_change_and_submit_retry(platform):
    """Revoking new work does not invalidate an already accepted operation."""
    gateway = register(platform)
    configure(platform, [gateway])
    job = submit(platform, [gateway])
    claim(platform, gateway)
    response = platform.client.post(
        PREFIX + "/register",
        json={**gateway, "name": "Local evaluator", "allowed_user_ids": [], "capacity": 1, "contract": CONTRACT},
    )
    assert response.status_code == 200
    assert platform.client.get(PREFIX).json() == []
    assert submit(platform, [gateway], job["id"])["attempt_id"] == job["attempt_id"]
    assert report(platform, gateway, job, "completed", 1, {"score": 1, "metrics": {"accuracy": 1}}).status_code == 200


def register_pool_member(platform, *, users=None, contract=None):
    """Register a leased SDK member without touching physical devices."""
    identity = {"id": str(uuid.uuid4()), "secret": uuid.uuid4().hex + uuid.uuid4().hex}
    response = platform.client.post(PREFIX + "/register", json={
        **identity, "name": "Pool " + identity["id"][:8], "capacity": 1,
        "allowed_user_ids": users if users is not None else [str(platform.owner.id)],
        "contract": contract or CONTRACT, "protocol_version": 2,
    })
    assert response.status_code == 200, response.text
    return identity


def submit_pool(platform, *, mode="auto", members=(), max_attempts=3, count=1):
    """Submit leased candidate jobs through the ordinary authenticated API."""
    with Session(platform.engine) as db:
        task = db.get(Task, platform.task.id)
        task.input_args = {"evaluator": {
            "type": "remote", "selection_mode": mode, "gateway_ids": [g["id"] for g in members],
            "contract": CONTRACT, "max_attempts": max_attempts, "lease_timeout": 60,
        }}
        db.add(task)
        db.commit()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("solution.py", "def solve(): return 1\n")
    jobs = []
    for _ in range(count):
        response = platform.client.post(PREFIX + f"/tasks/{platform.task.id}/jobs", headers=runtime_headers(platform), json={
            "id": str(uuid.uuid4()), "gateway_ids": [g["id"] for g in members], "selection_mode": mode,
            "dispatch_version": 2, "max_attempts": max_attempts, "lease_timeout": 60,
            "contract": CONTRACT, "timeout": 5, "bundle": base64.b64encode(archive.getvalue()).decode(),
        })
        assert response.status_code == 200, response.text
        jobs.append(response.json())
    return jobs


def force_elapsed_lease(platform, job):
    """Advance only an isolated fixture's persisted lease, without real sleeps."""
    with Session(platform.engine) as db:
        row = db.get(RemoteEvaluationJob, uuid.UUID(job["id"]))
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.add(row)
        db.commit()


def pool_result(platform, job):
    """Read the exact logical candidate through its task channel."""
    response = platform.client.get(
        PREFIX + f"/tasks/{platform.task.id}/jobs/{job['id']}", headers=runtime_headers(platform),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_dynamic_pool_accepts_a_late_member_without_editing_the_task(platform):
    """A newly connected comparable device can claim a concurrently queued candidate."""
    first = register_pool_member(platform)
    jobs = submit_pool(platform, count=2)
    one = claim(platform, first)
    second = register_pool_member(platform)
    two = claim(platform, second)
    assert {one["id"], two["id"]} == {j["id"] for j in jobs}
    assert one["dispatch_attempt"] == two["dispatch_attempt"] == 1
    assert one["managed_attempt"] and 0 < one["lease_remaining"] <= 60
    with Session(platform.engine) as db:
        assert db.get(Task, platform.task.id).input_args["evaluator"]["gateway_ids"] == []


def test_dynamic_pool_excludes_unauthorized_incompatible_and_legacy_devices(platform):
    """Automatic discovery preserves ACLs, benchmark identity and lease negotiation."""
    member = register_pool_member(platform)
    forbidden = register_pool_member(platform, users=[str(platform.other.id)])
    incompatible = register_pool_member(platform, contract=CONTRACT | {"data_version": "other-data"})
    legacy = register(platform)
    submit_pool(platform)
    for gateway in (forbidden, incompatible, legacy):
        assert claim(platform, gateway) is None
    assert claim(platform, member) is not None


def test_pool_infrastructure_failure_moves_to_another_device_and_fences_old_output(platform):
    """One shared budget covers both phones and stale artifacts cannot be published."""
    first, second = register_pool_member(platform), register_pool_member(platform)
    job = submit_pool(platform)[0]
    original = claim(platform, first)
    failure = report(platform, first, original, "failed", 1, {
        "error_message": "Device disconnected", "metadata": {"failure_category": "infrastructure"},
    })
    assert failure.json()["state"] == "queued"
    assert claim(platform, first) is None
    replacement = claim(platform, second)
    assert replacement["id"] == job["id"] and replacement["attempt_id"] != original["attempt_id"]
    assert replacement["dispatch_attempt"] == 2
    assert report(platform, first, original, "completed", 2, {"metrics": {"accuracy": 100}}).status_code == 409
    content = b"superseded artifact"
    digest = hashlib.sha256(content).hexdigest()
    response = platform.client.put(
        PREFIX + f"/{first['id']}/jobs/{job['id']}/artifacts/{digest}",
        content=content, headers=gateway_headers(first) | {"X-Attempt-Id": original["attempt_id"]},
    )
    assert response.status_code == 409
    assert report(platform, second, replacement, "completed", 1, {"score": 7, "metrics": {"accuracy": 7}}).status_code == 200
    final = pool_result(platform, job)
    assert final["report"]["score"] == 7
    history = final["report"]["metadata"]["attempt_history"]
    assert [item["state"] for item in history] == ["failed", "completed"]
    assert [item["gateway_id"] for item in history] == [first["id"], second["id"]]


def test_pool_attempt_exhaustion_fails_only_the_evaluation_without_a_score(platform):
    """Three failed dispatches terminate one candidate while its parent keeps running."""
    first, second = register_pool_member(platform), register_pool_member(platform)
    job = submit_pool(platform)[0]
    for index, member in enumerate((first, second, first), 1):
        attempt = claim(platform, member)
        assert attempt["dispatch_attempt"] == index
        response = report(platform, member, attempt, "failed", 1, {
            "error_message": "Device unreachable", "metadata": {"failure_category": "infrastructure"},
        })
        assert response.json()["state"] == ("queued" if index < 3 else "failed")
    final = pool_result(platform, job)
    assert final["state"] == "failed" and final["report"]["metrics"] == {}
    assert final["report"].get("score") is None
    metadata = final["report"]["metadata"]
    assert metadata["attempts_exhausted"] is True
    assert metadata["failure_category"] == "infrastructure"
    assert len(metadata["attempt_history"]) == 3
    assert claim(platform, first) is claim(platform, second) is None
    with Session(platform.engine) as db:
        assert db.get(Task, platform.task.id).status == TaskStatus.RUNNING


@pytest.mark.parametrize("category", ["candidate", "configuration"])
def test_non_device_failures_do_not_consume_every_pool_member(platform, category):
    """Invalid candidate/configuration outcomes fail once without device roulette."""
    first, second = register_pool_member(platform), register_pool_member(platform)
    job = submit_pool(platform)[0]
    attempt = claim(platform, first)
    response = report(platform, first, attempt, "failed", 1, {
        "error_message": "Invalid input", "metadata": {"failure_category": category},
    })
    assert response.json()["state"] == "failed"
    final = pool_result(platform, job)
    assert final["report"]["metadata"]["attempt_count"] == 1
    assert final["report"]["metadata"]["attempts_exhausted"] is False
    assert claim(platform, second) is None


@pytest.mark.parametrize("mode", [["auto"], {"selected": True}])
def test_malformed_task_selection_mode_returns_422(platform, mode):
    """Reject raw task selection containers before component configuration validation."""
    member = register_pool_member(platform)
    configure(platform, [member])
    with Session(platform.engine) as db:
        task = db.get(Task, platform.task.id)
        task.input_args = {"evaluator": {**task.input_args["evaluator"], "selection_mode": mode}}
        db.add(task)
        db.commit()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("solution.py", "def solve(): return 1\n")
    response = platform.client.post(
        PREFIX + f"/tasks/{platform.task.id}/jobs",
        headers=runtime_headers(platform),
        json={
            "id": str(uuid.uuid4()), "gateway_ids": [member["id"]],
            "dispatch_version": 2, "selection_mode": "selected",
            "contract": CONTRACT, "timeout": 5,
            "bundle": base64.b64encode(archive.getvalue()).decode(),
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown remote evaluation selection mode"
    with Session(platform.engine) as db:
        assert db.exec(select(RemoteEvaluationJob)).all() == []


@pytest.mark.parametrize("state", ["failed", "completed"])
@pytest.mark.parametrize("category", [{}, [], {"candidate": True}, ["candidate"], "unknown", 7, False, None])
def test_malformed_pool_failure_categories_fall_back_to_evaluator(platform, state, category):
    """Untrusted category values cannot crash reporting or fabricate candidate blame."""
    member = register_pool_member(platform)
    job = submit_pool(platform, max_attempts=1)[0]
    attempt = claim(platform, member)
    response = report(platform, member, attempt, state, 1, {
        "success": False,
        "error_message": "Evaluator returned malformed metadata",
        "metrics": {},
        "metadata": {
            "failure_category": category,
            "candidate_failure": "true",
            "infrastructure_failure": {"value": True},
        },
    })
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "failed"
    final = pool_result(platform, job)
    assert final["state"] == "failed"
    assert final["report"].get("score") is None
    metadata = final["report"]["metadata"]
    assert metadata["failure_category"] == "evaluator"
    assert metadata["candidate_failure"] is False
    assert metadata["infrastructure_failure"] is False
    assert metadata["attempts_exhausted"] is True
    assert metadata["attempt_history"][0]["failure_category"] == "evaluator"


def test_expired_pool_lease_transfers_and_late_heartbeat_cannot_resurrect_it(platform):
    """A disconnected phone loses publication authority independently of cleanup."""
    first, second = register_pool_member(platform), register_pool_member(platform)
    job = submit_pool(platform)[0]
    original = claim(platform, first)
    force_elapsed_lease(platform, original)
    response = platform.client.post(PREFIX + f"/{first['id']}/heartbeat", headers=gateway_headers(first), json={"ready": False})
    assert response.json()["jobs"] == []
    replacement = claim(platform, second)
    assert replacement["attempt_id"] != original["attempt_id"]
    assert replacement["dispatch_attempt"] == 2
    assert report(platform, first, original, "running", 1).status_code == 409
    assert pool_result(platform, job)["attempt_id"] == replacement["attempt_id"]


def test_pool_cancellation_prevents_failover_even_when_lease_expires(platform):
    """Stopping the parent takes precedence over retries and stale device reports."""
    first, second = register_pool_member(platform), register_pool_member(platform)
    job = submit_pool(platform)[0]
    original = claim(platform, first)
    with Session(platform.engine) as db:
        task = db.get(Task, platform.task.id)
        task.status = TaskStatus.FAILED
        db.add(task)
        db.commit()
        service.cancel_task_jobs(db, task.id)
    force_elapsed_lease(platform, original)
    assert claim(platform, second) is None
    with Session(platform.engine) as db:
        current = db.get(RemoteEvaluationJob, uuid.UUID(job["id"]))
        assert current.state == "cancelled"
        assert current.attempt_count == 1


def test_selected_pool_does_not_expand_to_unselected_devices(platform):
    """Manual opt-in remains available while automatic pools have no fixed IDs."""
    first, second = register_pool_member(platform), register_pool_member(platform)
    submit_pool(platform, mode="selected", members=[first])
    assert claim(platform, second) is None
    assert claim(platform, first) is not None


def test_lease_expiry_consumes_the_same_total_attempt_budget(platform):
    """An offline last attempt becomes a terminal infrastructure failure on read."""
    member = register_pool_member(platform)
    job = submit_pool(platform, max_attempts=1)[0]
    original = claim(platform, member)
    force_elapsed_lease(platform, original)
    final = pool_result(platform, job)
    assert final["state"] == "failed"
    assert final["attempt_id"] != original["attempt_id"]
    assert final["report"]["metadata"]["attempts_exhausted"] is True
    assert report(platform, member, original, "completed", 1, {"metrics": {"accuracy": 9}}).status_code == 409


def test_pool_without_devices_waits_without_spending_attempts(platform):
    """No resource acquisition is an idle wait, even before the first gateway joins."""
    job = submit_pool(platform)[0]
    for _ in range(3):
        status = pool_result(platform, job)
        assert status["state"] == "queued" and status["dispatch_attempt"] == 0
        assert status["lease_expires_at"] is None
    views = platform.client.get(PREFIX + f"/tasks/{platform.task.id}").json()
    assert views[0]["state"] == "waiting_gateway" and views[0]["attempt_count"] == 0
    member = register_pool_member(platform)
    assert claim(platform, member)["dispatch_attempt"] == 1


def test_uncertain_original_cleanup_does_not_block_an_independent_pool_device(platform):
    """Fencing permits another phone while the old SDK retains its cleanup debt."""
    first, second = register_pool_member(platform), register_pool_member(platform)
    submit_pool(platform)
    original = claim(platform, first)
    response = report(platform, first, original, "recovery_required", 1, {
        "message": "Owned device cleanup cannot yet be confirmed", "retry_confirmed": False,
    })
    assert response.status_code == 200 and response.json()["state"] == "queued"
    replacement = claim(platform, second)
    assert replacement["dispatch_attempt"] == 2
    assert replacement["attempt_id"] != original["attempt_id"]


@pytest.mark.parametrize("unavailable", ["busy", "offline", "not_ready"])
def test_unavailable_pool_waiting_does_not_count_as_a_device_failure(platform, unavailable):
    """Only a claim consumes budget; unavailable devices cause no lease or failure."""
    member = register_pool_member(platform)
    if unavailable == "busy":
        submit_pool(platform)
        assert claim(platform, member) is not None
    job = submit_pool(platform)[0]
    with Session(platform.engine) as db:
        gateway = db.get(EvaluationGateway, uuid.UUID(member["id"]))
        if unavailable == "offline":
            gateway.last_seen = datetime.now(UTC) - timedelta(days=1)
        elif unavailable == "not_ready":
            gateway.ready = False
        db.add(gateway)
        db.commit()
    if unavailable != "offline":
        assert claim(platform, member) is None
    status = pool_result(platform, job)
    assert status["state"] == "queued"
    assert status["dispatch_attempt"] == 0 and status["lease_expires_at"] is None
    assert status["report"]["metadata"]["attempt_history"] == []


@pytest.mark.parametrize("disconnect", [False, True])
def test_actual_sdk_pool_hands_a_failed_attempt_to_another_worker(platform, tmp_path, monkeypatch, disconnect):
    """Exercise two real SDK process pools, HTTP leases and a single runtime call."""
    import socket
    import threading

    import aiohttp
    import uvicorn
    from llm4ad.config.evaluator import EvalContext, RemoteEvaluatorConfig
    from llm4ad.gateway.sdk.client import evaluate_remote
    from llm4ad.gateway.sdk.gateway import Gateway

    monkeypatch.setattr(api, "_NOTIFICATION_INTERVAL", 0.05)
    host = FastAPI()
    host.mount("/api/v1/llm4ad", platform.app)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(host, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    counters = [tmp_path / "first-calls.txt", tmp_path / "second-calls.txt"]
    specs = []
    for index in range(2):
        path = tmp_path / f"adapter_{index}.py"
        outcome = ("await asyncio.sleep(30)" if disconnect else "raise EvaluationUnavailableError('Device detached')") if index == 0 else "return EvaluationResult(score=7, metrics={'accuracy': 7})"
        path.write_text(
            "import asyncio\nfrom pathlib import Path\n"
            "from llm4ad.gateway.sdk import GatewayEvaluator, EvaluationResult, EvaluationUnavailableError\n"
            "from llm4ad.gateway.sdk.contracts import EvaluationContract\n"
            "class Evaluator(GatewayEvaluator):\n"
            f"    contract = EvaluationContract.model_validate({CONTRACT!r})\n"
            "    async def evaluate(self, job, reporter):\n"
            f"        with Path({str(counters[index])!r}).open('a') as stream: stream.write('called\\n')\n"
            f"        {outcome}\n"
        )
        specs.append(f"{path}:Evaluator")
    first = Gateway(address, "first", [str(platform.owner.id)], specs[0], tmp_path / "gateway-first")
    second = Gateway(address, "second", [str(platform.owner.id)], specs[1], tmp_path / "gateway-second")
    original_request = first.request
    blocked = False

    async def transport(*args, **kwargs):
        if blocked:
            raise aiohttp.ClientConnectionError("Fixture network disconnected")
        return await original_request(*args, **kwargs)

    monkeypatch.setattr(first, "request", transport)
    monkeypatch.setenv("LLM4AD_REMOTE_URL", address + "/api/v1/llm4ad/evaluation-gateways")
    monkeypatch.setenv("LLM4AD_REMOTE_TOKEN", service.runtime_token(platform.task.id))
    monkeypatch.setenv("LLM4AD_REMOTE_TASK_ID", str(platform.task.id))
    config = RemoteEvaluatorConfig(contract=CONTRACT, lease_timeout=3)
    with Session(platform.engine) as db:
        task = db.get(Task, platform.task.id)
        task.input_args = {"evaluator": config.model_dump(mode="json")}
        db.add(task)
        db.commit()
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "solution.py").write_text("def solve(): return 1\n")

    async def until(predicate):
        async with asyncio.timeout(25):
            while not predicate():
                await asyncio.sleep(0.02)

    async def exercise():
        nonlocal blocked
        runners, request = [], None
        try:
            await until(lambda: server.started)
            runners.append(asyncio.create_task(first.run()))
            await until(lambda: len(platform.client.get(PREFIX).json()) == 1)
            response = platform.client.post(PREFIX + "/register", json={
                **second.identity, "name": "second", "allowed_user_ids": [str(platform.owner.id)],
                "capacity": 1, "contract": CONTRACT, "protocol_version": 2,
            })
            assert response.status_code == 200
            request = asyncio.create_task(evaluate_remote(config, EvalContext(project_root=str(candidate), timeout=60)))
            await until(lambda: counters[0].exists())
            if disconnect:
                blocked = True
            runners.append(asyncio.create_task(second.run()))
            await until(request.done)
            result = request.result()
            assert result.success and result.score == 7
            assert [path.read_text().splitlines() for path in counters] == [["called"], ["called"]]
            history = result.metadata["attempt_history"]
            assert [row["gateway_id"] for row in history] == [first.identity["id"], second.identity["id"]]
            assert history[0]["failure_category"] == "infrastructure"
            assert result.metadata["attempt_count"] == 2
            with Session(platform.engine) as db:
                jobs = db.exec(select(RemoteEvaluationJob)).all()
                assert len(jobs) == 1 and jobs[0].state == "completed"
        finally:
            if request and not request.done():
                request.cancel()
            for runner in runners:
                runner.cancel()
            await asyncio.gather(*runners, *([request] if request else []), return_exceptions=True)

    try:
        asyncio.run(exercise())
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


def test_pool_migration_preserves_legacy_identity_and_default_ownership(platform):
    """Upgrade existing rows without silently enabling leased failover for old jobs."""
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    member = register(platform)
    configure(platform, [member])
    original = submit(platform, [member])
    path = Path(api.__file__).resolve().parents[2] / "alembic/versions/c62f8a901de3_dynamic_evaluation_pool.py"
    spec = importlib.util.spec_from_file_location("pool_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with platform.engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        migration.upgrade()
    with Session(platform.engine) as db:
        row = db.get(RemoteEvaluationJob, uuid.UUID(original["id"]))
        gateway = db.get(EvaluationGateway, uuid.UUID(member["id"]))
        assert str(row.attempt_id) == original["attempt_id"]
        assert row.selection_mode == "selected" and row.max_attempts == 1
        assert row.attempt_count == 0 and row.attempt_history == []
        assert row.lease_expires_at is None
        assert gateway.protocol_version == 1 and gateway.ready is True


@pytest.mark.skipif(not os.environ.get("REMOTE_TEST_DATABASE_URL"), reason="Requires PostgreSQL row locks")
def test_postgres_dynamic_pool_claims_preserve_unique_attempts_and_device_capacity(platform):
    """Simultaneous claimers cannot reserve a phone twice or duplicate a candidate."""
    from concurrent.futures import ThreadPoolExecutor

    first, second = register_pool_member(platform), register_pool_member(platform)
    submit_pool(platform, count=8)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda gateway: claim(platform, gateway), [first, second, first, second]))
    assigned = [row for row in responses if row is not None]
    assert len(assigned) == len({row["id"] for row in assigned}) == 2
    with Session(platform.engine) as db:
        jobs = db.exec(select(RemoteEvaluationJob)).all()
        for member in (first, second):
            assert sum(str(job.gateway_id) == member["id"] and job.state == "accepted" for job in jobs) == 1
        assert sum(job.state == "queued" and job.attempt_count == 0 for job in jobs) == 6


@pytest.mark.skipif(not os.environ.get("REMOTE_TEST_DATABASE_URL"), reason="Requires PostgreSQL row locks")
def test_artifact_streaming_does_not_block_attempt_lease_renewal(platform):
    """A slow upload cannot hold the job lock and cause an otherwise healthy lease to expire."""
    from sqlalchemy import text

    member = register_pool_member(platform)
    submit_pool(platform)
    job = claim(platform, member)
    content = b"two-chunk evaluation trace"
    digest = hashlib.sha256(content).hexdigest()
    renewals = []

    def renew():
        with Session(platform.engine) as session:
            session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
            renewals.append(api.heartbeat(
                uuid.UUID(member["id"]), session, body=api.Heartbeat(ready=False),
                authorization=gateway_headers(member)["Authorization"],
            ))

    async def stream():
        yield content[:8]
        await asyncio.to_thread(renew)
        yield content[8:]

    with Session(platform.engine) as session:
        result = asyncio.run(api.upload_artifact(
            uuid.UUID(member["id"]), uuid.UUID(job["id"]), digest,
            SimpleNamespace(stream=stream), session,
            authorization=gateway_headers(member)["Authorization"], x_attempt_id=uuid.UUID(job["attempt_id"]),
        ))
    assert result["sha256"] == digest
    assert len(renewals) == 1 and renewals[0]["jobs"][0]["attempt_id"] == job["attempt_id"]
    assert platform.objects[result["object_key"]] == content


def test_busy_untried_peer_does_not_block_an_available_pool_member(platform):
    """Prefer another device only when it has a free execution slot."""
    first, second = register_pool_member(platform), register_pool_member(platform)
    submit_pool(platform, count=2)
    original = claim(platform, first)
    busy = claim(platform, second)
    assert busy["id"] != original["id"]
    assert report(platform, first, original, "failed", 1, {
        "error_message": "Temporary transport failure", "metadata": {"failure_category": "infrastructure"},
    }).json()["state"] == "queued"
    retry = claim(platform, first)
    assert retry["id"] == original["id"] and retry["dispatch_attempt"] == 2


@pytest.mark.parametrize("requested_mode", ["auto", "selected"])
def test_v1_submission_cannot_downgrade_a_frozen_automatic_pool_policy(platform, requested_mode):
    """A runtime cannot evade the configured lease and shared attempt budget."""
    member = register_pool_member(platform)
    submit_pool(platform, members=[member])
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("solution.py", "def solve(): return 1\n")
    response = platform.client.post(PREFIX + f"/tasks/{platform.task.id}/jobs", headers=runtime_headers(platform), json={
        "id": str(uuid.uuid4()), "gateway_ids": [member["id"]], "selection_mode": requested_mode,
        "contract": CONTRACT, "timeout": 5, "bundle": base64.b64encode(archive.getvalue()).decode(),
    })
    assert response.status_code == 422


@pytest.mark.skipif(not os.environ.get("REMOTE_TEST_DATABASE_URL"), reason="Requires PostgreSQL row locks")
def test_heartbeat_does_not_invert_the_expired_job_and_gateway_lock_order(platform):
    """A renewal waiting on an expired job must not hold its gateway hostage."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import text

    member = register_pool_member(platform)
    submit_pool(platform)
    original = claim(platform, member)
    force_elapsed_lease(platform, original)
    entered = threading.Event()
    identity = []

    def renew():
        with Session(platform.engine) as session:
            identity.append(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            entered.set()
            return api.heartbeat(uuid.UUID(member["id"]), session, authorization=gateway_headers(member)["Authorization"])

    with ThreadPoolExecutor(max_workers=1) as workers:
        with Session(platform.engine) as owner:
            service.lock_job(owner, uuid.UUID(original["id"]))
            future = workers.submit(renew)
            assert entered.wait(3)
            with Session(platform.engine) as observer:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    observer.expire_all()
                    waiting = observer.execute(text(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"
                    ), {"pid": identity[0]}).scalar_one()
                    observer.commit()
                    if waiting == "Lock":
                        break
                    time.sleep(0.01)
                assert waiting == "Lock"
            owner.execute(text("SET LOCAL lock_timeout = '1000ms'"))
            owner.exec(select(EvaluationGateway).where(EvaluationGateway.id == uuid.UUID(member["id"])).with_for_update()).one()
            owner.commit()
        assert future.result(timeout=5)["jobs"] == []
