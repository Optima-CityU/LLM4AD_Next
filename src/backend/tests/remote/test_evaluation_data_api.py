"""Task-authorized local and historical remote evaluation evidence tests."""

import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest
from llm4ad.config.evaluator import EvalContext
from llm4ad.evaluator.models import EvaluationArtifact, EvaluationResult
from llm4ad.evaluator.records import complete_record, new_record
from sqlmodel import Session
from test_gateway_api import platform as platform

from app.api.deps import get_current_user
from app.models import RemoteEvaluationJob, Task, TaskStatus
from app.services import evaluation_data as service


def local_record(platform, *, name="报告.json", data=b'{"value": 42}', media_type="application/json", success=True):
    """Capture a real local result using the same recorder as the dispatcher."""
    root = service.outbox_root(platform.task.id, platform.owner.id)
    identity = str(uuid.uuid4())
    directory = root / identity
    (directory / "output").mkdir(parents=True)
    (directory / "output" / "file").write_bytes(data)
    cfg = EvalContext(
        project_root="",
        evaluation_id=identity,
        algorithm_id="abc123def456",
        algorithm_name="Candidate",
        artifact_dir=str(directory / "output"),
        execution_id="run-1",
        source_fingerprint="source-a",
    )
    result = EvaluationResult(
        score=4,
        metrics={"quality": 4},
        success=success,
        artifacts=[EvaluationArtifact(name=name, path="file", media_type=media_type)],
    )
    record = complete_record(directory, new_record(cfg, task_id=str(platform.task.id)), result)
    return root, record


def url(platform, record=None, artifact=None):
    """Build task-bound public URLs."""
    path = f"/tasks/{platform.task.id}/evaluations"
    if record:
        path += f"/{record.id}"
    if artifact:
        path += f"/artifacts/{artifact.id}"
    return path


def test_local_upload_preview_download_and_ack_cleanup(platform):
    """Save exact bytes, expose typed details and prune only acknowledged bulk."""
    root, record = local_record(platform)
    assert service.upload_pending(root, str(platform.task.id))
    assert not (root / record.id / "files").exists()
    assert (root / record.id / "ack.json").exists()
    assert service.upload_pending(root, str(platform.task.id))
    response = platform.client.get(url(platform))
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert response.json()["data"][0]["storage_state"] == "saved"
    detail = platform.client.get(url(platform, record)).json()
    assert detail["record"]["source_fingerprint"] == "source-a"
    assert "object_key" not in json.dumps(detail)
    response = platform.client.get(url(platform, record, record.artifacts[0]) + "?download=false")
    assert response.status_code == 200 and response.content == b'{"value": 42}'
    assert response.headers["x-content-type-options"] == "nosniff"
    response = platform.client.get(url(platform, record, record.artifacts[0]))
    assert response.status_code == 200 and "filename*=UTF-8''" in response.headers["content-disposition"]


def test_transfer_failure_is_visible_and_retry_does_not_reexecute(platform, monkeypatch):
    """A lost object-store write keeps local bytes and resumes transfer only."""
    root, record = local_record(platform)
    original = service.storage.upload

    def unavailable(*_args, **_kwargs):
        raise OSError("Object store temporarily unavailable")

    monkeypatch.setattr(service.storage, "upload", unavailable)
    assert not service.upload_pending(root, str(platform.task.id))
    assert (root / record.id / "files" / record.artifacts[0].sha256).exists()
    assert not (root / record.id / "ack.json").exists()
    pending = platform.client.get(url(platform)).json()
    assert pending["data"][0]["storage_state"] == "error"
    monkeypatch.setattr(service.storage, "upload", original)
    assert service.upload_pending(root, str(platform.task.id))
    assert platform.client.get(url(platform)).json()["data"][0]["storage_state"] == "saved"


def test_task_owner_and_manifest_boundaries(platform):
    """Do not expose a sibling task, another user's record, or arbitrary objects."""
    root, record = local_record(platform)
    service.upload_pending(root, str(platform.task.id))
    platform.app.dependency_overrides[get_current_user] = lambda: platform.other
    assert platform.client.get(url(platform)).status_code == 403
    assert platform.client.get(url(platform, record, record.artifacts[0])).status_code == 403
    platform.app.dependency_overrides[get_current_user] = lambda: platform.owner
    with Session(platform.engine) as db:
        sibling = Task(name="child", project_id=platform.task.project_id, parent_id=platform.task.id)
        db.add(sibling)
        db.commit()
        db.refresh(sibling)
        sibling_id = sibling.id
    assert platform.client.get(f"/tasks/{sibling_id}/evaluations/{record.id}").status_code == 404
    missing = url(platform, record) + f"/artifacts/{'0' * 64}"
    assert platform.client.get(missing).status_code == 404


