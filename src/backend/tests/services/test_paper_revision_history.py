"""Complete revision history regressions with disposable SQLite and object storage."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from app import models
from app.api import deps
from app.api.llm4ad import paper_history as routes
from app.schemas.paper_revision_history import PaperHistoryRestoreRequest
from app.services import paper_revision_history as history
from app.services import paper_service


class MemoryStorage:
    """Implement only the existing fake store's upload, download, and delete API."""

    def __init__(self):
        """Keep immutable uploaded bytes and an injectable history failure."""
        self.objects = {}
        self.reads = []
        self.fail_history = False

    def upload(self, key, data, **_kwargs):
        """Record exact bytes without contacting RustFS."""
        if self.fail_history and "/history/revisions/" in key:
            raise OSError("History unavailable")
        self.objects[key] = data

    def download(self, key):
        """Read the original bytes and fail on absent objects."""
        self.reads.append(key)
        return self.objects[key]

    def delete(self, key):
        """Remove an uncommitted object without affecting old references."""
        self.objects.pop(key, None)


@pytest.fixture
def revision_db(tmp_path, monkeypatch):
    """Provide a real transaction and owned manuscript without external services."""
    engine = create_engine(f"sqlite:///{tmp_path / 'revisions.sqlite'}", connect_args={"check_same_thread": False})
    tables = [
        models.PaperWorkspace,
        models.PaperSourceVersion,
        models.PaperReview,
        models.PaperAgentRun,
        models.PaperEvaluationMetric,
        models.PaperBoundarySnapshot,
        models.PaperOptimizationTarget,
        models.PaperRevisionCandidate,
    ]
    models.PaperWorkspace.metadata.create_all(engine, tables=[model.__table__ for model in tables])
    storage = MemoryStorage()
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    user = models.User(email="revision-history@example.com", hashed_password="unused")
    with Session(engine, expire_on_commit=False) as db:
        workspace = models.PaperWorkspace(user_id=user.id, title="History", mode="manuscript")
        db.add(workspace)
        db.flush()
        with history.suppress_capture():
            source = paper_service._create_source_version(
                db,
                workspace,
                [("paper.md", b"# Original\r\nExact author text.\r\n"), ("figure.png", b"\x89PNG\x00old")],
                change_summary="Original source",
                commit=False,
            )
        review = models.PaperReview(
            workspace_id=workspace.id,
            source_version_id=source.id,
            reviewer_label="Reviewer A",
            object_key="original-review.md",
            content_hash="old",
        )
        storage.upload(review.object_key, b"## Original evidence\r\nPlease compare X.\r\n")
        workspace.rebuttal_context = {
            "baseline": {"summary": "Actual baseline"},
            "intake": {"paper_summary": "Author's supplied paper"},
            "analysis": {"summary": "Actual analysis"},
            "rendered": {"text": "Exact response.\r\n", "findings": ["Check the assumption"], "open_placeholders": []},
            "chair_message": "Dear chair, exact wording.\r\n",
        }
        workspace.rebuttal_entries = [
            {"id": "reply-a", "response": "Exact response.\r\n", "reviewer_id": str(review.id)}
        ]
        db.add_all([workspace, review])
        db.commit()
        yield db, user, workspace, source, review, storage
    engine.dispose()


def capture(fixture, **kwargs):
    """Capture and commit a complete test revision."""
    db, _user, workspace, *_rest = fixture
    return history.capture_revision(db, workspace, commit=True, **kwargs)


def request_for(fixture):
    """Observe the complete current state, independently of source identity."""
    db, _user, workspace, *_rest = fixture
    return PaperHistoryRestoreRequest(expected_state_hash=history.current_state_hash(db, workspace))


