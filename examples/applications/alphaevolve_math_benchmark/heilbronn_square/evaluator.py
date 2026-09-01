"""Evaluator for the 13-point unit-square Heilbronn problem."""

import itertools
import math
from typing import Any

import numpy as np
from _shared.runtime import JsonBenchmarkEvaluator, finite_array

from llm4ad.evaluator.base import BaseEvaluator


def _minimum_area(points: np.ndarray) -> float:
    minimum = math.inf
    for first, second, third in itertools.combinations(range(len(points)), 3):
        a, b, c = points[first], points[second], points[third]
        area = abs(float((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))) / 2.0
        minimum = min(minimum, area)
    return minimum


@BaseEvaluator.register("alphaevolve_heilbronn_square_evaluator")
class HeilbronnSquareEvaluator(JsonBenchmarkEvaluator):
    metric_name = "best_area_ratio"
    target_value = 0.0309
    score_mode = "objective_over_target"
    registry_key = "alphaevolve_heilbronn_square_evaluator"
    benchmark_key = "heilbronn_13_points_unit_square"

    def measure(self, payload: dict[str, Any]) -> float:
        points = finite_array(payload, "points", (13, 2))
        if np.any(points < 0) or np.any(points > 1):
            raise ValueError("points must lie inside the unit square")
        for first, second in itertools.combinations(range(len(points)), 2):
            difference = points[first] - points[second]
            if float(difference @ difference) < 1e-12:
                raise ValueError("points must be separated by at least 1e-6")

        minimum = _minimum_area(points)
        if minimum < 1e-10:
            raise ValueError("every triple of points must be non-collinear")
        reported_minimum = float(payload["minimum_area"])
        area_ratio = float(payload["best_area_ratio"])
        if not math.isfinite(reported_minimum) or not math.isfinite(area_ratio):
            raise ValueError("reported metrics must be finite")
        if abs(minimum - reported_minimum) >= 1e-5:
            raise ValueError("reported minimum_area does not match the point construction")
        return minimum
