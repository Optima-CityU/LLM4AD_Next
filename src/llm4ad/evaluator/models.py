"""Dependency-light evaluation metrics and result models."""

import mimetypes
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from llm4ad.evaluator.behavior import BehaviorData


def artifact_media_type(name: str, declared: str = "application/octet-stream") -> str:
    """Normalize an explicit media type or infer one for a generic declaration."""
    normalized = declared.split(";", 1)[0].strip().lower()
    if not normalized or normalized == "application/octet-stream":
        return mimetypes.guess_type(name)[0] or "application/octet-stream"
    return normalized


class MetricType(Enum):
    """Types of evaluation metrics."""

    MINIMIZE = "minimize"  # Lower is better
    MAXIMIZE = "maximize"  # Higher is better


class Metric(BaseModel):
    """A single metric definition."""

    name: str
    type: MetricType = MetricType.MAXIMIZE
    weight: float = 1.0  # For multi-objective optimization
    description: str = ""

    model_config = ConfigDict(frozen=True)


class EvaluationArtifact(BaseModel):
    """Declare a file produced by an evaluator for durable retention.

    Paths are relative to ``EvalContext.artifact_dir`` (``RemoteJob.artifact_dir``
    for gateway adapters) or absolute paths inside that directory. Declaring a file never changes the evaluation score.
    """

    name: str
    path: str
    media_type: str = "application/octet-stream"
    description: str = ""


class EvaluationFile(BaseModel):
    """Describe an immutable saved file without exposing local paths."""

    id: str
    name: str
    sha256: str
    size: int
    media_type: str = "application/octet-stream"
    description: str = ""


class EvaluationRecord(BaseModel):
    """Store one candidate, source version, profile and instance evaluation."""

    id: str
    task_id: str = ""
    execution_id: str = ""
    algorithm_id: str = ""
    algorithm_name: str = ""
    source_fingerprint: str = ""
    evaluation_profile: str = "standard"
    instance_id: str = ""
    origin: str = "local"
    state: str = "completed"
    created_at: str
    storage_state: str = "pending"
    storage_errors: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[EvaluationFile] = Field(default_factory=list)
    remote_job_id: str = ""
    remote_attempt_id: str = ""


class EvaluationResult(BaseModel):
    """Evaluation result container.

    Contains the computed score, detailed metrics, and any extra metadata
    for monitoring, logging, and algorithm analysis.

    Users only need to provide:
    - `metrics`: dict of metric name -> value (supports multiple objectives/metrics)
    - `score`: Primary aggregated score (computed by base class from metrics)
    Everything else is optional with good defaults.

    Extra information that is not used for evolution (monitoring, logs, analysis)
    should go into `metadata`.
    """

    score: float = 0.0  # Primary score for evolution (computed from metrics)
    metrics: dict[str, float]  # All evaluation metrics (objectives + secondary metrics)
    monitor_metrics: dict[str, float] = {}  # Metrics for monitoring (subset of metrics)
    metadata: dict[str, Any] = {}  # Extra info: monitoring, logs, analysis (optional)
    evolution_feedback: dict[str, Any] = Field(default_factory=dict)
    """Compact, safe evaluator findings that descendants may reuse."""
    success: bool = True  # Whether evaluation succeeded, defaults to True
    error_message: str | None = None  # Error message if failed, optional
    duration_ms: float = 0.0  # Evaluation duration in milliseconds, optional
    behavior: BehaviorData | None = None  # Behavior data from evaluation (optional)
    artifacts: list[EvaluationArtifact] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def candidate_summary(self) -> dict[str, Any]:
        """Keep candidate logs compact once a full evaluation record exists.

        Returns:
            Scores, safe feedback and durable record references. Legacy callers
            without record capture retain the original complete serialization.
        """
        if not self.metadata.get("evaluation_records"):
            return self.model_dump(mode="json")
        summary = self.model_dump(
            mode="json",
            include={
                "score",
                "metrics",
                "monitor_metrics",
                "success",
                "error_message",
                "duration_ms",
                "evolution_feedback",
            },
        )
        summary["metadata"] = {
            name: self.metadata[name]
            for name in (
                "evaluation_records",
                "remote_job_id",
                "remote_attempt_id",
                "num_instances",
                "successful_instances",
            )
            if name in self.metadata
        }
        return summary
