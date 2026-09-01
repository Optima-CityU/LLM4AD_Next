#!/usr/bin/env python3
"""Initial candidate for packing 11 unit hexagons in a regular hexagon."""

import json


# EVOLVE_START
def optimize_construct():
    positions = [
        (-3.3, -2.0), (-1.1, -2.0), (1.1, -2.0), (3.3, -2.0),
        (-3.3, 0.0), (-1.1, 0.0), (1.1, 0.0), (3.3, 0.0),
        (-2.2, 2.0), (0.0, 2.0), (2.2, 2.0),
    ]
    inner_hexagons = [[x, y, 0.0] for x, y in positions]
    return inner_hexagons, [0.0, 0.0], 8.0, 0.0
# EVOLVE_END


if __name__ == "__main__":
    inner, center, side, angle = optimize_construct()
    print(json.dumps({
        "inner_hexagons": inner,
        "outer_center": center,
        "outer_side_length": side,
        "outer_angle_degrees": angle,
    }))
