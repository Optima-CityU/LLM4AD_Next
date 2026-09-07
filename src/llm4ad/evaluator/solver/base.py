"""Contracts shared by solver-backed evaluators and problem adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SolverContext:
    """Trusted runtime context passed to a problem adapter."""

    backend: Any
    timeout: float
    project_root: str
    data_path: str = ""
    evaluation_profile: str = "standard"


@dataclass(slots=True)
class SolverRunResult:
    """Raw solver result, before independent problem validation."""

    status: str
    solution: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    candidate_patch: dict[str, Any] = field(default_factory=dict)
    """Trusted deep patch whose values should be inherited by descendants."""
    error_message: str | None = None


@dataclass(slots=True)
class SolverValidationResult:
    """Independent validation and metric extraction result."""

    valid: bool
    metrics: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


class BaseSolverAdapter(ABC):
    """Translate one structured candidate into a model and validate its solution."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Store problem-specific, trusted adapter configuration."""
        self.config = dict(config or {})

    @abstractmethod
    def solve(self, candidate: Mapping[str, Any], context: SolverContext) -> SolverRunResult:
        """Build and solve a mathematical model for ``candidate``."""

    @abstractmethod
    def validate(
        self,
        candidate: Mapping[str, Any],
        solution: Any,
        context: SolverContext,
    ) -> SolverValidationResult:
        """Validate the solution without trusting solver feasibility claims."""
