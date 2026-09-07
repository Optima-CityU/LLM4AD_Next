#!/usr/bin/env python3
"""Initial 13-point construction in a unit square."""

import itertools
import json
import random

import numpy as np
from scipy.spatial import ConvexHull


# EVOLVE_START
def find_best_placement(n):
    """Construct an initial placement for the evolvable search implementation."""
    if n != 13:
        raise ValueError("the benchmark evaluates n=13")
    generator = random.Random(42)
    points = [[generator.random(), generator.random()] for _ in range(13)]
    minimum_area = min(
        abs(
            (points[second][0] - points[first][0]) * (points[third][1] - points[first][1])
            - (points[second][1] - points[first][1]) * (points[third][0] - points[first][0])
        )
        / 2.0
        for first, second, third in itertools.combinations(range(len(points)), 3)
    )
    return points, minimum_area


# EVOLVE_END


def run_search_point(n=13):
    """Normalize candidate output through the fixed benchmark adapter.

    Args:
        n: Number of points requested by the benchmark.

    Returns:
        JSON-serializable points and the independently recomputed minimum area.

    Raises:
        ValueError: If the candidate returns a point array with an invalid shape.
    """
    points, _reported_minimum = find_best_placement(n)
    normalized = np.asarray(points, dtype=float)
    if normalized.shape != (n, 2):
        raise ValueError(f"candidate must return points with shape ({n}, 2)")
    minimum_area = min(
        abs(
            (normalized[second, 0] - normalized[first, 0])
            * (normalized[third, 1] - normalized[first, 1])
            - (normalized[second, 1] - normalized[first, 1])
            * (normalized[third, 0] - normalized[first, 0])
        )
        / 2.0
        for first, second, third in itertools.combinations(range(n), 3)
    )
    value = float(minimum_area)
    hull_area = float(ConvexHull(normalized).volume)
    if hull_area <= 0.0:
        raise ValueError("candidate convex hull must have positive area")
    return normalized.tolist(), value, value / hull_area


if __name__ == "__main__":
    points, minimum_area, best_area_ratio = run_search_point(13)
    print(json.dumps({"points": points, "minimum_area": minimum_area, "best_area_ratio": best_area_ratio}))
