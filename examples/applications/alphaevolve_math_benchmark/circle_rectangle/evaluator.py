"""Evaluator for packing 21 circles in a perimeter-four rectangle."""

import itertools
from typing import Any

import numpy as np
from _shared.runtime import JsonBenchmarkEvaluator, finite_array

from llm4ad.evaluator.base import BaseEvaluator


@BaseEvaluator.register("alphaevolve_circle_rectangle_evaluator")
class CircleRectanglePackingEvaluator(JsonBenchmarkEvaluator):
    metric_name = "sum_radii"
    registry_key = "alphaevolve_circle_rectangle_evaluator"
    benchmark_key = "packing_21_circles_in_perimeter_4_rectangle"

    def measure(self, payload: dict[str, Any]) -> float:
        circles = finite_array(payload, "circles", (21, 3))
        centers = circles[:, :2]
        radii = circles[:, 2]
        if np.any(radii < 0):
            raise ValueError("circle radii must be non-negative")
        for i, j in itertools.combinations(range(21), 2):
            if np.linalg.norm(centers[i] - centers[j]) < radii[i] + radii[j]:
                raise ValueError(f"circles {i} and {j} overlap")

        width = float(np.max(centers[:, 0] + radii) - np.min(centers[:, 0] - radii))
        height = float(np.max(centers[:, 1] + radii) - np.min(centers[:, 1] - radii))
        if width + height > 2.0:
            raise ValueError("the minimum circumscribing rectangle has perimeter greater than 4")
        return float(np.sum(radii))
