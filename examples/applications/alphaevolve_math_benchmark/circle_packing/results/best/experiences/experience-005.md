Successful packing of 26 circles in [0,1]^2 using a warm-started Bland simplex LP backbone with alternating contact relaxation and micro SLP steps; achieved valid, non-decreasing objectives with sum_radii 2.62285484831623.

- Edge-Fit Hex + Warm-Start LP + Hybrid Contact–SLP Relaxation: In the 26-circle unit-square packing task, prebuilding the LP structure and warm-starting a Bland’s-rule primal simplex between successive solves, while alternating dual/tightness-weighted, degree-normalized contact moves with periodic micro SLP steps (triggered every K=3 or when three accepted contact moves each improve the objective by < 1e−8) and accepting only non-decreasing objectives, yielded a valid solution with sum_radii 2.62285484831623; future designs should reuse this deterministic warm-start + monotone schedule because it reduces pivot counts and enables safe coordinated nudges with bounded move budgets (h0 ≈ 0.04, decay ≈ 0.7) that escape local bottlenecks without violating feasibility.
- HexWarm Contact + Trust-Region Micro-SLP combined a prebuilt A-template and warm-started Bland’s-rule primal simplex with alternating tightness-weighted contact relaxation and an adaptive trust-region micro SLP guarded by true-feasibility backtracking and monotone, objective-nondecreasing acceptance, enabling larger yet safe coordinated center moves and faster progress per LP budget; on the 26-circle unit-square task it achieved sum_radii 2.6235014012649764 with validity 1.0, outperforming parent variants scoring 2.62285484831623 and 2.614833774755255. Reuse this pattern by layering an adaptive trust-region SLP with true-feasibility backtracking on top of a warm-started radii LP and accepting only non-decreasing objectives to preserve determinism while permitting aggressive but safe moves.
- HexWarm LP + Near-Active Contact with Trust-Region Micro-SLP: On the 26-circle packing task, the method deterministically seeds an edge-fitted hexagonal layout and maximizes the sum of radii for fixed centers via a warm-started primal simplex LP with Bland's rule and fixed constraint ordering.
- Active/near-active contact guidance: The algorithm moves centers along inward boundary normals and pairwise contact normals when constraints are active or near-active, using capped inverse-slack weights, per-center unit-direction normalization, and monotone backtracking that only accepts non-decreasing LP objectives.
- Every K accepted contact moves or under stalled improvements, a deterministic trust-region micro-SLP jointly optimizes centers and radii in a linearized model with Bland's rule and true-feasibility backtracking, and accepts a step only if it yields a non-decreasing true objective, helping escape local bottlenecks beyond pure contact nudging.
- Metrics for HexWarm LP + Near-Active Contact with Trust-Region Micro-SLP (26-circle packing): The reported run achieved sum_radii = 2.6222066834549853 with validity = 1.0.

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 disjoint circles in the unit square.

This implementation follows a deterministic, optimization-driven approach:

Two-stage optimizer for packing 26 circles in [0,1]^2 that alternates an exact
LP for radii and two complementary center-move mechanisms:

1) Deterministic edge-fit hexagonal seeding of 26 centers with a small margin.
2) Exact (for fixed centers) linear program to maximize sum of radii using
   a prebuilt A-template and Bland’s-rule primal simplex.
3) Alternating improvement:
   - Active-set/contact relaxation: lightweight move along dual/tightness-weighted
     contact directions of active pairs and boundary ties, with monotone acceptance.
   - Periodic micro SLP steps: small coordinated center displacements via a
     conservative linearization of pair distances, solved by the same simplex.
4) Final exact LP for radii at the improved centers; monotone acceptance ensures
   non-decreasing sum of radii.

All steps are deterministic and require only NumPy (no external solvers).
A small primal-simplex LP solver for <=-constraints with nonnegative variables
and nonnegative RHS is included.

"""

import json
from typing import Optional, Tuple, List

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles."""
    if num_circles != 26:
        # The algorithm and seeding are tuned for 26 circles.
        # If a different number is requested, fall back to a simple grid.
        centers = _fallback_grid(num_circles)
        radii = _lp_radii_for_fixed_centers(centers)
        return centers, radii

    # Parameters (deterministic)
    eps = 1e-3
    # Seed spacings used to derive a base step-size for contact relaxation
    dx_seed = (1.0 - 2.0 * eps) / 5.0
    dy_seed = (np.sqrt(3.0) / 2.0) * dx_seed
    s0_base = 0.1 * min(dx_seed, dy_seed)
    rho = 0.95

    # 1) Deterministic edge-fitted hexagonal seeding
    centers = _hex_seed_26(eps=eps)

    # 2) Exact radii LP template (fixed centers but b depends on centers)
    radii_lp = _RadiiLPTemplate(centers.shape[0])
    r = radii_lp.solve(centers)
    obj = float(np.sum(r))

    # 3) Alternating improvement: contact relaxation + periodic micro SLP steps
    max_outer = 50
    K_contact_before_slp = 3
    small_improve_tol = 1e-8
    backtrack_max = 10

    # Adaptive modulation of contact step base s0
    s0 = s0_base
    reduced_once = False

    accepted_contact_since_slp = 0
    small_improvements = []  # track last few contact improvements

    slp_steps = 0  # count of micro SLP steps for decay of move budget

    N = centers.shape[0]

    # Pre-allocated arrays for speed
    v = np.zeros((N, 2), dtype=float)

    for t in range(max_outer):
        # Extract active set from current LP solution and geometry
        active_info = _extract_active_set(centers, r, tol_active=1e-9)

        # Build motion vectors from active constraints
        ok_v = _build_contact_motion_vectors(centers, r, active_info, out=v)
        if not ok_v:
            # No motion suggested: stop
            break

        # Normalize vectors to unit length where nonzero
        for i in range(N):
            nx = v[i, 0]
            ny = v[i, 1]
            norm = float(np.hypot(nx, ny))
            if norm > 0:
                v[i, 0] = nx / norm
                v[i, 1] = ny / norm

        # Monotone projected backtracking along v
        accepted = False
        step_len = s0 * (rho ** t)
        for bt in range(backtrack_max):
            C_cand = centers + step_len * v
            # Clip to margin box [eps, 1-eps]^2
            np.clip(C_cand, eps, 1.0 - eps, out=C_cand)
            r_cand = radii_lp.solve(C_cand)
            obj_cand = float(np.sum(r_cand))
            if obj_cand >= obj - 1e-12:
                centers = C_cand
                r = r_cand
                improvement = obj_cand - obj
                obj = obj_cand
                accepted = True
                accepted_contact_since_slp += 1
                small_improvements.append(improvement)
                if len(small_improvements) > 3:
                    small_improvements.pop(0)
                break
            step_len *= 0.5  # deterministic backtracking

        if not accepted:
            # Backtracking failed to accept any step; stop improvements
            break

        # Adaptive modulation: if three accepted contact moves in a row had tiny improvement
        # reduce s0 once; if stagnation persists, we will rely more on micro SLP.
        if len(small_improvements) == 3 and all(di < small_improve_tol for di in small_improvements):
            if not reduced_once:
                s0 *= 0.8
                reduced_once = True

        # Periodic micro SLP trigger conditions:
        trigger_slp = False
        if accepted_contact_since_slp >= K_contact_before_slp:
            trigger_slp = True
        elif len(small_improvements) == 3 and all(di < small_improve_tol for di in small_improvements):
            # Stagnation -> trigger SLP
            trigger_slp = True

        if trigger_slp:
            # Micro SLP step with decaying move budget
            h_t = 0.04 * (0.7 ** slp_steps)
            C_s, r_s, obj_s, slp_accepted = _micro_slp_step(centers, radii_lp, h_t=h_t, eps=eps)
            if slp_accepted:
                centers = C_s
                r = r_s
                obj = obj_s
            # Reset contact counters regardless of acceptance to avoid overly frequent SLP
            accepted_contact_since_slp = 0
            small_improvements.clear()
            slp_steps += 1

    # 4) Final exact LP for radii and a light polish
    r_final = radii_lp.solve(centers)
    r_final = np.nan_to_num(r_final, nan=0.0, posinf=0.0, neginf=0.0)
    r_final[r_final < 0] = 0.0
    centers = np.clip(centers, eps, 1.0 - eps)

    return centers, r_final


