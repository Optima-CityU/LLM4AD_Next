Why quadratic near-active weights with wider capture and a small boundary boost improved this deterministic LP+relocation circle-packing constructor.

- Quadratic Smooth-Active with Wider Capture and Boundary Boost: In deterministic LP-plus-relocation circle packers, widening the near-active windows (pairwise thr_pairs = 5.0*tol and boundary thr_boundaries = 6.0*tol) while switching to quadratic decay weights w = max(0, 1 - gap/thr)^2 and applying a small boundary normal boost (boundary_boost = 1.15) concentrates movement on truly tight contacts and nudges circles inward, yielding a higher LP objective (sum_radii = 2.5318253466697045 with validity = 1.0) than the parent’s 2.509238509824397; reuse this pattern together with a modestly larger initial step s0 = 0.12*min(dx, dy) under deterministic backtracking and lexicographic acceptance to safely accelerate progress without breaking feasibility.

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

This implementation follows the described algorithm:
1) Seed centers in a near-hexagonal pattern (6-column row centered vertically).
2) For fixed centers, solve a linear program (via deterministic primal simplex with Bland's rule)
   to maximize the sum of radii under boundary and pairwise non-overlap constraints.
3) Build a scale-aware, smoothly-weighted near-active set to generate stable relocation directions:
   - Pairwise near-contacts within a widened window (5*active_tol) receive a quadratically decaying weight.
   - Boundary near-contacts are detected side-specifically with a slightly wider window (6*active_tol)
     and the same quadratic weight profile, then inward unit normals are aggregated with a small boost.
   - Aggregated per-circle vectors are normalized to unit length and globally rescaled.
4) Deterministic backtracking step schedule. Accept steps via a lexicographic rule:
   - Strict improvement in sum of radii, or
   - Tie within 1e-12 in the objective but decreased contact energy.
5) Alternate LP and relocation until convergence or a fixed iteration cap, preserving determinism.
"""

import json
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    Deterministic algorithm:
    - Hexagonal-like seed with a 6-column row centered vertically for symmetry.
    - Alternate between LP-optimal radii and deterministic relocation guided by a
      scale-aware, smoothly-weighted near-active constraint set.
    - Per-circle normalization and global rescaling of motion vectors for stability.
    - Deterministic geometric backtracking with a lexicographic accept criterion.

    Mutation details integrated:
    - Smooth-active weights use quadratic decay with widened capture windows
      (pairs: 5*tol, boundaries: 6*tol).
    - Boundary contributions receive a slight inward boost factor (1.15).
    - Base step scale increased modestly (s0 = 0.12*min(dx, dy)).
    """
    assert num_circles == 26, "This constructor is specialized for 26 circles."

    # Margin so centers never land exactly on boundaries before LP
    eps = 1e-3

    # 1) Hexagonal-like deterministic seeding (centered 6-column row)
    centers = _hex_seed_26(eps=eps)

    # Alternating optimization parameters
    max_iters = 40
    rho = 0.95
    # Step schedule scaled by the exact horizontal/vertical pitch
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = np.sqrt(3.0) / 2.0 * dx
    # Slightly larger initial step per mutation
    s0 = 0.12 * min(dx, dy)
    # Scale-aware tolerance for identifying near-active constraints
    active_tol = 1e-6 * min(dx, dy)

    # Optimization loop
    radii, _ = _solve_lp_radii(centers)
    obj_sum = float(np.sum(radii))

    for t in range(max_iters):
        # 1) Solve LP for current centers (ensure we start each outer iter at LP optimum)
        radii, _ = _solve_lp_radii(centers)
        obj_sum = float(np.sum(radii))

        # 2) Extract smoothly-weighted near-active set (pairs and side-specific boundaries)
        active_pairs_w, active_boundaries_w = _smooth_active_set(centers, radii, tol=active_tol)

        # 3) Build movement vectors with weights, then per-circle normalization + global rescale
        v = _build_weighted_movement_vectors(centers, active_pairs_w, active_boundaries_w)

        # If no motion, we are locally stable; stop
        if not np.any(v):
            break

        # 4) Deterministic step size with short backtracking and lexicographic acceptance
        base_step = s0 * (rho**t)
        step = base_step
        accepted = False

        # Precompute current contact energy for tie-breaker
        E_curr = _contact_energy(centers, radii)

        for _ in range(10):
            trial_centers = centers + step * v
            # Project to [eps, 1 - eps]^2 deterministically
            trial_centers = np.clip(trial_centers, eps, 1.0 - eps)
            trial_radii, _ = _solve_lp_radii(trial_centers)
            trial_obj = float(np.sum(trial_radii))
            # Lexicographic acceptance:
            # 1) Strict primary improvement
            # 2) If primary is flat within ±1e-12, accept if contact energy decreases
            if trial_obj > obj_sum + 1e-12:
                centers = trial_centers
                radii = trial_radii
                obj_sum = trial_obj
                accepted = True
                break
            elif abs(trial_obj - obj_sum) <= 1e-12:
                E_trial = _contact_energy(trial_centers, trial_radii)
                if E_trial < E_curr - 1e-18:
                    centers = trial_centers
                    radii = trial_radii
                    obj_sum = trial_obj
                    accepted = True
                    break
            # Otherwise, backtrack
            step *= 0.5

        if not accepted:
            # Could not find an acceptable step; terminate to preserve determinism
            break

    # Final LP to be safe
    final_radii, _ = _solve_lp_radii(centers)
    return centers, final_radii


