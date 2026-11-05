"""Numerical and mathematical regressions for the original benchmark evaluators."""

from __future__ import annotations

import ast
import importlib.util
import math
import os
from pathlib import Path

import numpy as np
import pytest

EXAMPLE_ROOT = (
    Path(__file__).resolve().parents[3]
    / "examples/applications/alphaevolve_math_benchmark"
)
PATCHED_CASES = (
    "circle_packing", "circle_rectangle", "hexagon_packing",
    "max_min_distance_ratio", "second_autocorrelation", "uncertainty_inequality",
)


@pytest.fixture
def validators(monkeypatch):
    """Load the real source evaluators without changing their registrations."""
    monkeypatch.syspath_prepend(str(EXAMPLE_ROOT))
    modules = {}
    for case in PATCHED_CASES:
        spec = importlib.util.spec_from_file_location(
            f"alphaevolve_source_regression_{case}", EXAMPLE_ROOT / case / "evaluator.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[case] = module
    return modules


@pytest.mark.parametrize("outer_side", [1e-10, 3.5])
def test_hexagon_rejects_collapsed_tiny_outer_container(validators, outer_side):
    """Reject a translation that previously erased every outer edge."""
    evaluator = validators["hexagon_packing"].HexagonPackingEvaluator()
    with pytest.raises(ValueError):
        evaluator.measure({
            "inner_hexagons": [[3 * index, 0, 0] for index in range(11)],
            "outer_center": [1e100, 1e100], "outer_side_length": outer_side,
        })


def test_hexagon_accepts_contact_and_rejects_positive_overlap(validators):
    """Allow a valid hexagonal tiling while rejecting actual penetration."""
    module = validators["hexagon_packing"]
    first = module._hexagon(np.zeros(2), 1.0, 0.0)
    assert not module._polygons_overlap(
        first, module._hexagon(np.array([1.5, math.sqrt(3) / 2]), 1.0, 0.0)
    )
    assert module._polygons_overlap(
        first, module._hexagon(np.array([1.4999, math.sqrt(3) / 2]), 1.0, 0.0)
    )
    tiling = [[1.5 * q, math.sqrt(3) * (r + q / 2), 0.0]
              for q in range(4) for r in range(3)][:11]
    assert module.HexagonPackingEvaluator().measure({
        "inner_hexagons": tiling, "outer_center": [2.0, 3.0], "outer_side_length": 8.0,
    }) == 8.0


@pytest.mark.parametrize("outer_side", [1e7, 1e100, 1e308])
def test_hexagon_accepts_valid_layout_in_large_outer_container(validators, outer_side):
    """Large empty margins do not invalidate disjoint unit hexagons."""
    evaluator = validators["hexagon_packing"].HexagonPackingEvaluator()
    assert evaluator.measure({
        "inner_hexagons": [[3 * index, 0, 0] for index in range(11)],
        "outer_center": [0, 0],
        "outer_side_length": outer_side,
    }) == outer_side


@pytest.mark.parametrize("outer_side", [1e7, 1e100, 1e308])
def test_hexagon_rejects_unit_edge_outside_large_outer_container(validators, outer_side):
    """Retain unit-sized boundary checks even when the outer side is huge."""
    inner = [[3 * index, 0, 0] for index in range(11)]
    inner[-1] = [0, outer_side * (math.sqrt(3.0) / 2.0), 0]
    with pytest.raises(ValueError, match="outside"):
        validators["hexagon_packing"].HexagonPackingEvaluator().measure({
            "inner_hexagons": inner,
            "outer_center": [0, 0],
            "outer_side_length": outer_side,
        })


def test_rectangle_circle_radii_are_not_lost_to_translation(validators):
    """Reject the radius-one circle whose true bounding perimeter is eight."""
    circles = [[1e100, 1.0, 0.0] for _ in range(21)]
    circles[0] = [1e100, 0.0, 1.0]
    with pytest.raises(ValueError):
        validators["circle_rectangle"].CircleRectanglePackingEvaluator().measure({"circles": circles})


@pytest.mark.parametrize("scale", [1e-300, 1.0, 1e200, 1e308])
def test_distance_ratio_remains_invariant_under_scaling(validators, scale):
    """Keep the same sixteen-point ratio across representable coordinate scales."""
    points = np.array([[x - 1.5, y - 1.5] for x in range(4) for y in range(4)]) * scale
    evaluator = validators["max_min_distance_ratio"].MaxMinDistanceRatioEvaluator()
    assert evaluator.measure({"points": points.tolist()}) == pytest.approx(18.0, rel=1e-13)


def test_second_autocorrelation_accepts_roundoff_but_rejects_forged_score(validators):
    """Score the recomputed norm ratio rather than a candidate's claimed value."""
    evaluator = validators["second_autocorrelation"].SecondAutocorrelationEvaluator()
    assert evaluator.measure({"heights": [1.0] * 50, "c_lower_bound": 2 / 3}) == pytest.approx(2 / 3)
    for claimed in (99.0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            evaluator.measure({"heights": [1.0] * 50, "c_lower_bound": claimed})


def test_hermite_exact_radical_roots_have_a_valid_bound(validators):
    """Handle the exact roots of x squared times four-thirds x squared minus four."""
    evaluator = validators["uncertainty_inequality"].UncertaintyInequalityEvaluator()
    assert evaluator.measure({"coefficients": [1.0]}) == pytest.approx(3 / (2 * math.pi))


def test_circle_square_tangent_circles_remain_valid(validators):
    """Preserve the original permission for boundary and pairwise contacts."""
    centers = np.zeros((26, 2))
    radii = np.zeros(26)
    centers[:2] = [[0.25, 0.5], [0.75, 0.5]]
    radii[:2] = 0.25
    assert validators["circle_packing"].CirclePackingEvaluator._validate_geometry(centers, radii) is None


def test_original_and_shared_mathematical_kernels_match():
    """Check semantic parity with the optional companion comparison suite."""
    configured = os.environ.get("ALPHAEVOLVE_TEST_SUITE_ROOT")
    if not configured:
        pytest.skip("Set ALPHAEVOLVE_TEST_SUITE_ROOT to check companion oracle parity")
    adapted = Path(configured) / "alphaevolve_validators"
    for case in PATCHED_CASES:
        original_tree = ast.parse((EXAMPLE_ROOT / case / "evaluator.py").read_text())
        adapted_tree = ast.parse((adapted / f"{case}.py").read_text())
        original = [ast.dump(node, include_attributes=False) for node in original_tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        shared = [ast.dump(node, include_attributes=False) for node in adapted_tree.body
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        assert original == shared, case


def test_decimal_circle_contacts_do_not_fail_due_to_roundoff(validators):
    """Accept ordinary decimal grids while preserving the reported radii sum."""
    grid = np.array([(x, y) for y in (0.1, 0.3, 0.5, 0.7, 0.9)
                     for x in (0.1, 0.3, 0.5, 0.7, 0.9)])
    centers = np.vstack((grid, [0.0, 0.0]))
    radii = np.r_[np.full(25, 0.1), 0.0]
    assert validators["circle_packing"].CirclePackingEvaluator._validate_geometry(centers, radii) is None
    circles = np.column_stack((grid[:21], np.full(21, 0.1)))
    result = validators["circle_rectangle"].CircleRectanglePackingEvaluator().measure({"circles": circles.tolist()})
    assert result == pytest.approx(2.1)


def test_circle_roundoff_tolerance_does_not_hide_real_overlap(validators):
    """Reject actual penetration substantially above the rounding allowance."""
    centers = np.zeros((26, 2))
    radii = np.zeros(26)
    centers[:2] = [[0.1, 0.5], [0.3 - 1e-8, 0.5]]
    radii[:2] = 0.1
    assert "overlap" in validators["circle_packing"].CirclePackingEvaluator._validate_geometry(centers, radii)
    circles = np.column_stack((centers[:21], radii[:21]))
    with pytest.raises(ValueError, match="overlap"):
        validators["circle_rectangle"].CircleRectanglePackingEvaluator().measure({"circles": circles.tolist()})
