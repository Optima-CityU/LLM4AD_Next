"""Isolated regional editing regressions using SQLite and immutable memory storage."""

from __future__ import annotations

import copy
import json
import uuid
from urllib.parse import quote

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from app import models
from app.api import deps
from app.api.llm4ad import paper_regions as routes
from app.schemas import paper as paper_schemas
from app.schemas import paper_regions as schemas
from app.services import paper_regions, paper_service


class MemoryStorage:
    """Keep immutable artifact bytes without using any network services."""

    def __init__(self):
        self.objects = {}
        self.fail_snapshots = False

    def upload(self, key, data, **_kwargs):
        """Store exact bytes, optionally failing regional snapshot publication."""
        if self.fail_snapshots and "/snapshots/" in key:
            raise OSError("Storage unavailable")
        self.objects[key] = data

    def download(self, key):
        """Read exact bytes from the isolated store."""
        return self.objects[key]

    def delete(self, key):
        """Remove a failed unreferenced snapshot."""
        self.objects.pop(key, None)


@pytest.fixture
def regions(tmp_path, monkeypatch):
    """Provide a real isolated session, owner, review, and canonical text blocks."""
    engine = create_engine(f"sqlite:///{tmp_path / 'regions.sqlite'}", connect_args={"check_same_thread": False})
    tables = [
        models.PaperWorkspace,
        models.PaperSourceVersion,
        models.PaperReview,
        models.PaperEvaluationMetric,
        models.PaperAgentRun,
        models.PaperBoundarySnapshot,
        models.PaperOptimizationTarget,
    ]
    models.PaperWorkspace.metadata.create_all(engine, tables=[table.__table__ for table in tables])
    storage = MemoryStorage()
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    user = models.User(email="regions@example.com", hashed_password="unused")
    with Session(engine) as db:
        workspace = models.PaperWorkspace(user_id=user.id, title="Regions", mode="manuscript")
        db.add(workspace)
        db.flush()
        source = models.PaperSourceVersion(
            workspace_id=workspace.id,
            version=1,
            source_kind="markdown",
            filename="paper.md",
            object_key="paper.md",
            content_hash="original",
            content_size=9,
            manifest=["paper.md"],
        )
        storage.upload("paper.md", b"Original.")
        db.add(source)
        db.flush()
        review = models.PaperReview(
            workspace_id=workspace.id,
            source_version_id=source.id,
            reviewer_label="Reviewer A",
            object_key="review.md",
            content_hash="review-original",
        )
        storage.upload("review.md", b"## Original review\r\n\r\nMissing comparison.\r\n")
        workspace.active_source_version_id = source.id
        reviewer = str(review.id)
        workspace.rebuttal_context = {
            "baseline": {"summary": "Baseline summary"},
            "intake": {"summary": "Intake", "paper_summary": "Paper summary", "open_questions": ["Confirm the data"]},
            "analysis": {
                "summary": "Analysis",
                "concerns": [
                    {
                        "id": "r1-w1",
                        "reviewer_id": reviewer,
                        "concern": "Missing comparison.",
                        "answer_source": "Table 1",
                        "draft_move": "Explain results",
                        "source_refs": [{"review_id": reviewer, "quote": "Missing comparison."}],
                    }
                ],
                "reviewer_cards": [
                    {
                        "reviewer_id": reviewer,
                        "attitude": "Skeptical",
                        "sentiment": "mixed",
                        "movability": "swing",
                        "primary_concerns": ["comparison"],
                    }
                ],
            },
            "rendered": {
                "text": "Original full rebuttal",
                "global_response": {"title": "Shared", "response": "Global response"},
                "findings": ["Check the numbers"],
                "open_placeholders": ["Author must confirm sample size"],
            },
            "chair_message": "Dear chair",
        }
        workspace.rebuttal_entries = [
            {
                "id": "entry-1",
                "reviewer_id": reviewer,
                "title": "Comparison",
                "response": "A😀B old text old text C",
                "source_refs": ["paper.md#table-1"],
            }
        ]
        db.add(workspace)
        db.add(review)
        db.commit()
        yield db, user, workspace, review, storage, engine
    engine.dispose()


def region(regions, region_id="rebuttal:entry-1:response"):
    """Find one current stable region through the production snapshot service."""
    db, user, workspace, *_ = regions
    return next(item for item in paper_regions.get_snapshot(db, user, workspace.id).regions if item.id == region_id)


def queue(regions, target=None, **kwargs):
    """Create one real durable scoped request against the current revision."""
    db, user, workspace, *_ = regions
    target = target or region(regions)
    payload = {
        "base_revision": target.revision,
        "instruction": "Clarify the answer",
        "idempotency_key": str(uuid.uuid4()),
        **kwargs,
    }
    return paper_regions.create_request(db, user, workspace.id, target.id, schemas.PaperRegionRequestCreate(**payload))


