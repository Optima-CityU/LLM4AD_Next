"""Public task evaluation data without storage credentials or local paths."""

from llm4ad.evaluator.models import EvaluationRecord
from pydantic import BaseModel, Field


class EvaluationSummary(BaseModel):
    """Describe one evaluation in the task workbench."""

    id: str
    algorithm_id: str = ""
    algorithm_name: str = ""
    execution_id: str = ""
    evaluation_profile: str = "standard"
    instance_id: str = ""
    source_fingerprint: str = ""
    origin: str = "local"
    state: str = "completed"
    created_at: str
    storage_state: str = "saved"
    score: float | None = None
    artifact_count: int = 0
    transfer_pending: bool = False
    gateway_name: str | None = None
    gateway_online: bool | None = None
    progress: dict = Field(default_factory=dict)


class EvaluationList(BaseModel):
    """Return a filtered page of immutable evaluation identities."""

    data: list[EvaluationSummary]
    count: int
    page: int
    page_size: int
    storage_available: bool = True


class EvaluationDetail(BaseModel):
    """Combine a saved record with optional live gateway progress."""

    record: EvaluationRecord
    summary: EvaluationSummary
    available_artifact_ids: list[str] = Field(default_factory=list)
