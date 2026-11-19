"""Retired execution evidence keeps its own acknowledgement and ownership fences."""

import json
import sqlite3
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm4ad.gateway.sdk.journal import Journal, Reporter, gateway_lease
from llm4ad.gateway.sdk.lifecycle import worker_directory
from llm4ad.gateway.sdk.local_logs import LocalLogStore
from llm4ad.gateway.sdk.retention import EvidencePolicy, collect_finished


def retired_case(tmp_path, *, state="completed", acknowledged=True, uploaded=True, logs=False):
    """Replace one attempt with a current execution retaining independent outputs."""
    journal = Journal(tmp_path / "jobs.sqlite3")
    job = {key: str(uuid.uuid4()) for key in ("id", "attempt_id", "task_id")}
    work = tmp_path / "work" / job["id"] / job["attempt_id"]
    output = work / ".evaluation-artifacts" / "result.txt"
    output.parent.mkdir(parents=True)
    output.write_text("old attempt evidence")
    job.update(project_root=str(work), timeout=60)
    journal.accept(job)
    reporter = Reporter(journal, job["id"])
    reporter.save_state({"external_id": "old-external-job"})
    reporter.artifact("result.txt", output)
    if logs:
        reporter.local_log("worker.stdout").write_text("old worker output")
    if uploaded:
        with journal.connect() as db:
            db.execute("UPDATE artifacts SET object_key='uploaded-old-evidence'")
    journal.update(job["id"], state, {"success": True, "metadata": {"original": True}})
    if acknowledged:
        journal.acknowledge(job["id"], journal.get(job["id"])["seq"])
    previous = journal.get(job["id"])
    spool = Path(journal.artifacts(job["id"], job["attempt_id"])[0]["path"])
    replacement = {**job, "attempt_id": str(uuid.uuid4())}
    current_work = tmp_path / "work" / job["id"] / replacement["attempt_id"]
    replacement["project_root"] = str(current_work)
    current_output = current_work / ".evaluation-artifacts" / "result.txt"
    current_output.parent.mkdir(parents=True)
    current_output.write_text("new attempt evidence")
    journal.accept(replacement)
    Reporter(journal, job["id"]).artifact("result.txt", current_output)
    current_spool = Path(journal.artifacts(job["id"], replacement["attempt_id"])[0]["path"])
    gateway = SimpleNamespace(root=tmp_path, journal=journal, processes={}, adapter=object())
    return SimpleNamespace(
        gateway=gateway, journal=journal, job=job, previous=previous, spool=spool,
        output=output, replacement=replacement, current_output=current_output, current_spool=current_spool,
    )


def retired_rows(journal):
    """Inspect cleanup-only contexts without exposing them as current jobs."""
    with journal.connect() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT snapshot FROM retired_attempts")]


@pytest.mark.parametrize("logs", [False, True])
def test_acknowledged_retired_outputs_are_collected_without_touching_current_attempt(tmp_path, logs):
    """Replacing a row cannot orphan safe old outputs or delete the replacement's files."""
    case = retired_case(tmp_path, logs=logs)
    current = case.journal.get(case.job["id"])
    case.gateway.journal = Journal(case.journal.path)
    assert case.gateway.journal.rows() == [current]
    result = collect_finished(case.gateway)
    assert result["cleaned"] == 1 and result["errors"] == 0
    assert not case.spool.exists() and not case.output.exists()
    assert case.current_output.read_text() == "new attempt evidence"
    assert case.current_spool.is_file()
    assert case.journal.get(case.job["id"]) == current
    assert retired_rows(case.journal) == []
    assert collect_finished(case.gateway)["cleaned"] == 0


@pytest.mark.parametrize("changes", [
    {"acknowledged": False}, {"uploaded": False}, {"state": "recovery_required"},
])
def test_retired_cleanup_keeps_original_ack_upload_and_terminal_fences(tmp_path, changes):
    """The current attempt cannot provide missing acknowledgements or completion."""
    case = retired_case(tmp_path, **changes)
    case.journal.update(case.job["id"], "completed", {"success": True})
    case.journal.acknowledge(case.job["id"], case.journal.get(case.job["id"])["seq"])
    with case.journal.connect() as db:
        db.execute("UPDATE artifacts SET object_key='uploaded-current-evidence' WHERE attempt_id=?",
                   (case.replacement["attempt_id"],))
    collect_finished(case.gateway)
    assert case.output.is_file() and case.spool.is_file()
    assert retired_rows(case.journal)[0]["ack"] == case.previous["ack"]


