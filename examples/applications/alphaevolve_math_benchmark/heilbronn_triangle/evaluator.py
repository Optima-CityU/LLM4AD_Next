"""Evaluator for the 11-point equilateral-triangle Heilbronn problem."""

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


@BaseEvaluator.register("alphaevolve_heilbronn_triangle_evaluator")
class HeilbronnTriangleEvaluator(JsonBenchmarkEvaluator):
    metric_name = "min_area"
    target_value = 0.0365
    score_mode = "objective_over_target"
    registry_key = "alphaevolve_heilbronn_triangle_evaluator"
    benchmark_key = "heilbronn_11_points_equilateral_triangle"

    def measure(self, payload: dict[str, Any]) -> float:
        points = finite_array(payload, "points", (11, 2))
        root3 = math.sqrt(3.0)
        if len(set(map(tuple, points))) != len(points):
            raise ValueError("points must be distinct")
        for x_coordinate, y_coordinate in points:
            if not (y_coordinate >= 0 and root3 * x_coordinate <= root3 - y_coordinate and y_coordinate <= root3 * x_coordinate):
                raise ValueError("points must lie inside the unit-side equilateral triangle")
        for first, second, third in itertools.combinations(range(len(points)), 3):
            a, b, c = points[first], points[second], points[third]
            if (b[0] - a[0]) * (c[1] - a[1]) == (c[0] - a[0]) * (b[1] - a[1]):
                raise ValueError("every triple of points must be non-collinear")
        minimum = _minimum_area(points)
        return minimum / (root3 / 4.0)