def _hex_seed_26(eps: float = 1e-3) -> np.ndarray:
    """Hexagonal-like deterministic seeding for 26 points inside unit square.

    Pattern:
    - 5 rows with counts [5, 5, 6, 5, 5] (6-column row centered vertically)
    - horizontal pitch dx = (1 - 2*eps)/5 (exactly fits the 6-column row within [eps, 1-eps])
    - vertical pitch dy = sqrt(3)/2 * dx
    - y_r = y0 + r*dy with y0 = (1 - 4*dy)/2 so the 6-column row sits at y = 0.5
    - 6-col row: x = eps + c*dx, c=0..5; 5-col rows offset by dx/2
    - Clip coordinates to [eps, 1 - eps]
    """
    counts = [5, 5, 6, 5, 5]
    n = sum(counts)
    assert n == 26
    # Compute pitches
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = np.sqrt(3.0) / 2.0 * dx
    # Place rows centered vertically so the middle (6-column) row is at y=0.5
    y0 = (1.0 - 4.0 * dy) / 2.0
    x0 = eps
    centers: List[Tuple[float, float]] = []

    for r, cnt in enumerate(counts):
        y = y0 + r * dy
        if cnt == 6:
            # integer grid columns anchored at x0
            xs = [x0 + c * dx for c in range(cnt)]
        else:
            # hex offset by dx/2 for 5-column rows
            xs = [x0 + dx / 2.0 + c * dx for c in range(cnt)]
        for x in xs:
            centers.append((min(max(x, eps), 1.0 - eps), min(max(y, eps), 1.0 - eps)))

    return np.array(centers, dtype=float)