def test_editing_shared_response_updates_its_exact_submission_passage(regions):
    """A global response edit stays consistent with the saved copy-ready text."""
    db, user, workspace, *_ = regions
    workspace.rebuttal_context = {
        **workspace.rebuttal_context,
        "rendered": {
            **workspace.rebuttal_context["rendered"],
            "text": "Opening.\n\nGlobal response\n\nReviewer-specific answer.",
        },
    }
    db.add(workspace)
    db.commit()
    snapshot = paper_regions.get_snapshot(db, user, workspace.id)
    target = next(item for item in snapshot.regions if item.id == "rebuttal:global:response")
    paper_regions.patch_region(
        db, user, workspace.id, target.id,
        schemas.PaperRegionPatch(base_revision=target.revision, content="Edited shared response"),
    )
    assert workspace.rebuttal_context["rendered"]["global_response"]["response"] == "Edited shared response"
    assert workspace.rebuttal_context["rendered"]["text"] == "Opening.\n\nEdited shared response\n\nReviewer-specific answer."


def test_snapshot_covers_all_approved_text_and_persists_history(regions):
    """Expose all textual panels and persist drafts across independent sessions."""
    db, user, workspace, _review, storage, engine = regions
    snapshot = paper_regions.get_snapshot(db, user, workspace.id)
    kinds = {item.kind for item in snapshot.regions}
    assert {
        "review_original",
        "baseline_summary",
        "baseline_concern",
        "reviewer_analysis",
        "rebuttal_entry",
        "rebuttal_global",
        "author_needed_issue",
        "chair_message",
    } <= kinds
    request = queue(regions)
    paper_regions.propose_candidate(db, user, workspace.id, request.id, "A candidate", "Reason")
    with Session(engine) as fresh:
        restored = paper_regions.get_snapshot(fresh, user, workspace.id)
    assert restored.requests[0].replacement_text == "A candidate"
    assert restored.requests[0].state == "candidate"
    assert restored.regions == snapshot.regions
    db.refresh(workspace)
    pointer = workspace.rebuttal_context["_paper_regions"]
    assert set(pointer) == {"object_key", "revision"}
    assert pointer["object_key"] in storage.objects
    mirror = paper_service.paper_workspace_root(user.id, workspace.id) / ".research" / "regions"
    assert (mirror / f"{pointer['revision']}.json").is_file()


def test_dispatch_and_read_are_idempotent_and_context_survives_reconnect(regions):
    """Retain queued, dispatched, and processing prompts without a background engine."""
    db, user, workspace, *_ = regions
    request = queue(regions)
    assert paper_regions.workflow_context(db, user, workspace.id)["requests"][0]["state"] == "queued"
    dispatched = paper_regions.dispatch_request(db, user, workspace.id, request.id)
    assert dispatched.state == "dispatched"
    assert paper_regions.dispatch_request(db, user, workspace.id, request.id) == dispatched
    assert paper_regions.workflow_context(db, user, workspace.id)["requests"][0]["state"] == "dispatched"
    processing = paper_regions.read_request(db, user, workspace.id, request.id)
    assert processing.state == "processing"
    assert paper_regions.read_request(db, user, workspace.id, request.id) == processing
    assert paper_regions.dispatch_request(db, user, workspace.id, request.id).state == "processing"
    assert paper_regions.workflow_context(db, user, workspace.id)["requests"][0]["state"] == "processing"
    assert "read_region_request(request_id)" in processing.prompt
    assert "propose_region_change(request_id, replacement_text, reason)" in processing.prompt
    assert str(request.id) in processing.prompt
    assert "Dear chair" not in processing.prompt


def test_create_request_retries_are_idempotent_even_after_target_changes(regions):
    """Deduplicate browser retries while rejecting reuse with different instructions."""
    db, user, workspace, *_ = regions
    target = region(regions)
    request = queue(regions, target, idempotency_key="same-key")
    repeated = queue(regions, target, idempotency_key="same-key")
    assert repeated.id == request.id
    paper_regions.patch_region(
        db,
        user,
        workspace.id,
        target.id,
        schemas.PaperRegionPatch(base_revision=target.revision, content="Author edit"),
    )
    assert queue(regions, target, idempotency_key="same-key").state == "stale"
    with pytest.raises(HTTPException) as conflict:
        queue(regions, target, idempotency_key="same-key", instruction="Different request")
    assert conflict.value.status_code == 409


