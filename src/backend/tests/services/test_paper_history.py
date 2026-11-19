"""Isolated regression tests for source history and safe publication."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlmodel import Session, create_engine, select

from app import models
from app.schemas import paper as schemas
from app.services import paper_service


class MemoryStorage:
    """Keep immutable source bytes in memory without contacting RustFS."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def upload(self, key, data, **_kwargs):
        self.objects[key] = data.read() if hasattr(data, "read") else data

    def download(self, key, **_kwargs):
        return self.objects[key]

    def delete(self, key, **_kwargs):
        self.objects.pop(key, None)


@pytest.fixture
def history(tmp_path, monkeypatch):
    """Provide a real isolated SQL session with in-memory object storage."""
    engine = create_engine(f"sqlite:///{tmp_path / 'history.sqlite'}")
    tables = [
        models.PaperWorkspace,
        models.PaperSourceVersion,
        models.PaperReview,
        models.PaperEvaluationMetric,
        models.PaperBoundarySnapshot,
        models.PaperOptimizationTarget,
        models.PaperRevisionCandidate,
        models.PaperAgentRun,
    ]
    models.PaperWorkspace.metadata.create_all(engine, tables=[model.__table__ for model in tables])
    storage = MemoryStorage()
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    with patch("boto3.client"):
        from app.tasks import paper_agent
    monkeypatch.setattr(paper_agent, "storage", storage)
    monkeypatch.setattr(paper_agent, "get_db_session", lambda: Session(engine))
    user = models.User(email="history@example.com", hashed_password="unused")
    with Session(engine) as db:
        yield db, user, storage, paper_agent
    engine.dispose()


def source_workspace(history, mode="manuscript"):
    """Create one initial durable source through the production helper."""
    db, user, _storage, _agent = history
    if mode == "proposal":
        # These cases intentionally exercise existing Typst projects.
        workspace = models.PaperWorkspace(
            user_id=user.id, title="History", mode=mode, proposal_entry_path="proposal.typ"
        )
        db.add(workspace)
        db.flush()
        paper_service._create_source_version(
            db,
            workspace,
            paper_service.ensure_proposal_assembly(
                [
                    ("proposal.typ", paper_service._default_proposal_source(workspace.title, None)),
                    (paper_service._PROPOSAL_STYLE_PATH, paper_service._PROPOSAL_STYLE_SOURCE),
                ],
                "proposal.typ",
            ),
            change_summary="Legacy Typst source",
        )
    else:
        workspace = paper_service.create_workspace(db, user, schemas.PaperWorkspaceCreate(title="History", mode=mode))
    if workspace.active_source_version_id:
        source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    else:
        source = paper_service._create_source_version(
            db, workspace, [("paper.md", b"Original author text.")], change_summary="Initial source"
        )
    return workspace, source


@pytest.mark.parametrize("mode", ["proposal", "manuscript"])
def test_edits_preserve_original_bytes_and_link_versions(history, mode):
    """Retain downloadable originals while advancing the active revision."""
    db, user, storage, _agent = history
    workspace, original = source_workspace(history, mode)
    original_id, original_hash, original_key = original.id, original.content_hash, original.object_key
    original_bytes = storage.download(original_key)
    path = "proposal.typ" if mode == "proposal" else "paper.md"
    updated = paper_service.update_source_file(
        db, user, original.id, schemas.PaperSourceFileUpdateRequest(path=path, content="Author's new text.")
    )
    assert updated.id != original_id
    assert updated.parent_version_id == original_id
    assert updated.version == 2
    assert workspace.active_source_version_id == updated.id
    assert db.get(models.PaperSourceVersion, original_id).content_hash == original_hash
    assert storage.download(original_key) == original_bytes
    assert paper_service.download_source_version(db, user, original_id)[0] == original_bytes


def test_stale_edits_do_not_reactivate_an_old_source(history):
    """Reject changes against an old source after another author saved."""
    db, user, storage, _agent = history
    workspace, original = source_workspace(history)
    current = paper_service.update_source_file(
        db, user, original.id, schemas.PaperSourceFileUpdateRequest(path="paper.md", content="Saved by another editor.")
    )
    before = dict(storage.objects)
    with pytest.raises(HTTPException) as conflict:
        paper_service.update_source_file(
            db, user, original.id, schemas.PaperSourceFileUpdateRequest(path="paper.md", content="Stale edit.")
        )
    assert conflict.value.status_code == 409
    assert workspace.active_source_version_id == current.id
    assert storage.objects == before


