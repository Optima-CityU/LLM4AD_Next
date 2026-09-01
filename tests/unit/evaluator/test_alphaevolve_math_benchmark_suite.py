"""Contract tests for the bundled AlphaEvolve mathematics benchmark suite."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import math
import re
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

from llm4ad.config.schema import EvalContext

EXAMPLE_DIR = Path(__file__).resolve().parents[3] / "examples" / "applications" / "alphaevolve_math_benchmark"

EXPECTED_CONFIGS = {
    "circle_packing/code_config.yaml",
    "circle_packing/solver_config.yaml",
    "circle_rectangle/config.yaml",
    "hexagon_packing/config.yaml",
    "max_min_distance_ratio/config.yaml",
    "minimum_overlap/config.yaml",
    "uncertainty_inequality/config.yaml",
    "second_autocorrelation/config.yaml",
    "first_autocorrelation/config.yaml",
    "sums_differences/config.yaml",
    "heilbronn_triangle/config.yaml",
    "heilbronn_square/config.yaml",
}

OFFICIAL_CASE_CONTRACTS = {
    "hexagon_packing": (
        "HexagonPackingEvaluator",
        "3ac2acd29d453c7df63f6317457aa19735d7c13cfc2819cf72a75eef817fa01b",
        3.931,
        "target_over_objective",
        "outer_hex_side_length",
    ),
    "max_min_distance_ratio": (
        "MaxMinDistanceRatioEvaluator",
        "bcbb21ea9ddfe65460bfd1fe5ab2441d67d6ff2fb1725a9cb6868a97ee22ef28",
        12.889266112,
        "target_over_objective",
        "ratio_squared",
    ),
    "minimum_overlap": (
        "MinimumOverlapEvaluator",
        "675b310b78d2b9493e577ea646e1e010148ddb26784a8e16e833e32a13d8d2ca",
        0.380927,
        "target_over_objective",
        "upper bound",
    ),
    "uncertainty_inequality": (
        "UncertaintyInequalityEvaluator",
        "3a1d021909996ac4393c1ccec04c0bddacd8ac59698290f3c2b5542bbb67538a",
        0.3521,
        "target_over_objective",
        "c_upper_bound",
    ),
    "second_autocorrelation": (
        "SecondAutocorrelationEvaluator",
        "ac698187f4f34b344eddac407b8ec61b4f1a46cd48977df01cc4662cefe0dc35",
        0.8963,
        "objective_over_target",
        "c_lower_bound",
    ),
    "sums_differences": (
        "SumsDifferencesEvaluator",
        "cd259db6342273be6bcaf6ae51b7022892e3e43c8896896cff1e4b63e487349b",
        1.1319033750264975,
        "objective_over_target",
        "get_score_result",
    ),
    "heilbronn_triangle": (
        "HeilbronnTriangleEvaluator",
        "54934b9ea05e49471488e7675ae5c45fe3756114a71aced53b1212d35aa6cfad",
        0.0365,
        "objective_over_target",
        "min_area",
    ),
    "heilbronn_square": (
        "HeilbronnSquareEvaluator",
        "6d33664f3ee726af5d1a2c2a0cf140cce185b58dfde3619f8f32670515c0b59f",
        0.0309,
        "objective_over_target",
        "best_area_ratio",
    ),
}


def _load_evaluator(case_dir: str, class_name: str):
    module = _load_evaluator_module(case_dir)
    return getattr(module, class_name)


def _load_evaluator_module(case_dir: str):
    module_path = EXAMPLE_DIR / case_dir / "evaluator.py"
    if str(EXAMPLE_DIR) not in sys.path:
        sys.path.insert(0, str(EXAMPLE_DIR))
    spec = importlib.util.spec_from_file_location(f"{case_dir}_evaluator", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_candidate_module(case_dir: str):
    module_path = EXAMPLE_DIR / case_dir / "algorithm" / "solve.py"
    spec = importlib.util.spec_from_file_location(f"{case_dir}_candidate", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_suite_exposes_every_supported_benchmark_case() -> None:
    configs = {path.relative_to(EXAMPLE_DIR).as_posix() for path in EXAMPLE_DIR.rglob("*config.yaml")}
    assert configs == EXPECTED_CONFIGS

    for config_name in EXPECTED_CONFIGS:
        config = yaml.safe_load((EXAMPLE_DIR / config_name).read_text(encoding="utf-8"))
        assert config["description_en"]
        assert config["description_zh"]
        assert config["evaluator"].get("module") or config["evaluator"].get("adapter")
        workspace = EXAMPLE_DIR / config["version_control"]["local_path"]
        assert workspace.is_dir()
        assert any(workspace.glob("*.py"))

        evaluator_path = config["evaluator"].get("module") or config["evaluator"].get("adapter")
        evaluator_file = evaluator_path.rsplit(":", 1)[0]
        assert (EXAMPLE_DIR / evaluator_file).is_file()


def test_suite_uses_one_reproducible_experiment_contract() -> None:
    for config_name in EXPECTED_CONFIGS:
        config = yaml.safe_load((EXAMPLE_DIR / config_name).read_text(encoding="utf-8"))
        provider = config["providers"][0]
        coder = config["coder"]
        evaluator = config["evaluator"]
        evolution = config["evolution"]

        assert config["random_seed"] == 42
        assert (provider["temperature"], provider["max_tokens"], provider["timeout"]) == (
            0.7,
            8192,
            600.0,
        )
        assert (coder["max_gen_tokens"], coder["context_max_tokens"], coder["timeout"]) == (
            4096,
            8192,
            600.0,
        )
        assert (evaluator["timeout"], evaluator["parallel"], evaluator["batch_size"]) == (
            1200.0,
            True,
            5,
        )
        assert (
            evolution["max_generations"],
            evolution["early_stop_patience"],
            evolution["num_islands"],
            evolution["island_population_size"],
        ) == (7, 8, 3, 5)


@pytest.mark.parametrize(
    ("case_dir", "contract"),
    OFFICIAL_CASE_CONTRACTS.items(),
)
def test_remaining_cases_preserve_official_prompt_and_score_contract(
    case_dir: str,
    contract: tuple[str, str, float, str, str],
) -> None:
    class_name, prompt_sha256, target_value, score_mode, metric_name = contract
    config = yaml.safe_load((EXAMPLE_DIR / case_dir / "config.yaml").read_text(encoding="utf-8"))
    evaluator = _load_evaluator(case_dir, class_name)()

    assert hashlib.sha256(config["background"].encode()).hexdigest() == prompt_sha256
    assert evaluator.target_value == pytest.approx(target_value)
    assert evaluator.score_mode == score_mode
    assert metric_name in config["evaluator"]["metrics"]

    result = asyncio.run(
        evaluator.evaluate(EvalContext(project_root=str(EXAMPLE_DIR / case_dir / "algorithm"), timeout=20.0))
    )
    assert result.success is True, result.error_message
    raw_objective = result.metrics[metric_name]
    expected_score = (
        target_value / raw_objective
        if score_mode == "target_over_objective"
        else raw_objective / target_value
    )
    assert result.score == pytest.approx(expected_score)


def test_suite_markdown_has_no_vendor_or_notice_documents() -> None:
    forbidden_names = {"notice", "baidu"}
    for path in EXAMPLE_DIR.rglob("*"):
        assert not any(token in path.name.lower() for token in forbidden_names)

    for path in EXAMPLE_DIR.rglob("*.md"):
        text = path.read_text(encoding="utf-8").lower()
        assert "baidu" not in text
        assert "百度" not in text
        assert not any("\u4e00" <= character <= "\u9fff" for character in text)


def test_suite_readme_records_reference_metrics_and_reserves_local_results() -> None:
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
    for value in (
        "2.6358627564136983",
        "2.6359829624734026",
        "2.3658321334167627",
        "2.365832229500823",
        "3.930092",
        "3.928906855463712",
        "12.88926611203463",
        "12.889243547212832",
        "0.380924",
        "0.3809137564083654",
        "0.35209910442252773",
        "0.352099104421844",
        "0.8962799441554083",
        "0.9027021077220739",
        "1.5052939684401607",
        "1.509527314861778",
        "1.1219357374860444",
        "1.103534711409646",
        "0.036529889880030156",
        "0.0365298898793351",
        "0.030936889034895654",
        "0.030900663674639613",
        "2.635983083325037",
    ):
        assert value in readme

    assert readme.count("|  |  |") == 10

    assert (EXAMPLE_DIR / "circle_packing" / "results" / "best" / "result.json").is_file()
    best_code = EXAMPLE_DIR / "circle_packing" / "results" / "best" / "solve.py"
    assert best_code.is_file()
    assert f"[{best_code.name}](circle_packing/results/best/{best_code.name})" in readme
    assert list(EXAMPLE_DIR.glob("*/results/**/*.py")) == [best_code]
    assert "algorithm_id" not in readme


def test_runtime_sources_do_not_contain_published_results() -> None:
    readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
    comparison_section = readme.split("## Comparison results", 1)[1].split("## Experiment setup", 1)[0]
    published_values = {
        "2.6358627564136983", "2.6359829624734026",
        "2.3658321334167627", "2.365832229500823",
        "3.930092", "3.928906855463712",
        "12.88926611203463", "12.889243547212832",
        "0.380924", "0.3809137564083654",
        "0.35209910442252773", "0.352099104421844",
        "0.8962799441554083", "0.9027021077220739",
        "1.5052939684401607", "1.509527314861778",
        "1.1219357374860444", "1.103534711409646",
        "0.036529889880030156", "0.0365298898793351",
        "0.030936889034895654", "0.030900663674639613",
    }
    assert published_values.issubset(set(re.findall(r"`([0-9]+\.[0-9]+)`", comparison_section)))
    assert published_values

    model_visible_text = []
    for config_path in EXAMPLE_DIR.rglob("*config.yaml"):
        config_text = config_path.read_text(encoding="utf-8")
        config = yaml.safe_load(config_text)
        model_visible_text.append(config_text)
        model_visible_text.extend(
            str(value)
            for value in (
                config.get("background", ""),
                config.get("coder", {}).get("prompt_template", ""),
            )
        )
    for config_path in EXAMPLE_DIR.rglob("*config.yaml"):
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        workspace = EXAMPLE_DIR / config["version_control"]["local_path"]
        model_visible_text.extend(
            path.read_text(encoding="utf-8")
            for path in workspace.rglob("*")
            if path.is_file() and path.suffix in {".py", ".md", ".txt", ".yaml", ".yml"}
        )
    model_visible_text.extend(path.read_text(encoding="utf-8") for path in EXAMPLE_DIR.glob("*/evaluator.py"))
    combined = "\n".join(model_visible_text)

    # Public task statements contain their comparison targets by design. The
    # locally evolved result and its implementation must remain outside all
    # model-visible task inputs.
    assert "2.635983083325037" not in combined
    assert "circle_packing/results/best" not in combined


def test_platform_coder_prompts_do_not_add_candidate_runtime_limits() -> None:
    for config_name in EXPECTED_CONFIGS:
        config = yaml.safe_load((EXAMPLE_DIR / config_name).read_text(encoding="utf-8"))
        platform_coder_prompt = config.get("coder", {}).get("prompt_template", "")

        assert "1000 seconds" not in platform_coder_prompt


def test_circle_packing_rejects_any_positive_overlap() -> None:
    evaluator = _load_evaluator("circle_packing", "CirclePackingEvaluator")
    centers = np.full((26, 2), 0.5)
    radii = np.zeros(26)
    centers[0] = [0.2, 0.5]
    centers[1] = [0.4 - 5e-13, 0.5]
    radii[:2] = 0.1

    assert evaluator._validate_geometry(centers, radii) == "circles 0 and 1 overlap"


def test_circle_packing_baseline_leaves_machine_precision_safety_margin() -> None:
    evaluator = _load_evaluator("circle_packing", "CirclePackingEvaluator")
    centers, radii = _load_candidate_module("circle_packing").construct_packing()
    reference_distance = np.sqrt(np.sum((centers[7] - centers[8]) ** 2))
    assert radii[7] + radii[8] <= reference_distance

    assert evaluator._validate_geometry(centers, radii) is None


def test_rectangle_uses_minimum_circumscribing_rectangle_contract() -> None:
    evaluator = _load_evaluator("circle_rectangle", "CircleRectanglePackingEvaluator")()
    circles = [[index * 0.05, 0.0, 0.0] for index in range(21)]

    assert evaluator.measure({"circles": circles}) == 0.0


def test_rectangle_rejects_negative_circle_radii() -> None:
    evaluator = _load_evaluator("circle_rectangle", "CircleRectanglePackingEvaluator")()
    circles = [[0.0, 0.0, -1.0] for _ in range(21)]

    with pytest.raises(ValueError, match="non-negative"):
        evaluator.measure({"circles": circles})


def test_hexagons_that_touch_are_reported_as_intersecting() -> None:
    module = _load_evaluator_module("hexagon_packing")
    first = module._hexagon(np.array([0.0, 0.0]), 1.0, 0.0)
    second = module._hexagon(np.array([2.0, 0.0]), 1.0, 0.0)

    assert module._polygons_overlap(first, second) is True


def test_max_min_distance_accepts_official_nonzero_threshold() -> None:
    evaluator = _load_evaluator("max_min_distance_ratio", "MaxMinDistanceRatioEvaluator")()
    points = [[0.0, 0.0], [5e-10, 0.0]] + [[float(index), float(index % 3)] for index in range(1, 15)]

    assert math.isfinite(evaluator.measure({"points": points}))


def test_first_autocorrelation_clamps_candidate_values() -> None:
    evaluator = _load_evaluator("first_autocorrelation", "FirstAutocorrelationEvaluator")()
    sequence = np.array([0.0, 1000.0, 1.0])
    expected = 2 * len(sequence) * np.max(np.convolve(sequence, sequence)) / np.sum(sequence) ** 2

    assert evaluator.measure({"sequence": [-2.0, 2000.0, 1.0]}) == pytest.approx(expected)


def test_first_autocorrelation_preserves_the_official_experiment_contract() -> None:
    evaluator_module = _load_evaluator_module("first_autocorrelation")
    evaluator = evaluator_module.FirstAutocorrelationEvaluator()
    candidate = _load_candidate_module("first_autocorrelation")
    config = yaml.safe_load((EXAMPLE_DIR / "first_autocorrelation" / "config.yaml").read_text(encoding="utf-8"))
    sequence = candidate.run_search_for_best_sequence()
    prompt_contract = config["background"]
    result = asyncio.run(
        evaluator.evaluate(
            EvalContext(project_root=str(EXAMPLE_DIR / "first_autocorrelation" / "algorithm"), timeout=20.0)
        )
    )

    assert evaluator_module.TARGET_VALUE == pytest.approx(1.5053)
    assert len(sequence) == 600
    assert np.ptp(np.asarray(sequence, dtype=float)) > 0
    assert callable(candidate.evaluate_sequence)
    assert "run_search_for_best_sequence()" in prompt_contract
    assert "better upper bound <= 1.5053" in prompt_contract
    assert evaluator.measure({"sequence": [1.0, 2.0]}) == pytest.approx(16.0 / 9.0)
    assert result.success is True
    assert result.metrics["target_ratio"] == pytest.approx(evaluator_module.TARGET_VALUE / result.metrics["upper_bound"])
    assert result.score == pytest.approx(result.metrics["target_ratio"])


def test_sums_and_differences_uses_integer_conversion_contract() -> None:
    evaluator = _load_evaluator("sums_differences", "SumsDifferencesEvaluator")()

    assert evaluator.measure({"values": [0.9, 1.9]}) == pytest.approx(1.005)


def test_triangle_containment_has_no_boundary_tolerance() -> None:
    evaluator = _load_evaluator("heilbronn_triangle", "HeilbronnTriangleEvaluator")()
    generator = np.random.default_rng(42)
    barycentric = generator.dirichlet(np.ones(3), size=11)
    vertices = np.array([[0.0, 0.0], [1.0, 0.0], [0.5, math.sqrt(3.0) / 2.0]])
    points = barycentric @ vertices
    points[0, 1] = -1e-12

    with pytest.raises(ValueError, match="inside"):
        evaluator.measure({"points": points.tolist()})


def test_heilbronn_square_recomputes_the_candidate_score() -> None:
    evaluator = _load_evaluator("heilbronn_square", "HeilbronnSquareEvaluator")()
    points = np.random.default_rng(7).random((13, 2))
    minimum = _load_evaluator_module("heilbronn_square")._minimum_area(points)

    assert evaluator.measure(
        {
            "points": points.tolist(),
            "minimum_area": minimum + 5e-6,
            "best_area_ratio": minimum * 2,
        }
    ) == pytest.approx(minimum)


def test_second_autocorrelation_verifies_reported_lower_bound() -> None:
    module = _load_evaluator_module("second_autocorrelation")
    evaluator = module.SecondAutocorrelationEvaluator()
    heights = np.ones(50)
    correct = module._lower_bound(heights)

    assert evaluator.measure({"heights": heights.tolist(), "c_lower_bound": correct}) == correct
    with pytest.raises(ValueError, match="miscalculation"):
        evaluator.measure({"heights": heights.tolist(), "c_lower_bound": correct + 1e-12})


def test_uncertainty_inequality_matches_symbolic_reference_result() -> None:
    evaluator = _load_evaluator("uncertainty_inequality", "UncertaintyInequalityEvaluator")()

    assert evaluator.measure({"coefficients": [1.0, 2.0, 3.0]}) == pytest.approx(2.7174571283140323)


def test_uncertainty_inequality_rejects_zero_upper_bound() -> None:
    evaluator = _load_evaluator("uncertainty_inequality", "UncertaintyInequalityEvaluator")()

    with pytest.raises(ValueError, match="positive upper bound"):
        evaluator.measure({"coefficients": [0.0]})


@pytest.mark.parametrize(
    ("class_name", "case_dir", "metric_name", "expected_baseline"),
    [
        ("CirclePackingEvaluator", "circle_packing", "sum_radii", 0.9597642169962063),
        ("CircleRectanglePackingEvaluator", "circle_rectangle", "sum_radii", 2.099979),
        ("HexagonPackingEvaluator", "hexagon_packing", "outer_hex_side_length", 8.0),
        ("MaxMinDistanceRatioEvaluator", "max_min_distance_ratio", "ratio_squared", 18.0),
        ("MinimumOverlapEvaluator", "minimum_overlap", "upper bound", 0.5),
        ("UncertaintyInequalityEvaluator", "uncertainty_inequality", "c_upper_bound", 2.7174571283140323),
        ("SecondAutocorrelationEvaluator", "second_autocorrelation", "c_lower_bound", 2.0 / 3.0),
        ("FirstAutocorrelationEvaluator", "first_autocorrelation", "upper_bound", 2.008221721626784),
        ("SumsDifferencesEvaluator", "sums_differences", "get_score_result", 1.009),
        ("HeilbronnTriangleEvaluator", "heilbronn_triangle", "min_area", 0.0012539434895968255),
        ("HeilbronnSquareEvaluator", "heilbronn_square", "best_area_ratio", 0.000390043601303295),
    ],
)
def test_shipped_baselines_are_valid(
    class_name: str,
    case_dir: str,
    metric_name: str,
    expected_baseline: float,
) -> None:
    evaluator = _load_evaluator(case_dir, class_name)()

    result = asyncio.run(evaluator.evaluate(EvalContext(project_root=str(EXAMPLE_DIR / case_dir / "algorithm"), timeout=20.0)))

    assert result.success is True, result.error_message
    assert result.metrics["validity"] == 1.0
    assert result.metrics[metric_name] == pytest.approx(expected_baseline)
