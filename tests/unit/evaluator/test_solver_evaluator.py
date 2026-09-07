"""Tests for the generic solver-backed evaluator contract."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from llm4ad.config.evaluator import SolverEvaluatorConfig, SolverMetricConfig
from llm4ad.config.schema import EvalContext
from llm4ad.evaluator.solver.candidate import (
    CandidateLoadError,
    apply_candidate_patch,
    load_candidate,
)
from llm4ad.evaluator.solver.evaluator import SolverEvaluator
from llm4ad.evaluator.solver.expression import ExpressionError, evaluate_numeric_expression


def test_solver_config_exposes_adapter_candidate_and_metrics() -> None:
    """The config should expose only the generic solver-adapter contract."""
    config = SolverEvaluatorConfig(
        adapter="problem_adapter.py:DemoAdapter",
        candidate_file="model_spec.py",
        candidate_symbol="MODEL_SPEC",
        metrics=[SolverMetricConfig(name="objective", type="maximize")],
    )

    assert config.type == "solver"
    assert config.backend == "scip"
    assert config.adapter == "problem_adapter.py:DemoAdapter"
    assert config.metrics[0].name == "objective"


def test_python_literal_candidate_is_loaded_without_execution(tmp_path: Path) -> None:
    """Literal model specifications should load without importing candidate code."""
    candidate_path = tmp_path / "model_spec.py"
    candidate_path.write_text(
        "MODEL_SPEC = {'parameters': {'radius': 0.3}, 'x': '0.5 + radius'}\n",
        encoding="utf-8",
    )

    candidate = load_candidate(candidate_path, symbol="MODEL_SPEC")

    assert candidate["parameters"]["radius"] == pytest.approx(0.3)
    assert candidate["x"] == "0.5 + radius"


def test_python_candidate_rejects_executable_code(tmp_path: Path) -> None:
    """Executable expressions must not pass the structured candidate boundary."""
    candidate_path = tmp_path / "model_spec.py"
    candidate_path.write_text(
        "MODEL_SPEC = __import__('os').environ.copy()\n",
        encoding="utf-8",
    )

    with pytest.raises(CandidateLoadError, match="literal"):
        load_candidate(candidate_path, symbol="MODEL_SPEC")


def test_candidate_patch_updates_existing_initial_and_preserves_structure(
    tmp_path: Path,
) -> None:
    """A trusted evaluator patch should update state without replacing its domain."""
    candidate_path = tmp_path / "model_spec.py"
    candidate_path.write_text(
        "# EVOLVE_START\n"
        "MODEL_SPEC = {\n"
        "    'parameters': {\n"
        "        'phase': {'initial': 0.0, 'lower': -0.2, 'upper': 0.3},\n"
        "    },\n"
        "    'formula': 'phase + i',\n"
        "}\n"
        "# EVOLVE_END\n",
        encoding="utf-8",
    )

    changed = apply_candidate_patch(
        candidate_path,
        {"parameters": {"phase": {"initial": 0.125}}},
        symbol="MODEL_SPEC",
    )

    updated = load_candidate(candidate_path, symbol="MODEL_SPEC")
    assert changed is True
    assert updated["parameters"]["phase"] == {
        "initial": 0.125,
        "lower": -0.2,
        "upper": 0.3,
    }
    assert updated["formula"] == "phase + i"
    source = candidate_path.read_text(encoding="utf-8")
    assert "# EVOLVE_START" in source
    assert "# EVOLVE_END" in source


def test_numeric_expression_supports_whitelisted_math_only() -> None:
    """Formula evaluation should allow arithmetic but reject arbitrary Python."""
    value = evaluate_numeric_expression(
        "cx + radius * cos(2 * pi * i / count + phase)",
        {
            "cx": 0.5,
            "radius": 0.25,
            "i": 0,
            "count": 8,
            "phase": 0.0,
        },
    )

    assert value == pytest.approx(0.75)

    with pytest.raises(ExpressionError, match="not allowed"):
        evaluate_numeric_expression("__import__('os').getcwd()", {})


@pytest.mark.asyncio
async def test_solver_evaluator_runs_adapter_then_independent_validation(tmp_path: Path) -> None:
    """Evaluation should solve first and then derive scores from independent validation."""
    (tmp_path / "model_spec.py").write_text(
        "MODEL_SPEC = {'value': 3.5}\n",
        encoding="utf-8",
    )
    (tmp_path / "problem_adapter.py").write_text(
        """
