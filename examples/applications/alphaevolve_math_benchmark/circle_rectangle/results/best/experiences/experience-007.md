Key reusable choices that improved packing density while preserving a hard perimeter constraint.

- Perimeter-to-square reduction: Reducing the perimeter ≤ 4 constraint to packing inside the unit square [0,1]×[0,1] guarantees width ≤ 1 and height ≤ 1 so the optimizer can focus on maximizing the sum of radii without risking perimeter violations.
- Hexagonal seeding with trust-region active-set optimization: Seeding 21 centers on a clipped hexagonal lattice and then maximizing ∑Δr with a trust-region, active-set linearization plus feasibility projection and collision polish produced a valid packing with sum_radii 2.2803201128443242, exceeding the cited 5×5 grid baseline (∑r ≈ 2.10).
- Global inflation to saturate the square: When the packing does not touch the square boundaries, uniformly scaling positions and radii about the square center until a boundary becomes tight strictly increases the sum of radii while preserving nonoverlap, making it an effective final step.
- Multi-start with jitter: Running multiple seeds by flipping the 5-count row and adding small jitter (≤ 0.002) helps break symmetry and escape poor local optima, with selection by maximal ∑r.
- Multi-scale hex seeding with expanded row-shift patterns: The method diversified seeding by testing four row-count patterns [5,4,4,4,4], [4,5,4,4,4], [4,4,5,4,4], and [4,4,4,5,4] across three lattice spacings s ∈ {0.2×0.98, 0.2, 0.2×1.02} with v = s·sqrt(3)/2, and ran 6 jittered restarts per seed with unchanged jitter magnitude.
- Multi-scale hex seeding with expanded row-shift patterns: This multi-start perturbation targets the problem’s multi-modal sensitivity to lattice alignment and the placement of the 5-point row, letting the fixed-center LP inflation and trust-region moves exploit different active constraint patterns at n = 21 with low cost.
- Multi-scale hex seeding with expanded row-shift patterns: In this event, the approach achieved sum_radii = 2.305402731101652 with validity = 1.0, exceeding the parent score 2.298005016231055 while leaving LP parameters, trust-region steps, and feasibility safeguards unchanged.
- Barrier ascent with fixed-center LP polish and multi-pattern hex seeds: The method keeps a unit-square frame to meet the perimeter-4 constraint and uses a smooth interior-barrier ascent to place 21 disjoint circles while maximizing the sum of radii.
- Multi-pattern hex-like seeding with tiny jitter: The algorithm runs from multiple seeds using the existing 5-4-5-4-3 pattern and four shifted variants [5,4,4,4,4], [4,5,4,4,4], [4,4,5,4,4], and [4,4,4,5,4]; for each, it evenly spaces centers per row within [0,1], applies ±0.002 jitter clamped to [0,1], runs the barrier ascent, and selects the packing with the largest ∑r.
- Fixed-center LP inflation: After barrier ascent, the centers are frozen and an LP maximizes ∑r with bounds 0 ≤ r_i ≤ min(x_i, 1−x_i, y_i, 1−y_i) − ε and pairwise constraints r_i + r_j ≤ d_ij − ε, solved via scipy.optimize.linprog with HiGHS; the updated radii are accepted only if they increase ∑r, then x and y are re-clipped to [r+ε, 1−r−ε], and the step is skipped gracefully if SciPy is unavailable.
- Barrier ascent with fixed-center LP polish and multi-pattern hex seeds: In Event good_algorithm Generation 3, the method achieved sum_radii 2.3216812689390425 with validity 1.0 and no reported errors.
- Fixed-center LP inflation: The algorithm description states this LP polish removes small structured slack left by the smooth barrier optimizer in milliseconds for n=21, while the added seed patterns with tiny jitter broaden basin exploration with negligible complexity and were validated by prior top-performing variants.
- Hex-staggered seeding with added 5-4-5-4-3 pattern and small multi-restarts: On the 21-disc packing task with an axis-aligned perimeter constraint of 4, this configuration achieved sum_radii = 2.336906346422657 with validity = 1.0 after introducing true hex staggering, appending the [5, 4, 5, 4, 3] seed pattern, and running two tiny jitter restarts per seed.
- True hex staggering in hex_seed: Offsetting alternate rows by a half-step around x ≈ 0.5 (triangular-lattice staggering) increased initial local packing density without pushing rows to the boundary, which reduced barrier workload and allowed LP inflations to exploit tighter non-overlap constraints for n = 21.
- [5, 4, 5, 4, 3] seed pattern: Appending the [5, 4, 5, 4, 3] row-count pattern broadened the set of plausible contact graphs and frequently produced denser corner and edge fits than single-5-row variants at n = 21.
- Tiny multi-restarts per seed: Evaluating two additional jittered copies per seed with independent uniform jitter in [−1e−3, 1e−3] avoided unlucky local basins from symmetry and discretization artifacts at negligible overhead for n = 21, with best-of-three selection improving robustness.

