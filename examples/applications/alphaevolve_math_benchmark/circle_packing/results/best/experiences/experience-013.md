Use a single, deterministic micro‑SLP trust‑region step immediately after a tiny contact‑guided improvement to escape local plateaus while preserving monotone acceptance.

- Piggyback micro‑SLP step: After any accepted contact‑guided move with improvement < SMALL_IMPROVEMENT_THRESH, invoke one micro‑SLP trust‑region update initialized with h_small = 0.06·min(dx_seed, dy_seed), reuse TR_BACKTRACK and TR_FLOOR, and accept only if obj_micro ≥ obj_contact − OBJ_TOL to preserve monotone determinism; reset micro cadence counters only on acceptance.
- SMALL_IMPROVEMENT_STREAK_LIMIT constant: Raise SMALL_IMPROVEMENT_STREAK_LIMIT from 5 to 7 to slightly loosen the tiny‑improvement stopping guard and give the piggyback step more chances to consolidate progress before termination.
- Algorithm Piggyback micro‑SLP on tiny contact gains: The design targets local plateaus where contact‑only updates yield sub‑threshold gains and leverages freshly updated contact normals to frequently unlock an extra 1–3e−3 in objective within the same acceptance budget.
- Run 1 metrics for Piggyback micro‑SLP on tiny contact gains: The constructor achieved sum_radii = 2.624582696358895 with validity = 1.0 and no errors, integrating the piggyback step with +23/−3 line changes in solve.py while maintaining deterministic guards.
- Dual-trigger piggyback micro-SLP scaled by backtracking: After any accepted contact-guided move, the algorithm computes alpha_ratio = accepted_alpha / s0 and triggers a piggyback micro-SLP not only when improvement < SMALL_IMPROVEMENT_THRESH but also when alpha_ratio < 0.5, leveraging immediate coordinated adjustments exactly when heavy backtracking signals tightly coupled constraints; this run achieved sum_radii 2.6296189481749646 with validity 1.0 and no errors.
- Piggyback micro-SLP trust-region side h_small: The piggyback trust-region side is set to h_small = 0.06 * clamp(alpha_ratio, 0.5, 1.0) * min(dx_seed, dy_seed), shrinking steps proportionally after severe backtracking while retaining the original 0.06 scaling when the full step is accepted, providing conservative yet coordinated nudges.
- Piggyback micro-SLP acceptance policy: Piggyback updates are accepted only under the monotone guard obj_micro ≥ obj_contact − OBJ_TOL and do not increment the accepted-move counter, preserving determinism and the move budget while exploiting fresh active-set geometry that often unlocks an extra 1–3e−3 in objective within the same iteration budget.

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

Implements the algorithm described in the prompt:
- Deterministic hexagonal seeding of 26 centers.
- Fixed-center LP radii solver (maximize sum of radii) via primal simplex with Bland’s rule.
- Two-pass proportional pair repair to remove residual overlaps.
- Iterative center relaxation guided by near-active constraints with monotone acceptance.
- Periodic micro-SLP trust-region steps that jointly optimize radii and small center displacements.

All computations are deterministic: fixed seed pattern, lexicographic iteration orders,
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
ROW_COUNTS = [6, 5, 5, 5, 5]  # sums to 26
# LP Solver parameters
LP_MAX_PIVOTS = 20000  # safety cap on simplex pivots
SIMPLEX_TOL = 1e-12  # tolerance for entering/ratio tests

# Motion field parameters
BASE_W_BOUNDARY = 2.3
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

# Iteration limits
MAX_ACCEPTED_MOVES = 160
# Loosen tiny-improvement streak limit per the new algorithm tweak (from 5 to 7)
SMALL_IMPROVEMENT_STREAK_LIMIT = 7