def test_review_ids_survive_source_versions(history):
    """Carry stable review identities and metrics to the active source."""
    db, user, storage, _agent = history
    workspace, original = source_workspace(history)
    review = models.PaperReview(
        workspace_id=workspace.id, source_version_id=original.id, object_key="review.md", content_hash="review"
    )
    storage.upload("review.md", b"Original reviewer report.")
    metric = models.PaperEvaluationMetric(
        workspace_id=workspace.id,
        source_version_id=original.id,
        review_id=review.id,
        key="clarity",
        title="Clarity",
        description="Explain the method",
        source="reviewer",
        locked=True,
    )
    db.add(review)
    db.add(metric)
    db.commit()
    review_id = review.id
    updated = paper_service.update_source_file(
        db, user, original.id, schemas.PaperSourceFileUpdateRequest(path="paper.md", content="Clarified method.")
    )
    db.refresh(review)
    db.refresh(metric)
    assert review.id == review_id
    assert review.source_version_id == updated.id
    assert metric.source_version_id == updated.id
    assert storage.download(review.object_key) == b"Original reviewer report."


def test_version_number_uses_all_history(history):
    """Allocate above the maximum even when an older revision is active."""
    db, _user, _storage, _agent = history
    workspace, original = source_workspace(history)
    newer = models.PaperSourceVersion(
        workspace_id=workspace.id,
        version=12,
        source_kind="markdown",
        filename="paper.md",
        object_key="historical-paper.md",
        content_hash="historical",
        content_size=10,
        manifest=["paper.md"],
    )
    db.add(newer)
    db.commit()
    updated = paper_service._persist_source_entries(
        db, workspace, original, [("paper.md", b"Next version.")], change_summary="Advance"
    )
    assert updated.version == 13
    assert updated.parent_version_id == original.id


def test_proposal_manual_edit_can_run_during_coordinator(history):
    """Allow guarded local edits while retaining the legacy agent lock."""
    db, user, _storage, _agent = history
    workspace, source = source_workspace(history, "proposal")
    db.add(
        models.PaperAgentRun(
            workspace_id=workspace.id,
            source_version_id=source.id,
            run_kind="proposal_methods",
            status="running",
            request_payload={"auto_workflow": True},
        )
    )
    db.commit()
    updated = paper_service.update_source_file(
        db,
        user,
        source.id,
        schemas.PaperSourceFileUpdateRequest(
            path="sections/05-methods.typ", content="== Author methods\nProtected text."
        ),
    )
    assert updated.parent_version_id == source.id
    with pytest.raises(HTTPException):
        paper_service._ensure_paper_workspace_source_mutable(db, workspace.id)


def publish_proposal(history, tmp_path, workspace, source, *, continuous=True, reported=None, stage="proposal_methods"):
    """Publish the actual runtime source tree through the agent persistence path."""
    db, user, _storage, agent = history
    run = models.PaperAgentRun(
        workspace_id=workspace.id,
        source_version_id=source.id,
        run_kind=stage,
        status="running",
        request_payload={
            "auto_workflow": continuous,
            "writable_paths": ["sections/05-methods.typ"],
            "source_content_hash": source.content_hash,
        },
    )
    db.add(run)
    db.commit()
    output = tmp_path / f"publication-{run.id}" / "output"
    output.mkdir(parents=True)
    payload = {
        "source_paths": reported or ["sections/05-methods.typ"],
        "summary": "Proposed methods improvement.",
        "context_patch": {"upsert": [{"key": "scope", "title": "Scope", "content": "A bounded method."}]},
    }
    (output / "result.json").write_text(json.dumps(payload))
    published = agent._persist_output(run.id, user.id, output.parent)
    db.expire_all()
    return published, db.get(models.PaperAgentRun, run.id)


def test_first_proposal_write_publishes_a_new_version(history, tmp_path):
    """Publish a first section normally and bind the artifact to its new source."""
    db, _user, _storage, _agent = history
    workspace, source = source_workspace(history, "proposal")
    path = paper_service.paper_workspace_source_dir(workspace.user_id, workspace.id) / "sections/05-methods.typ"
    path.write_text("== Methods\nFirst generated method.")
    published, run = publish_proposal(history, tmp_path, workspace, source)
    assert published
    active = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    assert active.id != source.id
    assert active.parent_version_id == source.id
    assert run.source_version_id == active.id
    assert workspace.proposal_stage_states["methods"]["source_version_id"] == str(active.id)
    assert not db.exec(select(models.PaperRevisionCandidate)).all()


