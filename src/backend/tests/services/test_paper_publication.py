"""End-to-end persistence checks for the lightweight research publication tool."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, create_engine

from app import models
from app.schemas.paper import PaperSourceFileUpdateRequest, PaperWorkspaceCreate
from app.schemas.paper_publication import ResearchRevisionPublication
from app.services import paper_publication, paper_revision_history, paper_service


class MemoryStorage:
    """Store original source bytes and complete revisions without network access."""

    def __init__(self):
        self.objects = {}

    def upload(self, key, data, **_kwargs):
        self.objects[key] = data.read() if hasattr(data, "read") else data

    def download(self, key, **_kwargs):
        return self.objects[key]

    def delete(self, key, **_kwargs):
        self.objects.pop(key, None)


@pytest.fixture
def publication(tmp_path, monkeypatch):
    """Provide production persistence helpers with isolated SQL and object storage."""
    engine = create_engine(f"sqlite:///{tmp_path / 'publication.sqlite'}")
    tables = [
        models.User,
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
    user = models.User(email="publication@example.com", hashed_password="unused")
    with Session(engine) as db:
        db.add(user)
        db.commit()
        yield db, user, storage
    engine.dispose()


def setup_workspace(publication, mode):
    """Start a real native conversation against one initial document."""
    db, user, _storage = publication
    workspace = paper_service.create_workspace(db, user, PaperWorkspaceCreate(title="Research", mode=mode))
    if workspace.active_source_version_id is None:
        paper_service._create_source_version(
            db, workspace, [("paper.md", b"Author paper")], change_summary="Uploaded paper"
        )
    run = models.PaperAgentRun(
        workspace_id=workspace.id,
        source_version_id=workspace.active_source_version_id,
        run_kind="research_workflow",
        session_id="native-session",
        request_payload={"native_runtime": "cloudcli", "auto_workflow": True},
    )
    db.add(run)
    db.commit()
    return workspace, run


def request_for(db, workspace, artifact=None):
    """Pin a new logical revision to the complete content currently visible."""
    return ResearchRevisionPublication(
        summary="Current research revision",
        idempotency_key=uuid.uuid4().hex,
        base_revision=paper_revision_history.current_state_hash(db, workspace),
        artifact=artifact,
    )


def test_publish_detects_all_files_without_stage_or_source_paths(publication):
    """A single agent can integrate many chapters without the old six-422 loop."""
    db, _user, storage = publication
    workspace, run = setup_workspace(publication, "proposal")
    source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    request = request_for(db, workspace)
    root = paper_service.paper_workspace_source_dir(workspace.user_id, workspace.id)
    (root / "proposal.md").write_text("# A complete draft\n\n$\\operatorname{GP}(x)$", encoding="utf-8")
    (root / "sections").mkdir(exist_ok=True)
    for index in range(15):
        (root / "sections" / f"{index}.md").write_text(f"## Chapter {index}\nDraft.", encoding="utf-8")
    result = paper_publication.publish_revision(db, workspace, run, request)
    assert result["success"] and result["source_committed"]
    current = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    assert current.id != source.id
    assert len([path for path in current.manifest if path.startswith("sections/")]) >= 15
    assert b"\\operatorname{GP}" in dict(paper_service._source_entries(current))["proposal.md"]
    assert source.object_key in storage.objects


def test_rebuttal_uses_one_artifact_and_preserves_exact_submission(publication):
    """Do not require repeated strategy/draft/compliance bodies or a baseline gate."""
    db, _user, _storage = publication
    workspace, run = setup_workspace(publication, "manuscript")
    text = "Dear Reviewer,\n\n  Exact authored text.\n"
    result = paper_publication.publish_revision(
        db,
        workspace,
        run,
        request_for(
            db,
            workspace,
            {
                "type": "rebuttal",
                "rendered_text": text,
                "open_placeholders": ["Please provide the additional experiment."],
            },
        ),
    )
    assert result["success"]
    assert workspace.rebuttal_context["rendered"]["text"] == text
    assert "strategy" not in workspace.rebuttal_context
    assert "draft" not in workspace.rebuttal_context
    assert "compliance" not in workspace.rebuttal_context


def test_positive_reviewer_reply_does_not_require_an_invented_concern(publication):
    """Display a grounded acknowledgement without manufacturing a weakness ID."""
    db, _user, _storage = publication
    workspace, run = setup_workspace(publication, "manuscript")
    request = request_for(
        db,
        workspace,
        {
            "type": "rebuttal",
            "rendered_text": "Thank you for your assessment.",
            "entries": [
                {
                    "id": "acknowledgement",
                    "reviewer_id": "reviewer-id",
                    "label": "M",
                    "title": "Acknowledgement",
                    "response": "Thank you for your assessment.",
                    "concern_ids": [],
                    "evidence_status": "source_grounded",
                }
            ],
        },
    )
    assert paper_publication.publish_revision(db, workspace, run, request)["success"]
    assert workspace.rebuttal_entries[0]["concern_ids"] == []


def test_manual_output_edit_invalidates_old_generation_without_source_change(publication):
    """The old source-only guard must not overwrite a newer author reply."""
    db, _user, storage = publication
    workspace, run = setup_workspace(publication, "manuscript")
    original_source_id = workspace.active_source_version_id
    request = request_for(db, workspace, {"type": "ac_summary", "message": "Older generated message"})
    workspace.rebuttal_context = {"chair_message": "New author edit"}
    db.add(workspace)
    db.commit()
    objects = dict(storage.objects)
    with pytest.raises(HTTPException) as conflict:
        paper_publication.publish_revision(db, workspace, run, request)
    assert conflict.value.status_code == 409
    assert workspace.active_source_version_id == original_source_id
    assert workspace.chair_message == "New author edit"
    assert storage.objects == objects


def test_cancelled_conversation_cannot_publish(publication):
    """Late tool completion after stopping the assistant cannot mutate results."""
    db, _user, _storage = publication
    workspace, run = setup_workspace(publication, "manuscript")
    request = request_for(db, workspace, {"type": "ac_summary", "message": "Late result"})
    run.status = "cancelled"
    db.add(run)
    db.commit()
    with pytest.raises(HTTPException) as conflict:
        paper_publication.publish_revision(db, workspace, run, request)
    assert conflict.value.status_code == 409
    assert workspace.chair_message is None


def test_publication_schema_exposes_typos_instead_of_ignoring_fields():
    """Agent guesses such as written_files should produce an exact field error."""
    with pytest.raises(ValidationError) as invalid:
        ResearchRevisionPublication(
            summary="Draft",
            idempotency_key="one",
            base_revision="base",
            artifact={"type": "proposal", "written_files": ["proposal.md"]},
        )
    assert "written_files" in str(invalid.value)


def test_retry_is_durable_without_the_redis_response_cache(publication):
    """An uncertain response can be retried without creating a second revision."""
    db, _user, storage = publication
    workspace, run = setup_workspace(publication, "manuscript")
    request = request_for(db, workspace, {"type": "ac_summary", "message": "Exact message"})
    first = paper_publication.publish_revision(db, workspace, run, request)
    objects = dict(storage.objects)
    db.expire_all()
    assert paper_publication.publish_revision(db, workspace, run, request) == first
    assert storage.objects == objects
    changed = request.model_copy(update={"summary": "Changed content using the old key"})
    with pytest.raises(HTTPException) as conflict:
        paper_publication.publish_revision(db, workspace, run, changed)
    assert conflict.value.status_code == 409


def test_agent_can_continue_but_cannot_replace_a_manually_edited_document(publication):
    """Automatic revision continues until it would replace actual author changes."""
    db, user, _storage = publication
    workspace, run = setup_workspace(publication, "proposal")
    root = paper_service.paper_workspace_source_dir(workspace.user_id, workspace.id)
    (root / "proposal.md").write_text("# Initial researched draft", encoding="utf-8")
    first = paper_publication.publish_revision(db, workspace, run, request_for(db, workspace))
    assert first["source_committed"]
    (root / "proposal.md").write_text("# Agent refined its own draft", encoding="utf-8")
    second = paper_publication.publish_revision(db, workspace, run, request_for(db, workspace))
    assert second["source_committed"] and not second["requires_author_approval"]
    paper_service.update_source_file(
        db,
        user,
        workspace.active_source_version_id,
        PaperSourceFileUpdateRequest(path="proposal.md", content="# Exact author wording"),
    )
    request = request_for(db, workspace)
    (root / "proposal.md").write_text("# Agent would overwrite the author", encoding="utf-8")
    pending = paper_publication.publish_revision(db, workspace, run, request)
    assert pending["requires_author_approval"] and not pending["source_committed"]
    assert (root / "proposal.md").read_text() == "# Exact author wording"


def test_chat_review_intake_keeps_original_and_reuses_authoritative_identity(publication, monkeypatch):
    """Pasted review evidence becomes an editable original, not an invented UUID."""
    from app.api.llm4ad import paper_runtime

    db, user, _storage = publication
    workspace, run = setup_workspace(publication, "manuscript")
    monkeypatch.setattr(paper_runtime, "engine", db.get_bind())
    app = FastAPI()
    app.include_router(paper_runtime.router)
    original = "# Review\n\n" + "The reviewer asks for a concrete comparison.\n" * 100
    payload = {
        "workspace_id": str(workspace.id),
        "run_id": str(run.id),
        "reviewer_label": "Reviewer A",
        "content": original,
    }
    headers = {"x-llm4ad-runtime-token": paper_runtime.paper_workspace_runtime_token(workspace.id)}
    with TestClient(app) as client:
        denied = client.post("/papers/runtime/reviews", json=payload)
        assert denied.status_code == 401
        first = client.post("/papers/runtime/reviews", json=payload, headers=headers)
        assert first.status_code == 200
        second = client.post("/papers/runtime/reviews", json=payload, headers=headers)
        assert second.json() == first.json()
    review_id = uuid.UUID(first.json()["review_id"])
    review = db.get(models.PaperReview, review_id)
    assert review.workspace_id == workspace.id
    assert review.source_system == "chat"
    assert paper_service.get_reviewer_feedback(db, user, review_id).content == original.strip()