def construct_packing(num_circles: int = 26) -> Tuple[np.ndarray, np.ndarray]:
    """Return centers and radii for a valid packing of `num_circles` circles.

    Implements:
    1) Deterministic hexagonal seed for centers.
    2) Fixed-center LP radii optimization with Bland’s rule.
    3) Active-set guided center relaxation with monotone acceptance and backtracking.
    4) Periodic micro-SLP trust-region steps to coordinate multi-point moves.
    5) Final LP and two-pass pair repair for clean feasibility.

    Returns:
        centers: array (26, 2)
        radii: array (26,)
    """
    assert num_circles == 26, "This constructor is specialized for 26 circles."

    # Build deterministic hexagonal seed
    centers, dx_seed, dy_seed = hex_seed_centers(num_circles, eps=EPS)

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


def hex_seed_centers(num_circles: int, eps: float = EPS) -> Tuple[np.ndarray, float, float]:
    """Deterministic boundary-aware hexagonal seeding for 26 centers.

    Pattern:
      - Row counts: [6, 5, 5, 5, 5]
      - dx = (1 - 2*eps)/5
      - dy = (sqrt(3)/2) * dx
      - 6-column row aligned at x = eps + c*dx (c=0..5)
      - 5-column rows staggered by +0.5*dx at x = eps + 0.5*dx + c*dx (c=0..4)
      - y rows placed with spacing dy, starting from a deterministic base and clipped to [eps, 1-eps]
    """
    assert num_circles == 26
    centers = np.zeros((num_circles, 2), dtype=float)
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx

    # Vertical placement: center the rows deterministically
    num_rows = len(ROW_COUNTS)
    # total vertical span excluding margins (between first and last row centers)
    span = (num_rows - 1) * dy
    # start y such that rows are centered inside [eps, 1-eps]
    y_start = 0.5 - span / 2.0
    # shift into [eps, 1-eps] deterministically by clipping
    y_start = np.clip(y_start, eps, 1.0 - eps)

    idx = 0
    for r, count in enumerate(ROW_COUNTS):
        y = y_start + r * dy
        if count == 6:
            xs = [eps + c * dx for c in range(6)]
        else:
            xs = [eps + 0.5 * dx + c * dx for c in range(count)]
        for c in range(count):
            centers[idx, 0] = xs[c]
            centers[idx, 1] = y
            idx += 1

    # Clip to [eps, 1-eps] to ensure feasibility
    centers = np.clip(centers, eps, 1.0 - eps)
    return centers, dx, dy


def lp_radii_fixed_centers(centers: np.ndarray) -> Tuple[np.ndarray, float]:
    """Solve for optimal radii with fixed centers via primal simplex with Bland’s rule.

    LP form:
      maximize sum(r_i)
      subject to:
        - boundary constraints: r_i <= x_i, r_i <= y_i, r_i <= 1-x_i, r_i <= 1-y_i
        - pair constraints: r_i + r_j <= ||c_i - c_j||  for all i<j
      variables: r_i >= 0

    Returns:
        radii: array of optimal radii after two-pass pair repair
        obj: sum of radii (objective value)
    """
    n = centers.shape[0]
    # Boundary A, b
    x = centers[:, 0]
    y = centers[:, 1]
    clears = np.stack([x, y, 1.0 - x, 1.0 - y], axis=1)  # shape (n, 4)

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

    # Deterministic two-pass pair repair to remove tiny overlaps
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
                if ratio < min_ratio - 0:  # strict check
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
        # Avoid division by zero: pivot_value > tol by previous check
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
            # Clamp small negatives to zero
            x[var_idx] = xi if xi > 0.0 else 0.0

    obj = T[0, -1]
    # Numerical guard
    x = np.clip(x, 0.0, np.inf)
    return x, float(obj)