def test_utf16_selection_applies_only_the_exact_occurrence(regions):
    """Respect browser offsets after emoji and replace only the selected repeat."""
    db, user, workspace, *_ = regions
    target = region(regions)
    before_context = copy.deepcopy(workspace.rebuttal_context)
    request = queue(regions, target, selection={"start": 14, "end": 22, "original_text": "old text"})
    assert request.original_text == "old text"
    candidate = paper_regions.propose_candidate(db, user, workspace.id, request.id, "new text", "Clarified")
    assert candidate.state == "candidate"
    assert region(regions).content == target.content
    applied = paper_regions.apply_request(
        db, user, workspace.id, request.id, schemas.PaperRegionApply(base_revision=target.revision)
    )
    assert applied.state == "applied"
    assert region(regions).content == "A😀B old text new text C"
    db.refresh(workspace)
    assert workspace.rebuttal_context["chair_message"] == before_context["chair_message"]
    assert workspace.rebuttal_context["rendered"] == before_context["rendered"]
    assert (
        paper_regions.apply_request(
            db, user, workspace.id, request.id, schemas.PaperRegionApply(base_revision=target.revision)
        )
        == applied
    )


@pytest.mark.parametrize(
    "selection",
    [
        {"start": 1, "end": 2, "original_text": "😀"},
        {"start": 3, "end": 100, "original_text": "old text"},
        {"start": 5, "end": 13, "original_text": "wrong"},
    ],
)
def test_selection_conflicts_never_queue_or_write(regions, selection):
    """Reject split surrogate pairs, out-of-range offsets, and mismatched text."""
    with pytest.raises(HTTPException) as conflict:
        queue(regions, selection=selection)
    assert conflict.value.status_code == 409
    db, user, workspace, *_ = regions
    assert paper_regions.get_snapshot(db, user, workspace.id).requests == []


def test_author_edit_invalidates_candidate_and_never_overwrites_author(regions):
    """Keep a candidate stale after an intervening edit, even if text is restored."""
    db, user, workspace, *_ = regions
    target = region(regions)
    request = queue(regions, target)
    paper_regions.propose_candidate(db, user, workspace.id, request.id, "Agent candidate")
    changed = paper_regions.patch_region(
        db,
        user,
        workspace.id,
        target.id,
        schemas.PaperRegionPatch(base_revision=target.revision, content="Author edit"),
    )
    restored = paper_regions.patch_region(
        db,
        user,
        workspace.id,
        target.id,
        schemas.PaperRegionPatch(base_revision=changed.revision, content=target.content),
    )
    assert restored.revision != target.revision
    with pytest.raises(HTTPException) as conflict:
        paper_regions.apply_request(
            db, user, workspace.id, request.id, schemas.PaperRegionApply(base_revision=target.revision)
        )
    assert conflict.value.status_code == 409
    assert region(regions).content == target.content
    assert paper_regions.get_snapshot(db, user, workspace.id).requests[0].state == "stale"


def test_two_candidates_for_one_base_cannot_both_apply(regions):
    """Serialize approval so a second candidate cannot overwrite the first."""
    db, user, workspace, *_ = regions
    target = region(regions)
    first, second = queue(regions, target), queue(regions, target)
    for request, text in ((first, "First approved"), (second, "Second overwrite")):
        paper_regions.propose_candidate(db, user, workspace.id, request.id, text)
    paper_regions.apply_request(
        db, user, workspace.id, first.id, schemas.PaperRegionApply(base_revision=target.revision)
    )
    with pytest.raises(HTTPException) as conflict:
        paper_regions.apply_request(
            db, user, workspace.id, second.id, schemas.PaperRegionApply(base_revision=target.revision)
        )
    assert conflict.value.status_code == 409
    assert region(regions).content == "First approved"


def test_other_region_edits_do_not_invalidate_candidate_during_conversation(regions):
    """Allow independent edits while the existing CloudCLI workflow is active."""
    db, user, workspace, *_ = regions
    target = region(regions)
    request = queue(regions, target)
    paper_regions.propose_candidate(db, user, workspace.id, request.id, "Scoped replacement")
    db.add(models.PaperAgentRun(workspace_id=workspace.id, run_kind="research_workflow", status="running"))
    db.commit()
    chair = region(regions, "chair:message")
    paper_regions.patch_region(
        db,
        user,
        workspace.id,
        chair.id,
        schemas.PaperRegionPatch(base_revision=chair.revision, content="New chair message"),
    )
    applied = paper_regions.apply_request(
        db, user, workspace.id, request.id, schemas.PaperRegionApply(base_revision=target.revision)
    )
    assert applied.state == "applied"
    assert region(regions, "chair:message").content == "New chair message"