def test_proposal_rerun_waits_for_approval_and_accept_creates_history(history, tmp_path):
    """Preserve author text until an explicit guarded candidate acceptance."""
    db, user, storage, _agent = history
    workspace, source = source_workspace(history, "proposal")
    source = paper_service.update_source_file(
        db,
        user,
        source.id,
        schemas.PaperSourceFileUpdateRequest(
            path="sections/05-methods.typ",
            content="== Methods\nProtected author's wording.",
        ),
    )
    original_bytes = storage.download(source.object_key)
    root = paper_service.paper_workspace_source_dir(workspace.user_id, workspace.id)
    (root / "sections/05-methods.typ").write_text("== Methods\nAgent's replacement.")
    published, run = publish_proposal(history, tmp_path, workspace, source)
    assert not published
    assert run.message == "Proposal changes await author approval"
    assert workspace.active_source_version_id == source.id
    assert storage.download(source.object_key) == original_bytes
    assert "Protected author's wording." in (root / "sections/05-methods.typ").read_text()
    assert workspace.proposal_foundation is None
    candidate = db.exec(select(models.PaperRevisionCandidate)).one()
    patch_data = json.loads(paper_service.get_revision_patch(db, user, candidate.id).content)
    assert patch_data["schema_version"] == "paper-source-diff-v1"
    response = paper_service.accept_revision_candidate(db, user, candidate.id)
    accepted = db.get(models.PaperSourceVersion, response.source_version.id)
    assert accepted.parent_version_id == source.id
    assert workspace.active_source_version_id == accepted.id
    assert "Agent's replacement." in (root / "sections/05-methods.typ").read_text()
    assert storage.download(source.object_key) == original_bytes
    assert workspace.proposal_foundation["blocks"][0]["key"] == "scope"
    assert paper_service.accept_revision_candidate(db, user, candidate.id).source_version.id == accepted.id


def test_pending_proposal_cannot_overwrite_a_later_author_edit(history, tmp_path):
    """Keep candidate base revision checks after the author edits again."""
    db, user, _storage, _agent = history
    workspace, source = source_workspace(history, "proposal")
    source = paper_service.update_source_file(
        db,
        user,
        source.id,
        schemas.PaperSourceFileUpdateRequest(
            path="sections/05-methods.typ",
            content="== Methods\nOriginal author text.",
        ),
    )
    root = paper_service.paper_workspace_source_dir(workspace.user_id, workspace.id)
    (root / "sections/05-methods.typ").write_text("== Methods\nAgent draft.")
    publish_proposal(history, tmp_path, workspace, source)
    candidate = db.exec(select(models.PaperRevisionCandidate)).one()
    current = paper_service.update_source_file(
        db,
        user,
        source.id,
        schemas.PaperSourceFileUpdateRequest(
            path="sections/05-methods.typ",
            content="== Methods\nLater author edit.",
        ),
    )
    with pytest.raises(HTTPException) as conflict:
        paper_service.accept_revision_candidate(db, user, candidate.id)
    assert conflict.value.status_code == 409
    assert workspace.active_source_version_id == current.id
    assert "Later author edit." in (root / "sections/05-methods.typ").read_text()


def test_coordinator_reports_cross_stage_changes_but_legacy_remains_scoped(history, tmp_path):
    """Allow continuous coordination to improve an unrelated new section."""
    db, _user, _storage, _agent = history
    workspace, source = source_workspace(history, "proposal")
    root = paper_service.paper_workspace_source_dir(workspace.user_id, workspace.id)
    (root / "sections/01-rationale.typ").write_text("== Rationale\nFirst rationale.")
    with pytest.raises(ValueError, match="outside its boundary"):
        publish_proposal(history, tmp_path, workspace, source, continuous=False, reported=["sections/01-rationale.typ"])
    published, _run = publish_proposal(history, tmp_path, workspace, source, reported=["sections/01-rationale.typ"])
    assert published
    assert workspace.active_source_version_id != source.id
    assert len(db.exec(select(models.PaperSourceVersion)).all()) == 2


