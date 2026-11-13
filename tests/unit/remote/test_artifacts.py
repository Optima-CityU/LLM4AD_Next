"""Exercise immutable evaluation evidence and retry-safe upload acknowledgements."""

import base64
import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from llm4ad.remote.gateway import Gateway
from llm4ad.remote.journal import Journal, Reporter


@pytest.fixture
def evidence(tmp_path):
    """Create a real local journal and an explicitly synthetic trace fixture."""
    journal = Journal(tmp_path / "jobs.db")
    job = {key: str(uuid.uuid4()) for key in ("id", "attempt_id", "task_id")}
    job["timeout"] = 60
    journal.accept(job)
    path = tmp_path / "trace.pftrace"
    path.write_bytes(b"synthetic trace for unit transport verification")
    return journal, job, path


def test_spooled_artifact_survives_source_change_and_restart(evidence):
    """Artifacts preserve original bytes independently of the evaluator workspace."""
    journal, job, path = evidence
    expected = path.read_bytes()
    Reporter(journal, job["id"]).artifact("game/A1/trace.pftrace", path)
    path.write_bytes(b"changed source")
    item = Journal(journal.path).artifacts(job["id"], job["attempt_id"])[0]
    assert Path(item["path"]).read_bytes() == expected
    assert item["sha256"] == hashlib.sha256(expected).hexdigest()
    with pytest.raises(ValueError, match="cannot change"):
        Reporter(journal, job["id"]).artifact("game/A1/trace.pftrace", path)


def test_stale_attempt_cannot_register_new_artifacts(evidence):
    """A delayed evaluator must not attach files to a replacement attempt."""
    journal, job, path = evidence
    stale = Reporter(journal, job["id"])
    replacement = job | {"attempt_id": str(uuid.uuid4())}
    journal.accept(replacement)
    with pytest.raises(ValueError, match="no longer accepts"):
        stale.artifact("trace.pftrace", path)
    assert journal.artifacts(job["id"], replacement["attempt_id"]) == []


@pytest.mark.parametrize("name", ["/absolute", "../outside", "a\\b"])
def test_unsafe_artifact_names_rejected(evidence, name):
    """Evidence labels cannot become cross-platform traversal paths."""
    journal, job, path = evidence
    with pytest.raises(ValueError):
        Reporter(journal, job["id"]).artifact(name, path)


async def test_upload_retries_original_bytes_and_reuses_ack(evidence):
    """A network outage leaves a retryable outbox and does not rerun evaluation."""
    journal, job, path = evidence
    Reporter(journal, job["id"]).artifact("trace.pftrace", path)
    worker = Gateway.__new__(Gateway)
    worker.journal, worker.identity = journal, {"id": str(uuid.uuid4())}
    worker.request = AsyncMock(side_effect=aiohttp.ClientConnectionError("offline"))
    with pytest.raises(aiohttp.ClientConnectionError):
        await worker.upload_artifacts(None, job)
    assert journal.artifacts(job["id"], job["attempt_id"])[0]["object_key"] is None

    async def uploaded(session, method, suffix, **kwargs):
        assert method == "PUT"
        assert kwargs["data"].read() == path.read_bytes()
        assert kwargs["headers"]["X-Attempt-Id"] == job["attempt_id"]
        return {"object_key": "owned-object"}

    worker.request = AsyncMock(side_effect=uploaded)
    first = await worker.upload_artifacts(None, job)
    second = await worker.upload_artifacts(None, job)
    assert first == second and first[0]["object_key"] == "owned-object"
    worker.request.assert_awaited_once()
    assert job["task_id"] in first[0]["download_path"]


async def test_hgamey_png_retains_description_and_uploads_original_bytes(evidence):
    """Carry an executor screenshot through durable SDK retention and upload."""
    from llm4ad.gateway.examples.hgamey.evaluator import HGameyShaderEvaluator

    journal, job, source = evidence
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    digest = hashlib.sha256(image).hexdigest()
    description = "Candidate (B1, round 1); captured after the measurement window."
    response = MagicMock()
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)

    async def chunks(size):
        yield image

    response.content.iter_chunked = chunks
    session = MagicMock()
    session.get.return_value = response
    adapter = HGameyShaderEvaluator.__new__(HGameyShaderEvaluator)
    adapter.benchmark = SimpleNamespace(url="http://executor.example.org", poll_interval=0.001)
    state = {"evidence_directory": str(source.parent / "attempt"), "game": {"external_job_id": "a" * 32}}
    report = {"artifacts": [{
        "name": "shots/B1.png", "sha256": digest, "size": len(image),
        "media_type": "image/png", "description": description,
    }]}
    reporter = Reporter(journal, job["id"])
    await adapter.retain_game_artifacts(session, reporter, state, report)
    await adapter.retain_game_artifacts(session, reporter, state, report)
    session.get.assert_called_once()
    (Path(state["evidence_directory"]) / "game-artifacts" / digest).unlink()

    worker = Gateway.__new__(Gateway)
    worker.journal, worker.identity = journal, {"id": str(uuid.uuid4())}
    worker.request = AsyncMock(side_effect=aiohttp.ClientConnectionError("offline"))
    with pytest.raises(aiohttp.ClientConnectionError):
        await worker.upload_artifacts(None, job)
    retained = journal.artifacts(job["id"], job["attempt_id"])[0]
    assert Path(retained["path"]).read_bytes() == image

    async def uploaded(session, method, suffix, **kwargs):
        assert kwargs["data"].read() == image
        assert kwargs["headers"]["Content-Type"] == "image/png"
        return {"object_key": "owned-png-object"}

    worker.request = AsyncMock(side_effect=uploaded)
    manifest = await worker.upload_artifacts(None, job)
    assert manifest[0]["name"] == "game/shots/B1.png"
    assert manifest[0]["description"] == description
    assert manifest[0]["media_type"] == "image/png"
    assert await worker.upload_artifacts(None, job) == manifest
    worker.request.assert_awaited_once()