# ----------------------------
# Seeding strategies
# ----------------------------
def _hex_seed_26(eps: float = 1e-3) -> np.ndarray:
    """Edge-fitted hexagonal seeding for 26 centers within [eps, 1-eps]^2.

    Pattern: five rows with counts [6,5,5,5,5]; horizontal spacing dx fits 6 columns
    exactly between the eps margins; vertical spacing dy = sqrt(3)/2 * dx; rows with 5
    columns are staggered by dx/2. Vertically centered using y0 = 0.5 - 2*dy.
    """
    N = 26
    centers = np.zeros((N, 2), dtype=float)

    # Layout: rows [6,5,5,5,5]
    row_counts = [6, 5, 5, 5, 5]
    R = len(row_counts)
    assert sum(row_counts) == N

    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx

    # Vertical centering: y rows at y0 + k*dy for k=0..4
    y0 = 0.5 - 2.0 * dy

    idx = 0
    for k, n in enumerate(row_counts):
        yk = y0 + k * dy
        # Determine stagger offset: 5-column rows are staggered by dx/2
        x_offset = 0.0
        if n == 5:
            x_offset = dx / 2.0
        # For 6-column row, leftmost center at eps
        # For 5-column row, leftmost center at eps + dx/2
        x_start = eps + x_offset
        for i in range(n):
            centers[idx, 0] = x_start + i * dx
            centers[idx, 1] = yk
            idx += 1

    # Clip to [eps, 1-eps] just in case
    centers = np.clip(centers, eps, 1.0 - eps)
    return centers


def _fallback_grid(N: int) -> np.ndarray:
    """Simple deterministic fallback grid seeding for other N."""
    m = int(np.ceil(np.sqrt(N)))
    xs = np.linspace(0.1, 0.9, m)
    ys = np.linspace(0.1, 0.9, m)
    pts = []
    for j in range(m):
        for i in range(m):
            if len(pts) < N:
                pts.append([xs[i], ys[j]])
    return np.array(pts, dtype=float)


# ----------------------------
# Linear Programming Solver
# ----------------------------
class SimplexLP:
    """Simplex solver for LPs in the form:
        maximize c^T x
        subject to A x <= b,  x >= 0, and (crucially) b >= 0.

    - Adds slack variables s >= 0 to get A x + I s = b.
    - Starts from the feasible BFS: x = 0, s = b.
    - Uses Bland's rule for pivoting to reduce cycling risk.
    - Returns (status, x_opt, obj_val). Status in {"optimal", "unbounded", "infeasible"}.

    Notes:
    - This solver assumes that all RHS b are nonnegative (>= -tol).
      Rows with slightly negative b (due to numeric roundoff) are clipped to zero.
    - Works well for small to mid-size problems typical in this task.
    """

    def __init__(self, c: np.ndarray, A: np.ndarray, b: np.ndarray, tol: float = 1e-10, max_iters: int = 100000):
        self.tol = tol
        self.max_iters = max_iters

        # Validate inputs
        c = np.asarray(c, dtype=float).ravel()
        A = np.asarray(A, dtype=float)
        b = np.asarray(b, dtype=float).ravel()
        m, n = A.shape
        assert c.shape[0] == n, "c dimension mismatch"
        assert b.shape[0] == m, "b dimension mismatch"

        # Force nonnegative RHS (clip tiny negatives to zero)
        b = np.maximum(b, 0.0)

        # Build tableau with slack variables
        # Columns: [x (n), s (m), rhs]
        self.m = m
        self.n = n
        T = np.zeros((m + 1, n + m + 1), dtype=float)
        # Constraint rows
        T[:m, :n] = A
        T[:m, n:n + m] = np.eye(m)
        T[:m, -1] = b
        # Objective row for maximization: coefficients = -c (so negative entries invite entering)
        T[m, :n] = -c
        T[m, n:n + m] = 0.0
        T[m, -1] = 0.0

        self.T = T
        # Basis initially: slack variable indices (n .. n+m-1)
        self.basic = list(range(n, n + m))
        self.nonbasic = list(range(n))  # x variables

    def _choose_entering(self) -> Optional[int]:
        """Choose entering column using Bland's rule: smallest index with negative reduced cost."""
        obj_row = self.T[self.m, :-1]
        # Negative coefficient means we can improve objective by increasing this var (since we maximize)
        candidates = [j for j, coeff in enumerate(obj_row) if coeff < -self.tol]
        if not candidates:
            return None
        return min(candidates)

    def _choose_leaving(self, enter_col: int) -> Optional[int]:
        """Minimum ratio test with Bland's rule tie-breaking."""
        rhs = self.T[:self.m, -1]
        col = self.T[:self.m, enter_col]
        ratios: List[Tuple[float, int]] = []
        for i in range(self.m):
            a = col[i]
            if a > self.tol:  # positive coefficient => can increase entering var
                ratios.append((rhs[i] / a, i))
        if not ratios:
            return None  # unbounded
        # Find minimal ratio, tie-break by smallest row index (Bland)
        min_ratio = min(r[0] for r in ratios)
        candidates = [i for (r, i) in ratios if r <= min_ratio + 1e-12]
        return min(candidates)

    def _pivot(self, row: int, col: int) -> None:
        """Perform pivot at (row, col)."""
        T = self.T
        pivot = T[row, col]
        # Normalize pivot row
        T[row, :] = T[row, :] / pivot
        # Eliminate column in all other rows
        for i in range(T.shape[0]):
            if i == row:
                continue
            factor = T[i, col]
            if abs(factor) > 0:
                T[i, :] -= factor * T[row, :]
        # Update basis tracking
        self.basic[row] = col

    def solve(self) -> Tuple[str, Optional[np.ndarray], Optional[float]]:
        """Run simplex iterations."""
        iters = 0
        while iters < self.max_iters:
            iters += 1
            enter_col = self._choose_entering()
            if enter_col is None:
                # Optimal
                status = "optimal"
                x_opt = self._extract_solution()
                obj = self.T[self.m, -1]
                return status, x_opt, obj
            leave_row = self._choose_leaving(enter_col)
            if leave_row is None:
                # Unbounded objective
                return "unbounded", None, None
            self._pivot(leave_row, enter_col)
        return "infeasible", None, None  # iteration limit hit; treat as failure

    def _extract_solution(self) -> np.ndarray:
        """Extract the full solution for x variables (first n columns)."""
        m, n = self.m, self.n
        x = np.zeros(n, dtype=float)
        for i in range(m):
            col = self.basic[i]
            if col < n:
                # Basic original variable
                x[col] = self.T[i, -1]
        # Nonbasic original variables are zero
        # Clip small negatives to zero
        x[x < 0] = 0.0
        return x


# ----------------------------
# Radii LP Template (fixed A; only b changes with centers)
# ----------------------------
class _RadiiLPTemplate:
    """Prebuilt A-template for the exact radii LP with fixed centers.

    Model:
      max sum r_i
      s.t. r_i <= min_clear_i := min{x_i, y_i, 1-x_i, 1-y_i}           for i=1..N
           r_i + r_j <= ||c_i - c_j||                                   for all i<j
           r_i >= 0

    The A matrix is constant: identity for the first N rows and pairwise 1's for remaining rows.
    The RHS b is recomputed per solve from centers.
    """

    def __init__(self, N: int):
        self.N = N
        # Build pair list in lexicographic order (i<j)
        pairs: List[Tuple[int, int]] = []
        for i in range(N):
            for j in range(i + 1, N):
                pairs.append((i, j))
        self.pairs = pairs
        self.num_pairs = len(pairs)

        # Build constant A matrix
        m = N + self.num_pairs
        A = np.zeros((m, N), dtype=float)
        # Top N rows: identity
        A[:N, :N] = np.eye(N)
        # Pair rows: 1 in columns i and j
        for k, (i, j) in enumerate(pairs):
            A[N + k, i] = 1.0
            A[N + k, j] = 1.0
        self.A = A
        # Objective
        self.c = np.ones(N, dtype=float)

    def solve(self, centers: np.ndarray) -> np.ndarray:
        """Solve radii LP for given centers; returns r (N,), nonnegative."""
        N = self.N
        x = centers[:, 0]
        y = centers[:, 1]
        # Boundary caps: min clear distances to walls
        min_clear = np.minimum.reduce([x, y, 1.0 - x, 1.0 - y])
        # Pair distances
        b = np.zeros(N + self.num_pairs, dtype=float)
        b[:N] = min_clear
        for k, (i, j) in enumerate(self.pairs):
            d = float(np.linalg.norm(centers[i] - centers[j]))
            b[N + k] = d if d >= 0.0 else 0.0

        # Solve with simplex
        solver = SimplexLP(c=self.c, A=self.A, b=b, tol=1e-10, max_iters=400000)
        status, r, obj = solver.solve()
        if status != "optimal" or r is None:
            # Fallback: conservative greedy if simplex fails (should not happen).
            r = _greedy_radii_fallback(centers)
        # Ensure non-negativity and finiteness
        r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
        r[r < 0] = 0.0
        return r