def _solve_lp_radii(centers: np.ndarray) -> Tuple[np.ndarray, float]:
    """Solve LP to maximize sum of radii for fixed centers.

    Variables: r_i for i = 0..n-1
    Objective: maximize sum_i r_i
    Constraints:
      - r_i >= 0
      - r_i <= b_i = min(x_i, y_i, 1-x_i, 1-y_i)
      - r_i + r_j <= d_ij for all i<j, where d_ij = ||c_i - c_j||_2

    Implemented via primal simplex with slack variables and Bland's rule (deterministic).
    Returns (r, objective_value).
    """
    n = centers.shape[0]
    # Boundary clearances
    bnd = np.minimum.reduce([centers[:, 0], centers[:, 1], 1.0 - centers[:, 0], 1.0 - centers[:, 1]])
    # Pairwise distances
    dists = _pairwise_distances(centers)

    # Build constraints A x <= b
    # First boundaries
    A_rows: List[np.ndarray] = []
    b_vec: List[float] = []
    for i in range(n):
        row = np.zeros(n, dtype=float)
        row[i] = 1.0
        A_rows.append(row)
        b_vec.append(float(max(bnd[i], 0.0)))
    # Then pairwise constraints
    for i in range(n):
        for j in range(i + 1, n):
            row = np.zeros(n, dtype=float)
            row[i] = 1.0
            row[j] = 1.0
            A_rows.append(row)
            b_vec.append(float(max(dists[i, j], 0.0)))

    A = np.vstack(A_rows)
    b = np.array(b_vec, dtype=float)
    m = A.shape[0]

    # Objective: maximize sum r_i
    c = np.ones(n, dtype=float)

    # Build initial simplex tableau with slack variables
    # Variables: x = [r (n), s (m)]
    # Constraints: A r + I s = b, r >= 0, s >= 0
    # Objective row: -c for r, 0 for s, RHS 0
    T = np.zeros((m + 1, n + m + 1), dtype=float)
    # Fill constraints
    T[:m, :n] = A
    T[:m, n:n + m] = np.eye(m, dtype=float)
    T[:m, -1] = b
    # Objective row (last)
    T[m, :n] = -c
    T[m, n:n + m] = 0.0
    T[m, -1] = 0.0

    # Basis: initial basic variables are slacks s_i at indices n..n+m-1
    basis = list(range(n, n + m))
    tol = 1e-12
    max_pivots = 20000  # generous for determinism on small LP

    pivots = 0
    while True:
        # Choose entering variable by Bland's rule among r variables
        entering = None
        for j in range(n):
            if T[m, j] < -tol:
                entering = j
                break
        if entering is None:
            break  # optimal

        # Ratio test for leaving variable
        pivot_row = None
        min_ratio = np.inf
        col = entering
        for i in range(m):
            a_ij = T[i, col]
            if a_ij > tol:
                ratio = T[i, -1] / a_ij
                # Bland's tie-break: smallest row index for equal ratios
                if ratio < min_ratio - 1e-18 or (abs(ratio - min_ratio) <= 1e-18 and (pivot_row is None or i < pivot_row)):
                    min_ratio = ratio
                    pivot_row = i

        if pivot_row is None:
            # Unbounded (should not happen for this LP)
            break

        # Pivot operation
        _pivot(T, pivot_row, col)
        basis[pivot_row] = col

        pivots += 1
        if pivots > max_pivots:
            # Safeguard: break to avoid infinite loops due to degeneracy
            break

    # Extract solution
    x = np.zeros(n + m, dtype=float)
    for i in range(m):
        bi = basis[i]
        if bi < n + m:
            x[bi] = T[i, -1]
    r = x[:n].copy()
    r[r < 0] = 0.0  # clean tiny negatives
    obj_val = float(np.sum(r))
    return r, obj_val


def _pivot(T: np.ndarray, row: int, col: int) -> None:
    """Perform a pivot on tableau T at (row, col)."""
    pivot_val = T[row, col]
    if pivot_val == 0.0:
        return
    # Normalize pivot row
    T[row, :] = T[row, :] / pivot_val
    m, n = T.shape
    # Eliminate other rows
    for i in range(m):
        if i == row:
            continue
        factor = T[i, col]
        if factor != 0.0:
            T[i, :] -= factor * T[row, :]


def _pairwise_distances(centers: np.ndarray) -> np.ndarray:
    """Compute pairwise Euclidean distances between centers."""
    diff = centers[:, None, :] - centers[None, :, :]
    d = np.sqrt(np.sum(diff * diff, axis=2))
    return d


def _smooth_active_set(
    centers: np.ndarray,
    radii: np.ndarray,
    tol: float,
) -> Tuple[List[Tuple[int, int, float]], List[Tuple[int, str, float]]]:
    """Build a smoothly-weighted near-active set using a scale-aware tolerance.

    Changes per mutation:
    - Quadratic decay in weights with wider capture windows.
    - Separate thresholds for pairs and boundaries.

    Pairs:
      include (i, j) if d_ij <= r_i + r_j + thr_pairs where thr_pairs = 5*tol.
      weight w_ij = max(0, 1 - (gap / thr_pairs))^2, with gap = d_ij - (r_i + r_j).

    Boundaries:
      for each circle i and each side s in left, bottom, right, top, let clearance_s be
      the distance to that side. If clearance_s - r_i <= thr_boundaries (6*tol) then include (i, s) with
      weight w_is = max(0, 1 - (gap / thr_boundaries))^2, with gap = clearance_s - r_i.

    Returns:
      - active_pairs_w: list of (i, j, weight)
      - active_boundaries_w: list of (i, side, weight)
    """
    n = centers.shape[0]
    dists = _pairwise_distances(centers)
    active_pairs_w: List[Tuple[int, int, float]] = []
    thr_pairs = 5.0 * tol  # widened capture for pairs

    # Pairs with quadratic weights
    for i in range(n):
        for j in range(i + 1, n):
            gap = dists[i, j] - (radii[i] + radii[j])
            if gap <= thr_pairs:
                # Quadratic decay emphasizing tighter constraints
                base = 1.0 - max(gap, 0.0) / thr_pairs
                if base > 0.0:
                    w = base * base
                    active_pairs_w.append((i, j, w))

    # Boundaries: side-specific, deterministic side order with widened threshold
    xs = centers[:, 0]
    ys = centers[:, 1]
    clearances = {
        "left": xs,
        "bottom": ys,
        "right": 1.0 - xs,
        "top": 1.0 - ys,
    }
    sides_order = ("left", "bottom", "right", "top")
    active_boundaries_w: List[Tuple[int, str, float]] = []
    thr_boundaries = 6.0 * tol  # slightly wider for boundaries
    for i in range(n):
        for side in sides_order:
            clearance = float(clearances[side][i])
            gap = clearance - radii[i]
            if gap <= thr_boundaries:
                base = 1.0 - max(gap, 0.0) / thr_boundaries
                if base > 0.0:
                    w = base * base
                    active_boundaries_w.append((i, side, w))

    return active_pairs_w, active_boundaries_w


