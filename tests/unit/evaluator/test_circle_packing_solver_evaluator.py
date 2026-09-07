"""Tests for the solver-assisted circle-packing expression example."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from llm4ad.config.evaluator import SolverEvaluatorConfig
from llm4ad.config.schema import EvalContext
from llm4ad.evaluator.solver.base import SolverContext
from llm4ad.evaluator.solver.candidate import load_candidate
from llm4ad.evaluator.solver.evaluator import SolverEvaluator

EXAMPLE_DIR = Path(__file__).resolve().parents[3] / "examples" / "applications" / "alphaevolve_math_benchmark"
SOLVER_PROJECT = EXAMPLE_DIR / "circle_packing" / "solver"


def _load_adapter_class():
    module_path = EXAMPLE_DIR / "circle_packing" / "solver_adapter.py"
    spec = importlib.util.spec_from_file_location("circle_packing_solver_adapter", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CirclePackingScipAdapter


def test_expression_candidate_generates_exactly_26_finite_centers() -> None:
    """The baseline expression artifact should compile to the locked problem size."""
    adapter = _load_adapter_class()()
    candidate = load_candidate(SOLVER_PROJECT / "model_spec.py")

    centers = adapter.build_centers(candidate)

    assert centers.shape == (26, 2)
    assert centers.dtype.kind == "f"
    assert pytest.approx(float(centers[0, 0])) == 0.5
    assert pytest.approx(float(centers[0, 1])) == 0.5


def test_expression_candidate_accepts_model_authored_tunable_parameters() -> None:
    """Circle layouts may expose their own bounded continuous search variables."""
    adapter = _load_adapter_class()({"max_tunable_parameters": 8})
    candidate = {
        "parameters": {
            "cx": 0.5,
            "cy": 0.5,
            "dx": {"initial": 0.08, "lower": 0.06, "upper": 0.18},
            "dy": {"initial": 0.08, "lower": 0.06, "upper": 0.2},
        },
        "groups": [
            {
                "count": count,
                "x": "cx + (i - (count - 1) / 2) * dx + (group % 2) * dx / 2",
                "y": "cy + (group - 2) * dy",
            }
            for count in (5, 5, 6, 5, 5)
        ],
    }

    centers = adapter.build_centers(candidate, {"dx": 0.16, "dy": 0.17})

    assert centers.shape == (26, 2)
    assert float(centers[:, 0].max() - centers[:, 0].min()) > 0.7


def test_invalid_expression_is_reported_before_parameter_search() -> None:
    """Static formula errors should reach repair logic instead of looking infeasible."""
    adapter = _load_adapter_class()(
        {
            "num_circles": 26,
            "max_tunable_parameters": 8,
            "parameter_search_evaluations": 80,
            "parameter_search_population": 6,
        }
    )
    candidate = {
        "parameters": {
            "cx": {"initial": 0.5, "lower": 0.4, "upper": 0.6},
            "cy": 0.5,
        },
        "groups": [
            {
                "count": count,
                "x": "cx + (i / count))",
                "y": "cy + (group - 2) * 0.1",
            }
            for count in (5, 5, 6, 5, 5)
        ],
    }
    context = SolverContext(backend=object(), timeout=5.0, project_root=str(SOLVER_PROJECT))

    with pytest.raises(
        ValueError,
        match=r"Candidate expression validation failed for group 0 x: .*unmatched.*\)",
    ):
        adapter.solve(candidate, context)


@pytest.mark.asyncio
async def test_parameter_search_improves_layout_before_radius_optimization() -> None:
    """The evaluator searches model-defined geometry, not only radii at fixed centers."""
    pytest.importorskip("pyscipopt")
    candidate = {
        "parameters": {
            "cx": 0.5,
            "cy": 0.5,
            "dx": {"initial": 0.07, "lower": 0.06, "upper": 0.18},
            "dy": {"initial": 0.07, "lower": 0.06, "upper": 0.2},
        },
        "groups": [
            {
                "count": count,
                "x": "cx + (i - (count - 1) / 2) * dx + (group % 2) * dx / 2",
                "y": "cy + (group - 2) * dy",
            }
            for count in (5, 5, 6, 5, 5)
        ],
    }
    adapter = _load_adapter_class()(
        {
            "num_circles": 26,
            "max_tunable_parameters": 8,
            "parameter_search_evaluations": 80,
            "parameter_search_population": 6,
            "parameter_search_seed": 7,
        }
    )
    from llm4ad.evaluator.solver.backend import create_backend

    context = SolverContext(
        backend=create_backend("scip"),
        timeout=20.0,
        project_root=str(SOLVER_PROJECT),
    )
    initial = adapter.solve_fixed_parameters(candidate, context)

    searched = adapter.solve(candidate, context)

    assert initial.solution is not None
    assert searched.solution is not None
    assert sum(searched.solution["radii"]) > sum(initial.solution["radii"]) + 0.2
    assert searched.status == "feasible"
    assert searched.metadata["parameter_search_evaluations"] <= 80
    assert searched.metadata["tunable_parameters"] == 2


def test_independent_validator_rejects_overlapping_solver_solution() -> None:
    """Independent geometry checks should reject a falsely feasible solution."""
    adapter = _load_adapter_class()()
    candidate = load_candidate(SOLVER_PROJECT / "model_spec.py")
    context = SolverContext(backend=object(), timeout=5.0, project_root=str(SOLVER_PROJECT))
    invalid_solution = {
        "centers": [[0.5, 0.5]] * 26,
        "radii": [0.2] * 26,
    }

    validation = adapter.validate(candidate, invalid_solution, context)

    assert validation.valid is False
    assert "overlap" in (validation.error_message or "").lower()


def test_solver_example_config_uses_generic_solver_evaluator() -> None:
    """The separate example should use the reusable solver evaluator contract."""
    raw = yaml.safe_load(
        (EXAMPLE_DIR / "circle_packing" / "solver_config.yaml").read_text(encoding="utf-8")
    )

    config = SolverEvaluatorConfig.model_validate(raw["evaluator"])

    assert config.type == "solver"
    assert config.backend == "scip"
    assert config.candidate_file == "model_spec.py"
    assert config.adapter.endswith("CirclePackingScipAdapter")
    assert [metric.name for metric in config.metrics] == ["sum_radii", "validity"]
    assert config.adapter_config["max_tunable_parameters"] == 64
    assert config.adapter_config["parameter_search_evaluations"] > 1
    assert config.adapter_config["elite_parameter_search_multiplier"] == 4.0
    assert config.adapter_config["geometry_safety_margin"] == pytest.approx(1e-9)
    assert config.max_retries == 1
    assert raw["evolution"]["type"] == "diverse_island_ga"
    assert raw["evolution"]["elite_reevaluation_count"] == 1


def test_example_does_not_expose_a_target_answer_to_the_model() -> None:
    """The model receives the problem contract without a target or known construction."""
    visible_text = "\n".join(
        [
            (EXAMPLE_DIR / "circle_packing" / "solver_config.yaml").read_text(encoding="utf-8"),
            (SOLVER_PROJECT / "model_spec.py").read_text(encoding="utf-8"),
        ]
    )

    lowered = visible_text.lower()
    assert "target score" not in lowered
    assert "known best" not in lowered
    assert "reference solution" not in lowered


def test_baseline_demonstrates_model_authored_parameter_domains() -> None:
    """The seed teaches the model to expose geometry for downstream optimization."""
    candidate = load_candidate(SOLVER_PROJECT / "model_spec.py")

    tunable = candidate["parameters"]["middle_radius"]

    assert tunable == {
        "initial": 0.25,
        "lower": 0.08,
        "upper": 0.42,
    }


@pytest.mark.asyncio
async def test_scip_example_baseline_returns_a_verified_feasible_solution() -> None:
    """The real backend should solve and verify the baseline expression candidate."""
    pytest.importorskip("pyscipopt")
    raw = yaml.safe_load(
        (EXAMPLE_DIR / "circle_packing" / "solver_config.yaml").read_text(encoding="utf-8")
    )
    config = SolverEvaluatorConfig.model_validate(raw["evaluator"])
    evaluator = SolverEvaluator(config, config_dir=str(EXAMPLE_DIR))

    result = await evaluator.evaluate(EvalContext(project_root=str(SOLVER_PROJECT), timeout=10.0))

    assert result.success is True
    assert result.metrics["validity"] == 1.0
    assert result.metrics["sum_radii"] > 0.0
    assert result.score == pytest.approx(result.metrics["sum_radii"])
    assert result.metadata["solver_status"] == "feasible"
    assert result.metadata["solver"]["inner_solver_status"] == "optimal"
    assert result.metrics["solver_optimal"] == 0.0
    assert result.metadata["solver"]["parameter_search_best_objective"] > (
        result.metadata["solver"]["parameter_search_initial_objective"] + 0.01
    )
