Periodic fixed-center LP corrections that remove residual slack with negligible overhead and robust fallbacks.

- lp_inflate(centers) maximizes the sum of radii for fixed centers subject to r_i ≥ 0, r_i ≤ m_i where m_i = min(x_i, 1 − x_i, y_i, 1 − y_i), and pairwise constraints r_i + r_j ≤ d_ij − ε with ε a tiny clearance (e.g., 1e−12) to preserve strict feasibility.
- Periodic Exact LP Inflation on Fixed Centers: The algorithm calls lp_inflate after initial jitter/projection/overlap resolution in run_seed, every K = 50 iterations inside growth_and_move with centers frozen (accepting the new radii only if the sum of radii increases), and once after global_uniform_inflation before final overlap polish and feasibility projection.
- lp_inflate(centers): The LP has 21 variables with approximately 210 pairwise constraints and 42 bound constraints, is solved in milliseconds using a standard LP solver, and falls back to keeping current radii unchanged if the solver is unavailable or fails.
- Periodic Exact LP Inflation on Fixed Centers: Periodic exact LP corrections eliminate residual slack left by local growth and help escape suboptimal radius patterns with negligible runtime overhead at n = 21, as reflected by sum_radii 2.298005016231055 with validity 1.0 in this run.
- LP-Preselect + Post-Stage LP with Centered Uniform Inflation Gate: The method freezes centers and rectangle sides to solve a fixed-center LP that reallocates radii with a −eps feasibility margin, and it accepts the LP update only when the sum of radii strictly increases, preserving strict feasibility while reclaiming slack left by nonlinear steps.
- Fixed-center LP preselection of seeds: After multi-start seed generation, it runs one fixed-center LP per seed, ranks by Σ r, keeps only the top K=2 seeds, initializes SLSQP with the LP radii, and falls back to the original seeds if SciPy is unavailable or the LP fails.
- Centered uniform inflation gate: Near the end, it uniformly scales centers and radii about the rectangle center by the largest t ≥ 1 that satisfies all boundary inequalities, then applies the fixed-center LP and accepts the change only if Σ r increases, leveraging that pairwise distances and radii scale equally to preserve non-overlap.
- LP-Preselect + Post-Stage LP with Centered Uniform Inflation Gate: In this run it outperformed its parent by converting structured slack into objective value, achieving sum_radii 2.339366090823248 with validity 1.0 versus parent_score 2.3326301114441557 at N=21.