```python
#!/usr/bin/env python3
"""High-density packing of 21 disjoint circles inside the unit square.

Algorithm overview:
- Reduce the perimeter constraint (width + height ≤ 2) to packing inside the unit square.
- Seed with near-hexagonal lattice patterns [5,4,4,4,4] and [4,5,4,4,4].
- Initialize equal radii at the maximum non-overlapping value given the seed.
- Iteratively grow radii using a coordinated, feasibility-aware update and gently move centers
  using repulsive forces from near neighbors and boundaries (trust-region-like behavior).
- After each growth/move, project back to feasibility and polish overlaps by separating pairs.
- Multi-start with small jitter to escape symmetric local optima; keep the best result.
- Final global uniform inflation about the square center to saturate the boundary without
  introducing overlap (uniform scaling preserves nonoverlap).
"""

import json
import math
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 21):
    """Construct a dense packing of 21 circles inside the unit square.

    Returns:
        ndarray of shape (21, 3) with columns [x, y, r], all strictly feasible.
    """

    # Numerical tolerances
    eps = 1e-12  # internal epsilon for separation/feasibility
    final_clearance = 1e-9  # ensure strictly feasible output

    rng = np.random.RandomState(42)

    assert num_circles == 21, "This implementation targets exactly 21 circles."

    # Hex lattice parameters: s horizontally, v vertically
    s = 0.2
    v = (math.sqrt(3) / 2.0) * s

    # Two patterns to try: positions of row counts
    patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
    ]

    def hex_seed(row_counts: List[int]) -> np.ndarray:
        """Generate centers on a clipped hexagonal lattice for the given row counts."""
        nrows = len(row_counts)
        # Symmetric vertical placement
        y0 = 0.5 - ((nrows - 1) * v) / 2.0
        centers = []
        for k, m in enumerate(row_counts):
            y = y0 + k * v
            if m == 5:
                xs = 0.5 * s + np.arange(m) * s  # 0.1, 0.3, 0.5, 0.7, 0.9 for s=0.2
            elif m == 4:
                xs = s + np.arange(m) * s  # 0.2, 0.4, 0.6, 0.8
            else:
                raise ValueError("Unsupported row count in pattern")
            for x in xs:
                centers.append([x, y])
        centers = np.array(centers, dtype=float)
        assert centers.shape[0] == num_circles
        return centers

    def min_pairwise_distance(centers: np.ndarray) -> float:
        """Compute the minimum Euclidean distance between distinct centers."""
        n = centers.shape[0]
        min_d = np.inf
        for i in range(n):
            dx = centers[i, 0] - centers[:, 0]
            dy = centers[i, 1] - centers[:, 1]
            d = np.hypot(dx, dy)
            d[i] = np.inf
            m = np.min(d)
            if m < min_d:
                min_d = m
        return float(min_d)

    def boundary_margin_for_center(c: np.ndarray, r: float) -> float:
        """Minimal margin to the square boundary for a circle center c with radius r."""
        x, y = c
        return min(x - r, 1.0 - r - x, y - r, 1.0 - r - y)

    def initialize_radii(centers: np.ndarray) -> np.ndarray:
        """Compute a safe initial equal radius based on nearest-neighbor and boundary gaps."""
        # Initial centers are inside the unit square by construction; start with equal radii.
        min_d = min_pairwise_distance(centers)
        # Boundary margins for r=0 are simply min(x, 1-x, y, 1-y)
        base_margin = float(np.min(np.minimum.reduce([centers[:, 0], 1.0 - centers[:, 0], centers[:, 1], 1.0 - centers[:, 1]])))
        r0 = min(min_d / 2.0, base_margin) - 1e-6
        r0 = max(r0, 1e-6)
        return np.full(centers.shape[0], r0, dtype=float)

    def resolve_overlaps(centers: np.ndarray, radii: np.ndarray, max_iter: int = 1000) -> None:
        """Iteratively separate overlapping circle pairs, clamping to boundary feasibility."""
        n = centers.shape[0]
        for _ in range(max_iter):
            any_overlap = False
            for i in range(n):
                for j in range(i + 1, n):
                    dx = centers[j, 0] - centers[i, 0]
                    dy = centers[j, 1] - centers[i, 1]
                    d = math.hypot(dx, dy)
                    needed = radii[i] + radii[j] + eps
                    if d < needed:
                        any_overlap = True
                        # Push apart by half the overlap along the line of centers
                        if d < 1e-12:
                            # Centers coincide; choose a random direction
                            theta = rng.uniform(0.0, 2.0 * math.pi)
                            ux, uy = math.cos(theta), math.sin(theta)
                        else:
                            ux, uy = dx / d, dy / d
                        push = 0.5 * (needed - d)
                        centers[i, 0] -= ux * push
                        centers[i, 1] -= uy * push
                        centers[j, 0] += ux * push
                        centers[j, 1] += uy * push
                        # Clamp back to boundary feasibility
                        for k in (i, j):
                            centers[k, 0] = min(max(centers[k, 0], radii[k] + eps), 1.0 - radii[k] - eps)
                            centers[k, 1] = min(max(centers[k, 1], radii[k] + eps), 1.0 - radii[k] - eps)
            if not any_overlap:
                break

    def feasibility_projection(centers: np.ndarray, radii: np.ndarray) -> None:
        """Project centers back to satisfy boundary constraints exactly."""
        centers[:, 0] = np.minimum(np.maximum(centers[:, 0], radii + eps), 1.0 - radii - eps)
        centers[:, 1] = np.minimum(np.maximum(centers[:, 1], radii + eps), 1.0 - radii - eps)

    def compute_pairwise_slacks(centers: np.ndarray, radii: np.ndarray) -> np.ndarray:
        """Compute pairwise slacks s_ij = d_ij - (r_i + r_j)."""
        n = centers.shape[0]
        slacks = np.empty((n, n), dtype=float)
        for i in range(n):
            dx = centers[i, 0] - centers[:, 0]
            dy = centers[i, 1] - centers[:, 1]
            d = np.hypot(dx, dy)
            slacks[i, :] = d - (radii[i] + radii)
            slacks[i, i] = np.inf
        return slacks

    def growth_and_move(centers: np.ndarray, radii: np.ndarray, iterations: int = 250) -> Tuple[np.ndarray, np.ndarray]:
        """Run iterative coordinated growth of radii and gentle center moves to reduce constraints."""
        n = centers.shape[0]
        trust_radius = 0.02
        near_thresh = 0.03  # thresholds for activating constraints (neighbors/boundary)
        beta = 0.4  # fraction of available slack to consume per coordinate ascent step

        best_centers = centers.copy()
        best_radii = radii.copy()
        best_sum = float(np.sum(best_radii))

        for it in range(iterations):
            # Compute slacks
            slacks = compute_pairwise_slacks(centers, radii)
            # Per-circle minimal slack including boundaries
            boundary_slacks = np.minimum.reduce([
                centers[:, 0] - radii,
                1.0 - radii - centers[:, 0],
                centers[:, 1] - radii,
                1.0 - radii - centers[:, 1],
            ])
            min_pair_slack = np.min(slacks, axis=1)
            avail_slack = np.minimum(boundary_slacks, min_pair_slack)

            # Coordinate ascent on radii: increase one-by-one respecting current active constraints
            order = np.argsort(-avail_slack)  # descending order by available slack
            delta_r = np.zeros(n, dtype=float)
            for idx in order:
                # Updated boundary slack with current delta
                bslack = min(
                    centers[idx, 0] - (radii[idx] + delta_r[idx]),
                    1.0 - (radii[idx] + delta_r[idx]) - centers[idx, 0],
                    centers[idx, 1] - (radii[idx] + delta_r[idx]),
                    1.0 - (radii[idx] + delta_r[idx]) - centers[idx, 1],
                )
                # Updated pairwise slacks with current delta for previously updated indices
                smax = bslack
                for j in range(n):
                    if j == idx:
                        continue
                    sij = slacks[idx, j] - (delta_r[idx] + delta_r[j])
                    if sij < smax:
                        smax = sij
                # Propose increase within trust in radius space
                dr = max(0.0, beta * smax)
                delta_r[idx] += dr

            # Apply delta radii
            radii += delta_r

            # Gentle center moves to relieve near constraints
            move = np.zeros_like(centers)
            for i in range(n):
                # Boundary pushes
                left = centers[i, 0] - radii[i]
                right = 1.0 - radii[i] - centers[i, 0]
                bottom = centers[i, 1] - radii[i]
                top = 1.0 - radii[i] - centers[i, 1]
                # Push inside if close to boundary
                if left < near_thresh:
                    move[i, 0] += (near_thresh - left)
                if right < near_thresh:
                    move[i, 0] -= (near_thresh - right)
                if bottom < near_thresh:
                    move[i, 1] += (near_thresh - bottom)
                if top < near_thresh:
                    move[i, 1] -= (near_thresh - top)

                # Neighbor pushes
                for j in range(n):
                    if j == i:
                        continue
                    dx = centers[i, 0] - centers[j, 0]
                    dy = centers[i, 1] - centers[j, 1]
                    dij = math.hypot(dx, dy)
                    # Active only for near interactions
                    sij = dij - (radii[i] + radii[j])
                    if sij < near_thresh:
                        # Push i away from j
                        if dij < 1e-12:
                            # Random direction if coincident
                            theta = rng.uniform(0.0, 2.0 * math.pi)
                            ux, uy = math.cos(theta), math.sin(theta)
                        else:
                            ux, uy = dx / dij, dy / dij
                        strength = (near_thresh - sij)
                        move[i, 0] += ux * strength * 0.5
                        move[i, 1] += uy * strength * 0.5

            # Trust-region on movement
            norms = np.hypot(move[:, 0], move[:, 1])
            scale = np.minimum(1.0, np.divide(trust_radius, norms, out=np.ones_like(norms), where=norms > 0))
            centers += move * scale[:, None]

            # Project to feasibility, then polish overlaps
            feasibility_projection(centers, radii)
            resolve_overlaps(centers, radii, max_iter=200)

            # Keep best feasible solution so far (sum of radii)
            current_sum = float(np.sum(radii))
            if current_sum > best_sum + 1e-12:
                best_sum = current_sum
                best_centers = centers.copy()
                best_radii = radii.copy()

            # Basic stagnation check and trust radius adjustment
            if it % 50 == 49:
                # If no improvement over last block, slightly reduce trust radius
                if best_sum < current_sum + 1e-9:
                    trust_radius = max(trust_radius * 0.7, 1e-4)

        return best_centers, best_radii

    def global_uniform_inflation(centers: np.ndarray, radii: np.ndarray) -> None:
        """Uniformly scale (x, y, r) about the square center to saturate a boundary."""
        c = 0.5
        n = centers.shape[0]
        s_max = np.inf

        for i in range(n):
            x, y, r = centers[i, 0], centers[i, 1], radii[i]
            # Left: c + s*(x - c - r) >= 0
            aL = x - c - r
            if aL < 0:
                s_max = min(s_max, c / (-aL))
            # Right: 0.5 - s*(x - c + r) >= 0
            aR = x - c + r
            if aR > 0:
                s_max = min(s_max, 0.5 / aR)
            # Bottom: c + s*(y - c - r) >= 0
            aB = y - c - r
            if aB < 0:
                s_max = min(s_max, c / (-aB))
            # Top: 0.5 - s*(y - c + r) >= 0
            aT = y - c + r
            if aT > 0:
                s_max = min(s_max, 0.5 / aT)

        if not np.isfinite(s_max) or s_max <= 1.0:
            return  # nothing to do

        # Apply scaling with a tiny safety margin
        s = max(1.0, s_max * (1.0 - 1e-12))
        centers[:, 0] = c + s * (centers[:, 0] - c)
        centers[:, 1] = c + s * (centers[:, 1] - c)
        radii *= s
        # No overlaps are introduced by uniform scaling; reproject to boundary for numerical safety.
        feasibility_projection(centers, radii)

    def run_seed(centers: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Run the optimization pipeline on a given seed; return best feasible packing."""
        radii = initialize_radii(centers)
        feasibility_projection(centers, radii)
        resolve_overlaps(centers, radii, max_iter=200)
        centers, radii = growth_and_move(centers, radii, iterations=300)
        # Final global inflation to reach boundaries
        global_uniform_inflation(centers, radii)
        resolve_overlaps(centers, radii, max_iter=200)
        feasibility_projection(centers, radii)
        return centers, radii

    # Multi-start: try different patterns and jitters
    best_centers = None
    best_radii = None
    best_sum = -np.inf

    for pattern in patterns:
        base_centers = hex_seed(pattern)
        for j in range(4):
            # Tiny jitter to break symmetry
            jitter = (rng.rand(*base_centers.shape) - 0.5) * 0.002
            centers = base_centers + jitter
            # Ensure initial centers stay inside [0,1]
            centers[:, 0] = np.clip(centers[:, 0], 0.0, 1.0)
            centers[:, 1] = np.clip(centers[:, 1], 0.0, 1.0)

            c_opt, r_opt = run_seed(centers.copy())
            sum_r = float(np.sum(r_opt))
            if sum_r > best_sum + 1e-12:
                best_sum = sum_r
                best_centers = c_opt.copy()
                best_radii = r_opt.copy()

    # Final polish: enforce tiny clearance and exact feasibility
    feasibility_projection(best_centers, best_radii)
    resolve_overlaps(best_centers, best_radii, max_iter=500)
    feasibility_projection(best_centers, best_radii)
    # Apply tiny shrink to ensure strict clearance on all constraints
    best_radii *= (1.0 - final_clearance)

    return np.column_stack((best_centers, best_radii))


# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```