def test_capture_exact_outputs_and_no_duplicate_visible_mirrors(revision_db):
    """Expose canonical prose once while retaining complete internal restore data."""
    db, user, workspace, source, review, storage = revision_db
    original_objects = dict(storage.objects)
    revision = capture(revision_db, origin="manual", summary="Original complete state")
    result = history.get_history(db, user, workspace.id)
    assert result.current_revision_id == revision.id
    assert result.current_state_hash == revision.state_hash
    comparison = history.compare_revisions(db, user, workspace.id, revision.id, revision.id)
    paths = {item.path for item in comparison.files}
    assert paths == {
        "source/paper.md",
        "source/figure.png",
        f"reviews/{review.id}.md",
        "outputs/baseline.json",
        "outputs/rebuttal.md",
        "outputs/ac-summary.md",
        "outputs/author-actions.json",
    }
    assert history.get_file(db, user, workspace.id, revision.id, "outputs/rebuttal.md").content == "Exact response.\r\n"
    assert history.get_file(db, user, workspace.id, revision.id, "outputs/ac-summary.md").content.startswith(
        "Dear chair"
    )
    assert history.get_file(db, user, workspace.id, revision.id, f"reviews/{review.id}.md").content.endswith("X.\r\n")
    assert history.get_file(db, user, workspace.id, revision.id, "source/figure.png").binary
    assert all(storage.objects[key] == raw for key, raw in original_objects.items())
    assert result.revisions[0].source_version_id == source.id
    assert set(workspace.rebuttal_context["_paper_history"]) == {"object_key", "revision", "state_hash"}


@pytest.mark.parametrize("kind", ["history", "regional", "progress", "stage_activity"])
def test_control_changes_do_not_change_hash_or_proliferate_captures(revision_db, kind):
    """Ignore reads, queues, and activity without dropping actual output metadata."""
    db, user, workspace, *_ = revision_db
    revision = capture(revision_db)
    context = copy.deepcopy(workspace.rebuttal_context)
    if kind == "history":
        context["_paper_history"]["extra_control"] = "ignored"
    elif kind == "regional":
        context["_paper_regions"] = {"object_key": "queue-only.json", "revision": "new-queue"}
    elif kind == "progress":
        context["research_progress"] = {"requests": ["read"], "progress": 98}
    else:
        workspace.proposal_stage_states = {"autorebuttal": {"status": "running", "progress": 98}}
    workspace.rebuttal_context = context
    assert history.current_state_hash(db, workspace) == revision.state_hash
    assert capture(revision_db).id == revision.id
    assert history.get_history(db, user, workspace.id).current_revision_id == revision.id


@pytest.mark.parametrize("field", ["chair", "rendered", "entries", "foundation", "review", "findings"])
def test_actual_author_changes_invalidate_complete_state_token(revision_db, field):
    """Protect text and evidence changes even when the input source is identical."""
    db, _user, workspace, _source, review, storage = revision_db
    revision = capture(revision_db)
    context = copy.deepcopy(workspace.rebuttal_context)
    if field == "chair":
        context["chair_message"] = "Author's corrected chair message"
    elif field == "rendered":
        context["rendered"]["text"] = "Author's complete revision"
    elif field == "entries":
        workspace.rebuttal_entries = [{"id": "reply-a", "response": "Corrected response"}]
    elif field == "foundation":
        workspace.proposal_foundation = {"blocks": [{"key": "goal", "title": "Goal", "content": "Corrected goal"}]}
    elif field == "review":
        storage.upload("edited-review.md", b"Corrected actual reviewer evidence")
        review.object_key = "edited-review.md"
        db.add(review)
    else:
        workspace.proposal_stage_states = {"autorebuttal": {"findings": ["A substantive new finding"]}}
    workspace.rebuttal_context = context
    assert history.current_state_hash(db, workspace) != revision.state_hash


def test_capture_preserves_dirty_text_and_joins_outer_transaction(revision_db):
    """Never refresh pending author content or commit the caller's mutation early."""
    db, _user, workspace, *_ = revision_db
    first = capture(revision_db)
    workspace.rebuttal_context = {**workspace.rebuttal_context, "chair_message": "Unflushed author text"}
    second = history.capture_revision(db, workspace, origin="manual")
    assert workspace.rebuttal_context["chair_message"] == "Unflushed author text"
    assert second.id != first.id
    db.rollback()
    db.refresh(workspace)
    assert workspace.rebuttal_context["_paper_history"]["revision"] == first.id
    assert workspace.rebuttal_context["chair_message"] != "Unflushed author text"