def test_coordinator_discovers_modified_paths_without_manual_reporting(history, tmp_path):
    """Integrate actual file changes without asking the model to repeat paths."""
    workspace, source = source_workspace(history, "proposal")
    root = paper_service.paper_workspace_source_dir(workspace.user_id, workspace.id)
    (root / "sections/01-rationale.typ").write_text("== Rationale\nUnexpected change.")
    published, run = publish_proposal(history, tmp_path, workspace, source)
    assert published
    assert run.status == "ready"


def test_restore_creates_new_version_and_keeps_downloadable_history(history):
    """Restore old content without deleting or reactivating the old source row."""
    db, user, storage, _agent = history
    workspace, original = source_workspace(history)
    original_bytes = storage.download(original.object_key)
    current = paper_service.update_source_file(
        db, user, original.id, schemas.PaperSourceFileUpdateRequest(path="paper.md", content="Later author text.")
    )
    restored = paper_service.restore_source_version(
        db,
        user,
        original.id,
        expected_source_version_id=current.id,
        expected_content_hash=current.content_hash,
    )
    assert restored.id not in {original.id, current.id}
    assert restored.parent_version_id == current.id
    assert restored.version == 3
    assert workspace.active_source_version_id == restored.id
    assert storage.download(restored.object_key) == original_bytes
    assert paper_service.download_source_version(db, user, original.id)[0] == original_bytes


def test_source_hash_guard_prevents_same_revision_content_races(history):
    """Check content hashes in addition to the active revision identity."""
    db, _user, storage, _agent = history
    workspace, source = source_workspace(history)
    before = dict(storage.objects)
    with pytest.raises(HTTPException) as conflict:
        paper_service._persist_source_entries(
            db,
            workspace,
            source,
            [("paper.md", b"Stale changed text.")],
            change_summary="Stale",
            expected_content_hash="outdated-hash",
        )
    assert conflict.value.status_code == 409
    assert workspace.active_source_version_id == source.id
    assert storage.objects == before