async def test_missing_optional_screenshot_does_not_skip_other_images(evidence):
    """A missing A arm preserves the available B image in the real SDK outbox."""
    from llm4ad.gateway.examples.hgamey.evaluator import HGameyShaderEvaluator

    journal, job, source = evidence
    image = b"synthetic optional screenshot transport fixture"
    checksum = hashlib.sha256(image).hexdigest()
    missing = MagicMock()
    missing.__aenter__ = AsyncMock(side_effect=aiohttp.ClientResponseError(None, (), status=404))
    present = MagicMock()
    present.__aenter__ = AsyncMock(return_value=present)
    present.__aexit__ = AsyncMock(return_value=False)

    async def chunks(size):
        yield image

    present.content.iter_chunked = chunks
    session = MagicMock()
    session.get.side_effect = [missing, present]
    adapter = HGameyShaderEvaluator.__new__(HGameyShaderEvaluator)
    adapter.benchmark = SimpleNamespace(url="http://executor.example.org", poll_interval=0.001)
    state = {"evidence_directory": str(source.parent / "attempt"), "game": {"external_job_id": "a" * 32}}
    files = [
        {"name": f"shots/{side}1.png", "sha256": checksum, "size": len(image), "media_type": "image/png"}
        for side in "AB"
    ]
    errors = await adapter.retain_game_artifacts(session, Reporter(journal, job["id"]), state, {"artifacts": files})
    assert errors == ["game/shots/A1.png: ClientResponseError (HTTP 404)"]
    assert [item["name"] for item in journal.artifacts(job["id"], job["attempt_id"])] == ["game/shots/B1.png"]
    assert session.get.call_count == 2


@pytest.mark.parametrize("acknowledged,uploaded,managed,removed", [
    (False, True, False, False), (True, False, False, False),
    (True, True, True, False), (True, True, False, True),
])
def test_gateway_prunes_only_acknowledged_sdk_bulk(tmp_path, acknowledged, uploaded, managed, removed):
    """Generic cleanup must respect transfer ACKs and adapter-owned retention."""
    from types import SimpleNamespace

    root = tmp_path / "gateway"
    root.mkdir()
    journal = Journal(root / "jobs.sqlite3")
    job = {key: str(uuid.uuid4()) for key in ("id", "attempt_id", "task_id")}
    work = root / "work" / job["id"] / job["attempt_id"]
    output = work / ".evaluation-artifacts"
    output.mkdir(parents=True)
    job.update(timeout=60, project_root=str(work))
    journal.accept(job)
    original = output / "report.txt"
    original.write_text("retained output")
    Reporter(journal, job["id"]).artifact("report.txt", original)
    spool = Path(journal.artifacts(job["id"], job["attempt_id"])[0]["path"])
    if uploaded:
        with journal.connect() as db:
            db.execute("UPDATE artifacts SET object_key='saved'")
    journal.update(job["id"], "completed", {"success": True})
    if acknowledged:
        journal.acknowledge(job["id"], journal.get(job["id"])["seq"])
    worker = Gateway.__new__(Gateway)
    worker.root, worker.journal, worker.processes = root, journal, {}
    worker.adapter = SimpleNamespace(manages_evidence_retention=managed)
    worker.cleanup_evidence()
    assert spool.exists() is not removed
    assert output.exists() is not removed
    assert journal.get(job["id"])["state"] == "completed"


def test_returned_missing_artifact_keeps_remote_score(tmp_path, monkeypatch):
    """Invalid optional attachments cannot turn a valid measurement into failure."""
    from llm4ad.remote.gateway import execute

    monkeypatch.chdir(tmp_path)
    if hasattr(__import__("os"), "setsid"):
        monkeypatch.setattr("os.setsid", lambda: None)
    adapter = tmp_path / "adapter.py"
    adapter.write_text('''from llm4ad.remote.contracts import GatewayEvaluator, EvaluationContract
from llm4ad.evaluator.models import EvaluationResult, EvaluationArtifact, Metric
class Adapter(GatewayEvaluator):
    contract = EvaluationContract(evaluator_id="files", version="1", data_version="1", metrics=[Metric(name="value")])
    async def evaluate(self, job, reporter):
        return EvaluationResult(score=7, metrics={"value": 7}, artifacts=[EvaluationArtifact(name="missing.txt", path="missing.txt")])
''')
    journal = Journal(tmp_path / "journal.sqlite3")
    job = {key: str(uuid.uuid4()) for key in ("id", "attempt_id", "task_id")}
    job.update(timeout=60, project_root=str(tmp_path))
    journal.accept(job)
    execute(journal.path, f"{adapter}:Adapter", job)
    row = journal.get(job["id"])
    result = __import__("json").loads(row["report"])
    assert row["state"] == "completed" and result["score"] == 7 and result["success"]
    assert result["metadata"]["artifact_errors"]
