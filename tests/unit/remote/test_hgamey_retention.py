"""Local evidence retention fixtures; no phone, network or real trace data."""

import json
import os
import shutil
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from llm4ad.gateway.examples.hgamey.evidence import HGameyEvidencePolicy
from llm4ad.remote.gateway import Gateway
from llm4ad.remote.journal import Journal, Reporter


@pytest.fixture
def retention(monkeypatch):
    """Load the container janitor without starting the service or executor."""
    from llm4ad.gateway.sdk import retention as module
    return module


@pytest.fixture
def make_attempt(tmp_path):
    """Build actual journal/outbox files around explicitly synthetic evidence."""
    def build(*, startup=False):
        root = tmp_path / uuid.uuid4().hex
        sdk, inputs, jobs = root / "gateway", root / "inputs", root / "executor/jobs"
        sdk.mkdir(parents=True)
        jobs.mkdir(parents=True)
        journal = Journal(sdk / ("startup.sqlite3" if startup else "jobs.sqlite3"))
        job = {key: str(uuid.uuid4()) for key in ("id", "attempt_id", "task_id")}
        work = sdk / "work" / job['id'] / job['attempt_id']
        work.mkdir(parents=True)
        (work / "shader.comp").write_text("// Synthetic candidate worktree")
        job.update(project_root=str(work), timeout=120)
        journal.accept(job)
        key = "a" * 64
        stage = inputs / "llm4ad-hgamey" / ("b" * 64) / "jobs" / key
        stage.mkdir(parents=True)
        owner = {"job_id": job['id'], "attempt_id": job['attempt_id'], "task_id": job['task_id']}
        (stage / "owner.json").write_text(json.dumps(owner))
        (stage / "candidate.comp").write_text("// Synthetic source retained for diagnosis")
        (stage / "candidate.spv").write_bytes(b"synthetic compiled output")
        reference = inputs / "llm4ad-hgamey" / ("b" * 64) / "reference/frame.png"
        reference.parent.mkdir()
        reference.write_bytes(b"synthetic shared reference; never remove")
        ids = [uuid.uuid4().hex, uuid.uuid4().hex]
        tokens = [key, "c" * 64]
        for native_id, token in zip(ids, tokens, strict=True):
            folder = jobs / native_id
            (folder / "evidence/traces").mkdir(parents=True)
            (folder / "inputs").mkdir()
            (folder / "inputs/reference.rgba").write_bytes(b"synthetic private frame")
            (folder / "evidence/traces/A1_pan.mp4").write_bytes(b"synthetic recording")
            (folder / "evidence/traces/trace.pftrace").write_bytes(b"synthetic trace")
            (folder / "result.json").write_text(json.dumps({"status": "PASS", "conditions_observed": {"restored": True}}))
            (folder / "input.json").write_text(json.dumps({"idempotency_key": token}))
            (folder / "job.json").write_text(json.dumps({
                "id": native_id, "state": "completed", "cleanup_status": "completed",
                "idempotency_key": token, "device_id": "fixture-phone", "finished_at": 900,
            }))
        source = jobs / ids[1] / "evidence/traces/trace.pftrace"
        Reporter(journal, job['id']).artifact("game/A1/trace.pftrace", source)
        saved = {
            "idempotency_key": key, "evidence_directory": str(stage),
            "remote_job_id": job['id'], "remote_attempt_id": job['attempt_id'],
            "external_job_id": ids[0], "request": {"device_id": "fixture-phone"},
            "game": {"external_job_id": ids[1], "idempotency_key": tokens[1], "submission_started": True,
                     "request": {"device_id": "fixture-phone"}},
        }
        metadata = {"evidence_directory": str(stage)}
        if startup:
            saved['purpose'] = metadata['purpose'] = 'startup_readiness_only'
        Reporter(journal, job['id']).save_state(saved)
        journal.update(job['id'], 'completed', {'success': True, 'metrics': {}, 'metadata': metadata})
        def get(native_id):
            return json.loads((jobs / native_id / 'job.json').read_text())
        engine = SimpleNamespace(jobs=jobs, mutex=threading.RLock(), active=None, blocked_jobs=set(), get=get)
        benchmark = SimpleNamespace(inputs=inputs, profile=stage.parent.parent, attempt_key=lambda _: key)
        gateway = SimpleNamespace(root=sdk, adapter=SimpleNamespace(benchmark=benchmark), processes={})
        return SimpleNamespace(root=root, journal=journal, job=job, sdk=sdk, jobs=jobs, ids=ids,
                               stage=stage, work=work, reference=reference, engine=engine, gateway=gateway)
    return build