# ----------------------------
# Active set and contact relaxation
# ----------------------------
def _extract_active_set(centers: np.ndarray, r: np.ndarray, tol_active: float = 1e-9):
    """Identify LP-active constraints: pair contacts and boundary ties.

    Returns a dict with:
      - 'active_pairs': list of (i,j,d_ij,tightness,u_ij) for pairs close to tight
      - 'boundary_ties': list per i of sides tied to the minimum and their inward normals
                         with a weight factor based on tightness to the wall
    """
    N = centers.shape[0]
    # Pair active set
    active_pairs = []
    for i in range(N):
        for j in range(i + 1, N):
            diff = centers[i] - centers[j]
            dij = float(np.hypot(diff[0], diff[1]))
            # Guard direction deterministically if almost coincident
            if dij > 1e-12:
                u = diff / dij
            else:
                u = np.array([1.0, 0.0], dtype=float)
            # Tight if r_i + r_j >= d_ij - tol
            if r[i] + r[j] >= dij - tol_active:
                # Weight tightness metric in [0,1]
                gap = dij - (r[i] + r[j])
                denom = max(dij, 1e-9)
                tightness = 1.0 - (gap / denom)
                if tightness < 0.0:
                    tightness = 0.0
                elif tightness > 1.0:
                    tightness = 1.0
                active_pairs.append((i, j, dij, tightness, u))

    # Boundary ties
    boundary_ties = [[] for _ in range(N)]
    x = centers[:, 0]
    y = centers[:, 1]
    for i in range(N):
        dists = np.array([x[i], y[i], 1.0 - x[i], 1.0 - y[i]], dtype=float)
        min_clear = float(np.min(dists))
        # If radius is close to the min clearance, sides achieving min are ties
        if r[i] >= min_clear - tol_active:
            gap_i = min_clear - r[i]
            denom = max(min_clear, 1e-9)
            tightness = 1.0 - (gap_i / denom)
            if tightness < 0.0:
                tightness = 0.0
            elif tightness > 1.0:
                tightness = 1.0
            # Identify sides at minimum, with inward normals
            # Order: [left(x), bottom(y), right(1-x), top(1-y)]
            sides = []
            if abs(dists[0] - min_clear) <= 1e-12:
                sides.append(np.array([1.0, 0.0]))   # inward from left wall
            if abs(dists[1] - min_clear) <= 1e-12:
                sides.append(np.array([0.0, 1.0]))   # inward from bottom wall
            if abs(dists[2] - min_clear) <= 1e-12:
                sides.append(np.array([-1.0, 0.0]))  # inward from right wall
            if abs(dists[3] - min_clear) <= 1e-12:
                sides.append(np.array([0.0, -1.0]))  # inward from top wall
            for nrm in sides:
                boundary_ties[i].append((nrm, tightness))

    return {"active_pairs": active_pairs, "boundary_ties": boundary_ties}


def _build_contact_motion_vectors(
    centers: np.ndarray,
    r: np.ndarray,
    active_info,
    out: Optional[np.ndarray] = None,
) -> bool:
    """Form dual/tightness-weighted contact motion vectors.

    - For each active pair (i,j): add w_pair * u_ij to i, and -w_pair * u_ij to j,
      where w_pair = tightness / max(d_ij,1e-9).
    - For boundary ties: add inward normals with w_bound = tightness based on wall tightness.
    - Degree normalization: divide each vector by (1 + total weight) to avoid hubs dominating.

    Returns True if at least one nonzero vector is produced, else False.
    """
    N = centers.shape[0]
    if out is None:
        v = np.zeros((N, 2), dtype=float)
    else:
        out.fill(0.0)
        v = out

    # Track accumulated weights per node for normalization
    wsum = np.zeros(N, dtype=float)

    # Active pairs
    for (i, j, dij, tightness, u) in active_info["active_pairs"]:
        denom = max(dij, 1e-9)
        w_pair = tightness / denom
        if w_pair > 0.0:
            v[i, 0] += w_pair * u[0]
            v[i, 1] += w_pair * u[1]
            v[j, 0] -= w_pair * u[0]
            v[j, 1] -= w_pair * u[1]
            wsum[i] += w_pair
            wsum[j] += w_pair

    # Boundary ties
    ties = active_info["boundary_ties"]
    for i in range(N):
        if not ties[i]:
            continue
        for (nrm, tightness) in ties[i]:
            w_bound = tightness
            if w_bound > 0.0:
                v[i, 0] += w_bound * nrm[0]
                v[i, 1] += w_bound * nrm[1]
                wsum[i] += w_bound

    # Degree normalization
    for i in range(N):
        denom = 1.0 + wsum[i]
        v[i, 0] /= denom
        v[i, 1] /= denom

    # Check if any nonzero
    any_nonzero = False
    for i in range(N):
        if v[i, 0] != 0.0 or v[i, 1] != 0.0:
            any_nonzero = True
            break
    return any_nonzero


