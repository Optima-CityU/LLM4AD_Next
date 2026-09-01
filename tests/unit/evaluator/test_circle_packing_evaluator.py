"""Regression tests for the AlphaEvolve circle-packing example adapter."""

import importlib.util
from pathlib import Path

import pytest
import yaml

from llm4ad.config.schema import EvalContext

EXAMPLE_DIR = Path(__file__).resolve().parents[3] / "examples" / "applications" / "alphaevolve_math_benchmark"


def _load_evaluator_class():
    module_path = EXAMPLE_DIR / "circle_packing" / "evaluator.py"
    spec = importlib.util.spec_from_file_location("circle_packing_evaluator", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CirclePackingEvaluator


@pytest.mark.asyncio
async def test_circle_packing_baseline_uses_raw_objective_score():
    """The shipped initial program should produce a valid reproducible score."""
    evaluator = _load_evaluator_class()()
    project_root = EXAMPLE_DIR / "circle_packing" / "algorithm"

    result = await evaluator.evaluate(EvalContext(project_root=str(project_root), timeout=10.0))

    assert result.success is True
    assert result.metrics["validity"] == 1.0
    assert result.metrics["sum_radii"] > 0
    assert result.score == pytest.approx(result.metrics["sum_radii"])
    assert "alphaevolve_ratio" not in result.metrics
    assert "alphaevolve_gap" not in result.metrics


def test_circle_packing_config_uses_comparable_candidate_budget():
    """The default run should approximate the paper's 100-evaluation budget."""
    config = yaml.safe_load(
        (EXAMPLE_DIR / "circle_packing" / "code_config.yaml").read_text(encoding="utf-8")
    )

    evolution = config["evolution"]
    initial_candidates = evolution["num_islands"] * evolution["island_population_size"]
    elites_per_island = max(1, int(evolution["island_population_size"] * evolution["elite_ratio"]))
    candidates_per_generation = evolution["num_islands"] * (
        evolution["island_population_size"] - elites_per_island
    )
    total_candidates = initial_candidates + candidates_per_generation * evolution["max_generations"]

    assert total_candidates == 99
    assert evolution["early_stop_patience"] > evolution["max_generations"]
    assert config["providers"][0]["temperature"] == pytest.approx(0.7)
    assert config["providers"][0]["max_tokens"] == 8192
    assert config["coder"]["max_gen_tokens"] == 4096
    assert config["coder"]["context_max_tokens"] == 8192


def test_circle_packing_model_context_does_not_leak_published_targets():
    """Evolution prompts must not reveal known benchmark answers."""
    config = yaml.safe_load(
        (EXAMPLE_DIR / "circle_packing" / "code_config.yaml").read_text(encoding="utf-8")
    )
    model_visible_text = "\n".join(
        [
            config["background"],
            config["coder"]["prompt_template"],
        ]
    ).lower()

    assert "alphaevolve" not in model_visible_text
    assert "loongflow" not in model_visible_text
    assert "2.6358627564136983" not in model_visible_text


@pytest.mark.asyncio
async def test_circle_packing_invalid_geometry_is_an_evaluation_failure(tmp_path):
    """Overlapping circles must fail validation rather than become a score."""
    (tmp_path / "solve.py").write_text(
        """
import json

centers = [[0.5, 0.5]] * 26
radii = [0.2] * 26
print(json.dumps({"centers": centers, "radii": radii}))
""".strip(),
        encoding="utf-8",
    )
    evaluator = _load_evaluator_class()()

    result = await evaluator.evaluate(EvalContext(project_root=str(tmp_path), timeout=10.0))

    assert result.success is False
    assert result.metrics["validity"] == 0.0
    assert "overlap" in (result.error_message or "").lower()
