"""Publish assistant revisions without imposing a scientific stage machine."""

from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import HTTPException
from sqlmodel import Session, select

from app import models
from app.schemas.paper_publication import (
    BaselineRevisionArtifact,
    ChairRevisionArtifact,
    ProposalRevisionArtifact,
    RebuttalRevisionArtifact,
    ResearchRevisionPublication,
)
from app.services import paper_service


def _overwrites_author_changes(db, workspace, source, entries) -> bool:
    """Protect manual changes without requiring approval for every agent revision."""
    previous = dict(paper_service._source_entries(source))
    replacement = dict(entries)
    changed = {path for path in previous.keys() | replacement.keys() if previous.get(path) != replacement.get(path)}
    if any(path.startswith("project_context/") for path in changed):
        return True
    latest = db.exec(
        select(models.PaperAgentRun)
        .where(
            models.PaperAgentRun.workspace_id == workspace.id,
            models.PaperAgentRun.run_kind.startswith("proposal_"),
            models.PaperAgentRun.status == "ready",
            models.PaperAgentRun.artifact_object_key.is_not(None),
        )
        .order_by(models.PaperAgentRun.created_time.desc())
    ).first()
    if latest is None or latest.source_version_id is None:
        return paper_service._proposal_changes_need_approval(workspace, list(previous.items()), entries)
    if latest.source_version_id == source.id:
        return False
    published_source = db.get(models.PaperSourceVersion, latest.source_version_id)
    if published_source is None or published_source.workspace_id != workspace.id:
        return paper_service._proposal_changes_need_approval(workspace, list(previous.items()), entries)
    published = dict(paper_service._source_entries(published_source))
    author_paths = {path for path in previous.keys() | published.keys() if previous.get(path) != published.get(path)}
    return bool(changed & author_paths)


