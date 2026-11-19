"""Owner-scoped decisions on pending paper source revisions."""

import uuid

from fastapi import HTTPException
from sqlmodel import Session, select

from app import models
from app.schemas.paper_history import PaperRevisionRejectResponse


def reject_revision_candidate(
    db: Session,
    current_user: models.User,
    candidate_id: uuid.UUID,
) -> PaperRevisionRejectResponse:
    """Reject a pending revision without changing canonical content or storage.

    Args:
        db: Session used to serialize the candidate decision.
        current_user: Owner rejecting the suggestion.
        candidate_id: Candidate to discard, including an obsolete draft.

    Returns:
        The rejected candidate identifier and terminal status.

    Raises:
        HTTPException: If the candidate is foreign, missing, applied, or terminal.
    """
    candidate = db.exec(
        select(models.PaperRevisionCandidate)
        .join(models.PaperWorkspace, models.PaperWorkspace.id == models.PaperRevisionCandidate.workspace_id)
        .where(
            models.PaperRevisionCandidate.id == candidate_id,
            models.PaperWorkspace.user_id == current_user.id,
        )
        .with_for_update(of=models.PaperRevisionCandidate)
        .execution_options(populate_existing=True)
    ).first()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Paper revision candidate not found")
    if candidate.accepted_source_version_id is not None or candidate.status in {"accepted", "applied"}:
        raise HTTPException(status_code=409, detail="Applied revision candidates cannot be rejected")
    if candidate.status == "rejected":
        return PaperRevisionRejectResponse(candidate_id=candidate.id, status="rejected")
    if candidate.status not in {"draft", "pending"}:
        raise HTTPException(status_code=409, detail="Only pending revision candidates can be rejected")
    candidate.status = "rejected"
    db.add(candidate)
    db.commit()
    return PaperRevisionRejectResponse(candidate_id=candidate.id, status="rejected")