# ----------------------------
# Micro SLP step (conservative linearization)
# ----------------------------
def _micro_slp_step(
    centers: np.ndarray,
    radii_lp: _RadiiLPTemplate,
    h_t: float = 0.04,
    eps: float = 1e-3,
) -> Tuple[np.ndarray, np.ndarray, float, bool]:
    """One micro SLP step that allows small center moves with conservative linearized pair constraints.

    Variables: r_i, dxp_i, dxm_i, dyp_i, dym_i (all >= 0). Δx = dxp - dxm, Δy = dyp - dym.

    Constraints:
      - r_i <= x_i + Δx_i
      - r_i <= y_i + Δy_i
      - r_i <= 1 - (x_i + Δx_i)
      - r_i <= 1 - (y_i + Δy_i)
      - r_i + r_j <= d_ij + u_ij^T(Δc_i - Δc_j) (linearized, conservative)
      - 0 <= dxp_i, dxm_i, dyp_i, dym_i <= h_t
      - Center after move stays within [0,1]: encoded as bounds on Δx_i, Δy_i
    """
    C = centers.copy()
    N = C.shape[0]
    r_best = radii_lp.solve(C)
    obj_prev = float(np.sum(r_best))

    # Variable indices
    def var_indices(n: int):
        r0 = 0
        dxp0 = r0 + n
        dxm0 = dxp0 + n
        dyp0 = dxm0 + n
        dym0 = dyp0 + n
        total = dym0 + n
        return r0, dxp0, dxm0, dyp0, dym0, total

    r0, dxp0, dxm0, dyp0, dym0, nvar = var_indices(N)
    cvec = np.zeros(nvar, dtype=float)
    cvec[r0:r0 + N] = 1.0

    A_rows = []
    b_vals = []

    def add_row(coeffs: List[Tuple[int, float]], rhs: float):
        row = np.zeros(nvar, dtype=float)
        for j, v in coeffs:
            row[j] = v
        b_vals.append(max(0.0, rhs))
        A_rows.append(row)

    x = C[:, 0]
    y = C[:, 1]

    # Wall constraints (exact)
    for i in range(N):
        # r_i <= x_i + Δx_i  => r_i - dxp_i + dxm_i <= x_i
        add_row([(r0 + i, 1.0), (dxp0 + i, -1.0), (dxm0 + i, 1.0)], x[i])
        # r_i <= y_i + Δy_i
        add_row([(r0 + i, 1.0), (dyp0 + i, -1.0), (dym0 + i, 1.0)], y[i])
        # r_i <= 1 - (x_i + Δx_i) => r_i + dxp_i - dxm_i <= 1 - x_i
        add_row([(r0 + i, 1.0), (dxp0 + i, 1.0), (dxm0 + i, -1.0)], 1.0 - x[i])
        # r_i <= 1 - (y_i + Δy_i)
        add_row([(r0 + i, 1.0), (dyp0 + i, 1.0), (dym0 + i, -1.0)], 1.0 - y[i])

    # Pairwise constraints (linearized conservative)
    for i in range(N):
        for j in range(i + 1, N):
            diff = C[i] - C[j]
            dij = float(np.hypot(diff[0], diff[1]))
            if dij > 1e-12:
                u = diff / dij
            else:
                u = np.array([0.0, 0.0], dtype=float)
            ux, uy = float(u[0]), float(u[1])
            # r_i + r_j - u^T(Δc_i - Δc_j) <= d_ij
            terms = [
                (r0 + i, 1.0),
                (r0 + j, 1.0),
                (dxp0 + i, -ux),
                (dxm0 + i, ux),
                (dyp0 + i, -uy),
                (dym0 + i, uy),
                (dxp0 + j, ux),
                (dxm0 + j, -ux),
                (dyp0 + j, uy),
                (dym0 + j, -uy),
            ]
            add_row(terms, dij)

    # Move budgets: |Δx_i| <= h_t, |Δy_i| <= h_t
    for i in range(N):
        add_row([(dxp0 + i, 1.0)], h_t)
        add_row([(dxm0 + i, 1.0)], h_t)
        add_row([(dyp0 + i, 1.0)], h_t)
        add_row([(dym0 + i, 1.0)], h_t)

    # Keep centers within [0,1] after move: 0 <= x_i + Δx_i <= 1, same for y
    for i in range(N):
        # Δx_i = dxp_i - dxm_i
        add_row([(dxp0 + i, 1.0), (dxm0 + i, -1.0)], 1.0 - x[i])   # upper
        add_row([(dxp0 + i, -1.0), (dxm0 + i, 1.0)], x[i])         # lower
        add_row([(dyp0 + i, 1.0), (dym0 + i, -1.0)], 1.0 - y[i])   # upper
        add_row([(dyp0 + i, -1.0), (dym0 + i, 1.0)], y[i])         # lower

    A = np.array(A_rows, dtype=float)
    b = np.array(b_vals, dtype=float)

    # Solve SLP
    solver = SimplexLP(c=cvec, A=A, b=b, tol=1e-10, max_iters=600000)
    status, sol, obj_lin = solver.solve()
    if status != "optimal" or sol is None:
        # SLP failed; reject step
        return C, r_best, obj_prev, False

    dx = sol[dxp0:dxp0 + N] - sol[dxm0:dxm0 + N]
    dy = sol[dyp0:dyp0 + N] - sol[dym0:dym0 + N]
    C_new = C.copy()
    C_new[:, 0] = np.clip(C_new[:, 0] + dx, eps, 1.0 - eps)
    C_new[:, 1] = np.clip(C_new[:, 1] + dy, eps, 1.0 - eps)

    # Recompute exact radii and objective; accept only if non-decreasing
    r_new = radii_lp.solve(C_new)
    obj_new = float(np.sum(r_new))
    if obj_new >= obj_prev - 1e-12:
        return C_new, r_new, obj_new, True
    return C, r_best, obj_prev, False


# ----------------------------
# Generic LP for arbitrary N (fallback path)
# ----------------------------
def _lp_radii_for_fixed_centers(centers: np.ndarray) -> np.ndarray:
    """Solve the exact LP for radii with fixed centers (generic N):
        maximize sum r_i
        s.t. r_i >= 0
             r_i <= distances to walls (four constraints per circle)
             r_i + r_j <= ||c_i - c_j|| for all pairs
    """
    N = centers.shape[0]
    # Variables: r (N), all nonnegative
    c = np.ones(N, dtype=float)

    A_rows = []
    b_vals = []

    # Wall constraints (separately for each wall for numerical robustness)
    x = centers[:, 0]
    y = centers[:, 1]
    # r_i <= x_i
    for i in range(N):
        row = np.zeros(N)
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(x[i])
    # r_i <= y_i
    for i in range(N):
        row = np.zeros(N)
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(y[i])
    # r_i <= 1 - x_i
    for i in range(N):
        row = np.zeros(N)
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(1.0 - x[i])
    # r_i <= 1 - y_i
    for i in range(N):
        row = np.zeros(N)
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(1.0 - y[i])

    # Pairwise disjointness: r_i + r_j <= d_ij
    for i in range(N):
        for j in range(i + 1, N):
            row = np.zeros(N)
            row[i] = 1.0
            row[j] = 1.0
            d = float(np.linalg.norm(centers[i] - centers[j]))
            A_rows.append(row)
            b_vals.append(d)

    A = np.array(A_rows, dtype=float)
    b = np.array(b_vals, dtype=float)

    # Solve via Simplex
    solver = SimplexLP(c=c, A=A, b=b, tol=1e-10, max_iters=200000)
    status, r, obj = solver.solve()
    if status != "optimal" or r is None:
        # Fallback: conservative greedy if simplex fails (should not happen).
        r = _greedy_radii_fallback(centers)
    # Ensure non-negativity and finiteness
    r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
    r[r < 0] = 0.0
    return r


# ----------------------------
# Fallback greedy radii (only used if LP fails unexpectedly)
# ----------------------------
def _greedy_radii_fallback(centers: np.ndarray) -> np.ndarray:
    """Conservative greedy radii computation used only as a last resort."""
    N = centers.shape[0]
    x = centers[:, 0]
    y = centers[:, 1]
    r = np.minimum.reduce([x, y, 1 - x, 1 - y])
    # Iteratively enforce pairwise constraints by uniform shrinkage on pairs
    for i in range(N):
        for j in range(i + 1, N):
            d = float(np.linalg.norm(centers[i] - centers[j]))
            s = r[i] + r[j]
            if s > d and s > 0:
                scale = d / s
                r[i] *= scale
                r[j] *= scale
    r[r < 0] = 0.0
    return r


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

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 disjoint circles in the unit square.

This implementation follows a deterministic, optimization-driven approach:

Two-stage optimizer for packing 26 circles in [0,1]^2 that alternates an exact
LP for radii and two complementary center-move mechanisms:

1) Deterministic edge-fit hexagonal seeding of 26 centers with a small margin.
2) Exact (for fixed centers) linear program to maximize sum of radii using
   a prebuilt A-template and Bland’s-rule primal simplex.
3) Alternating improvement:
   - Active-set/contact relaxation: lightweight move along dual/tightness-weighted
     contact directions of active pairs and boundary ties, with monotone acceptance.
   - Periodic micro SLP steps: small coordinated center displacements via a
     conservative linearization of pair distances, solved by the same simplex
     and guarded by an adaptive trust region with true-feasibility backtracking.
4) Final exact LP for radii at the improved centers; monotone acceptance ensures
   non-decreasing sum of radii.

All steps are deterministic and require only NumPy (no external solvers).
A small primal-simplex LP solver for <=-constraints with nonnegative variables
and nonnegative RHS is included.

