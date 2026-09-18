"""Validation and persistence for native research stage artifacts."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field, model_validator
from sqlmodel import select

from app import models
from app.core.db import get_db_session
from app.core.storage import storage
from app.schemas import paper as paper_schemas
from app.services import paper_service, paper_workflow


class DiscoveryArtifact(BaseModel):
    """Validated output from an algorithm discovery run."""

    proposals: list[paper_schemas.PaperAlgorithmProposalCreate] = Field(min_length=1, max_length=20)


class ProposalFormattingArtifact(BaseModel):
    """Validated Typst result and its durable project-level foundation."""

    source_path: str = Field(min_length=1, max_length=1_024)
    summary: str = Field(min_length=1, max_length=20_000)
    context_patch: paper_schemas.ProposalContextPatch


class ProposalSectionArtifact(BaseModel):
    """Validated result produced by one proposal writing stage."""

    source_paths: list[str] = Field(min_length=1, max_length=10)
    summary: str = Field(min_length=1, max_length=20_000)
    citations: list[str] = Field(default_factory=list, max_length=500)
    context_patch: paper_schemas.ProposalContextPatch


class ProposalFinalReviewArtifact(BaseModel):
    """Read-only whole-proposal review produced before PDF export."""

    entry_path: str = Field(min_length=1, max_length=1_024)
    summary: str = Field(min_length=1, max_length=20_000)
    findings: list[str] = Field(default_factory=list, max_length=200)
    ready_for_export: bool
    context_patch: paper_schemas.ProposalContextPatch

    @model_validator(mode="after")
    def require_blocking_findings(self) -> ProposalFinalReviewArtifact:
        """Require an actionable explanation when export remains blocked."""
        if not self.ready_for_export and not self.findings:
            raise ValueError("A review that requires revision must include at least one finding")
        return self


class MetricArtifact(BaseModel):
    """Validated optional metric suggestions."""

    metrics: list[paper_schemas.PaperMetricDraft] = Field(min_length=1, max_length=50)


class RevisionCandidateArtifact(BaseModel):
    """One exact, reviewable source replacement emitted by the paper agent."""

    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=20_000)
    source_path: str = Field(min_length=1, max_length=1_024)
    original_text: str = Field(min_length=1, max_length=500_000)
    replacement_text: str = Field(min_length=1, max_length=500_000)
    provenance: list[str] = Field(default_factory=list, max_length=200)


class RevisionArtifact(BaseModel):
    """Validated collection of independent paper revision candidates."""

    candidates: list[RevisionCandidateArtifact] = Field(min_length=1, max_length=10)


class BoundaryArtifact(paper_schemas.PaperBoundaryDraft):
    """Validated paper boundary returned by the analysis agent."""


class TargetArtifact(BaseModel):
    """Validated collection of source-grounded optimization targets."""

    targets: list[paper_schemas.PaperOptimizationTargetDraft] = Field(min_length=1, max_length=100)


def _merge_proposal_context(
    current: dict[str, object] | None,
    patch: paper_schemas.ProposalContextPatch,
) -> paper_schemas.ProposalFoundation:
    """Apply model-defined context changes while preserving untouched blocks.

    Args:
        current: Latest persisted proposal context.
        patch: Explicit model-requested block updates and removals.

    Returns:
        The validated complete proposal context after the patch.

    Raises:
        ValueError: If the patch would leave the proposal without context.
    """
    blocks = paper_schemas.ProposalFoundation.model_validate(current).blocks if current is not None else []
    removed = set(patch.remove_keys)
    merged = {block.key: block for block in blocks if block.key not in removed}
    for block in patch.upsert:
        merged[block.key] = block
    if not merged:
        raise ValueError("Proposal context must contain at least one block")
    return paper_schemas.ProposalFoundation(blocks=list(merged.values()))


def _canonical_source_reference(raw_path: str, manifest: Iterable[str]) -> str:
    """Map a trusted paper-runtime path to its stored manifest-relative path."""
    available = set(manifest)
    normalized = raw_path.strip().replace("\\", "/")
    if normalized in available:
        return normalized
    for prefix in (
        "/workspace/source/",
        "source/",
        "/workspace/input/source/",
        "input/source/",
    ):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break
    candidate = PurePosixPath(normalized)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or any(part in {"", "."} for part in candidate.parts)
        or candidate.as_posix() not in available
    ):
        raise ValueError(f"Source path is not in the manifest: {raw_path}")
    return candidate.as_posix()


def _canonical_boundary_payload(
    artifact: BoundaryArtifact,
    manifest: Iterable[str],
) -> dict:
    """Normalize all boundary provenance to stored source-relative paths."""
    payload = artifact.model_dump(mode="json")
    for section in payload["sections"]:
        for source_ref in section.get("source_refs", []):
            source_ref["path"] = _canonical_source_reference(source_ref["path"], manifest)
    for source_entry in payload.get("source_map", []):
        source_path = source_entry.get("path")
        if isinstance(source_path, str) and source_path:
            source_entry["path"] = _canonical_source_reference(source_path, manifest)
    return payload


def _source_path_allowed(path: str, allowed_paths: Iterable[str]) -> bool:
    """Return whether a source-relative path belongs to the current stage."""
    return any(path == allowed or (allowed.endswith("/") and path.startswith(allowed)) for allowed in allowed_paths)


def _validate_proposal_source_changes(
    previous_entries: Iterable[tuple[str, bytes]],
    current_entries: Iterable[tuple[str, bytes]],
    allowed_paths: list[str],
) -> set[str]:
    """Reject source edits outside the current proposal stage boundary."""
    previous = dict(previous_entries)
    current = dict(current_entries)
    changed = {path for path in previous.keys() | current.keys() if previous.get(path) != current.get(path)}
    forbidden = sorted(path for path in changed if not _source_path_allowed(path, allowed_paths))
    if forbidden:
        raise ValueError(f"Proposal stage modified source outside its boundary: {', '.join(forbidden[:10])}")
    return changed


def _update_proposal_stage_state(
    workspace: models.PaperWorkspace,
    run: models.PaperAgentRun,
    source_version_id: uuid.UUID,
    *,
    status: str = "ready",
    summary: str | None = None,
    findings: list[str] | None = None,
) -> None:
    """Record one stage outcome and invalidate its completed dependents.

    Args:
        workspace: Proposal workspace whose stage state should change.
        run: Native agent run publishing the stage result.
        source_version_id: Source version containing the published result.
        status: Durable stage outcome exposed to the author.
        summary: Optional concise result summary.
        findings: Optional review findings that require follow-up.

    Raises:
        ValueError: If the supplied status is not a proposal-stage outcome.
    """
    if status not in {"ready", "needs_revision"}:
        raise ValueError(f"Unsupported proposal stage outcome: {status}")
    workflow = paper_workflow.get_research_workflow(workspace.mode)
    stage = workflow.stage_for_run_kind(run.run_kind)
    states = dict(workspace.proposal_stage_states or {})
    previous_stage = states.get(stage)
    previous_iteration = (
        previous_stage.get("iteration", 0)
        if isinstance(previous_stage, dict) and isinstance(previous_stage.get("iteration", 0), int)
        else 0
    )
    for dependent_stage in workflow.dependent_stages(stage):
        previous = states.get(dependent_stage)
        if isinstance(previous, dict) and previous.get("status") in {
            "ready",
            "needs_revision",
        }:
            states[dependent_stage] = {**previous, "status": "stale"}
    states[stage] = {
        "status": status,
        "run_id": str(run.id),
        "source_version_id": str(source_version_id),
        "updated_time": datetime.now(UTC).isoformat(),
        "iteration": previous_iteration + 1,
        "summary": summary,
        "findings": findings or [],
    }
    workspace.proposal_stage_states = states


def _supersede_boundary_targets(
    targets: Iterable[models.PaperOptimizationTarget],
) -> None:
    """Mark artifacts derived from an older boundary as non-actionable."""
    for target in targets:
        target.status = "superseded"


def _workspace_source_entries(source_root: Path) -> list[tuple[str, bytes]]:
    """Collect a validated, symlink-free project tree after an agent edit."""
    entries: list[tuple[str, bytes]] = []
    for path in sorted(source_root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Paper workspace source cannot contain symbolic links")
        if not path.is_file():
            continue
        relative = path.relative_to(source_root).as_posix()
        paper_service._validate_zip_path(relative)
        entries.append((relative, path.read_bytes()))
    paper_service.validate_paper_source_batch(entries)
    return entries


def _persist_output(run_id: uuid.UUID, user_id: uuid.UUID, work_dir: Path) -> bool:
    """Persist a validated artifact and report whether project source was committed."""
    result_path = work_dir / "output" / "result.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    source_committed = False
    with get_db_session() as db:
        run = db.get(models.PaperAgentRun, run_id)
        if run is None or run.status == models.PaperAgentRunStatus.CANCELLED.value:
            return False
        workspace = db.get(models.PaperWorkspace, run.workspace_id)
        if workspace is None or workspace.user_id != user_id:
            raise RuntimeError("Paper run owner changed")
        artifact_key = f"paper/{user_id}/{workspace.id}/runs/{run.id}/result.json"
        storage.upload(artifact_key, result_path.read_bytes(), content_type="application/json")

        if run.run_kind == models.PaperAgentRunKind.PROPOSAL_FORMATTING.value:
            artifact = ProposalFormattingArtifact.model_validate(payload)
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if source is None:
                raise RuntimeError("Paper source is missing")
            requested_path = paper_service._validate_zip_path(
                str((run.request_payload or {}).get("source_path") or "")
            ).as_posix()
            if PurePosixPath(requested_path).suffix.lower() != ".typ":
                raise ValueError("Proposal formatting target must be a Typst source")
            source_root = paper_service.paper_workspace_source_dir(
                user_id,
                workspace.id,
            )
            entries = _workspace_source_entries(source_root)
            current_manifest = [path for path, _data in entries]
            source_path = _canonical_source_reference(artifact.source_path, current_manifest)
            if source_path != requested_path or PurePosixPath(source_path).suffix.lower() != ".typ":
                raise ValueError("Proposal formatting result does not match the requested Typst source")
            allowed_paths = list((run.request_payload or {}).get("writable_paths") or [])
            _validate_proposal_source_changes(paper_service._source_entries(source), entries, allowed_paths)
            entries = paper_service.ensure_proposal_assembly(entries, source_path)
            entry_map = dict(entries)
            formatted = entry_map.get(source_path, b"")
            if not formatted.strip() or len(formatted) > 5 * 1024 * 1024:
                raise ValueError("Formatted Typst source is empty or too large")
            workspace.proposal_foundation = _merge_proposal_context(
                workspace.proposal_foundation,
                artifact.context_patch,
            ).model_dump(mode="json")
            workspace.proposal_entry_path = source_path
            paper_service._persist_source_entries(
                db,
                workspace,
                source,
                entries,
                change_summary=artifact.summary,
            )
            _update_proposal_stage_state(
                workspace,
                run,
                source.id,
                summary=artifact.summary,
            )
            source_committed = True
        elif run.run_kind in {
            models.PaperAgentRunKind.PROPOSAL_LITERATURE.value,
            models.PaperAgentRunKind.PROPOSAL_RATIONALE.value,
            models.PaperAgentRunKind.PROPOSAL_OBJECTIVES.value,
            models.PaperAgentRunKind.PROPOSAL_METHODS.value,
            models.PaperAgentRunKind.PROPOSAL_INNOVATION_PLAN.value,
            models.PaperAgentRunKind.PROPOSAL_FOUNDATION_FEASIBILITY.value,
        }:
            artifact = ProposalSectionArtifact.model_validate(payload)
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if source is None:
                raise RuntimeError("Paper source is missing")
            source_root = paper_service.paper_workspace_source_dir(user_id, workspace.id)
            entries = _workspace_source_entries(source_root)
            allowed_paths = list((run.request_payload or {}).get("writable_paths") or [])
            changed_paths = _validate_proposal_source_changes(
                paper_service._source_entries(source),
                entries,
                allowed_paths,
            )
            if not workspace.proposal_entry_path:
                raise ValueError("Proposal entry document is missing")
            entries = paper_service.ensure_proposal_assembly(
                entries,
                workspace.proposal_entry_path,
            )
            current_manifest = [path for path, _data in entries]
            reported_paths = {_canonical_source_reference(path, current_manifest) for path in artifact.source_paths}
            if not changed_paths or not changed_paths.issubset(reported_paths):
                raise ValueError("Proposal stage result does not describe every modified source file")
            if any(not _source_path_allowed(path, allowed_paths) for path in reported_paths):
                raise ValueError("Proposal stage result contains a source path owned by another stage")
            paper_service._persist_source_entries(
                db,
                workspace,
                source,
                entries,
                change_summary=artifact.summary,
            )
            workspace.proposal_foundation = _merge_proposal_context(
                workspace.proposal_foundation,
                artifact.context_patch,
            ).model_dump(mode="json")
            _update_proposal_stage_state(
                workspace,
                run,
                source.id,
                summary=artifact.summary,
            )
            source_committed = True
        elif run.run_kind == models.PaperAgentRunKind.PROPOSAL_FINAL_REVIEW.value:
            artifact = ProposalFinalReviewArtifact.model_validate(payload)
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if source is None:
                raise RuntimeError("Paper source is missing")
            source_root = paper_service.paper_workspace_source_dir(user_id, workspace.id)
            entries = _workspace_source_entries(source_root)
            allowed_paths = list((run.request_payload or {}).get("writable_paths") or [])
            changed_paths = _validate_proposal_source_changes(
                paper_service._source_entries(source),
                entries,
                allowed_paths,
            )
            current_manifest = [path for path, _data in entries]
            entry_path = _canonical_source_reference(artifact.entry_path, current_manifest)
            if entry_path != workspace.proposal_entry_path or changed_paths.difference({entry_path}):
                raise ValueError("Final review may only update the proposal entry document")
            entries = paper_service.ensure_proposal_assembly(entries, entry_path)
            paper_service._persist_source_entries(
                db,
                workspace,
                source,
                entries,
                change_summary=artifact.summary,
            )
            workspace.proposal_foundation = _merge_proposal_context(
                workspace.proposal_foundation,
                artifact.context_patch,
            ).model_dump(mode="json")
            _update_proposal_stage_state(
                workspace,
                run,
                source.id,
                status=("ready" if artifact.ready_for_export else "needs_revision"),
                summary=artifact.summary,
                findings=artifact.findings,
            )
            source_committed = True
        elif run.run_kind == models.PaperAgentRunKind.BOUNDARY_ANALYSIS.value:
            artifact = BoundaryArtifact.model_validate(payload)
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if source is None:
                raise RuntimeError("Paper source is missing")
            boundary_payload = _canonical_boundary_payload(artifact, source.manifest)
            boundary = db.exec(
                select(models.PaperBoundarySnapshot).where(
                    models.PaperBoundarySnapshot.source_version_id == run.source_version_id
                )
            ).first()
            if boundary is None:
                boundary = models.PaperBoundarySnapshot(
                    workspace_id=workspace.id,
                    source_version_id=run.source_version_id,
                )
            else:
                derived_targets = list(
                    db.exec(
                        select(models.PaperOptimizationTarget).where(
                            models.PaperOptimizationTarget.boundary_id == boundary.id,
                            models.PaperOptimizationTarget.status != "superseded",
                        )
                    ).all()
                )
                _supersede_boundary_targets(derived_targets)
                for target in derived_targets:
                    db.add(target)
            boundary.run_id = run.id
            boundary.status = "confirmed"
            boundary.content = boundary_payload
            boundary.updated_time = datetime.now(UTC)
            db.add(boundary)
        elif run.run_kind == models.PaperAgentRunKind.ISSUE_EXTRACTION.value:
            artifact = TargetArtifact.model_validate(payload)
            boundary = db.exec(
                select(models.PaperBoundarySnapshot).where(
                    models.PaperBoundarySnapshot.source_version_id == run.source_version_id,
                )
            ).first()
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if boundary is None or source is None:
                raise RuntimeError("Confirmed paper boundary is missing")
            source_entries = dict(paper_service._source_entries(source))
            replaceable_targets = list(
                db.exec(
                    select(models.PaperOptimizationTarget).where(
                        models.PaperOptimizationTarget.boundary_id == boundary.id,
                        models.PaperOptimizationTarget.status.in_(["draft", "ignored"]),
                    )
                ).all()
            )
            for existing_target in replaceable_targets:
                db.delete(existing_target)
            for item in artifact.targets:
                source_path = _canonical_source_reference(
                    item.source_path,
                    source_entries,
                )
                if item.source_quote:
                    try:
                        source_text = source_entries[source_path].decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise ValueError("Optimization target points to a non-text source") from exc
                    quote_count = source_text.count(item.source_quote)
                    if quote_count == 0:
                        raise ValueError(f"Target quote was not found in source: {source_path}")
                    if item.target_type == "manuscript" and quote_count != 1:
                        raise ValueError(f"Manuscript target quote is not unique in source: {source_path}")
                target_payload = item.model_dump(exclude={"review_ids"})
                target_payload["source_path"] = source_path
                db.add(
                    models.PaperOptimizationTarget(
                        workspace_id=workspace.id,
                        source_version_id=run.source_version_id,
                        boundary_id=boundary.id,
                        review_ids=[str(value) for value in item.review_ids],
                        **target_payload,
                    )
                )
        elif run.run_kind == models.PaperAgentRunKind.ALGORITHM_DISCOVERY.value:
            artifact = DiscoveryArtifact.model_validate(payload)
            for proposal in artifact.proposals:
                db.add(
                    models.PaperAlgorithmProposal(
                        workspace_id=workspace.id,
                        source_version_id=run.source_version_id,
                        run_id=run.id,
                        target_id=(
                            uuid.UUID(str((run.request_payload or {}).get("target_id")))
                            if (run.request_payload or {}).get("target_id")
                            else None
                        ),
                        **proposal.model_dump(),
                    )
                )
        elif run.run_kind == models.PaperAgentRunKind.METRIC_SUGGESTION.value:
            artifact = MetricArtifact.model_validate(payload)
            review = db.get(models.PaperReview, run.review_id)
            if review is None:
                raise RuntimeError("Reviewer baseline is missing")
            existing = {
                item.key
                for item in db.exec(
                    select(models.PaperEvaluationMetric).where(
                        models.PaperEvaluationMetric.source_version_id == run.source_version_id
                    )
                ).all()
            }
            for metric in artifact.metrics:
                if metric.source != "model_suggested" or metric.key in existing:
                    raise ValueError("Model metric suggestions cannot replace reviewer baseline metrics")
                db.add(
                    models.PaperEvaluationMetric(
                        workspace_id=workspace.id,
                        source_version_id=run.source_version_id,
                        review_id=review.id,
                        **metric.model_dump(),
                    )
                )
                existing.add(metric.key)
        elif run.run_kind == models.PaperAgentRunKind.PAPER_REVISION.value:
            artifact = RevisionArtifact.model_validate(payload)
            source = db.get(models.PaperSourceVersion, run.source_version_id)
            if source is None:
                raise RuntimeError("Paper source is missing")
            for item in artifact.candidates:
                candidate_id = uuid.uuid4()
                patch_key = f"paper/{user_id}/{workspace.id}/runs/{run.id}/candidates/{candidate_id}.json"
                replacement_payload = item.model_dump(mode="json")
                replacement_payload["source_path"] = _canonical_source_reference(
                    item.source_path,
                    source.manifest,
                )
                storage.upload(
                    patch_key,
                    json.dumps(replacement_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    content_type="application/json",
                )
                db.add(
                    models.PaperRevisionCandidate(
                        id=candidate_id,
                        workspace_id=workspace.id,
                        source_version_id=run.source_version_id,
                        run_id=run.id,
                        target_id=(
                            uuid.UUID(str((run.request_payload or {}).get("target_id")))
                            if (run.request_payload or {}).get("target_id")
                            else None
                        ),
                        title=item.title,
                        summary=item.summary,
                        patch_object_key=patch_key,
                    )
                )
        elif run.run_kind == models.PaperAgentRunKind.JUDGE.value:
            artifact = paper_schemas.PaperJudgeScorePayload.model_validate(payload)
            candidate_id = uuid.UUID(str((run.request_payload or {}).get("candidate_id")))
            candidate = db.get(models.PaperRevisionCandidate, candidate_id)
            if candidate is None or candidate.workspace_id != workspace.id:
                raise RuntimeError("Pinned revision candidate is missing")
            metrics = list(
                db.exec(
                    select(models.PaperEvaluationMetric).where(
                        models.PaperEvaluationMetric.source_version_id == run.source_version_id,
                        models.PaperEvaluationMetric.selected.is_(True),
                    )
                ).all()
            )
            baseline_keys = {item.key for item in metrics if item.locked}
            optional_keys = {item.key for item in metrics if not item.locked}
            if set(artifact.baseline) != baseline_keys or set(artifact.optional) != optional_keys:
                raise ValueError("Judge output does not cover the selected evaluation metrics")
            reviewer_role = str((run.request_payload or {}).get("reviewer_role") or "")
            judge_index = 0 if reviewer_role == "A" else 1
            judge_result = db.exec(
                select(models.PaperJudgeResult).where(
                    models.PaperJudgeResult.candidate_id == candidate.id,
                    models.PaperJudgeResult.judge_index == judge_index,
                )
            ).first()
            if judge_result is None:
                judge_result = models.PaperJudgeResult(
                    candidate_id=candidate.id,
                    judge_index=judge_index,
                )
            judge_result.provider_id = run.provider_id
            judge_result.model_name = run.model_name or ""
            judge_result.baseline_scores = artifact.baseline
            judge_result.optional_scores = artifact.optional
            judge_result.rationale = artifact.rationale
            judge_result.citations = artifact.citations
            db.add(judge_result)
        run.status = models.PaperAgentRunStatus.READY.value
        run.progress = 100
        run.stage = "completed"
        if (
            run.run_kind == models.PaperAgentRunKind.PROPOSAL_FINAL_REVIEW.value
            and isinstance(artifact, ProposalFinalReviewArtifact)
            and not artifact.ready_for_export
        ):
            run.message = "Proposal review requires revision"
        else:
            run.message = "Research stage result is ready"
        run.artifact_object_key = artifact_key
        run.error_code = None
        run.error = None
        run.updated_time = datetime.now(UTC)
        db.add(run)
        db.commit()
    return source_committed