def test_restore_copies_source_and_all_outputs_forward(revision_db):
    """Restore exact content as a new source without mutating historical objects."""
    db, user, workspace, source, review, storage = revision_db
    target = capture(revision_db)
    old_objects = dict(storage.objects)
    workspace.rebuttal_context = {
        **workspace.rebuttal_context,
        "chair_message": "Later chair",
        "rendered": {"text": "Later response"},
        "_paper_regions": {"object_key": "current-queue.json", "revision": "current-queue"},
    }
    workspace.rebuttal_entries = [{"id": "reply-b", "response": "Later entry"}]
    storage.upload("later-review.md", b"Later reviewer edit")
    review.object_key = "later-review.md"
    db.add_all([workspace, review])
    db.commit()
    later = capture(revision_db)
    result = history.restore_revision(db, user, workspace.id, target.id, request_for(revision_db))
    assert result.id not in {target.id, later.id}
    assert result.origin == "restore"
    assert workspace.active_source_version_id != source.id
    restored_source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    assert restored_source.parent_version_id == source.id
    assert restored_source.version == 2
    assert dict(paper_service._source_entries(restored_source))["paper.md"] == b"# Original\r\nExact author text.\r\n"
    assert workspace.rebuttal_context["chair_message"] == "Dear chair, exact wording.\r\n"
    assert workspace.rebuttal_context["rendered"]["text"] == "Exact response.\r\n"
    assert workspace.rebuttal_context["_paper_regions"]["revision"] == "current-queue"
    assert workspace.rebuttal_entries[0]["id"] == "reply-a"
    assert review.object_key == "original-review.md"
    assert all(storage.objects[key] == raw for key, raw in old_objects.items())


@pytest.mark.parametrize("change", ["output", "review", "source", "revision"])
def test_restore_rejects_stale_content_or_revision_without_uploads(revision_db, change):
    """An unchanged source ID alone cannot authorize overwriting later author edits."""
    db, user, workspace, source, review, storage = revision_db
    target = capture(revision_db)
    request = request_for(revision_db)
    if change == "output":
        workspace.rebuttal_context = {**workspace.rebuttal_context, "chair_message": "New author text"}
    elif change == "review":
        storage.upload("new-review.md", b"New evidence")
        review.object_key = "new-review.md"
        db.add(review)
    elif change == "source":
        with history.suppress_capture():
            paper_service._create_source_version(
                db, workspace, [("paper.md", b"New source")], parent=source, change_summary="Author edit", commit=False
            )
    else:
        request.expected_revision_id = "a-different-observed-revision"
    db.add(workspace)
    db.commit()
    before = dict(storage.objects)
    with pytest.raises(HTTPException) as failure:
        history.restore_revision(db, user, workspace.id, target.id, request)
    assert failure.value.status_code == 409
    assert storage.objects == before


def test_rollback_cleans_new_manifests_but_preserves_committed_history(revision_db, monkeypatch):
    """Remove captures when an outer publisher fails after capture returned."""
    db, _user, workspace, *_rest, storage = revision_db
    original = capture(revision_db)
    before = dict(storage.objects)
    workspace.rebuttal_context = {**workspace.rebuttal_context, "chair_message": "Will roll back"}
    history.capture_revision(db, workspace)
    assert set(storage.objects) - set(before)
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("Injected outer commit failure")))
    with pytest.raises(RuntimeError, match="outer commit"):
        db.commit()
    db.rollback()
    assert storage.objects == before
    db.refresh(workspace)
    assert workspace.rebuttal_context["_paper_history"]["revision"] == original.id


def test_outer_source_publication_commit_failure_cleans_history_and_source(revision_db, monkeypatch):
    """Cover the production source hook's complete rollback cleanup boundary."""
    db, _user, workspace, source, _review, storage = revision_db
    capture(revision_db)
    before = dict(storage.objects)
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("Source commit failed")))
    with pytest.raises(RuntimeError, match="Source commit"):
        paper_service._create_source_version(
            db, workspace, [("paper.md", b"Uncommitted source")], parent=source, change_summary="Fail atomically"
        )
    assert storage.objects == before
    db.refresh(workspace)
    assert workspace.active_source_version_id == source.id