def test_version_publication_failure_preserves_original_and_cleans_upload(history, monkeypatch):
    """Roll back the pointer and remove a new object when SQL commit fails."""
    db, user, storage, _agent = history
    workspace, original = source_workspace(history)
    source_id, workspace_id = original.id, workspace.id
    before = dict(storage.objects)

    def fail_commit():
        raise RuntimeError("Injected database failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="Injected database failure"):
        paper_service.update_source_file(
            db, user, source_id, schemas.PaperSourceFileUpdateRequest(path="paper.md", content="Uncommitted text.")
        )
    assert storage.objects == before
    assert db.get(models.PaperWorkspace, workspace_id).active_source_version_id == source_id
    assert len(db.exec(select(models.PaperSourceVersion)).all()) == 1


def test_unaffected_stage_artifact_rebases_without_changing_historical_run(history):
    """Keep predecessor artifacts usable while retaining their original source."""
    db, user, storage, _agent = history
    workspace, original = source_workspace(history, "proposal")
    run = models.PaperAgentRun(
        workspace_id=workspace.id,
        source_version_id=original.id,
        run_kind="proposal_literature",
        status="ready",
        artifact_object_key="literature.json",
    )
    storage.upload("literature.json", b'{"summary": "Original literature evidence"}')
    workspace.proposal_stage_states = {
        "literature": {"run_id": str(run.id), "source_version_id": str(original.id), "status": "ready"},
    }
    db.add(run)
    db.add(workspace)
    db.commit()
    current = paper_service.update_source_file(
        db,
        user,
        original.id,
        schemas.PaperSourceFileUpdateRequest(
            path="sections/07-plan.typ",
            content="== Plan\nAuthor's plan.",
            workflow_stage="innovation_plan",
        ),
    )
    latest = paper_service.get_stage_artifact(db, user.id, workspace.id, "literature")
    assert latest.source_version_id == current.id
    assert latest.status == "ready"
    historical = paper_service.get_stage_artifact(db, user.id, workspace.id, "literature", run_id=run.id)
    assert historical.source_version_id == original.id
    db.refresh(run)
    assert run.source_version_id == original.id


def test_last_file_deletion_preserves_original_and_allows_restore(history):
    """Keep the source row and download even when the working tree is cleared."""
    db, user, storage, _agent = history
    workspace, original = source_workspace(history)
    before = storage.download(original.object_key)
    assert (
        paper_service.delete_source_path(db, user, original.id, schemas.PaperSourcePathDeleteRequest(path="paper.md"))
        is None
    )
    assert workspace.active_source_version_id is None
    assert db.get(models.PaperSourceVersion, original.id) is not None
    assert paper_service.download_source_version(db, user, original.id)[0] == before
    restored = paper_service.restore_source_version(db, user, original.id, expected_source_version_id=None)
    assert restored.id != original.id
    assert restored.parent_version_id == original.id
    assert restored.version == 2
    assert storage.download(restored.object_key) == before


def test_editorial_review_does_not_block_source_export(history, tmp_path):
    """Store editorial findings while allowing the owned source to be exported."""
    db, user, _storage, agent = history
    workspace, source = source_workspace(history, "proposal")
    workspace.proposal_foundation = {"blocks": [{"key": "scope", "title": "Scope", "content": "A proposal."}]}
    run = models.PaperAgentRun(
        workspace_id=workspace.id,
        source_version_id=source.id,
        run_kind="proposal_final_review",
        status="running",
        request_payload={"auto_workflow": True, "source_content_hash": source.content_hash},
    )
    db.add(workspace)
    db.add(run)
    db.commit()
    output = tmp_path / "review" / "output"
    output.mkdir(parents=True)
    (output / "result.json").write_text(
        json.dumps(
            {
                "entry_path": "proposal.typ",
                "summary": "Consider clarifying the evidence.",
                "findings": ["Clarify the evidence."],
                "ready_for_export": False,
                "context_patch": {"upsert": [], "remove_keys": []},
            }
        )
    )
    assert agent._persist_output(run.id, user.id, output.parent)
    db.expire_all()
    assert workspace.proposal_stage_states["final_review"]["status"] == "needs_revision"
    exported = paper_service.export_source_version(db, user, workspace.active_source_version_id)
    assert str(workspace.active_source_version_id) in exported.url
    assert paper_service.download_source_version(db, user, source.id)[0]


def test_agent_publication_rejects_changed_source_hash(history, tmp_path):
    """Enforce the source hash captured when the agent started its work."""
    db, user, storage, agent = history
    workspace, source = source_workspace(history, "proposal")
    run = models.PaperAgentRun(
        workspace_id=workspace.id,
        source_version_id=source.id,
        run_kind="proposal_methods",
        status="running",
        request_payload={"auto_workflow": True, "source_content_hash": "old-hash"},
    )
    db.add(run)
    db.commit()
    output = tmp_path / "stale-run" / "output"
    output.mkdir(parents=True)
    (output / "result.json").write_text("{}")
    before = dict(storage.objects)
    with pytest.raises(HTTPException) as conflict:
        agent._persist_output(run.id, user.id, output.parent)
    assert conflict.value.status_code == 409
    assert storage.objects == before


def test_algorithm_legacy_persistence_remains_mutable(history):
    """Keep algorithm source persistence compatible with its existing stages."""
    db, _user, storage, _agent = history
    workspace, source = source_workspace(history, "algorithm")
    updated = paper_service._persist_source_entries(
        db,
        workspace,
        source,
        [("paper.md", b"Updated algorithm source.")],
        change_summary="Legacy edit",
    )
    assert updated.id == source.id
    assert updated.version == 1
    assert storage.download(updated.object_key) == b"Updated algorithm source."


def test_coordinator_still_rejects_symlinked_source_roots(history, tmp_path):
    """Prevent ownership bypass from weakening filesystem traversal safety."""
    _db, _user, _storage, agent = history
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "paper.md").write_text("Private file.")
    linked = tmp_path / "linked-source"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        agent._workspace_source_entries(linked)


def test_original_review_edit_endpoint_archives_pre_edit_evidence(history):
    """Resolve pre-edit review text even when no region was opened before saving."""
    from app.services import paper_regions

    db, user, storage, _agent = history
    workspace, source = source_workspace(history)
    original = "Original reviewer wording with a precise concern."
    review = paper_service.attach_reviewer_feedback(
        db,
        user,
        source.id,
        schemas.PaperReviewCreate(
            content=original,
            reviewer_label="Reviewer A",
        ),
    )
    original_id, original_key = review.id, review.object_key
    original_version = hashlib.sha256(original.encode()).hexdigest()
    updated = paper_service.update_reviewer_feedback(
        db,
        user,
        review.id,
        schemas.PaperReviewUpdate(
            content="Author corrected the reviewer transcription.",
            reviewer_label="Reviewer A",
        ),
    )
    assert updated.id == original_id
    assert updated.object_key != original_key
    assert storage.download(original_key).decode() == original
    assert paper_regions.get_review_source(db, user, workspace.id, review.id, original_version) == original
    refs = paper_regions._resolve_refs(
        workspace,
        paper_regions._load(workspace),
        {str(review.id): updated},
        [
            {"review_id": str(review.id), "version": original_version, "quote": "precise concern"},
        ],
    )
    assert refs[0].status == "verified"
    assert original_version in refs[0].path
    second_version = updated.content_hash
    paper_service.update_reviewer_feedback(
        db,
        user,
        review.id,
        schemas.PaperReviewUpdate(
            content="A second correction.",
            reviewer_label="Reviewer A",
        ),
    )
    assert paper_regions.get_review_source(db, user, workspace.id, review.id, original_version) == original
    assert paper_regions.get_review_source(db, user, workspace.id, review.id, second_version) == (
        "Author corrected the reviewer transcription."
    )