```python
#!/usr/bin/env python3
"""High-density packing of 21 disjoint circles inside the unit square.

Algorithm overview:
- Reduce the perimeter constraint (width + height ≤ 2) to packing inside the unit square.
- Seed with near-hexagonal lattice patterns [5,4,4,4,4] and [4,5,4,4,4].
- Initialize equal radii at the maximum non-overlapping value given the seed.
- Iteratively grow radii using a coordinated, feasibility-aware update and gently move centers
  using repulsive forces from near neighbors and boundaries (trust-region-like behavior).
- Periodically and at key points, freeze centers and solve a small linear program (LP) to
  globally re-optimize radii exactly for those fixed centers (lp_inflate).
- After each growth/move, project back to feasibility and polish overlaps by separating pairs.
- Multi-start with small jitter to escape symmetric local optima; keep the best result.
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
"""Improved candidate for packing 21 circles in a perimeter-four rectangle.

Implements the discrete perimeter-balanced two-scale construction with polishing:

Core components:
- Discrete perimeter-balanced search for the best m_x × m_y grid and interstitial count t.
- Two-scale seed geometry: base circles arranged on a rectangular grid, plus up to t
  interstitial circles placed at cell centers.
- Clearance-aware interstitial selection with deterministic tie-breaking; multi-start seeds.
- Optional nonlinear polishing (if SciPy is available) that optimizes centers, radii,
  and rectangle sides W, H under:
    • non-overlap,
    • boundary containment,
    • perimeter budget W + H = 2 (active at optimum).
  Uses sparse adjacency (grid/knn/interstitial neighbors) and lazy constraint augmentation,
  plus epsilon annealing from 1e−6 → 1e−12.
- Final uniform shrink δ = 1e−12 for strict disjointness.

The result is 21 disjoint circles inside an axis-aligned rectangle with perimeter = 4,
with a strong sum of radii for N = 21.
"""

import json
from typing import List, Tuple, Optional, Dict, Set

import numpy as np


# EVOLVE_START
def _select_grid_for_N(N: int) -> Tuple[int, int, int]:
    """Enumerate feasible (m_x, m_y) grids and select the best by the score S.

    A grid (m_x, m_y) can host B = m_x*m_y base circles and up to
    I = (m_x - 1)*(m_y - 1) interstitials. For target N, set t = N - B and
    require 0 <= t <= I.

    Score:
      S = [B + α (N − B)] / (m_x + m_y), where α = sqrt(2) − 1.

    Tie-breakers:
      1) minimize (m_x + m_y)
      2) minimize |m_x − m_y|
      3) lexicographic (m_x, m_y)

    Returns:
      (m_x, m_y, t)
    """
    if N <= 0:
        return (1, 1, 0)

    alpha = np.sqrt(2.0) - 1.0

    best = None
    best_key = None

    # Enumerate reasonable ranges; for safety cap at N (sufficient).
    for mx in range(1, N + 1):
        for my in range(1, N + 1):
            B = mx * my
            if B > N:
                continue
            I = (mx - 1) * (my - 1) if mx > 0 and my > 0 else 0
            t = N - B
            if t < 0 or t > I:
                continue

            S = (B + alpha * (N - B)) / (mx + my)

            # Build sorting key: maximize S -> minimize -S
            key = (-S, mx + my, abs(mx - my), mx, my)
            if best is None or key < best_key:
                best = (mx, my, t)
                best_key = key

    if best is None:
        # Fallback (should not happen with the above enumeration)
        best = (1, N, 0)

    return best


def _grid_seed_geometry(mx: int, my: int) -> Tuple[float, float, float, float]:
    """Return base geometry values for the grid:
    r0_exact, W, H, r1_exact such that W + H = 2 and r1_exact = (sqrt(2) - 1) * r0_exact.
    """
    r0_exact = 1.0 / float(mx + my)
    W = 2.0 * mx * r0_exact
    H = 2.0 * my * r0_exact
    r1_exact = r0_exact * (np.sqrt(2.0) - 1.0)
    return r0_exact, W, H, r1_exact


def _base_centers(mx: int, my: int, W: float, H: float) -> np.ndarray:
    """Compute m_x × m_y base centers on a regular grid."""
    xs = (np.arange(mx) + 0.5) * W / mx
    ys = (np.arange(my) + 0.5) * H / my
    centers = np.array([(x, y) for y in ys for x in xs], dtype=float)
    return centers  # shape (mx*my, 2)


def _cell_centers(mx: int, my: int, W: float, H: float) -> List[Tuple[int, int, float, float]]:
    """Return list of interstitial candidate cell centers with their cell indices (i, j)."""
    out = []
    for j in range(my - 1):
        for i in range(mx - 1):
            x = (i + 1) * W / mx
            y = (j + 1) * H / my
            out.append((i, j, x, y))
    return out


def _clearance_proxy_for_cell(i: int, j: int, mx: int, my: int, W: float, H: float, r0: float) -> float:
    """Compute a local clearance proxy for the interstitial at cell (i, j).

    Proxy is the minimum of:
      - distance to any of the four neighboring base centers minus r0
      - distance to the boundary of the rectangle
    The distances to the four base centers are identical for cell centers in a
    regular grid, but we compute the formula explicitly for clarity.
    """
    # Interstitial center
    x = (i + 1) * W / mx
    y = (j + 1) * H / my
    # Neighbor base centers at the corners of the cell
    cx = [(i + 0.5) * W / mx, (i + 1.5) * W / mx]
    cy = [(j + 0.5) * H / my, (j + 1.5) * H / my]
    dmin_to_base = float("inf")
    for xb in cx:
        for yb in cy:
            d = np.hypot(x - xb, y - yb) - r0
            if d < dmin_to_base:
                dmin_to_base = d
    # Distance to boundary
    d_boundary = min(x, W - x, y, H - y)
    return min(dmin_to_base, d_boundary)


def _interstitial_candidates_ranked(mx: int, my: int, W: float, H: float, r0: float) -> List[Tuple[float, float, int, int, float, float]]:
    """Return ranked interstitial candidates:
    Each item is (score_desc, tie_center_dist2, j, i, x, y).
    We will sort by highest clearance score, then closest to center, then by (j, i).
    """
    cx, cy = W / 2.0, H / 2.0
    ranked: List[Tuple[float, float, int, int, float, float]] = []
    for j in range(my - 1):
        for i in range(mx - 1):
            x = (i + 1) * W / mx
            y = (j + 1) * H / my
            score = _clearance_proxy_for_cell(i, j, mx, my, W, H, r0)
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            ranked.append((score, -d2, j, i, x, y))
    # Sort descending by score (hence reverse), then ascending by distance (since we used -d2),
    # then by j, i for determinism.
    ranked.sort(key=lambda it: (-it[0], it[1], it[2], it[3]))
    return ranked


def _build_initial_circles(mx: int, my: int, t: int, eps0: float, multistart_k: int = 5) -> List[Dict]:
    """Build one or more seed layouts.

    Returns a list of seeds (dicts) with keys:
      - 'W', 'H', 'r0_exact', 'r1_exact', 'circles' (array Nx3), 'types' (list of 'base'/'inter'),
        'meta' (dictionary with indices and mapping details for adjacency),
        'interstitial_cells' (list of chosen cell (i, j))
    If t == 1, we produce up to multistart_k seeds by picking the top-k ranked candidates.
    Otherwise we produce a single seed with the top-t ranked interstitials.
    """
    seeds: List[Dict] = []

    r0_exact, W, H, r1_exact = _grid_seed_geometry(mx, my)
    r0 = max(0.0, r0_exact - eps0)
    r1 = max(0.0, r1_exact - eps0)

    base_centers = _base_centers(mx, my, W, H)
    ranked = _interstitial_candidates_ranked(mx, my, W, H, r0_exact)

    # Utility to build one seed given chosen cells
    def make_seed(chosen_cells: List[Tuple[int, int]]) -> Dict:
        circles: List[Tuple[float, float, float]] = []
        types: List[str] = []
        # Base circles first
        for (x, y) in base_centers:
            circles.append((x, y, r0))
            types.append("base")
        # Interstitials
        for (ci, cj) in chosen_cells:
            x = (ci + 1) * W / mx
            y = (cj + 1) * H / my
            circles.append((x, y, r1))
            types.append("inter")

        circles_arr = np.array(circles, dtype=float)

        # Build meta for adjacency
        # Map base (i,j) -> index
        base_index = {(i, j): (j * mx + i) for j in range(my) for i in range(mx)}
        # Interstitial map: for each chosen cell (ci, cj), record its circle index and its four base neighbors
        inter_map = {}
        for k, (ci, cj) in enumerate(chosen_cells):
            idx = len(base_centers) + k
            neighbors = [(ci, cj), (ci + 1, cj), (ci, cj + 1), (ci + 1, cj + 1)]
            inter_map[(ci, cj)] = {
                "index": idx,
                "base_neighbors": [base_index[p] for p in neighbors],
            }

        meta = {
            "mx": mx,
            "my": my,
            "base_index": base_index,
            "inter_map": inter_map,
            "B": mx * my,
            "t": len(chosen_cells),
        }
        return {
            "W": W,
            "H": H,
            "r0_exact": r0_exact,
            "r1_exact": r1_exact,
            "circles": circles_arr,
            "types": types,
            "meta": meta,
            "interstitial_cells": chosen_cells,
        }

    if t <= 0:
        seeds.append(make_seed([]))
        return seeds

    # If only one interstitial, build multiple seeds (multi-start) from top-k candidates
    if t == 1:
        k = min(multistart_k, len(ranked))
        for h in range(k):
            _, _, j, i, _, _ = ranked[h]
            seeds.append(make_seed([(i, j)]))
    else:
        # Choose top-t candidates deterministically
        chosen: List[Tuple[int, int]] = []
        for h in range(min(t, len(ranked))):
            _, _, j, i, _, _ = ranked[h]
            chosen.append((i, j))
        seeds.append(make_seed(chosen))

    return seeds


def _build_initial_adjacency(seed: Dict) -> Set[Tuple[int, int]]:
    """Build a sparse initial adjacency set for non-overlap constraints.

    Includes:
      - Grid neighbors (4-neighborhood and diagonals) among base circles.
      - Interstitial circle to its four surrounding base circles.
      - k-nearest neighbors (k=6) to capture nearby contacts.
    """
    circles = seed["circles"]
    meta = seed["meta"]
    mx = meta["mx"]
    my = meta["my"]
    B = meta["B"]
    t = meta["t"]

    N = circles.shape[0]
    centers = circles[:, :2]

    pairs: Set[Tuple[int, int]] = set()

    # Base grid neighbors (4-neighborhood + diagonals)
    def idx(i: int, j: int) -> int:
        return j * mx + i

    for j in range(my):
        for i in range(mx):
            a = idx(i, j)
            # Right/left
            if i + 1 < mx:
                b = idx(i + 1, j)
                pairs.add((min(a, b), max(a, b)))
            if j + 1 < my:
                b = idx(i, j + 1)
                pairs.add((min(a, b), max(a, b)))
            # Diagonals
            if i + 1 < mx and j + 1 < my:
                b = idx(i + 1, j + 1)
                pairs.add((min(a, b), max(a, b)))
            if i - 1 >= 0 and j + 1 < my:
                b = idx(i - 1, j + 1)
                pairs.add((min(a, b), max(a, b)))

    # Interstitial neighbors to their four surrounding base circles
    for (ci, cj), imap in meta["inter_map"].items():
        inter_idx = imap["index"]
        for bidx in imap["base_neighbors"]:
            pairs.add((min(inter_idx, bidx), max(inter_idx, bidx)))

    # k-nearest neighbors to capture nearby pairs
    k = 6
    for i in range(N):
        d2 = np.sum((centers - centers[i]) ** 2, axis=1)
        order = np.argsort(d2)
        cnt = 0
        for j in order:
            if j == i:
                continue
            pairs.add((min(i, j), max(i, j)))
            cnt += 1
            if cnt >= k:
                break

    return pairs


def _lp_inflate_fixed_centers(centers: np.ndarray, W: float, H: float, eps: float) -> Tuple[Optional[np.ndarray], bool]:
    """Fixed-center linear program to maximize sum of radii.

    Given fixed centers and rectangle (W, H), solve:
      maximize sum r_i
      subject to:
        0 <= r_i <= min(x_i, W - x_i, y_i, H - y_i) - eps
        r_i + r_j <= ||c_i - c_j|| - eps  for all i < j

    Implemented as minimize -sum r_i, using SciPy linprog (method='highs').
    Returns (radii, True) if successful, else (None, False).
    """
    try:
        from scipy.optimize import linprog
    except Exception:
        return None, False

    N = centers.shape[0]
    # Bounds per variable
    x = centers[:, 0]
    y = centers[:, 1]
    ub = np.minimum.reduce([x, W - x, y, H - y]) - eps
    ub = np.clip(ub, 0.0, None)
    bounds = [(0.0, float(ub[i])) for i in range(N)]

    # Pairwise constraints A_ub r <= b_ub
    # For each pair: (e_i + e_j) r <= d_ij - eps
    pairs_i = []
    pairs_j = []
    b = []
    for i in range(N):
        for j in range(i + 1, N):
            dx = centers[i, 0] - centers[j, 0]
            dy = centers[i, 1] - centers[j, 1]
            dij = float(np.hypot(dx, dy))
            rhs = dij - eps
            # If rhs is negative, the LP will be infeasible; still hand to solver
            pairs_i.append(i)
            pairs_j.append(j)
            b.append(rhs)

    # Build sparse-like A as dense since N is small (<= 21)
    m = len(b)
    if m > 0:
        A = np.zeros((m, N), dtype=float)
        for k, (i, j) in enumerate(zip(pairs_i, pairs_j)):
            A[k, i] = 1.0
            A[k, j] = 1.0
        A_ub = A
        b_ub = np.array(b, dtype=float)
    else:
        A_ub = None
        b_ub = None

    # Objective: minimize -sum r_i
    c = -np.ones(N, dtype=float)

    res = linprog(c=c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if not res.success or res.x is None:
        return None, False

    r = np.array(res.x, dtype=float)
    return r, True


def _centered_uniform_inflation(W: float, H: float, centers: np.ndarray, radii: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float, bool]:
    """Compute and apply a centered uniform inflation (about rectangle center).

    Scaling:
      Let (cx, cy) = (W/2, H/2). For t >= 1,
        x' = cx + t (x - cx), y' = cy + t (y - cy), r' = t r.

    Pairwise separations and radii both scale by t, so non-overlap is preserved.
    To maintain boundary containment we require, for each circle:
      x' - r' >= 0
      W - (x' + r') >= 0
      y' - r' >= 0
      H - (y' + r') >= 0

    This yields linear inequalities a + b t >= 0. We find the largest feasible t >= 1.

    Returns (centers', radii', t, applied_flag).
    """
    N = centers.shape[0]
    cx, cy = W / 2.0, H / 2.0
    x = centers[:, 0]
    y = centers[:, 1]
    r = radii

    # Collect bounds on t: lower (from b>0), upper (from b<0)
    t_lower = 1.0
    t_upper = float("inf")

    # For each circle and each boundary inequality
    for i in range(N):
        # Left: cx + t*(x - cx - r) >= 0
        a = cx
        b = (x[i] - cx - r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)
        else:
            # b == 0 -> requires a >= 0, which holds since cx >= 0
            pass

        # Right: W - cx - t*(x - cx + r) >= 0
        a = W - cx
        b = -(x[i] - cx + r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)

        # Bottom: cy + t*(y - cy - r) >= 0
        a = cy
        b = (y[i] - cy - r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)

        # Top: H - cy - t*(y - cy + r) >= 0
        a = H - cy
        b = -(y[i] - cy + r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)

    # Numerical guard: lower can't exceed upper; also require t_upper finite
    if not np.isfinite(t_upper):
        t_upper = float("inf")
    # We seek t_max >= 1
    t_max = min(t_upper, max(t_lower, 1.0))

    # Apply only if strictly > 1 by a tiny threshold
    if not (t_max > 1.0 + 1e-12):
        return centers.copy(), radii.copy(), 1.0, False

    t = t_max
    # Apply scaling
    centers_new = np.empty_like(centers)
    centers_new[:, 0] = cx + t * (x - cx)
    centers_new[:, 1] = cy + t * (y - cy)
    radii_new = t * r

    return centers_new, radii_new, t, True


def _solve_polish(seed: Dict, eps_schedule: List[float], max_lazy_iters: int = 3) -> Optional[Dict]:
    """Polish a seed using constrained nonlinear optimization (SLSQP if available).

    Returns a dict with keys:
      - 'W', 'H', 'circles' (Nx3) for the best polished result,
      or None if polishing fails entirely.
    """
    try:
        from scipy.optimize import minimize
    except Exception:
        return None  # SciPy not available

    circles0 = seed["circles"].copy()
    W0 = seed["W"]
    H0 = seed["H"]
    N = circles0.shape[0]

    # Variable layout: v = [W, H, x[0..N-1], y[0..N-1], r[0..N-1]]
    def pack_vars(W, H, centers, radii):
        return np.concatenate(([W, H], centers[:, 0], centers[:, 1], radii))

    def unpack_vars(v):
        W, H = v[0], v[1]
        x = v[2 : 2 + N]
        y = v[2 + N : 2 + 2 * N]
        r = v[2 + 2 * N : 2 + 3 * N]
        centers = np.stack([x, y], axis=1)
        return W, H, centers, r

    # Initial variables
    v0 = pack_vars(W0, H0, circles0[:, :2], circles0[:, 2])

    # Anchor circle: bottom-left among base circles (min y then min x)
    # We enforce touching left and bottom: x_i - r_i = 0 and y_i - r_i = 0.
    # Determine anchor index: among the first B base circles.
    meta = seed["meta"]
    B = meta["B"]
    base_centers = circles0[:B, :2]
    base_order = np.lexsort((base_centers[:, 0], base_centers[:, 1]))  # sort by y, then x
    anchor_idx = int(base_order[0])

    # Build an initial adjacency
    adjacency = _build_initial_adjacency(seed)

    best_result = None
    best_sum_r = -np.inf

    # To support the final centered uniform inflation, keep track of the last accepted (W,H,centers,r)
    last_WHR = None  # tuple (W, H, centers, r)

    # Epsilon annealing loop
    for eps in eps_schedule:
        lazy_iters = 0
        adjacency_current = set(adjacency)

        while lazy_iters <= max_lazy_iters:
            # Build constraints for given eps and adjacency
            cons = []

            # Equality: perimeter W + H = 2
            def ceq_perimeter(v):
                W, H, _, _ = unpack_vars(v)
                return 2.0 - (W + H)

            cons.append({"type": "eq", "fun": ceq_perimeter})

            # Inequalities: W >= 0, H >= 0
            cons.append({"type": "ineq", "fun": lambda v: v[0]})
            cons.append({"type": "ineq", "fun": lambda v: v[1]})

            # Anchor equalities for the bottom-left base circle: x - r = 0 and y - r = 0
            def ceq_anchor_x(v, idx=anchor_idx):
                W, H, centers, r = unpack_vars(v)
                return centers[idx, 0] - r[idx]

            def ceq_anchor_y(v, idx=anchor_idx):
                W, H, centers, r = unpack_vars(v)
                return centers[idx, 1] - r[idx]

            cons.append({"type": "eq", "fun": ceq_anchor_x})
            cons.append({"type": "eq", "fun": ceq_anchor_y})

            # Boundary inequalities for each circle:
            # x_i - r_i >= 0; W - r_i - x_i >= 0; y_i - r_i >= 0; H - r_i - y_i >= 0
            for i in range(N):
                cons.append({"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[2][i, 0] - unpack_vars(v)[3][i])})
                cons.append(
                    {"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[0] - unpack_vars(v)[3][i] - unpack_vars(v)[2][i, 0])}
                )
                cons.append({"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[2][i, 1] - unpack_vars(v)[3][i])})
                cons.append(
                    {"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[1] - unpack_vars(v)[3][i] - unpack_vars(v)[2][i, 1])}
                )
                # r_i >= 0
                cons.append({"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[3][i])})

            # Non-overlap constraints for pairs in adjacency:
            # Use squared separation: ||c_i - c_j||^2 - (r_i + r_j + eps)^2 >= 0
            for (i, j) in sorted(adjacency_current):
                def g_pair(v, i=i, j=j, eps=eps):
                    _, _, centers, r = unpack_vars(v)
                    dx = centers[i, 0] - centers[j, 0]
                    dy = centers[i, 1] - centers[j, 1]
                    d2 = dx * dx + dy * dy
                    rs = r[i] + r[j] + eps
                    return d2 - rs * rs

                cons.append({"type": "ineq", "fun": g_pair})

            # Objective: minimize negative sum of radii
            def f(v):
                _, _, _, r = unpack_vars(v)
                return -np.sum(r)

            # (Optional) Gradient of objective
            def fprime(v):
                grad = np.zeros_like(v)
                grad[2 + 2 * N :] = -1.0
                return grad

            # Run SLSQP
            res = minimize(
                f,
                v0,
                jac=fprime,
                constraints=cons,
                method="SLSQP",
                options={"maxiter": 400, "ftol": 1e-12, "disp": False},
            )

            # If failure, try to proceed with current x if it exists; otherwise break
            if not res.success:
                v_res = res.x if isinstance(res.x, np.ndarray) else v0
            else:
                v_res = res.x

            # Update starting point for next rounds
            v0 = v_res.copy()

            # Validate and augment constraints lazily: check all pairs
            W, H, centers, r = unpack_vars(v_res)
            violated_pairs: List[Tuple[int, int, float]] = []
            for i in range(N):
                for j in range(i + 1, N):
                    dx = centers[i, 0] - centers[j, 0]
                    dy = centers[i, 1] - centers[j, 1]
                    d2 = dx * dx + dy * dy
                    rs = r[i] + r[j] + eps
                    if d2 < rs * rs - 1e-16:
                        violated_pairs.append((i, j, d2 - rs * rs))

            if violated_pairs:
                # Add these pairs to adjacency and iterate
                for (i, j, _) in violated_pairs:
                    adjacency_current.add((i, j))
                lazy_iters += 1
                continue  # re-solve with augmented adjacency
            else:
                # No violations: accept this eps stage result (with possible LP reallocation)
                sum_r = float(np.sum(r))

                # Post-annealing-stage LP inflation with acceptance gate
                r_lp, ok_lp = _lp_inflate_fixed_centers(centers, W, H, eps=eps)
                if ok_lp:
                    sum_r_lp = float(np.sum(r_lp))
                    if sum_r_lp > sum_r + 1e-15:
                        # Accept LP radii reallocation
                        r = r_lp
                        sum_r = sum_r_lp
                        # Update v0 radii for the next stage start
                        v0 = pack_vars(W, H, centers, r)

                # Update best trackers and last stage state
                if sum_r > best_sum_r:
                    best_sum_r = sum_r
                    best_result = {"W": W, "H": H, "circles": np.concatenate([centers, r[:, None]], axis=1)}
                last_WHR = (W, H, centers.copy(), r.copy())

                break  # move to next epsilon

        # proceed to next epsilon stage (or exit if max lazy iters exceeded)

    # After the final epsilon stage converges, attempt a centered uniform inflation + LP,
    # accepting only if it improves the sum of radii.
    if last_WHR is not None:
        W, H, centers, r = last_WHR
        centers_scaled, r_scaled, t, applied = _centered_uniform_inflation(W, H, centers, r)
        if applied:
            # Run LP after scaling; use the tightest eps (final stage), i.e., last element of schedule
            eps_final = eps_schedule[-1] if len(eps_schedule) > 0 else 1e-12
            r_lp, ok_lp = _lp_inflate_fixed_centers(centers_scaled, W, H, eps=eps_final)
            if ok_lp:
                sum_old = float(np.sum(r))
                sum_new = float(np.sum(r_lp))
                if sum_new > sum_old + 1e-15:
                    # Accept improved configuration
                    best_sum_r = sum_new
                    best_result = {
                        "W": W,
                        "H": H,
                        "circles": np.concatenate([centers_scaled, r_lp[:, None]], axis=1),
                    }

    return best_result


def construct_packing(num_circles: int = 21) -> np.ndarray:
    """Construct a packing of `num_circles` disjoint circles within a rectangle
    of perimeter exactly 4 using a two-scale rectangular grid plus interstitials,
    followed by optional nonlinear polishing.

    Algorithm:
      - Enumerate feasible grids (m_x, m_y) and select the best by S.
      - Base geometry r0_exact = 1 / (m_x + m_y), W = 2 m_x r0_exact, H = 2 m_y r0_exact.
      - Place m_x × m_y base circles with radius r0_exact − eps0 and t interstitials with
        radius r1_exact − eps0, chosen by a clearance-aware score. Build a few multi-start
        seeds (for t = 1).
      - LP preselection: for each seed, run a fixed-center LP to maximize sum of radii;
        keep the top-K (K=2) seeds for SLSQP polishing, initializing their radii with
        the LP solution. If SciPy is unavailable, skip this step and keep all seeds.
      - Polish each kept seed with SLSQP under sparse constraints with epsilon annealing
        and post-stage LP reallocation; keep the best polished layout. If SciPy is not
        available, return the best seed by sum of radii.
      - Final centered uniform inflation + LP acceptance gate, then finalize with a
        uniform shrink δ = 1e−12 to radii.

    Returns:
      numpy array (num_circles, 3): rows of [x, y, radius].
    """
    if num_circles <= 0:
        return np.zeros((0, 3), dtype=float)

    # 1) Discrete perimeter-balanced search for the grid
    m_x, m_y, t = _select_grid_for_N(num_circles)
    B = m_x * m_y
    assert 0 <= t <= max(0, (m_x - 1) * (m_y - 1))
    assert B + t == num_circles

    # 2) Two-scale seeds with W + H = 2; epsilon annealing seed epsilon
    eps0 = 1e-6
    seeds = _build_initial_circles(m_x, m_y, t, eps0=eps0, multistart_k=6)

    # 2a) LP preselection of seeds (deterministic), keep top K=2 by LP sum of radii,
    # and initialize their radii from the LP solution. If SciPy unavailable/fails, skip.
    K = 2
    try:
        # Probe SciPy availability for linprog
        from scipy.optimize import linprog  # noqa: F401

        # Compute LP radii and sums for all seeds
        seed_scores: List[Tuple[float, int]] = []
        lp_radii_all: Dict[int, np.ndarray] = {}
        for si, seed in enumerate(seeds):
            centers = seed["circles"][:, :2]
            W = seed["W"]
            H = seed["H"]
            r_lp, ok = _lp_inflate_fixed_centers(centers, W, H, eps=eps0)
            if ok and r_lp is not None:
                lp_radii_all[si] = r_lp
                seed_scores.append((float(np.sum(r_lp)), si))
            else:
                # Fall back to current radii for scoring if LP failed on this seed
                seed_scores.append((float(np.sum(seed["circles"][:, 2])), si))

        # Rank and keep top K seeds
        seed_scores.sort(key=lambda t: -t[0])
        keep_indices = [si for (_, si) in seed_scores[: min(K, len(seeds))]]
        new_seeds: List[Dict] = []
        for si in keep_indices:
            seed = seeds[si]
            # Initialize radii with LP radii if present
            if si in lp_radii_all:
                seed = dict(seed)  # shallow copy
                circles = seed["circles"].copy()
                circles[:, 2] = lp_radii_all[si]
                seed["circles"] = circles
            new_seeds.append(seed)
        seeds = new_seeds
    except Exception:
        # SciPy not available: skip preselection, keep current behavior
        pass

    # Try polishing with epsilon annealing. If SciPy is unavailable or optimization fails,
    # we will fall back to the best seed by simple sum of radii.
    eps_schedule = [1e-6, 1e-8, 1e-12]
    best_layout = None
    best_sum_r = -np.inf

    for seed in seeds:
        polished = _solve_polish(seed, eps_schedule=eps_schedule, max_lazy_iters=3)
        if polished is not None:
            circles = polished["circles"]
            total_r = float(np.sum(circles[:, 2]))
            if total_r > best_sum_r:
                best_sum_r = total_r
                best_layout = circles
        else:
            # No SciPy or failure: use the seed directly
            circles = seed["circles"]
            total_r = float(np.sum(circles[:, 2]))
            if total_r > best_sum_r:
                best_sum_r = total_r
                best_layout = circles

    # 3) Final uniform shrink to guarantee strict disjointness
    delta = 1e-12
    circles_out = best_layout.copy()
    circles_out[:, 2] = np.clip(circles_out[:, 2] - delta, 0.0, None)

    return circles_out
# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```