def uploaded(attempt, *, acknowledge=True):
    """Model the durable upload receipt and optional platform final report ack."""
    with attempt.journal.connect() as db:
        db.execute("UPDATE artifacts SET object_key='owned-platform-object'")
    if acknowledge:
        row = attempt.journal.get(attempt.job['id'])
        attempt.journal.acknowledge(attempt.job['id'], row['seq'])


def test_unacknowledged_final_result_keeps_all_evidence(retention, make_attempt):
    """Retain all evidence until the final platform report is acknowledged."""
    attempt = make_attempt()
    uploaded(attempt, acknowledge=False)
    result = retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)
    assert result['cleaned'] == 0
    assert (attempt.stage / 'candidate.spv').exists() and attempt.work.exists()
    assert all((attempt.jobs / key / 'evidence/traces/A1_pan.mp4').exists() for key in attempt.ids)


def test_missing_artifact_upload_receipt_blocks_cleanup(retention, make_attempt):
    """Never clean a still-retryable artifact outbox."""
    attempt = make_attempt()
    attempt.journal.acknowledge(attempt.job['id'], attempt.journal.get(attempt.job['id'])['seq'])
    result = retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)
    assert result['cleaned'] == 0 and attempt.work.exists()
    assert (attempt.jobs / attempt.ids[0] / 'inputs/reference.rgba').exists()


@pytest.mark.parametrize('condition', ['active', 'uncertain', 'wrong_key', 'wrong_device'])
def test_every_native_job_must_be_inactive_restored_and_owned(retention, make_attempt, condition):
    """Check every external job before deleting evidence from any job."""
    attempt = make_attempt()
    uploaded(attempt)
    if condition == 'active':
        attempt.engine.active = attempt.ids[1]
    else:
        p = attempt.jobs / attempt.ids[1] / 'job.json'
        job = json.loads(p.read_text())
        job.update({'cleanup_status': 'uncertain'} if condition == 'uncertain' else
                   {'idempotency_key': 'd' * 64} if condition == 'wrong_key' else {'device_id': 'other-phone'})
        p.write_text(json.dumps(job))
    result = retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)
    assert result['cleaned'] == 0
    assert all((attempt.jobs / key / 'evidence').exists() for key in attempt.ids)
    assert attempt.work.exists()


def test_acknowledged_cleanup_preserves_remote_results_and_idempotency(retention, make_attempt):
    """Remove bulk copies without changing durable reports or upload receipts."""
    attempt = make_attempt()
    uploaded(attempt)
    before = attempt.journal.get(attempt.job['id'])
    outbox = Path(attempt.journal.artifacts(attempt.job['id'], attempt.job['attempt_id'])[0]['path'])
    result = retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)
    assert result['cleaned'] == 1 and result['removed_bytes'] > 0
    assert not attempt.work.exists() and not outbox.exists()
    assert not (attempt.stage / 'candidate.spv').exists()
    assert (attempt.stage / 'candidate.comp').exists() and attempt.reference.exists()
    for key in attempt.ids:
        assert not (attempt.jobs / key / 'evidence').exists()
        assert not (attempt.jobs / key / 'inputs').exists()
        assert attempt.engine.get(key)['state'] == 'completed'
        assert json.loads((attempt.jobs / key / 'result.json').read_text())['status'] == 'PASS'
    assert attempt.journal.get(attempt.job['id']) == before
    assert attempt.journal.artifacts(attempt.job['id'], attempt.job['attempt_id'])[0]['object_key'] == 'owned-platform-object'
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1100)['cleaned'] == 0


async def test_uploaded_manifest_survives_deletion_of_local_outbox(retention, make_attempt):
    """Redelivery reuses upload receipts after the acknowledged files are collected."""
    attempt = make_attempt()
    uploaded(attempt)
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)['cleaned'] == 1
    gateway = Gateway.__new__(Gateway)
    gateway.journal, gateway.identity = attempt.journal, {'id': str(uuid.uuid4())}
    gateway.request = AsyncMock(side_effect=AssertionError('An uploaded artifact must not be uploaded again'))
    manifest = await gateway.upload_artifacts(None, attempt.job)
    assert manifest[0]['object_key'] == 'owned-platform-object'
    gateway.request.assert_not_called()


def test_debug_delay_survives_collector_restart(retention, make_attempt):
    """Persist the optional delay instead of restarting its clock on each pass."""
    attempt = make_attempt()
    uploaded(attempt)
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000, keep_seconds=60)['cleaned'] == 0
    assert attempt.work.exists()
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1059, keep_seconds=60)['cleaned'] == 0
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1060, keep_seconds=60)['cleaned'] == 1