def test_restore_storage_failure_rolls_back_all_canonical_content(revision_db):
    """Keep current source and outputs if publishing the restore manifest fails."""
    db, user, workspace, source, _review, storage = revision_db
    target = capture(revision_db)
    workspace.rebuttal_context = {**workspace.rebuttal_context, "chair_message": "Current author text"}
    db.add(workspace)
    db.commit()
    capture(revision_db)
    before = dict(storage.objects)
    storage.fail_history = True
    with pytest.raises(OSError, match="History unavailable"):
        history.restore_revision(db, user, workspace.id, target.id, request_for(revision_db))
    storage.fail_history = False
    db.refresh(workspace)
    assert workspace.active_source_version_id == source.id
    assert workspace.rebuttal_context["chair_message"] == "Current author text"
    assert storage.objects == before


def test_restore_suppresses_parent_hooks_without_suppressing_later_captures(revision_db, monkeypatch):
    """Restore cannot recursively create intermediary source revisions."""
    db, user, workspace, *_ = revision_db
    target = capture(revision_db)
    workspace.rebuttal_context = {**workspace.rebuttal_context, "chair_message": "New current output"}
    db.add(workspace)
    db.commit()
    later = capture(revision_db)
    original_create = paper_service._create_source_version
    observed = []

    def create_with_hook(*args, **kwargs):
        observed.append(history.capture_revision(db, workspace))
        result = original_create(*args, **kwargs)
        observed.append(history.capture_revision(db, workspace))
        return result

    monkeypatch.setattr(paper_service, "_create_source_version", create_with_hook)
    restored = history.restore_revision(db, user, workspace.id, target.id, request_for(revision_db))
    assert observed == [None, None]
    assert restored.id != later.id
    assert capture(revision_db).id == restored.id


def test_restoring_current_state_still_creates_new_revision(revision_db):
    """An explicit restore always copies forward, even for identical content."""
    db, user, workspace, source, *_ = revision_db
    current = capture(revision_db)
    restored = history.restore_revision(db, user, workspace.id, current.id, request_for(revision_db))
    assert restored.id != current.id
    assert restored.origin == "restore"
    assert workspace.active_source_version_id != source.id


def test_nested_rollback_deletes_only_its_new_manifests(revision_db):
    """A savepoint rollback must retain manifests owned by the outer transaction."""
    db, _user, workspace, *_rest, storage = revision_db
    capture(revision_db)
    workspace.rebuttal_context = {**workspace.rebuttal_context, "chair_message": "Outer transaction text"}
    outer = history.capture_revision(db, workspace)
    outer_objects = dict(storage.objects)
    nested = db.begin_nested()
    workspace.rebuttal_context = {**workspace.rebuttal_context, "chair_message": "Nested transaction text"}
    history.capture_revision(db, workspace)
    assert set(storage.objects) - set(outer_objects)
    nested.rollback()
    assert storage.objects == outer_objects
    db.commit()
    assert workspace.rebuttal_context["_paper_history"]["revision"] == outer.id


def test_session_close_cleans_abandoned_manifests(revision_db):
    """Closing an uncommitted publisher cannot leave its history uploads behind."""
    db, _user, workspace, *_rest, storage = revision_db
    capture(revision_db)
    before = dict(storage.objects)
    workspace.rebuttal_context = {**workspace.rebuttal_context, "chair_message": "Abandoned draft"}
    history.capture_revision(db, workspace)
    assert set(storage.objects) - set(before)
    db.close()
    assert storage.objects == before


def test_explicitly_empty_canonical_text_does_not_resurrect_legacy_mirror(revision_db):
    """Preserve an author's deletion even when a historical compliance mirror exists."""
    db, user, workspace, *_ = revision_db
    workspace.rebuttal_context = {
        **workspace.rebuttal_context,
        "rendered": {"text": ""},
        "compliance": {"rendered_text": "Obsolete mirrored prose"},
    }
    revision = capture(revision_db)
    assert history.get_file(db, user, workspace.id, revision.id, "outputs/rebuttal.md").content == ""