```python
#!/usr/bin/env python3
"""High-density packing of 21 disjoint circles inside the unit square.

Algorithm overview:
- Reduce the perimeter constraint (width + height ≤ 2) to packing inside the unit square.
- Seed with near-hexagonal lattice patterns, now expanded to try four row-count patterns
  [5,4,4,4,4], [4,5,4,4,4], [4,4,5,4,4], and [4,4,4,5,4], and three nearby lattice spacings
  s ∈ {0.2×0.98, 0.2, 0.2×1.02} with vertical spacing v = s·sqrt(3)/2.
- Initialize equal radii at the maximum non-overlapping value given the seed.
- Iteratively grow radii using a coordinated, feasibility-aware update and gently move centers
  using repulsive forces from near neighbors and boundaries (trust-region-like behavior).
- Periodically and at key points, freeze centers and solve a small linear program (LP) to
  globally re-optimize radii exactly for those fixed centers (lp_inflate).
- After each growth/move, project back to feasibility and polish overlaps by separating pairs.
- Multi-start with small jitter to escape symmetric local optima; keep the best result.
  The number of jittered restarts per seed is increased from 4 to 6 to better explore local basins.
- Final global uniform inflation about the square center to saturate the boundary without
  introducing overlap (uniform scaling preserves nonoverlap), followed by a final LP inflation.
"""

import json
import math
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 21):
    """Construct a dense packing of 21 circles inside the unit square.

    Returns:
        ndarray of shape (21, 3) with columns [x, y, r], all strictly feasible.
    """

    # Numerical tolerances
    eps = 1e-12  # internal epsilon for separation/feasibility (also used in LP pair constraints)
    final_clearance = 1e-9  # ensure strictly feasible output

    rng = np.random.RandomState(42)

    assert num_circles == 21, "This implementation targets exactly 21 circles."

    # Base hex lattice parameter: s horizontally; we will sweep nearby scales
    s_base = 0.2

    # Row-count patterns to try: positions of the 5-count row
    patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],  # deeper interior
        [4, 4, 4, 5, 4],  # deeper interior
    ]

    def hex_seed(row_counts: List[int], s: float, v: float) -> np.ndarray:
        """Generate centers on a clipped hexagonal lattice for the given row counts.

        Args:
            row_counts: list of int row population counts (sum must equal num_circles).
            s: horizontal lattice spacing.
            v: vertical lattice spacing (typically s * sqrt(3) / 2).

        Returns:
            ndarray of shape (num_circles, 2) with centers inside [0, 1]^2.
        """
        nrows = len(row_counts)
        # Symmetric vertical placement, re-centered with given v
        y0 = 0.5 - ((nrows - 1) * v) / 2.0
        centers = []
        for k, m in enumerate(row_counts):
            y = y0 + k * v
            if m == 5:
                xs = 0.5 * s + np.arange(m) * s  # 0.1, 0.3, 0.5, 0.7, 0.9 for s=0.2
            elif m == 4:
                xs = s + np.arange(m) * s       # 0.2, 0.4, 0.6, 0.8 for s=0.2
            else:
                raise ValueError("Unsupported row count in pattern")
            for x in xs:
                centers.append([x, y])
        centers = np.array(centers, dtype=float)
        assert centers.shape[0] == num_circles
        return centers

    def min_pairwise_distance(centers: np.ndarray) -> float:
        """Compute the minimum Euclidean distance between distinct centers."""
        n = centers.shape[0]
        min_d = np.inf
        for i in range(n):
            dx = centers[i, 0] - centers[:, 0]
            dy = centers[i, 1] - centers[:, 1]
            d = np.hypot(dx, dy)
            d[i] = np.inf
            m = np.min(d)
            if m < min_d:
                min_d = m
        return float(min_d)

    def boundary_margin_for_center(c: np.ndarray, r: float) -> float:
        """Minimal margin to the square boundary for a circle center c with radius r."""
        x, y = c
        return min(x - r, 1.0 - r - x, y - r, 1.0 - r - y)

    def initialize_radii(centers: np.ndarray) -> np.ndarray:
        """Compute a safe initial equal radius based on nearest-neighbor and boundary gaps."""
        # Initial centers are inside the unit square by construction; start with equal radii.
        min_d = min_pairwise_distance(centers)
        # Boundary margins for r=0 are simply min(x, 1-x, y, 1-y)
        base_margin = float(np.min(np.minimum.reduce([centers[:, 0], 1.0 - centers[:, 0], centers[:, 1], 1.0 - centers[:, 1]])))
        r0 = min(min_d / 2.0, base_margin) - 1e-6
        r0 = max(r0, 1e-6)
        return np.full(centers.shape[0], r0, dtype=float)

    def resolve_overlaps(centers: np.ndarray, radii: np.ndarray, max_iter: int = 1000) -> None:
        """Iteratively separate overlapping circle pairs, clamping to boundary feasibility."""
        n = centers.shape[0]
        for _ in range(max_iter):
            any_overlap = False
            for i in range(n):
                for j in range(i + 1, n):
                    dx = centers[j, 0] - centers[i, 0]
                    dy = centers[j, 1] - centers[i, 1]
                    d = math.hypot(dx, dy)
                    needed = radii[i] + radii[j] + eps
                    if d < needed:
                        any_overlap = True
                        # Push apart by half the overlap along the line of centers
                        if d < 1e-12:
                            # Centers coincide; choose a random direction
                            theta = rng.uniform(0.0, 2.0 * math.pi)
                            ux, uy = math.cos(theta), math.sin(theta)
                        else:
                            ux, uy = dx / d, dy / d
                        push = 0.5 * (needed - d)
                        centers[i, 0] -= ux * push
                        centers[i, 1] -= uy * push
                        centers[j, 0] += ux * push
                        centers[j, 1] += uy * push
                        # Clamp back to boundary feasibility
                        for k in (i, j):
                            centers[k, 0] = min(max(centers[k, 0], radii[k] + eps), 1.0 - radii[k] - eps)
                            centers[k, 1] = min(max(centers[k, 1], radii[k] + eps), 1.0 - radii[k] - eps)
            if not any_overlap:
                break

    def feasibility_projection(centers: np.ndarray, radii: np.ndarray) -> None:
        """Project centers back to satisfy boundary constraints exactly."""
        centers[:, 0] = np.minimum(np.maximum(centers[:, 0], radii + eps), 1.0 - radii - eps)
        centers[:, 1] = np.minimum(np.maximum(centers[:, 1], radii + eps), 1.0 - radii - eps)

    def compute_pairwise_slacks(centers: np.ndarray, radii: np.ndarray) -> np.ndarray:
        """Compute pairwise slacks s_ij = d_ij - (r_i + r_j)."""
        n = centers.shape[0]
        slacks = np.empty((n, n), dtype=float)
        for i in range(n):
            dx = centers[i, 0] - centers[:, 0]
            dy = centers[i, 1] - centers[:, 1]
            d = np.hypot(dx, dy)
            slacks[i, :] = d - (radii[i] + radii)
            slacks[i, i] = np.inf
        return slacks

    def lp_inflate(centers: np.ndarray):
        """Fixed-center LP: maximize sum_i r_i subject to:
           - 0 <= r_i <= m_i where m_i = min(x_i, 1-x_i, y_i, 1-y_i)
           - r_i + r_j <= d_ij - eps for all i < j
           Uses scipy.optimize.linprog if available. Returns optimized radii or None on failure.
        """
        # Lazy import to keep robustness if SciPy is unavailable.
        try:
            from scipy.optimize import linprog
        except Exception:
            return None

        n = centers.shape[0]
        # Boundary caps
        m = np.minimum.reduce([centers[:, 0], 1.0 - centers[:, 0], centers[:, 1], 1.0 - centers[:, 1]])
        m = np.maximum(m, 0.0)  # ensure nonnegative bounds

        # Objective: maximize sum r_i  <=> minimize -sum r_i
        c = -np.ones(n, dtype=float)

        # Pairwise constraints: r_i + r_j <= d_ij - eps
        # Build A_ub and b_ub
        rows = []
        rhs = []
        for i in range(n):
            for j in range(i + 1, n):
                dx = centers[i, 0] - centers[j, 0]
                dy = centers[i, 1] - centers[j, 1]
                dij = math.hypot(dx, dy)
                b = max(0.0, dij - eps)  # clamp to 0 for robustness if centers coincide
                row = np.zeros(n, dtype=float)
                row[i] = 1.0
                row[j] = 1.0
                rows.append(row)
                rhs.append(b)
        if rows:
            A_ub = np.vstack(rows)
            b_ub = np.array(rhs, dtype=float)
        else:
            A_ub = None
            b_ub = None

        # Variable bounds
        bounds = [(0.0, float(mi)) for mi in m]

        # Solve with HiGHS
        try:
            res = linprog(c=c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        except Exception:
            return None

        if not res.success or res.x is None:
            return None

        radii_opt = np.asarray(res.x, dtype=float)
        # Numerical guard: clip slightly within bounds
        radii_opt = np.minimum(radii_opt, m)
        radii_opt = np.maximum(radii_opt, 0.0)
        return radii_opt

    def growth_and_move(centers: np.ndarray, radii: np.ndarray, iterations: int = 250) -> Tuple[np.ndarray, np.ndarray]:
        """Run iterative coordinated growth of radii and gentle center moves to reduce constraints.

        Every K iterations, freeze centers and run lp_inflate to globally re-optimize radii.
        """
        n = centers.shape[0]
        trust_radius = 0.02
        near_thresh = 0.03  # thresholds for activating constraints (neighbors/boundary)
        beta = 0.4  # fraction of available slack to consume per coordinate ascent step
        K = 50      # LP correction frequency

        best_centers = centers.copy()
        best_radii = radii.copy()
        best_sum = float(np.sum(best_radii))

        for it in range(iterations):
            # Compute slacks
            slacks = compute_pairwise_slacks(centers, radii)
            # Per-circle minimal slack including boundaries
            boundary_slacks = np.minimum.reduce([
                centers[:, 0] - radii,
                1.0 - radii - centers[:, 0],
                centers[:, 1] - radii,
                1.0 - radii - centers[:, 1],
            ])
            min_pair_slack = np.min(slacks, axis=1)
            avail_slack = np.minimum(boundary_slacks, min_pair_slack)

            # Coordinate ascent on radii: increase one-by-one respecting current active constraints
            order = np.argsort(-avail_slack)  # descending order by available slack
            delta_r = np.zeros(n, dtype=float)
            for idx in order:
                # Updated boundary slack with current delta
                bslack = min(
                    centers[idx, 0] - (radii[idx] + delta_r[idx]),
                    1.0 - (radii[idx] + delta_r[idx]) - centers[idx, 0],
                    centers[idx, 1] - (radii[idx] + delta_r[idx]),
                    1.0 - (radii[idx] + delta_r[idx]) - centers[idx, 1],
                )
                # Updated pairwise slacks with current delta for previously updated indices
                smax = bslack
                for j in range(n):
                    if j == idx:
                        continue
                    sij = slacks[idx, j] - (delta_r[idx] + delta_r[j])
                    if sij < smax:
                        smax = sij
                # Propose increase within trust in radius space
                dr = max(0.0, beta * smax)
                delta_r[idx] += dr

            # Apply delta radii
            radii += delta_r

            # Gentle center moves to relieve near constraints
            move = np.zeros_like(centers)
            for i in range(n):
                # Boundary pushes
                left = centers[i, 0] - radii[i]
                right = 1.0 - radii[i] - centers[i, 0]
                bottom = centers[i, 1] - radii[i]
                top = 1.0 - radii[i] - centers[i, 1]
                # Push inside if close to boundary
                if left < near_thresh:
                    move[i, 0] += (near_thresh - left)
                if right < near_thresh:
                    move[i, 0] -= (near_thresh - right)
                if bottom < near_thresh:
                    move[i, 1] += (near_thresh - bottom)
                if top < near_thresh:
                    move[i, 1] -= (near_thresh - top)

                # Neighbor pushes
                for j in range(n):
                    if j == i:
                        continue
                    dx = centers[i, 0] - centers[j, 0]
                    dy = centers[i, 1] - centers[j, 1]
                    dij = math.hypot(dx, dy)
                    # Active only for near interactions
                    sij = dij - (radii[i] + radii[j])
                    if sij < near_thresh:
                        # Push i away from j
                        if dij < 1e-12:
                            # Random direction if coincident
                            theta = rng.uniform(0.0, 2.0 * math.pi)
                            ux, uy = math.cos(theta), math.sin(theta)
                        else:
                            ux, uy = dx / dij, dy / dij
                        strength = (near_thresh - sij)
                        move[i, 0] += ux * strength * 0.5
                        move[i, 1] += uy * strength * 0.5

            # Trust-region on movement
            norms = np.hypot(move[:, 0], move[:, 1])
            scale = np.minimum(1.0, np.divide(trust_radius, norms, out=np.ones_like(norms), where=norms > 0))
            centers += move * scale[:, None]

            # Project to feasibility, then polish overlaps
            feasibility_projection(centers, radii)
            resolve_overlaps(centers, radii, max_iter=200)

            # Periodic exact LP inflation on fixed centers
            if (it + 1) % K == 0:
                r_lp = lp_inflate(centers)
                if r_lp is not None:
                    sum_before = float(np.sum(radii))
                    sum_after = float(np.sum(r_lp))
                    if sum_after > sum_before + 1e-12:
                        radii = r_lp
                        # Projection for numerical safety
                        feasibility_projection(centers, radii)

            # Keep best feasible solution so far (sum of radii)
            current_sum = float(np.sum(radii))
            if current_sum > best_sum + 1e-12:
                best_sum = current_sum
                best_centers = centers.copy()
                best_radii = radii.copy()

            # Basic stagnation check and trust radius adjustment
            if it % 50 == 49:
                # If no improvement over last block, slightly reduce trust radius
                if best_sum < current_sum + 1e-9:
                    trust_radius = max(trust_radius * 0.7, 1e-4)

        return best_centers, best_radii

    def global_uniform_inflation(centers: np.ndarray, radii: np.ndarray) -> None:
        """Uniformly scale (x, y, r) about the square center to saturate a boundary."""
        c = 0.5
        n = centers.shape[0]
        s_max = np.inf

        for i in range(n):
            x, y, r = centers[i, 0], centers[i, 1], radii[i]
            # Left: c + s*(x - c - r) >= 0
            aL = x - c - r
            if aL < 0:
                s_max = min(s_max, c / (-aL))
            # Right: 0.5 - s*(x - c + r) >= 0
            aR = x - c + r
            if aR > 0:
                s_max = min(s_max, 0.5 / aR)
            # Bottom: c + s*(y - c - r) >= 0
            aB = y - c - r
            if aB < 0:
                s_max = min(s_max, c / (-aB))
            # Top: 0.5 - s*(y - c + r) >= 0
            aT = y - c + r
            if aT > 0:
                s_max = min(s_max, 0.5 / aT)

        if not np.isfinite(s_max) or s_max <= 1.0:
            return  # nothing to do

        # Apply scaling with a tiny safety margin
        s = max(1.0, s_max * (1.0 - 1e-12))
        centers[:, 0] = c + s * (centers[:, 0] - c)
        centers[:, 1] = c + s * (centers[:, 1] - c)
        radii *= s
        # No overlaps are introduced by uniform scaling; reproject to boundary for numerical safety.
        feasibility_projection(centers, radii)

    def run_seed(centers: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Run the optimization pipeline on a given seed; return best feasible packing.

        Integration points for LP inflation:
        - After initial projection and overlap resolution, run lp_inflate once on fixed centers.
        - Inside the growth_and_move loop, run lp_inflate every K iterations.
        - After global uniform inflation, run lp_inflate again before final polish.
        """
        radii = initialize_radii(centers)
        feasibility_projection(centers, radii)
        resolve_overlaps(centers, radii, max_iter=200)

        # One-shot LP inflation to remove slack from equal-radius initialization
        r_lp = lp_inflate(centers)
        if r_lp is not None:
            if float(np.sum(r_lp)) > float(np.sum(radii)) + 1e-12:
                radii = r_lp
                feasibility_projection(centers, radii)

        centers, radii = growth_and_move(centers, radii, iterations=300)

        # Final global inflation to reach boundaries
        global_uniform_inflation(centers, radii)

        # Final LP inflation to exploit any slack created by uniform scaling
        r_lp = lp_inflate(centers)
        if r_lp is not None:
            if float(np.sum(r_lp)) > float(np.sum(radii)) + 1e-12:
                radii = r_lp
                feasibility_projection(centers, radii)

        # Final polish
        resolve_overlaps(centers, radii, max_iter=200)
        feasibility_projection(centers, radii)
        return centers, radii

    # Multi-start: iterate patterns and nearby lattice spacings with small jitters
    best_centers = None
    best_radii = None
    best_sum = -np.inf

    # Lattice scale factors to try around the base spacing
    scale_factors = [0.98, 1.0, 1.02]

    for pattern in patterns:
        for sf in scale_factors:
            s = s_base * sf
            v = (math.sqrt(3) / 2.0) * s
            base_centers = hex_seed(pattern, s, v)
            # Jittered restarts per seed increased from 4 to 6
            for j in range(6):
                # Tiny jitter to break symmetry
                jitter = (rng.rand(*base_centers.shape) - 0.5) * 0.002
                centers = base_centers + jitter
                # Ensure initial centers stay inside [0,1]
                centers[:, 0] = np.clip(centers[:, 0], 0.0, 1.0)
                centers[:, 1] = np.clip(centers[:, 1], 0.0, 1.0)

                c_opt, r_opt = run_seed(centers.copy())
                sum_r = float(np.sum(r_opt))
                if sum_r > best_sum + 1e-12:
                    best_sum = sum_r
                    best_centers = c_opt.copy()
                    best_radii = r_opt.copy()

    # Final polish: enforce tiny clearance and exact feasibility
    feasibility_projection(best_centers, best_radii)
    resolve_overlaps(best_centers, best_radii, max_iter=500)
    feasibility_projection(best_centers, best_radii)
    # Apply tiny shrink to ensure strict clearance on all constraints
    best_radii *= (1.0 - final_clearance)

    return np.column_stack((best_centers, best_radii))


# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```

