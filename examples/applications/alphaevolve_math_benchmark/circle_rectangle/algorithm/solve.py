#!/usr/bin/env python3
"""Initial candidate for packing 21 circles in a perimeter-four rectangle."""

import json

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 21):
    radius = 0.099999
    centers = np.array(
        [[(column + 0.5) / 5, (row + 0.5) / 5] for row in range(5) for column in range(5)][:num_circles],
        dtype=float,
    )
    radii = np.full(num_circles, radius, dtype=float)
    return np.column_stack((centers, radii))


# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