def test_lazy_legacy_sources_runs_and_regional_chain_keep_original_ids(revision_db):
    """Recover stored legacy outputs without copying today's prose into old records."""
    db, user, workspace, source, _review, storage = revision_db
    artifact = {"summary": "Original AC result", "message": "Historical chair wording"}
    storage.upload("old-ac.json", json.dumps(artifact).encode())
    run = models.PaperAgentRun(
        workspace_id=workspace.id,
        source_version_id=source.id,
        run_kind="ac_summary",
        status="ready",
        artifact_object_key="old-ac.json",
    )
    region = {
        "regions": {
            "chair:message": {
                "id": "chair:message",
                "content": "Regional author wording",
                "target": ["context", "chair_message"],
            }
        },
        "review_versions": {},
    }
    storage.upload("old-region.json", json.dumps(region).encode())
    workspace.rebuttal_context = {**workspace.rebuttal_context, "_paper_regions": {"object_key": "old-region.json"}}
    db.add_all([run, workspace])
    db.commit()
    originals = dict(storage.objects)
    result = history.get_history(db, user, workspace.id)
    ids = {item.id for item in result.revisions}
    assert {f"source:{source.id}", f"run:{run.id}", "region:old-region"} <= ids
    assert history.get_file(db, user, workspace.id, f"source:{source.id}", "outputs/ac-summary.md").exists is False
    assert (
        history.get_file(db, user, workspace.id, f"run:{run.id}", "outputs/ac-summary.md").content
        == artifact["message"]
    )
    assert all(storage.objects[key] == raw for key, raw in originals.items())
    assert history.get_history(db, user, workspace.id).current_revision_id == result.current_revision_id


def test_listing_legacy_history_never_reads_source_or_artifact_bodies(revision_db):
    """History listing must stay cheap regardless of source size or version count."""
    db, user, workspace, source, _review, storage = revision_db
    source_ids = []
    for number in range(2, 14):
        historical = models.PaperSourceVersion(
            workspace_id=workspace.id,
            version=number,
            source_kind=source.source_kind,
            filename=source.filename,
            object_key=f"historical/source-{number}.zip",
            content_hash=f"source-hash-{number}",
            content_size=100 * 1024 * 1024,
            manifest=["paper.md", "figure.png"],
        )
        db.add(historical)
        source_ids.append(historical.id)
    raw = json.dumps({"summary": "Recorded result", "message": "Original historical chair text"}).encode()
    storage.upload("historical/ac-result.json", raw)
    run = models.PaperAgentRun(
        workspace_id=workspace.id,
        source_version_id=source.id,
        run_kind="ac_summary",
        status="ready",
        message="Saved chair result",
        artifact_object_key="historical/ac-result.json",
    )
    db.add(run)
    db.commit()
    storage.reads.clear()
    result = history.get_history(db, user, workspace.id)
    assert {f"source:{identity}" for identity in source_ids} <= {item.id for item in result.revisions}
    assert f"run:{run.id}" in {item.id for item in result.revisions}
    assert source.object_key not in storage.reads
    assert not any(key.startswith("historical/") for key in storage.reads)
    storage.reads.clear()
    assert history.get_history(db, user, workspace.id).current_revision_id == result.current_revision_id
    assert source.object_key not in storage.reads
    assert not any(key.startswith("historical/") for key in storage.reads)
    selected = history.get_file(db, user, workspace.id, f"run:{run.id}", "outputs/ac-summary.md")
    assert selected.content == "Original historical chair text"
    assert "historical/ac-result.json" in storage.reads
    assert not any(key.startswith("historical/source-") for key in storage.reads)


def test_regional_listing_defers_archived_review_bodies(revision_db):
    """Recover regional JSON metadata without downloading every evidence version."""
    db, user, workspace, _source, review, storage = revision_db
    raw = b"Archived original review evidence"
    fingerprint = hashlib.sha256(raw).hexdigest()
    storage.upload("archived-evidence.md", raw)
    snapshot = {
        "regions": {},
        "review_versions": {str(review.id): {fingerprint: {"object_key": "archived-evidence.md"}}},
    }
    storage.upload("legacy-regional.json", json.dumps(snapshot).encode())
    workspace.rebuttal_context = {
        **workspace.rebuttal_context,
        "_paper_regions": {"object_key": "legacy-regional.json"},
    }
    db.add(workspace)
    db.commit()
    storage.reads.clear()
    history.get_history(db, user, workspace.id)
    assert "legacy-regional.json" in storage.reads
    assert "archived-evidence.md" not in storage.reads
    file = history.get_file(db, user, workspace.id, "region:legacy-regional", f"reviews/{review.id}/{fingerprint}.md")
    assert file.content == raw.decode()