def test_unknown_game_submission_keeps_both_native_jobs(retention, make_attempt):
    """Keep all evidence when a game dispatch might still be unresolved."""
    attempt = make_attempt()
    uploaded(attempt)
    saved = json.loads(attempt.journal.get(attempt.job['id'])['saved'])
    saved['game'].pop('external_job_id')
    saved['game'].pop('submission_started')
    Reporter(attempt.journal, attempt.job['id']).save_state(saved)
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)['cleaned'] == 0
    assert all((attempt.jobs / key / 'evidence').exists() for key in attempt.ids)


def test_live_sdk_process_defers_cleanup(retention, make_attempt):
    """Do not remove a workspace while its SDK process can still use it."""
    attempt = make_attempt()
    uploaded(attempt)
    attempt.gateway.processes[attempt.job['id']] = SimpleNamespace(is_alive=lambda: True)
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)['cleaned'] == 0
    assert attempt.work.exists()


@pytest.mark.parametrize('state', ['running', 'recovery_required', 'failed', 'cancelled'])
def test_noncompleted_attempts_are_never_automatically_pruned(retention, make_attempt, state):
    """Preserve failed, cancelled and uncertain attempts for diagnosis."""
    attempt = make_attempt()
    uploaded(attempt)
    attempt.journal.update(attempt.job['id'], state)
    attempt.journal.acknowledge(attempt.job['id'], attempt.journal.get(attempt.job['id'])['seq'])
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)['cleaned'] == 0
    assert attempt.work.exists()


def test_unowned_stage_path_blocks_all_deletion(retention, make_attempt):
    """Reject reference-directory paths before removing any attempt data."""
    attempt = make_attempt()
    uploaded(attempt)
    saved = json.loads(attempt.journal.get(attempt.job['id'])['saved'])
    saved['evidence_directory'] = str(attempt.reference.parent)
    Reporter(attempt.journal, attempt.job['id']).save_state(saved)
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)['cleaned'] == 0
    assert all((attempt.jobs / key / 'evidence').exists() for key in attempt.ids)
    assert attempt.reference.exists()


def test_retry_after_partial_cleanup_never_reruns_measurement(retention, make_attempt, monkeypatch):
    """Finish a partial filesystem cleanup using the same terminal job records."""
    attempt = make_attempt()
    uploaded(attempt)
    prune = retention.prune_directory
    def interrupted(path, keep):
        if path == attempt.stage:
            raise OSError('synthetic storage interruption')
        return prune(path, keep)
    monkeypatch.setattr(retention, 'prune_directory', interrupted)
    first = retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)
    assert first['cleaned'] == 0 and attempt.work.exists()
    monkeypatch.setattr(retention, 'prune_directory', prune)
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1100)['cleaned'] == 1
    assert all(attempt.engine.get(key)['state'] == 'completed' for key in attempt.ids)


def test_successful_readiness_needs_local_completion_not_platform_upload(retention, make_attempt):
    """Collect successful readiness evidence using its durable local receipt."""
    attempt = make_attempt(startup=True)
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)['cleaned'] == 1
    assert all(not (attempt.jobs / key / 'evidence').exists() for key in attempt.ids)
    assert attempt.journal.get(attempt.job['id'])['ack'] == -1


def test_failed_readiness_keeps_diagnostics(retention, make_attempt):
    """Preserve a rejected readiness experiment and its raw evidence."""
    attempt = make_attempt(startup=True)
    attempt.journal.update(attempt.job['id'], 'failed', {'success': False})
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)['cleaned'] == 0
    assert all((attempt.jobs / key / 'evidence').exists() for key in attempt.ids)


@pytest.mark.parametrize('state', ['completed', 'failed'])
def test_compilation_failure_cleans_only_owned_local_build_files(retention, make_attempt, state):
    """Clean acknowledged build failures without touching unsubmitted phone jobs."""
    attempt = make_attempt()
    uploaded(attempt)
    Reporter(attempt.journal, attempt.job['id']).save_state({})
    attempt.journal.update(attempt.job['id'], state, {
        'success': False, 'metrics': {}, 'metadata': {
            'candidate_failure': True, 'error_type': 'CandidateCompilationError', 'evidence_directory': str(attempt.stage),
        },
    })
    attempt.journal.acknowledge(attempt.job['id'], attempt.journal.get(attempt.job['id'])['seq'])
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)['cleaned'] == 1
    assert not attempt.work.exists() and not (attempt.stage / 'candidate.spv').exists()
    assert all((attempt.jobs / key / 'evidence').exists() for key in attempt.ids)


