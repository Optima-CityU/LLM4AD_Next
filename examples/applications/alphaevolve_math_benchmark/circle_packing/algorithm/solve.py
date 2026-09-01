#!/usr/bin/env python3
"""Initial candidate for packing 26 circles in the unit square."""

import json

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles."""
    centers = np.zeros((num_circles, 2), dtype=float)
    centers[0] = [0.5, 0.5]

    for i in range(8):
        angle = 2 * np.pi * i / 8
        centers[i + 1] = [
            0.5 + 0.3 * np.cos(angle),
            0.5 + 0.3 * np.sin(angle),
        ]

    for i in range(16):
        angle = 2 * np.pi * i / 16
        centers[i + 9] = [
            0.5 + 0.7 * np.cos(angle),
            0.5 + 0.7 * np.sin(angle),
        ]

    centers = np.clip(centers, 0.01, 0.99)
    radii = compute_max_radii(centers)
    return centers, radii


def compute_max_radii(centers):
    """Greedily shrink border-limited radii until every pair is disjoint."""
    radii = np.minimum.reduce([centers[:, 0], centers[:, 1], 1 - centers[:, 0], 1 - centers[:, 1]])
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            distance = float(np.sqrt(np.sum((centers[i] - centers[j]) ** 2)))
            radius_sum = float(radii[i] + radii[j])
            if radius_sum > distance and radius_sum > 0:
                scale = np.nextafter(distance, 0.0) / radius_sum
                radii[i] *= scale
                radii[j] *= scale
    return radii


# EVOLVE_END


def main() -> None:
    """Serialize the constructed packing for the isolated evaluator."""
    centers, radii = construct_packing(26)
    print(
        json.dumps(
            {
                "centers": np.asarray(centers, dtype=float).tolist(),
                "radii": np.asarray(radii, dtype=float).tolist(),
            },
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