def test_legacy_regional_restore_recovers_source_review_and_output_text(revision_db):
    """Restore only recorded legacy fields while retaining newer unknown content."""
    db, user, workspace, source, review, storage = revision_db
    original = storage.download(review.object_key)
    fingerprint = hashlib.sha256(original).hexdigest()
    region = {
        "regions": {
            "source": {"id": "proposal:paper.md", "target": ["source", "paper.md"], "content": "Older source text"},
            "chair": {"id": "chair:message", "target": ["context", "chair_message"], "content": "Older author wording"},
            "review": {"id": "review:original", "target": ["review", str(review.id)], "content": original.decode()},
        },
        "review_versions": {str(review.id): {fingerprint: {"object_key": review.object_key}}},
    }
    storage.upload("legacy-edit.json", json.dumps(region).encode())
    workspace.rebuttal_context = {**workspace.rebuttal_context, "_paper_regions": {"object_key": "legacy-edit.json"}}
    db.add(workspace)
    db.commit()
    history.get_history(db, user, workspace.id)
    storage.upload("later-evidence.md", b"Later review evidence")
    review.object_key = "later-evidence.md"
    db.add(review)
    db.commit()
    history.restore_revision(db, user, workspace.id, "region:legacy-edit", request_for(revision_db))
    updated = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    assert updated.id != source.id
    assert dict(paper_service._source_entries(updated))["paper.md"] == b"Older source text"
    assert workspace.rebuttal_context["chair_message"] == "Older author wording"
    assert workspace.rebuttal_context["rendered"]["text"] == "Exact response.\r\n"
    assert review.object_key == "original-review.md"


def test_compare_lists_text_binary_additions_and_deletions(revision_db):
    """Return the union of paths with byte sizes and all four statuses."""
    db, user, workspace, source, _review, _storage = revision_db
    first = capture(revision_db)
    with history.suppress_capture():
        paper_service._create_source_version(
            db,
            workspace,
            [("paper.md", b"Changed"), ("new.txt", b"Added")],
            parent=source,
            change_summary="Change files",
            commit=False,
        )
    db.commit()
    second = capture(revision_db)
    result = history.compare_revisions(db, user, workspace.id, first.id, second.id)
    files = {item.path: item for item in result.files}
    assert files["source/paper.md"].status == "modified"
    assert files["source/new.txt"].status == "added"
    assert files["source/figure.png"].status == "deleted"
    assert files["source/figure.png"].binary
    assert files["outputs/ac-summary.md"].status == "unchanged"
    assert sum(result.counts.values()) == len(files)


def export_fixture(fixture, source=None, build="a" * 64):
    """Store the renderer's actual manifest shape with no extra storage API."""
    _db, _user, workspace, initial, _review, storage = fixture
    source = source or initial
    prefix = f"paper/{workspace.user_id}/{workspace.id}/renders/{source.id}/{source.content_hash}/{build}"
    storage.upload(f"{prefix}/proposal.pdf", b"%PDF\x00exact render")
    storage.upload(f"{prefix}/proposal.typ", b"Exact generated Typst")
    manifest = {
        "source_version_id": str(source.id),
        "source_hash": source.content_hash,
        "status": "ready",
        "diagnostics": [],
        "files": [
            {"path": "proposal.pdf", "url": "/authenticated.pdf"},
            {"path": "proposal.typ", "url": "/authenticated.typ"},
        ],
    }
    key = f"{prefix}/manifest.json"
    storage.upload(key, json.dumps(manifest).encode())
    return key