"""

import json
from typing import Optional, Tuple, List

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles."""
    if num_circles != 26:
        # The algorithm and seeding are tuned for 26 circles.
        # If a different number is requested, fall back to a simple grid.
        centers = _fallback_grid(num_circles)
        radii = _lp_radii_for_fixed_centers(centers)
        return centers, radii

    # Parameters (deterministic)
    eps = 1e-3
    eps_wall = 1e-6  # small inward wall slack to avoid grazing in SLP
    # Seed spacings used to derive a base step-size for contact relaxation
    dx_seed = (1.0 - 2.0 * eps) / 5.0
    dy_seed = (np.sqrt(3.0) / 2.0) * dx_seed
    s0_base = 0.1 * min(dx_seed, dy_seed)
    rho = 0.95

    # 1) Deterministic edge-fitted hexagonal seeding
    centers = _hex_seed_26(eps=eps)

    # 2) Exact radii LP template (fixed centers but b depends on centers)
    radii_lp = _RadiiLPTemplate(centers.shape[0])
    r = radii_lp.solve(centers)
    obj = float(np.sum(r))

    # 3) Alternating improvement: contact relaxation + periodic micro SLP steps
    max_outer = 50
    K_contact_before_slp = 3
    small_improve_tol = 1e-8
    backtrack_max = 10

    # Adaptive modulation of contact step base s0
    s0 = s0_base
    reduced_once = False

    accepted_contact_since_slp = 0
    small_improvements = []  # track last few contact improvements

    # Trust-region parameters for micro SLP (persist across triggers)
    trust_h = 0.04
    h_max = 0.12
    h_floor = 1e-5

    N = centers.shape[0]
    v = np.zeros((N, 2), dtype=float)

    for t in range(max_outer):
        # Extract active set from current LP solution and geometry
        active_info = _extract_active_set(centers, r, tol_active=1e-9)

        # Build motion vectors from active constraints
        ok_v = _build_contact_motion_vectors(centers, r, active_info, out=v)
        if not ok_v:
            # No motion suggested: try a micro SLP before giving up
            C_s, r_s, obj_s, slp_accepted, trust_h = _micro_slp_trust_step(
                centers, radii_lp, trust_h, h_max=h_max, h_floor=h_floor, eps=eps, eps_wall=eps_wall, prev_obj=obj
            )
            if slp_accepted:
                centers = C_s
                r = r_s
                obj = obj_s
            break

        # Normalize vectors to unit length where nonzero
        for i in range(N):
            nx = v[i, 0]
            ny = v[i, 1]
            norm = float(np.hypot(nx, ny))
            if norm > 0:
                v[i, 0] = nx / norm
                v[i, 1] = ny / norm

        # Monotone projected backtracking along v
        accepted = False
        step_len = s0 * (rho ** t)
        for bt in range(backtrack_max):
            C_cand = centers + step_len * v
            # Clip to margin box [eps, 1-eps]^2
            np.clip(C_cand, eps, 1.0 - eps, out=C_cand)
            r_cand = radii_lp.solve(C_cand)
            obj_cand = float(np.sum(r_cand))
            if obj_cand >= obj - 1e-12:
                centers = C_cand
                r = r_cand
                improvement = obj_cand - obj
                obj = obj_cand
                accepted = True
                accepted_contact_since_slp += 1
                small_improvements.append(improvement)
                if len(small_improvements) > 3:
                    small_improvements.pop(0)
                break
            step_len *= 0.5  # deterministic backtracking

        if not accepted:
            # If backtracking fails, try a micro SLP step to escape before stopping
            C_s, r_s, obj_s, slp_accepted, trust_h = _micro_slp_trust_step(
                centers, radii_lp, trust_h, h_max=h_max, h_floor=h_floor, eps=eps, eps_wall=eps_wall, prev_obj=obj
            )
            if slp_accepted:
                centers = C_s
                r = r_s
                obj = obj_s
            break

        # Adaptive modulation: if three accepted contact moves in a row had tiny improvement
        # reduce s0 once; if stagnation persists, we will rely more on micro SLP.
        if len(small_improvements) == 3 and all(di < small_improve_tol for di in small_improvements):
            if not reduced_once:
                s0 *= 0.8
                reduced_once = True

        # Periodic micro SLP trigger conditions:
        trigger_slp = False
        if accepted_contact_since_slp >= K_contact_before_slp:
            trigger_slp = True
        elif len(small_improvements) == 3 and all(di < small_improve_tol for di in small_improvements):
            trigger_slp = True

        if trigger_slp:
            C_s, r_s, obj_s, slp_accepted, trust_h = _micro_slp_trust_step(
                centers, radii_lp, trust_h, h_max=h_max, h_floor=h_floor, eps=eps, eps_wall=eps_wall, prev_obj=obj
            )
            if slp_accepted:
                centers = C_s
                r = r_s
                obj = obj_s
            # Reset contact counters regardless of acceptance to avoid overly frequent SLP
            accepted_contact_since_slp = 0
            small_improvements.clear()

    # 4) Final exact LP for radii and a light polish
    r_final = radii_lp.solve(centers)
    r_final = np.nan_to_num(r_final, nan=0.0, posinf=0.0, neginf=0.0)
    r_final[r_final < 0] = 0.0
    centers = np.clip(centers, eps, 1.0 - eps)

    return centers, r_final


# ----------------------------
# Seeding strategies
# ----------------------------
def _hex_seed_26(eps: float = 1e-3) -> np.ndarray:
    """Edge-fitted hexagonal seeding for 26 centers within [eps, 1-eps]^2.

    Pattern: five rows with counts [6,5,5,5,5]; horizontal spacing dx fits 6 columns
    exactly between the eps margins; vertical spacing dy = sqrt(3)/2 * dx; rows with 5
    columns are staggered by dx/2. Vertically centered using y0 = 0.5 - 2*dy.
    """
    N = 26
    centers = np.zeros((N, 2), dtype=float)

    # Layout: rows [6,5,5,5,5]
    row_counts = [6, 5, 5, 5, 5]
    R = len(row_counts)
    assert sum(row_counts) == N

    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx

    # Vertical centering: y rows at y0 + k*dy for k=0..4
    y0 = 0.5 - 2.0 * dy

    idx = 0
    for k, n in enumerate(row_counts):
        yk = y0 + k * dy
        # Determine stagger offset: 5-column rows are staggered by dx/2
        x_offset = 0.0
        if n == 5:
            x_offset = dx / 2.0
        # For 6-column row, leftmost center at eps
        # For 5-column row, leftmost center at eps + dx/2
        x_start = eps + x_offset
        for i in range(n):
            centers[idx, 0] = x_start + i * dx
            centers[idx, 1] = yk
            idx += 1

    # Clip to [eps, 1-eps] just in case
    centers = np.clip(centers, eps, 1.0 - eps)
    return centers


def _fallback_grid(N: int) -> np.ndarray:
    """Simple deterministic fallback grid seeding for other N."""
    m = int(np.ceil(np.sqrt(N)))
    xs = np.linspace(0.1, 0.9, m)
    ys = np.linspace(0.1, 0.9, m)
    pts = []
    for j in range(m):
        for i in range(m):
            if len(pts) < N:
                pts.append([xs[i], ys[j]])
    return np.array(pts, dtype=float)


# ----------------------------
# Linear Programming Solver
# ----------------------------
class SimplexLP:
    """Simplex solver for LPs in the form:
        maximize c^T x
        subject to A x <= b,  x >= 0, and (crucially) b >= 0.

    - Adds slack variables s >= 0 to get A x + I s = b.
    - Starts from the feasible BFS: x = 0, s = b.
    - Uses Bland's rule for pivoting to reduce cycling risk.
    - Returns (status, x_opt, obj_val). Status in {"optimal", "unbounded", "infeasible"}.

    Notes:
    - This solver assumes that all RHS b are nonnegative (>= -tol).
      Rows with slightly negative b (due to numeric roundoff) are clipped to zero.
    - Works well for small to mid-size problems typical in this task.
    """

    def __init__(self, c: np.ndarray, A: np.ndarray, b: np.ndarray, tol: float = 1e-10, max_iters: int = 100000):
        self.tol = tol
        self.max_iters = max_iters

        # Validate inputs
        c = np.asarray(c, dtype=float).ravel()
        A = np.asarray(A, dtype=float)
        b = np.asarray(b, dtype=float).ravel()
        m, n = A.shape
        assert c.shape[0] == n, "c dimension mismatch"
        assert b.shape[0] == m, "b dimension mismatch"

        # Force nonnegative RHS (clip tiny negatives to zero)
        b = np.maximum(b, 0.0)

        # Build tableau with slack variables
        # Columns: [x (n), s (m), rhs]
        self.m = m
        self.n = n
        T = np.zeros((m + 1, n + m + 1), dtype=float)
        # Constraint rows
        T[:m, :n] = A
        T[:m, n:n + m] = np.eye(m)
        T[:m, -1] = b
        # Objective row for maximization: coefficients = -c (so negative entries invite entering)
        T[m, :n] = -c
        T[m, n:n + m] = 0.0
        T[m, -1] = 0.0

        self.T = T
        # Basis initially: slack variable indices (n .. n+m-1)
        self.basic = list(range(n, n + m))
        self.nonbasic = list(range(n))  # x variables

    def _choose_entering(self) -> Optional[int]:
        """Choose entering column using Bland's rule: smallest index with negative reduced cost."""
        obj_row = self.T[self.m, :-1]
        # Negative coefficient means we can improve objective by increasing this var (since we maximize)
        candidates = [j for j, coeff in enumerate(obj_row) if coeff < -self.tol]
        if not candidates:
            return None
        return min(candidates)

    def _choose_leaving(self, enter_col: int) -> Optional[int]:
        """Minimum ratio test with Bland's rule tie-breaking."""
        rhs = self.T[:self.m, -1]
        col = self.T[:self.m, enter_col]
        ratios: List[Tuple[float, int]] = []
        for i in range(self.m):
            a = col[i]
            if a > self.tol:  # positive coefficient => can increase entering var
                ratios.append((rhs[i] / a, i))
        if not ratios:
            return None  # unbounded
        # Find minimal ratio, tie-break by smallest row index (Bland)
        min_ratio = min(r[0] for r in ratios)
        candidates = [i for (r, i) in ratios if r <= min_ratio + 1e-12]
        return min(candidates)

    def _pivot(self, row: int, col: int) -> None:
        """Perform pivot at (row, col)."""
        T = self.T
        pivot = T[row, col]
        # Normalize pivot row
        T[row, :] = T[row, :] / pivot
        # Eliminate column in all other rows
        for i in range(T.shape[0]):
            if i == row:
                continue
            factor = T[i, col]
            if abs(factor) > 0:
                T[i, :] -= factor * T[row, :]
        # Update basis tracking
        self.basic[row] = col

    def solve(self) -> Tuple[str, Optional[np.ndarray], Optional[float]]:
        """Run simplex iterations."""
        iters = 0
        while iters < self.max_iters:
            iters += 1
            enter_col = self._choose_entering()
            if enter_col is None:
                # Optimal
                status = "optimal"
                x_opt = self._extract_solution()
                obj = self.T[self.m, -1]
                return status, x_opt, obj
            leave_row = self._choose_leaving(enter_col)
            if leave_row is None:
                # Unbounded objective
                return "unbounded", None, None
            self._pivot(leave_row, enter_col)
        return "infeasible", None, None  # iteration limit hit; treat as failure

    def _extract_solution(self) -> np.ndarray:
        """Extract the full solution for x variables (first n columns)."""
        m, n = self.m, self.n
        x = np.zeros(n, dtype=float)
        for i in range(m):
            col = self.basic[i]
            if col < n:
                # Basic original variable
                x[col] = self.T[i, -1]
        # Nonbasic original variables are zero
        # Clip small negatives to zero
        x[x < 0] = 0.0
        return x