def test_retired_cleanup_waits_for_old_worker_and_original_restoration_policy(tmp_path):
    """Policy checks use the old handle under the resource lock after workers stop."""
    case = retired_case(tmp_path)
    seen, locked = [], []

    class Policy(EvidencePolicy):
        """Refuse cleanup while the original backend resource remains unresolved."""

        def lock(self):
            """Use the existing resource lease as the guard."""
            return gateway_lease(tmp_path / "policy")

        def plan(self, gateway, job, saved, metadata):
            """Validate the immutable original context before allowing any deletion."""
            with pytest.raises(sqlite3.OperationalError), gateway_lease(tmp_path / "policy"):
                pass
            locked.append(True)
            seen.append((job, saved, metadata))
            raise ValueError("Original resource restoration is unconfirmed")

    policy = Policy()
    with gateway_lease(worker_directory(case.journal, case.job["id"], case.job["attempt_id"])):
        assert collect_finished(case.gateway, policy=policy)["cleaned"] == 0
        assert seen == []
    result = collect_finished(case.gateway, policy=policy)
    assert result["errors"] == 1 and locked == [True]
    assert seen == [(case.job, {"external_id": "old-external-job"}, {"original": True})]
    assert case.spool.is_file() and case.output.is_file()
    assert len(retired_rows(case.journal)) == 1


def test_retired_logs_are_archived_before_output_cleanup_and_expire_independently(tmp_path):
    """Retired backend logs remain private and readable by retention after bulk cleanup."""
    case = retired_case(tmp_path)
    case.gateway.local_log_sources = lambda job, saved: {"compiler.stdout": case.output}
    assert collect_finished(case.gateway)["cleaned"] == 1
    assert not case.output.exists()
    logs = LocalLogStore(case.journal)
    with pytest.raises(ValueError, match="no longer current"):
        logs.read(case.job["id"], case.job["attempt_id"], "compiler.stdout")
    manifest = json.loads((logs.directory(case.job["id"], case.job["attempt_id"]) / "manifest.json").read_text())
    archived = Path(manifest["sources"]["compiler.stdout"]["path"])
    assert archived.read_text() == "old attempt evidence"
    assert logs.collect(now=time.time() + 604801)["cleaned"] == 1
    assert not archived.exists()


def test_retired_cleanup_preserves_retention_delay(tmp_path):
    """An attempt replacement cannot reset or bypass an existing cleanup delay."""
    case = retired_case(tmp_path)
    now = time.time()
    assert collect_finished(case.gateway, keep_seconds=60, now=now)["cleaned"] == 0
    assert case.spool.is_file()
    assert collect_finished(case.gateway, keep_seconds=60, now=now + 61)["cleaned"] == 1
    assert not case.spool.exists()


def test_existing_private_snapshots_migrate_without_changing_current_state(tmp_path):
    """An older journal's retained contexts become eligible for guarded bulk cleanup."""
    case = retired_case(tmp_path, logs=True)
    current = case.journal.get(case.job["id"])
    with case.journal.connect() as db:
        db.execute("DROP TABLE retired_attempts")
    migrated = Journal(case.journal.path)
    assert migrated.get(case.job["id"]) == current
    assert retired_rows(migrated)[0]["saved"] == case.previous["saved"]
    assert collect_finished(case.gateway)["cleaned"] == 1
    assert retired_rows(Journal(case.journal.path)) == []


def test_cleaned_receipt_recovers_context_transfer_after_database_rollback(tmp_path):
    """A durable cleanup receipt can finish retiring context without orphaning logs."""
    case = retired_case(tmp_path)
    snapshot = retired_rows(case.journal)[0]
    case.gateway.local_log_sources = lambda job, saved: {"compiler.stdout": case.output}
    assert collect_finished(case.gateway)["cleaned"] == 1
    with case.journal.connect() as db:
        db.execute("DELETE FROM local_log_attempts")
        db.execute(
            "INSERT INTO retired_attempts(job_id,attempt_id,snapshot) VALUES(?,?,?)",
            (case.job["id"], case.job["attempt_id"], json.dumps(snapshot)),
        )
    assert collect_finished(case.gateway)["cleaned"] == 0
    assert retired_rows(case.journal) == []
    assert LocalLogStore(case.journal).collect(now=time.time() + 604801)["cleaned"] == 1


@pytest.mark.parametrize("unsafe", ["worktree", "spool_symlink"])
def test_retired_cleanup_cannot_follow_replacement_paths(tmp_path, unsafe):
    """Old cleanup context cannot redirect deletion into a current attempt's files."""
    case = retired_case(tmp_path)
    if unsafe == "worktree":
        snapshot = retired_rows(case.journal)[0]
        job = {**case.job, "project_root": case.replacement["project_root"]}
        snapshot["job"] = json.dumps(job)
        with case.journal.connect() as db:
            db.execute("UPDATE retired_attempts SET snapshot=?", (json.dumps(snapshot),))
    else:
        case.spool.unlink()
        case.spool.symlink_to(case.current_spool)
    result = collect_finished(case.gateway)
    assert result["errors"] == 1 and result["cleaned"] == 0
    assert case.output.is_file() and case.current_output.is_file()
    assert case.current_spool.read_text() == "new attempt evidence"
    assert len(retired_rows(case.journal)) == 1


def test_retired_snapshot_cannot_clean_a_current_again_attempt(tmp_path):
    """A retained terminal snapshot cannot substitute for a current execution fence."""
    case = retired_case(tmp_path)
    case.journal.accept(case.job)
    result = collect_finished(case.gateway)
    assert result["cleaned"] == 0
    assert case.spool.is_file() and case.output.is_file()
    assert case.journal.get(case.job["id"])["state"] == "accepted"