def test_reject_retains_diff_without_changing_content(regions):
    """Retain rejected drafts across reloads and refuse subsequent publication."""
    db, user, workspace, *_ = regions
    target = region(regions)
    request = queue(regions, target)
    paper_regions.propose_candidate(db, user, workspace.id, request.id, "Rejected draft")
    rejected = paper_regions.reject_request(db, user, workspace.id, request.id)
    assert rejected.state == "rejected"
    assert rejected.replacement_text == "Rejected draft"
    assert paper_regions.reject_request(db, user, workspace.id, request.id) == rejected
    with pytest.raises(HTTPException) as conflict:
        paper_regions.propose_candidate(db, user, workspace.id, request.id, "Late candidate")
    assert conflict.value.status_code == 409
    assert region(regions).content == target.content


def test_candidate_retry_cannot_change_the_diff_awaiting_author_approval(regions):
    """Freeze a published diff because approval carries only its base revision."""
    db, user, workspace, *_ = regions
    request = queue(regions)
    candidate = paper_regions.propose_candidate(
        db, user, workspace.id, request.id, "Reviewed candidate", "First reason"
    )
    repeated = paper_regions.propose_candidate(db, user, workspace.id, request.id, "Reviewed candidate", "Retry reason")
    assert repeated == candidate
    with pytest.raises(HTTPException) as conflict:
        paper_regions.propose_candidate(db, user, workspace.id, request.id, "Unreviewed overwrite")
    assert conflict.value.status_code == 409
    assert paper_regions.get_snapshot(db, user, workspace.id).requests[0].replacement_text == "Reviewed candidate"


def test_evidence_verifies_exact_quotes_and_preserves_raw_review_versions(regions):
    """Keep clickable CRLF originals after reviewers' editable text changes."""
    db, user, workspace, review, storage, _engine = regions
    target = region(regions, f"review:{review.id}")
    ref = region(regions, "concern:r1-w1:concern").source_refs[0]
    assert ref.status == "verified"
    original = paper_regions.get_review_source(db, user, workspace.id, review.id, ref.version)
    assert original.encode() == storage.objects["review.md"]
    changed = paper_regions.patch_region(
        db,
        user,
        workspace.id,
        target.id,
        schemas.PaperRegionPatch(base_revision=target.revision, content="Edited report"),
    )
    assert changed.id == target.id
    assert changed.revision != target.revision
    assert storage.objects["review.md"] == original.encode()
    assert paper_regions.get_review_source(db, user, workspace.id, review.id, ref.version) == original
    state = paper_regions._load(workspace)
    refs = paper_regions._resolve_refs(
        workspace,
        state,
        {str(review.id): review},
        [
            {"review_id": str(review.id), "version": ref.version, "quote": "Missing comparison."},
            {"review_id": str(review.id), "version": ref.version, "quote": "Invented exact quote"},
            "paper.md#legacy",
        ],
    )
    assert [item.status for item in refs] == ["verified", "unverified", "unverified"]


def test_foreign_reviews_and_workspace_requests_are_not_resolved(regions):
    """Prevent cross-workspace ownership leaks through evidence and request IDs."""
    db, user, workspace, _review, _storage, _engine = regions
    other_user = models.User(email="other@example.com", hashed_password="unused")
    with pytest.raises(HTTPException) as denied:
        paper_regions.get_snapshot(db, other_user, workspace.id)
    assert denied.value.status_code == 404
    foreign_id = uuid.uuid4()
    ref = paper_regions._resolve_refs(
        workspace,
        paper_regions._load(workspace),
        {},
        [{"review_id": str(foreign_id), "quote": "Private report", "path": "/private/foreign-review"}],
    )[0]
    assert ref.status == "unverified"
    assert ref.path is None
    with pytest.raises(HTTPException) as missing:
        paper_regions.read_request(db, user, workspace.id, foreign_id)
    assert missing.value.status_code == 404


def test_legacy_review_updates_preserve_original_and_invalidate_candidates(regions):
    """Archive through the real legacy writer, including change-and-restore races."""
    db, user, workspace, review, storage, _engine = regions
    target = region(regions, f"review:{review.id}")
    initial_ref = target.source_refs[0]
    request = queue(regions, target)
    paper_regions.propose_candidate(db, user, workspace.id, request.id, "Obsolete candidate")
    paper_service.update_reviewer_feedback(
        db,
        user,
        review.id,
        paper_schemas.PaperReviewUpdate(reviewer_label="Reviewer A", content="Temporary author edit"),
    )
    paper_service.update_reviewer_feedback(
        db, user, review.id, paper_schemas.PaperReviewUpdate(reviewer_label="Reviewer A", content=target.content)
    )
    current = paper_regions.get_snapshot(db, user, workspace.id)
    assert current.requests[0].state == "stale"
    assert (
        paper_regions.get_review_source(db, user, workspace.id, review.id, initial_ref.version).encode()
        == storage.objects["review.md"]
    )
    assert region(regions, "concern:r1-w1:concern").source_refs[0].version == initial_ref.version