```python
#!/usr/bin/env python3
"""SOCP-inspired packing of 21 circles inside a unit square (perimeter 4).

We construct exactly 21 disjoint circles, each fully contained in [0,1]x[0,1],
so the minimal axis-aligned circumscribing rectangle has perimeter exactly 4.
We maximize the sum of radii approximately via a smooth interior-barrier ascent.

Core idea:
- Fix the frame to a unit square, ensuring perimeter 4.
- Variables: centers (x_i, y_i) and radii r_i >= 0 for i=1..21.
- Constraints:
  * Non-overlap: ||(xi - xj, yi - yj)|| >= r_i + r_j + eps
  * In-box: r_i + eps <= x_i <= 1 - r_i - eps and same for y
  * Positivity: r_i > 0
- Objective: maximize sum(r_i).

We implement a smooth barrier objective:
  f(x,y,r) = sum(r_i) + mu * (sum over constraints of log(slack))
with slacks s>0 for all constraints:
  s_ij = ||d_ij|| - (r_i + r_j + eps)
  s_xL = x_i - r_i - eps
  s_xR = 1 - r_i - eps - x_i
  s_yB = y_i - r_i - eps
  s_yT = 1 - r_i - eps - y_i
  s_r  = r_i

We then run a gradient-ascent with backtracking line search for decreasing mu.

Mutation implemented here:
- Keep the unit-square frame and the interior-barrier ascent intact.
- Add two proven enhancements:
  1) Multi-pattern hex-like seeding with tiny jitter and best-of selection:
     try five row-count patterns (including the legacy 5-4-5-4-3), seed centers
     evenly per row, apply ±0.002 jitter, run barrier from each, keep the best ∑r.
  2) Final fixed-center LP inflation:
     after choosing the best barrier result, freeze centers and maximize ∑r over
     radii using a small LP (SciPy HiGHS). Accept only if ∑r increases, then
     re-clip centers for safety.
"""

import json
import math
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 21) -> np.ndarray:
    """Construct 21 disjoint circles inside a unit square maximizing sum radii.

    Returns:
        numpy array of shape (num_circles, 3) with rows [x, y, r].
    """
    if num_circles != 21:
        # Fallback to a safe uniform tiny packing if asked for other sizes
        n = num_circles
        grid = int(math.ceil(math.sqrt(n)))
        # Evenly space in a grid, tiny radii to ensure disjointness and containment
        r0 = 0.01
        xs = np.linspace(r0 + 1e-4, 1 - r0 - 1e-4, grid)
        ys = np.linspace(r0 + 1e-4, 1 - r0 - 1e-4, grid)
        pts = []
        for yy in ys:
            for xx in xs:
                pts.append([xx, yy])
                if len(pts) == n:
                    break
            if len(pts) == n:
                break
        centers = np.array(pts, dtype=float)
        radii = np.full(n, r0, dtype=float)
        return np.column_stack((centers, radii))

    # Parameters
    N = num_circles
    eps = 1e-4  # small margin to ensure strict separation and containment
    tiny = 1e-12

    # Barrier ascent hyperparameters (unchanged from parent)
    mu_schedule = [5e-2, 2e-2, 1e-2, 5e-3, 3e-3, 1e-3]
    iters_per_mu = 300
    base_step_init = 0.05
    min_step = 1e-8
    backtrack = 0.5

    rng = np.random.default_rng()

    def build_centers(row_counts: List[int]) -> np.ndarray:
        """Build evenly spaced centers from row-count pattern inside [0,1]^2."""
        n_rows = len(row_counts)
        ys = (np.arange(n_rows) + 0.5) / n_rows  # evenly spaced rows in [0,1]
        centers_: List[Tuple[float, float]] = []
        for k, nc in enumerate(row_counts):
            xs = (np.arange(nc) + 0.5) / nc
            yk = ys[k]
            for xk in xs:
                centers_.append((float(xk), float(yk)))
        centers_arr = np.array(centers_, dtype=float)
        assert centers_arr.shape[0] == N
        return centers_arr

    def jitter_centers(centers_arr: np.ndarray, amp: float = 0.002) -> np.ndarray:
        """Apply tiny uniform jitter ±amp to break symmetry; clamp to [0,1]."""
        jitter = rng.uniform(low=-amp, high=amp, size=centers_arr.shape)
        cj = centers_arr + jitter
        np.clip(cj, 0.0, 1.0, out=cj)
        return cj

    def run_barrier_from_seed(centers_arr: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Run the inherited barrier ascent starting from the given centers."""
        # Initialize small radii to ensure a strictly feasible start
        r0 = 0.01
        x = centers_arr[:, 0].copy()
        y = centers_arr[:, 1].copy()
        r = np.full(N, r0, dtype=float)

        # Utility: build all unordered pairs i<j for pairwise constraints
        pair_i = []
        pair_j = []
        for i in range(N):
            for j in range(i + 1, N):
                pair_i.append(i)
                pair_j.append(j)
        pair_i_arr = np.array(pair_i, dtype=int)
        pair_j_arr = np.array(pair_j, dtype=int)

        def compute_slacks(xv, yv, rv):
            # Pairwise distances
            dx = xv[pair_i_arr] - xv[pair_j_arr]
            dy = yv[pair_i_arr] - yv[pair_j_arr]
            d = np.hypot(dx, dy)
            # Avoid division issues: ensure d >= tiny
            d = np.maximum(d, tiny)
            s_pairs = d - (rv[pair_i_arr] + rv[pair_j_arr] + eps)

            # Boundary slacks
            s_xL = xv - rv - eps
            s_xR = 1.0 - rv - eps - xv
            s_yB = yv - rv - eps
            s_yT = 1.0 - rv - eps - yv
            # Positive radii
            s_r = rv

            return (dx, dy, d, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r)

        def feasible(slacks) -> bool:
            # All slacks must be strictly positive for barrier
            for arr in slacks[3:]:
                if np.any(arr <= 0.0):
                    return False
            return True

        def objective(mu, slacks_) -> float:
            # f = sum r + mu * sum log(slacks)
            _, _, _, s_pairs_, s_xL_, s_xR_, s_yB_, s_yT_, s_r_ = slacks_
            logs = (
                np.sum(np.log(s_pairs_))
                + np.sum(np.log(s_xL_))
                + np.sum(np.log(s_xR_))
                + np.sum(np.log(s_yB_))
                + np.sum(np.log(s_yT_))
                + np.sum(np.log(s_r_))
            )
            return float(np.sum(r)) + mu * float(logs)

        # Ensure initial feasibility and tiny inflation if margins too tight
        slacks = compute_slacks(x, y, r)
        if not feasible(slacks):
            # Reduce r0 further and reset
            r = np.full(N, 0.005, dtype=float)
            slacks = compute_slacks(x, y, r)
            if not feasible(slacks):
                # Pull centers slightly towards middle and reduce r
                x = 0.5 + 0.8 * (x - 0.5)
                y = 0.5 + 0.8 * (y - 0.5)
                r = np.full(N, 0.003, dtype=float)
                slacks = compute_slacks(x, y, r)

        # Perform barrier ascent
        base_step = base_step_init
        for mu in mu_schedule:
            for _ in range(iters_per_mu):
                dx, dy, d, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r = slacks

                # Gradients initialization
                grad_x = np.zeros(N, dtype=float)
                grad_y = np.zeros(N, dtype=float)
                grad_r = np.ones(N, dtype=float)  # from sum(r_i)

                # Pairwise constraints gradient
                coef = mu / (s_pairs * d)
                # Accumulate to i
                np.add.at(grad_x, pair_i_arr, coef * dx)
                np.add.at(grad_y, pair_i_arr, coef * dy)
                # Accumulate to j (negative sign)
                np.add.at(grad_x, pair_j_arr, -coef * dx)
                np.add.at(grad_y, pair_j_arr, -coef * dy)
                # Radii contributions
                coef_r = mu / s_pairs
                np.add.at(grad_r, pair_i_arr, -coef_r)
                np.add.at(grad_r, pair_j_arr, -coef_r)

                # Boundary constraints gradients via slacks
                # Left: s_xL = x - r - eps
                grad_x += mu / s_xL
                grad_r += -mu / s_xL
                # Right: s_xR = 1 - r - eps - x
                grad_x += -mu / s_xR
                grad_r += -mu / s_xR
                # Bottom: s_yB = y - r - eps
                grad_y += mu / s_yB
                grad_r += -mu / s_yB
                # Top: s_yT = 1 - r - eps - y
                grad_y += -mu / s_yT
                grad_r += -mu / s_yT
                # Positivity: s_r = r
                grad_r += mu / s_r

                # Backtracking line search for feasibility and ascent
                step = base_step
                f_curr = objective(mu, slacks)
                improved = False
                for _bt in range(40):
                    xn = x + step * grad_x
                    yn = y + step * grad_y
                    rn = r + step * grad_r
                    # Compute new slacks
                    slacks_n = compute_slacks(xn, yn, rn)
                    # Note: feasible() expects strictly positive slacks
                    if feasible(slacks_n):
                        f_new = (np.sum(rn) + mu * (
                            np.sum(np.log(slacks_n[3]))
                            + np.sum(np.log(slacks_n[4]))
                            + np.sum(np.log(slacks_n[5]))
                            + np.sum(np.log(slacks_n[6]))
                            + np.sum(np.log(slacks_n[7]))
                            + np.sum(np.log(slacks_n[8]))
                        ))
                        if f_new >= f_curr:
                            # Accept
                            x, y, r = xn, yn, rn
                            slacks = slacks_n
                            improved = True
                            break
                    # Reduce step
                    step *= backtrack
                    if step < min_step:
                        break
                if not improved:
                    # If no improvement, slightly reduce base step to avoid oscillations
                    base_step *= 0.9
                    if base_step < min_step:
                        # If we are stuck, break early for this mu
                        break

        # Final polishing: small projection to ensure strict feasibility
        r = np.maximum(r, 1e-6)
        # Clamp x,y into [r+eps, 1-r-eps]
        x = np.clip(x, r + eps, 1.0 - r - eps)
        y = np.clip(y, r + eps, 1.0 - r - eps)

        # Ensure pairwise separations by small shrinking if needed (safety net)
        for _ in range(3):
            dx = x[pair_i_arr] - x[pair_j_arr]
            dy = y[pair_i_arr] - y[pair_j_arr]
            d = np.hypot(dx, dy)
            need = r[pair_i_arr] + r[pair_j_arr] + eps
            viol = need - d
            if np.all(viol <= 0):
                break
            # Reduce the larger of the two radii slightly where violated
            for k in range(len(pair_i_arr)):
                if viol[k] > 0:
                    i = pair_i_arr[k]
                    j = pair_j_arr[k]
                    if r[i] >= r[j]:
                        r[i] = max(r[i] - 0.5 * viol[k], 1e-6)
                    else:
                        r[j] = max(r[j] - 0.5 * viol[k], 1e-6)
            # Re-clip x,y into box after radius adjustments
            x = np.clip(x, r + eps, 1.0 - r - eps)
            y = np.clip(y, r + eps, 1.0 - r - eps)

        return x, y, r

    def lp_inflate_fixed_centers(x: np.ndarray, y: np.ndarray, r: np.ndarray) -> np.ndarray:
        """With centers fixed, maximize sum of radii via a small LP.

        Constraints:
          0 <= r_i <= m_i where m_i = min(x_i, 1−x_i, y_i, 1−y_i) − eps
          r_i + r_j <= d_ij − eps for all i<j
        If SciPy is unavailable, return the input radii unchanged.
        """
        try:
            from scipy.optimize import linprog  # type: ignore
        except Exception:
            return r  # SciPy not available; skip gracefully

        # Upper bounds from the box, ensure non-negative
        m = np.minimum.reduce([x, 1.0 - x, y, 1.0 - y]) - eps
        m = np.maximum(m, 0.0)

        # Pairwise constraints r_i + r_j <= d_ij - eps
        pair_i = []
        pair_j = []
        for i in range(N):
            for j in range(i + 1, N):
                pair_i.append(i)
                pair_j.append(j)
        pair_i = np.array(pair_i, dtype=int)
        pair_j = np.array(pair_j, dtype=int)

        dx = x[pair_i] - x[pair_j]
        dy = y[pair_i] - y[pair_j]
        d = np.hypot(dx, dy)
        b_ub = d - eps

        M = len(pair_i)
        # Build A_ub sparse-like in dense form (small N, fine)
        A_ub = np.zeros((M, N), dtype=float)
        A_ub[np.arange(M), pair_i] = 1.0
        A_ub[np.arange(M), pair_j] = 1.0

        # Objective maximize sum r -> minimize -sum r
        c = -np.ones(N, dtype=float)
        bounds = [(0.0, float(m[i])) for i in range(N)]

        try:
            res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        except Exception:
            return r  # Solver failed/unsupported, keep current radii

        if not res.success or res.x is None:
            return r

        r_lp = np.asarray(res.x, dtype=float)
        if np.sum(r_lp) > np.sum(r) + 1e-12:
            return r_lp
        return r

    def safety_project(x: np.ndarray, y: np.ndarray, r: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Final safety projection to enforce containment and separation."""
        # Clamp into box
        r = np.maximum(r, 1e-6)
        x = np.clip(x, r + eps, 1.0 - r - eps)
        y = np.clip(y, r + eps, 1.0 - r - eps)

        # Pairwise safety shrink (few passes)
        pair_i = []
        pair_j = []
        for i in range(N):
            for j in range(i + 1, N):
                pair_i.append(i)
                pair_j.append(j)
        pair_i = np.array(pair_i, dtype=int)
        pair_j = np.array(pair_j, dtype=int)

        for _ in range(3):
            dx = x[pair_i] - x[pair_j]
            dy = y[pair_i] - y[pair_j]
            d = np.hypot(dx, dy)
            need = r[pair_i] + r[pair_j] + eps
            viol = need - d
            if np.all(viol <= 0):
                break
            for k in range(len(pair_i)):
                if viol[k] > 0:
                    i = pair_i[k]
                    j = pair_j[k]
                    if r[i] >= r[j]:
                        r[i] = max(r[i] - 0.5 * viol[k], 1e-6)
                    else:
                        r[j] = max(r[j] - 0.5 * viol[k], 1e-6)
            x = np.clip(x, r + eps, 1.0 - r - eps)
            y = np.clip(y, r + eps, 1.0 - r - eps)
        return x, y, r

    # Multi-pattern hex-like seeding with tiny jitter and best-of selection
    patterns = [
        [5, 4, 5, 4, 3],  # legacy pattern (sums to 21)
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
    ]
    jitter_restarts_per_pattern = 3
    jitter_amp = 0.002

    best_x = None
    best_y = None
    best_r = None
    best_sum_r = -1.0

    for row_counts in patterns:
        base_centers = build_centers(row_counts)
        for _restart in range(jitter_restarts_per_pattern):
            seed_centers = jitter_centers(base_centers, amp=jitter_amp)
            x, y, r = run_barrier_from_seed(seed_centers)
            s = float(np.sum(r))
            if s > best_sum_r:
                best_sum_r = s
                best_x, best_y, best_r = x.copy(), y.copy(), r.copy()

    # At this point we have the best barrier result across seeds.
    # Run fixed-center LP inflation once to remove residual slack in radii.
    if best_x is None:
        # Should not happen, but fallback to a safe uniform tiny packing
        centers = build_centers([5, 4, 5, 4, 3])
        r0 = 0.01
        return np.column_stack((centers, np.full(N, r0, dtype=float)))

    r_lp = lp_inflate_fixed_centers(best_x, best_y, best_r)
    # Accept LP radii only if improved
    if np.sum(r_lp) > np.sum(best_r) + 1e-12:
        best_r = r_lp
        # Re-clip centers into [r+eps, 1-r-eps] for numerical safety
        best_x = np.clip(best_x, best_r + eps, 1.0 - best_r - eps)
        best_y = np.clip(best_y, best_r + eps, 1.0 - best_r - eps)

    # Final safety projection (kept consistent with the parent implementation)
    best_x, best_y, best_r = safety_project(best_x, best_y, best_r)

    circles = np.column_stack((best_x, best_y, best_r))
    return circles


# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```

