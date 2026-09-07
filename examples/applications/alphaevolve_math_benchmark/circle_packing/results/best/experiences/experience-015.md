Insight about mechanisms that improved deterministic 26-circle packing performance.

- Boundary-biased annealing with corner-aware boosts: Under deterministic schedules β_t = 1.2 − 0.2·min(1, t/20) and α_t = 0.9 + 0.1·min(1, t/20), combined with endpoint-aware pair attenuation, this mechanism prioritizes early boundary retreat and dampens pair pushes on boundary-limited circles to accelerate edge decongestion.
- Extended lexicographic acceptance: The accept rule adds a tertiary maximum-violation tie-break (decrease > 1e−16) when Σ r_i improvement (> 1e−12) and energy decrease (> 1e−18) are flat, enabling deterministic progress on objective plateaus without randomness.
- Adaptive (annealed) scale-aware near-active set: The tolerance tol_t = max(1e−8·min(dx,dy), tol0·ρ_tol^t) with tol0 = 1e−6·min(dx,dy) and ρ_tol = 0.92 keeps contacts generous early and sharper later, reducing oscillations and guiding stable, precise relocations.
- Annealed Smooth-Active Hex with Corner-Aware Forces: On the 26-circle unit-square packing task, the algorithm achieved sum_radii = 2.5295826819208918 with validity = 1.0, outperforming Parent 1 (2.504830892336784) and Parent 2 (2.509238509824397), supporting reuse of annealed tolerance with early boundary bias, corner-aware boosts, and a tertiary tie-break in future deterministic packers.

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

Implements the described algorithm:

Seed:
- Centered hex-like seed with 5 rows (counts [5, 5, 6, 5, 5]); horizontal pitch
  dx = (1 − 2*eps)/5; vertical pitch dy = sqrt(3)/2 * dx; the 6-column row is
  centered vertically (y = 0.5). 5-column rows are offset by dx/2.

LP (fixed centers):
- Maximize sum of radii subject to boundary and pairwise non-overlap constraints:
  r_i >= 0; r_i <= clearance to closest side; r_i + r_j <= distance(c_i, c_j).
- Deterministic primal simplex with slack basis and Bland’s rule.

Relocation (smooth-active with annealing):
- Adaptive, scale-aware tolerance tol_t = max(floor, tol0 * rho_tol^t) with tol0 = 1e-6 * min(dx, dy),
  rho_tol = 0.92 and floor = 1e-8 * min(dx, dy).
- Build a near-active set:
  * Pairs (i, j): include if d_ij <= r_i + r_j + 3*tol_t with weight w_ij decaying linearly
    in the band; w_ij = max(0, 1 - (d_ij - (r_i + r_j)) / (3*tol_t)).
  * Boundaries: for each side s in deterministic order ("left", "bottom", "right", "top"),
    include if clearance_s - r_i <= 3*tol_t with weight w_is = max(0, 1 - (clearance_s - r_i)/(3*tol_t)).

Boundary-biased annealing and corner-aware boosts:
- Weight scalings per iteration t:
  * β_t = 1.2 − 0.2 * min(1, t/20) applied to boundary weights (bias toward interior early).
  * α_t = 0.9 + 0.1 * min(1, t/20) applied to pair weights at endpoints that have any boundary
    contact in the same iteration (attenuation early).
- Corner-aware boost: if a circle has near-active contacts with at least two distinct sides,
  multiply all its boundary weights by γ_corner = 1.15 to encourage motion away from corners.

Deterministic force aggregation with normalization:
- For each pair (i, j): add endpoint-adjusted weight times the unit vector along the line,
  with anti-symmetric contributions; for boundaries: add inward unit normals scaled by weights.
- Normalize per-circle vectors to unit length (if nonzero), then globally rescale to max-norm 1.

Backtracking and lexicographic acceptance:
- Proposed step s_t = s0 * rho^t with s0 = 0.1 * min(dx, dy) and rho = 0.95; try s_t / 2^k for k=0..9.
- For each trial: project centers to [eps, 1 - eps]^2, resolve LP, and compute:
  1) Primary: objective sum of radii Σ r_i.
  2) Secondary: contact energy E = Σ_{i<j} max(0, r_i + r_j − d_ij)^2 + Σ_i max(0, r_i − b_i)^2.
  3) Tertiary: max violation D = max(0, max_{i<j}(r_i + r_j − d_ij), max_i(r_i − b_i)).
- Acceptance is lexicographic and deterministic:
  1) If Σ r_i increases by > 1e−12, accept.
  2) Else if |ΔΣ r_i| ≤ 1e−12 and E decreases by > 1e−18, accept.
  3) Else if both flat and D decreases by > 1e−16, accept.
- Stop if all movement vectors are zero or if no backtracking step can be accepted; cap at 40 iterations.
- Final LP solve for robustness.

