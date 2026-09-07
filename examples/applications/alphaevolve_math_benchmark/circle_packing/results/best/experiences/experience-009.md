Insight on why the constructor achieved strong, valid performance and what to reuse.

- EdgeHex-45 + WarmLP + One-Sided Repair with Adaptive TightWalls and Piggyback Dual-Trigger Micro-SLP: The algorithm’s strong outcome (sum_radii 2.625000957 with validity 1.0) stems from chaining a fixed-center LP with an immediate deterministic one-sided pair repair in exactly two lexicographic passes, which strictly enforces feasibility while minimizing collateral shrinkage; when contact-guided moves yield tiny gains (<1e−8), it attempts a piggyback micro‑SLP in a small trust region and accepts only under a monotone non‑decreasing objective, with a periodic/plateau-triggered micro‑SLP reinforcing coordinated improvements. Reuse this pattern: after every LP, apply one‑sided repair and gate any contact or micro‑SLP step behind a non‑decreasing objective check, and begin with a best‑of‑45 hex seed scored by LP+repair to balance wall slack early.

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

Implements the algorithm described in the prompt:
- Best-of-45 edge-fitted hexagonal seeding of 26 centers.
- Fixed-center LP radii solver (maximize sum of radii) via primal simplex with Bland’s rule.
- Deterministic one-sided minimal pair repair to remove residual overlaps.
- Iterative center relaxation guided by near-active constraints with monotone acceptance.
- Piggyback micro-SLP on tiny line-search gains and periodic micro-SLP trust-region steps
  that jointly optimize radii and small center displacements.