```python
#!/usr/bin/env python3
"""SOCP-inspired packing of 21 circles inside a unit square (perimeter 4).

We construct exactly 21 disjoint circles, each fully contained in [0,1]x[0,1],
so the minimal axis-aligned circumscribing rectangle has perimeter exactly 4.
We maximize the sum of radii approximately via a smooth interior-barrier ascent.

Core idea:
- Fix the frame to a unit square, ensuring perimeter 4.
- Variables: centers (x_i, y_i) and radii r_i >= 0 for i=1..21.
- Constraints:
  * Non-overlap: ||(xi - xj, yi - yj)|| >= r_i + r_j + eps
  * In-box: r_i + eps <= x_i <= 1 - r_i - eps and same for y
  * Positivity: r_i > 0
- Objective: maximize sum(r_i).

We implement a smooth barrier objective:
  f(x,y,r) = sum(r_i) + mu * (sum over constraints of log(slack))
with slacks s>0 for all constraints:
  s_ij = ||d_ij|| - (r_i + r_j + eps)
  s_xL = x_i - r_i - eps
  s_xR = 1 - r_i - eps - x_i
  s_yB = y_i - r_i - eps
  s_yT = 1 - r_i - eps - y_i
  s_r  = r_i

We then run a gradient-ascent with backtracking line search for decreasing mu.

This is not a full SOCP solver; it's a robust, projected barrier ascent suitable
for this small instance. It produces a feasible packing and typically improves
over a uniform 5x5 grid baseline by reallocating radii/positions.
"""

import json
import math
from typing import List, Tuple, Optional

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 21) -> np.ndarray:
    """Construct 21 disjoint circles inside a unit square maximizing sum radii.

    Returns:
        numpy array of shape (num_circles, 3) with rows [x, y, r].
    """
    if num_circles != 21:
        # Fallback to a safe uniform tiny packing if asked for other sizes
        n = num_circles
        grid = int(math.ceil(math.sqrt(n)))
        # Evenly space in a grid, tiny radii to ensure disjointness and containment
        r0 = 0.01
        xs = np.linspace(r0 + 1e-4, 1 - r0 - 1e-4, grid)
        ys = np.linspace(r0 + 1e-4, 1 - r0 - 1e-4, grid)
        pts = []
        for yy in ys:
            for xx in xs:
                pts.append([xx, yy])
                if len(pts) == n:
                    break
            if len(pts) == n:
                break
        centers = np.array(pts, dtype=float)
        radii = np.full(n, r0, dtype=float)
        return np.column_stack((centers, radii))

    # Parameters
    N = num_circles
    # Reduce internal separation epsilon to release slack while preserving strict inequalities
    eps = 1e-6
    tiny = 1e-12

    # Utility: build all unordered pairs i<j for pairwise constraints
    pair_i = []
    pair_j = []
    for i in range(N):
        for j in range(i + 1, N):
            pair_i.append(i)
            pair_j.append(j)
    pair_i = np.array(pair_i, dtype=int)
    pair_j = np.array(pair_j, dtype=int)

    # Helper: exact LP inflation of radii for fixed centers (optional acceleration).
    # This removes residual slack for the current centers by solving:
    #   maximize sum r_i
    #   subject to 0 <= r_i <= m_i (m_i = min(x_i, 1-x_i, y_i, 1-y_i))
    #              r_i + r_j <= d_ij - eps
    # Uses scipy.optimize.linprog(method='highs') if available; otherwise no-op.
    def lp_inflate(centers_xy: np.ndarray, eps_lp: float) -> Optional[np.ndarray]:
        try:
            from scipy.optimize import linprog  # type: ignore
        except Exception:
            return None

        n = centers_xy.shape[0]
        x_c = centers_xy[:, 0]
        y_c = centers_xy[:, 1]
        # Boundary caps m_i based on fixed centers
        m = np.minimum.reduce([x_c, 1.0 - x_c, y_c, 1.0 - y_c])

        # Build pairwise constraints matrix A_ub r <= b_ub for r_i + r_j <= d_ij - eps
        num_pairs = n * (n - 1) // 2
        A_ub = np.zeros((num_pairs, n), dtype=float)
        b_ub = np.zeros(num_pairs, dtype=float)

        idx = 0
        for i in range(n):
            xi = x_c[i]
            yi = y_c[i]
            for j in range(i + 1, n):
                dx = xi - x_c[j]
                dy = yi - y_c[j]
                dij = math.hypot(dx, dy)
                rhs = max(dij - eps_lp, 0.0)  # ensure feasibility even if centers too close
                A_ub[idx, i] = 1.0
                A_ub[idx, j] = 1.0
                b_ub[idx] = rhs
                idx += 1

        # Objective: maximize sum r -> minimize -sum r
        c = -np.ones(n, dtype=float)
        bounds = [(0.0, float(mi)) for mi in m]

        res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        if not res.success or res.x is None:
            return None
        r_sol = np.asarray(res.x, dtype=float)
        # Clip numerical noise
        r_sol = np.maximum(r_sol, 0.0)
        return r_sol

    # Hex-like seed generator with true triangular-lattice staggering:
    # - pattern is a list of 5 row counts summing to 21, e.g., [5,4,4,4,4]
    # - horizontal spacing s; vertical spacing v = s*sqrt(3)/2
    # - rows centered vertically around 0.5 with y_k = 0.5 + (k-2)*v for k=0..4
    # - horizontally, alternate rows are shifted by a half-step:
    #     row_shift = ((k % 2) - 0.5) * 0.5 * s  -> {-0.25*s, +0.25*s, ...}
    #   and positions are:
    #     x = 0.5 + row_shift + (m - (n_k - 1)/2) * s,  m=0..n_k-1
    # - tiny jitter in [-1e-3,1e-3] to break symmetry, with clipping to interior
    def hex_seed(pattern: List[int], s: float, jitter: float = 1e-3) -> np.ndarray:
        v = s * (math.sqrt(3.0) / 2.0)
        centers: List[Tuple[float, float]] = []
        for k, n_k in enumerate(pattern):
            yk = 0.5 + (k - 2) * v
            row_shift = ((k % 2) - 0.5) * 0.5 * s
            for m in range(n_k):
                xk = 0.5 + row_shift + (m - (n_k - 1) / 2.0) * s
                centers.append((xk, yk))
        centers_arr = np.array(centers, dtype=float)
        # Add tiny jitter
        if jitter > 0:
            rng = np.random.default_rng()
            centers_arr += rng.uniform(-jitter, jitter, size=centers_arr.shape)
        # Clip into the unit square interior (leaving a tiny margin)
        centers_arr = np.clip(centers_arr, 1e-3, 1 - 1e-3)
        return centers_arr

    # Helper to run the barrier ascent + periodic LP inflation from an initial center seed.
    def run_barrier_from_seed(centers_init: np.ndarray) -> np.ndarray:
        # Initialize small radii to ensure a strictly feasible start
        r0 = 0.01
        x = centers_init[:, 0].copy()
        y = centers_init[:, 1].copy()
        r = np.full(N, r0, dtype=float)

        # Barrier ascent hyperparameters
        mu_schedule = [5e-2, 2e-2, 1e-2, 5e-3, 3e-3, 1e-3]
        iters_per_mu = 300
        base_step = 0.05
        min_step = 1e-8
        backtrack = 0.5

        def compute_slacks(xv, yv, rv):
            # Pairwise distances
            dx = xv[pair_i] - xv[pair_j]
            dy = yv[pair_i] - yv[pair_j]
            d = np.hypot(dx, dy)
            # Avoid division issues: ensure d >= tiny
            d = np.maximum(d, tiny)
            s_pairs = d - (rv[pair_i] + rv[pair_j] + eps)

            # Boundary slacks
            s_xL = xv - rv - eps
            s_xR = 1.0 - rv - eps - xv
            s_yB = yv - rv - eps
            s_yT = 1.0 - rv - eps - yv
            # Positive radii
            s_r = rv

            return (dx, dy, d, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r)

        def feasible(slacks) -> bool:
            # All slacks must be strictly positive for barrier
            for arr in slacks[3:]:
                if np.any(arr <= 0.0):
                    return False
            return True

        def objective(mu, slacks) -> float:
            # f = sum r + mu * sum log(slacks)
            _, _, _, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r = slacks
            logs = (
                np.sum(np.log(s_pairs))
                + np.sum(np.log(s_xL))
                + np.sum(np.log(s_xR))
                + np.sum(np.log(s_yB))
                + np.sum(np.log(s_yT))
                + np.sum(np.log(s_r))
            )
            return float(np.sum(r)) + mu * float(logs)

        # Ensure initial feasibility and tiny inflation if margins too tight
        slacks = compute_slacks(x, y, r)
        if not feasible(slacks):
            r = np.full(N, 0.005, dtype=float)
            slacks = compute_slacks(x, y, r)
            if not feasible(slacks):
                x = 0.5 + 0.8 * (x - 0.5)
                y = 0.5 + 0.8 * (y - 0.5)
                r = np.full(N, 0.003, dtype=float)
                slacks = compute_slacks(x, y, r)

        # Perform barrier ascent with periodic LP inflation after each mu stage
        for mu in mu_schedule:
            for _ in range(iters_per_mu):
                dx_, dy_, d_, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r = slacks

                # Gradients initialization
                grad_x = np.zeros(N, dtype=float)
                grad_y = np.zeros(N, dtype=float)
                grad_r = np.ones(N, dtype=float)  # from sum(r_i)

                # Pairwise constraints gradient
                coef = mu / (s_pairs * d_)
                np.add.at(grad_x, pair_i, coef * dx_)
                np.add.at(grad_x, pair_j, -coef * dx_)
                np.add.at(grad_y, pair_i, coef * dy_)
                np.add.at(grad_y, pair_j, -coef * dy_)
                coef_r = mu / s_pairs
                np.add.at(grad_r, pair_i, -coef_r)
                np.add.at(grad_r, pair_j, -coef_r)

                # Boundary constraints gradients via slacks
                grad_x += mu / s_xL
                grad_r += -mu / s_xL
                grad_x += -mu / s_xR
                grad_r += -mu / s_xR
                grad_y += mu / s_yB
                grad_r += -mu / s_yB
                grad_y += -mu / s_yT
                grad_r += -mu / s_yT
                # Positivity: s_r = r
                grad_r += mu / s_r

                # Backtracking line search for feasibility and ascent
                step = base_step
                f_curr = objective(mu, slacks)
                improved = False
                for _bt in range(40):
                    xn = x + step * grad_x
                    yn = y + step * grad_y
                    rn = r + step * grad_r
                    # Compute new slacks
                    slacks_n = compute_slacks(xn, yn, rn)
                    if feasible(slacks_n):
                        f_new = (np.sum(rn) + mu * (
                            np.sum(np.log(slacks_n[3]))
                            + np.sum(np.log(slacks_n[4]))
                            + np.sum(np.log(slacks_n[5]))
                            + np.sum(np.log(slacks_n[6]))
                            + np.sum(np.log(slacks_n[7]))
                            + np.sum(np.log(slacks_n[8]))
                        ))
                        if f_new >= f_curr:
                            x, y, r = xn, yn, rn
                            slacks = slacks_n
                            improved = True
                            break
                    step *= backtrack
                    if step < min_step:
                        break
                if not improved:
                    base_step *= 0.9
                    if base_step < min_step:
                        break

            # Periodic fixed-center LP inflation after barrier stage
            centers_now = np.column_stack((x, y))
            r_lp = lp_inflate(centers_now, eps)
            if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
                r = r_lp
                # Recompute slacks with updated radii
                slacks = compute_slacks(x, y, r)
                # Ensure feasibility (LP should enforce, but be defensive)
                if not feasible(slacks):
                    # Slight shrink to restore strict feasibility
                    r *= 0.999
                    slacks = compute_slacks(x, y, r)

        # Final fixed-center LP inflation before uniform scaling
        centers_now = np.column_stack((x, y))
        r_lp = lp_inflate(centers_now, eps)
        if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
            r = r_lp

        # Final global uniform inflation about square center (0.5, 0.5)
        def global_uniform_inflation(xv, yv, rv, eps_g):
            # Constraints for each circle i:
            # s_xL' = 0.5 - eps + t*(x-0.5 - r) >= 0
            # s_xR' = 0.5 - eps - t*(x-0.5 + r) >= 0
            # s_yB' = 0.5 - eps + t*(y-0.5 - r) >= 0
            # s_yT' = 0.5 - eps - t*(y-0.5 + r) >= 0
            A = 0.5 - eps_g
            # Compute all B coefficients
            B_xL = xv - 0.5 - rv
            B_xR = -(xv - 0.5 + rv)
            B_yB = yv - 0.5 - rv
            B_yT = -(yv - 0.5 + rv)

            t_ubs = []

            def append_bounds(B_arr):
                # For each entry with B < 0, the upper bound is -A/B
                mask = B_arr < 0
                if np.any(mask):
                    t_ubs.extend(list((-A / B_arr[mask])))

            append_bounds(B_xL)
            append_bounds(B_xR)
            append_bounds(B_yB)
            append_bounds(B_yT)

            if len(t_ubs) == 0:
                # No upper bounds -> already at boundary or centered; no scaling change
                return xv, yv, rv

            t_max = min(t_ubs)
            # We aim to expand; only apply if t_max > 1
            if t_max > 1.0 + 1e-12:
                t = t_max * (1.0 - 1e-12)
                xv2 = 0.5 + t * (xv - 0.5)
                yv2 = 0.5 + t * (yv - 0.5)
                rv2 = t * rv
                return xv2, yv2, rv2
            else:
                return xv, yv, rv

        x, y, r = global_uniform_inflation(x, y, r, eps)

        # One more LP inflation after uniform scaling
        centers_now = np.column_stack((x, y))
        r_lp = lp_inflate(centers_now, eps)
        if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
            r = r_lp

        # Final polishing: small projection to ensure strict feasibility
        r = np.maximum(r, 1e-6)
        x = np.clip(x, r + eps, 1.0 - r - eps)
        y = np.clip(y, r + eps, 1.0 - r - eps)

        # Ensure pairwise separations satisfy d >= ri + rj + eps by small shrinking if needed
        for _ in range(3):
            dxp = x[pair_i] - x[pair_j]
            dyp = y[pair_i] - y[pair_j]
            d = np.hypot(dxp, dyp)
            need = r[pair_i] + r[pair_j] + eps
            viol = need - d
            if np.all(viol <= 0):
                break
            for k in range(len(pair_i)):
                if viol[k] > 0:
                    i = pair_i[k]; j = pair_j[k]
                    if r[i] >= r[j]:
                        r[i] = max(r[i] - 0.5 * viol[k], 1e-6)
                    else:
                        r[j] = max(r[j] - 0.5 * viol[k], 1e-6)
            x = np.clip(x, r + eps, 1.0 - r - eps)
            y = np.clip(y, r + eps, 1.0 - r - eps)

        return np.column_stack((x, y, r))

    # Multi-start hex seeding:
    # Patterns with a single 5-count row in different positions plus an additional
    # [5, 4, 5, 4, 3] pattern to expand plausible high-quality contact graphs.
    patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
        [5, 4, 5, 4, 3],  # added pattern
    ]
    # Lattice spacings around 0.2 with slight variation
    spacings = [0.2 * 0.98, 0.2, 0.2 * 1.02]

    best_circles = None
    best_sum = -1.0

    rng_global = np.random.default_rng()

    for pat in patterns:
        for s in spacings:
            # Base seed with true hex staggering
            centers_seed = hex_seed(pat, s, jitter=1e-3)

            # Tiny multi-restarts per seed: create two additional jittered copies
            # using the same jitter magnitude (±1e−3), with clipping safeguards.
            seeds = [centers_seed]
            for _ in range(2):
                jitter = rng_global.uniform(-1e-3, 1e-3, size=centers_seed.shape)
                centers_j = np.clip(centers_seed + jitter, 1e-3, 1 - 1e-3)
                seeds.append(centers_j)

            # Run the pipeline for each seed and keep the best by sum of radii
            local_best = None
            local_best_sum = -1.0
            for seed in seeds:
                circles = run_barrier_from_seed(seed)
                sum_r = float(np.sum(circles[:, 2]))
                if sum_r > local_best_sum + tiny:
                    local_best_sum = sum_r
                    local_best = circles

            if local_best is not None and local_best_sum > best_sum + tiny:
                best_sum = local_best_sum
                best_circles = local_best

    assert best_circles is not None
    return best_circles
# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```
