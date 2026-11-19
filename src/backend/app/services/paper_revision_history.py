"""Immutable RustFS history for paper sources, outputs, and reviewer evidence."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import mimetypes
import uuid
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import event, inspect
from sqlmodel import Session, select

from app import models
from app.core.config import settings
from app.schemas import paper_revision_history as schemas
from app.services import paper_service

_POINTER = "_paper_history"
_CONTROLS = {
    _POINTER,
    "_paper_regions",
    "_paper_exports",
    "research_progress",
    "research_workflow_steps",
    "research_workflow_sequence",
}
_SUPPRESSED = ContextVar("paper_history_capture_suppressed", default=False)
_FIELDS = ("proposal_foundation", "proposal_entry_path", "proposal_stage_states", "rebuttal_entries")
_UPLOADS = "paper_history_pending_uploads"


@event.listens_for(Session, "after_commit")
def _committed_uploads(session):
    """Retain committed immutable manifests and clear their rollback bookkeeping."""
    if not session.in_nested_transaction():
        session.info.pop(_UPLOADS, None)


@event.listens_for(Session, "after_rollback")
def _rollback_uploads(session):
    """Clean only manifests uploaded by the transaction being rolled back."""
    nested = session.get_nested_transaction()
    retained = []
    for storage, key, transaction in session.info.pop(_UPLOADS, []):
        if nested is not None and transaction is not nested:
            retained.append((storage, key, transaction))
            continue
        try:
            storage.delete(key)
        except Exception:
            logger.warning("Could not clean a rolled-back paper history manifest: {}", key)
    if retained:
        session.info[_UPLOADS] = retained


@event.listens_for(Session, "after_transaction_end")
def _closed_uploads(session, transaction):
    """Also clean an uncommitted outer transaction abandoned by Session.close."""
    if transaction.parent is None:
        for storage, key, _owner in session.info.pop(_UPLOADS, []):
            try:
                storage.delete(key)
            except Exception:
                logger.warning("Could not clean an abandoned paper history manifest: {}", key)


@contextmanager
def suppress_capture():
    """Prevent parent mutation hooks from recursively capturing a restore."""
    token = _SUPPRESSED.set(True)
    try:
        yield
    finally:
        _SUPPRESSED.reset(token)


def _json(value) -> bytes:
    """Encode canonical JSON without altering any stored text values."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")


def _hash(value) -> str:
    """Fingerprint canonical values with SHA-256."""
    return hashlib.sha256(_json(value)).hexdigest()


def _context(value: dict | None) -> dict:
    """Exclude coordination pointers and progress from author content."""
    return {key: copy.deepcopy(item) for key, item in (value or {}).items() if key not in _CONTROLS}


def _read(key: str) -> bytes:
    """Read an immutable object, failing closed when history is unavailable."""
    try:
        return paper_service._storage().download(key)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Paper history object is unavailable") from exc


def _read_json(key: str) -> dict:
    """Read one object without exposing storage keys to API clients."""
    try:
        value = json.loads(_read(key))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Paper history object is invalid") from exc