def test_legacy_preservation_hook_joins_the_existing_transaction(regions):
    """Stage the original without committing any caller-owned database changes."""
    db, _user, workspace, review, storage, _engine = regions
    paper_regions.preserve_review_original(db, workspace, review)
    staged_key = workspace.rebuttal_context["_paper_regions"]["object_key"]
    assert staged_key in storage.objects
    db.rollback()
    db.refresh(workspace)
    assert "_paper_regions" not in workspace.rebuttal_context
    assert storage.objects["review.md"].startswith(b"## Original review\r\n")


def test_invalid_legacy_reference_is_unverified_without_blocking_edit(regions):
    """Accept incomplete historical evidence while refusing unowned source resolution."""
    db, _user, workspace, *_ = regions
    context = copy.deepcopy(workspace.rebuttal_context)
    context["analysis"]["concerns"][0]["source_refs"] = [
        {"review_id": "old-review-name", "version": 1, "quote": "Legacy quote", "path": "/unowned"},
        {"review_id": str(uuid.uuid4()), "version": "missing", "quote": "Missing quote"},
    ]
    workspace.rebuttal_context = context
    db.add(workspace)
    db.commit()
    target = region(regions, "concern:r1-w1:concern")
    assert all(ref.status == "unverified" for ref in target.source_refs)
    assert all(ref.path is None for ref in target.source_refs)
    assert queue(regions, target).state == "queued"


def test_missing_archived_legacy_evidence_is_unverified(regions):
    """Keep old missing object references from blocking the regional workflow."""
    db, _user, workspace, review, *_ = regions
    region(regions)
    state = paper_regions._load(workspace)
    state["review_versions"][str(review.id)]["missing-version"] = {"object_key": "missing-original.md"}
    refs = paper_regions._resolve_refs(
        workspace,
        state,
        {str(review.id): review},
        [{"review_id": str(review.id), "version": "missing-version", "quote": "Missing comparison."}],
    )
    assert refs[0].status == "unverified"
    assert refs[0].path is None


def test_snapshot_storage_failure_rolls_back_canonical_edit(regions):
    """Never commit a canonical edit if its durable revision snapshot failed."""
    db, user, workspace, _review, storage, _engine = regions
    target = region(regions)
    pointer = copy.deepcopy(workspace.rebuttal_context["_paper_regions"])
    storage.fail_snapshots = True
    with pytest.raises(OSError):
        paper_regions.patch_region(
            db,
            user,
            workspace.id,
            target.id,
            schemas.PaperRegionPatch(base_revision=target.revision, content="Lost edit"),
        )
    db.refresh(workspace)
    assert workspace.rebuttal_entries[0]["response"] == target.content
    assert workspace.rebuttal_context["_paper_regions"] == pointer


def test_stale_manual_base_does_not_write_any_artifact(regions):
    """Return 409 without changing the snapshot or canonical target."""
    db, user, workspace, _review, storage, _engine = regions
    target = region(regions)
    before = dict(storage.objects)
    with pytest.raises(HTTPException) as conflict:
        paper_regions.patch_region(
            db, user, workspace.id, target.id, schemas.PaperRegionPatch(base_revision="stale", content="Overwrite")
        )
    assert conflict.value.status_code == 409
    assert storage.objects == before


def test_runtime_operations_return_serializable_precise_requests(regions):
    """Exercise the authenticated bridge's exact context, read, and propose contract."""
    db, user, workspace, *_ = regions
    request = queue(regions)
    snapshot = paper_regions.runtime_operation(db, user, workspace.id, "context")
    assert snapshot["regions"] and snapshot["requests"][0]["id"] == str(request.id)
    claimed = paper_regions.runtime_operation(db, user, workspace.id, "read", request_id=str(request.id))
    assert claimed["state"] == "processing"
    proposed = paper_regions.runtime_operation(
        db, user, workspace.id, "propose", request_id=request.id, replacement_text="", reason="Remove obsolete text"
    )
    assert proposed["state"] == "candidate"
    assert proposed["replacement_text"] == ""
    json.dumps(proposed)
    with pytest.raises(HTTPException) as invalid:
        paper_regions.runtime_operation(db, user, workspace.id, "propose", request_id="invalid")
    assert invalid.value.status_code == 422