from llm4ad.evaluator.solver.base import (
    BaseSolverAdapter,
    SolverRunResult,
    SolverValidationResult,
)


class DemoAdapter(BaseSolverAdapter):
    def solve(self, candidate, context):
        return SolverRunResult(
            status="optimal",
            solution={"value": candidate["value"]},
            metadata={"nodes": 2},
            candidate_patch={"value": 4.0},
        )

    def validate(self, candidate, solution, context):
        return SolverValidationResult(
            valid=True,
            metrics={"objective": float(solution["value"]), "validity": 1.0},
        )
""".strip(),
        encoding="utf-8",
    )
    config = SolverEvaluatorConfig(
        adapter="problem_adapter.py:DemoAdapter",
        candidate_file="model_spec.py",
        candidate_symbol="MODEL_SPEC",
        metrics=[
            SolverMetricConfig(name="objective", type="maximize", weight=1.0),
            SolverMetricConfig(name="validity", type="maximize", weight=0.0),
        ],
    )
    evaluator = SolverEvaluator(config=config, config_dir=str(tmp_path))

    result = await evaluator.evaluate(EvalContext(project_root=str(tmp_path), timeout=5.0))

    assert result.success is True
    assert result.score == pytest.approx(3.5)
    assert result.metrics["objective"] == pytest.approx(3.5)
    assert result.metrics["validity"] == 1.0
    assert result.metrics["solver_nodes"] == 2.0
    assert result.metrics["solver_feasible"] == 1.0
    assert result.metrics["solver_optimal"] == 1.0
    assert result.metadata["solver_status"] == "optimal"
    assert result.metadata["solver"]["nodes"] == 2
    assert result.evolution_feedback["solver_status"] == "optimal"
    assert result.evolution_feedback["solver"]["nodes"] == 2
    assert result.evolution_feedback["candidate_update"] == {
        "candidate_file": "model_spec.py",
        "candidate_symbol": "MODEL_SPEC",
        "patch": {"value": 4.0},
    }


@pytest.mark.asyncio
async def test_solver_evaluator_rejects_non_finite_adapter_metrics(tmp_path: Path) -> None:
    """Invalid numerical evidence must produce an explicit failed evaluation."""
    (tmp_path / "model_spec.json").write_text('{"value": 1}', encoding="utf-8")
    (tmp_path / "invalid_adapter.py").write_text(
        """
from llm4ad.evaluator.solver.base import (
    BaseSolverAdapter,
    SolverRunResult,
    SolverValidationResult,
)


class InvalidAdapter(BaseSolverAdapter):
    def solve(self, candidate, context):
        return SolverRunResult(status="optimal", solution={"value": candidate["value"]})

    def validate(self, candidate, solution, context):
        return SolverValidationResult(valid=True, metrics={"objective": float("nan")})
""".strip(),
        encoding="utf-8",
    )
    config = SolverEvaluatorConfig(
        adapter="invalid_adapter.py:InvalidAdapter",
        candidate_file="model_spec.json",
        metrics=[SolverMetricConfig(name="objective")],
    )
    evaluator = SolverEvaluator(config=config, config_dir=str(tmp_path))

    result = await evaluator.evaluate(EvalContext(project_root=str(tmp_path), timeout=5.0))

    assert result.success is False
    assert result.metrics == {}
    assert "finite" in (result.error_message or "").lower()
    assert not math.isnan(result.score)


def test_relative_adapter_path_falls_back_to_task_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-memory task configs should resolve adapters from the mounted task directory."""
    (tmp_path / "problem_adapter.py").write_text(
        "from llm4ad.evaluator.solver.base import BaseSolverAdapter\n"
        "class DemoAdapter(BaseSolverAdapter):\n"
        "    def solve(self, candidate, context): raise NotImplementedError\n"
        "    def validate(self, candidate, solution, context): raise NotImplementedError\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    evaluator = SolverEvaluator(
        SolverEvaluatorConfig(
            adapter="problem_adapter.py:DemoAdapter",
            metrics=[SolverMetricConfig(name="objective")],
        )
    )

    adapter = evaluator._create_adapter()

    assert adapter.__class__.__name__ == "DemoAdapter"