def test_corrupted_file_is_not_served(platform):
    """A manifest cannot silently serve different bytes after storage corruption."""
    root, record = local_record(platform)
    service.upload_pending(root, str(platform.task.id))
    key = service.prefix(platform.task.id) + f"files/{record.id}/{record.artifacts[0].sha256}"
    platform.objects[key] = b"corrupted"
    assert platform.client.get(url(platform, record, record.artifacts[0])).status_code == 409


def test_binary_download_and_html_safe_text_preview(platform):
    """Trace remains downloadable; untrusted markup is served as plain text."""
    root, record = local_record(
        platform, name="trace.pftrace", data=b"trace bytes", media_type="application/octet-stream"
    )
    service.upload_pending(root, str(platform.task.id))
    assert platform.client.get(url(platform, record, record.artifacts[0]) + "?download=false").status_code == 415
    assert platform.client.get(url(platform, record, record.artifacts[0])).content == b"trace bytes"
    root, html = local_record(platform, name="report.html", data=b"<script>bad()</script>", media_type="text/html")
    service.upload_pending(root, str(platform.task.id))
    response = platform.client.get(url(platform, html, html.artifacts[0]) + "?download=false")
    assert response.headers["content-type"].startswith("text/plain")
    assert "sandbox" in response.headers["content-security-policy"]


def test_filters_pagination_and_failed_candidate(platform):
    """Keep stage/instance details separate while retaining unsuccessful evidence."""
    root, first = local_record(platform)
    _, second = local_record(platform, success=False)
    second.evaluation_profile = "elite"
    path = root / second.id / "record.json"
    path.write_text(second.model_dump_json())
    service.upload_pending(root, str(platform.task.id))
    response = platform.client.get(url(platform) + "?page_size=1")
    assert response.json()["count"] == 2 and len(response.json()["data"]) == 1
    response = platform.client.get(url(platform) + "?evaluation_profile=elite&algorithm_id=abc123def456")
    assert response.json()["count"] == 1
    assert response.json()["data"][0]["id"] == second.id
    assert response.json()["data"][0]["score"] is None
    assert platform.client.get(url(platform) + "?algorithm_id=other").json()["count"] == 0


def test_historical_remote_result_requires_no_file_migration(platform):
    """Adapt existing gateway manifests and reuse their original object keys."""
    job_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    content = b"historical trace"
    digest = hashlib.sha256(content).hexdigest()
    file_key = f"remote-evaluation/{platform.task.id}/{job_id}/artifacts/{attempt_id}/{digest}"
    result_key = f"remote-evaluation/{platform.task.id}/{job_id}/result-{attempt_id}.json"
    platform.objects[file_key] = content
    platform.objects[result_key] = json.dumps(
        {
            "score": 1,
            "metrics": {"latency": 2},
            "metadata": {
                "artifacts": [
                    {
                        "name": "capture.pftrace",
                        "sha256": digest,
                        "size": len(content),
                        "media_type": "application/octet-stream",
                        "object_key": file_key,
                    }
                ]
            },
        }
    ).encode()
    with Session(platform.engine) as db:
        db.add(
            RemoteEvaluationJob(
                id=job_id,
                attempt_id=attempt_id,
                task_id=platform.task.id,
                user_id=platform.owner.id,
                selected_gateway_ids=[],
                contract_key="fixture",
                object_key="unused",
                state="completed",
                request={},
                report={"result_key": result_key, "score": 1},
                created_at=datetime.now(UTC),
            )
        )
        db.commit()
    from app.models import TaskLog

    with Session(platform.engine) as db:
        db.add(
            TaskLog(
                task_id=platform.task.id,
                type="generated",
                data={
                    "id": "legacycandidate",
                    "name": "Historical candidate",
                    "custom_metadata": {
                        "evaluation_profiles": {
                            "standard": {
                                "source_fingerprint": "legacy-source",
                                "evidence": {"remote_job_id": str(job_id)},
                            }
                        }
                    },
                },
            )
        )
        db.commit()
    response = platform.client.get(url(platform) + "?algorithm_id=legacycandidate")
    assert response.json()["count"] == 1
    assert response.json()["data"][0]["algorithm_name"] == "Historical candidate"
    assert response.json()["data"][0]["artifact_count"] == 1
    detail_url = url(platform) + f"/{attempt_id}"
    detail = platform.client.get(detail_url).json()
    artifact = detail["record"]["artifacts"][0]
    assert platform.client.get(detail_url + f"/artifacts/{artifact['id']}").content == content
    assert len(platform.objects) == 2