def test_review_edit_failure_preserves_original_and_regional_pointer(history, monkeypatch):
    """Rollback review changes atomically with the regional archive pointer."""
    db, user, storage, _agent = history
    _workspace, source = source_workspace(history)
    review = paper_service.attach_reviewer_feedback(
        db,
        user,
        source.id,
        schemas.PaperReviewCreate(
            content="The original review.",
            reviewer_label="Reviewer A",
        ),
    )
    original_id, original_key = review.id, review.object_key

    def fail_commit():
        raise RuntimeError("Injected review commit failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="Injected review commit failure"):
        paper_service.update_reviewer_feedback(
            db,
            user,
            review.id,
            schemas.PaperReviewUpdate(
                content="Uncommitted replacement.",
                reviewer_label="Reviewer A",
            ),
        )
    assert db.get(models.PaperReview, original_id).object_key == original_key
    assert storage.download(original_key) == b"The original review."


def test_manuscript_candidate_preserves_old_source_on_accept(history):
    """Accept the existing exact-fragment format through immutable history."""
    db, user, storage, _agent = history
    workspace, source = source_workspace(history)
    candidate = models.PaperRevisionCandidate(
        workspace_id=workspace.id,
        source_version_id=source.id,
        title="Clarify text",
        summary="Clarify the method.",
        patch_object_key="fragment-candidate.json",
    )
    storage.upload(
        candidate.patch_object_key,
        json.dumps(
            {
                "source_path": "paper.md",
                "original_text": "Original author text.",
                "replacement_text": "Clarified author text.",
            }
        ).encode(),
    )
    db.add(candidate)
    db.commit()
    response = paper_service.accept_revision_candidate(db, user, candidate.id)
    accepted = response.source_version
    assert accepted.id != source.id
    assert accepted.parent_version_id == source.id
    assert workspace.active_source_version_id == accepted.id
    assert storage.download(source.object_key) == b"Original author text."
    assert storage.download(accepted.object_key) == b"Clarified author text."
    assert candidate.accepted_source_version_id == accepted.id


def test_restore_api_requires_current_revision_and_enforces_ownership(history):
    """Expose restore through authenticated routes with optimistic conflict checks."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.llm4ad import paper_history

    db, user, _storage, _agent = history
    workspace, original = source_workspace(history)
    current = paper_service.update_source_file(
        db, user, original.id, schemas.PaperSourceFileUpdateRequest(path="paper.md", content="New author text.")
    )
    app = FastAPI()
    app.include_router(paper_history.router, prefix="/api/v1/llm4ad")
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_current_user] = lambda: user
    endpoint = f"/api/v1/llm4ad/papers/source-versions/{original.id}/restore"
    with TestClient(app) as client:
        assert client.post(endpoint, json={}).status_code == 422
        assert client.post(endpoint, json={"expected_source_version_id": str(original.id)}).status_code == 409
        response = client.post(
            endpoint,
            json={
                "expected_source_version_id": str(current.id),
                "expected_content_hash": current.content_hash,
            },
        )
        assert response.status_code == 200
        assert response.json()["parent_version_id"] == str(current.id)
        assert response.json()["id"] not in {str(original.id), str(current.id)}
        app.dependency_overrides[deps.get_current_user] = lambda: models.User(
            email="someone-else@example.com", hashed_password="unused"
        )
        assert (
            client.post(
                endpoint,
                json={
                    "expected_source_version_id": str(workspace.active_source_version_id),
                },
            ).status_code
            == 404
        )


def pending_source_candidate(history, source, *, status="draft", accepted_source_version_id=None):
    """Store an approval candidate without touching its canonical source."""
    db, _user, storage, _agent = history
    candidate = models.PaperRevisionCandidate(
        workspace_id=source.workspace_id,
        source_version_id=source.id,
        title="Proposal replacement",
        summary="Review this full source diff.",
        patch_object_key=f"{source.id}/{status}-candidate.json",
        status=status,
        accepted_source_version_id=accepted_source_version_id,
    )
    storage.upload(candidate.patch_object_key, b'{"schema_version": "paper-source-diff-v1"}')
    db.add(candidate)
    db.commit()
    return candidate


@pytest.mark.parametrize("status", ["draft", "pending"])
def test_reject_source_candidate_is_idempotent_and_preserves_canonical_content(history, status):
    """Reject pending suggestions without changing source, context, or object bytes."""
    from app.services import paper_history

    db, user, storage, _agent = history
    workspace, source = source_workspace(history, "proposal")
    candidate = pending_source_candidate(history, source, status=status)
    db.refresh(workspace)
    db.refresh(source)
    source_before = source.model_dump()
    workspace_before = workspace.model_dump()
    objects_before = dict(storage.objects)
    root = paper_service.paper_workspace_source_dir(workspace.user_id, workspace.id)
    (root / "proposal.typ").write_text("An agent's uncommitted working tree.")
    response = paper_history.reject_revision_candidate(db, user, candidate.id)
    assert response.candidate_id == candidate.id
    assert response.status == "rejected"
    assert db.get(models.PaperRevisionCandidate, candidate.id).status == "rejected"
    rejected_time = candidate.updated_time
    assert paper_history.reject_revision_candidate(db, user, candidate.id) == response
    assert candidate.updated_time == rejected_time
    db.refresh(workspace)
    db.refresh(source)
    assert workspace.model_dump() == workspace_before
    assert source.model_dump() == source_before
    assert storage.objects == objects_before
    assert (root / "proposal.typ").read_text() == "An agent's uncommitted working tree."
    assert len(db.exec(select(models.PaperSourceVersion)).all()) == 1


@pytest.mark.parametrize("status", ["accepted", "applied", "cancelled", "failed"])
def test_reject_candidate_rejects_applied_and_nonpending_states(history, status):
    """Keep accepted or otherwise terminal candidates out of the rejection path."""
    from app.services import paper_history

    db, user, storage, _agent = history
    _workspace, source = source_workspace(history, "proposal")
    candidate = pending_source_candidate(history, source, status=status)
    objects_before = dict(storage.objects)
    with pytest.raises(HTTPException) as conflict:
        paper_history.reject_revision_candidate(db, user, candidate.id)
    assert conflict.value.status_code == 409
    assert candidate.status == status
    assert storage.objects == objects_before


def test_reject_candidate_checks_accepted_version_even_if_status_is_pending(history):
    """Treat an existing accepted version as applied despite a legacy status value."""
    from app.services import paper_history

    _db, user, _storage, _agent = history
    _workspace, source = source_workspace(history, "proposal")
    candidate = pending_source_candidate(history, source, accepted_source_version_id=source.id)
    with pytest.raises(HTTPException) as conflict:
        paper_history.reject_revision_candidate(history[0], user, candidate.id)
    assert conflict.value.status_code == 409
    assert candidate.status == "draft"


def test_reject_candidate_requires_owner_and_allows_discarding_a_stale_draft(history):
    """Hide foreign candidates while permitting rejection after later author edits."""
    from app.services import paper_history

    db, user, _storage, _agent = history
    workspace, source = source_workspace(history)
    candidate = pending_source_candidate(history, source)
    stranger = models.User(email="foreign@example.com", hashed_password="unused")
    with pytest.raises(HTTPException) as missing:
        paper_history.reject_revision_candidate(db, stranger, candidate.id)
    assert missing.value.status_code == 404
    current = paper_service.update_source_file(
        db, user, source.id, schemas.PaperSourceFileUpdateRequest(path="paper.md", content="Later author edit.")
    )
    assert paper_history.reject_revision_candidate(db, user, candidate.id).status == "rejected"
    assert workspace.active_source_version_id == current.id
    with pytest.raises(HTTPException) as missing:
        paper_history.reject_revision_candidate(db, user, models.PaperRevisionCandidate().id)
    assert missing.value.status_code == 404


def test_accept_cannot_revive_a_rejected_candidate(history):
    """Keep rejection terminal through the existing candidate acceptance endpoint."""
    db, user, storage, _agent = history
    workspace, source = source_workspace(history)
    candidate = pending_source_candidate(history, source, status="rejected")
    storage.upload(
        candidate.patch_object_key,
        json.dumps(
            {
                "source_path": "paper.md",
                "original_text": "Original author text.",
                "replacement_text": "Rejected text.",
            }
        ).encode(),
    )
    objects_before = dict(storage.objects)
    with pytest.raises(HTTPException) as conflict:
        paper_service.accept_revision_candidate(db, user, candidate.id)
    assert conflict.value.status_code == 409
    assert candidate.status == "rejected"
    assert workspace.active_source_version_id == source.id
    assert storage.objects == objects_before


def test_reject_api_returns_candidate_status_and_hides_foreign_candidates(history):
    """Let the history UI reject a full-source candidate through authenticated HTTP."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.llm4ad import paper_history

    db, user, _storage, _agent = history
    _workspace, source = source_workspace(history, "proposal")
    candidate = pending_source_candidate(history, source)
    applied = pending_source_candidate(history, source, status="accepted")
    app = FastAPI()
    app.include_router(paper_history.router, prefix="/api/v1/llm4ad")
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_current_user] = lambda: user
    endpoint = f"/api/v1/llm4ad/papers/revision-candidates/{candidate.id}/reject"
    with TestClient(app) as client:
        response = client.post(endpoint)
        assert response.status_code == 200
        assert response.json() == {"candidate_id": str(candidate.id), "status": "rejected"}
        assert client.post(endpoint).json() == response.json()
        assert client.post(f"/api/v1/llm4ad/papers/revision-candidates/{applied.id}/reject").status_code == 409
        app.dependency_overrides[deps.get_current_user] = lambda: models.User(
            email="foreign@example.com", hashed_password="unused"
        )
        assert client.post(endpoint).status_code == 404


