Actionable design pattern grounded in SLVI-26’s construction and observed metrics.

- 5x5 Square Lattice With Void-Insertion (SLVI-26): SLVI-26 achieved sum_radii = 2.541421356 with validity = 1.0 by using a capacity-tight 5×5 square lattice of 25 circles at R=0.1 flush with the unit-square borders, then inserting one extra circle at the void center (0.4, 0.4) with r_extra = (sqrt(2) − 1)·R ≈ 0.041421356 to be tangent to its four nearest neighbors while keeping borders nonbinding. Future designs should start from a deterministic, border-saturating lattice and analytically add elements in mid-cell voids with radii set by min(border clearance, distance to neighbors minus their radii), preferring interior voids where borders do not bind, instead of relying on greedy shrinking.

```python
#!/usr/bin/env python3
"""Deterministic 26-circle packing in the unit square.

Implements a 5x5 square lattice of equal circles at radius R=0.1 (border-tight),
plus one extra circle placed at the center of a lattice void with radius
r_extra = (sqrt(2) - 1) * R. This construction places exactly 26 disjoint
circles inside the unit square and maximizes the sum of radii under the
described deterministic strategy.

The output is serialized as JSON with "centers" and "radii" fields.
"""

import json

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    Algorithm:
    - Base grid: Place 25 equal circles of radius R=0.1 on a 5x5 square lattice.
      Centers lie at coordinates {R + 2R * k | k in {0,1,2,3,4}} along x and y,
      i.e., {0.1, 0.3, 0.5, 0.7, 0.9}. Circles are tangent to neighbors and
      to the square's border, but do not overlap.
    - Extra circle: Place one additional circle at the center of a lattice void,
      specifically at (0.4, 0.4) which is equidistant from its four nearest
      grid centers: (0.3,0.3), (0.5,0.3), (0.3,0.5), (0.5,0.5).
      Its maximal radius is r_extra = sqrt(2) * R - R = (sqrt(2) - 1) * R.
      Border clearances at (0.4,0.4) are larger, so neighbor tangencies bind
      the extra circle's radius.

    This construction is closed-form, deterministic, and yields exactly 26
    circles satisfying all constraints.

    Parameters
    ----------
    num_circles : int
        Number of circles to construct. This implementation is defined for 26.

    Returns
    -------
    centers : np.ndarray, shape (26, 2)
        XY centers for each circle.
    radii : np.ndarray, shape (26,)
        Radii for each circle.
    """
    if num_circles != 26:
        raise ValueError("This deterministic constructor is defined for exactly 26 circles.")

    # Base radius for the 5x5 lattice: largest uniform radius fitting within [0,1]^2.
    R = 0.1

    # Prepare arrays
    centers = np.zeros((26, 2), dtype=float)
    radii = np.zeros(26, dtype=float)

    # 1) Place the 25 grid circles in row-major order.
    # Grid coordinates along each axis: 0.1, 0.3, 0.5, 0.7, 0.9
    coords = R + 2.0 * R * np.arange(5)  # array([0.1, 0.3, 0.5, 0.7, 0.9])
    idx = 0
    for j in range(5):  # y-index
        for i in range(5):  # x-index
            centers[idx] = [coords[i], coords[j]]
            radii[idx] = R
            idx += 1
    assert idx == 25

    # 2) Place the extra circle at the center of a lattice void.
    # Choose the void centered between grid cells around (0.3,0.3)-(0.5,0.5), i.e., (0.4,0.4).
    extra_center = np.array([R + (2 * 1 + 1) * R, R + (2 * 1 + 1) * R])  # (0.4, 0.4)

    # 3) Compute the extra radius by saturating tangency to the four nearest neighbors.
    # Distance to each of the four nearest grid centers is sqrt( (0.1)^2 + (0.1)^2 ) = sqrt(2) * R.
    r_extra = (np.sqrt(2.0) - 1.0) * R

    # Border clearance at (0.4,0.4) is min(0.4, 0.4, 0.6, 0.6) = 0.4 > r_extra, so neighbors bind.
    centers[25] = extra_center
    radii[25] = r_extra

    # Optional deterministic validity checks (should all pass).
    # - All circles must lie within the unit square.
    eps = 1e-12
    mins = centers.min(axis=1)
    maxs = (1.0 - centers).min(axis=1)
    assert np.all(radii >= -eps)
    assert np.all(mins >= radii - eps)  # left/bottom borders
    assert np.all(maxs >= radii - eps)  # right/top borders

    # - No pair overlaps (distance >= sum of radii).
    for a in range(26):
        for b in range(a + 1, 26):
            dist = float(np.linalg.norm(centers[a] - centers[b]))
            assert dist + eps >= radii[a] + radii[b]

    return centers, radii


def compute_max_radii(centers):
    """Greedily shrink border-limited radii until every pair is disjoint.

    Note: This utility is not used by the deterministic constructor above.
    It remains here to preserve the project interface.
    """
    radii = np.minimum.reduce([centers[:, 0], centers[:, 1], 1 - centers[:, 0], 1 - centers[:, 1]])
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            distance = float(np.linalg.norm(centers[i] - centers[j]))
            radius_sum = float(radii[i] + radii[j])
            if radius_sum > distance and radius_sum > 0:
                scale = distance / radius_sum
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
```
