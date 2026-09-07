#!/usr/bin/env python3
"""Initial 16-point planar construction for the max/min distance ratio."""

import json

import numpy as np


# EVOLVE_START
def optimize_construct(n=16, d=2):
    if (n, d) != (16, 2):
        raise ValueError("the benchmark evaluates n=16 and d=2")
    points = np.array([[float(column), float(row)] for row in range(4) for column in range(4)])
    distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=-1)
    nonzero = distances[distances > 0]
    return points, float((np.max(nonzero) / np.min(nonzero)) ** 2)
# EVOLVE_END


if __name__ == "__main__":
    points, _ = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist()}))
