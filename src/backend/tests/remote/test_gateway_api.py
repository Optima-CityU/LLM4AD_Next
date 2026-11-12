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