def test_region_routes_auth_dispatch_and_no_public_candidate(regions):
    """Expose the public contract while leaving candidate publication internal."""
    db, user, workspace, *_ = regions
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/llm4ad")
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_current_user] = lambda: user
    root = f"/api/v1/llm4ad/papers/workspaces/{workspace.id}"
    with TestClient(app) as client:
        response = client.get(f"{root}/regions")
        assert response.status_code == 200
        target = next(item for item in response.json()["regions"] if item["id"] == "chair:message")
        request = client.post(
            f"{root}/regions/{target['id']}/requests",
            json={"base_revision": target["revision"], "instruction": "Shorten", "idempotency_key": "route-key"},
        )
        assert request.status_code == 200
        request_id = request.json()["id"]
        assert client.post(f"{root}/region-requests/{request_id}/dispatch").json()["state"] == "dispatched"
        assert (
            client.post(
                f"{root}/region-requests/{request_id}/candidate", json={"replacement_text": "Unsafe"}
            ).status_code
            == 404
        )
        assert client.post(f"{root}/region-requests/{request_id}/reject").json()["state"] == "rejected"
        assert "PaperRegionSnapshot" in app.openapi()["components"]["schemas"]


def test_proposal_region_edit_creates_linked_source_and_keeps_other_section(regions):
    """Apply proposal edits through immutable source versions without global blocking."""
    db, user, workspace, _review, storage, _engine = regions
    workspace.mode = "proposal"
    db.add(workspace)
    db.commit()
    source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    original_key = source.object_key
    source_id = source.id
    next_source = paper_service._persist_source_entries(
        db,
        workspace,
        source,
        [("sections/01.typ", b"Original section."), ("sections/02.typ", b"Unrelated section.")],
        change_summary="Sections",
    )
    snapshot = paper_regions.get_snapshot(db, user, workspace.id)
    target = next(item for item in snapshot.regions if item.title == "sections/01.typ")
    unrelated = next(item for item in snapshot.regions if item.title == "sections/02.typ")
    assert "/" in target.id
    queued = queue(regions, target)
    paper_regions.propose_candidate(db, user, workspace.id, queued.id, "Revised section.")
    applied = paper_regions.apply_request(
        db, user, workspace.id, queued.id, schemas.PaperRegionApply(base_revision=target.revision)
    )
    assert applied.state == "applied"
    updated = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    assert updated.parent_version_id == next_source.id
    assert updated.id != next_source.id
    entries = dict(paper_service._source_entries(updated))
    assert entries["sections/01.typ"] == b"Revised section."
    assert entries["sections/02.typ"] == b"Unrelated section."
    assert storage.objects[original_key] == b"Original."
    assert db.get(models.PaperSourceVersion, source_id) is not None
    current = paper_regions.get_snapshot(db, user, workspace.id)
    assert next(item for item in current.regions if item.id == unrelated.id).revision == unrelated.revision
    assert len(db.exec(select(models.PaperSourceVersion)).all()) == 3


def test_no_quote_analysis_is_unverified_without_manufacturing_or_truncating_text(regions):
    """Link full originals without treating an inferred excerpt as claim support."""
    db, _user, workspace, review, storage, _engine = regions
    full_review = "Original review. " * 500
    storage.objects[review.object_key] = full_review.encode()
    context = copy.deepcopy(workspace.rebuttal_context)
    concern = context["analysis"]["concerns"][0]
    concern["concern"] = "An unsupported claim that never appeared in the review"
    concern["source_refs"] = [{"review_id": str(review.id)}]
    workspace.rebuttal_context = context
    db.add(workspace)
    db.commit()
    analysis = region(regions, "concern:r1-w1:concern")
    assert analysis.source_refs[0].status == "unverified"
    assert analysis.source_refs[0].quote is None
    assert analysis.source_refs[0].path
    original = region(regions, f"review:{review.id}")
    assert original.content == full_review
    assert original.source_refs[0].quote is None
    assert original.source_refs[0].status == "current"
    assert (
        paper_regions.get_review_source(db, _user, workspace.id, review.id, original.source_refs[0].version)
        == full_review
    )
    state = paper_regions._load(workspace)
    implicit = paper_regions._resolve_refs(workspace, state, {str(review.id): review}, [], review.id)
    assert implicit[0].quote is None
    assert implicit[0].status == "unverified"


def test_text_list_ids_survive_insertion_and_reordering(regions):
    """Keep an approved request attached to its own issue after adjacent changes."""
    db, user, workspace, *_ = regions
    snapshot = paper_regions.get_snapshot(db, user, workspace.id)
    target = next(item for item in snapshot.regions if item.content == "Confirm the data")
    request = queue(regions, target)
    paper_regions.propose_candidate(db, user, workspace.id, request.id, "Data confirmed")
    context = copy.deepcopy(workspace.rebuttal_context)
    context["intake"]["open_questions"].insert(0, "New independent question")
    workspace.rebuttal_context = context
    db.add(workspace)
    db.commit()
    current = region(regions, target.id)
    assert current.content == target.content
    assert current.revision == target.revision
    paper_regions.apply_request(
        db, user, workspace.id, request.id, schemas.PaperRegionApply(base_revision=target.revision)
    )
    db.refresh(workspace)
    assert workspace.rebuttal_context["intake"]["open_questions"] == ["New independent question", "Data confirmed"]
    assert region(regions, target.id).content == "Data confirmed"