def publish_revision(
    db: Session,
    workspace: models.PaperWorkspace,
    parent_run: models.PaperAgentRun,
    request: ResearchRevisionPublication,
) -> dict:
    """Save the actual edited files or a minimal display artifact atomically.

    Args:
        db: Session owning the publication transaction.
        workspace: Workspace authenticated by the runtime endpoint.
        parent_run: Existing assistant conversation that supplied the revision.
        request: Exact, discoverable publication payload and its observed base.

    Returns:
        Saved revision metadata, or an existing author-review candidate.
    """
    from app.services import paper_revision_history
    from app.tasks.paper_agent import _publish_proposal_source, _update_proposal_stage_state, _workspace_source_entries

    workspace = db.exec(
        select(models.PaperWorkspace)
        .where(models.PaperWorkspace.id == workspace.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if workspace.mode not in {"proposal", "manuscript"}:
        raise HTTPException(status_code=409, detail="Use the existing algorithm publication tools for this workspace")
    db.refresh(parent_run)
    if parent_run.status == models.PaperAgentRunStatus.CANCELLED.value:
        raise HTTPException(status_code=409, detail="The assistant conversation was cancelled")
    publication_id = uuid.uuid5(parent_run.id, f"research-revision:{request.idempotency_key}")
    fingerprint = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    prior = db.get(models.PaperAgentRun, publication_id)
    if prior is not None:
        recorded = prior.request_payload or {}
        if recorded.get("publication_fingerprint") != fingerprint:
            raise HTTPException(status_code=409, detail="This revision key was already used for different content")
        if prior.status == models.PaperAgentRunStatus.READY.value and recorded.get("publication_result"):
            return recorded["publication_result"]
        raise HTTPException(status_code=409, detail="This revision is already being saved")
    current_revision = paper_revision_history.current_state_hash(db, workspace)
    if current_revision != request.base_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Research content changed; compare the current author edits before saving",
                "revision": current_revision,
            },
        )
    artifact = request.artifact or ProposalRevisionArtifact()
    if isinstance(artifact, ProposalRevisionArtifact) != (workspace.mode == "proposal"):
        raise HTTPException(status_code=422, detail="The artifact type does not match the research workspace")
    source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
    if source is None:
        raise HTTPException(status_code=409, detail="Research source is unavailable")
    # Capture the before-image too, including legacy outputs that predate history.
    paper_revision_history.capture_revision(db, workspace, origin="checkpoint", summary="Before assistant revision")
    kind = {
        "proposal": models.PaperAgentRunKind.PROPOSAL_FORMATTING.value,
        "baseline": models.PaperAgentRunKind.REBUTTAL_BASELINE.value,
        "rebuttal": models.PaperAgentRunKind.AUTOREBUTTAL.value,
        "ac_summary": models.PaperAgentRunKind.AC_SUMMARY.value,
    }[artifact.type]
    run = models.PaperAgentRun(
        id=publication_id,
        workspace_id=workspace.id,
        source_version_id=source.id,
        run_kind=kind,
        status=models.PaperAgentRunStatus.RUNNING.value,
        session_id=parent_run.session_id,
        provider_id=parent_run.provider_id,
        model_name=parent_run.model_name,
        request_payload={
            "native_runtime": "cloudcli",
            "auto_workflow": True,
            "parent_run_id": str(parent_run.id),
            "base_revision": request.base_revision,
            "revision_key": request.idempotency_key,
            "publication_fingerprint": fingerprint,
            "source_content_hash": source.content_hash,
        },
    )
    db.add(run)
    db.flush()
    payload = {**artifact.model_dump(mode="json"), "summary": request.summary}
    key = f"paper/{workspace.user_id}/{workspace.id}/runs/{run.id}/result.json"
    storage = paper_service._storage()
    storage.upload(key, json.dumps(payload, ensure_ascii=False).encode(), content_type="application/json")
    committed = False
    try:
        if isinstance(artifact, ProposalRevisionArtifact):
            entries = _workspace_source_entries(
                paper_service.paper_workspace_source_dir(workspace.user_id, workspace.id)
            )
            entry_path = paper_service._validate_zip_path(
                artifact.entry_path or workspace.proposal_entry_path or "proposal.md"
            ).as_posix()
            if entry_path not in dict(entries):
                raise HTTPException(status_code=422, detail=f"Proposal entry does not exist: {entry_path}")
            if not entry_path.endswith((".typ", ".md", ".markdown")):
                raise HTTPException(status_code=422, detail="Select a Markdown or Typst proposal entry")
            # Saving a document is independent of whether it currently exports.
            committed = _publish_proposal_source(
                db,
                workspace,
                run,
                source,
                entries,
                artifact_key=key,
                summary=request.summary,
                context_patch=artifact.context_patch,
                entry_path=entry_path,
                findings=artifact.findings,
                approval_required=_overwrites_author_changes(db, workspace, source, entries),
            )
        elif isinstance(artifact, BaselineRevisionArtifact):
            context = dict(workspace.rebuttal_context or {})
            context.update(
                {
                    "intake": artifact.intake.model_dump(mode="json"),
                    "analysis": artifact.analysis.model_dump(mode="json"),
                    "baseline": {
                        "summary": request.summary,
                        "findings": artifact.findings,
                        "ready_for_generation": True,
                    },
                }
            )
            workspace.rebuttal_context = context
            _update_proposal_stage_state(workspace, run, source.id, summary=request.summary, findings=artifact.findings)
        elif isinstance(artifact, RebuttalRevisionArtifact):
            workspace.rebuttal_entries = [entry.model_dump(mode="json") for entry in artifact.entries]
            context = dict(workspace.rebuttal_context or {})
            context["rendered"] = {
                "output_format": artifact.output_format,
                "text": artifact.rendered_text,
                "global_response": (
                    artifact.global_response.model_dump(mode="json") if artifact.global_response else None
                ),
                "findings": artifact.findings,
                "open_placeholders": artifact.open_placeholders,
                "ready_for_submission": True,
            }
            # Legacy strategy/draft/compliance mirrors must not remain current.
            for obsolete in ("strategy", "draft", "compliance"):
                context.pop(obsolete, None)
            workspace.rebuttal_context = context
            _update_proposal_stage_state(workspace, run, source.id, summary=request.summary, findings=artifact.findings)
        elif isinstance(artifact, ChairRevisionArtifact):
            workspace.rebuttal_context = {**(workspace.rebuttal_context or {}), "chair_message": artifact.message}
            _update_proposal_stage_state(workspace, run, source.id, summary=request.summary)
        candidate_id = (run.request_payload or {}).get("pending_candidate_id")
        run.artifact_object_key = key
        run.status = models.PaperAgentRunStatus.READY.value
        run.stage = "awaiting_author_review" if candidate_id else "completed"
        run.progress = 100
        run.message = request.summary[:500]
        db.add(workspace)
        db.add(run)
        db.flush()
        paper_revision_history.capture_revision(db, workspace, origin="agent", summary=request.summary, run_id=run.id)
        revision = paper_revision_history.current_state_hash(db, workspace)
        result = {
            "success": True,
            "run_id": str(run.id),
            "revision": revision,
            "source_version_id": str(workspace.active_source_version_id),
            "source_committed": committed,
            "requires_author_approval": bool(candidate_id),
            "pending_candidate_id": candidate_id,
        }
        run.request_payload = {**(run.request_payload or {}), "publication_result": result}
        db.add(run)
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)
        raise
    if workspace.mode == "proposal":
        active_source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
        if active_source:
            paper_service._sync_paper_workspace_source_best_effort(workspace, active_source)
    else:
        from app.services.paper_regions import sync_manuscript_context_best_effort

        sync_manuscript_context_best_effort(db, workspace)
    return result
