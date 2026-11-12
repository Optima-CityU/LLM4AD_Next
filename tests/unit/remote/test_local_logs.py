"""Exercise actual output capture and independent local-log ownership/retention."""

import json
import multiprocessing
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm4ad.gateway.sdk.gateway import execute
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.local_logs import LocalLogStore
from llm4ad.gateway.sdk.retention import (
    CleanupPlan,
    EvidencePolicy,
    collect_finished,
    collect_local_logs,
)


@pytest.fixture
def local(tmp_path):
    """Create one real journal and canonical SDK-owned candidate directory."""
    job = {
        "id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "timeout": 30,
    }
    work = tmp_path / "work" / job["id"] / job["attempt_id"]
    work.mkdir(parents=True)
    job["project_root"] = str(work)
    journal = Journal(tmp_path / "jobs.sqlite3")
    journal.accept(job)
    return journal, job, LocalLogStore(journal)


def test_local_outputs_are_not_reports_or_artifacts(local):
    """Writing a log cannot increment upload sequence or populate artifact outboxes."""
    journal, job, logs = local
    previous = journal.get(job["id"])["seq"]
    path = Reporter(journal, job["id"]).local_log(
        "compiler/stderr.log", description="Compiler diagnostics"
    )
    path.write_text("local only\n", encoding="utf-8")
    assert journal.get(job["id"])["seq"] == previous
    assert journal.artifacts(job["id"], job["attempt_id"]) == []
    sources = logs.list(job["id"], job["attempt_id"])
    assert sources[0]["name"] == "compiler/stderr.log" and sources[0]["available"]
    assert "path" not in sources[0]


@pytest.mark.parametrize(
    "name", ["../secret", "/etc/passwd", "x/../../secret", "x\\secret", "a/./b", ""]
)
def test_local_log_names_cannot_address_arbitrary_files(local, name):
    """The reader accepts source names, not browser-supplied filesystem paths."""
    _, job, logs = local
    with pytest.raises(ValueError):
        logs.create(job["id"], job["attempt_id"], name)


def test_utf8_incremental_reads_keep_incomplete_characters(local):
    """A character split across writes is returned once its bytes arrive."""
    _, job, logs = local
    path = logs.create(job["id"], job["attempt_id"], "worker.stdout")
    data = "编译\n".encode()
    path.write_bytes(data[:2])
    first = logs.read(job["id"], job["attempt_id"], "worker.stdout", offset=0)
    assert first["text"] == "" and first["next_offset"] == 0
    with path.open("ab") as stream:
        stream.write(data[2:])
    second = logs.read(job["id"], job["attempt_id"], "worker.stdout", offset=first["next_offset"])
    assert second["text"] == "编译\n" and second["next_offset"] == len(data)
    path.write_text("x")
    assert logs.read(job["id"], job["attempt_id"], "worker.stdout", offset=second["next_offset"])[
        "reset"
    ]


def test_missing_sources_and_stale_attempts_are_distinct(local):
    """A superseded attempt cannot read the replacement worker's output."""
    journal, job, logs = local
    path = logs.create(job["id"], job["attempt_id"], "worker.stdout")
    path.unlink()
    assert not logs.read(job["id"], job["attempt_id"], "worker.stdout")["available"]
    with pytest.raises(LookupError):
        logs.read(job["id"], job["attempt_id"], "unknown")
    journal.accept({**job, "attempt_id": str(uuid.uuid4())})
    with pytest.raises(ValueError, match="no longer current"):
        logs.list(job["id"], job["attempt_id"])


def test_registered_symlinks_are_rejected(local, tmp_path):
    """Declared sources cannot follow a symlink into unrelated local content."""
    _, job, logs = local
    target = tmp_path / "private.txt"
    target.write_text("private")
    link = tmp_path / "output.log"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        logs.register_external(job["id"], job["attempt_id"], "backend.stdout", link)


def test_download_is_a_snapshot_of_the_registered_source(local):
    """Appending or deleting the live source does not change an issued download."""
    _, job, logs = local
    path = logs.create(job["id"], job["attempt_id"], "worker.stdout")
    path.write_text("before\n")
    snapshot = logs.snapshot(job["id"], job["attempt_id"], "worker.stdout")
    try:
        path.write_text("after\n")
        assert snapshot.read_text() == "before\n"
    finally:
        snapshot.unlink()