# ----------------------------
# Radii LP Template (fixed A; only b changes with centers)
# ----------------------------
class _RadiiLPTemplate:
    """Prebuilt A-template for the exact radii LP with fixed centers.

    Model:
      max sum r_i
      s.t. r_i <= min_clear_i := min{x_i, y_i, 1-x_i, 1-y_i}           for i=1..N
           r_i + r_j <= ||c_i - c_j||                                   for all i<j
           r_i >= 0

    The A matrix is constant: identity for the first N rows and pairwise 1's for remaining rows.
    The RHS b is recomputed per solve from centers.
    """

    def __init__(self, N: int):
        self.N = N
        # Build pair list in lexicographic order (i<j)
        pairs: List[Tuple[int, int]] = []
        for i in range(N):
            for j in range(i + 1, N):
                pairs.append((i, j))
        self.pairs = pairs
        self.num_pairs = len(pairs)

        # Build constant A matrix
        m = N + self.num_pairs
        A = np.zeros((m, N), dtype=float)
        # Top N rows: identity
        A[:N, :N] = np.eye(N)
        # Pair rows: 1 in columns i and j
        for k, (i, j) in enumerate(pairs):
            A[N + k, i] = 1.0
            A[N + k, j] = 1.0
        self.A = A
        # Objective
        self.c = np.ones(N, dtype=float)

    def solve(self, centers: np.ndarray) -> np.ndarray:
        """Solve radii LP for given centers; returns r (N,), nonnegative."""
        N = self.N
        x = centers[:, 0]
        y = centers[:, 1]
        # Boundary caps: min clear distances to walls
        min_clear = np.minimum.reduce([x, y, 1.0 - x, 1.0 - y])
        # Pair distances
        b = np.zeros(N + self.num_pairs, dtype=float)
        b[:N] = min_clear
        for k, (i, j) in enumerate(self.pairs):
            d = float(np.linalg.norm(centers[i] - centers[j]))
            b[N + k] = d if d >= 0.0 else 0.0

        # Solve with simplex
        solver = SimplexLP(c=self.c, A=self.A, b=b, tol=1e-10, max_iters=400000)
        status, r, obj = solver.solve()
        if status != "optimal" or r is None:
            # Fallback: conservative greedy if simplex fails (should not happen).
            r = _greedy_radii_fallback(centers)
        # Ensure non-negativity and finiteness
        r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
        r[r < 0] = 0.0
        return r


# ----------------------------
# Active set and contact relaxation
# ----------------------------
def _extract_active_set(centers: np.ndarray, r: np.ndarray, tol_active: float = 1e-9):
    """Identify LP-active constraints: pair contacts and boundary ties.

    Returns a dict with:
      - 'active_pairs': list of (i,j,d_ij,tightness,u_ij) for pairs close to tight
      - 'boundary_ties': list per i of sides tied to the minimum and their inward normals
                         with a weight factor based on tightness to the wall
    """
    N = centers.shape[0]
    # Pair active set
    active_pairs = []
    for i in range(N):
        for j in range(i + 1, N):
            diff = centers[i] - centers[j]
            dij = float(np.hypot(diff[0], diff[1]))
            # Guard direction deterministically if almost coincident
            if dij > 1e-12:
                u = diff / dij
            else:
                u = np.array([1.0, 0.0], dtype=float)
            # Tight if r_i + r_j >= d_ij - tol
            if r[i] + r[j] >= dij - tol_active:
                # Weight tightness metric in [0,1]
                gap = dij - (r[i] + r[j])
                denom = max(dij, 1e-9)
                tightness = 1.0 - (gap / denom)
                if tightness < 0.0:
                    tightness = 0.0
                elif tightness > 1.0:
                    tightness = 1.0
                active_pairs.append((i, j, dij, tightness, u))

    # Boundary ties
    boundary_ties = [[] for _ in range(N)]
    x = centers[:, 0]
    y = centers[:, 1]
    for i in range(N):
        dists = np.array([x[i], y[i], 1.0 - x[i], 1.0 - y[i]], dtype=float)
        min_clear = float(np.min(dists))
        # If radius is close to the min clearance, sides achieving min are ties
        if r[i] >= min_clear - tol_active:
            gap_i = min_clear - r[i]
            denom = max(min_clear, 1e-9)
            tightness = 1.0 - (gap_i / denom)
            if tightness < 0.0:
                tightness = 0.0
            elif tightness > 1.0:
                tightness = 1.0
            # Identify sides at minimum, with inward normals
            # Order: [left(x), bottom(y), right(1-x), top(1-y)]
            sides = []
            if abs(dists[0] - min_clear) <= 1e-12:
                sides.append(np.array([1.0, 0.0]))   # inward from left wall
            if abs(dists[1] - min_clear) <= 1e-12:
                sides.append(np.array([0.0, 1.0]))   # inward from bottom wall
            if abs(dists[2] - min_clear) <= 1e-12:
                sides.append(np.array([-1.0, 0.0]))  # inward from right wall
            if abs(dists[3] - min_clear) <= 1e-12:
                sides.append(np.array([0.0, -1.0]))  # inward from top wall
            for nrm in sides:
                boundary_ties[i].append((nrm, tightness))

    return {"active_pairs": active_pairs, "boundary_ties": boundary_ties}


