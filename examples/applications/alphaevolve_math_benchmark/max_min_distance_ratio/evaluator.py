"""Evaluator for the 16-point maximum/minimum distance ratio."""

import itertools
import math
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
        points = finite_array(payload, 'points', (16, 2))
        pairs = list(itertools.combinations(range(16), 2))
        # Subtract each pair before selecting a scale: a distant origin can
        # otherwise erase the separation between representable close points.
        with np.errstate(over='ignore', invalid='ignore'):
            differences = np.array([points[i] - points[j] for i, j in pairs])
        overflow_pairs = ~np.isfinite(differences).all(axis=1)
        if np.any(overflow_pairs):
            scale = float(np.max(np.abs(points)))
            differences /= scale
            for index in np.flatnonzero(overflow_pairs):
                i, j = pairs[index]
                differences[index] = points[i] / scale - points[j] / scale
        else:
            scale = float(np.max(np.abs(differences)))
            if scale == 0:
                raise ValueError('all points must be distinct')
            differences /= scale
        distances = np.hypot(differences[:, 0], differences[:, 1])
        minimum = float(np.min(distances))
        maximum = float(np.max(distances))
        if minimum <= 0 or not math.isfinite(minimum) or not math.isfinite(maximum):
            raise ValueError('all points must be distinct')
        ratio = maximum / minimum
        if not math.isfinite(ratio) or ratio > math.sqrt(np.finfo(float).max):
            raise ValueError('distance ratio is invalid')
        ratio_squared = ratio ** 2
        if not math.isfinite(ratio_squared) or ratio_squared < 1:
            raise ValueError('distance ratio is invalid')
        return ratio_squared