def test_real_worker_capture_includes_children_without_uploading_output(local, tmp_path):
    """Spawn the SDK worker and check Python, file-descriptor and child-process output."""
    journal, job, logs = local
    adapter = tmp_path / "adapter.py"
    adapter.write_text("""import os, sys, subprocess
from llm4ad.gateway.sdk import GatewayEvaluator, EvaluationContract, EvaluationResult, Metric
class Evaluator(GatewayEvaluator):
    contract=EvaluationContract(evaluator_id="capture",version="1",data_version="1",metrics=[Metric(name="quality")])
    async def evaluate(self, job, reporter):
        print("python stdout", flush=True)
        print("python stderr", file=sys.stderr, flush=True)
        os.write(1,b"fd stdout\\n")
        subprocess.run([sys.executable,"-c","print('child stdout')"],check=True)
        reporter.message("structured reporting only")
        return EvaluationResult(score=1,metrics={"quality":1})
""")
    context = multiprocessing.get_context("spawn")
    worker = context.Process(target=execute, args=(journal.path, f"{adapter}:Evaluator", job))
    worker.start()
    worker.join(20)
    try:
        assert not worker.is_alive() and worker.exitcode == 0
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(5)
    assert journal.get(job["id"])["state"] == "completed"
    out = logs.read(job["id"], job["attempt_id"], "worker.stdout", offset=0)["text"]
    err = logs.read(job["id"], job["attempt_id"], "worker.stderr", offset=0)["text"]
    assert all(text in out for text in ("python stdout", "fd stdout", "child stdout"))
    assert "python stderr" in err
    messages = Reporter(journal, job["id"]).enrich({})["metadata"]["evaluation_messages"]
    assert not any("python stdout" in item["message"] for item in messages)
    assert journal.artifacts(job["id"], job["attempt_id"]) == []
    stages = logs.lifecycle(job["id"], job["attempt_id"])["lifecycle_stages"]
    assert [item["stage"] for item in stages] == ["preparing", "evaluating", "finalizing"]
    assert all(item["status"] == "completed" for item in stages)


@pytest.mark.parametrize("described", [False, True])
def test_external_logs_are_archived_before_worktree_evidence_cleanup(local, described):
    """The normal acknowledgement-fenced collector preserves local diagnostics first."""
    journal, job, logs = local
    source = Path(job["project_root"]) / ".evaluation-artifacts" / "build.log"
    source.parent.mkdir()
    source.write_text("compiler diagnostic\n")
    journal.update(job["id"], "completed", {"success": True, "score": 1})
    journal.acknowledge(job["id"], journal.get(job["id"])["seq"])
    declared = {"path": source, "description": "Compiler output"} if described else source
    gateway = SimpleNamespace(
        root=Path(journal.path).parent,
        journal=journal,
        processes={},
        adapter=object(),
        local_log_sources=lambda payload, saved: {"backend/compiler.log": declared},
    )
    result = collect_finished(gateway)
    assert result["cleaned"] == 1 and not source.exists()
    assert (
        logs.read(job["id"], job["attempt_id"], "backend/compiler.log")["text"]
        == "compiler diagnostic\n"
    )
    finished = journal.get(job["id"])["finished_at"]
    assert logs.collect(now=finished + 3600)["cleaned"] == 0
    assert logs.collect(now=finished + 604801)["cleaned"] == 1
    assert not logs.read(job["id"], job["attempt_id"], "backend/compiler.log")["available"]


def test_unresolved_local_work_is_not_expired(local):
    """Suspended work keeps its logs regardless of time spent awaiting recovery."""
    journal, job, logs = local
    path = logs.create(job["id"], job["attempt_id"], "worker.stdout")
    path.write_text("before interruption")
    journal.update(job["id"], "recovery_required", {"message": "Awaiting confirmation"})
    assert logs.collect(keep_seconds=0, now=time.time() + 10000000)["cleaned"] == 0
    assert path.is_file()


def test_unconfirmed_backend_restoration_prevents_local_log_deletion(local):
    """Terminal transport state alone cannot delete diagnostics for an unrestored device."""
    journal, job, logs = local
    path = logs.create(job["id"], job["attempt_id"], "worker.stdout")
    path.write_text("device restoration pending")
    journal.update(job["id"], "completed", {"success": False})

    class DevicePolicy(EvidencePolicy):
        """Model an external device whose restoration is not yet confirmed."""

        def plan(self, gateway, job, saved, metadata):
            """Refuse cleanup while the resource remains uncertain."""
            raise ValueError("Restoration is unconfirmed")

    gateway = SimpleNamespace(adapter=object())
    result = collect_local_logs(
        gateway, journal, policy=DevicePolicy(), keep_seconds=0, now=time.time() + 10000000
    )
    assert result["cleaned"] == 0 and result["errors"] == 1
    assert path.is_file()


