"""Generic solver-backed evaluation primitives."""

from llm4ad.evaluator.solver.base import (
    BaseSolverAdapter,
    SolverContext,
    SolverRunResult,
    SolverValidationResult,
)
from llm4ad.evaluator.solver.evaluator import SolverEvaluator
from llm4ad.evaluator.solver.parameter_search import (
    ParameterSearchResult,
    ParameterSpace,
    TunableParameter,
    maximize_bounded_parameters,
)

__all__ = [
    "BaseSolverAdapter",
    "ParameterSearchResult",
    "ParameterSpace",
    "SolverContext",
    "SolverEvaluator",
    "SolverRunResult",
    "SolverValidationResult",
    "TunableParameter",
    "maximize_bounded_parameters",
]