def test_worker_restart_recovers_pending_data(platform, monkeypatch):
    """Reconcile the persistent outbox against the real task database."""
    from contextlib import contextmanager

    root, record = local_record(platform)

    @contextmanager
    def database():
        with Session(platform.engine) as db:
            yield db

    monkeypatch.setattr("app.core.db.get_db_session", database)
    assert service.retry_outboxes()
    assert (root / record.id / "ack.json").exists()
    assert platform.client.get(url(platform)).json()["count"] == 1


def test_existing_task_deletion_cleans_all_evaluation_namespaces(platform):
    """The existing delete operation owns evidence, even after evaluator config changes."""
    from sqlmodel import SQLModel

    from app.models import AutoDesignSession, PaperWorkspace, TaskLog, TaskStatus
    from app.services.task_service.crud import delete_task

    SQLModel.metadata.create_all(
        platform.engine, tables=[PaperWorkspace.__table__, AutoDesignSession.__table__, TaskLog.__table__]
    )
    root, record = local_record(platform)
    service.upload_pending(root, str(platform.task.id))
    remote_key = f"remote-evaluation/{platform.task.id}/old-evidence"
    platform.objects[remote_key] = b"older evaluator evidence"
    with Session(platform.engine) as db:
        task = db.get(Task, platform.task.id)
        task.status = TaskStatus.COMPLETED
        db.add(task)
        db.commit()
        delete_task(db, task.id, platform.owner)
        assert db.get(Task, platform.task.id) is None
    assert not root.exists()
    assert not any(key.startswith(service.prefix(platform.task.id)) for key in platform.objects)
    assert remote_key not in platform.objects


def test_pending_outbox_is_not_under_disposable_run_directory(platform, tmp_path):
    """Container mounts retain snapshots when run initialization clears old files."""
    from app.services.evolution_runner import _build_spec

    run_dir = tmp_path / "user" / str(platform.task.id)
    spec = _build_spec({"task_id": str(platform.task.id), "run_dir": str(run_dir), "run_args": {}}, "test-key")
    assert spec.env["LLM4AD_TASK_ID"] == str(platform.task.id)
    assert spec.env["LLM4AD_EXECUTION_ID"]
    outbox_source = next(path for path, mount in spec.mounts.items() if mount == "/task/evaluation-outbox")
    assert not __import__("pathlib").Path(outbox_source).is_relative_to(run_dir)


def test_preview_limit_does_not_limit_original_download(platform, monkeypatch):
    """Large-file preview refusal must leave the original download available."""
    from app.core.config import settings

    root, record = local_record(platform)
    service.upload_pending(root, str(platform.task.id))
    monkeypatch.setattr(settings, "EVALUATION_PREVIEW_MAX_BYTES", 1)
    artifact_url = url(platform, record, record.artifacts[0])
    assert platform.client.get(artifact_url + "?download=false").status_code == 413
    assert platform.client.get(artifact_url).status_code == 200


def test_deleted_historical_payload_is_explicitly_unavailable(platform):
    """Do not invent files or leave a missing historical result perpetually pending."""
    identity, attempt = uuid.uuid4(), uuid.uuid4()
    with Session(platform.engine) as db:
        db.add(
            RemoteEvaluationJob(
                id=identity,
                attempt_id=attempt,
                task_id=platform.task.id,
                user_id=platform.owner.id,
                selected_gateway_ids=[],
                contract_key="fixture",
                object_key="unused",
                state="completed",
                request={},
                report={"result_key": f"remote-evaluation/{platform.task.id}/{identity}/result-{attempt}.json"},
            )
        )
        db.commit()
    response = platform.client.get(url(platform) + f"/{attempt}")
    assert response.status_code == 200
    assert response.json()["record"]["storage_state"] == "unavailable"
    assert response.json()["record"]["artifacts"] == []


