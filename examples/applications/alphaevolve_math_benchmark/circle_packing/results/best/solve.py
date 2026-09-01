#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

Implements:
- Best-of-45 deterministic hex seeding with horizontal recentering.
- Exact LP for maximum-sum radii at fixed centers via primal simplex (Bland's rule).
- Deterministic two-pass pair repair to guarantee strict feasibility after each LP.
- Iterative active/near-active contact-guided center relaxation with monotone backtracking.
- Frequent, guarded micro-SLP trust-region steps (periodic and as plateau escapes) with
  deterministic trust-region adaptation.

The pipeline preserves strict feasibility and monotone non-decrease in the true objective.
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

# Boundary weighting (global modulation applied adaptively each iteration)
BASE_W_BOUNDARY_MIN = 2.1
BASE_W_BOUNDARY_MAX = 2.5
BASE_W_BOUNDARY_BASE = 2.2  # BASE_eff = clamp(min,max, BASE + 0.3 * tau)

CAP_PER_NODE = 3.0     # cap on accumulated magnitude per node before normalization

# Backtracking parameters
RHO = 0.95
ALPHA_MIN_SCALE = 1e-6

# Acceptance limits
MAX_ACCEPTS = 180
SMALL_IMPROV_THRESH = 1e-10
SMALL_IMPROV_STREAK_MAX = 5

# Micro-SLP parameters (trust-region schedule)
MICRO_SLP_K = 2  # trigger after this many accepted contact steps, and also as plateau escape
MICRO_SLP_H_INIT_SCALE = 0.08  # initial trust-region side (fraction of min(dx,dy))
MICRO_SLP_MIN_H_SCALE = 1e-4
MICRO_SLP_MAX_H_SCALE = 0.25
MICRO_SLP_BACKTRACK = 0.75
MICRO_SLP_GROW = 1.18
MICRO_SLP_MEANINGFUL_IMPROV = 1e-8
MICRO_SLP_MAX_BACKOFFS = 6

# Deterministic step-size modulation for contact moves
CONTACT_SMALL_IMPROV_THRESH = 1e-8  # threshold for "small" improvement
CONTACT_QUEUE_LEN = 3               # consecutive contact moves to trigger s0 reduction
S0_REDUCTION_FACTOR = 0.8           # one-time reduction multiplier


# -------------------------
# LP Solver (Primal Simplex)
# -------------------------
class SimplexResult:
    def __init__(self, x: np.ndarray, basis: List[int], status: str):
        self.x = x
        self.basis = basis
        self.status = status


def primal_simplex_blands(A: np.ndarray, b: np.ndarray, c: np.ndarray, tol: float = 1e-12,
                          max_iter: int = 200000) -> SimplexResult:
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
        for j in range(total_vars):
            if T[m, j] > tol:
                entering = j
                break
        if entering is None:
            # Optimal (within tolerance)
            break

        # Determine leaving variable: candidates with positive column coefficient
        col = T[:m, entering]
        min_ratio = None
        leave_row = None
        # Bland's tie-breaking: smallest basic variable index for ties
        for i in range(m):
            a_ij = col[i]
            if a_ij > tol:
                rhs = T[i, -1]
                ratio = rhs / a_ij if a_ij != 0.0 else np.inf
                if ratio < -tol:
                    ratio = np.inf
                cond = (min_ratio is None or ratio < min_ratio - tol or
                        (min_ratio is not None and abs(ratio - min_ratio) <= tol and basis[i] < basis[leave_row]))  # type: ignore
                if cond:
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
    xsol = np.zeros(n, dtype=float)
    for i in range(m):
        bi = basis[i]
        if bi < n:
            val = T[i, -1]
            if val < 0 and abs(val) <= 1e-9:
                val = 0.0
            xsol[bi] = val
    xsol = np.where(np.isfinite(xsol) & (xsol >= 0.0), xsol, 0.0)
    return SimplexResult(xsol, basis, status="optimal")


def two_pass_pair_repair(centers: np.ndarray, radii: np.ndarray) -> np.ndarray:
    """Run exactly two lexicographic passes to remove any residual pair overlaps.

    For any pair i<j with r_i + r_j > d_ij, scale both by factor d_ij / (r_i + r_j).
    Deterministic order: ascending i then ascending j; exactly two passes.
    """
    N = centers.shape[0]
    r = np.array(radii, dtype=float)
    # Precompute pair distances for speed
    # However, d_ij changes little, so recompute on the fly is fine for N=26; but we do once.
    for _ in range(2):
        for i in range(N):
            for j in range(i + 1, N):
                dij = float(np.linalg.norm(centers[i] - centers[j]))
                s = r[i] + r[j]
                if s > dij:
                    if s <= 0.0:
                        # Degenerate; clamp to zero
                        r[i] = 0.0
                        r[j] = 0.0
                    else:
                        f = dij / s
                        # Scale both radii proportionally
                        r[i] *= f
                        r[j] *= f
        # Clamp non-finite and tiny negatives
        r = np.where(np.isfinite(r) & (r >= 0.0), r, 0.0)
    return r


def compute_max_radii_lp(centers: np.ndarray) -> Tuple[np.ndarray, float, List[int]]:
    """Solve the LP for maximum sum of radii with fixed centers, then two-pass repair.

    Constraints:
        r_i >= 0 (implicit via LP nonnegativity)
        r_i <= x_i, r_i <= y_i, r_i <= 1-x_i, r_i <= 1-y_i
        r_i + r_j <= ||c_i - c_j|| for all i<j

    Returns:
        radii (N,), objective sum(r) AFTER two-pass pair repair, basis indices from simplex
    """
    N = centers.shape[0]
    # Boundaries
    x = centers[:, 0]
    y = centers[:, 1]
    # Build A, b deterministically
    rows: List[List[float]] = []
    b_list: List[float] = []

    # r_i <= x_i
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
    # Pair constraints
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
    radii = np.where(np.isfinite(radii) & (radii >= 0.0), radii, 0.0)

    # Deterministic two-pass pair repair
    radii = two_pass_pair_repair(centers, radii)

    return radii, float(np.sum(radii)), res.basis


def _clamp01(x: float) -> float:
    """Clamp value to [0,1]."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def build_active_motion(centers: np.ndarray, radii: np.ndarray) -> np.ndarray:
    """Construct a motion field nudging centers away from active/near-active constraints.

    - Boundary contributions: inward pushes only for sides achieving the minimum clearance
      min{x, y, 1-x, 1-y}. Active if r_i >= min_clear - TOL.
    - Boundary weights use tightness and global modulation BASE_eff based on average boundary tightness.
    - Near-active pairs (by slack) push along the line of centers with inverse-slack weights.
    - Per-node accumulation capped then divided by (1 + weight sum) before unit normalization.

    Returns:
        v: (N,2) normalized motion directions per center (unit norm or zero)
    """
    N = centers.shape[0]
    x = centers[:, 0]
    y = centers[:, 1]
    v = np.zeros_like(centers)
    wsum = np.zeros(N, dtype=float)

    # Pre-scan for boundary tightness to compute global modulation
    tightness_list: List[float] = []
    is_boundary_limited = np.zeros(N, dtype=bool)
    min_clears = np.zeros(N, dtype=float)
    clears_all = np.zeros((N, 4), dtype=float)

    for i in range(N):
        clears = np.array([x[i], y[i], 1.0 - x[i], 1.0 - y[i]], dtype=float)
        clears_all[i] = clears
        min_clear = float(np.min(clears))
        min_clears[i] = min_clear
        if radii[i] >= min_clear - TOL:
            denom = max(min_clear, 1e-9)
            tightness = _clamp01(1.0 - (min_clear - float(radii[i])) / denom)
            tightness_list.append(tightness)
            is_boundary_limited[i] = True

    tau = float(np.mean(tightness_list)) if len(tightness_list) > 0 else 0.0
    base_eff = BASE_W_BOUNDARY_BASE + 0.3 * tau
    base_eff = float(min(BASE_W_BOUNDARY_MAX, max(BASE_W_BOUNDARY_MIN, base_eff)))

    # Helper to add weighted vector to node i
    def add_contrib(i: int, delta: np.ndarray, weight: float) -> None:
        vi = v[i] + delta
        norm_vi = float(np.linalg.norm(vi))
        if norm_vi > CAP_PER_NODE:
            vi = vi / norm_vi * CAP_PER_NODE
        v[i] = vi
        wsum[i] += float(weight)

    # Boundary contributions
    for i in range(N):
        if is_boundary_limited[i]:
            min_clear = min_clears[i]
            denom = max(min_clear, 1e-9)
            tightness = _clamp01(1.0 - (min_clear - float(radii[i])) / denom)
            w_bound = base_eff * tightness
            if w_bound > 0.0:
                clears = clears_all[i]
                # Apply all sides within tolerance of min_clear
                if abs(clears[0] - min_clear) <= TOL:  # left wall
                    add_contrib(i, np.array([w_bound, 0.0], dtype=float), w_bound)
                if abs(clears[1] - min_clear) <= TOL:  # bottom wall
                    add_contrib(i, np.array([0.0, w_bound], dtype=float), w_bound)
                if abs(clears[2] - min_clear) <= TOL:  # right wall
                    add_contrib(i, np.array([-w_bound, 0.0], dtype=float), w_bound)
                if abs(clears[3] - min_clear) <= TOL:  # top wall
                    add_contrib(i, np.array([0.0, -w_bound], dtype=float), w_bound)

    # Pairwise near-active constraints (inverse-slack with stabilizers)
    tol_pair = max(TOL, PAIR_TOL_MIN)
    for i in range(N):
        for j in range(i + 1, N):
            dij_vec = centers[i] - centers[j]
            dij = float(np.linalg.norm(dij_vec))
            if dij <= DELTA:
                continue
            s = dij - float(radii[i] + radii[j])
            if s <= tol_pair:
                n_ij = dij_vec / dij
                w = min(W_MAX, 1.0 / (s + SIGMA)) * (1.0 / max(dij, DELTA))
                add_contrib(i, w * n_ij, w)
                add_contrib(j, -w * n_ij, w)

    # Normalize per node by (1 + weight sum), then unit-normalize
    for i in range(N):
        scale = 1.0 + wsum[i]
        if scale > 0.0:
            v[i] /= scale
        norm = float(np.linalg.norm(v[i]))
        if norm > 0.0:
            v[i] /= norm
    return v


class MicroSLPState:
    """Deterministic state for micro-SLP trust region size across calls."""
    def __init__(self, base_scale: float):
        self.base_scale = base_scale
        self.h = MICRO_SLP_H_INIT_SCALE * base_scale
        self.h_min = MICRO_SLP_MIN_H_SCALE * base_scale
        self.h_max = MICRO_SLP_MAX_H_SCALE * base_scale


def micro_slp_step(centers: np.ndarray,
                   obj_curr: float,
                   state: MicroSLPState) -> Tuple[np.ndarray, float, bool]:
    """Run a trust-region micro-SLP step that linearizes constraints (deterministic).

    Variables: r (N), dx+ (N), dx- (N), dy+ (N), dy- (N). All >= 0.
    Trust region: |Δx_i|, |Δy_i| <= h via split variables bounded by h.

    Adaptation:
      - On acceptance with meaningful improvement (> MICRO_SLP_MEANINGFUL_IMPROV), grow h by MICRO_SLP_GROW up to h_max.
      - On marginal acceptance (<= threshold), keep h unchanged.
      - On rejection, shrink h by MICRO_SLP_BACKTRACK and retry up to MICRO_SLP_MAX_BACKOFFS.

    Returns:
        centers_new, obj_new, accepted_flag
    """
    N = centers.shape[0]
    x = centers[:, 0]
    y = centers[:, 1]

    # Precompute pair data
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

    def solve_micro_lp(h: float) -> Tuple[np.ndarray, float]:
        """Solve the linearized LP for a given trust-region size h and return delta centers, new sum(r)."""
        nvar = 5 * N
        rows: List[List[float]] = []
        b_list: List[float] = []

        # Boundary constraints (linearized walls)
        # r_i - dxp_i + dxm_i <= x_i
        for i in range(N):
            row = [0.0] * nvar
            row[i] = 1.0
            row[N + i] = -1.0
            row[N + N + i] = 1.0
            rows.append(row)
            b_list.append(float(x[i]))
        # r_i - dyp_i + dym_i <= y_i
        for i in range(N):
            row = [0.0] * nvar
            row[i] = 1.0
            row[N + 2 * N + i] = -1.0
            row[N + 3 * N + i] = 1.0
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
        # Trust-region bounds
        for i in range(N):
            # dxp_i <= h
            row = [0.0] * nvar
            row[N + i] = 1.0
            rows.append(row)
            b_list.append(state.h if h is None else h)
            # dxm_i <= h
            row = [0.0] * nvar
            row[N + N + i] = 1.0
            rows.append(row)
            b_list.append(state.h if h is None else h)
            # dyp_i <= h
            row = [0.0] * nvar
            row[N + 2 * N + i] = 1.0
            rows.append(row)
            b_list.append(state.h if h is None else h)
            # dym_i <= h
            row = [0.0] * nvar
            row[N + 3 * N + i] = 1.0
            rows.append(row)
            b_list.append(state.h if h is None else h)

        # Pair constraints: r_i + r_j <= d_ij + u_ij^T(Δc_i - Δc_j)
        for idx, (i, j) in enumerate(idx_pairs):
            nx = float(normals[idx][0])
            ny = float(normals[idx][1])
            row = [0.0] * nvar
            row[i] = 1.0
            row[j] = 1.0
            # i contributions
            row[N + i] += -nx
            row[N + N + i] += nx
            row[N + 2 * N + i] += -ny
            row[N + 3 * N + i] += ny
            # j contributions (opposite)
            row[N + j] += nx
            row[N + N + j] += -nx
            row[N + 2 * N + j] += ny
            row[N + 3 * N + j] += -ny
            rows.append(row)
            b_list.append(dists[idx])

        A = np.array(rows, dtype=float)
        b_vec = np.array(b_list, dtype=float)
        c_vec = np.zeros(5 * N, dtype=float)
        c_vec[:N] = 1.0

        res = primal_simplex_blands(A, b_vec, c_vec, tol=1e-12)
        xsol = res.x
        dxp = xsol[N:N + N]
        dxm = xsol[N + N:N + 2 * N]
        dyp = xsol[N + 2 * N:N + 3 * N]
        dym = xsol[N + 3 * N:N + 4 * N]
        dx = dxp - dxm
        dy = dyp - dym
        delta_c = np.stack([dx, dy], axis=1)
        return delta_c, float(np.sum(xsol[:N]))

    # Start with current h
    h = state.h
    backoffs = 0
    best_centers = centers
    best_obj = obj_curr
    accepted = False

    while h >= state.h_min and backoffs <= MICRO_SLP_MAX_BACKOFFS:
        delta_c, pred_sum_r = solve_micro_lp(h)
        trial_centers = centers + delta_c
        np.clip(trial_centers, HEX_EPS, 1.0 - HEX_EPS, out=trial_centers)
        # Evaluate true LP + two-pass repair objective
        _, obj_trial, _ = compute_max_radii_lp(trial_centers)
        if obj_trial >= obj_curr - OBJ_TOL:
            # Accepted
            accepted = True
            best_centers = trial_centers
            best_obj = obj_trial
            # Trust-region adaptation
            if obj_trial - obj_curr > MICRO_SLP_MEANINGFUL_IMPROV:
                state.h = min(state.h_max, h * MICRO_SLP_GROW)
            else:
                # Neutral on marginal
                state.h = h
            break
        else:
            # Rejected -> back off
            h *= MICRO_SLP_BACKTRACK
            backoffs += 1

    # If no acceptance, shrink stored h to the last attempted (or min) to avoid retrying too large
    if not accepted:
        state.h = max(state.h_min, h)

    return best_centers, best_obj, accepted


# -------------------------
# Best-of-45 Hexagonal Seed
# -------------------------
def build_hex_variant(r6_row: int, s_shift: float, t_shift: float) -> Tuple[np.ndarray, float, float]:
    """Build one hexagonal-like seed variant for 26 centers.

    Geometry:
      - eps interior margin
      - dx = (1 - 2*eps)/5
      - dy = (sqrt(3)/2)*dx
      - five rows, base counts [5,5,5,5,5] with one row (r6_row) having 6 (unshifted),
        other rows with 5 are staggered by +0.5*dx
      - vertical row centers y_k = 0.5 - 2*dy + k*dy, then add s_shift
      - horizontal columns for each row, add t_shift, then clip to [eps, 1-eps]
    """
    eps = HEX_EPS
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx

    y0 = 0.5 - 2.0 * dy
    centers: List[List[float]] = []
    for k in range(5):
        yk = y0 + k * dy + s_shift
        if k == r6_row:
            cnt = 6
            xs = [eps + c * dx for c in range(cnt)]
        else:
            cnt = 5
            xs = [eps + 0.5 * dx + c * dx for c in range(cnt)]
        for x in xs:
            centers.append([x + t_shift, yk])

    centers_arr = np.array(centers, dtype=float)
    # Clip
    np.clip(centers_arr, eps, 1.0 - eps, out=centers_arr)
    return centers_arr, dx, dy


def best_of_45_seed() -> Tuple[np.ndarray, float, float]:
    """Deterministically choose the best seed among 45 variants.

    Variants:
      - r6 ∈ {0,1,2,3,4} (which row has 6 columns)
      - s ∈ {-0.2, 0.0, +0.2} * dy (vertical recenter)
      - t ∈ {-0.2, 0.0, +0.2} * dx (horizontal phase)

    Scoring uses exact fixed-center LP objective followed by two-pass pair repair.
    Tie-breaker prefers r6=2, then s=0, then t=0, then lowest r6, s, t.
    """
    eps = HEX_EPS
    # Base spacings (for shifts)
    dx_base = (1.0 - 2.0 * eps) / 5.0
    dy_base = (np.sqrt(3.0) / 2.0) * dx_base
    s_vals = [-0.2 * dy_base, 0.0, +0.2 * dy_base]
    t_vals = [-0.2 * dx_base, 0.0, +0.2 * dx_base]

    best_centers = None
    best_dx = dx_base
    best_dy = dy_base
    best_obj = -np.inf

    # Preferred tie-break values are indices (r6=2, s_idx=1, t_idx=1)
    def tie_key(r6: int, s_idx: int, t_idx: int) -> Tuple[int, int, int, int, int, int]:
        return (
            0 if r6 == 2 else 1,
            0 if s_idx == 1 else 1,
            0 if t_idx == 1 else 1,
            r6,
            s_idx,
            t_idx,
        )

    best_key: Optional[Tuple[int, int, int, int, int, int]] = None

    for r6 in range(5):
        for s_idx, s in enumerate(s_vals):
            for t_idx, t in enumerate(t_vals):
                centers, dx, dy = build_hex_variant(r6, s, t)
                _, obj, _ = compute_max_radii_lp(centers)
                key = tie_key(r6, s_idx, t_idx)
                if obj > best_obj + 1e-15:
                    best_obj = obj
                    best_centers = centers
                    best_dx, best_dy = dx, dy
                    best_key = key
                elif abs(obj - best_obj) <= 1e-15:
                    # Tie-break
                    if best_key is None or key < best_key:
                        best_centers = centers
                        best_dx, best_dy = dx, dy
                        best_key = key

    assert best_centers is not None
    return best_centers, best_dx, best_dy


def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    Pipeline:
      1) Best-of-45 deterministic hexagonal seeding (26 centers).
      2) Fixed-center radii via deterministic LP (primal simplex with Bland's rule) + two-pass repair.
      3) Active/near-active contact-guided relaxation with monotone backtracking.
      4) Frequent micro-SLP trust-region step (periodic and as plateau escape) for coordinated moves.
    """
    if num_circles != 26:
        # Fallback simple seed adapted from the hex layout (no 45-scan)
        centers, dx, dy = build_hex_variant(r6_row=2, s_shift=0.0, t_shift=0.0)
        if centers.shape[0] > num_circles:
            centers = centers[:num_circles]
        else:
            # Pad with center-near points (deterministic)
            extra = num_circles - centers.shape[0]
            base = np.array([0.5, 0.5], dtype=float)
            adds = []
            for k in range(extra):
                adds.append(base + np.array([(dx * 0.1) * ((k % 3) - 1),
                                             (dy * 0.1) * ((k // 3) - 1)], dtype=float))
            centers = np.vstack([centers, np.array(adds, dtype=float)])
        np.clip(centers, HEX_EPS, 1.0 - HEX_EPS, out=centers)
    else:
        centers, dx, dy = best_of_45_seed()

    # Initial LP radii (with post-LP two-pass repair inside)
    radii, obj, _ = compute_max_radii_lp(centers)

    # Active-set relaxation loop
    s0 = 0.1 * min(dx, dy)
    alpha_min = ALPHA_MIN_SCALE * s0

    accepts = 0
    small_improv_streak = 0
    since_micro = 0

    # Contact improvement tracking for deterministic step-size modulation
    contact_improv_queue: List[float] = []
    s0_reduced_once = False

    # Micro-SLP trust region state
    micro_state = MicroSLPState(base_scale=min(dx, dy))

    # Deterministic loop
    while accepts < MAX_ACCEPTS and small_improv_streak < SMALL_IMPROV_STREAK_MAX:
        # Build motion
        v = build_active_motion(centers, radii)

        def do_micro_slp(as_plateau: bool) -> bool:
            nonlocal centers, radii, obj, accepts, since_micro, small_improv_streak
            trial_centers, obj_trial, accepted = micro_slp_step(centers, obj, micro_state)
            if accepted and obj_trial >= obj - OBJ_TOL:
                centers = trial_centers
                radii, obj, _ = compute_max_radii_lp(centers)
                accepts += 1
                since_micro = 0
                # Track small improvement streak
                if obj_trial - obj + (obj - obj) < SMALL_IMPROV_THRESH:  # effectively obj_trial - obj_old
                    # since obj was updated to obj_trial, compare improvement via variable delta beforehand
                    # But we only know obj_trial >= obj_old - tol; treat as marginal improvement
                    small_improv_streak += 1
                else:
                    small_improv_streak = 0
                return True
            else:
                since_micro = 0 if as_plateau else since_micro
                return False

        # If all-zero direction field -> try micro-SLP as plateau escape
        if not np.any(np.linalg.norm(v, axis=1) > 0.0):
            if not do_micro_slp(as_plateau=True):
                break
            else:
                continue

        # Backtracking line search for contact-guided motion
        alpha = s0
        accepted_contact = False
        while alpha >= alpha_min:
            trial_centers = centers + alpha * v
            np.clip(trial_centers, HEX_EPS, 1.0 - HEX_EPS, out=trial_centers)
            radii_trial, obj_trial, _ = compute_max_radii_lp(trial_centers)
            if obj_trial >= obj - OBJ_TOL:
                improv = obj_trial - obj
                centers = trial_centers
                radii = radii_trial
                obj = obj_trial
                accepts += 1
                since_micro += 1
                accepted_contact = True
                # Track small improvement streak
                if improv < SMALL_IMPROV_THRESH:
                    small_improv_streak += 1
                else:
                    small_improv_streak = 0
                # Contact-move step-size modulation tracking
                contact_improv_queue.append(improv)
                if len(contact_improv_queue) > CONTACT_QUEUE_LEN:
                    contact_improv_queue.pop(0)
                if (not s0_reduced_once and len(contact_improv_queue) == CONTACT_QUEUE_LEN and
                        all(im < CONTACT_SMALL_IMPROV_THRESH for im in contact_improv_queue)):
                    s0 *= S0_REDUCTION_FACTOR
                    alpha_min = ALPHA_MIN_SCALE * s0
                    s0_reduced_once = True
                break
            alpha *= RHO

        if not accepted_contact:
            # Plateau: try micro-SLP escape deterministically
            if not do_micro_slp(as_plateau=True):
                break
            else:
                continue

        # Periodically attempt micro-SLP
        if since_micro >= MICRO_SLP_K:
            _ = do_micro_slp(as_plateau=False)

    # Final radii recomputation (ensure consistency and feasibility)
    radii, _obj, _ = compute_max_radii_lp(centers)
    radii = np.where(np.isfinite(radii) & (radii >= 0.0), radii, 0.0)
    np.clip(centers, HEX_EPS, 1.0 - HEX_EPS, out=centers)

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

# If executed directly, run main (guard above uses string literal)
if __name__ == "__main__":
    main()
