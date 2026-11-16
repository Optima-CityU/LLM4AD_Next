"""Optional structured evaluator failures without transport or scheduler assumptions."""

from __future__ import annotations

from typing import Literal

import aiohttp
from pydantic import ValidationError

from llm4ad.evaluator.models import EvaluationArtifact

FailureCategory = Literal["candidate", "infrastructure", "evaluator", "configuration"]


def failure_category(
    metadata: dict, error: BaseException | None = None, *, default: FailureCategory = "evaluator"
) -> FailureCategory:
    """Classify failures consistently, honoring explicit evaluator verdicts first.

    Args:
        metadata: Structured failure information, including legacy verdict flags.
        error: Original exception, if an operation raised rather than returned.
        default: Category for failures without an explicit verdict or known type.

    Returns:
        The category used by pool routing and diagnostic metadata.
    """
    explicit = metadata.get("failure_category")
    if isinstance(explicit, str) and explicit in {"candidate", "infrastructure", "evaluator", "configuration"}:
        return explicit
    if metadata.get("candidate_failure") is True:
        return "candidate"
    if metadata.get("infrastructure_failure") is True:
        return "infrastructure"
    if isinstance(error, (OSError, TimeoutError, aiohttp.ClientError, EvaluationUnavailableError)):
        return "infrastructure"
    if isinstance(error, (EvaluationConfigurationError, ValidationError, ImportError)):
        return "configuration"
    return default


class EvaluationError(Exception):
    """Report a known evaluation failure with domain context and diagnostic files."""

    def __init__(
        self,
        message: str,
        *,
        metadata: dict | None = None,
        evolution_feedback: dict | None = None,
        artifacts: list[EvaluationArtifact] | None = None,
    ):
        """Keep diagnostic information for the SDK's normal result/file pipeline.

        Args:
            message: Human-readable reason for this failed evaluation.
            metadata: JSON-compatible domain context such as the failing stage.
            evolution_feedback: Findings the algorithm designer may reuse.
            artifacts: Diagnostic files relative to the job artifact directory.
        """
        super().__init__(message)
        self.metadata = dict(metadata or {})
        self.evolution_feedback = dict(evolution_feedback or {})
        self.artifacts = list(artifacts or [])


class EvaluationUnavailableError(EvaluationError):
    """Retry a transient resource failure within the SDK's original execution budget."""


class EvaluationPreparationError(EvaluationError, RuntimeError):
    """Fail a local preparation known not to have submitted external execution."""


class EvaluationConfigurationError(EvaluationError, ValueError):
    """Report an invalid evaluator configuration that cannot improve on failover."""