def test_storage_failure_never_changes_container_completion(platform, tmp_path, monkeypatch):
    """A final transfer enqueue failure cannot rewrite a completed evolution task."""
    from types import SimpleNamespace

    from app.services import evolution_runner
    from app.services.container_runtime import ContainerJobStatus
    from app.tasks.evaluation_data import recover_uploads

    monkeypatch.setattr(evolution_runner, "_write_encrypted_config", lambda *_: "test-key")
    monkeypatch.setattr(evolution_runner, "push_log_entry", lambda *_: None)

    class CompletedContainer:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self):
            return SimpleNamespace(status=ContainerJobStatus.COMPLETED, exit_code=0)

    def unavailable():
        raise OSError("Broker unavailable")

    monkeypatch.setattr(evolution_runner, "ContainerJob", CompletedContainer)
    monkeypatch.setattr(recover_uploads, "delay", unavailable)
    result = evolution_runner.run_evolution_container(
        {
            "task_id": str(platform.task.id),
            "run_dir": str(tmp_path / "run"),
            "run_args": {},
        },
        check_cancelled=lambda: False,
    )
    assert result["status"] == "success"


def test_image_bytes_are_available_for_inline_preview(platform):
    """Serve a valid small PNG through the same verified attachment channel."""
    import struct
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )
    root, record = local_record(platform, name="measurement.png", data=png, media_type="application/octet-stream")
    service.upload_pending(root, str(platform.task.id))
    response = platform.client.get(url(platform, record, record.artifacts[0]) + "?download=false")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == png


def test_transfer_lease_waits_cooperatively(platform, monkeypatch):
    """Contending transfers must not freeze a gevent worker's heartbeat loop."""
    import threading
    import time
    from types import SimpleNamespace

    import pytest

    gevent = pytest.importorskip("gevent")
    root = service.outbox_root(platform.task.id, platform.owner.id)
    held, release = threading.Event(), threading.Event()
    events = []

    def holder():
        with service.evidence_lease(root):
            held.set()
            release.wait(0.5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(1)
    monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=time.monotonic, sleep=gevent.sleep))

    def contender():
        with service.evidence_lease(root):
            events.append("acquired")

    def heartbeat():
        gevent.sleep(0.01)
        events.append("heartbeat")
        release.set()

    workers = [gevent.spawn(contender), gevent.spawn(heartbeat)]
    try:
        gevent.joinall(workers, timeout=2, raise_error=True)
        assert events == ["heartbeat", "acquired"]
    finally:
        release.set()
        thread.join(timeout=1)
        for worker in workers:
            worker.kill()


def test_object_store_outage_keeps_local_records_visible(platform, monkeypatch):
    """Read pending and acknowledged local details even while RustFS is offline."""
    root, record = local_record(platform)
    assert platform.client.get(url(platform)).json()["data"][0]["transfer_pending"] is True
    service.upload_pending(root, str(platform.task.id))

    def offline(*_args, **_kwargs):
        raise OSError("Object storage offline")

    monkeypatch.setattr(service.storage, "list_objects", offline)
    monkeypatch.setattr(service.storage, "download", offline)
    response = platform.client.get(url(platform))
    assert response.status_code == 200
    assert response.json()["storage_available"] is False
    assert response.json()["data"][0]["id"] == record.id
    detail = platform.client.get(url(platform, record)).json()
    assert detail["record"]["storage_state"] == "offline"
    assert detail["record"]["result"]["score"] == 4
    assert detail["available_artifact_ids"] == []
    assert (root / record.id / "ack.json").exists()