def _build_contact_motion_vectors(
    centers: np.ndarray,
    r: np.ndarray,
    active_info,
    out: Optional[np.ndarray] = None,
) -> bool:
    """Form dual/tightness-weighted contact motion vectors.

    - For each active pair (i,j): add w_pair * u_ij to i, and -w_pair * u_ij to j,
      where w_pair = tightness / max(d_ij,1e-9).
    - For boundary ties: add inward normals with w_bound = tightness based on wall tightness.
    - Degree normalization: divide each vector by (1 + total weight) to avoid hubs dominating.

    Returns True if at least one nonzero vector is produced, else False.
    """
    N = centers.shape[0]
    if out is None:
        v = np.zeros((N, 2), dtype=float)
    else:
        out.fill(0.0)
        v = out

    # Track accumulated weights per node for normalization
    wsum = np.zeros(N, dtype=float)

    # Active pairs
    for (i, j, dij, tightness, u) in active_info["active_pairs"]:
        denom = max(dij, 1e-9)
        w_pair = tightness / denom
        if w_pair > 0.0:
            v[i, 0] += w_pair * u[0]
            v[i, 1] += w_pair * u[1]
            v[j, 0] -= w_pair * u[0]
            v[j, 1] -= w_pair * u[1]
            wsum[i] += w_pair
            wsum[j] += w_pair

    # Boundary ties
    ties = active_info["boundary_ties"]
    for i in range(N):
        if not ties[i]:
            continue
        for (nrm, tightness) in ties[i]:
            w_bound = tightness
            if w_bound > 0.0:
                v[i, 0] += w_bound * nrm[0]
                v[i, 1] += w_bound * nrm[1]
                wsum[i] += w_bound

    # Degree normalization
    for i in range(N):
        denom = 1.0 + wsum[i]
        v[i, 0] /= denom
        v[i, 1] /= denom

    # Check if any nonzero
    any_nonzero = False
    for i in range(N):
        if v[i, 0] != 0.0 or v[i, 1] != 0.0:
            any_nonzero = True
            break
    return any_nonzero


# ----------------------------
# Micro SLP trust-region step (adaptive with true-feasibility backtracking)
# ----------------------------
def _micro_slp_trust_step(
    centers: np.ndarray,
    radii_lp: '_RadiiLPTemplate',
    h: float,
    h_max: float = 0.12,
    h_floor: float = 1e-5,
    eps: float = 1e-3,
    eps_wall: float = 1e-6,
    prev_obj: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray, float, bool, float]:
    """Adaptive trust-region micro SLP step with feasibility backtracking.

    - Builds linearized SLP with move budget |Δx_i|,|Δy_i| <= h and inward wall slack eps_wall.
    - Solves SLP; checks true feasibility of (r_lin, C+ΔC) against nonlinear constraints.
    - If infeasible, halves h and retries up to h_floor.
    - If feasible, recomputes exact radii at C+ΔC and accepts only if objective is nondecreasing.
    - Updates trust-region radius h for future calls based on improvement.

    Returns (centers_out, radii_out, obj_out, accepted, h_next).
    """
    C = centers.copy()
    N = C.shape[0]
    r_best = radii_lp.solve(C)
    obj_prev = float(np.sum(r_best)) if prev_obj is None else float(prev_obj)

    # Helper for variable indexing
    def var_indices(n: int):
        r0 = 0
        dxp0 = r0 + n
        dxm0 = dxp0 + n
        dyp0 = dxm0 + n
        dym0 = dyp0 + n
        total = dym0 + n
        return r0, dxp0, dxm0, dyp0, dym0, total

    # Build pair list once for determinism
    pairs = []
    for i in range(N):
        for j in range(i + 1, N):
            pairs.append((i, j))

    # Backtracking loop on trust region size h
    h_try = float(h)
    accepted = False
    C_out = C
    r_out = r_best
    obj_out = obj_prev
    h_next = h

    while h_try >= h_floor and not accepted:
        # Build SLP for current h_try
        r0, dxp0, dxm0, dyp0, dym0, nvar = var_indices(N)
        cvec = np.zeros(nvar, dtype=float)
        cvec[r0:r0 + N] = 1.0

        A_rows = []
        b_vals = []

        def add_row(coeffs: List[Tuple[int, float]], rhs: float):
            row = np.zeros(nvar, dtype=float)
            for j, v in coeffs:
                row[j] = v
            # Clip RHS to nonnegative for simplex feasibility
            b_vals.append(max(0.0, rhs))
            A_rows.append(row)

        x = C[:, 0]
        y = C[:, 1]

        # Wall constraints with inward slack eps_wall
        # r_i <= x_i + Δx_i - eps_wall
        for i in range(N):
            add_row([(r0 + i, 1.0), (dxp0 + i, -1.0), (dxm0 + i, 1.0)], x[i] - eps_wall)
            add_row([(r0 + i, 1.0), (dyp0 + i, -1.0), (dym0 + i, 1.0)], y[i] - eps_wall)
            add_row([(r0 + i, 1.0), (dxp0 + i, 1.0), (dxm0 + i, -1.0)], 1.0 - x[i] - eps_wall)
            add_row([(r0 + i, 1.0), (dyp0 + i, 1.0), (dym0 + i, -1.0)], 1.0 - y[i] - eps_wall)

        # Pairwise constraints (linearized conservative)
        for (i, j) in pairs:
            diff = C[i] - C[j]
            dij = float(np.hypot(diff[0], diff[1]))
            if dij > 1e-12:
                u = diff / dij
            else:
                u = np.array([0.0, 0.0], dtype=float)
            ux, uy = float(u[0]), float(u[1])
            # r_i + r_j - u^T(Δc_i - Δc_j) <= d_ij
            terms = [
                (r0 + i, 1.0),
                (r0 + j, 1.0),
                (dxp0 + i, -ux),
                (dxm0 + i, ux),
                (dyp0 + i, -uy),
                (dym0 + i, uy),
                (dxp0 + j, ux),
                (dxm0 + j, -ux),
                (dyp0 + j, uy),
                (dym0 + j, -uy),
            ]
            add_row(terms, dij)

        # Move budgets and keep centers inside [0,1]
        for i in range(N):
            # Budget bounds
            add_row([(dxp0 + i, 1.0)], h_try)
            add_row([(dxm0 + i, 1.0)], h_try)
            add_row([(dyp0 + i, 1.0)], h_try)
            add_row([(dym0 + i, 1.0)], h_try)
            # Bounds for resulting centers (linear)
            add_row([(dxp0 + i, 1.0), (dxm0 + i, -1.0)], 1.0 - x[i])   # x + Δx <= 1
            add_row([(dxp0 + i, -1.0), (dxm0 + i, 1.0)], x[i])         # -(x+Δx) <= 0 => Δx >= -x
            add_row([(dyp0 + i, 1.0), (dym0 + i, -1.0)], 1.0 - y[i])   # y + Δy <= 1
            add_row([(dyp0 + i, -1.0), (dym0 + i, 1.0)], y[i])         # Δy >= -y

        A = np.array(A_rows, dtype=float)
        b = np.array(b_vals, dtype=float)

        solver = SimplexLP(c=cvec, A=A, b=b, tol=1e-10, max_iters=600000)
        status, sol, obj_lin = solver.solve()
        if status != "optimal" or sol is None:
            # If LP fails (unlikely), shrink trust region and retry
            h_try *= 0.5
            continue

        # Retrieve proposed move and radii from SLP
        dx = sol[dxp0:dxp0 + N] - sol[dxm0:dxm0 + N]
        dy = sol[dyp0:dyp0 + N] - sol[dym0:dym0 + N]
        r_lin = sol[r0:r0 + N]

        C_new = C.copy()
        C_new[:, 0] = C_new[:, 0] + dx
        C_new[:, 1] = C_new[:, 1] + dy
        # For robustness, clip to open-margin box (very small displacement near walls)
        C_new[:, 0] = np.clip(C_new[:, 0], eps, 1.0 - eps)
        C_new[:, 1] = np.clip(C_new[:, 1], eps, 1.0 - eps)

        # True-feasibility check of linearized solution against nonlinear constraints
        if not _check_true_feasibility(C_new, r_lin, eps_wall=eps_wall):
            # Over-optimistic linearization; shrink trust region and retry
            h_try *= 0.5
            continue

        # Compute exact radii objective at updated centers; monotone acceptance
        r_exact = radii_lp.solve(C_new)
        obj_new = float(np.sum(r_exact))
        if obj_new >= obj_prev - 1e-12:
            # Accept
            C_out = C_new
            r_out = r_exact
            obj_out = obj_new
            # Adapt trust region
            improve = obj_new - obj_prev
            if improve > 1e-8:
                h_next = min(h_try * 1.2, h_max)
            else:
                h_next = max(h_try * 0.9, h_floor)
            accepted = True
        else:
            # Reject due to monotone policy; shrink for next attempt
            h_try *= 0.5

    # If never accepted, modestly shrink trust region to be conservative next time
    if not accepted:
        h_next = max(h * 0.8, h_floor)
    return C_out, r_out, obj_out, accepted, h_next


