#!/usr/bin/env python3
"""Initial 11-point construction in an equilateral triangle."""

import json
import itertools
import math
import random


# EVOLVE_START
def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")
    generator = random.Random(42)
    points = []
    for _ in range(11):
        u = generator.random()
        v = generator.random()
        if u + v > 1.0:
            u, v = 1.0 - u, 1.0 - v
        points.append([u + 0.5 * v, math.sqrt(3.0) * 0.5 * v])
    minimum_area = min(
        abs(
            (points[second][0] - points[first][0]) * (points[third][1] - points[first][1])
            - (points[second][1] - points[first][1]) * (points[third][0] - points[first][0])
        )
        / 2.0
        for first, second, third in itertools.combinations(range(len(points)), 3)
    )
    return points, minimum_area / (math.sqrt(3.0) / 4.0)
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