def test_missing_original_returns_not_found_instead_of_server_error(platform):
    """An externally removed file should not trigger a global maintenance error."""
    root, record = local_record(platform)
    service.upload_pending(root, str(platform.task.id))
    key = service.prefix(platform.task.id) + f"files/{record.id}/{record.artifacts[0].sha256}"
    del platform.objects[key]
    assert platform.client.get(url(platform, record, record.artifacts[0])).status_code == 404


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_terminal_gateway_result_retains_files_and_candidate_references(platform, state):
    """Preserve results, messages and described diagnostics across both APIs."""
    from test_gateway_api import claim, configure, gateway_headers, register, report, runtime_headers, submit

    gateway = register(platform)
    configure(platform, [gateway])
    submit(platform, [gateway])
    job = claim(platform, gateway)
    data = b"synthetic failure diagnostic"
    digest = hashlib.sha256(data).hexdigest()
    receipt = platform.client.put(
        f"/evaluation-gateways/{gateway['id']}/jobs/{job['id']}/artifacts/{digest}",
        content=data,
        headers={**gateway_headers(gateway), "X-Attempt-Id": job["attempt_id"]},
    ).json()
    messages = [{"message": "Preparing evaluation"}, {"message": "Candidate diagnostic available"}]
    success = state == "completed"
    fields = {
        "score": 1,
        "metrics": {"accuracy": 1},
        "monitor_metrics": {"samples": 2},
        "duration_ms": 12.5,
        "evolution_feedback": {"hint": "Inspect the diagnostic"},
        "success": success,
        "error_message": None if success else "Candidate raised an error",
    }
    result = report(
        platform,
        gateway,
        job,
        state,
        2,
        {
            **fields,
            "message": "Candidate raised an error",
            "error_type": "RuntimeError",
            "metadata": {
                "artifacts": [{**receipt, "name": "failure.txt", "media_type": "text/plain"}],
                "artifact_descriptions": {"failure.txt": "Candidate diagnostic log"},
                "evaluation_messages": messages,
                "diagnostic": {"instance": "sample-1"},
            },
        },
    )
    assert result.status_code == 200, result.text
    runtime = platform.client.get(
        f"/evaluation-gateways/tasks/{platform.task.id}/jobs/{job['id']}",
        headers=runtime_headers(platform),
    ).json()["report"]
    for name, value in fields.items():
        assert runtime[name] == value
    if not success:
        assert runtime["message"] == runtime["error_message"] == "Candidate raised an error"
    assert runtime["metadata"]["evaluation_records"][0]["id"] == job["attempt_id"]
    assert runtime["metadata"]["evaluation_messages"] == messages
    assert runtime["metadata"]["artifact_descriptions"] == {"failure.txt": "Candidate diagnostic log"}
    detail = platform.client.get(url(platform) + f"/{job['attempt_id']}").json()
    assert detail["record"]["state"] == state
    for name, value in fields.items():
        assert detail["record"]["result"][name] == value
    assert detail["record"]["result"]["metadata"]["evaluation_messages"] == messages
    assert detail["record"]["result"]["metadata"]["diagnostic"] == {"instance": "sample-1"}
    assert detail["record"]["artifacts"][0]["description"] == "Candidate diagnostic log"
    artifact_id = detail["record"]["artifacts"][0]["id"]
    assert platform.client.get(url(platform) + f"/{job['attempt_id']}/artifacts/{artifact_id}").content == data


def test_unavailable_remote_result_retains_concise_failure(platform):
    """Keep a failed completed result understandable after its full payload is lost."""
    identity, attempt = uuid.uuid4(), uuid.uuid4()
    message = "Candidate output could not be parsed"
    with Session(platform.engine) as db:
        db.add(
            RemoteEvaluationJob(
                id=identity,
                attempt_id=attempt,
                task_id=platform.task.id,
                user_id=platform.owner.id,
                selected_gateway_ids=[],
                contract_key="fixture",
                object_key="unused",
                state="completed",
                request={},
                report={"success": False, "error_message": message},
            )
        )
        db.commit()
    response = platform.client.get(url(platform) + f"/{attempt}")
    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert record["state"] == "failed"
    assert record["result"]["error_message"] == message
    assert record["storage_state"] == "unavailable"


def test_outage_during_index_read_uses_local_record(platform, monkeypatch):
    """A connection lost after listing indexes still returns useful local evidence."""
    root, record = local_record(platform)
    service.upload_pending(root, str(platform.task.id))

    def offline(*_args, **_kwargs):
        raise OSError("Connection dropped after object listing")

    monkeypatch.setattr(service.storage, "download", offline)
    response = platform.client.get(url(platform))
    assert response.status_code == 200
    assert response.json()["storage_available"] is False
    assert response.json()["data"][0]["id"] == record.id


