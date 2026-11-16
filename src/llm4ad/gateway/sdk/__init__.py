"""Remote evaluation contracts and gateway SDK."""

from llm4ad.evaluator.models import EvaluationArtifact, EvaluationResult, Metric, MetricType
from llm4ad.gateway.sdk.checkpoints import TaskCheckpoints
from llm4ad.gateway.sdk.contracts import EvaluationContract, GatewayEvaluator, RemoteJob
from llm4ad.gateway.sdk.errors import (
    EvaluationConfigurationError,
    EvaluationError,
    EvaluationPreparationError,
    EvaluationUnavailableError,
)
from llm4ad.gateway.sdk.journal import Reporter
from llm4ad.gateway.sdk.retries import retry_read

__all__ = [
    "EvaluationArtifact",
    "EvaluationContract",
    "EvaluationConfigurationError",
    "EvaluationError",
    "EvaluationPreparationError",
    "EvaluationResult",
    "EvaluationUnavailableError",
    "GatewayEvaluator",
    "Metric",
    "MetricType",
    "RemoteJob",
    "Reporter",
    "TaskCheckpoints",
    "retry_read",
]
