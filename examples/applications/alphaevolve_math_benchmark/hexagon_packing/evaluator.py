"""Evaluator for packing 11 unit hexagons in a regular hexagon."""

import itertools
import math
from typing import Any

import numpy as np
from _shared.runtime import JsonBenchmarkEvaluator, finite_array

from llm4ad.evaluator.base import BaseEvaluator, MetricType


GEOMETRY_TOL = 1e-9


def _hexagon(center: np.ndarray, side: float, angle_degrees: float) -> np.ndarray:
    if not math.isfinite(angle_degrees):
        raise ValueError('hexagon rotation must be finite')
    angles = np.deg2rad(angle_degrees % 360.0 + np.arange(6) * 60.0)
    return center + side * np.column_stack((np.cos(angles), np.sin(angles)))


def _polygons_overlap(first: np.ndarray, second: np.ndarray) -> bool:
    for polygon in (first, second):
        edges = np.roll(polygon, -1, axis=0) - polygon
        for edge in edges:
            length = float(np.hypot(edge[0], edge[1]))
            if not math.isfinite(length) or length == 0:
                raise ValueError('hexagon edges are numerically degenerate')
            axis = np.array([-edge[1], edge[0]]) / length
            first_projection = first @ axis
            second_projection = second @ axis
            # Disjoint interiors may share an edge or vertex. A separating
            # axis with zero overlap is a valid contact, not a collision.
            if first_projection.max() <= second_projection.min() + GEOMETRY_TOL or second_projection.max() <= first_projection.min() + GEOMETRY_TOL:
                return False
    return True


def _inside(point: np.ndarray, polygon: np.ndarray) -> bool:
    edges = np.roll(polygon, -1, axis=0) - polygon
    lengths = np.hypot(edges[:, 0], edges[:, 1])
    if not np.isfinite(lengths).all() or np.any(lengths == 0):
        return False
    edges = edges / lengths[:, None]
    offsets = point - polygon
    crosses = edges[:, 0] * offsets[:, 1] - edges[:, 1] * offsets[:, 0]
    return bool(np.isfinite(crosses).all() and np.all(crosses >= -GEOMETRY_TOL))


@BaseEvaluator.register("alphaevolve_hexagon_packing_evaluator")
class HexagonPackingEvaluator(JsonBenchmarkEvaluator):
    metric_name = "outer_hex_side_length"
    metric_type = MetricType.MINIMIZE
    target_value = 3.931
    score_mode = "target_over_objective"
    registry_key = "alphaevolve_hexagon_packing_evaluator"
    benchmark_key = "packing_11_unit_hexagons_in_hexagon"

    def measure(self, payload: dict[str, Any]) -> float:
        inner = finite_array(payload, 'inner_hexagons', (11, 3))
        outer_center = finite_array(payload, 'outer_center', (2,))
        outer_side = float(payload['outer_side_length'])
        outer_angle = float(payload.get('outer_angle_degrees', 0.0))
        if not math.isfinite(outer_side) or outer_side < 1e-10:
            raise ValueError('outer_side_length must be finite and positive')
        if not math.isfinite(outer_angle):
            raise ValueError('outer_angle_degrees must be finite')
        # Eleven disjoint unit hexagons have eleven times a unit hexagon's
        # area. This necessary bound also excludes collapsed tiny containers.
        if outer_side + GEOMETRY_TOL < math.sqrt(11.0):
            raise ValueError('outer hexagon area cannot contain eleven unit hexagons')
        with np.errstate(over='ignore', invalid='ignore'):
            relative_centers = inner[:, :2] - outer_center
        if not np.isfinite(relative_centers).all():
            raise ValueError('hexagon coordinates have an unrepresentable relative span')
        # Containment uses support functions in an outer-centered frame, so
        # adding a unit vertex to a huge absolute center cannot erase an edge.
        # Reduce before adding the normal offset, which huge angles can erase.
        normals = _hexagon(np.zeros(2), 1.0, (outer_angle % 360.0) + 30.0)
        apothem = outer_side * (math.sqrt(3.0) / 2.0)
        offsets = [_hexagon(np.zeros(2), 1.0, float(row[2])) for row in inner]
        for index, (center, vertices) in enumerate(zip(relative_centers, offsets)):
            support = np.max(vertices @ normals.T, axis=0)
            # Subtract the center projection before comparing unit support.
            # Adding support to a huge projection can round away an entire edge.
            remaining_distance = apothem - center @ normals.T
            if np.any(support > remaining_distance + GEOMETRY_TOL):
                raise ValueError(f'inner hexagon {index} lies outside the outer hexagon')
        for i, j in itertools.combinations(range(11), 2):
            displacement = relative_centers[j] - relative_centers[i]
            # Circumradii are one, so these pairs cannot intersect. Nearby
            # pairs are checked in their own frame to retain unit precision.
            if np.hypot(displacement[0], displacement[1]) > 2.0 + GEOMETRY_TOL:
                continue
            if _polygons_overlap(offsets[i], displacement + offsets[j]):
                raise ValueError(f'inner hexagons {i} and {j} overlap')
        return outer_side