def test_corrupt_managed_path_cannot_delete_unrelated_files(local, tmp_path):
    """A corrupted catalog cannot redirect managed retention outside its attempt."""
    journal, job, logs = local
    logs.create(job["id"], job["attempt_id"], "worker.stdout")
    unrelated = tmp_path / "credentials.txt"
    unrelated.write_text("must remain")
    manifest = logs.directory(job["id"], job["attempt_id"]) / "manifest.json"
    data = json.loads(manifest.read_text())
    data["sources"]["worker.stdout"]["path"] = str(unrelated)
    manifest.write_text(json.dumps(data))
    journal.update(job["id"], "completed", {"success": True})
    assert not logs.list(job["id"], job["attempt_id"])[0]["available"]
    assert logs.collect(keep_seconds=0, now=time.time() + 10000000)["cleaned"] == 0
    assert unrelated.read_text() == "must remain"


def test_superseded_attempt_logs_expire_without_exposing_them_to_the_browser(local):
    """Replacement keeps cleanup context rather than orphaning old log files forever."""
    journal, job, logs = local
    path = logs.create(job["id"], job["attempt_id"], "worker.stdout")
    path.write_text("old attempt output")
    journal.update(job["id"], "completed", {"success": True})
    replacement = {**job, "attempt_id": str(uuid.uuid4())}
    journal.accept(replacement)
    replacement_path = logs.create(job["id"], replacement["attempt_id"], "worker.stdout")
    replacement_path.write_text("new execution is still active")
    with pytest.raises(ValueError, match="no longer current"):
        logs.read(job["id"], job["attempt_id"], "worker.stdout")
    assert logs.collect(now=time.time() + 3600)["cleaned"] == 0
    assert logs.collect(now=time.time() + 604801)["cleaned"] == 1
    assert not path.exists() and replacement_path.is_file()
    with journal.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM local_log_attempts").fetchone()[0] == 0


def test_superseded_unrestored_external_attempt_keeps_its_original_context(local):
    """A new attempt cannot substitute its restoration state for the old external job."""
    journal, job, logs = local
    path = logs.create(job["id"], job["attempt_id"], "worker.stdout")
    path.write_text("external cleanup uncertain")
    Reporter(journal, job["id"]).save_state({"external_id": "old-device-job"})
    journal.update(job["id"], "recovery_required", {"message": "Unconfirmed restoration"})
    journal.accept({**job, "attempt_id": str(uuid.uuid4())})
    seen = []

    class DevicePolicy(EvidencePolicy):
        """Model a failed external restoration check."""

        def plan(self, gateway, payload, saved, metadata):
            """Retain the exact superseded identity and its saved resource handle."""
            seen.append((payload["attempt_id"], saved))
            raise ValueError("Restoration remains unconfirmed")

    result = collect_local_logs(
        SimpleNamespace(adapter=object()), journal, policy=DevicePolicy(), now=time.time() + 604801
    )
    assert result["cleaned"] == 0 and result["errors"] == 1
    assert seen == [(job["attempt_id"], {"external_id": "old-device-job"})]
    assert path.exists()


def test_retired_external_state_is_not_deleted_without_a_restoration_policy(local):
    """Absent optional backend hooks cannot imply that uncertain external work ended."""
    journal, job, logs = local
    path = logs.create(job["id"], job["attempt_id"], "worker.stdout")
    path.write_text("unknown external outcome")
    Reporter(journal, job["id"]).save_state({"external_id": "uncertain"})
    journal.update(job["id"], "recovery_required", {})
    journal.accept({**job, "attempt_id": str(uuid.uuid4())})
    future = time.time() + 604801
    assert logs.collect(now=future)["cleaned"] == 0
    assert (
        collect_local_logs(SimpleNamespace(adapter=object()), journal, now=future)["cleaned"] == 0
    )
    assert path.exists()


def test_log_expiration_can_use_restoration_without_bulk_cleanup_eligibility(local):
    """Private logs can expire while a backend deliberately retains failed experiment evidence."""
    journal, job, logs = local
    path = logs.create(job["id"], job["attempt_id"], "worker.stderr")
    path.write_text("retained independently of raw evidence")
    journal.update(job["id"], "failed", {"success": False})

    class Policy(EvidencePolicy):
        """Keep bulk evidence while independently confirming a restored resource."""

        def plan(self, gateway, payload, saved, metadata):
            """Refuse to collect bulk experiment data."""
            raise AssertionError("Bulk evidence policy must not govern private log lifetime")

        def restoration_plan(self, gateway, payload, saved, metadata):
            """Confirm that resource cleanup has completed."""
            assert payload["attempt_id"] == job["attempt_id"]
            return CleanupPlan()

    result = collect_local_logs(
        SimpleNamespace(adapter=object()), journal, policy=Policy(), now=time.time() + 604801
    )
    assert result["cleaned"] == 1 and not path.exists()