@pytest.mark.parametrize("candidate", ["candidate__v2", "__candidate__", "candidate___v2"])
def test_candidate_index_preserves_delimiter_characters(platform, candidate):
    """List and filter saved records whose valid candidate names contain index delimiters."""
    root, record = local_record(platform)
    record.algorithm_id = candidate
    (root / record.id / "record.json").write_text(record.model_dump_json())
    assert service.upload_pending(root, str(platform.task.id))
    for suffix in ("", f"?algorithm_id={candidate}"):
        response = platform.client.get(url(platform) + suffix)
        assert response.status_code == 200
        assert response.json()["count"] == 1
        assert response.json()["data"][0]["algorithm_id"] == candidate


@pytest.mark.parametrize("status", list(TaskStatus))
@pytest.mark.parametrize("origin", ["local", "remote"])
def test_recovery_closes_only_local_records_of_terminal_tasks(platform, monkeypatch, status, origin):
    """A terminal database task permits recovery; a live task or gateway-owned attempt does not."""
    from contextlib import contextmanager

    root, record = local_record(platform)
    record.state, record.origin, record.result = "running", origin, {}
    path = root / record.id / "record.json"
    path.write_text(record.model_dump_json())
    before = path.read_bytes()
    with Session(platform.engine) as db:
        task = db.get(Task, platform.task.id)
        task.status = status
        db.add(task)
        db.commit()

    @contextmanager
    def database():
        with Session(platform.engine) as db:
            yield db

    monkeypatch.setattr("app.core.db.get_db_session", database)
    assert service.upload_task_pending(platform.task.id)
    if origin == "local" and status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
        saved = json.loads(path.read_bytes())
        assert saved["state"] == "failed" and saved["result"]["success"] is False
        assert saved["result"]["metadata"]["failure_type"] == "EvaluationInterrupted"
        assert saved["execution_id"] == record.execution_id
        assert (root / record.id / "ack.json").exists()
        response = platform.client.get(url(platform, record))
        assert response.status_code == 200 and response.json()["record"]["state"] == "failed"
        assert platform.client.get(url(platform, record, record.artifacts[0])).content == b'{"value": 42}'
        reconciled = path.read_bytes()
        assert service.upload_task_pending(platform.task.id)
        assert path.read_bytes() == reconciled
    else:
        assert path.read_bytes() == before
        assert not (root / record.id / "ack.json").exists()
        assert platform.objects == {}


def test_terminal_task_recovery_preserves_already_completed_evaluation(platform, monkeypatch):
    """A task failure cannot replace an evaluation result that was already recorded."""
    from contextlib import contextmanager

    root, record = local_record(platform)
    with Session(platform.engine) as db:
        task = db.get(Task, platform.task.id)
        task.status = TaskStatus.FAILED
        db.add(task)
        db.commit()

    @contextmanager
    def database():
        with Session(platform.engine) as db:
            yield db

    monkeypatch.setattr("app.core.db.get_db_session", database)
    assert service.upload_task_pending(platform.task.id)
    saved = json.loads((root / record.id / "record.json").read_bytes())
    assert saved["state"] == "completed" and saved["result"]["score"] == 4


def test_finalization_requests_recovery_after_terminal_commit(platform, monkeypatch):
    """A reconciliation queued before container exit must be retried after terminal ownership is known."""
    from contextlib import contextmanager

    from app.tasks import evolution
    from app.tasks.evaluation_data import recover_uploads

    execution_id = str(uuid.uuid4())
    with Session(platform.engine) as db:
        task = db.get(Task, platform.task.id)
        task.celery_task_id = execution_id
        db.add(task)
        db.commit()

    @contextmanager
    def database():
        with Session(platform.engine) as db:
            yield db

    queued = []

    def recover():
        with Session(platform.engine) as db:
            queued.append(db.get(Task, platform.task.id).status)

    monkeypatch.setattr(evolution, "get_db_session", database)
    monkeypatch.setattr(evolution, "read_all_logs", lambda *_: [])
    monkeypatch.setattr(evolution, "push_log_entry", lambda *_: None)
    monkeypatch.setattr("app.services.credential_broker.revoke_task_tokens", lambda *_: 0)
    monkeypatch.setattr(recover_uploads, "delay", recover)
    evolution._finalize_task(execution_id, TaskStatus.FAILED)
    assert queued == [TaskStatus.FAILED]