def _path(value: str) -> str:
    """Accept only an exact relative manifest path, never a storage key."""
    path = PurePosixPath(value)
    if (
        not value
        or value == "."
        or len(value) > 2048
        or path.is_absolute()
        or "\\" in value
        or path.as_posix() != value
        or any(part in {".", ".."} for part in path.parts)
        or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise HTTPException(status_code=422, detail="Invalid history file path")
    return value


def _file(path: str, raw: bytes, **location) -> dict:
    """Describe exact bytes independently of their backing object location."""
    try:
        raw.decode("utf-8")
        binary = b"\x00" in raw
    except UnicodeDecodeError:
        binary = True
    mime = mimetypes.guess_type(path)[0] or ("application/octet-stream" if binary else "text/plain")
    if mime in {"application/pdf", "application/zip"} or (mime.startswith("image/") and mime != "image/svg+xml"):
        binary = True
    return {"size": len(raw), "hash": hashlib.sha256(raw).hexdigest(), "binary": binary, "mime_type": mime, **location}


def _text(files: dict, path: str, value, *, structured: bool = False) -> None:
    """Expose existing structured values or prose without generating new prose."""
    if value is None:
        return
    raw = _json(value) if structured else value.encode("utf-8")
    files[path] = _file(path, raw, content=raw.decode("utf-8"))


def _source(db: Session, workspace: models.PaperWorkspace, source_id=None) -> dict | None:
    """Resolve a source descriptor only within its owning workspace."""
    source_id = source_id or workspace.active_source_version_id
    source = db.get(models.PaperSourceVersion, source_id) if source_id else None
    if source is None:
        if source_id:
            raise HTTPException(status_code=503, detail="Paper history source is unavailable")
        return None
    if source.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Paper source not found")
    return {
        key: str(value) if isinstance(value, uuid.UUID) else copy.deepcopy(value)
        for key, value in {
            "id": source.id,
            "object_key": source.object_key,
            "source_kind": source.source_kind,
            "filename": source.filename,
            "content_hash": source.content_hash,
            "manifest": source.manifest,
        }.items()
    }


def _source_files(source: dict | None) -> dict:
    """Reference original source objects without rewriting historical bundles."""
    if not source:
        return {}
    raw = _read(source["object_key"])
    if source["source_kind"] == "markdown":
        path = f"source/{_path(source['filename'])}"
        return {path: _file(path, raw, object_key=source["object_key"])}
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name in source["manifest"]:
                path = f"source/{_path(name)}"
                files[path] = _file(path, archive.read(name), object_key=source["object_key"], archive_path=name)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise HTTPException(status_code=503, detail="Paper history source bundle is invalid") from exc
    return files


def _reviews(db: Session, workspace: models.PaperWorkspace) -> list[dict]:
    """Capture stable reviewer identities and their exact evidence bytes."""
    result = []
    for review in db.exec(select(models.PaperReview).where(models.PaperReview.workspace_id == workspace.id)).all():
        item = {
            key: copy.deepcopy(getattr(review, key))
            for key in (
                "source_system",
                "reviewer_label",
                "title",
                "object_key",
                "baseline_dimensions",
            )
        }
        item.update(id=str(review.id), content_hash=hashlib.sha256(_read(review.object_key)).hexdigest())
        result.append(item)
    return sorted(result, key=lambda item: item["id"])


def _state(db: Session, workspace: models.PaperWorkspace) -> dict:
    """Read all canonical content while leaving coordination state out of history."""
    state = {key: copy.deepcopy(getattr(workspace, key)) for key in _FIELDS}
    state.update(
        source=_source(db, workspace),
        rebuttal_context=_context(workspace.rebuttal_context),
        reviews=_reviews(db, workspace),
    )
    state["exports"] = copy.deepcopy((workspace.rebuttal_context or {}).get("_paper_exports", {}))
    return state


def _state_hash(state: dict) -> str:
    """Hash content rather than control pointers, stage activity, or object locations."""
    stages = {
        name: {key: value for key, value in stage.items() if key in {"summary", "findings", "artifact", "output"}}
        for name, stage in state.get("proposal_stage_states", {}).items()
        if isinstance(stage, dict)
    }
    stages = {name: output for name, output in stages.items() if output}
    return _hash(
        {
            "source_hash": (state.get("source") or {}).get("content_hash"),
            "proposal_foundation": state.get("proposal_foundation"),
            "proposal_entry_path": state.get("proposal_entry_path"),
            "stage_outputs": stages,
            "rebuttal_context": _context(state.get("rebuttal_context")),
            "rebuttal_entries": state.get("rebuttal_entries", []),
            "reviews": [
                {key: value for key, value in item.items() if key != "object_key"} for item in state.get("reviews", [])
            ],
            "region_targets": state.get("region_targets"),
        }
    )


def current_state_hash(db: Session, workspace: models.PaperWorkspace) -> str:
    """Return the complete author-content token for an optimistic publication.

    Args:
        db: Caller-owned session containing pending content changes.
        workspace: Already authorized workspace; dirty values are retained.

    Returns:
        SHA-256 of source content, all current output content, and reviewer evidence.
    """
    return _state_hash(_state(db, workspace))


def _files(state: dict) -> dict:
    """Build stable namespaces for source, outputs, artifacts, and evidence."""
    files = _source_files(state.get("source"))
    context = state.get("rebuttal_context", {})
    baseline = {key: context[key] for key in ("baseline", "intake", "analysis") if key in context}
    if baseline:
        _text(files, "outputs/baseline.json", baseline, structured=True)
    for review in (context.get("intake") or {}).get("reviewer_sources", []):
        text = review.get("review_markdown")
        identity = review.get("review_id") or review.get("reviewer_id")
        if isinstance(text, str) and identity:
            _text(files, f"reviews/{quote(str(identity), safe='-_.')}.md", text)
    if state.get("proposal_foundation"):
        _text(files, "outputs/proposal-foundation.json", state["proposal_foundation"], structured=True)
    rendered = context.get("rendered") or {}
    text = rendered.get("text")
    if not isinstance(text, str):
        text = (context.get("compliance") or {}).get("rendered_text")
    if isinstance(text, str):
        _text(files, "outputs/rebuttal.md", text)
    else:
        for entry in state.get("rebuttal_entries", []):
            if isinstance(entry.get("response"), str):
                identity = quote(str(entry.get("id", "entry")), safe="-_.")
                _text(files, f"outputs/rebuttal/{identity}.md", entry["response"])
        response = rendered.get("global_response") or {}
        if isinstance(response.get("response"), str):
            _text(files, "outputs/rebuttal/global.md", response["response"])
    if isinstance(context.get("chair_message"), str):
        _text(files, "outputs/ac-summary.md", context["chair_message"])
    actions = {key: rendered[key] for key in ("findings", "open_placeholders") if rendered.get(key)}
    findings = {
        key: stage["findings"]
        for key, stage in state.get("proposal_stage_states", {}).items()
        if isinstance(stage, dict) and stage.get("findings")
    }
    if findings:
        actions["stage_findings"] = findings
    if actions:
        _text(files, "outputs/author-actions.json", actions, structured=True)
    for review in state.get("reviews", []):
        path = f"reviews/{review['id']}.md"
        files[path] = _file(path, _read(review["object_key"]), object_key=review["object_key"])
    return files


def _export_files(workspace, state):
    """Attach immutable builds lazily without changing the content revision."""
    source = state.get("source") or {}
    source_id = source.get("id")
    references = set(state.get("exports", {}).get(source_id, []))
    references.update(((workspace.rebuttal_context or {}).get("_paper_exports") or {}).get(source_id, []))
    files = {}
    for key in sorted(references):
        prefix = f"paper/{workspace.user_id}/{workspace.id}/renders/{source_id}/{source.get('content_hash')}/"
        if not key.startswith(prefix) or not key.endswith("/manifest.json") or ".." in PurePosixPath(key).parts:
            raise HTTPException(status_code=503, detail="Paper export history reference is invalid")
        manifest = _read_json(key)
        if str(manifest.get("source_version_id")) != source_id or manifest.get("source_hash") != source.get(
            "content_hash"
        ):
            raise HTTPException(status_code=503, detail="Paper export history source does not match")
        build = PurePosixPath(key).parent.name
        for item in manifest.get("files", []):
            name = _path(item["path"])
            object_key = f"{key.removesuffix('/manifest.json')}/{name}"
            path = f"exports/{build}/{name}"
            files[path] = _file(path, _read(object_key), object_key=object_key)
        diagnostics = {"status": manifest.get("status"), "diagnostics": manifest.get("diagnostics", [])}
        if diagnostics["diagnostics"] or diagnostics["status"] != "ready":
            _text(files, f"exports/{build}/diagnostics.json", diagnostics, structured=True)
    return files


def record_export(db, workspace, manifest_key: str, commit: bool = True) -> None:
    """Register an owned render without changing content hash or revision identity.

    Args:
        db: Session updating the small per-source export pointer.
        workspace: Authorized workspace, retaining pending author text.
        manifest_key: Renderer key ending in source-hash/build-hash/manifest.json.
        commit: Commit here or join the caller's transaction.
    """
    prefix = f"paper/{workspace.user_id}/{workspace.id}/renders/"
    if (
        not manifest_key.startswith(prefix)
        or not manifest_key.endswith("/manifest.json")
        or ".." in PurePosixPath(manifest_key).parts
        or PurePosixPath(manifest_key).as_posix() != manifest_key
    ):
        raise HTTPException(status_code=422, detail="Invalid owned export manifest")
    manifest = _read_json(manifest_key)
    try:
        identity = uuid.UUID(str(manifest["source_version_id"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid export source") from exc
    source = _source(db, workspace, identity)
    expected = f"{prefix}{identity}/{source['content_hash']}/"
    if not manifest_key.startswith(expected) or manifest.get("source_hash") != source["content_hash"]:
        raise HTTPException(status_code=422, detail="Export manifest source does not match")
    dirty_context = inspect(workspace).attrs.rebuttal_context.history.has_changes()
    with db.no_autoflush:
        stored = db.exec(
            select(models.PaperWorkspace.rebuttal_context)
            .where(
                models.PaperWorkspace.id == workspace.id,
            )
            .with_for_update()
        ).one()
    context = copy.deepcopy((workspace.rebuttal_context if dirty_context else stored) or {})
    for control in (_POINTER, "_paper_regions"):
        if (stored or {}).get(control):
            context[control] = stored[control]
    references = copy.deepcopy((stored or {}).get("_paper_exports", {}))
    for source_id, keys in context.get("_paper_exports", {}).items():
        references.setdefault(source_id, [])
        references[source_id] = list(dict.fromkeys([*references[source_id], *keys]))
    keys = references.setdefault(str(identity), [])
    if manifest_key not in keys:
        keys.append(manifest_key)
    context["_paper_exports"] = references
    workspace.rebuttal_context = context
    db.add(workspace)
    if commit:
        db.commit()
    else:
        db.flush()


def _key(workspace: models.PaperWorkspace, revision_id: str) -> str:
    """Locate a newly captured immutable manifest within its owner namespace."""
    return f"paper/{workspace.user_id}/{workspace.id}/history/revisions/{revision_id}.json"


def _chain(workspace: models.PaperWorkspace, *, head_only: bool = False) -> list[dict]:
    """Follow only validated owned manifests and detect a corrupt chain."""
    pointer = (workspace.rebuttal_context or {}).get(_POINTER) or {}
    key = pointer.get("object_key")
    revisions, seen = [], set()
    while key:
        if key in seen or not key.startswith(f"paper/{workspace.user_id}/{workspace.id}/history/revisions/"):
            raise HTTPException(status_code=503, detail="Paper history chain is invalid")
        seen.add(key)
        manifest = _read_json(key)
        if manifest.get("workspace_id") != str(workspace.id):
            raise HTTPException(status_code=503, detail="Paper history ownership is invalid")
        revisions.append(manifest)
        if head_only:
            break
        key = manifest.get("previous_object_key")
    return revisions


def _revision(manifest: dict, current_id: str | None = None) -> schemas.PaperHistoryRevision:
    """Project public metadata without exposing object keys or private pointers."""
    return schemas.PaperHistoryRevision.model_validate(
        {
            **{key: manifest[key] for key in ("id", "label", "created_time", "origin", "summary", "state_hash")},
            "source_version_id": (manifest.get("state", {}).get("source") or {}).get("id"),
            "is_current": manifest["id"] == current_id,
        }
    )


def _manifest(
    workspace, state, *, revision_id=None, origin, summary, created_time=None, label=None, files=None, partial=False
):
    """Describe an immutable complete capture or an explicitly partial legacy record."""
    revision_id = revision_id or uuid.uuid4().hex
    return {
        "schema_version": 1,
        "workspace_id": str(workspace.id),
        "id": revision_id,
        "label": label or summary or "Workspace revision",
        "summary": summary,
        "origin": origin,
        "created_time": str(created_time or datetime.now(UTC)),
        "state_hash": _state_hash(state),
        "state": state,
        "files": {} if files is None else files,
        "deferred_files": files is None,
        "partial": partial,
    }


def _append(db, workspace, manifest, created):
    """Upload before advancing the tiny transactional database pointer."""
    context = copy.deepcopy(workspace.rebuttal_context or {})
    manifest["previous_object_key"] = (context.get(_POINTER) or {}).get("object_key")
    key = _key(workspace, uuid.uuid4().hex)
    created.append(key)
    db.info.setdefault(_UPLOADS, []).append(
        (paper_service._storage(), key, db.get_nested_transaction() or db.get_transaction())
    )
    paper_service._storage().upload(key, _json(manifest), content_type="application/json")
    context[_POINTER] = {"object_key": key, "revision": manifest["id"], "state_hash": manifest["state_hash"]}
    workspace.rebuttal_context = context
    db.add(workspace)


def _artifact_state(kind: str, artifact: dict) -> dict:
    """Recover only output fields actually recorded in a historical artifact."""
    context = {}
    state = {}
    if kind == "rebuttal_baseline":
        context.update({key: artifact[key] for key in ("intake", "analysis") if key in artifact})
        context["baseline"] = {
            key: artifact[key] for key in ("summary", "ready_for_generation", "findings") if key in artifact
        }
    elif kind == "autorebuttal":
        context.update({key: artifact[key] for key in ("strategy", "draft", "compliance") if key in artifact})
        compliance = artifact.get("compliance") or artifact
        if "entries" in compliance:
            state["rebuttal_entries"] = compliance["entries"]
        text = compliance.get("rendered_text", compliance.get("text"))
        if isinstance(text, str):
            context["rendered"] = {
                "text": text,
                "output_format": artifact.get("output_format")
                or ((artifact.get("strategy") or {}).get("format_plan") or {}).get("output_format", "markdown"),
                "ready_for_submission": compliance.get("ready_for_submission", False),
                **{
                    key: compliance[key]
                    for key in (
                        "global_response",
                        "findings",
                        "open_placeholders",
                        "ready_for_submission",
                    )
                    if key in compliance
                },
            }
    elif kind == "ac_summary" and "message" in artifact:
        context["chair_message"] = artifact["message"]
    if context:
        state["rebuttal_context"] = context
    return state


def _legacy(db: Session, workspace: models.PaperWorkspace) -> list[dict]:
    """Recover original IDs and exact stored bytes without inventing missing state."""
    revisions = []
    for source in db.exec(
        select(models.PaperSourceVersion).where(models.PaperSourceVersion.workspace_id == workspace.id)
    ).all():
        revisions.append(
            _manifest(
                workspace,
                {"source": _source(db, workspace, source.id)},
                revision_id=f"source:{source.id}",
                origin="legacy",
                summary=source.change_summary
                or "Recovered source snapshot; historical outputs were not recorded here.",
                created_time=source.created_time,
                label=f"Source v{source.version}",
                partial=True,
            )
        )
    runs = db.exec(
        select(models.PaperAgentRun).where(
            models.PaperAgentRun.workspace_id == workspace.id,
            models.PaperAgentRun.status == "ready",
        )
    ).all()
    for run in runs:
        if not run.artifact_object_key:
            continue
        state = {}
        if run.source_version_id:
            state["source"] = _source(db, workspace, run.source_version_id)
        manifest = _manifest(
            workspace,
            state,
            revision_id=f"run:{run.id}",
            origin="legacy",
            summary=run.message or "Recovered artifact; open this revision to read its recorded output.",
            created_time=run.updated_time,
            label=f"{run.run_kind}: {run.id}",
            partial=True,
        )
        manifest["legacy_artifact"] = {"object_key": run.artifact_object_key, "run_kind": run.run_kind}
        manifest["state_hash"] = _hash(
            {
                "source_hash": (state.get("source") or {}).get("content_hash"),
                "legacy_artifact": manifest["legacy_artifact"],
                "run_id": str(run.id),
            }
        )
        revisions.append(manifest)
    pointer = (workspace.rebuttal_context or {}).get("_paper_regions") or {}
    key, seen = pointer.get("object_key"), set()
    regional_revisions = []
    while key:
        if key in seen:
            raise HTTPException(status_code=503, detail="Regional history chain is invalid")
        seen.add(key)
        snapshot = _read_json(key)
        targets = [copy.deepcopy(region) for region in snapshot.get("regions", {}).values() if isinstance(region, dict)]
        state = {"region_targets": targets}
        files = {}
        for region in targets:
            if isinstance(region.get("content"), str):
                _text(
                    files, f"outputs/regions/{quote(str(region.get('id', 'region')), safe='-_.')}.md", region["content"]
                )
        for identity, versions in snapshot.get("review_versions", {}).items():
            for version, location in versions.items():
                path = f"reviews/{quote(identity, safe='-_.')}/{quote(version, safe='-_.')}.md"
                files[path] = {"object_key": location["object_key"], "deferred": True}
        files["artifacts/regions.json"] = {"object_key": key, "deferred": True}
        regional_revisions.append(
            _manifest(
                workspace,
                state,
                revision_id=f"region:{PurePosixPath(key).stem}",
                origin="legacy",
                summary="Recovered regional snapshot; original capture timestamp unavailable.",
                created_time=workspace.updated_time,
                label="Regional snapshot",
                files=files,
                partial=True,
            )
        )
        key = snapshot.get("previous_object_key")
    revisions.extend(reversed(regional_revisions))
    return sorted(revisions, key=lambda item: item["created_time"])


def capture_revision(db, workspace, origin="agent", summary="", run_id=None, commit=False):
    """Capture canonical content, joining the caller's transaction by default.

    Args:
        db: Session whose outer transaction owns the workspace mutation.
        workspace: Authorized workspace, including uncommitted content changes.
        origin: Provenance such as agent, manual, legacy, or restore.
        summary: Human-readable description without regenerated document prose.
        run_id: Optional artifact run associated with this capture.
        commit: Whether to commit here instead of leaving it to the caller.

    Returns:
        Captured revision, an unchanged current revision, or None during restore hooks.
    """
    if _SUPPRESSED.get():
        return None
    created = []
    try:
        with db.no_autoflush:
            stored = db.exec(
                select(models.PaperWorkspace.rebuttal_context)
                .where(
                    models.PaperWorkspace.id == workspace.id,
                )
                .with_for_update()
            ).one()
        context = copy.deepcopy(workspace.rebuttal_context or {})
        if (stored or {}).get(_POINTER):
            context[_POINTER] = stored[_POINTER]
            workspace.rebuttal_context = context
        db.flush()
        state = _state(db, workspace)
        chain = _chain(workspace, head_only=True)
        if chain and chain[0]["state_hash"] == _state_hash(state) and origin != "restore":
            if commit:
                db.commit()
            return _revision(chain[0], chain[0]["id"])
        if not chain:
            for legacy in _legacy(db, workspace):
                _append(db, workspace, legacy, created)
        manifest = _manifest(workspace, state, origin=origin, summary=summary)
        if run_id:
            run = db.get(models.PaperAgentRun, run_id)
            if run is None or run.workspace_id != workspace.id:
                raise HTTPException(status_code=404, detail="Paper history run not found")
            if run.artifact_object_key:
                manifest["artifact_object_key"] = run.artifact_object_key
            manifest["run_id"] = str(run.id)
        _append(db, workspace, manifest, created)
        db.flush()
        if commit:
            db.commit()
        return _revision(manifest, manifest["id"])
    except Exception:
        db.rollback()
        for key in created:
            paper_service._storage().delete(key)
        raise


def _owned_lock(db, user, workspace_id):
    """Hide foreign workspaces and refresh state under the mutation row lock."""
    paper_service._owned_workspace(db, user.id, workspace_id)
    return db.exec(
        select(models.PaperWorkspace)
        .where(
            models.PaperWorkspace.id == workspace_id,
            models.PaperWorkspace.user_id == user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def get_history(db, user, workspace_id) -> schemas.PaperHistoryResponse:
    """Lazily recover legacy records and return complete owned revision metadata."""
    workspace = _owned_lock(db, user, workspace_id)
    current = capture_revision(db, workspace, origin="legacy", summary="Current workspace")
    response = schemas.PaperHistoryResponse(
        revisions=[_revision(item, current.id) for item in _chain(workspace)],
        current_revision_id=current.id,
        current_state_hash=current.state_hash,
    )
    db.commit()
    return response


def _resolve(db, user, workspace_id, revision_id):
    """Resolve public revision IDs exclusively through the owner's history chain."""
    workspace = paper_service._owned_workspace(db, user.id, workspace_id)
    for manifest in _chain(workspace):
        if manifest["id"] == revision_id:
            _materialize(manifest)
            manifest["files"].update(_export_files(workspace, manifest["state"]))
            return workspace, manifest
    raise HTTPException(status_code=404, detail="Paper history revision not found")


def _materialize(manifest: dict) -> None:
    """Read bodies only for the selected revision, retaining original object refs."""
    artifact_ref = manifest.get("legacy_artifact")
    raw_artifact = None
    if artifact_ref:
        raw_artifact = _read(artifact_ref["object_key"])
        try:
            artifact = json.loads(raw_artifact)
            if not isinstance(artifact, dict):
                raise ValueError("Expected a JSON object")
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=503, detail="Paper history artifact is invalid") from exc
        manifest["state"].update(_artifact_state(artifact_ref["run_kind"], artifact))
    if manifest.get("deferred_files"):
        manifest["files"] = _files(manifest["state"])
        manifest["deferred_files"] = False
    if raw_artifact is not None and not any(path.startswith("outputs/") for path in manifest["files"]):
        path = f"metadata/{quote(artifact_ref['run_kind'], safe='-_.')}.json"
        manifest["files"][path] = _file(path, raw_artifact, object_key=artifact_ref["object_key"])
    for path, item in list(manifest["files"].items()):
        if item.get("deferred"):
            manifest["files"][path] = _file(path, _read(item["object_key"]), object_key=item["object_key"])


def compare_revisions(db, user, workspace_id, base_id, target_id) -> schemas.PaperHistoryComparison:
    """Compare complete inventories, including binary and unchanged files."""
    _, base = _resolve(db, user, workspace_id, base_id)
    _, target = _resolve(db, user, workspace_id, target_id)
    before, after = base["files"], target["files"]
    counts = dict.fromkeys(("added", "modified", "deleted", "unchanged"), 0)
    files = []
    for path in sorted(before.keys() | after.keys()):
        left, right = before.get(path), after.get(path)
        status = (
            "added"
            if left is None
            else "deleted"
            if right is None
            else "unchanged"
            if left["hash"] == right["hash"]
            else "modified"
        )
        counts[status] += 1
        item = right or left
        files.append(
            schemas.PaperHistoryComparedFile(
                path=path,
                status=status,
                mime_type=item["mime_type"],
                binary=item["binary"],
                before_size=left["size"] if left else 0,
                after_size=right["size"] if right else 0,
            )
        )
    return schemas.PaperHistoryComparison(base_id=base_id, target_id=target_id, files=files, counts=counts)


def _bytes(item: dict) -> bytes:
    """Read only the exact location recorded in an authorized manifest."""
    if "content" in item:
        raw = item["content"].encode("utf-8")
    else:
        raw = _read(item["object_key"])
        if item.get("archive_path"):
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    raw = archive.read(item["archive_path"])
            except (KeyError, zipfile.BadZipFile) as exc:
                raise HTTPException(status_code=503, detail="Paper history file is unavailable") from exc
    if hashlib.sha256(raw).hexdigest() != item["hash"]:
        raise HTTPException(status_code=503, detail="Paper history file integrity check failed")
    return raw


def get_file(db, user, workspace_id, revision_id, path) -> schemas.PaperHistoryFile:
    """Return exact text and an authenticated URL, or an explicit absent side."""
    _, manifest = _resolve(db, user, workspace_id, revision_id)
    path = _path(path)
    item = manifest["files"].get(path)
    if item is None:
        return schemas.PaperHistoryFile(
            path=path,
            content=None,
            binary=False,
            mime_type=mimetypes.guess_type(path)[0] or "text/plain",
            download_url=None,
            exists=False,
        )
    raw = _bytes(item)
    url = f"{settings.API_V1_STR}/llm4ad/papers/workspaces/{workspace_id}/history/{quote(revision_id, safe='')}/download?{urlencode({'path': path})}"
    return schemas.PaperHistoryFile(
        path=path,
        content=None if item["binary"] else raw.decode("utf-8"),
        binary=item["binary"],
        mime_type=item["mime_type"],
        download_url=url,
        exists=True,
    )


def download_file(db, user, workspace_id, revision_id, path) -> tuple[bytes, str]:
    """Read an owned historical file without issuing storage credentials or URLs."""
    _, manifest = _resolve(db, user, workspace_id, revision_id)
    path = _path(path)
    item = manifest["files"].get(path)
    if item is None:
        raise HTTPException(status_code=404, detail="Paper history file not found")
    return _bytes(item), item["mime_type"]


def _merge(left: dict, right: dict) -> dict:
    """Apply only recorded legacy context fields, retaining unknown old content."""
    result = copy.deepcopy(left)
    for key, value in right.items():
        result[key] = (
            _merge(result.get(key, {}), value)
            if isinstance(value, dict) and isinstance(result.get(key, {}), dict)
            else copy.deepcopy(value)
        )
    return result


def _restore_regions(db, workspace, targets, files):
    """Apply exact legacy regional values only when their stable target still exists."""
    names = {
        "context": "rebuttal_context",
        "entries": "rebuttal_entries",
        "foundation": "proposal_foundation",
        "stages": "proposal_stage_states",
    }
    source_updates = {}
    for region in targets:
        target = region.get("target", [])
        if target and target[0] == "source":
            source_updates[paper_service._validate_zip_path(target[1]).as_posix()] = region["content"].encode("utf-8")
            continue
        if target and target[0] == "review":
            review = db.get(models.PaperReview, uuid.UUID(target[1]))
            if review is None or review.workspace_id != workspace.id:
                raise HTTPException(status_code=409, detail="Legacy reviewer target no longer exists")
            fingerprint = hashlib.sha256(region["content"].encode("utf-8")).hexdigest()
            original = next(
                (
                    item
                    for path, item in files.items()
                    if path.startswith(f"reviews/{review.id}/") and item["hash"] == fingerprint
                ),
                None,
            )
            if original is None:
                raise HTTPException(status_code=409, detail="Legacy reviewer evidence is unavailable")
            _bytes(original)
            review.object_key = original["object_key"]
            review.content_hash = fingerprint
            db.add(review)
            continue
        if not target or target[0] not in names:
            continue
        value = copy.deepcopy(getattr(workspace, names[target[0]]))
        cursor = value
        try:
            if target[0] == "entries" and region.get("id", "").startswith("rebuttal:"):
                identity = region["id"].split(":")[1]
                cursor = next(entry for entry in value if str(entry.get("id")) == identity)
                parts = target[2:-1]
            else:
                parts = target[1:-1]
            for part in parts:
                cursor = cursor[part]
            cursor[target[-1]] = region["content"]
        except (KeyError, IndexError, TypeError, StopIteration) as exc:
            raise HTTPException(status_code=409, detail="Legacy regional target no longer exists") from exc
        setattr(workspace, names[target[0]], value)
    if source_updates:
        source = db.get(models.PaperSourceVersion, workspace.active_source_version_id)
        if source is None:
            raise HTTPException(status_code=409, detail="Legacy source restore requires an existing source")
        entries = dict(paper_service._source_entries(source))
        entries.update(source_updates)
        return paper_service._create_source_version(
            db,
            workspace,
            list(entries.items()),
            parent=source,
            change_summary="Restored recorded regional source text",
            commit=False,
        )
    return None


def restore_revision(db, user, workspace_id, revision_id, request) -> schemas.PaperHistoryRevision:
    """Restore known historical content as a new guarded, atomic revision."""
    workspace = _owned_lock(db, user, workspace_id)
    if current_state_hash(db, workspace) != request.expected_state_hash:
        raise HTTPException(status_code=409, detail="Paper content changed; reload before restoring")
    pointer = (workspace.rebuttal_context or {}).get(_POINTER) or {}
    if request.expected_revision_id is not None and pointer.get("revision") != request.expected_revision_id:
        raise HTTPException(status_code=409, detail="Paper history changed; reload before restoring")
    _, target = _resolve(db, user, workspace_id, revision_id)
    state = target["state"]
    source_key = None
    restored = None
    try:
        capture_revision(db, workspace, origin="manual", summary="Before history restore")
        with suppress_capture():
            parent = (
                db.get(models.PaperSourceVersion, workspace.active_source_version_id)
                if workspace.active_source_version_id
                else None
            )
            if "source" in state:
                if state["source"]:
                    entries = [
                        (path.removeprefix("source/"), _bytes(item))
                        for path, item in target["files"].items()
                        if path.startswith("source/")
                    ]
                    restored = paper_service._create_source_version(
                        db,
                        workspace,
                        entries,
                        parent=parent,
                        change_summary=f"Restored {target['label']}",
                        commit=False,
                    )
                    source_key = restored.object_key
                else:
                    workspace.active_source_version_id = None
            for field in _FIELDS:
                if field in state:
                    setattr(workspace, field, copy.deepcopy(state[field]))
            controls = {key: value for key, value in (workspace.rebuttal_context or {}).items() if key in _CONTROLS}
            if "rebuttal_context" in state:
                content = (
                    _merge(_context(workspace.rebuttal_context), state["rebuttal_context"])
                    if target.get("partial")
                    else copy.deepcopy(state["rebuttal_context"])
                )
                workspace.rebuttal_context = {**content, **controls}
            regional_source = _restore_regions(db, workspace, state.get("region_targets", []), target["files"])
            if regional_source is not None:
                restored = regional_source
                source_key = restored.object_key
            for item in state.get("reviews", []):
                review = db.get(models.PaperReview, uuid.UUID(item["id"]))
                if review is not None and review.workspace_id != workspace.id:
                    raise HTTPException(status_code=409, detail="Reviewer history identity conflicts")
                if review is None:
                    if not workspace.active_source_version_id:
                        raise HTTPException(status_code=409, detail="Reviewer history requires a source")
                    review = models.PaperReview(
                        id=uuid.UUID(item["id"]),
                        workspace_id=workspace.id,
                        source_version_id=workspace.active_source_version_id,
                    )
                for key, value in item.items():
                    if key != "id":
                        setattr(review, key, copy.deepcopy(value))
                db.add(review)
            db.add(workspace)
            db.flush()
        result = capture_revision(db, workspace, origin="restore", summary=f"Restored {target['label']}")
        db.commit()
    except Exception:
        db.rollback()
        if source_key:
            paper_service._storage().delete(source_key)
        raise
    if restored is not None:
        paper_service._sync_paper_workspace_source_best_effort(workspace, restored)
    return result
