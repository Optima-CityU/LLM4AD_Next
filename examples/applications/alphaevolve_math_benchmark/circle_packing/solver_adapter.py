"""Solver adapter for expression-based packing of 26 circles."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

import numpy as np
from numpy.typing import NDArray

from llm4ad.evaluator.solver.base import (
    BaseSolverAdapter,
    SolverContext,
    SolverRunResult,
    SolverValidationResult,
)
from llm4ad.evaluator.solver.expression import (
    ExpressionError,
    evaluate_numeric_expression,
    validate_numeric_expression,
)
from llm4ad.evaluator.solver.parameter_search import (
    ParameterSpace,
    maximize_bounded_parameters,
)


class CirclePackingScipAdapter(BaseSolverAdapter):
    """Search model-authored center families, optimize radii, and verify geometry."""

    _RESERVED_PARAMETER_NAMES = {
        "i",
        "count",
        "group",
        "pi",
        "e",
        "abs",
        "cos",
        "sin",
        "sqrt",
        "min",
        "max",
    }

    @property
    def num_circles(self) -> int:
        """Return the benchmark's locked circle count."""
        value = self.config.get("num_circles", 26)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("num_circles must be an integer")
        return value

    @property
    def tolerance(self) -> float:
        """Return the independent geometry validation tolerance."""
        value = self.config.get("geometry_tolerance", 1e-10)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("geometry_tolerance must be numeric")
        return float(value)

    def _positive_int_config(self, name: str, default: int) -> int:
        value = self.config.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    def _nonnegative_float_config(self, name: str, default: float) -> float:
        value = self.config.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise ValueError(f"{name} must be a non-negative number")
        return float(value)

    def _make_radii_strictly_feasible(
        self,
        centers: NDArray[np.float64],
        radii: NDArray[np.float64],
    ) -> tuple[NDArray[np.float64], float]:
        """Project solver-tolerant radii into the independent validator's interior."""
        margin = self._nonnegative_float_config(
            "geometry_safety_margin",
            max(1e-9, self.tolerance * 4.0),
        )
        adjusted = np.maximum(radii.astype(np.float64, copy=True), 0.0)
        border_limits = np.minimum.reduce(
            [centers[:, 0], centers[:, 1], 1.0 - centers[:, 0], 1.0 - centers[:, 1]]
        )
        adjusted = np.minimum(adjusted, np.maximum(0.0, border_limits - margin))

        for first in range(self.num_circles):
            for second in range(first + 1, self.num_circles):
                distance = float(np.linalg.norm(centers[first] - centers[second]))
                excess = float(adjusted[first] + adjusted[second] - max(0.0, distance - margin))
                if excess <= 0.0:
                    continue
                larger, smaller = (
                    (first, second)
                    if adjusted[first] >= adjusted[second]
                    else (second, first)
                )
                reduction = min(float(adjusted[larger]), excess)
                adjusted[larger] -= reduction
                excess -= reduction
                if excess > 0.0:
                    adjusted[smaller] = max(0.0, float(adjusted[smaller]) - excess)

        total_adjustment = float(np.sum(np.maximum(0.0, radii - adjusted)))
        return adjusted, total_adjustment

    def parameter_space(self, candidate: Mapping[str, Any]) -> ParameterSpace:
        """Parse the candidate's own fixed values and bounded search variables."""
        raw_parameters = candidate.get("parameters", {})
        if not isinstance(raw_parameters, Mapping):
            raise ValueError("parameters must be an object")
        return ParameterSpace.from_mapping(
            raw_parameters,
            max_dimensions=self._positive_int_config("max_tunable_parameters", 64),
            reserved_names=self._RESERVED_PARAMETER_NAMES,
        )

    def validate_candidate(self, candidate: Mapping[str, Any]) -> None:
        """Reject static candidate defects before spending the parameter-search budget."""
        raw_groups = candidate.get("groups")
        if not isinstance(raw_groups, list) or not raw_groups:
            raise ValueError("Candidate validation failed: groups must be a non-empty list")

        space = self.parameter_space(candidate)
        variable_names = set(space.fixed) | set(space.names) | {"i", "count", "group"}
        total_centers = 0
        for group_index, group in enumerate(raw_groups):
            if not isinstance(group, Mapping):
                raise ValueError(f"Candidate validation failed: group {group_index} must be an object")
            count = group.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise ValueError(
                    f"Candidate validation failed: group {group_index} count "
                    "must be a positive integer"
                )
            total_centers += count
            for axis in ("x", "y"):
                expression = group.get(axis)
                if not isinstance(expression, str):
                    raise ValueError(
                        f"Candidate validation failed: group {group_index} {axis} "
                        "must be an expression"
                    )
                try:
                    validate_numeric_expression(expression, variable_names)
                except ExpressionError as exc:
                    raise ValueError(
                        "Candidate expression validation failed for "
                        f"group {group_index} {axis}: {exc}"
                    ) from exc

        if total_centers != self.num_circles:
            raise ValueError(
                "Candidate validation failed: formula groups must generate exactly "
                f"{self.num_circles} centers, got {total_centers}"
            )

    def build_centers(
        self,
        candidate: Mapping[str, Any],
        parameter_values: Mapping[str, float] | None = None,
    ) -> NDArray[np.float64]:
        """Evaluate group formulas using fixed or optimizer-selected parameters."""
        raw_groups = candidate.get("groups")
        if not isinstance(raw_groups, list) or not raw_groups:
            raise ValueError("groups must be a non-empty list")
        space = self.parameter_space(candidate)
        parameters = space.resolve(parameter_values)

        centers: list[list[float]] = []
        for group_index, group in enumerate(raw_groups):
            if not isinstance(group, Mapping):
                raise ValueError(f"group {group_index} must be an object")
            count = group.get("count")
            x_expression = group.get("x")
            y_expression = group.get("y")
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise ValueError(f"group {group_index} count must be a positive integer")
            if not isinstance(x_expression, str) or not isinstance(y_expression, str):
                raise ValueError(f"group {group_index} x and y must be expressions")

            for index in range(count):
                variables = {
                    **parameters,
                    "i": index,
                    "count": count,
                    "group": group_index,
                }
                centers.append(
                    [
                        evaluate_numeric_expression(x_expression, variables),
                        evaluate_numeric_expression(y_expression, variables),
                    ]
                )

        array: NDArray[np.float64] = np.asarray(centers, dtype=np.float64)
        if array.shape != (self.num_circles, 2):
            raise ValueError(
                f"formula groups must generate exactly {self.num_circles} centers, got {len(centers)}"
            )
        if not np.isfinite(array).all():
            raise ValueError("center formulas must produce only finite values")
        if np.any(array < 0.0) or np.any(array > 1.0):
            raise ValueError("center formulas must remain inside the unit square")
        return array

    def solve_fixed_parameters(
        self,
        candidate: Mapping[str, Any],
        context: SolverContext,
        parameter_values: Mapping[str, float] | None = None,
        *,
        timeout: float | None = None,
    ) -> SolverRunResult:
        """Solve the exact maximum-radii LP for one concrete parameter vector."""
        centers = self.build_centers(candidate, parameter_values)
        model = context.backend.create_model(
            "circle_packing_parameterized_centers",
            timeout=max(0.01, float(timeout if timeout is not None else context.timeout)),
        )
        border_limits = np.minimum.reduce(
            [centers[:, 0], centers[:, 1], 1.0 - centers[:, 0], 1.0 - centers[:, 1]]
        )
        radii = [
            model.addVar(name=f"r_{index}", vtype="C", lb=0.0, ub=float(border_limits[index]))
            for index in range(self.num_circles)
        ]
        for first in range(self.num_circles):
            for second in range(first + 1, self.num_circles):
                distance = float(np.linalg.norm(centers[first] - centers[second]))
                model.addCons(
                    radii[first] + radii[second] <= distance,
                    name=f"disjoint_{first}_{second}",
                )

        model.setObjective(sum(radii), sense="maximize")
        model.optimize()
        status = str(model.getStatus())
        best_solution = model.getBestSol()
        if best_solution is None:
            return SolverRunResult(
                status=status,
                error_message=f"The solver found no feasible radius assignment ({status})",
                metadata=self._solver_metadata(model),
            )

        raw_radii: NDArray[np.float64] = np.asarray(
            [float(model.getSolVal(best_solution, radius)) for radius in radii],
            dtype=np.float64,
        )
        optimized_radii, feasibility_adjustment = self._make_radii_strictly_feasible(
            centers,
            raw_radii,
        )
        return SolverRunResult(
            status=status,
            solution={
                "centers": centers.tolist(),
                "radii": optimized_radii.tolist(),
                "parameters": self.parameter_space(candidate).resolve(parameter_values),
            },
            metadata={
                **self._solver_metadata(model),
                "feasibility_adjustment": feasibility_adjustment,
            },
        )

    def solve(self, candidate: Mapping[str, Any], context: SolverContext) -> SolverRunResult:
        """Search the model-authored geometry domain and solve radii at every layout."""
        self.validate_candidate(candidate)
        space = self.parameter_space(candidate)
        if not space.tunable:
            return self.solve_fixed_parameters(candidate, context)

        max_evaluations = self._positive_int_config("parameter_search_evaluations", 120)
        if context.evaluation_profile == "elite":
            raw_multiplier = self.config.get("elite_parameter_search_multiplier", 4.0)
            if isinstance(raw_multiplier, bool) or not isinstance(raw_multiplier, int | float):
                raise ValueError("elite_parameter_search_multiplier must be numeric")
            multiplier = float(raw_multiplier)
            if not math.isfinite(multiplier) or multiplier < 1.0:
                raise ValueError("elite_parameter_search_multiplier must be at least 1")
            max_evaluations = max(max_evaluations, math.ceil(max_evaluations * multiplier))
        population_size = self._positive_int_config("parameter_search_population", 6)
        seed = self.config.get("parameter_search_seed", 42)
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("parameter_search_seed must be an integer")

        deadline = time.monotonic() + float(context.timeout)
        cached_runs: dict[tuple[float, ...], SolverRunResult] = {}
        initial_objective: float | None = None

        def objective(parameters: dict[str, float]) -> float | None:
            nonlocal initial_objective
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            key = tuple(parameters[name] for name in space.names)
            try:
                run = self.solve_fixed_parameters(
                    candidate,
                    context,
                    {name: parameters[name] for name in space.names},
                    timeout=remaining,
                )
            except ValueError:
                return None
            if run.solution is None:
                return None
            cached_runs[key] = run
            value = float(sum(run.solution["radii"]))
            if initial_objective is None:
                initial_objective = value
            return value

        search = maximize_bounded_parameters(
            space,
            objective,
            max_evaluations=max_evaluations,
            seed=seed,
            population_size=population_size,
            deadline=deadline,
        )
        if search is None:
            return SolverRunResult(
                status="search_failed",
                error_message=(
                    "No feasible geometry was found inside the candidate-declared parameter domain"
                ),
                metadata={
                    "tunable_parameters": len(space.tunable),
                    "parameter_search_evaluations": max_evaluations,
                },
            )

        best_key = tuple(search.parameters[name] for name in space.names)
        best_run = cached_runs.get(best_key)
        if best_run is None:
            remaining = max(0.01, deadline - time.monotonic())
            best_run = self.solve_fixed_parameters(
                candidate,
                context,
                {name: search.parameters[name] for name in space.names},
                timeout=remaining,
            )
        metadata = {
            **best_run.metadata,
            "inner_solver_status": best_run.status,
            "tunable_parameters": len(space.tunable),
            "parameter_search_evaluations": search.evaluations,
            "parameter_search_invalid_evaluations": search.invalid_evaluations,
            "parameter_search_feasible_evaluations": search.feasible_evaluations,
            "parameter_search_feasible_ratio": (
                search.feasible_evaluations / search.evaluations
                if search.evaluations
                else 0.0
            ),
            "parameter_search_stopped_reason": search.stopped_reason,
            "parameter_search_strategy": search.strategy,
            "parameter_search_initial_objective": initial_objective,
            "parameter_search_best_objective": search.objective,
            "optimized_parameters": search.parameters,
            "evaluation_profile": context.evaluation_profile,
        }
        return SolverRunResult(
            status="feasible",
            solution=best_run.solution,
            metadata=metadata,
            candidate_patch={
                "parameters": space.initial_value_patch(search.parameters),
            },
        )

    @staticmethod
    def _solver_metadata(model: Any) -> dict[str, float | int]:
        metadata: dict[str, float | int] = {
            "solve_time_seconds": float(model.getSolvingTime()),
            "nodes": int(model.getNTotalNodes()),
        }
        gap = float(model.getGap())
        if math.isfinite(gap):
            metadata["gap"] = gap
        return metadata

    def validate(
        self,
        candidate: Mapping[str, Any],
        solution: Any,
        context: SolverContext,
    ) -> SolverValidationResult:
        """Independently recompute every geometry constraint and objective."""
        del candidate, context
        if not isinstance(solution, Mapping):
            return SolverValidationResult(valid=False, error_message="solution must be an object")
        try:
            centers = np.asarray(solution.get("centers"), dtype=float)
            radii = np.asarray(solution.get("radii"), dtype=float)
        except (TypeError, ValueError) as exc:
            return SolverValidationResult(valid=False, error_message=f"invalid solution arrays: {exc}")

        if centers.shape != (self.num_circles, 2):
            return SolverValidationResult(valid=False, error_message="centers have an invalid shape")
        if radii.shape != (self.num_circles,):
            return SolverValidationResult(valid=False, error_message="radii have an invalid shape")
        if not np.isfinite(centers).all() or not np.isfinite(radii).all():
            return SolverValidationResult(valid=False, error_message="solution values must be finite")
        if np.any(radii < 0.0):
            return SolverValidationResult(valid=False, error_message="radii must be non-negative")

        lower = centers - radii[:, None]
        upper = centers + radii[:, None]
        if np.any(lower < -self.tolerance) or np.any(upper > 1.0 + self.tolerance):
            return SolverValidationResult(
                valid=False, error_message="one or more circles lie outside the unit square"
            )

        for first in range(self.num_circles):
            for second in range(first + 1, self.num_circles):
                distance = float(np.linalg.norm(centers[first] - centers[second]))
                if distance + self.tolerance < float(radii[first] + radii[second]):
                    return SolverValidationResult(
                        valid=False,
                        error_message=f"circles {first} and {second} overlap",
                    )

        objective = float(np.sum(radii))
        return SolverValidationResult(
            valid=True,
            metrics={"sum_radii": objective, "validity": 1.0},
            metadata={"num_circles": self.num_circles},
        )
