"""Optional structured evaluator failures without transport or scheduler assumptions."""

from __future__ import annotations

from llm4ad.evaluator.models import EvaluationArtifact


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
    """Keep work pending recovery when a resource or execution outcome is unavailable."""
