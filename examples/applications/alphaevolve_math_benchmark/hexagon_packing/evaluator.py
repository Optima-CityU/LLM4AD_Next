"""Evaluator for packing 11 unit hexagons in a regular hexagon."""

import itertools
import math
from typing import Any

import numpy as np
from _shared.runtime import TOL, JsonBenchmarkEvaluator, finite_array

from llm4ad.evaluator.base import BaseEvaluator, MetricType


def _hexagon(center: np.ndarray, side: float, angle_degrees: float) -> np.ndarray:
    angles = np.deg2rad(angle_degrees + np.arange(6) * 60.0)
    return center + side * np.column_stack((np.cos(angles), np.sin(angles)))


def _polygons_overlap(first: np.ndarray, second: np.ndarray) -> bool:
    for polygon in (first, second):
        edges = np.roll(polygon, -1, axis=0) - polygon
        for edge in edges:
            axis = np.array([-edge[1], edge[0]])
            first_projection = first @ axis
            second_projection = second @ axis
            if first_projection.max() < second_projection.min() - TOL or second_projection.max() < first_projection.min() - TOL:
                return False
    return True


def _inside(point: np.ndarray, polygon: np.ndarray) -> bool:
    edges = np.roll(polygon, -1, axis=0) - polygon
    offsets = point - polygon
    crosses = edges[:, 0] * offsets[:, 1] - edges[:, 1] * offsets[:, 0]
    return bool(np.all(crosses >= -TOL))


@BaseEvaluator.register("alphaevolve_hexagon_packing_evaluator")
class HexagonPackingEvaluator(JsonBenchmarkEvaluator):
    metric_name = "outer_hex_side_length"
    metric_type = MetricType.MINIMIZE
    target_value = 3.931
    score_mode = "target_over_objective"
    registry_key = "alphaevolve_hexagon_packing_evaluator"
    benchmark_key = "packing_11_unit_hexagons_in_hexagon"

    def measure(self, payload: dict[str, Any]) -> float:
        inner = finite_array(payload, "inner_hexagons", (11, 3))
        outer_center = finite_array(payload, "outer_center", (2,))
        outer_side = float(payload["outer_side_length"])
        outer_angle = float(payload.get("outer_angle_degrees", 0.0))
        if not math.isfinite(outer_side) or outer_side < 1e-10:
            raise ValueError("outer_side_length must be finite and positive")
        outer = _hexagon(outer_center, outer_side, outer_angle)
        polygons = [_hexagon(row[:2], 1.0, float(row[2])) for row in inner]
        for index, polygon in enumerate(polygons):
            if not all(_inside(vertex, outer) for vertex in polygon):
                raise ValueError(f"inner hexagon {index} lies outside the outer hexagon")
        for i, j in itertools.combinations(range(11), 2):
            if _polygons_overlap(polygons[i], polygons[j]):
                raise ValueError(f"inner hexagons {i} and {j} overlap")
        return outer_side