@pytest.mark.parametrize('report', [
    {'success': False, 'metadata': {'failure_type': 'RuntimeError'}},
    {'success': False, 'metadata': {'candidate_failure': True, 'error_type': 'RuntimeError'}},
    {'success': False, 'metadata': {'candidate_failure': False, 'error_type': 'CandidateCompilationError'}},
    {'success': True, 'metadata': {'candidate_failure': True, 'error_type': 'CandidateCompilationError'}},
    {'metadata': {'candidate_failure': True, 'error_type': 'CandidateCompilationError'}},
])
def test_failed_noncompilation_results_keep_all_evidence(retention, make_attempt, report):
    """Failed-state cleanup requires a real negative result and exact candidate compilation markers."""
    attempt = make_attempt()
    Reporter(attempt.journal, attempt.job['id']).save_state({})
    attempt.journal.update(attempt.job['id'], 'failed', report)
    uploaded(attempt)
    result = retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)
    assert result['cleaned'] == 0
    assert attempt.work.exists() and (attempt.stage / 'candidate.spv').exists()
    assert all((attempt.jobs / key / 'evidence').exists() for key in attempt.ids)


def test_private_log_restoration_guard_is_separate_from_failed_bulk_cleanup(make_attempt):
    """Known-restored failures may expire private logs while their bulk evidence stays retained."""
    attempt = make_attempt()
    attempt.journal.update(attempt.job['id'], 'failed', {
        'success': False, 'metrics': {}, 'metadata': {'failure_type': 'RuntimeError'},
    })
    saved = json.loads(attempt.journal.get(attempt.job['id'])['saved'])
    policy = HGameyEvidencePolicy(attempt.engine, attempt.gateway)
    with pytest.raises(ValueError, match='known candidate compilation'):
        policy.plan(attempt.gateway, attempt.job, saved, {})
    assert len(policy.restoration_plan(attempt.gateway, attempt.job, saved, {}).external_jobs) == 2
    attempt.engine.active = attempt.ids[0]
    with pytest.raises(ValueError, match='restoration is unconfirmed'):
        policy.restoration_plan(attempt.gateway, attempt.job, saved, {})
    assert attempt.work.exists()


@pytest.mark.parametrize('condition', ['unacknowledged', 'missing_upload', 'unrestored', 'unowned'])
def test_failed_compilation_cleanup_preserves_delivery_and_ownership_guards(retention, make_attempt, condition):
    """Compilation errors do not bypass upload acknowledgements or external restoration and ownership."""
    attempt = make_attempt()
    if condition != 'unrestored':
        Reporter(attempt.journal, attempt.job['id']).save_state({})
    attempt.journal.update(attempt.job['id'], 'failed', {
        'success': False, 'metrics': {}, 'metadata': {
            'candidate_failure': True, 'error_type': 'CandidateCompilationError', 'evidence_directory': str(attempt.stage),
        },
    })
    if condition == 'missing_upload':
        attempt.journal.acknowledge(attempt.job['id'], attempt.journal.get(attempt.job['id'])['seq'])
    else:
        uploaded(attempt, acknowledge=condition != 'unacknowledged')
    if condition == 'unrestored':
        path = attempt.jobs / attempt.ids[0] / 'job.json'
        record = json.loads(path.read_text())
        record['cleanup_status'] = 'uncertain'
        path.write_text(json.dumps(record))
    elif condition == 'unowned':
        path = attempt.stage / 'owner.json'
        record = json.loads(path.read_text())
        record['attempt_id'] = str(uuid.uuid4())
        path.write_text(json.dumps(record))
    result = retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)
    assert result['cleaned'] == 0
    assert attempt.work.exists() and (attempt.stage / 'candidate.spv').exists()
    assert all((attempt.jobs / key / 'evidence').exists() for key in attempt.ids)


@pytest.mark.skipif(os.name == 'nt', reason='The Linux container owns these symlink paths')
def test_worktree_symlink_cannot_redirect_cleanup(retention, make_attempt):
    """Refuse symlink workspaces before deleting any other attempt evidence."""
    attempt = make_attempt()
    uploaded(attempt)
    outside = attempt.root / 'unrelated'
    outside.mkdir()
    marker = outside / 'keep.txt'
    marker.write_text('unrelated data')
    shutil.rmtree(attempt.work)
    attempt.work.symlink_to(outside, target_is_directory=True)
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)['cleaned'] == 0
    assert marker.read_text() == 'unrelated data'
    assert all((attempt.jobs / key / 'evidence').exists() for key in attempt.ids)


@pytest.mark.skipif(os.name == 'nt', reason='The Linux container owns these symlink paths')
def test_child_symlink_is_removed_without_following_target(retention, make_attempt):
    """Unlink attempt-owned links while preserving unrelated target data."""
    attempt = make_attempt()
    uploaded(attempt)
    marker = attempt.root / 'keep.txt'
    marker.write_text('unrelated data')
    (attempt.stage / 'linked-output').symlink_to(marker)
    assert retention.collect_finished(attempt.gateway, policy=HGameyEvidencePolicy(attempt.engine, attempt.gateway), now=1000)['cleaned'] == 1
    assert marker.read_text() == 'unrelated data'
