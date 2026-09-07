Insight from a successful hex-lattice packing that maximized sum of radii without overlaps by using analytic bounds and uniform radii.

- Hex26: Deterministic hex-lattice packing with bound-tight spacing: Place 26 centers on a hexagonal lattice using six staggered rows [5, 4, 5, 4, 4, 4] with alternating offsets so nearest‑neighbor distances are equal, then derive spacing s from global bounds—s = min(1/5, 1/(1 + 5*sqrt(3)/2)) = 1/(1 + 5*sqrt(3)/2) ≈ 0.1877 for m = 6—and set a uniform radius r = s/2 ≈ 0.09385 to guarantee edge clearance and non‑overlap without greedy pairwise shrinking; this bound‑tight, deterministic construction achieved sum_radii ≈ 2.438966 with validity 1.0, and should be reused by computing s from horizontal and vertical limits, assigning uniform r = s/2, and placing centers with offsets while verifying neighbor distances instead of iterative scaling.

```python
#!/usr/bin/env python3
"""Deterministic hex-lattice packing of 26 circles in the unit square."""

import json

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """
    Construct centers and radii for a valid packing of 26 circles using a hexagonal lattice.

    Strategy:
    - Place centers on a triangular (hexagonal) lattice, which is the densest packing for equal circles.
    - Use six staggered rows with counts [5, 4, 5, 4, 4, 4], alternating horizontal offsets (0, s/2, 0, ...).
    - Derive spacing s from global bounds so every center is at least radius r from the square edges:
        * Horizontal bound for widest row of n_max=5 centers: s <= 1 / n_max.
        * Vertical bound for m rows with row spacing h_y = (sqrt(3)/2) * s: s <= 1 / (1 + (m - 1) * sqrt(3)/2).
      Choose s = min(horizontal_bound, vertical_bound). Then set r = s / 2.
    - With alternating offsets, the nearest-neighbor distance is exactly s, so r = s / 2 guarantees non-overlap.
    - Radii are set directly from the geometric bounds, no greedy shrinking.

    Returns:
        centers: shape (26, 2), array of (x, y) center coordinates
        radii:   shape (26,), array of circle radii (all equal)

    Notes:
    - The construction is deterministic and avoids unnecessary reductions in radii.
    - A small one-pass safety adjustment is applied to account for floating-point noise:
      r_i = min(r, edge_clearance_i, 0.5 * min_j ||c_i - c_j||).
      In exact geometry, all r_i equal r; the min() is purely a guardrail.
    """
    if num_circles != 26:
        raise ValueError("This constructor is designed specifically for 26 circles.")

    # Hex-lattice parameters
    # Row counts totaling 26. Use alternating offsets to preserve triangular adjacency.
    row_counts = [5, 4, 5, 4, 4, 4]
    m = len(row_counts)
    n_max = max(row_counts)

    sqrt3 = np.sqrt(3.0)

    # Derive spacing s from horizontal and vertical bounds.
    # Horizontal width bound for the widest row (n_max centers, offset 0):
    # Ensures leftmost x >= r and rightmost x <= 1 - r.
    s_horizontal_bound = 1.0 / float(n_max)

    # Vertical bound over m rows so that bottom/top centers are at least r from edges.
    s_vertical_bound = 1.0 / (1.0 + (m - 1) * (sqrt3 / 2.0))

    # Final spacing and radius
    s = min(s_horizontal_bound, s_vertical_bound)
    r = s / 2.0
    h_y = (sqrt3 / 2.0) * s

    # Deterministic center placement:
    # - Row y-coordinates: y_j = r + j * h_y
    # - Row offsets alternate: 0, s/2, 0, s/2, ...
    centers_list = []
    for j, n_j in enumerate(row_counts):
        y_j = r + j * h_y
        offset_j = 0.0 if (j % 2 == 0) else (s / 2.0)
        # Place n_j centers across the row
        for k in range(n_j):
            x_k = r + offset_j + k * s
            centers_list.append([x_k, y_j])

    centers = np.asarray(centers_list, dtype=float)
    if centers.shape != (num_circles, 2):
        # Defensive check to ensure we produced the expected number of centers
        raise RuntimeError(f"Expected {num_circles} centers, got {centers.shape[0]}")

    # Compute radii from exact geometric bounds with a small safety check.
    radii = compute_uniform_radii(centers, base_radius=r)

    return centers, radii


def compute_uniform_radii(centers: np.ndarray, base_radius: float) -> np.ndarray:
    """
    Compute per-circle radii from:
    - base_radius r (derived from lattice spacing)
    - edge clearance for each center (distance to the square boundary)
    - half of the nearest-neighbor distance

    In the ideal hex-lattice construction, all radii equal base_radius.
    This function applies conservative min bounds to guard against floating-point effects.
    """
    n = centers.shape[0]
    # Edge clearance for each center (minimum distance to the square boundary)
    edge_clearance = np.minimum.reduce(
        [centers[:, 0], centers[:, 1], 1.0 - centers[:, 0], 1.0 - centers[:, 1]]
    )

    # Compute nearest-neighbor distances (O(n^2) is fine for n=26)
    # Distance matrix where d[i, j] = ||centers[i] - centers[j]||
    diffs = centers[:, None, :] - centers[None, :, :]
    dists = np.sqrt(np.sum(diffs * diffs, axis=2))
    # Ignore self-distance by setting diagonal to +inf when computing nearest neighbor
    np.fill_diagonal(dists, np.inf)
    nearest = np.min(dists, axis=1)

    # Radius per circle: conservative minimum of all bounds
    radii = np.minimum(base_radius, edge_clearance)
    radii = np.minimum(radii, 0.5 * nearest)

    # Ensure non-negative finite radii
    radii = np.clip(radii, 0.0, np.finfo(float).max)
    return radii


def compute_max_radii(centers):
    """
    Deprecated greedy shrinker retained for backward compatibility.

    This function is not used by the hex-lattice constructor. It greedily scales
    overlapping radii and typically underperforms the analytical lattice bounds.
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