All computations are deterministic: fixed seed scan ordering, lexicographic iteration orders,
Bland’s rule for LP pivots, monotone acceptance rules, and clipping to [eps, 1-eps].
"""

import json
from typing import Tuple

import numpy as np


# EVOLVE_START
# Numerical constants and parameters used across the algorithm
TOL = 1e-10
OBJ_TOL = 1e-12

# Seeding parameters
EPS = 1e-3  # interior margin
ROW_COUNTS_BASE = [5, 5, 5, 5, 6]  # one row of 6; its position is scanned
SEED_PHASES = (-0.2, 0.0, 0.2)  # relative phase shifts
# LP Solver parameters
LP_MAX_PIVOTS = 20000  # safety cap on simplex pivots
SIMPLEX_TOL = 1e-12  # tolerance for entering/ratio tests

# Motion field parameters
BASE_W_BOUNDARY_DEFAULT = 2.2
W_MAX = 1e6
SIGMA = 1e-9
DELTA = 1e-9
CAP_PER_NODE = 3.0

# Backtracking parameters
RHO = 0.95
ALPHA_MIN_SCALE = 1e-6
SMALL_IMPROVEMENT_THRESH = 1e-8

# Micro-SLP trust-region parameters
TR_START_SCALE = 0.08
TR_BACKTRACK = 0.75
TR_FLOOR_SCALE = 1e-4
TR_GROWTH = 1.15  # gentle growth when improvement meaningful
MICRO_SLP_MAX_ATTEMPTS = 8
PIGGYBACK_TR_SCALE = 0.06  # small trust region for piggyback micro-SLP

# Iteration limits
MAX_ACCEPTED_MOVES = 180
SMALL_IMPROVEMENT_STREAK_LIMIT = 7


def construct_packing(num_circles: int = 26) -> Tuple[np.ndarray, np.ndarray]:
    """Return centers and radii for a valid packing of `num_circles` circles.

    Implements:
    1) Best-of-45 deterministic hexagonal seed for centers.
    2) Fixed-center LP radii optimization with Bland’s rule.
    3) Active-set guided center relaxation with monotone acceptance and backtracking.
    4) Piggyback and periodic micro-SLP trust-region steps to coordinate multi-point moves.
    5) Final LP and two-pass deterministic one-sided pair repair for clean feasibility.

    Returns:
        centers: array (26, 2)
        radii: array (26,)
    """
    assert num_circles == 26, "This constructor is specialized for 26 circles."

    # Best-of-45 deterministic hexagonal seed (r6 row index and small phases s,t)
    centers, dx_seed, dy_seed = best_of_45_hex_seed(num_circles, eps=EPS)

    # Initial fixed-center LP solve
    radii, obj = lp_radii_fixed_centers(centers)

    # Iterative relaxation of centers guided by near-active constraints
    centers, radii = optimize_centers(centers, radii, obj, dx_seed, dy_seed, eps=EPS)

    # Final LP radii and pair repair
    radii, _ = lp_radii_fixed_centers(centers)
    radii = pair_repair(centers, radii, passes=2)

    # Clamp outputs to valid ranges
    centers = np.clip(centers, EPS, 1.0 - EPS)
    radii = np.clip(radii, 0.0, np.inf)
    return centers, radii


def best_of_45_hex_seed(num_circles: int, eps: float = EPS) -> Tuple[np.ndarray, float, float]:
    """Scan 45 variants of hexagonal seeds and pick the best by fixed-center LP score.

    Variants:
      - Choose row r6 in {0,1,2,3,4} to carry 6 columns; others carry 5.
      - Apply vertical phase s in {-0.2,0,+0.2} times dy.
      - Apply horizontal phase t in {-0.2,0,+0.2} times dy to the 5-column row offsets.

    Tie-breaker: prefer r6 == 2, s == 0, t == 0 deterministically.
    """
    assert num_circles == 26
    # Common spacings
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx

    best_centers = None
    best_obj = -np.inf
    best_key = None

    # Build span and centered y_start (without vertical shift)
    num_rows = 5
    span = (num_rows - 1) * dy
    y_start_base = 0.5 - span / 2.0
    # Clamp into [eps,1-eps] just in case (though span ensures interior)
    y_start_base = np.clip(y_start_base, eps, 1.0 - eps)

    # Deterministic scan order
    for r6 in range(5):
        for s in SEED_PHASES:
            for t in SEED_PHASES:
                centers = np.zeros((num_circles, 2), dtype=float)
                idx = 0
                y_start = y_start_base + s * dy
                for r in range(num_rows):
                    y = y_start + r * dy
                    count = 6 if r == r6 else 5
                    if count == 6:
                        # 6-column row: standard alignment at integer multiples of dx
                        xs = [eps + c * dx for c in range(6)]
                    else:
                        # 5-column rows: staggered by +0.5*dx with small phase t*dy in x
                        x_shift = 0.5 * dx + t * dy
                        xs = [eps + x_shift + c * dx for c in range(5)]
                    for c in range(count):
                        centers[idx, 0] = xs[c]
                        centers[idx, 1] = y
                        idx += 1

                # Clip to [eps, 1-eps] to ensure feasibility
                centers = np.clip(centers, eps, 1.0 - eps)
                # Score via fixed-center LP + repair
                radii, obj = lp_radii_fixed_centers(centers)
                # Tie-breaker key: prefer r6==2, s==0, t==0; then lexicographic by (abs(s), abs(t), r6)
                # We build a tuple that gets minimized: (-obj) is compared separately.
                tie_key = (
                    0 if r6 == 2 else 1,
                    0 if abs(s) <= 1e-15 else 1,
                    0 if abs(t) <= 1e-15 else 1,
                    abs(s),
                    abs(t),
                    r6,
                )
                # Update best with primary metric obj, then tie key
                if obj > best_obj + 0.0:
                    best_obj = obj
                    best_centers = centers
                    best_key = tie_key
                elif abs(obj - best_obj) <= 1e-15:
                    # Compare tie keys deterministically (prefer smaller tuple)
                    if tie_key < best_key:
                        best_centers = centers
                        best_key = tie_key

    return best_centers, dx, dy


def lp_radii_fixed_centers(centers: np.ndarray) -> Tuple[np.ndarray, float]:
    """Solve for optimal radii with fixed centers via primal simplex with Bland’s rule.

    LP form:
      maximize sum(r_i)
      subject to:
        - boundary constraints: r_i <= x_i, r_i <= y_i, r_i <= 1-x_i, r_i <= 1-y_i
        - pair constraints: r_i + r_j <= ||c_i - c_j||  for all i<j
      variables: r_i >= 0

    Returns:
        radii: array of optimal radii after two-pass deterministic one-sided pair repair
        obj: sum of radii (objective value)
    """
    n = centers.shape[0]
    # Boundary A, b
    x = centers[:, 0]
    y = centers[:, 1]

    # Number of constraints
    m_bnd = 4 * n
    m_pairs = n * (n - 1) // 2
    m = m_bnd + m_pairs

    # Build A (m x n) and b (m)
    A = np.zeros((m, n), dtype=float)
    b = np.zeros(m, dtype=float)

    # Fill boundary constraints
    row = 0
    # r_i <= x_i
    for i in range(n):
        A[row, i] = 1.0
        b[row] = x[i]
        row += 1
    # r_i <= y_i
    for i in range(n):
        A[row, i] = 1.0
        b[row] = y[i]
        row += 1
    # r_i <= 1-x_i
    for i in range(n):
        A[row, i] = 1.0
        b[row] = 1.0 - x[i]
        row += 1
    # r_i <= 1-y_i
    for i in range(n):
        A[row, i] = 1.0
        b[row] = 1.0 - y[i]
        row += 1

    # Fill pair constraints r_i + r_j <= d_ij
    for i in range(n):
        for j in range(i + 1, n):
            A[row, i] = 1.0
            A[row, j] = 1.0
            d = float(np.linalg.norm(centers[i] - centers[j]))
            b[row] = d
            row += 1

    c = np.ones(n, dtype=float)  # maximize sum(r_i)

    # Solve LP in standard form using primal simplex and Bland’s rule
    r_opt, obj = primal_simplex_max(A, b, c, tol=SIMPLEX_TOL, max_pivots=LP_MAX_PIVOTS)

    # Deterministic two-pass one-sided minimal pair repair to remove tiny overlaps
    r_opt = pair_repair(centers, r_opt, passes=2)

    return r_opt, float(np.sum(r_opt))


def primal_simplex_max(A: np.ndarray, b: np.ndarray, c: np.ndarray, tol: float = SIMPLEX_TOL, max_pivots: int = LP_MAX_PIVOTS) -> Tuple[np.ndarray, float]:
    """Primal simplex with Bland’s rule for maximize c^T x subject to A x <= b, x >= 0.

    Standard form:
      A x + s = b,  s >= 0
      Maximize c^T x

    Implementation details:
      - Dense tableau approach with explicit slack columns.
      - Bland’s rule: smallest-index entering variable with negative reduced cost (row 0).
      - Ties for leaving variable broken deterministically by smallest row index.
      - Extract solution via tracked basis variables.

    Args:
        A: (m, n) constraint matrix
        b: (m,) RHS vector
        c: (n,) objective coefficients
        tol: numerical tolerance for pivot tests
        max_pivots: hard cap on pivot iterations

    Returns:
        x_opt: optimal nonnegative decision variable vector (n,)
        obj: optimal objective value (float)
    """
    m, n = A.shape
    # Build tableau: rows = m + 1, cols = n (decision) + m (slack) + 1 (RHS)
    cols = n + m + 1
    T = np.zeros((m + 1, cols), dtype=float)
    # Objective row: z - c^T x = 0 -> coefficients are -c for decision variables
    T[0, :n] = -c
    T[0, -1] = 0.0

    # Constraint rows: A x + I s = b
    T[1 : m + 1, :n] = A
    T[1 : m + 1, n : n + m] = np.eye(m)
    T[1 : m + 1, -1] = b

    # Basis tracking: initial basis are the slack variables at indices n..n+m-1
    basis_var = np.array([n + i for i in range(m)], dtype=int)

    pivots = 0
    while pivots < max_pivots:
        # Bland’s rule: choose smallest-index entering variable with negative coefficient in row 0
        # Only decision variables (r) can enter; slack variables have zero reduced cost initially.
        entering = -1
        for j in range(n):
            if T[0, j] < -tol:
                entering = j
                break
        if entering == -1:
            # Optimal: no entering variable with negative reduced cost
            break

        # Ratio test: among rows with positive pivot column, choose minimal ratio RHS / col
        pivot_row = -1
        pivot_value = 0.0
        min_ratio = np.inf
        for i in range(1, m + 1):
            col_val = T[i, entering]
            if col_val > tol:
                ratio = T[i, -1] / col_val
                if ratio < min_ratio - 0:
                    min_ratio = ratio
                    pivot_row = i
                    pivot_value = col_val
                elif abs(ratio - min_ratio) <= tol:
                    # Tie break deterministically by smallest row index
                    if i < pivot_row:
                        pivot_row = i
                        pivot_value = col_val
        if pivot_row == -1:
            # Unbounded (should not occur due to slack bounds); return zeros deterministically
            x = np.zeros(n, dtype=float)
            return x, 0.0

        # Pivot operation: normalize pivot row
        T[pivot_row, :] = T[pivot_row, :] / pivot_value

        # Eliminate entering column in all other rows including objective row
        for i in range(0, m + 1):
            if i == pivot_row:
                continue
            factor = T[i, entering]
            if abs(factor) > 0.0:
                T[i, :] = T[i, :] - factor * T[pivot_row, :]

        # Update basis variable for this row
        basis_var[pivot_row - 1] = entering

        pivots += 1

    # Extract solution x: variables in basis have their RHS values, others zero
    x = np.zeros(n, dtype=float)
    for i in range(1, m + 1):
        var_idx = basis_var[i - 1]
        if var_idx < n:
            xi = T[i, -1]
            x[var_idx] = xi if xi > 0.0 else 0.0

    obj = T[0, -1]
    x = np.clip(x, 0.0, np.inf)
    return x, float(obj)


def pair_repair(centers: np.ndarray, radii: np.ndarray, passes: int = 2) -> np.ndarray:
    """Deterministic two-pass one-sided minimal pair repair to remove residual overlaps.

    For each pair (i<j) in lexicographic order, exactly `passes` sweeps:
        - Compute excess s = (r_i + r_j) - d_ij.
        - If s > tol, subtract exactly s from ONE radius only so the pair saturates:
            r_k := max(0, r_k - s) for the chosen k in {i, j}.
        - Deterministic selection rule:
            1) Compute boundary slack_b(t) = min(x_t, y_t, 1-x_t, 1-y_t) - r_t for t in {i,j}.
               Prefer shrinking the circle with larger slack_b (i.e., less boundary-limited).
            2) If |slack_b(i) - slack_b(j)| <= tiny_tol, shrink the larger radius.
            3) If still tied within tiny_tol, shrink the higher index (j) to ensure determinism.
    """
    n = len(radii)
    r = np.array(radii, dtype=float)
    # Small tolerances: violation threshold and tie-break tolerances
    viol_tol = max(SIMPLEX_TOL, 1e-15)
    tie_tol = 1e-15

    xs = centers[:, 0]
    ys = centers[:, 1]
    min_clears = np.minimum.reduce([xs, ys, 1.0 - xs, 1.0 - ys])

    for _ in range(max(1, passes)):
        for i in range(n):
            for j in range(i + 1, n):
                dij = float(np.linalg.norm(centers[i] - centers[j]))
                s = (r[i] + r[j]) - dij
                if s > viol_tol:
                    # Boundary-aware slacks with current radii
                    slack_i = min_clears[i] - r[i]
                    slack_j = min_clears[j] - r[j]

                    # Decide which radius to shrink
                    if slack_i > slack_j + tie_tol:
                        k = i
                    elif slack_j > slack_i + tie_tol:
                        k = j
                    else:
                        # slacks tied
                        if r[i] > r[j] + tie_tol:
                            k = i
                        elif r[j] > r[i] + tie_tol:
                            k = j
                        else:
                            k = j  # deterministic

                    # Subtract exactly the excess; clamp at zero
                    r[k] = max(0.0, r[k] - s)

        r = np.clip(r, 0.0, np.inf)

    return r


def optimize_centers(centers: np.ndarray, radii: np.ndarray, obj: float, dx_seed: float, dy_seed: float, eps: float = EPS) -> Tuple[np.ndarray, np.ndarray]:
    """Iteratively relax centers guided by near-active constraints, with monotone acceptance.

    - Build motion field from active boundary and near-contact pairs.
    - Monotone backtracked line search: accept only non-decreasing LP objectives.
    - Piggyback micro-SLP when line search yields tiny improvement.
    - Periodic micro-SLP trust-region steps; also try micro-SLP as a plateau escape.
    """
    n = centers.shape[0]
    current_centers = centers.copy()
    current_radii = radii.copy()
    current_obj = obj

    # Step-size baseline and thresholds
    s0 = 0.1 * min(dx_seed, dy_seed)
    alpha_min = ALPHA_MIN_SCALE * s0
    accepts = 0
    tiny_improve_streak = 0

    # Trust-region step cadence
    tr_h = TR_START_SCALE * min(dx_seed, dy_seed)
    tr_floor = TR_FLOOR_SCALE * min(dx_seed, dy_seed)

    # Deterministic loop limits
    for _ in range(MAX_ACCEPTED_MOVES * 2):
        # Build motion field vector per center
        v, wsum = build_motion_field(current_centers, current_radii, eps=eps)

        # Normalize vector field and cap magnitudes
        v_norm = normalize_vectors(v, cap=CAP_PER_NODE, wsum=wsum)

        # Backtracking line search
        accepted = False
        alpha = s0
        improvement = 0.0
        while alpha >= alpha_min:
            trial_centers = current_centers + alpha * v_norm
            # Clip to [eps, 1-eps]
            trial_centers = np.clip(trial_centers, eps, 1.0 - eps)

            trial_radii, trial_obj = lp_radii_fixed_centers(trial_centers)

            # Accept only non-decreasing objective with tolerance
            if trial_obj >= current_obj - OBJ_TOL:
                # Apply
                improvement = trial_obj - current_obj
                current_centers = trial_centers
                current_radii = trial_radii
                current_obj = trial_obj
                accepted = True
                accepts += 1
                if improvement < SMALL_IMPROVEMENT_THRESH:
                    tiny_improve_streak += 1
                else:
                    tiny_improve_streak = 0
                # Slightly reduce s0 if we had consecutive tiny improvements to quell micro-oscillations
                if tiny_improve_streak >= 3:
                    s0 *= 0.8
                    tiny_improve_streak = 0
                break
            alpha *= RHO

        if accepted:
            # Piggyback micro-SLP on tiny gains to harvest coordinated improvements
            if improvement < SMALL_IMPROVEMENT_THRESH:
                h_small = PIGGYBACK_TR_SCALE * min(dx_seed, dy_seed)
                c2p, r2p, o2p = micro_slp_step(current_centers, current_radii, current_obj, h=h_small, eps=eps)
                if o2p >= current_obj - OBJ_TOL:
                    current_centers = c2p
                    current_radii = r2p
                    current_obj = o2p  # accept if non-decreasing

            # Periodically attempt a micro-SLP trust-region step to coordinate small multi-point moves
            if accepts % 2 == 0:
                c2, r2, o2 = micro_slp_step(current_centers, current_radii, current_obj, h=tr_h, eps=eps)
                if o2 >= current_obj - OBJ_TOL:
                    # Improvement assessment before updating current_obj
                    delta = o2 - current_obj
                    current_centers = c2
                    current_radii = r2
                    current_obj = o2
                    # gentle growth if meaningful improvement
                    if delta > SMALL_IMPROVEMENT_THRESH:
                        tr_h = min(tr_h * TR_GROWTH, 0.12 * min(dx_seed, dy_seed))
                else:
                    # Backtrack trust region size if rejected
                    tr_h = max(tr_h * TR_BACKTRACK, tr_floor)
        else:
            # No accepted contact move: try micro-SLP escape step
            prev_obj = current_obj
            c2, r2, o2 = micro_slp_step(current_centers, current_radii, current_obj, h=tr_h, eps=eps)
            if o2 >= current_obj - OBJ_TOL:
                delta = o2 - prev_obj
                current_centers = c2
                current_radii = r2
                current_obj = o2
                accepts += 1
                tiny_improve_streak = 0
                # gentle growth on meaningful success
                if delta > SMALL_IMPROVEMENT_THRESH:
                    tr_h = min(tr_h * TR_GROWTH, 0.12 * min(dx_seed, dy_seed))
            else:
                # Backtrack trust region and check termination
                tr_h = max(tr_h * TR_BACKTRACK, tr_floor)

            # Termination checks
            if accepts >= MAX_ACCEPTED_MOVES or tiny_improve_streak >= SMALL_IMPROVEMENT_STREAK_LIMIT:
                break

        # Additional termination guard
        if accepts >= MAX_ACCEPTED_MOVES or tiny_improve_streak >= SMALL_IMPROVEMENT_STREAK_LIMIT:
            break

    return current_centers, current_radii


def build_motion_field(centers: np.ndarray, radii: np.ndarray, eps: float = EPS) -> Tuple[np.ndarray, np.ndarray]:
    """Construct a motion field from near-active constraints.

    Boundary activeness:
      - For each i, compute clears [x_i, y_i, 1-x_i, 1-y_i], min_clear.
      - If r_i >= min_clear - tau_bnd, add inward normals for sides achieving min_clear.
      - Weight magnitude by BASE · tightness^1.5, tightness computed via relative slack.
        BASE is softly modulated by average boundary tightness and clamped in [2.1, 2.5].

    Pair activeness:
      - For each pair (i, j), slack s_ij = d_ij - (r_i + r_j).
      - If s_ij <= tol_pair, push along separating direction n with weight
        w = min(W_MAX, 1/(s_ij + SIGMA)) * 1/max(d_ij, DELTA),
        add -n*w to i and +n*w to j.

    Accumulation safeguards:
      - Maintain per-node weight sum.
      - Return accumulated vector and weights for later normalization.
    """
    n = centers.shape[0]
    v = np.zeros_like(centers)
    wsum = np.zeros(n, dtype=float)

    xs = centers[:, 0]
    ys = centers[:, 1]
    clears = np.stack([xs, ys, 1.0 - xs, 1.0 - ys], axis=1)
    min_clear = np.min(clears, axis=1)
    tau_bnd = np.maximum.reduce([np.full(n, 5e-5), 0.02 * min_clear, np.full(n, 10.0 * TOL)])

    # Compute per-node boundary tightness and average to modulate BASE
    tightness_arr = np.zeros(n, dtype=float)
    active_boundary = np.zeros(n, dtype=bool)
    for i in range(n):
        mc = min_clear[i]
        if radii[i] >= mc - tau_bnd[i]:
            t = 1.0 - (mc - radii[i]) / max(mc, 1e-9)
            tightness_arr[i] = np.clip(t, 0.0, 1.0)
            active_boundary[i] = True
    if np.any(active_boundary):
        avg_tight = float(np.mean(tightness_arr[active_boundary]))
    else:
        avg_tight = 0.0
    base_w = np.clip(BASE_W_BOUNDARY_DEFAULT + 0.3 * (avg_tight - 0.5), 2.1, 2.5)

    # Boundary contributions using Adaptive TightWalls
    for i in range(n):
        if not active_boundary[i]:
            continue
        mc = min_clear[i]
        tightness = tightness_arr[i]
        w = base_w * (tightness ** 1.5)
        # Add contributions for the minimum sides (can be multiple)
        for sidx, val in enumerate(clears[i]):
            if abs(val - mc) <= 1e-12:
                if sidx == 0:  # x_i -> inward +x
                    v[i, 0] += +w
                elif sidx == 1:  # y_i -> inward +y
                    v[i, 1] += +w
                elif sidx == 2:  # 1-x_i -> inward -x
                    v[i, 0] += -w
                else:  # 1-y_i -> inward -y
                    v[i, 1] += -w
                wsum[i] += w

    # Pair contributions (inverse-slack weighting with caps)
    tol_pair = max(TOL, 1e-6)
    for i in range(n):
        pi = centers[i]
        ri = radii[i]
        for j in range(i + 1, n):
            pj = centers[j]
            rj = radii[j]
            d_vec = pj - pi
            d = float(np.linalg.norm(d_vec))
            if d < DELTA:
                nvec = np.array([1.0, 0.0], dtype=float)
            else:
                nvec = d_vec / d
            slack = d - (ri + rj)
            if slack <= tol_pair:
                w = min(W_MAX, 1.0 / (slack + SIGMA)) * (1.0 / max(d, DELTA))
                vi = -nvec * w
                vj = +nvec * w
                v[i] += vi
                v[j] += vj
                wsum[i] += w
                wsum[j] += w

    return v, wsum


def normalize_vectors(v: np.ndarray, cap: float = CAP_PER_NODE, wsum: np.ndarray = None) -> np.ndarray:
    """Cap per-node vector magnitudes, weight-average, and unit-normalize per node."""
    u = v.copy()
    n = u.shape[0]
    # Cap magnitudes
    for i in range(n):
        mag = float(np.linalg.norm(u[i]))
        if mag > cap and mag > 0.0:
            u[i] *= cap / mag

    # Weight-average to avoid hub dominance
    if wsum is not None:
        for i in range(n):
            denom = 1.0 + wsum[i]
            if denom > 0.0:
                u[i] /= denom

    # Unit-normalize per node
    for i in range(n):
        mag = float(np.linalg.norm(u[i]))
        if mag > 0.0:
            u[i] /= mag
        else:
            u[i] = np.array([0.0, 0.0], dtype=float)

    return u


def micro_slp_step(centers: np.ndarray, radii: np.ndarray, obj: float, h: float, eps: float = EPS) -> Tuple[np.ndarray, np.ndarray, float]:
    """Periodic micro-SLP trust-region step.

    Linearized LP over both radii and bounded center displacements:
      - Variables: r_i >= 0, u+_i >= 0, u-_i >= 0, v+_i >= 0, v-_i >= 0, with bounds u+_i <= h, u-_i <= h, v+_i <= h, v-_i <= h
      - Δx_i = u+_i - u-_i; Δy_i = v+_i - v-_i
      - Boundary constraints:
          r_i - u+_i + u-_i <= x_i
          r_i - v+_i + v-_i <= y_i
          r_i + u+_i - u-_i <= 1 - x_i
          r_i + v+_i - v-_i <= 1 - y_i
      - Pair constraints (linearized along current pair normals):
          r_i + r_j - nx*u+_j + nx*u-_j + nx*u+_i - nx*u-_i
                       - ny*v+_j + ny*v-_j + ny*v+_i - ny*v-_i <= d_ij
      - Trust-region bounds:
          u+_i <= h, u-_i <= h, v+_i <= h, v-_i <= h
      - Objective: maximize sum r_i

    Returns:
        trial_centers, trial_radii, trial_obj
    """
    n = centers.shape[0]
    # Build linearized constraints
    # Variables: [r(0..n-1), u+(n..2n-1), u-(2n..3n-1), v+(3n..4n-1), v-(4n..5n-1)]
    var_count = 5 * n
    # Constraints count:
    m_bnd = 4 * n
    m_pairs = n * (n - 1) // 2
    m_bounds = 4 * n
    m = m_bnd + m_pairs + m_bounds

    A = np.zeros((m, var_count), dtype=float)
    b = np.zeros(m, dtype=float)

    xs = centers[:, 0]
    ys = centers[:, 1]

    row = 0
    # Boundary constraints
    for i in range(n):
        # r_i - u+_i + u-_i <= x_i
        A[row, i] = 1.0
        A[row, n + i] = -1.0
        A[row, 2 * n + i] = +1.0
        b[row] = xs[i]
        row += 1
    for i in range(n):
        # r_i - v+_i + v-_i <= y_i
        A[row, i] = 1.0
        A[row, 3 * n + i] = -1.0
        A[row, 4 * n + i] = +1.0
        b[row] = ys[i]
        row += 1
    for i in range(n):
        # r_i + u+_i - u-_i <= 1 - x_i
        A[row, i] = 1.0
        A[row, n + i] = +1.0
        A[row, 2 * n + i] = -1.0
        b[row] = 1.0 - xs[i]
        row += 1
    for i in range(n):
        # r_i + v+_i - v-_i <= 1 - y_i
        A[row, i] = 1.0
        A[row, 3 * n + i] = +1.0
        A[row, 4 * n + i] = -1.0
        b[row] = 1.0 - ys[i]
        row += 1

    # Pair constraints: linearized along normals
    for i in range(n):
        for j in range(i + 1, n):
            d_vec = centers[j] - centers[i]
            d = float(np.linalg.norm(d_vec))
            if d < DELTA:
                nx, ny = 1.0, 0.0
            else:
                nx, ny = (d_vec / d).tolist()
            # r_i + r_j + linearized displacement terms <= d
            A[row, i] = 1.0
            A[row, j] = 1.0
            # j contributions: moving j along +n increases distance -> negative coeff for u+_j and v+_j
            A[row, n + j] += -nx
            A[row, 2 * n + j] += +nx
            A[row, 3 * n + j] += -ny
            A[row, 4 * n + j] += +ny
            # i contributions: moving i along -n increases distance -> positive coeff for u+_i and v+_i
            A[row, n + i] += +nx
            A[row, 2 * n + i] += -nx
            A[row, 3 * n + i] += +ny
            A[row, 4 * n + i] += -ny
            b[row] = d
            row += 1

    # Trust-region bound constraints: u+_i <= h, u-_i <= h, v+_i <= h, v-_i <= h
    for i in range(n):
        A[row, n + i] = 1.0
        b[row] = h
        row += 1
    for i in range(n):
        A[row, 2 * n + i] = 1.0
        b[row] = h
        row += 1
    for i in range(n):
        A[row, 3 * n + i] = 1.0
        b[row] = h
        row += 1
    for i in range(n):
        A[row, 4 * n + i] = 1.0
        b[row] = h
        row += 1

    # Objective: maximize sum r_i
    c = np.zeros(var_count, dtype=float)
    c[:n] = 1.0

    # Solve micro LP using primal simplex with Bland's rule
    x_opt, _ = primal_simplex_max(A, b, c, tol=SIMPLEX_TOL, max_pivots=LP_MAX_PIVOTS)

    # Extract variables
    r_opt = x_opt[:n]
    u_plus = x_opt[n : 2 * n]
    u_minus = x_opt[2 * n : 3 * n]
    v_plus = x_opt[3 * n : 4 * n]
    v_minus = x_opt[4 * n : 5 * n]

    # Displacements
    dx = u_plus - u_minus
    dy = v_plus - v_minus

    trial_centers = centers.copy()
    trial_centers[:, 0] += dx
    trial_centers[:, 1] += dy
    # Clip to [eps, 1-eps]
    trial_centers = np.clip(trial_centers, eps, 1.0 - eps)

    # Recompute exact LP radii for new centers and repair
    trial_radii, trial_obj = lp_radii_fixed_centers(trial_centers)
    trial_radii = pair_repair(trial_centers, trial_radii, passes=2)

    return trial_centers, trial_radii, trial_obj


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
# EVOLVE_END
```