def _check_true_feasibility(centers: np.ndarray, radii: np.ndarray, eps_wall: float = 1e-6) -> bool:
    """Verify that radii and centers satisfy true geometric constraints with inward slack."""
    N = centers.shape[0]
    # Wall checks
    x = centers[:, 0]
    y = centers[:, 1]
    min_clear = np.minimum.reduce([x, y, 1.0 - x, 1.0 - y]) - eps_wall
    if np.any(radii > min_clear + 1e-12):
        return False
    # Pairwise checks
    for i in range(N):
        for j in range(i + 1, N):
            dij = float(np.hypot(centers[i, 0] - centers[j, 0], centers[i, 1] - centers[j, 1]))
            if radii[i] + radii[j] > dij + 1e-12:
                return False
    return True


# ----------------------------
# Generic LP for arbitrary N (fallback path)
# ----------------------------
def _lp_radii_for_fixed_centers(centers: np.ndarray) -> np.ndarray:
    """Solve the exact LP for radii with fixed centers (generic N):
        maximize sum r_i
        s.t. r_i >= 0
             r_i <= distances to walls (four constraints per circle)
             r_i + r_j <= ||c_i - c_j|| for all pairs
    """
    N = centers.shape[0]
    # Variables: r (N), all nonnegative
    c = np.ones(N, dtype=float)

    A_rows = []
    b_vals = []

    # Wall constraints (separately for each wall for numerical robustness)
    x = centers[:, 0]
    y = centers[:, 1]
    # r_i <= x_i
    for i in range(N):
        row = np.zeros(N)
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(x[i])
    # r_i <= y_i
    for i in range(N):
        row = np.zeros(N)
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(y[i])
    # r_i <= 1 - x_i
    for i in range(N):
        row = np.zeros(N)
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(1.0 - x[i])
    # r_i <= 1 - y_i
    for i in range(N):
        row = np.zeros(N)
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(1.0 - y[i])

    # Pairwise disjointness: r_i + r_j <= d_ij
    for i in range(N):
        for j in range(i + 1, N):
            row = np.zeros(N)
            row[i] = 1.0
            row[j] = 1.0
            d = float(np.linalg.norm(centers[i] - centers[j]))
            A_rows.append(row)
            b_vals.append(d)

    A = np.array(A_rows, dtype=float)
    b = np.array(b_vals, dtype=float)

    # Solve via Simplex
    solver = SimplexLP(c=c, A=A, b=b, tol=1e-10, max_iters=200000)
    status, r, obj = solver.solve()
    if status != "optimal" or r is None:
        # Fallback: conservative greedy if simplex fails (should not happen).
        r = _greedy_radii_fallback(centers)
    # Ensure non-negativity and finiteness
    r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
    r[r < 0] = 0.0
    return r


# ----------------------------
# Fallback greedy radii (only used if LP fails unexpectedly)
# ----------------------------
def _greedy_radii_fallback(centers: np.ndarray) -> np.ndarray:
    """Conservative greedy radii computation used only as a last resort."""
    N = centers.shape[0]
    x = centers[:, 0]
    y = centers[:, 1]
    r = np.minimum.reduce([x, y, 1 - x, 1 - y])
    # Iteratively enforce pairwise constraints by uniform shrinkage on pairs
    for i in range(N):
        for j in range(i + 1, N):
            d = float(np.linalg.norm(centers[i] - centers[j]))
            s = r[i] + r[j]
            if s > d and s > 0:
                scale = d / s
                r[i] *= scale
                r[j] *= scale
    r[r < 0] = 0.0
    return r


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
SMALL_IMPROV_STREAK_MAX = 3

# Micro-SLP parameters
MICRO_SLP_K = 3            # run micro-SLP every K accepted moves
MICRO_SLP_H_INIT_SCALE = 0.05  # initial trust-region side (fraction of min(dx,dy))
MICRO_SLP_BACKTRACK = 0.7
MICRO_SLP_MIN_H_SCALE = 1e-4


def hex_seed_26() -> Tuple[np.ndarray, float, float]:
    """Create a deterministic hexagonal-like seed of 26 centers inside [0,1]^2.

    Layout: 5 rows with counts [5, 5, 6, 5, 5], y symmetric around 0.5.
    Center spacing: dx = (1 - 2*eps)/5; dy = (sqrt(3)/2)*dx.
    Offsets: middle row (k=2) starts at eps, others offset by 0.5*dx.

    Returns:
        centers: (26,2) array
        dx, dy: spacings used (useful for step sizing)
    """
    eps = HEX_EPS
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx

    rows = [5, 5, 6, 5, 5]
    y0 = (0.5 - 2.0 * dy)  # k=0 row
    centers: List[List[float]] = []
    for k, cnt in enumerate(rows):
        yk = y0 + k * dy
        if k == 2:
            # 6 centers, aligned
            xs = [eps + c * dx for c in range(cnt)]
        else:
            # 5 centers, offset by 0.5 dx
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


def build_active_motion(centers: np.ndarray, radii: np.ndarray) -> np.ndarray:
    """Construct a motion field that nudges centers away from active/near-active constraints.

    Returns:
        v: (N,2) normalized motion directions per center (unit norm or zero)
    """
    N = centers.shape[0]
    x = centers[:, 0]
    y = centers[:, 1]
    v = np.zeros_like(centers)
    deg = np.zeros(N, dtype=float)

    # Boundary constraints activeness
    # r <= x (left): inward normal +x
    active_left = np.where(radii >= x - TOL)[0]
    for i in active_left:
        v[i, 0] += BASE_W_BOUNDARY
        deg[i] += 1.0
    # r <= y (bottom): inward normal +y
    active_bottom = np.where(radii >= y - TOL)[0]
    for i in active_bottom:
        v[i, 1] += BASE_W_BOUNDARY
        deg[i] += 1.0
    # r <= 1 - x (right): inward normal -x
    active_right = np.where(radii >= (1.0 - x) - TOL)[0]
    for i in active_right:
        v[i, 0] -= BASE_W_BOUNDARY
        deg[i] += 1.0
    # r <= 1 - y (top): inward normal -y
    active_top = np.where(radii >= (1.0 - y) - TOL)[0]
    for i in active_top:
        v[i, 1] -= BASE_W_BOUNDARY
        deg[i] += 1.0

    # Pairwise near-active constraints
    tol_pair = max(TOL, PAIR_TOL_MIN)
    for i in range(N):
        for j in range(i + 1, N):
            # Slack s_ij = d_ij - (r_i + r_j)
            dij_vec = centers[i] - centers[j]
            dij = float(np.linalg.norm(dij_vec))
            if dij <= DELTA:
                continue
            s = dij - float(radii[i] + radii[j])
            if s <= tol_pair:
                n_ij = dij_vec / dij
                w = min(W_MAX, 1.0 / (s + SIGMA)) * (1.0 / max(dij, DELTA))
                # Add contributions with capped accumulation
                v_i = v[i] + w * n_ij
                v_j = v[j] - w * n_ij
                # Cap per node accumulation
                if np.linalg.norm(v_i) > CAP_PER_NODE:
                    v_i = v_i / np.linalg.norm(v_i) * CAP_PER_NODE
                if np.linalg.norm(v_j) > CAP_PER_NODE:
                    v_j = v_j / np.linalg.norm(v_j) * CAP_PER_NODE
                v[i] = v_i
                v[j] = v_j
                deg[i] += 1.0
                deg[j] += 1.0

    # Normalize per node
    for i in range(N):
        if deg[i] > 0.0:
            v[i] /= max(deg[i], 1.0)
        norm = np.linalg.norm(v[i])
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

    # Deterministic loop
    while accepts < MAX_ACCEPTS and small_improv_streak < SMALL_IMPROV_STREAK_MAX:
        # Build motion
        v = build_active_motion(centers, radii)
        # If all zeros, break
        if not np.any(np.linalg.norm(v, axis=1) > 0.0):
            break

        # Backtracking line search
        alpha = s0
        accepted = False
        while alpha >= alpha_min:
            trial_centers = centers + alpha * v
            np.clip(trial_centers, HEX_EPS, 1.0 - HEX_EPS, out=trial_centers)
            radii_trial, obj_trial, _ = compute_max_radii_lp(trial_centers)
            if obj_trial >= obj - OBJ_TOL:
                # Accept
                centers = trial_centers
                radii = radii_trial
                accepts += 1
                since_micro += 1
                # Improvement tracking
                if obj_trial - obj < SMALL_IMPROV_THRESH:
                    small_improv_streak += 1
                else:
                    small_improv_streak = 0
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
