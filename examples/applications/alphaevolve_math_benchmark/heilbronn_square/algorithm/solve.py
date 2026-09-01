#!/usr/bin/env python3
"""Initial 13-point construction in a unit square."""

import itertools
import json
import random


# EVOLVE_START
def run_search_point(n=13):
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
    return points, minimum_area, minimum_area


# EVOLVE_END


if __name__ == "__main__":
    points, minimum_area, best_area_ratio = run_search_point(13)
    print(json.dumps({"points": points, "minimum_area": minimum_area, "best_area_ratio": best_area_ratio}))