def test_exports_are_lazy_immutable_refs_and_do_not_change_content_revision(revision_db):
    """Register PDF and Typst outputs without conflicting with an active author draft."""
    db, user, workspace, *_ = revision_db
    revision = capture(revision_db)
    before_hash = history.current_state_hash(db, workspace)
    key = export_fixture(revision_db)
    history.record_export(db, workspace, key)
    history.record_export(db, workspace, key)
    result = history.get_history(db, user, workspace.id)
    assert result.current_revision_id == revision.id
    assert result.current_state_hash == before_hash
    path = f"exports/{'a' * 64}/proposal.pdf"
    file = history.get_file(db, user, workspace.id, revision.id, path)
    assert file.binary and file.content is None
    assert history.download_file(db, user, workspace.id, revision.id, path)[0] == b"%PDF\x00exact render"
    assert len(workspace.rebuttal_context["_paper_exports"][str(revision.source_version_id)]) == 1


def test_export_registration_preserves_author_edits_from_another_session(revision_db):
    """A slow preview cannot overwrite outputs or a newer history pointer."""
    db, _user, workspace, *_ = revision_db
    capture(revision_db)
    key = export_fixture(revision_db)
    with Session(db.get_bind(), expire_on_commit=False) as render_db:
        stale = render_db.get(models.PaperWorkspace, workspace.id)
        workspace.rebuttal_context = {**workspace.rebuttal_context, "chair_message": "Concurrent author correction"}
        newer = capture(revision_db)
        history.record_export(render_db, stale, key)
    db.expire_all()
    assert workspace.rebuttal_context["chair_message"] == "Concurrent author correction"
    assert workspace.rebuttal_context["_paper_history"]["revision"] == newer.id


def test_foreign_revision_cannot_be_read_or_restored_from_owned_workspace(revision_db):
    """Scope revision lookup to the workspace chain, independent of identifier shape."""
    db, user, workspace, *_ = revision_db
    capture(revision_db)
    other = models.PaperWorkspace(user_id=user.id, title="Other", mode="manuscript")
    db.add(other)
    db.commit()
    foreign = history.capture_revision(db, other, commit=True)
    with pytest.raises(HTTPException) as missing:
        history.get_file(db, user, workspace.id, foreign.id, "outputs/ac-summary.md")
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as missing:
        history.restore_revision(db, user, workspace.id, foreign.id, request_for(revision_db))
    assert missing.value.status_code == 404


@pytest.mark.parametrize(
    "path", ["../secret", "/etc/passwd", "outputs/../secret", "source\\secret", ".", "a//b", "a/./b"]
)
def test_rejects_unsafe_file_paths(revision_db, path):
    """Never translate caller paths into arbitrary storage keys or filesystem reads."""
    db, user, workspace, *_ = revision_db
    revision = capture(revision_db)
    with pytest.raises(HTTPException) as failure:
        history.get_file(db, user, workspace.id, revision.id, path)
    assert failure.value.status_code == 422


def test_routes_expose_contract_authenticated_downloads_and_restore_guards(revision_db):
    """Exercise the real router with exact public models and disposable dependencies."""
    db, user, workspace, *_ = revision_db
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/llm4ad")
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_current_user] = lambda: user
    root = f"/api/v1/llm4ad/papers/workspaces/{workspace.id}/history"
    with TestClient(app) as client:
        response = client.get(root)
        assert response.status_code == 200
        data = response.json()
        identity = data["current_revision_id"]
        assert client.get(f"{root}/compare", params={"base_id": identity, "target_id": identity}).status_code == 200
        file = client.get(f"{root}/{identity}/file", params={"path": "source/figure.png"}).json()
        downloaded = client.get(file["download_url"])
        assert downloaded.status_code == 200
        assert downloaded.content == b"\x89PNG\x00old"
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        assert "attachment" in downloaded.headers["content-disposition"]
        assert client.post(f"{root}/{identity}/restore", json={}).status_code == 422
        assert client.post(f"{root}/{identity}/restore", json={"expected_state_hash": "0" * 64}).status_code == 409
        app.dependency_overrides[deps.get_current_user] = lambda: models.User(
            email="foreign@example.com", hashed_password="unused"
        )
        assert client.get(root).status_code == 404
        assert client.get(f"{root}/{identity}/file", params={"path": "source/paper.md"}).status_code == 404
        assert client.get(file["download_url"]).status_code == 404
        assert (
            client.post(
                f"{root}/{identity}/restore", json={"expected_state_hash": data["current_state_hash"]}
            ).status_code
            == 404
        )
