"""Reusable bounded search for model-authored structured parameters."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


def _finite_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be a numeric literal")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


@dataclass(frozen=True, slots=True)
class TunableParameter:
    """One model-authored continuous parameter and its bounded domain."""

    name: str
    initial: float
    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class ParameterSpace:
    """Validated fixed and tunable parameters declared by a candidate."""

    fixed: dict[str, float]
    tunable: tuple[TunableParameter, ...]

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, object],
        *,
        max_dimensions: int,
        reserved_names: set[str] | None = None,
    ) -> ParameterSpace:
        """Parse numeric constants and ``initial/lower/upper`` declarations."""
        if max_dimensions < 0:
            raise ValueError("max_dimensions must be non-negative")
        reserved = reserved_names or set()
        fixed: dict[str, float] = {}
        tunable: list[TunableParameter] = []

        for name, value in raw.items():
            if not isinstance(name, str) or not name.isidentifier():
                raise ValueError("parameter names must be valid identifiers")
            if name in reserved:
                raise ValueError(f"parameter name {name!r} is reserved")
            if isinstance(value, int | float) and not isinstance(value, bool):
                fixed[name] = _finite_number(value, label=f"parameter {name!r}")
                continue
            if not isinstance(value, Mapping):
                raise ValueError(
                    f"parameter {name!r} must be numeric or a tunable parameter object"
                )
            required = {"initial", "lower", "upper"}
            if set(value) != required:
                raise ValueError(
                    f"tunable parameter {name!r} must contain exactly "
                    "initial, lower, and upper"
                )
            initial = _finite_number(value["initial"], label=f"parameter {name!r} initial")
            lower = _finite_number(value["lower"], label=f"parameter {name!r} lower")
            upper = _finite_number(value["upper"], label=f"parameter {name!r} upper")
            if lower >= upper:
                raise ValueError(f"parameter {name!r} lower must be less than upper")
            if not lower <= initial <= upper:
                raise ValueError(f"parameter {name!r} initial must lie within its bounds")
            tunable.append(
                TunableParameter(
                    name=name,
                    initial=initial,
                    lower=lower,
                    upper=upper,
                )
            )

        if len(tunable) > max_dimensions:
            raise ValueError(
                f"candidate may declare at most {max_dimensions} tunable parameters"
            )
        return cls(fixed=fixed, tunable=tuple(tunable))

    @property
    def names(self) -> tuple[str, ...]:
        """Return tunable names in stable candidate declaration order."""
        return tuple(parameter.name for parameter in self.tunable)

    @property
    def initial(self) -> tuple[float, ...]:
        """Return the model-provided starting vector."""
        return tuple(parameter.initial for parameter in self.tunable)

    @property
    def bounds(self) -> tuple[tuple[float, float], ...]:
        """Return model-provided search bounds."""
        return tuple((parameter.lower, parameter.upper) for parameter in self.tunable)

    def resolve(
        self,
        values: Sequence[float] | Mapping[str, float] | None = None,
    ) -> dict[str, float]:
        """Combine fixed values with a concrete tunable vector."""
        if values is None:
            vector = self.initial
        elif isinstance(values, Mapping):
            if set(values) != set(self.names):
                raise ValueError("parameter overrides must match every tunable name")
            vector = tuple(values[name] for name in self.names)
        else:
            vector = tuple(values)
        if len(vector) != len(self.tunable):
            raise ValueError("parameter vector has the wrong dimension")

        resolved = dict(self.fixed)
        for parameter, value in zip(self.tunable, vector, strict=True):
            numeric = _finite_number(value, label=f"parameter {parameter.name!r}")
            if not parameter.lower <= numeric <= parameter.upper:
                raise ValueError(f"parameter {parameter.name!r} lies outside its bounds")
            resolved[parameter.name] = numeric
        return resolved

    def initial_value_patch(
        self,
        values: Mapping[str, float],
    ) -> dict[str, dict[str, float]]:
        """Build a deep patch that promotes optimized tunable values to initials.

        The returned mapping deliberately contains only existing tunable names and
        their ``initial`` field.  Candidate-owned bounds and all structural fields
        therefore remain unchanged when a generic candidate patch is applied.
        """
        tunable_names = set(self.names)
        missing = tunable_names.difference(values)
        unexpected = set(values).difference(tunable_names, self.fixed)
        if missing or unexpected:
            raise ValueError(
                "optimized parameters must contain every tunable name and no unknown names"
            )
        resolved = self.resolve({name: values[name] for name in self.names})
        return {
            parameter.name: {"initial": resolved[parameter.name]}
            for parameter in self.tunable
        }


@dataclass(frozen=True, slots=True)
class ParameterSearchResult:
    """Best feasible parameter assignment observed within a resource budget."""

    parameters: dict[str, float]
    objective: float
    evaluations: int
    invalid_evaluations: int
    feasible_evaluations: int
    stopped_reason: str
    strategy: str = "hybrid_qmc_pattern"


class _SearchStoppedError(RuntimeError):
    """Internal control flow for strict evaluation and wall-time budgets."""


def maximize_bounded_parameters(
    space: ParameterSpace,
    objective: Callable[[dict[str, float]], float | None],
    *,
    max_evaluations: int,
    seed: int,
    population_size: int = 8,
    deadline: float | None = None,
) -> ParameterSearchResult | None:
    """Maximize over model-authored bounds with deterministic global/local search.

    ``None`` from the objective denotes an invalid candidate point. Platform
    controls only cost and time; all mathematical bounds come from ``space``.
    Normalized low-discrepancy restarts cover disconnected feasible components,
    while shrinking pattern steps refine narrow feasible neighborhoods without
    relying on gradients or continuity.
    """
    if max_evaluations <= 0:
        raise ValueError("max_evaluations must be positive")
    if population_size < 4:
        raise ValueError("population_size must be at least 4")

    evaluations = 0
    invalid_evaluations = 0
    feasible_evaluations = 0
    best_parameters: dict[str, float] | None = None
    best_objective = float("-inf")
    stopped_reason = "completed"
    cache: dict[tuple[float, ...], float] = {}
    feasible_archive: list[tuple[float, tuple[float, ...]]] = []

    def evaluate_vector(vector: Sequence[float]) -> float:
        nonlocal evaluations, invalid_evaluations, feasible_evaluations
        nonlocal best_parameters, best_objective
        if evaluations >= max_evaluations:
            raise _SearchStoppedError("evaluation_budget")
        if deadline is not None and time.monotonic() >= deadline:
            raise _SearchStoppedError("timeout")
        key = tuple(float(value) for value in vector)
        cached = cache.get(key)
        if cached is not None:
            return cached
        parameters = space.resolve(key)
        value = objective(parameters)
        evaluations += 1
        if value is None or not math.isfinite(float(value)):
            invalid_evaluations += 1
            minimized = 1e100
        else:
            numeric = float(value)
            feasible_evaluations += 1
            feasible_archive.append((numeric, key))
            if numeric > best_objective:
                best_objective = numeric
                best_parameters = parameters
            minimized = -numeric
        cache[key] = minimized
        return minimized

    try:
        evaluate_vector(space.initial)
        if space.tunable and evaluations < max_evaluations:
            import numpy as np
            from scipy.stats import qmc

            dimension = len(space.tunable)
            lower = np.asarray([bound[0] for bound in space.bounds], dtype=float)
            width = np.asarray([bound[1] - bound[0] for bound in space.bounds], dtype=float)
            initial_unit = (np.asarray(space.initial, dtype=float) - lower) / width
            rng = np.random.default_rng(seed)

            def evaluate_unit(unit_vector: Sequence[float]) -> float:
                normalized = np.clip(np.asarray(unit_vector, dtype=float), 0.0, 1.0)
                return evaluate_vector(lower + normalized * width)

            remaining = max_evaluations - evaluations
            global_fraction = 0.2 if dimension * 4 > max_evaluations else 0.35
            global_target = min(
                remaining,
                max(population_size * 2, int(max_evaluations * global_fraction)),
            )
            if global_target > 0:
                exponent = max(0, math.ceil(math.log2(global_target)))
                global_points = qmc.Sobol(
                    d=dimension,
                    scramble=True,
                    seed=seed,
                ).random_base2(exponent)[:global_target]
                for point in global_points:
                    evaluate_unit(point)

            local_budget = max_evaluations - evaluations
            local_attempt = 0
            max_attempts = max(32, local_budget * 8)
            while evaluations < max_evaluations and local_attempt < max_attempts:
                progress = min(1.0, local_attempt / max(1, local_budget - 1))
                step = 0.25 * (1e-5 / 0.25) ** progress
                if feasible_archive:
                    feasible_archive.sort(key=lambda item: item[0], reverse=True)
                    anchor_raw = feasible_archive[local_attempt % min(3, len(feasible_archive))][1]
                    anchor = (np.asarray(anchor_raw, dtype=float) - lower) / width
                else:
                    anchor = initial_unit

                mode = local_attempt % 5
                if mode < 4:
                    coordinate = (local_attempt // 2) % dimension
                    direction = 1.0 if local_attempt % 2 == 0 else -1.0
                    proposal = anchor.copy()
                    proposal[coordinate] += direction * step
                else:
                    direction_vector = rng.normal(size=dimension)
                    norm = float(np.max(np.abs(direction_vector)))
                    if norm <= 0.0:
                        local_attempt += 1
                        continue
                    proposal = anchor + step * direction_vector / norm

                evaluate_unit(proposal)
                local_attempt += 1
            if evaluations >= max_evaluations:
                stopped_reason = "evaluation_budget"
    except _SearchStoppedError as exc:
        stopped_reason = str(exc)

    if best_parameters is None:
        return None
    return ParameterSearchResult(
        parameters=best_parameters,
        objective=best_objective,
        evaluations=evaluations,
        invalid_evaluations=invalid_evaluations,
        feasible_evaluations=feasible_evaluations,
        stopped_reason=stopped_reason,
    )
