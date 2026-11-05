"""Source regressions for the third independent benchmark oracle audit."""

import ast
import importlib.util
import math
import os
from pathlib import Path

import numpy as np
import pytest

EXAMPLE_ROOT = Path(__file__).resolve().parents[3] / "examples/applications/alphaevolve_math_benchmark"
CASES = (
    "first_autocorrelation", "second_autocorrelation", "heilbronn_square",
    "heilbronn_triangle", "hexagon_packing", "max_min_distance_ratio",
    "minimum_overlap", "sums_differences", "uncertainty_inequality",
)


@pytest.fixture
def validators(monkeypatch):
    """Load changed original evaluator modules without changing package imports."""
    monkeypatch.syspath_prepend(str(EXAMPLE_ROOT))
    modules = {}
    for case in ("heilbronn_square", "heilbronn_triangle", "hexagon_packing"):
        spec = importlib.util.spec_from_file_location(
            f"alphaevolve_source_v3_{case}", EXAMPLE_ROOT / case / "evaluator.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[case] = module
    return modules


def test_triangle_accepts_natural_boundary_and_rejects_real_violation(validators):
    """Keep the closed right edge feasible despite ordinary rounding error."""
    vertices = np.array([[0, 0], [1, 0], [0.5, math.sqrt(3) / 2]])
    points = np.random.default_rng(42).dirichlet([1, 1, 1], 11) @ vertices
    points[0] = [0.5915, math.sqrt(3) * (1 - 0.5915)]
    evaluator = validators["heilbronn_triangle"].HeilbronnTriangleEvaluator()
    assert evaluator.measure({"points": points.tolist()}) > 0
    points[0, 1] += 1e-8
    with pytest.raises(ValueError, match="inside"):
        evaluator.measure({"points": points.tolist()})


def test_square_accepts_boundary_after_natural_half_turn(validators):
    """Keep a closed square corner feasible after a pi rotation."""
    points = np.random.default_rng(42).uniform(0.1, 0.9, (13, 2))
    points[0] = [0, 1]
    angle = math.pi
    rotation = np.array([[math.cos(angle), -math.sin(angle)],
                         [math.sin(angle), math.cos(angle)]])
    points = (points - 0.5) @ rotation.T + 0.5
    module = validators["heilbronn_square"]
    evaluator = module.HeilbronnSquareEvaluator()
    payload = {"points": points.tolist(), "minimum_area": module._minimum_area(points),
               "best_area_ratio": 0.0}
    assert evaluator.measure(payload) > 0
    points[0, 1] = -1e-8
    with pytest.raises(ValueError, match="inside"):
        evaluator.measure(dict(payload, points=points.tolist()))


@pytest.mark.parametrize("rotation,side,valid", [
    (0.0, 4.93725492220354, True), (10.0, 4.863771641426987, False),
])
def test_hexagon_outer_angle_is_reduced_before_adding_normal_offset(validators, rotation, side, valid):
    """Equivalent finite angles must agree on valid and invalid constructions."""
    inner = np.array([[1.5 * q, math.sqrt(3) * (r + q / 2), 0]
                      for q in range(4) for r in range(3)][:11])
    inner[:, :2] -= inner[:, :2].mean(axis=0)
    angle = math.radians(rotation)
    matrix = np.array([[math.cos(angle), -math.sin(angle)],
                       [math.sin(angle), math.cos(angle)]])
    inner[:, :2] = inner[:, :2] @ matrix.T
    inner[:, 2] = rotation
    evaluator = validators["hexagon_packing"].HexagonPackingEvaluator()
    for outer_angle in (280.0, 1e20):
        payload = {"inner_hexagons": inner.tolist(), "outer_center": [0, 0],
                   "outer_side_length": side, "outer_angle_degrees": outer_angle}
        if valid:
            assert evaluator.measure(payload) == side
        else:
            with pytest.raises(ValueError, match="outside"):
                evaluator.measure(payload)


def test_nine_original_and_adapted_mathematical_kernels_match():
    """Check all nine numerical kernels and constants against the companion suite."""
    configured = os.environ.get("ALPHAEVOLVE_TEST_SUITE_ROOT")
    if not configured:
        pytest.skip("Set ALPHAEVOLVE_TEST_SUITE_ROOT for companion oracle parity")
    for case in CASES:
        trees = [ast.parse(path.read_text()) for path in (
            EXAMPLE_ROOT / case / "evaluator.py",
            Path(configured) / "alphaevolve_validators" / f"{case}.py",
        )]
        kernels = [[ast.dump(node, include_attributes=False) for node in tree.body
                    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.FunctionDef,
                                         ast.AsyncFunctionDef, ast.ClassDef))]
                   for tree in trees]
        assert kernels[0] == kernels[1], case


@pytest.mark.parametrize("outlier_position", [0, 15])
def test_source_distance_preserves_a_small_cluster_beside_large_outlier(monkeypatch, outlier_position):
    """Avoid collapsing distinct close pairs when selecting a global origin."""
    monkeypatch.syspath_prepend(str(EXAMPLE_ROOT))
    spec = importlib.util.spec_from_file_location(
        "alphaevolve_source_v3_distance", EXAMPLE_ROOT / "max_min_distance_ratio/evaluator.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    points = [[i, 0] for i in range(15)]
    points.insert(outlier_position, [1e150, 0])
    assert module.MaxMinDistanceRatioEvaluator().measure({"points": points}) == pytest.approx(1e300)