def pair_repair(centers: np.ndarray, radii: np.ndarray, passes: int = 2) -> np.ndarray:
    """Deterministic two-pass proportional pair repair to remove residual numerical overlaps.

    For each pair (i<j) in lexicographic order:
        if r_i + r_j > d_ij: scale both radii by factor d_ij / (r_i + r_j).
    Repeat exactly `passes` times.

    Returns:
        repaired radii (nonnegative).
    """
    n = len(radii)
    r = np.array(radii, dtype=float)
    for _ in range(max(1, passes)):
        for i in range(n):
            for j in range(i + 1, n):
                dij = float(np.linalg.norm(centers[i] - centers[j]))
                s = r[i] + r[j]
                if s > dij and s > 0.0:
                    scale = dij / s
                    # Proportional scale
                    r[i] *= scale
                    r[j] *= scale
        # Clamp to non-negative finite values
        r = np.clip(r, 0.0, np.inf)
    return r


def optimize_centers(centers: np.ndarray, radii: np.ndarray, obj: float, dx_seed: float, dy_seed: float, eps: float = EPS) -> Tuple[np.ndarray, np.ndarray]:
    """Iteratively relax centers guided by near-active constraints, with monotone acceptance.

    - Build motion field from active boundary and near-contact pairs.
    - Monotone backtracked line search: accept only non-decreasing LP objectives.
    - Periodic micro-SLP trust-region steps when line search fails or every few accepts.
    - NEW: Immediately after accepting a contact-guided step with tiny improvement,
           attempt a single piggyback micro-SLP step with a slightly smaller trust region
           (h_small = 0.06 * min(dx_seed, dy_seed)) and accept only if non-decreasing.
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
    for _ in range(MAX_ACCEPTED_MOVES * 2):  # upper bound on trials
        # Build motion field vector per center
        v, wsum = build_motion_field(current_centers, current_radii, eps=eps)

        # Normalize vector field and cap magnitudes
        v_norm = normalize_vectors(v, cap=CAP_PER_NODE, wsum=wsum)

        # Backtracking line search
        accepted = False
        alpha = s0
        while alpha >= alpha_min:
            trial_centers = current_centers + alpha * v_norm
            # Clip to [eps, 1-eps]
            trial_centers = np.clip(trial_centers, eps, 1.0 - eps)

            trial_radii, trial_obj = lp_radii_fixed_centers(trial_centers)

            # Accept only non-decreasing objective with tolerance
            if trial_obj >= current_obj - OBJ_TOL:
                # Apply
                # Compute improvement prior to updating current_obj
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

                # Piggyback micro-SLP step on tiny improvement:
                # single attempt with smaller trust region h_small = 0.06 * min(dx_seed, dy_seed)
                if improvement < SMALL_IMPROVEMENT_THRESH:
                    h_small = 0.06 * min(dx_seed, dy_seed)
                    c_micro, r_micro, o_micro = micro_slp_step(current_centers, current_radii, current_obj, h=h_small, eps=eps)
                    # Accept piggyback only if monotone (non-decreasing)
                    if o_micro >= current_obj - OBJ_TOL:
                        # Apply piggyback update
                        current_centers = c_micro
                        current_radii = r_micro
                        current_obj = o_micro
                        # Reset micro cadence counters only on acceptance:
                        # In this implementation, periodic micro cadence is driven by accepts % 2 and tr_h.
                        # We keep 'accepts' unchanged to preserve move budgeting; no cadence counter to reset here.
                        # Trust-region parameters for periodic micro remain unchanged.
                break
            alpha *= RHO

        if accepted:
            # Periodically attempt a micro-SLP trust-region step to coordinate small multi-point moves
            if accepts % 2 == 0:
                c2, r2, o2 = micro_slp_step(current_centers, current_radii, current_obj, h=tr_h, eps=eps)
                if o2 >= current_obj - OBJ_TOL:
                    current_centers = c2
                    current_radii = r2
                    # growth if meaningful improvement
                    if (o2 - current_obj) > 1e-6:
                        tr_h = min(tr_h * TR_GROWTH, 0.12 * min(dx_seed, dy_seed))
                    current_obj = o2
                else:
                    # Backtrack trust region size if rejected
                    tr_h = max(tr_h * TR_BACKTRACK, tr_floor)
        else:
            # No accepted contact move: try micro-SLP escape step
            c2, r2, o2 = micro_slp_step(current_centers, current_radii, current_obj, h=tr_h, eps=eps)
            if o2 >= current_obj - OBJ_TOL:
                current_centers = c2
                current_radii = r2
                current_obj = o2
                accepts += 1
                tiny_improve_streak = 0
                # gentle growth on success
                if (o2 - current_obj) > 1e-6:
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
      - Weight magnitude by BASE_W_BOUNDARY * tightness^1.5, tightness computed via relative slack.

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

    # Boundary contributions
    xs = centers[:, 0]
    ys = centers[:, 1]
    clears = np.stack([xs, ys, 1.0 - xs, 1.0 - ys], axis=1)
    min_clear = np.min(clears, axis=1)
    tau_bnd = np.maximum.reduce([np.full(n, 5e-5), 0.02 * min_clear, np.full(n, 10.0 * TOL)])

    for i in range(n):
        mc = min_clear[i]
        if radii[i] >= mc - tau_bnd[i]:
            # sides that achieve min_clear are active
            # normals for [left, bottom, right, top] inward into the square
            # left wall x_i: inward is +x
            # bottom wall y_i: inward is +y
            # right wall 1-x_i: inward is -x
            # top wall 1-y_i: inward is -y
            tightness = 1.0 - (mc - radii[i]) / max(mc, 1e-9)
            tightness = np.clip(tightness, 0.0, 1.0)
            w = BASE_W_BOUNDARY * (tightness ** 1.5)
            # Add contributions for the minimum sides (can be multiple)
            for sidx, val in enumerate(clears[i]):
                if abs(val - mc) <= 1e-12:
                    if sidx == 0:  # x_i
                        v[i, 0] += +w
                    elif sidx == 1:  # y_i
                        v[i, 1] += +w
                    elif sidx == 2:  # 1-x_i
                        v[i, 0] += -w
                    else:  # 1-y_i
                        v[i, 1] += -w
                    wsum[i] += w

    # Pair contributions
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
                # Degenerate pair: push apart along a fixed axis deterministically
                nvec = np.array([1.0, 0.0], dtype=float)
            else:
                nvec = d_vec / d
            slack = d - (ri + rj)
            if slack <= tol_pair:
                w = min(W_MAX, 1.0 / (slack + SIGMA)) * (1.0 / max(d, DELTA))
                # push i opposite to nvec, j along nvec
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
            # If no motion, use zero vector
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
            # j contributions with negative signs for u+_j and v+_j (moving away along +n increases distance)
            A[row, n + j] += -nx
            A[row, 2 * n + j] += +nx
            A[row, 3 * n + j] += -ny
            A[row, 4 * n + j] += +ny
            # i contributions with positive signs for u+_i and v+_i (moving opposite decreases)
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

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

Implements the algorithm described in the prompt:
- Deterministic hexagonal seeding of 26 centers.
- Fixed-center LP radii solver (maximize sum of radii) via primal simplex with Bland’s rule.
- Two-pass proportional pair repair to remove residual overlaps.
- Iterative center relaxation guided by near-active constraints with monotone acceptance.
- Periodic micro-SLP trust-region steps that jointly optimize radii and small center displacements.
- Piggyback micro-SLP triggered on small improvement or heavy backtracking, with trust region
  scaled by the accepted step ratio for coordinated, conservative nudges.

All computations are deterministic: fixed seed pattern, lexicographic iteration orders,
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
ROW_COUNTS = [6, 5, 5, 5, 5]  # sums to 26
# LP Solver parameters
LP_MAX_PIVOTS = 20000  # safety cap on simplex pivots
SIMPLEX_TOL = 1e-12  # tolerance for entering/ratio tests

# Motion field parameters
BASE_W_BOUNDARY = 2.3
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

# Iteration limits
MAX_ACCEPTED_MOVES = 160
SMALL_IMPROVEMENT_STREAK_LIMIT = 5


def construct_packing(num_circles: int = 26) -> Tuple[np.ndarray, np.ndarray]:
    """Return centers and radii for a valid packing of `num_circles` circles.

    Implements:
    1) Deterministic hexagonal seed for centers.
    2) Fixed-center LP radii optimization with Bland’s rule.
    3) Active-set guided center relaxation with monotone acceptance and backtracking.
    4) Periodic micro-SLP trust-region steps to coordinate multi-point moves.
    5) Piggyback micro-SLP triggered when improvement is tiny or heavy backtracking occurred.
    6) Final LP and two-pass pair repair for clean feasibility.

    Returns:
        centers: array (26, 2)
        radii: array (26,)
    """
    assert num_circles == 26, "This constructor is specialized for 26 circles."

    # Build deterministic hexagonal seed
    centers, dx_seed, dy_seed = hex_seed_centers(num_circles, eps=EPS)

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


def hex_seed_centers(num_circles: int, eps: float = EPS) -> Tuple[np.ndarray, float, float]:
    """Deterministic boundary-aware hexagonal seeding for 26 centers.

    Pattern:
      - Row counts: [6, 5, 5, 5, 5]
      - dx = (1 - 2*eps)/5
      - dy = (sqrt(3)/2) * dx
      - 6-column row aligned at x = eps + c*dx (c=0..5)
      - 5-column rows staggered by +0.5*dx at x = eps + 0.5*dx + c*dx (c=0..4)
      - y rows placed with spacing dy, starting from a deterministic base and clipped to [eps, 1-eps]
    """
    assert num_circles == 26
    centers = np.zeros((num_circles, 2), dtype=float)
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx

    # Vertical placement: center the rows deterministically
    num_rows = len(ROW_COUNTS)
    # total vertical span excluding margins (between first and last row centers)
    span = (num_rows - 1) * dy
    # start y such that rows are centered inside [eps, 1-eps]
    y_start = 0.5 - span / 2.0
    # shift into [eps, 1-eps] deterministically by clipping
    y_start = np.clip(y_start, eps, 1.0 - eps)

    idx = 0
    for r, count in enumerate(ROW_COUNTS):
        y = y_start + r * dy
        if count == 6:
            xs = [eps + c * dx for c in range(6)]
        else:
            xs = [eps + 0.5 * dx + c * dx for c in range(count)]
        for c in range(count):
            centers[idx, 0] = xs[c]
            centers[idx, 1] = y
            idx += 1

    # Clip to [eps, 1-eps] to ensure feasibility
    centers = np.clip(centers, eps, 1.0 - eps)
    return centers, dx, dy


def lp_radii_fixed_centers(centers: np.ndarray) -> Tuple[np.ndarray, float]:
    """Solve for optimal radii with fixed centers via primal simplex with Bland’s rule.

    LP form:
      maximize sum(r_i)
      subject to:
        - boundary constraints: r_i <= x_i, r_i <= y_i, r_i <= 1-x_i, r_i <= 1-y_i
        - pair constraints: r_i + r_j <= ||c_i - c_j||  for all i<j
      variables: r_i >= 0

    Returns:
        radii: array of optimal radii after two-pass pair repair
        obj: sum of radii (objective value)
    """
    n = centers.shape[0]
    # Boundary A, b
    x = centers[:, 0]
    y = centers[:, 1]
    clears = np.stack([x, y, 1.0 - x, 1.0 - y], axis=1)  # shape (n, 4)

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

    # Deterministic two-pass pair repair to remove tiny overlaps
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
                if ratio < min_ratio - 0:  # strict check
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
        # Avoid division by zero: pivot_value > tol by previous check
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
            # Clamp small negatives to zero
            x[var_idx] = xi if xi > 0.0 else 0.0

    obj = T[0, -1]
    # Numerical guard
    x = np.clip(x, 0.0, np.inf)
    return x, float(obj)


def pair_repair(centers: np.ndarray, radii: np.ndarray, passes: int = 2) -> np.ndarray:
    """Deterministic two-pass proportional pair repair to remove residual numerical overlaps.

    For each pair (i<j) in lexicographic order:
        if r_i + r_j > d_ij: scale both radii by factor d_ij / (r_i + r_j).
    Repeat exactly `passes` times.

    Returns:
        repaired radii (nonnegative).
    """
    n = len(radii)
    r = np.array(radii, dtype=float)
    for _ in range(max(1, passes)):
        for i in range(n):
            for j in range(i + 1, n):
                dij = float(np.linalg.norm(centers[i] - centers[j]))
                s = r[i] + r[j]
                if s > dij and s > 0.0:
                    scale = dij / s
                    # Proportional scale
                    r[i] *= scale
                    r[j] *= scale
        # Clamp to non-negative finite values
        r = np.clip(r, 0.0, np.inf)
    return r


def optimize_centers(centers: np.ndarray, radii: np.ndarray, obj: float, dx_seed: float, dy_seed: float, eps: float = EPS) -> Tuple[np.ndarray, np.ndarray]:
    """Iteratively relax centers guided by near-active constraints, with monotone acceptance.

    - Build motion field from active boundary and near-contact pairs.
    - Monotone backtracked line search: accept only non-decreasing LP objectives.
    - Piggyback micro-SLP step after accepted contact move when improvement is tiny or
      when heavy backtracking occurred (alpha_ratio < 0.5). Trust-region side scaled by
      accepted step ratio: h_small = 0.06 * clamp(alpha_ratio, 0.5, 1.0) * min(dx_seed, dy_seed).
    - Periodic micro-SLP trust-region steps when line search fails or every few accepts.
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
    for _ in range(MAX_ACCEPTED_MOVES * 2):  # upper bound on trials
        # Build motion field vector per center
        v, wsum = build_motion_field(current_centers, current_radii, eps=eps)

        # Normalize vector field and cap magnitudes
        v_norm = normalize_vectors(v, cap=CAP_PER_NODE, wsum=wsum)

        # Backtracking line search
        accepted = False
        alpha = s0
        while alpha >= alpha_min:
            trial_centers = current_centers + alpha * v_norm
            # Clip to [eps, 1-eps]
            trial_centers = np.clip(trial_centers, eps, 1.0 - eps)

            trial_radii, trial_obj = lp_radii_fixed_centers(trial_centers)

            # Accept only non-decreasing objective with tolerance
            if trial_obj >= current_obj - OBJ_TOL:
                # Determine improvement size and accepted step ratio before updating s0
                improvement = trial_obj - current_obj
                accepted_alpha = alpha
                alpha_ratio = 0.0
                if s0 > 0.0:
                    alpha_ratio = accepted_alpha / s0

                # Apply contact-guided move
                current_centers = trial_centers
                current_radii = trial_radii
                current_obj = trial_obj
                accepted = True
                accepts += 1

                # Tiny improvement streak tracking
                if improvement < SMALL_IMPROVEMENT_THRESH:
                    tiny_improve_streak += 1
                else:
                    tiny_improve_streak = 0

                # Piggyback micro-SLP trigger:
                # - When improvement is tiny OR when heavy backtracking occurred (alpha_ratio < 0.5)
                # Trust-region scaled by accepted step ratio with clamp in [0.5, 1.0]
                if (improvement < SMALL_IMPROVEMENT_THRESH) or (alpha_ratio < 0.5):
                    scale_ratio = min(max(alpha_ratio, 0.5), 1.0)
                    h_small = 0.06 * scale_ratio * min(dx_seed, dy_seed)
                    c2, r2, o2 = micro_slp_step(current_centers, current_radii, current_obj, h=h_small, eps=eps)
                    # Monotone guard relative to contact-accepted objective
                    if o2 >= current_obj - OBJ_TOL:
                        current_centers = c2
                        current_radii = r2
                        current_obj = o2
                        # NOTE: do not increment 'accepts' on piggyback acceptance
                        # to preserve move budget and cadence.

                # Slightly reduce s0 if we had consecutive tiny improvements to quell micro-oscillations
                if tiny_improve_streak >= 3:
                    s0 *= 0.8
                    tiny_improve_streak = 0
                break
            alpha *= RHO

        if accepted:
            # Periodically attempt a micro-SLP trust-region step to coordinate small multi-point moves
            if accepts % 2 == 0:
                c2, r2, o2 = micro_slp_step(current_centers, current_radii, current_obj, h=tr_h, eps=eps)
                if o2 >= current_obj - OBJ_TOL:
                    current_centers = c2
                    current_radii = r2
                    # growth if meaningful improvement
                    if (o2 - current_obj) > 1e-6:
                        tr_h = min(tr_h * TR_GROWTH, 0.12 * min(dx_seed, dy_seed))
                    current_obj = o2
                else:
                    # Backtrack trust region size if rejected
                    tr_h = max(tr_h * TR_BACKTRACK, tr_floor)
        else:
            # No accepted contact move: try micro-SLP escape step
            c2, r2, o2 = micro_slp_step(current_centers, current_radii, current_obj, h=tr_h, eps=eps)
            if o2 >= current_obj - OBJ_TOL:
                current_centers = c2
                current_radii = r2
                current_obj = o2
                accepts += 1
                tiny_improve_streak = 0
                # gentle growth on success
                if (o2 - current_obj) > 1e-6:
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
      - Weight magnitude by BASE_W_BOUNDARY * tightness^1.5, tightness computed via relative slack.

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

    # Boundary contributions
    xs = centers[:, 0]
    ys = centers[:, 1]
    clears = np.stack([xs, ys, 1.0 - xs, 1.0 - ys], axis=1)
    min_clear = np.min(clears, axis=1)
    tau_bnd = np.maximum.reduce([np.full(n, 5e-5), 0.02 * min_clear, np.full(n, 10.0 * TOL)])

    for i in range(n):
        mc = min_clear[i]
        if radii[i] >= mc - tau_bnd[i]:
            # sides that achieve min_clear are active
            # normals for [left, bottom, right, top] inward into the square
            # left wall x_i: inward is +x
            # bottom wall y_i: inward is +y
            # right wall 1-x_i: inward is -x
            # top wall 1-y_i: inward is -y
            tightness = 1.0 - (mc - radii[i]) / max(mc, 1e-9)
            tightness = np.clip(tightness, 0.0, 1.0)
            w = BASE_W_BOUNDARY * (tightness ** 1.5)
            # Add contributions for the minimum sides (can be multiple)
            for sidx, val in enumerate(clears[i]):
                if abs(val - mc) <= 1e-12:
                    if sidx == 0:  # x_i
                        v[i, 0] += +w
                    elif sidx == 1:  # y_i
                        v[i, 1] += +w
                    elif sidx == 2:  # 1-x_i
                        v[i, 0] += -w
                    else:  # 1-y_i
                        v[i, 1] += -w
                    wsum[i] += w

    # Pair contributions
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
                # Degenerate pair: push apart along a fixed axis deterministically
                nvec = np.array([1.0, 0.0], dtype=float)
            else:
                nvec = d_vec / d
            slack = d - (ri + rj)
            if slack <= tol_pair:
                w = min(W_MAX, 1.0 / (slack + SIGMA)) * (1.0 / max(d, DELTA))
                # push i opposite to nvec, j along nvec
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
            # If no motion, use zero vector
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
            # j contributions with negative signs for u+_j and v+_j (moving away along +n increases distance)
            A[row, n + j] += -nx
            A[row, 2 * n + j] += +nx
            A[row, 3 * n + j] += -ny
            A[row, 4 * n + j] += +ny
            # i contributions with positive signs for u+_i and v+_i (moving opposite decreases)
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