All tie-breaks and schedules are deterministic to ensure repeatability.
"""

import json
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    Deterministic algorithm with:
    - Centered hex-like seeding.
    - LP for radii with deterministic primal simplex (Bland’s rule).
    - Annealed smooth-active relocation with boundary-biased weights and corner boosts.
    - Deterministic backtracking with extended lexicographic acceptance (objective, energy, max violation).
    """
    assert num_circles == 26, "This constructor is specialized for 26 circles."

    # Keep a small epsilon margin so centers never land exactly on boundaries
    eps = 1e-3

    # 1) Hexagonal-like deterministic seeding (centered 6-column row)
    centers = _hex_seed_26(eps=eps)

    # Pitches for scaling and schedules
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = np.sqrt(3.0) / 2.0 * dx
    pitch = min(dx, dy)

    # Step schedule parameters (geometric decay)
    max_iters = 40
    rho = 0.95
    s0 = 0.1 * pitch

    # Adaptive (annealed) tolerance for near-active detection
    tol0 = 1e-6 * pitch
    rho_tol = 0.92
    tol_floor = 1e-8 * pitch

    # Solve initial LP
    radii, _ = _solve_lp_radii(centers)
    obj_sum = float(np.sum(radii))

    for t in range(max_iters):
        # Always re-solve LP at the start of each iteration to anchor to the current optimum
        radii, _ = _solve_lp_radii(centers)
        obj_sum = float(np.sum(radii))

        # Annealed tolerance for this iteration
        tol_t = max(tol_floor, tol0 * (rho_tol**t))

        # Smoothly-weighted near-active set (pairs and side-specific boundaries)
        active_pairs_w, active_boundaries_w = _smooth_active_set(centers, radii, tol=tol_t)

        # Boundary-biased annealing schedules and parameters
        progress = min(1.0, t / 20.0)
        beta_t = 1.2 - 0.2 * progress  # scales boundary weights (>1 early, taper to 1)
        alpha_t = 0.9 + 0.1 * progress  # endpoint attenuation for pairs with boundary involvement (rises to 1)
        gamma_corner = 1.15  # corner-aware boost for boundary contacts on 2+ sides

        # Build movement vectors with annealed weights, endpoint attenuation, and corner boosts
        v = _build_weighted_movement_vectors(
            centers=centers,
            active_pairs_w=active_pairs_w,
            active_boundaries_w=active_boundaries_w,
            alpha_t=alpha_t,
            beta_t=beta_t,
            gamma_corner=gamma_corner,
        )

        # If no motion, we are locally stable; stop
        if not np.any(v):
            break

        # Step schedule and deterministic backtracking with extended lexicographic rule
        base_step = s0 * (rho**t)
        step = base_step
        accepted = False

        # Precompute current tie-break metrics
        E_curr = _contact_energy(centers, radii)
        D_curr = _max_violation(centers, radii)

        for _ in range(10):
            trial_centers = centers + step * v
            # Project to [eps, 1 - eps]^2 deterministically
            trial_centers = np.clip(trial_centers, eps, 1.0 - eps)
            trial_radii, _ = _solve_lp_radii(trial_centers)
            trial_obj = float(np.sum(trial_radii))

            # Lexicographic acceptance:
            # 1) Strict primary improvement (objective)
            if trial_obj > obj_sum + 1e-12:
                centers = trial_centers
                radii = trial_radii
                obj_sum = trial_obj
                accepted = True
                break

            # 2) If primary is flat within ±1e-12, accept if contact energy decreases
            if abs(trial_obj - obj_sum) <= 1e-12:
                E_trial = _contact_energy(trial_centers, trial_radii)
                if E_trial < E_curr - 1e-18:
                    centers = trial_centers
                    radii = trial_radii
                    obj_sum = trial_obj
                    accepted = True
                    break

                # 3) If both are flat within those thresholds, accept if max violation decreases
                if abs(E_trial - E_curr) <= 1e-18:
                    D_trial = _max_violation(trial_centers, trial_radii)
                    if D_trial < D_curr - 1e-16:
                        centers = trial_centers
                        radii = trial_radii
                        obj_sum = trial_obj
                        accepted = True
                        break

            # Otherwise, backtrack
            step *= 0.5

        if not accepted:
            # Could not find an acceptable step; terminate deterministically
            break

    # Final LP to be safe before returning
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

    Pairs:
      include (i, j) if d_ij <= r_i + r_j + 3*tol.
      weight w_ij = max(0, 1 - (d_ij - (r_i + r_j)) / (3*tol)).
    Boundaries:
      for each circle i and each side s in left, bottom, right, top, let clearance_s be
      the distance to that side. If clearance_s - r_i <= 3*tol then include (i, s) with
      weight w_is = max(0, 1 - (clearance_s - r_i) / (3*tol)).

    Returns:
      - active_pairs_w: list of (i, j, weight)
      - active_boundaries_w: list of (i, side, weight)
    """
    n = centers.shape[0]
    dists = _pairwise_distances(centers)
    active_pairs_w: List[Tuple[int, int, float]] = []
    thr = 3.0 * max(tol, 0.0)

    # Pairs with linear weights
    if thr > 0.0:
        for i in range(n):
            for j in range(i + 1, n):
                gap = dists[i, j] - (radii[i] + radii[j])
                if gap <= thr:
                    w = 1.0 - max(gap, 0.0) / thr
                    if w > 0.0:
                        active_pairs_w.append((i, j, w))

    # Boundaries: side-specific, deterministic side order
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
    if thr > 0.0:
        for i in range(n):
            for side in sides_order:
                clearance = float(clearances[side][i])
                gap = clearance - radii[i]
                if gap <= thr:
                    w = 1.0 - max(gap, 0.0) / thr
                    if w > 0.0:
                        active_boundaries_w.append((i, side, w))

    return active_pairs_w, active_boundaries_w


def _build_weighted_movement_vectors(
    centers: np.ndarray,
    active_pairs_w: List[Tuple[int, int, float]],
    active_boundaries_w: List[Tuple[int, str, float]],
    alpha_t: float,
    beta_t: float,
    gamma_corner: float,
) -> np.ndarray:
    """Build deterministic movement vectors from the smoothly-weighted near-active set.

    - Endpoint-aware pair attenuation: for a pair (i, j) with base weight w,
      apply weight at endpoint i as (alpha_t * w) if circle i has any boundary near-contact
      this iteration; else w. Similarly for j.
    - Boundary-bias: boundary weights are scaled by beta_t; if a circle has near-active
      contacts with at least two distinct sides, multiply all its boundary contributions
      by gamma_corner to encourage motion away from corners.
    - For pairs: add w_i * u_ij to i and -w_j * u_ij to j, where u_ij is the unit vector from j to i.
    - For boundaries: add scaled weight times inward unit normal.

    After aggregation, normalize each nonzero per-circle vector to unit length and
    rescale globally so the maximum norm is 1.

    Returns:
      v: array of shape (n, 2) with the movement vectors.
    """
    n = centers.shape[0]
    v = np.zeros_like(centers)

    # Determine for each circle whether it has any boundary near-contact and corner status
    has_boundary = np.zeros(n, dtype=bool)
    side_sets = [set() for _ in range(n)]
    for (i, side, _w) in active_boundaries_w:
        has_boundary[i] = True
        side_sets[i].add(side)

    # Precompute corner-boost flags
    corner_boost = np.array([len(side_sets[i]) >= 2 for i in range(n)], dtype=bool)

    # Near-active pairs: endpoint-aware attenuation
    for (i, j, w) in active_pairs_w:
        diff = centers[i] - centers[j]
        dist = float(np.linalg.norm(diff))
        if dist > 0.0:
            u = diff / dist
        else:
            u = np.array([1.0, 0.0])  # deterministic tie-break if coincident
        wi = (alpha_t * w) if has_boundary[i] else w
        wj = (alpha_t * w) if has_boundary[j] else w
        v[i] += wi * u
        v[j] -= wj * u

    # Near-active boundaries: inward unit normals (away from edges), scaled by beta_t and corner boost
    for (i, side, w) in active_boundaries_w:
        scale = beta_t * (gamma_corner if corner_boost[i] else 1.0)
        if side == "left":
            v[i] += (w * scale) * np.array([+1.0, 0.0])
        elif side == "right":
            v[i] += (w * scale) * np.array([-1.0, 0.0])
        elif side == "bottom":
            v[i] += (w * scale) * np.array([0.0, +1.0])
        elif side == "top":
            v[i] += (w * scale) * np.array([0.0, -1.0])

    # Per-circle normalization
    norms = np.linalg.norm(v, axis=1)
    for i in range(n):
        if norms[i] > 0.0:
            v[i] /= norms[i]

    # Global rescaling so max norm is 1
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


def _max_violation(centers: np.ndarray, radii: np.ndarray) -> float:
    """Compute the maximum single constraint violation:
    D = max(0, max_{i<j}(r_i + r_j - d_ij), max_i(r_i - b_i)).
    """
    dists = _pairwise_distances(centers)
    n = centers.shape[0]
    max_pair = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            overlap = (radii[i] + radii[j]) - dists[i, j]
            if overlap > max_pair:
                max_pair = overlap
    xs = centers[:, 0]
    ys = centers[:, 1]
    b_i = np.minimum.reduce([xs, ys, 1.0 - xs, 1.0 - ys])
    max_bnd = float(np.max(radii - b_i))
    return float(max(0.0, max(max_pair, max_bnd)))
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