def _build_weighted_movement_vectors(
    centers: np.ndarray,
    active_pairs_w: List[Tuple[int, int, float]],
    active_boundaries_w: List[Tuple[int, str, float]],
) -> np.ndarray:
    """Build deterministic movement vectors from the smoothly-weighted near-active set.

    - For near-active pair (i, j, w): add w * u_ij to i and -w * u_ij to j,
      where u_ij is the unit vector from j to i (c_i - c_j)/||...||.
      If centers coincide, use a fixed axis direction for tie-breaking.
    - For boundary near-active (i, side, w): add boundary_boost * w times the inward unit normal
      for that side (boundary_boost = 1.15).

    After aggregation, normalize each nonzero per-circle vector to unit length and
    rescale globally so the maximum norm is 1. This prevents overly connected nodes
    from dominating the step while keeping a consistent scale.

    Returns:
      v: array of shape (n, 2) with the movement vectors.
    """
    n = centers.shape[0]
    v = np.zeros_like(centers)
    boundary_boost = 1.15  # slight inward bias from walls

    # Near-active pairs
    for (i, j, w) in active_pairs_w:
        diff = centers[i] - centers[j]
        dist = float(np.linalg.norm(diff))
        if dist > 0.0:
            u = diff / dist
        else:
            u = np.array([1.0, 0.0])  # deterministic tie-break if coincident
        v[i] += w * u
        v[j] -= w * u

    # Near-active boundaries: inward unit normals (away from edges), boosted
    for (i, side, w) in active_boundaries_w:
        if side == "left":
            v[i] += boundary_boost * w * np.array([+1.0, 0.0])
        elif side == "right":
            v[i] += boundary_boost * w * np.array([-1.0, 0.0])
        elif side == "bottom":
            v[i] += boundary_boost * w * np.array([0.0, +1.0])
        elif side == "top":
            v[i] += boundary_boost * w * np.array([0.0, -1.0])

    # Per-circle normalization
    norms = np.linalg.norm(v, axis=1)
    for i in range(n):
        if norms[i] > 0.0:
            v[i] /= norms[i]

    # Global rescaling so max norm is 1 (kept for determinism, even if redundant after per-circle normalization)
    max_norm = float(np.max(np.linalg.norm(v, axis=1)))
    if max_norm > 0.0:
        v /= max_norm

    return v


def _contact_energy(centers: np.ndarray, radii: np.ndarray) -> float:
    """Compute contact energy:
       E = sum_{i<j} max(0, r_i + r_j - d_ij)^2 + sum_i max(0, r_i - b_i)^2
    where b_i is the clearance to the nearest side.
    """
    dists = _pairwise_distances(centers)
    n = centers.shape[0]
    # Pairwise term
    E_pairs = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            overlap = (radii[i] + radii[j]) - dists[i, j]
            if overlap > 0.0:
                E_pairs += overlap * overlap
    # Boundary term
    xs = centers[:, 0]
    ys = centers[:, 1]
    b_i = np.minimum.reduce([xs, ys, 1.0 - xs, 1.0 - ys])
    boundary_violation = radii - b_i
    boundary_violation[boundary_violation < 0.0] = 0.0
    E_bnd = float(np.sum(boundary_violation * boundary_violation))
    return E_pairs + E_bnd


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
