Actionable pattern from a well-performing run that tuned micro-SLP cadence, trust-region scale, backtracking aggressiveness, and patience while preserving determinism and monotone acceptance.

- Earlier micro-SLP with wider trust region and longer small-gain patience: Increasing micro-SLP cadence (MICRO_SLP_K: 3→2) and slightly enlarging the trust region with gentler backtracking (MICRO_SLP_H_INIT_SCALE: 0.05→0.08, MICRO_SLP_BACKTRACK: 0.7→0.75), while extending small-improvement patience (SMALL_IMPROV_STREAK_MAX: 3→5), preserved the monotone objective guarantee (obj_trial ≥ obj_curr − OBJ_TOL) and determinism, and yielded a higher final sum of radii (2.6310935266815343, validity 1.0) than its parent (2.6273226183762586); reuse this when local linearization is good by alternating contact nudges with frequent guarded micro-SLP steps, starting with a slightly bolder h and milder backtracking, and allowing longer small-gain streaks to compound progress without early termination.

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

Implements:
- Deterministic edge-fitted hexagonal seeding for 26 centers.
- Exact LP for maximum-sum radii at fixed centers via primal simplex (Bland's rule).
- Iterative active/near-active contact-guided center relaxation with monotone backtracking.
- Optional micro-SLP trust-region linearized step (deterministic, guarded acceptance).
"""

import json
from typing import List, Optional, Tuple

import numpy as np


# EVOLVE_START
# ---------------------------
# Deterministic configuration
# ---------------------------
HEX_EPS = 1e-3  # interior margin for seeding and clipping
TOL = 1e-10     # general numerical tolerance for activeness
OBJ_TOL = 1e-12 # monotone acceptance tolerance
PAIR_TOL_MIN = 1e-6  # minimum tolerance for near-active pairs
W_MAX = 1e6     # cap for inverse-slack weights
SIGMA = 1e-9    # small stabilizer for inverse slack
DELTA = 1e-9    # small stabilizer for distances
BASE_W_BOUNDARY = 2.0  # base weight for boundary inward normal contributions
CAP_PER_NODE = 3.0     # cap on accumulated magnitude per node before normalization

# Backtracking parameters
RHO = 0.95
ALPHA_MIN_SCALE = 1e-6

# Acceptance limits
MAX_ACCEPTS = 120
SMALL_IMPROV_THRESH = 1e-10
# Mutation: allow a longer streak of small but positive improvements before stopping
SMALL_IMPROV_STREAK_MAX = 5

# Micro-SLP parameters
# Mutation: trigger micro-SLP more frequently (every 2 accepted moves)
MICRO_SLP_K = 2
# Mutation: modestly enlarge initial trust-region side and soften backtracking
MICRO_SLP_H_INIT_SCALE = 0.08  # initial trust-region side (fraction of min(dx,dy))
MICRO_SLP_BACKTRACK = 0.75
MICRO_SLP_MIN_H_SCALE = 1e-4

# Deterministic step-size modulation for contact moves
CONTACT_SMALL_IMPROV_THRESH = 1e-8  # threshold for "small" improvement
CONTACT_QUEUE_LEN = 3               # consecutive contact moves to trigger s0 reduction
S0_REDUCTION_FACTOR = 0.8           # one-time reduction multiplier


def hex_seed_26() -> Tuple[np.ndarray, float, float]:
    """Create a deterministic hexagonal-like seed of 26 centers inside [0,1]^2.

    Mutation: edge-fitted hex seed layout switched to rows [6, 5, 5, 5, 5].
    - dx = (1 - 2*eps)/5
    - dy = (sqrt(3)/2)*dx
    - y rows at yk = (0.5 - 2*dy) + k*dy for k=0..4
    - 6-column row aligned with x columns at xs = eps + c*dx (no 0.5*dx offset)
    - 5-column rows are staggered by +0.5*dx offset

    Returns:
        centers: (26,2) array
        dx, dy: spacings used (useful for step sizing)
    """
    eps = HEX_EPS
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx

    rows = [6, 5, 5, 5, 5]
    y0 = (0.5 - 2.0 * dy)  # k=0 row base y
    centers: List[List[float]] = []
    for k, cnt in enumerate(rows):
        yk = y0 + k * dy
        if cnt == 6:
            # 6 centers, aligned to columns at eps + c*dx
            xs = [eps + c * dx for c in range(cnt)]
        else:
            # 5 centers, staggered by 0.5*dx relative to aligned columns
            xs = [eps + 0.5 * dx + c * dx for c in range(cnt)]
        for x in xs:
            centers.append([x, yk])
    centers_arr = np.array(centers, dtype=float)
    # Clip to ensure interior points and numeric stability
    np.clip(centers_arr, eps, 1.0 - eps, out=centers_arr)
    return centers_arr, dx, dy


# -------------------------
# LP Solver (Primal Simplex)
# -------------------------
class SimplexResult:
    def __init__(self, x: np.ndarray, basis: List[int], status: str):
        self.x = x
        self.basis = basis
        self.status = status


def primal_simplex_blands(A: np.ndarray, b: np.ndarray, c: np.ndarray, tol: float = 1e-12,
                          max_iter: int = 100000) -> SimplexResult:
    """Primal simplex for maximize c^T x subject to A x <= b, x >= 0.

    Deterministic (Bland's rule): pick smallest-index entering var with positive reduced cost,
    leaving via minimum ratio with tie-break by smallest basic var index.

    Args:
        A: (m, n) inequality matrix
        b: (m,) RHS, must be >= 0
        c: (n,) objective coefficients
        tol: numerical tolerance for positivity and comparisons
        max_iter: limit on pivot iterations

    Returns:
        SimplexResult: x solution (n,), basis indices (over total variables n+m), status string
    """
    m, n = A.shape
    # Build tableau with slack variables s >= 0: A x + I s = b
    # Columns: [x (n), s (m)], RHS last column.
    total_vars = n + m
    # Initialize tableau
    T = np.zeros((m + 1, total_vars + 1), dtype=float)
    # Constraint rows
    T[:m, :n] = A
    T[:m, n:n + m] = np.eye(m)
    T[:m, -1] = b
    # Objective row: reduced costs initialized to c for non-basic vars (x columns); slack reduced costs 0
    T[m, :n] = c
    T[m, n:n + m] = 0.0
    T[m, -1] = 0.0

    # Basic variable indices per row, initial slack basis
    basis = [n + i for i in range(m)]

    # Validate b >= 0
    if np.any(b < -tol):
        # Infeasible (should not happen for our construction)
        return SimplexResult(np.zeros(n), basis, status="infeasible_b")

    def pivot(pivot_row: int, pivot_col: int):
        """Perform a pivot at (pivot_row, pivot_col)."""
        piv = T[pivot_row, pivot_col]
        if abs(piv) <= tol:
            return False
        # Normalize pivot row
        T[pivot_row, :] = T[pivot_row, :] / piv
        # Eliminate pivot column from other rows, including objective
        for r in range(m + 1):
            if r == pivot_row:
                continue
            factor = T[r, pivot_col]
            if factor != 0.0:
                T[r, :] = T[r, :] - factor * T[pivot_row, :]
        # Update basis
        basis[pivot_row] = pivot_col
        return True

    # Simplex iterations
    iters = 0
    while iters < max_iter:
        iters += 1
        # Determine entering variable using Bland's rule: smallest j with positive reduced cost > tol
        entering = None
        # Reduced costs are T[m, j]; positive means improvement
        for j in range(total_vars):
            if T[m, j] > tol:
                entering = j
                break
        if entering is None:
            # Optimal (within tolerance)
            break

        # Determine leaving variable: candidates with positive column coefficient
        col = T[:m, entering]
        # Feasibility: col_i > tol to consider
        min_ratio = None
        leave_row = None
        # Bland's tie-breaking: smallest basic variable index
        for i in range(m):
            a_ij = col[i]
            if a_ij > tol:
                ratio = T[i, -1] / a_ij
                if ratio < -tol:
                    # Negative RHS shouldn't happen
                    ratio = np.inf
                if min_ratio is None or ratio < min_ratio - tol or (abs(ratio - (min_ratio or 0.0)) <= tol and basis[i] < basis[leave_row]):  # type: ignore
                    min_ratio = ratio
                    leave_row = i
        if leave_row is None:
            # Unbounded (should not happen for our packing LP)
            break

        # Perform pivot
        if not pivot(leave_row, entering):
            # Degenerate pivot -> stop to avoid looping
            break

    # Extract solution for original variables x (n)
    x = np.zeros(n, dtype=float)
    for i in range(m):
        bi = basis[i]
        if bi < n:
            val = T[i, -1]
            if val < 0 and abs(val) <= 1e-9:
                val = 0.0
            x[bi] = val
    # Clamp small negatives to 0
    x = np.where(x < 0.0, 0.0, x)
    return SimplexResult(x, basis, status="optimal")


def compute_max_radii_lp(centers: np.ndarray) -> Tuple[np.ndarray, float, List[int]]:
    """Solve the LP for maximum sum of radii with fixed centers.

    Constraints:
        r_i >= 0 (implicit via LP nonnegativity)
        r_i <= x_i, r_i <= y_i, r_i <= 1-x_i, r_i <= 1-y_i
        r_i + r_j <= ||c_i - c_j|| for all i<j
    Returns:
        radii (N,), objective sum(r), basis indices from simplex (for potential warm-start)
    """
    N = centers.shape[0]
    # Boundaries
    x = centers[:, 0]
    y = centers[:, 1]
    # Build A, b
    # Boundary constraints: 4N
    rows: List[List[float]] = []
    b_list: List[float] = []
    # For each i, r_i <= x_i -> e_i^T r <= x_i
    for i in range(N):
        row = [0.0] * N
        row[i] = 1.0
        rows.append(row)
        b_list.append(float(x[i]))
    # r_i <= y_i
    for i in range(N):
        row = [0.0] * N
        row[i] = 1.0
        rows.append(row)
        b_list.append(float(y[i]))
    # r_i <= 1 - x_i
    for i in range(N):
        row = [0.0] * N
        row[i] = 1.0
        rows.append(row)
        b_list.append(float(1.0 - x[i]))
    # r_i <= 1 - y_i
    for i in range(N):
        row = [0.0] * N
        row[i] = 1.0
        rows.append(row)
        b_list.append(float(1.0 - y[i]))
    # Pair constraints: r_i + r_j <= d_ij
    for i in range(N):
        for j in range(i + 1, N):
            row = [0.0] * N
            row[i] = 1.0
            row[j] = 1.0
            d = float(np.linalg.norm(centers[i] - centers[j]))
            rows.append(row)
            b_list.append(d)

    A = np.array(rows, dtype=float)
    b = np.array(b_list, dtype=float)
    c = np.ones(N, dtype=float)
    res = primal_simplex_blands(A, b, c, tol=1e-12)
    radii = res.x
    # Clamp to non-negative finite
    radii = np.where(np.isfinite(radii) & (radii >= 0.0), radii, 0.0)
    return radii, float(np.sum(radii)), res.basis


def _clamp01(x: float) -> float:
    """Clamp value to [0,1]."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def build_active_motion(centers: np.ndarray, radii: np.ndarray) -> np.ndarray:
    """Construct a motion field that nudges centers away from active/near-active constraints.

    Mutation:
      - Boundary contributions: tightness-weighted inward pushes only for sides achieving the
        minimum clearance min{x, y, 1-x, 1-y}, active if r_i >= min_clear - TOL.
      - Accumulate scalar weight sums per node (pair weights + boundary weights) and
        weight-normalize v_i by (1 + wsum_i) before unit normalization.
      - Keep inverse-slack pair weighting and CAP_PER_NODE safety cap.
    Returns:
        v: (N,2) normalized motion directions per center (unit norm or zero)
    """
    N = centers.shape[0]
    x = centers[:, 0]
    y = centers[:, 1]
    v = np.zeros_like(centers)
    wsum = np.zeros(N, dtype=float)

    # Helper to add a weighted vector to node i with cap and accumulate weight
    def add_contrib(i: int, delta: np.ndarray, weight: float) -> None:
        vi = v[i] + delta
        # Cap per-node accumulation magnitude
        norm_vi = float(np.linalg.norm(vi))
        if norm_vi > CAP_PER_NODE:
            vi = vi / norm_vi * CAP_PER_NODE
        v[i] = vi
        wsum[i] += float(weight)

    # Boundary tightness-weighted inward pushes (only at tightest sides)
    for i in range(N):
        clears = np.array([x[i], y[i], 1.0 - x[i], 1.0 - y[i]], dtype=float)
        min_clear = float(np.min(clears))
        if radii[i] >= min_clear - TOL:
            # tightness in [0,1]: higher when r is closer to min_clear
            denom = max(min_clear, 1e-9)
            tightness = _clamp01(1.0 - (min_clear - float(radii[i])) / denom)
            w_bound = BASE_W_BOUNDARY * tightness
            if w_bound > 0.0:
                # For each side achieving the minimum, add inward normal
                # Use numerical tolerance TOL for tie detection
                for side, val in enumerate(clears):
                    if abs(val - min_clear) <= TOL:
                        if side == 0:  # left: inward +x
                            add_contrib(i, np.array([w_bound, 0.0], dtype=float), w_bound)
                        elif side == 1:  # bottom: inward +y
                            add_contrib(i, np.array([0.0, w_bound], dtype=float), w_bound)
                        elif side == 2:  # right: inward -x
                            add_contrib(i, np.array([-w_bound, 0.0], dtype=float), w_bound)
                        else:  # top: inward -y
                            add_contrib(i, np.array([0.0, -w_bound], dtype=float), w_bound)

    # Pairwise near-active constraints (inverse-slack with stabilizers)
    tol_pair = max(TOL, PAIR_TOL_MIN)
    for i in range(N):
        for j in range(i + 1, N):
            # Slack s_ij = d_ij - (r_i + r_j)
            dij_vec = centers[i] - centers[j]
            dij = float(np.linalg.norm(dij_vec))
            if dij <= DELTA:
                # Avoid degenerate direction; skip since centers coincide (shouldn't happen)
                continue
            s = dij - float(radii[i] + radii[j])
            if s <= tol_pair:
                n_ij = dij_vec / dij
                # Weight: inverse slack with stabilizers; scale by inverse distance
                w = min(W_MAX, 1.0 / (s + SIGMA)) * (1.0 / max(dij, DELTA))
                # Add contributions with capped accumulation; accumulate weight sums
                add_contrib(i, w * n_ij, w)
                add_contrib(j, -w * n_ij, w)

    # Weight-sum normalization followed by unit normalization per node
    for i in range(N):
        # Normalize by (1 + total weight) to reduce hub dominance
        scale = 1.0 + wsum[i]
        if scale > 0.0:
            v[i] /= scale
        norm = float(np.linalg.norm(v[i]))
        if norm > 0.0:
            v[i] /= norm
    return v


def micro_slp_step(centers: np.ndarray, radii: np.ndarray, base_dx: float, base_dy: float) -> Tuple[np.ndarray, float]:
    """Run a small trust-region micro-SLP step that linearizes constraints.

    Variables: r (N), dx+ (N), dx- (N), dy+ (N), dy- (N). All >= 0.
    Trust region: dx+ <= h, dx- <= h, dy+ <= h, dy- <= h => |Δ| <= h.

    Returns:
        centers_new, sum_radii_new if accepted improvement; else return original.
    """
    N = centers.shape[0]
    x = centers[:, 0]
    y = centers[:, 1]

    # Precompute pair distances and unit normals
    idx_pairs = []
    dists = []
    normals = []
    for i in range(N):
        for j in range(i + 1, N):
            dij_vec = centers[i] - centers[j]
            dij = float(np.linalg.norm(dij_vec))
            if dij <= DELTA:
                n_ij = np.array([1.0, 0.0], dtype=float)
            else:
                n_ij = dij_vec / dij
            idx_pairs.append((i, j))
            dists.append(dij)
            normals.append(n_ij)
    dists = np.array(dists, dtype=float)
    normals = np.array(normals, dtype=float)

    # Build linearized LP
    # Variable ordering: [r (N), dxp(N), dxm(N), dyp(N), dym(N)]
    def solve_micro_lp(h: float) -> Tuple[np.ndarray, float]:
        nvar = 5 * N
        rows: List[List[float]] = []
        b_list: List[float] = []

        # Boundary constraints
        # r_i - dxp_i + dxm_i <= x_i
        for i in range(N):
            row = [0.0] * nvar
            row[i] = 1.0  # r_i
            row[N + i] = -1.0  # dxp_i
            row[N + N + i] = 1.0  # dxm_i
            rows.append(row)
            b_list.append(float(x[i]))
        # r_i - dyp_i + dym_i <= y_i
        for i in range(N):
            row = [0.0] * nvar
            row[i] = 1.0
            row[N + 2 * N + i] = -1.0  # dyp_i
            row[N + 3 * N + i] = 1.0   # dym_i
            rows.append(row)
            b_list.append(float(y[i]))
        # r_i + dxp_i - dxm_i <= 1 - x_i
        for i in range(N):
            row = [0.0] * nvar
            row[i] = 1.0
            row[N + i] = 1.0
            row[N + N + i] = -1.0
            rows.append(row)
            b_list.append(float(1.0 - x[i]))
        # r_i + dyp_i - dym_i <= 1 - y_i
        for i in range(N):
            row = [0.0] * nvar
            row[i] = 1.0
            row[N + 2 * N + i] = 1.0
            row[N + 3 * N + i] = -1.0
            rows.append(row)
            b_list.append(float(1.0 - y[i]))
        # Trust-region bounds: dxp_i <= h; dxm_i <= h; dyp_i <= h; dym_i <= h
        for i in range(N):
            # dxp_i <= h
            row = [0.0] * nvar
            row[N + i] = 1.0
            rows.append(row)
            b_list.append(h)
            # dxm_i <= h
            row = [0.0] * nvar
            row[N + N + i] = 1.0
            rows.append(row)
            b_list.append(h)
            # dyp_i <= h
            row = [0.0] * nvar
            row[N + 2 * N + i] = 1.0
            rows.append(row)
            b_list.append(h)
            # dym_i <= h
            row = [0.0] * nvar
            row[N + 3 * N + i] = 1.0
            rows.append(row)
            b_list.append(h)
        # Pair constraints linearized:
        # r_i + r_j <= d_ij + n_x*(dx_i - dx_j) + n_y*(dy_i - dy_j)
        # dx_i = dxp_i - dxm_i; dy_i = dyp_i - dym_i
        for idx, (i, j) in enumerate(idx_pairs):
            nvec = normals[idx]
            nx = float(nvec[0])
            ny = float(nvec[1])
            row = [0.0] * nvar
            row[i] = 1.0
            row[j] = 1.0
            # i contributions
            row[N + i] += -nx        # dxp_i with -nx
            row[N + N + i] += nx     # dxm_i with +nx
            row[N + 2 * N + i] += -ny   # dyp_i with -ny
            row[N + 3 * N + i] += ny    # dym_i with +ny
            # j contributions (note opposite sign)
            row[N + j] += nx
            row[N + N + j] += -nx
            row[N + 2 * N + j] += ny
            row[N + 3 * N + j] += -ny
            rows.append(row)
            b_list.append(dists[idx])

        A = np.array(rows, dtype=float)
        b_vec = np.array(b_list, dtype=float)
        c_vec = np.zeros(nvar, dtype=float)
        c_vec[:N] = 1.0  # maximize sum r
        res = primal_simplex_blands(A, b_vec, c_vec, tol=1e-12)
        xsol = res.x
        # Reconstruct Δx, Δy and radii objective
        dxp = xsol[N:N + N]
        dxm = xsol[N + N:N + 2 * N]
        dyp = xsol[N + 2 * N:N + 3 * N]
        dym = xsol[N + 3 * N:N + 4 * N]
        dx = dxp - dxm
        dy = dyp - dym
        return np.stack([dx, dy], axis=1), float(np.sum(xsol[:N]))

    # Trust region loop
    base_scale = min(base_dx, base_dy)
    h = MICRO_SLP_H_INIT_SCALE * base_scale
    h_min = MICRO_SLP_MIN_H_SCALE * base_scale

    # Current objective
    _, obj_curr, _ = compute_max_radii_lp(centers)

    while h >= h_min:
        delta_c, pred_obj = solve_micro_lp(h)
        trial_centers = centers + delta_c
        np.clip(trial_centers, HEX_EPS, 1.0 - HEX_EPS, out=trial_centers)
        radii_trial, obj_trial, _ = compute_max_radii_lp(trial_centers)
        if obj_trial >= obj_curr - OBJ_TOL:
            return trial_centers, obj_trial
        # else backtrack
        h *= MICRO_SLP_BACKTRACK

    # If not accepted, return unchanged
    return centers, obj_curr


def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    Pipeline:
      1) Deterministic hexagonal seeding (26 centers).
      2) Fixed-center radii via deterministic LP (primal simplex with Bland's rule).
      3) Active/near-active contact-guided relaxation with monotone backtracking.
      4) Periodic micro-SLP trust-region step (optional) for coordinated moves.

    Mutations integrated:
      - Edge-fitted hex seed layout: [6, 5, 5, 5, 5] with dy=(sqrt(3)/2)*dx; 6-row aligned, 5-rows staggered.
      - Tightness-weighted boundary pushes and weight-sum normalization in active motion.
      - Deterministic one-time step-size modulation: after three consecutive small-contact accepts, s0 <- 0.8*s0.
      - Increased micro-SLP cadence (K=2), enlarged initial trust region (h_scale=0.08), gentler backtracking (0.75),
        and extended small-improvement streak limit (5).
    """
    if num_circles != 26:
        # Generalization not implemented: fall back to simple grid-like seed
        centers, dx, dy = hex_seed_26()
        if centers.shape[0] > num_circles:
            centers = centers[:num_circles]
        else:
            # Pad with small jitter-free grid near center
            extra = num_circles - centers.shape[0]
            base = np.array([0.5, 0.5], dtype=float)
            adds = [base + np.array([dx * ((k % 3) - 1) * 0.1, dy * ((k // 3) - 1) * 0.1]) for k in range(extra)]
            centers = np.vstack([centers, np.array(adds)])
        np.clip(centers, HEX_EPS, 1.0 - HEX_EPS, out=centers)
    else:
        centers, dx, dy = hex_seed_26()

    # Initial LP radii
    radii, obj, _ = compute_max_radii_lp(centers)

    # Active-set relaxation loop
    # Step size parameters
    s0 = 0.1 * min(dx, dy)
    alpha_min = ALPHA_MIN_SCALE * s0

    accepts = 0
    small_improv_streak = 0
    last_improv_obj = obj
    since_micro = 0

    # Contact improvement tracking for deterministic step-size modulation
    contact_improv_queue: List[float] = []
    s0_reduced_once = False

    # Deterministic loop
    while accepts < MAX_ACCEPTS and small_improv_streak < SMALL_IMPROV_STREAK_MAX:
        # Build motion
        v = build_active_motion(centers, radii)
        # If all zeros, break
        if not np.any(np.linalg.norm(v, axis=1) > 0.0):
            break

        # Backtracking line search for contact-guided motion
        alpha = s0
        accepted = False
        while alpha >= alpha_min:
            trial_centers = centers + alpha * v
            np.clip(trial_centers, HEX_EPS, 1.0 - HEX_EPS, out=trial_centers)
            radii_trial, obj_trial, _ = compute_max_radii_lp(trial_centers)
            if obj_trial >= obj - OBJ_TOL:
                # Accept contact move
                improv = obj_trial - obj
                centers = trial_centers
                radii = radii_trial
                accepts += 1
                since_micro += 1
                # Track small improvement streak (legacy)
                if improv < SMALL_IMPROV_THRESH:
                    small_improv_streak += 1
                else:
                    small_improv_streak = 0
                # Track contact-move improvements for s0 modulation
                contact_improv_queue.append(improv)
                if len(contact_improv_queue) > CONTACT_QUEUE_LEN:
                    contact_improv_queue.pop(0)
                if (not s0_reduced_once and len(contact_improv_queue) == CONTACT_QUEUE_LEN and
                        all(im < CONTACT_SMALL_IMPROV_THRESH for im in contact_improv_queue)):
                    # One-time deterministic reduction of base contact step
                    s0 *= S0_REDUCTION_FACTOR
                    alpha_min = ALPHA_MIN_SCALE * s0  # keep consistent
                    s0_reduced_once = True

                obj = obj_trial
                last_improv_obj = obj
                accepted = True
                break
            alpha *= RHO

        if not accepted:
            # Could not find non-decreasing step
            break

        # Periodically attempt micro-SLP
        if since_micro >= MICRO_SLP_K:
            trial_centers, obj_trial = micro_slp_step(centers, radii, dx, dy)
            if obj_trial >= obj - OBJ_TOL:
                # Accept new centers
                centers = trial_centers
                radii, obj, _ = compute_max_radii_lp(centers)
                # Reset counters modestly
                accepts += 1
                since_micro = 0
                if obj - last_improv_obj < SMALL_IMPROV_THRESH:
                    small_improv_streak += 1
                else:
                    small_improv_streak = 0
                last_improv_obj = obj
            else:
                # No acceptance: reduce trigger
                since_micro = 0

    # Final radii recomputation (ensure consistency)
    radii, _obj, _ = compute_max_radii_lp(centers)

    return centers, radii


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


if __name__ == "main":
    main()

# If executed directly, run main (guard above uses string literal)
if __name__ == "__main__":
    main()
```