def test_proposal_context_and_stage_text_are_independently_editable(regions):
    """Include proposal summaries, findings, and shared context in regional editing."""
    db, user, workspace, *_ = regions
    workspace.mode = "proposal"
    workspace.proposal_foundation = {"blocks": [{"key": "scope", "title": "Scope", "content": "Original scope"}]}
    workspace.proposal_stage_states = {"methods": {"summary": "Method summary", "findings": ["Method finding"]}}
    db.add(workspace)
    db.commit()
    target = region(regions, "proposal-context:scope:content")
    paper_regions.patch_region(
        db,
        user,
        workspace.id,
        target.id,
        schemas.PaperRegionPatch(base_revision=target.revision, content="Revised scope"),
    )
    summary = region(regions, "proposal-stage:methods:summary")
    paper_regions.patch_region(
        db,
        user,
        workspace.id,
        summary.id,
        schemas.PaperRegionPatch(base_revision=summary.revision, content="Revised summary"),
    )
    db.refresh(workspace)
    assert workspace.proposal_foundation["blocks"][0]["content"] == "Revised scope"
    assert workspace.proposal_stage_states["methods"]["summary"] == "Revised summary"
    assert workspace.proposal_stage_states["methods"]["findings"] == ["Method finding"]


def test_encoded_proposal_source_path_matches_patch_and_request_routes(regions):
    """Exercise ASGI decoding of encoded slashes against actual proposal regions."""
    db, user, workspace, *_ = regions
    workspace.mode = "proposal"
    db.add(workspace)
    db.commit()
    source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    paper_service._persist_source_entries(
        db,
        workspace,
        source,
        [("sections/nested/01.typ", b"Proposal section.")],
        change_summary="Proposal path fixture",
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/llm4ad")
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_current_user] = lambda: user
    root = f"/api/v1/llm4ad/papers/workspaces/{workspace.id}"
    with TestClient(app) as client:
        snapshot = client.get(f"{root}/regions").json()
        target = next(item for item in snapshot["regions"] if item["id"] == "proposal:sections/nested/01.typ")
        encoded = quote(target["id"], safe="")
        saved = client.patch(
            f"{root}/regions/{encoded}", json={"base_revision": target["revision"], "content": "Author revision."}
        )
        assert saved.status_code == 200
        queued = client.post(
            f"{root}/regions/{encoded}/requests",
            json={
                "base_revision": saved.json()["revision"],
                "instruction": "Refine the section",
                "idempotency_key": "encoded-path",
            },
        )
        assert queued.status_code == 200
        assert queued.json()["region_id"] == target["id"]


@pytest.mark.parametrize("action", ["patch", "apply"])
@pytest.mark.parametrize("kind", ["review", "entry", "context"])
def test_manuscript_mirrors_refresh_after_committed_regional_edit(regions, action, kind):
    """Refresh real runtime review files and full context after author approval."""
    db, user, workspace, review, storage, _engine = regions
    source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    second_review = models.PaperReview(
        workspace_id=workspace.id,
        source_version_id=source.id,
        reviewer_label="Reviewer B",
        object_key="second-review.md",
        content_hash="second-original",
    )
    storage.upload(second_review.object_key, b"Unchanged second review.")
    db.add(second_review)
    db.commit()
    paper_service.sync_paper_workspace_reviews(db, workspace, source)
    paper_service.sync_paper_workspace_rebuttal_context(workspace)
    target_id = {
        "review": f"review:{review.id}",
        "entry": "rebuttal:entry-1:response",
        "context": "baseline:analysis:summary",
    }[kind]
    target = region(regions, target_id)
    replacement = "Complete author update 😀\n" * 300
    if action == "patch":
        paper_regions.patch_region(
            db,
            user,
            workspace.id,
            target.id,
            schemas.PaperRegionPatch(base_revision=target.revision, content=replacement),
        )
    else:
        request = queue(regions, target)
        paper_regions.propose_candidate(db, user, workspace.id, request.id, replacement)
        paper_regions.apply_request(
            db, user, workspace.id, request.id, schemas.PaperRegionApply(base_revision=target.revision)
        )
    root = paper_service.paper_workspace_root(user.id, workspace.id) / ".research"
    db.refresh(workspace)
    index = json.loads((root / "reviews" / "index.json").read_text())
    assert {item["review_id"] for item in index["reviews"]} == {str(review.id), str(second_review.id)}
    assert (root / "reviews" / f"{second_review.id}.md").read_bytes() == b"Unchanged second review."
    expected_review = replacement.encode() if kind == "review" else storage.objects["review.md"]
    assert (root / "reviews" / f"{review.id}.md").read_bytes() == expected_review
    mirrored = json.loads((root / "rebuttal" / "context.json").read_text())
    assert mirrored == {
        "context": workspace.rebuttal_context,
        "entries": workspace.rebuttal_entries,
        "stage_states": workspace.proposal_stage_states,
    }


