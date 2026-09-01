"""Evaluator for the 16-point maximum/minimum distance ratio."""

import itertools
from typing import Any

import numpy as np
from _shared.runtime import JsonBenchmarkEvaluator, finite_array

from llm4ad.evaluator.base import BaseEvaluator, MetricType


@BaseEvaluator.register("alphaevolve_max_min_distance_evaluator")
class MaxMinDistanceRatioEvaluator(JsonBenchmarkEvaluator):
    metric_name = "ratio_squared"
    metric_type = MetricType.MINIMIZE
    target_value = 12.889266112
    score_mode = "target_over_objective"
    registry_key = "alphaevolve_max_min_distance_evaluator"
    benchmark_key = "max_to_min_distance_ratio_16_points_2d"

    def measure(self, payload: dict[str, Any]) -> float:
        points = finite_array(payload, "points", (16, 2))
        distances = np.array([np.linalg.norm(points[i] - points[j]) for i, j in itertools.combinations(range(16), 2)])
        minimum = float(np.min(distances))
        maximum = float(np.max(distances))
        if abs(minimum) < 1e-10 or abs(maximum) < 1e-10:
            raise ValueError("all points must be distinct")
        ratio_squared = float((maximum / minimum) ** 2)
        if ratio_squared < 1e-10:
            raise ValueError("distance ratio is invalid")
        return ratio_squared