def test_publication_refreshes_stale_workspace_after_locking(history, tmp_path, monkeypatch):
    """Preserve regional queue updates committed before publication acquires its lock."""
    db, user, storage, agent = history
    workspace, source = source_workspace(history)
    run = models.PaperAgentRun(
        workspace_id=workspace.id,
        source_version_id=source.id,
        run_kind="ac_summary",
        status="running",
        request_payload={"auto_workflow": True, "source_content_hash": source.content_hash},
    )
    workspace.rebuttal_context = {"baseline": {"summary": "Original context"}}
    db.add(run)
    db.add(workspace)
    db.commit()
    source_id, run_id, workspace_id = source.id, run.id, workspace.id
    with Session(db.get_bind(), expire_on_commit=False) as stale_session:
        stale_workspace = stale_session.get(models.PaperWorkspace, workspace_id)
        assert "_paper_regions" not in stale_workspace.rebuttal_context
        snapshot = {
            "regions": {},
            "requests": [{"id": "queued-before-publication", "state": "queued"}],
            "review_versions": {},
        }
        storage.upload("regional-queue.json", json.dumps(snapshot).encode())
        workspace.rebuttal_context = {
            **workspace.rebuttal_context,
            "_paper_regions": {"object_key": "regional-queue.json", "revision": "concurrent-region-queue"},
        }
        workspace.proposal_stage_states = {"rebuttal_baseline": {"status": "needs_revision"}}
        db.add(workspace)
        db.commit()
        assert "_paper_regions" not in stale_workspace.rebuttal_context
        output = tmp_path / "stale-context-publication" / "output"
        output.mkdir(parents=True)
        (output / "result.json").write_text(json.dumps({"summary": "A chair summary.", "message": "Dear chair."}))
        monkeypatch.setattr(agent, "get_db_session", lambda: stale_session)
        assert agent._persist_output(run_id, user.id, output.parent) is False
    db.expire_all()
    preserved = db.get(models.PaperWorkspace, workspace_id)
    assert preserved.active_source_version_id == source_id
    assert preserved.rebuttal_context["_paper_regions"]["revision"] == "concurrent-region-queue"
    assert preserved.rebuttal_context["chair_message"] == "Dear chair."
    assert preserved.proposal_stage_states["rebuttal_baseline"]["status"] == "needs_revision"
    assert preserved.proposal_stage_states["ac_summary"]["status"] == "ready"
    assert json.loads(storage.download("regional-queue.json"))["requests"][0]["state"] == "queued"