@pytest.mark.parametrize("failure", ["reviews", "context"])
@pytest.mark.parametrize("action", ["patch", "apply"])
def test_manuscript_mirror_failures_preserve_committed_edit(regions, monkeypatch, failure, action):
    """Attempt each mirror independently and retain commits after filesystem errors."""
    db, user, workspace, review, _storage, engine = regions
    target = region(regions, f"review:{review.id}")
    workspace_id, review_id = workspace.id, review.id
    replacement = "Durably saved reviewer text."
    calls = []
    sync_reviews = paper_service.sync_paper_workspace_reviews
    sync_context = paper_service.sync_paper_workspace_rebuttal_context

    def reviews_mirror(*args):
        calls.append("reviews")
        with Session(engine) as fresh:
            persisted = fresh.get(models.PaperReview, review_id)
            assert paper_service._storage().download(persisted.object_key).decode() == replacement
        if failure == "reviews":
            raise OSError("Cannot replace runtime review files")
        return sync_reviews(*args)

    def context_mirror(*args):
        calls.append("context")
        with Session(engine) as fresh:
            assert "_paper_regions" in fresh.get(models.PaperWorkspace, workspace_id).rebuttal_context
        if failure == "context":
            raise OSError("Cannot replace runtime rebuttal context")
        return sync_context(*args)

    monkeypatch.setattr(paper_service, "sync_paper_workspace_reviews", reviews_mirror)
    monkeypatch.setattr(paper_service, "sync_paper_workspace_rebuttal_context", context_mirror)
    if action == "patch":
        saved = paper_regions.patch_region(
            db,
            user,
            workspace_id,
            target.id,
            schemas.PaperRegionPatch(base_revision=target.revision, content=replacement),
        )
        assert saved.content == replacement
    else:
        request = queue(regions, target)
        paper_regions.propose_candidate(db, user, workspace_id, request.id, replacement)
        saved = paper_regions.apply_request(
            db, user, workspace_id, request.id, schemas.PaperRegionApply(base_revision=target.revision)
        )
        assert saved.state == "applied"
    assert calls == ["reviews", "context"]
    with Session(engine) as fresh:
        persisted = fresh.get(models.PaperReview, review_id)
        assert paper_service._storage().download(persisted.object_key).decode() == replacement
        if action == "apply":
            assert paper_regions.get_snapshot(fresh, user, workspace_id).requests[0].state == "applied"


def test_snapshot_polling_and_candidate_publication_do_not_refresh_manuscript_mirrors(regions, monkeypatch):
    """Reserve runtime file writes for committed regional edits and approvals."""
    db, user, workspace, *_ = regions
    calls = []
    monkeypatch.setattr(paper_service, "sync_paper_workspace_reviews", lambda *_args: calls.append("reviews"))
    monkeypatch.setattr(paper_service, "sync_paper_workspace_rebuttal_context", lambda *_args: calls.append("context"))
    target = region(regions)
    request = queue(regions, target)
    paper_regions.dispatch_request(db, user, workspace.id, request.id)
    paper_regions.read_request(db, user, workspace.id, request.id)
    paper_regions.propose_candidate(db, user, workspace.id, request.id, "Unapproved candidate")
    paper_regions.get_snapshot(db, user, workspace.id)
    paper_regions.get_snapshot(db, user, workspace.id)
    paper_regions.reject_request(db, user, workspace.id, request.id)
    assert calls == []


def test_legacy_review_update_refreshes_manuscript_mirrors(regions):
    """Use the same post-commit mirror for the existing review-edit endpoint."""
    db, user, workspace, review, *_ = regions
    updated = paper_service.update_reviewer_feedback(
        db,
        user,
        review.id,
        paper_schemas.PaperReviewUpdate(reviewer_label="Reviewer A updated", content="Legacy author edit."),
    )
    root = paper_service.paper_workspace_root(user.id, workspace.id) / ".research"
    assert (root / "reviews" / f"{review.id}.md").read_bytes() == b"Legacy author edit."
    index = json.loads((root / "reviews" / "index.json").read_text())
    assert index["reviews"][0]["display_label"] == updated.reviewer_label
    db.refresh(workspace)
    assert json.loads((root / "rebuttal" / "context.json").read_text()) == {
        "context": workspace.rebuttal_context,
        "entries": workspace.rebuttal_entries,
        "stage_states": workspace.proposal_stage_states,
    }
