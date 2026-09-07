Deterministic LP + active-constraint motion with a hex seed tweak ([5,5,6,5,5]) and doubled boundary-normal weighting delivered modest, reliable gains in sum of radii for 26-circle packing.

- Centered dense row hex seed + inward-biased active motion: Placing the 6-column hexagonal row in the vertical middle (row counts [5,5,6,5,5]) and doubling the weight of active boundary normals relative to pairwise normals reduced boundary bottlenecks in the deterministic LP-plus-active-motion pipeline, enabling boundary-limited circles to move inward where the LP could grow them. With a small deterministic budget increase (max_outer raised to 60 and backtracking iterations to 16) while keeping monotone backtracking and all tolerances unchanged, this variant achieved a higher sum of radii (2.5254693135455444) than its parent (2.5152447105789904) at validity 1.0. Reuse this pattern when hex seeds interact with box constraints: center the densest row away from boundaries and upweight boundary pushes to steer centers inward, preserving determinism and the existing LP solver setup.
- Centered Hex Seed + Soft-Active Inward Guidance: Combining a centered hexagonal seed (row counts [5, 5, 6, 5, 5]) with soft-active guidance that weights near-binding pair and boundary constraints (tol_active = 1e−9; tol_soft_pair = tol_soft_bnd = 1e−4), plus per-center direction normalization, produced anticipatory and stable motion under a monotone backtracking regime.
- Centered Hex Seed + Soft-Active Inward Guidance: An inward boundary bias with base weight w_bnd_base = 2.0 and a conservative cap w_bnd_max = 3.0 prioritized boundary relief without oversteering, while a deterministic LP pipeline (primal simplex with Bland’s rule; deterministic tableau Bland fallback) preserved strict reproducibility.
- Centered Hex Seed + Soft-Active Inward Guidance: Under these choices, the algorithm achieved sum_radii = 2.530079116705978 with validity = 1.0, outperforming Parent 1 (2.5254693135455444) and Parent 2 (2.515512731603428).
- Centered Hex Seed + Soft-Active Inward Guidance: Future designs should reuse soft-active weighting of near-active constraints together with a capped inward boundary bias and per-center vector normalization on a balanced centered hex seed to reduce zig-zagging and improve convergence while maintaining determinism.
- construct_packing acceptance loop: Compute improvement = trial_sum − prev_sum before updating prev_sum and update the small_improve_streak using a 1e-10 threshold to prevent a zeroed improvement from prematurely tripping the stall detector, enabling the accept_budget to be used and yielding a higher objective (score 2.561868539185472 versus parent 2.155255953853201 with validity 1.0).
- build_active_motion boundary weighting: Upweight active boundary normals with w_bnd_base = 2.0 and cap the cumulative per-circle boundary weight at w_bnd_max = 3.0 to bias boundary-limited circles inward, creating slack that the LP can convert into larger radii while preserving determinism.
- construct_packing per-center direction normalization: After dividing by deg_safe, normalize each center’s direction vector to unit length when its norm is positive before applying the step size to reduce zig-zagging and improve acceptance under monotone backtracking without changing determinism.
- Active constraint tolerance and accept budget settings: Loosen active_tol from 1e-12 to 1e-10 in both motion building and acceptance, and increase accept_budget from 90 to 120, so the loop can recognize near-active constraints and harvest more improvements unlocked by the corrected streak logic.

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

This implements a two-stage deterministic optimization:
  1) For fixed centers, solve an LP that maximizes the sum of radii under
     boundary and non-overlap constraints using a primal simplex with Bland's rule.
     A deterministic tableau simplex is used as a fallback to ensure robustness.
  2) Move centers along normals of the LP’s active constraints with monotone
     backtracking, re-solving the LP after each tentative move.

All choices are deterministic; no randomness is used.
"""

import json
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    This constructor is deterministic and follows the algorithm described:
    - Hexagonal edge-fitted seeding (with the 6-column row in the vertical middle).
    - LP solve (maximize sum of radii) for fixed centers via primal simplex with Bland's rule.
      Fallback to a deterministic tableau Bland simplex if needed.
    - Active-constraint-guided center motions with monotone backtracking.
      Boundary active normals are weighted more strongly to bias motion inward.

    Parameters
    ----------
    num_circles : int
        Number of circles to place. This implementation targets 26 exactly.

    Returns
    -------
    centers : np.ndarray, shape (26, 2)
    radii : np.ndarray, shape (26,)
    """
    if num_circles != 26:
        # This candidate targets exactly 26 circles as required by the task.
        raise ValueError("This constructor is designed for exactly 26 circles.")

    # 1) Hexagonal edge-fitted seeding (deterministic)
    eps = 1e-3
    centers, dx, dy = _hex_seed_26(eps=eps)
    # Defensive clip, even though seed construction is already within bounds.
    centers = np.clip(centers, eps, 1.0 - eps)

    # 2) Initial LP solve for radii (fixed centers)
    radii, obj = _lp_maximize_sum_radii(centers)

    # 3) Active-constraint-guided motion with monotone backtracking
    # Step schedule
    s = 0.1 * min(dx, dy)
    rho = 0.95  # step decay per accepted outer iteration
    tol_obj = 1e-12
    tol_active = 1e-9
    # Increased outer iterations to allow inward-biased directions to settle
    max_outer = 60
    stagnation_count = 0

    for _ in range(max_outer):
        # Build normalized movement directions from active constraints
        v = _active_constraint_direction(centers, radii, tol_active=tol_active)

        # If no active movement direction (all zero), stop
        if not np.any(np.linalg.norm(v, axis=1) > 0):
            break

        # Monotone backtracking line-search along v
        accepted = False
        step_len = s
        cand_obj = obj
        # Slightly larger backtracking budget for robustness
        for _bt in range(16):
            cand_centers = centers + step_len * v
            # Keep centers inside open margins to avoid boundary drift out of domain.
            cand_centers = np.clip(cand_centers, eps, 1.0 - eps)

            cand_radii, cand_obj = _lp_maximize_sum_radii(cand_centers)

            # Accept if objective does not decrease
            if cand_obj + tol_obj >= obj:
                centers = cand_centers
                radii = cand_radii
                accepted = True
                break
            step_len *= 0.5

        if not accepted:
            # Could not find a non-decreasing step; stop
            break

        # Decrease base step size slightly for stability
        s *= rho

        # Stagnation check: if improvement is tiny, count and possibly stop
        if cand_obj - obj < 1e-8:
            stagnation_count += 1
        else:
            stagnation_count = 0
        obj = cand_obj
        if stagnation_count >= 3:
            break

    # Final LP and hygiene clamp
    radii, _ = _lp_maximize_sum_radii(centers)
    radii = np.clip(radii, 0.0, np.inf)

    return centers, radii


def _hex_seed_26(eps: float = 1e-3) -> Tuple[np.ndarray, float, float]:
    """Construct a deterministic hexagonal-lattice seed for exactly 26 centers.

    Layout (tweaked):
      - 5 rows with counts [5, 5, 6, 5, 5] (place the 6-column row in the vertical middle)
      - dx = (1 - 2*eps)/5
      - dy = (sqrt(3)/2) * dx
      - rows centered vertically
      - Placement rules unchanged:
          * 6-column rows: x = eps + c*dx, c = 0..5
          * 5-column rows: x = eps + dx/2 + c*dx, c = 0..4

    Parameters
    ----------
    eps : float
        Open margin from the boundary.

    Returns
    -------
    centers : np.ndarray, shape (26, 2)
    dx : float
    dy : float
    """
    rows = 5
    # Hex seed tweak: move 6-column row to the middle
    counts = [5, 5, 6, 5, 5]
    assert sum(counts) == 26

    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx
    # Center rows vertically
    y0 = 0.5 - 0.5 * (rows - 1) * dy

    centers: List[List[float]] = []
    for k in range(rows):
        y = y0 + k * dy
        if counts[k] == 6:
            # 6-column row: integer multiples starting at eps
            xs = [eps + c * dx for c in range(6)]
        else:
            # 5-column rows: half-shift
            xs = [eps + 0.5 * dx + c * dx for c in range(5)]
        for x in xs:
            centers.append([x, y])

    centers_arr = np.asarray(centers, dtype=float)
    # Clip defensively
    centers_arr = np.clip(centers_arr, eps, 1.0 - eps)
    return centers_arr, dx, dy


def _lp_maximize_sum_radii(centers: np.ndarray) -> Tuple[np.ndarray, float]:
    """Solve LP: maximize sum_i r_i subject to
       - r_i >= 0
       - r_i <= min(x_i, y_i, 1-x_i, 1-y_i)
       - r_i + r_j <= ||c_i - c_j||   for all i < j

    Deterministic pipeline:
      - Primary: primal simplex with explicit basis matrix and Bland's rule
        (restrict entering to radii variables only).
      - Fallback: deterministic tableau-based Bland simplex.

    Parameters
    ----------
    centers : np.ndarray, shape (n, 2)

    Returns
    -------
    radii : np.ndarray, shape (n,)
    obj : float
        Optimal objective value (sum of radii).
    """
    n = centers.shape[0]
    # Build constraints in deterministic order:
    # First boundary constraints in index order, then pair constraints in lex order.
    # A r <= b
    x = centers[:, 0]
    y = centers[:, 1]
    up = np.minimum.reduce([x, y, 1.0 - x, 1.0 - y])  # upper bounds for r_i
    m1 = n

    # Pair constraints
    pairs: List[Tuple[int, int]] = []
    dists: List[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((i, j))
            d = float(np.linalg.norm(centers[i] - centers[j]))
            dists.append(d)
    m2 = len(pairs)
    m = m1 + m2

    # Build A and b for A r <= b
    A = np.zeros((m, n), dtype=float)
    b = np.zeros((m,), dtype=float)

    # Boundary constraints rows 0..n-1 : r_i <= up_i
    for i in range(n):
        A[i, i] = 1.0
        b[i] = max(0.0, float(up[i]))

    # Pair constraints rows n..n+m2-1 : r_i + r_j <= ||ci-cj||
    for k, (ij, dist) in enumerate(zip(pairs, dists), start=n):
        i, j = ij
        A[k, i] = 1.0
        A[k, j] = 1.0
        b[k] = max(0.0, float(dist))

    # Primary solver: basis-matrix primal simplex with Bland's rule
    radii, obj, ok = _simplex_primary(A, b)

    if not ok:
        # Fallback: deterministic tableau Bland simplex
        radii, obj = _simplex_tableau(A, b)

    # Numerical hygiene: clamp tiny negatives to zero
    radii = np.clip(radii, 0.0, np.inf)
    obj = float(np.sum(radii))
    return radii, obj


def _simplex_primary(A: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, float, bool]:
    """Primary LP solver: primal simplex using an explicit basis matrix B and Bland's rule.

    Variables: z = [r (n), s (m)], s are slacks. Start with slacks basic (identity).
    Entering variables restricted to r only.

    Returns radii, obj, success_flag.
    """
    m, n = A.shape
    total_vars = n + m

    # Basis starts as the slack variables (identity)
    B_vars = np.arange(n, n + m, dtype=int)  # indices into z
    # Precompute A column references for r-variables
    A_cols = [A[:, j].copy() for j in range(n)]

    # Objective costs: c for z
    c = np.zeros((total_vars,), dtype=float)
    c[:n] = 1.0  # maximize sum of radii

    # Tolerances
    tol_rc = 1e-12
    tol_dir = 1e-12
    ratio_tie_tol = 1e-12

    # Helper to build B matrix from current basis
    def build_B_matrix(Bv: np.ndarray) -> np.ndarray:
        B = np.zeros((m, m), dtype=float)
        for col, var in enumerate(Bv):
            if var < n:
                B[:, col] = A_cols[var]
            else:
                row = var - n
                B[row, col] = 1.0
        return B

    # Solve linear system with fallback to least-squares for robustness
    def solve_linear(mat: np.ndarray, rhs: np.ndarray) -> np.ndarray:
        try:
            sol = np.linalg.solve(mat, rhs)
        except np.linalg.LinAlgError:
            sol, *_ = np.linalg.lstsq(mat, rhs, rcond=None)
        return sol

    # Keep a set for faster "is in basis" checks among r variables (linear scan is fine)
    def in_basis_r(var_idx: int, Bv: np.ndarray) -> bool:
        for v in Bv:
            if v == var_idx:
                return True
        return False

    max_pivots = 2000
    pivots = 0
    success = True
    while True:
        pivots += 1
        if pivots > max_pivots:
            success = False
            break  # fallback will handle

        # Build B and compute x_B and dual multipliers w via B^T w = c_B
        B = build_B_matrix(B_vars)
        c_B = np.array([1.0 if var < n else 0.0 for var in B_vars], dtype=float)

        # Dual multipliers from B^T w = c_B
        try:
            w = solve_linear(B.T, c_B)
        except Exception:
            success = False
            break
        if not np.all(np.isfinite(w)):
            success = False
            break

        # Compute reduced costs for candidate entering variables (r vars not in basis)
        entering_candidates = []
        for j in range(n):
            if in_basis_r(j, B_vars):
                continue
            a_j = A_cols[j]
            # reduced cost: c_j - w^T a_j; here c_j = 1
            rc = 1.0 - float(np.dot(w, a_j))
            if rc > tol_rc:
                entering_candidates.append(j)

        if not entering_candidates:
            # Optimal: no positive reduced costs
            break

        # Bland's rule: pick smallest index amongst candidates
        entering_idx = min(entering_candidates)

        # Compute direction d = B^{-1} a_enter and current basic solution x_B = B^{-1} b
        a_enter = A_cols[entering_idx]
        d = solve_linear(B, a_enter)
        x_B = solve_linear(B, b)
        if not (np.all(np.isfinite(d)) and np.all(np.isfinite(x_B))):
            success = False
            break

        # Ratio test: theta = min_i x_B[i] / d[i] over d[i] > tol
        theta = np.inf
        leaving_row = None
        for i_row, d_i in enumerate(d):
            if d_i > tol_dir:
                ratio = x_B[i_row] / d_i
                if ratio < theta - ratio_tie_tol:
                    theta = ratio
                    leaving_row = i_row
                elif abs(ratio - theta) <= ratio_tie_tol:
                    # Tie on ratio: choose smallest basic var index (lexicographic)
                    if leaving_row is None or B_vars[i_row] < B_vars[leaving_row]:
                        leaving_row = i_row

        if leaving_row is None or not np.isfinite(theta):
            # Unbounded or numerical failure in this bounded problem -> fallback
            success = False
            break

        # Pivot: replace leaving basic var with entering var
        B_vars[leaving_row] = entering_idx

    # Recover primal solution z from final basis
    radii = np.zeros((n,), dtype=float)
    if success:
        B = build_B_matrix(B_vars)
        x_B = solve_linear(B, b)
        if not np.all(np.isfinite(x_B)):
            success = False
        else:
            z = np.zeros((n + m,), dtype=float)
            for col, var in enumerate(B_vars):
                z[var] = x_B[col]
            radii = z[:n].copy()

    obj = float(np.sum(np.clip(radii, 0.0, np.inf)))
    return radii, obj, success


def _simplex_tableau(A: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, float]:
    """Fallback solver: deterministic tableau-based Bland primal simplex.

    Standard form:
      maximize c^T r
      subject to A r + s = b, r >= 0, s >= 0
      with c_j = 1 for r-variables and 0 for slacks.

    Entering variables restricted to r only; Bland's rule with lexicographic
    leaving rule to avoid cycling.
    """
    m, n = A.shape
    total_vars = n + m

    # Build tableau T of shape (m+1) x (total_vars+1), last column is RHS
    # Initialize with identity slacks basis
    T = np.zeros((m + 1, total_vars + 1), dtype=float)

    # Constraint rows: [A | I | b]
    T[:m, :n] = A
    for i in range(m):
        T[i, n + i] = 1.0
    T[:m, -1] = b

    # Objective row (row m): reduced costs for non-basic variables.
    # With initial basis slacks (cost 0), reduced costs equal c (for r vars),
    # and 0 for slack vars. We'll store the reduced costs directly in row m.
    T[m, :n] = 1.0  # c for r-variables
    # Slack costs 0, RHS (objective value) initially 0

    # Basic variables per row (initially slacks)
    B = np.arange(n, n + m, dtype=int)

    tol_rc = 1e-12
    tol_dir = 1e-12
    ratio_tie_tol = 1e-12
    max_pivots = 10000
    pivots = 0

    def pivot(row: int, col: int):
        """Perform a pivot on (row, col): make T[row, col] == 1, zero it out elsewhere."""
        piv = T[row, col]
        # Scale pivot row
        T[row, :] = T[row, :] / piv
        # Eliminate column col in all other rows
        for r in range(m + 1):
            if r == row:
                continue
            factor = T[r, col]
            if factor != 0.0:
                T[r, :] -= factor * T[row, :]

    while True:
        pivots += 1
        if pivots > max_pivots:
            break  # give up; we'll read whatever solution we have

        # Identify entering variable among r-vars using Bland: smallest index j with rc_j > tol
        entering = None
        for j in range(n):
            rc = T[m, j]
            if rc > tol_rc:  # positive reduced cost improves objective
                entering = j
                break

        if entering is None:
            # Optimal: no positive reduced costs
            break

        # Determine leaving row via deterministic minimum ratio test
        best_ratio = np.inf
        leaving_row = None
        for i in range(m):
            a_ij = T[i, entering]
            if a_ij > tol_dir:
                ratio = T[i, -1] / a_ij
                if ratio < best_ratio - ratio_tie_tol:
                    best_ratio = ratio
                    leaving_row = i
                elif abs(ratio - best_ratio) <= ratio_tie_tol:
                    # lexicographic tie-breaking by basic index
                    if leaving_row is None or B[i] < B[leaving_row]:
                        leaving_row = i

        if leaving_row is None or not np.isfinite(best_ratio):
            # Unbounded or numerical issue; stop
            break

        # Pivot and update basis
        pivot(leaving_row, entering)
        B[leaving_row] = entering

    # Extract solution: basic vars have their RHS; nonbasic are zero
    z = np.zeros((total_vars,), dtype=float)
    for i in range(m):
        var = B[i]
        if 0 <= var < total_vars:
            z[var] = T[i, -1]

    radii = z[:n].copy()
    radii = np.clip(radii, 0.0, np.inf)
    obj = float(np.sum(radii))
    return radii, obj


def _active_constraint_direction(
    centers: np.ndarray, radii: np.ndarray, tol_active: float = 1e-9
) -> np.ndarray:
    """Construct movement directions along the normals of active constraints.

    Active pair constraints: r_i + r_j >= ||c_i - c_j|| - tol_active
    Active boundary constraints: r_i >= min_clearance - tol_active

    The direction accumulates inverse-distance–weighted separating normals for pairs,
    and inward normals for active boundaries. Boundary normals are weighted by a
    factor of 2.0 to bias motion inward. Each center's vector is normalized.

    Parameters
    ----------
    centers : np.ndarray, shape (n, 2)
    radii : np.ndarray, shape (n,)
    tol_active : float

    Returns
    -------
    v : np.ndarray, shape (n, 2)
        Normalized movement vectors per center; zero rows mean no movement.
    """
    n = centers.shape[0]
    v = np.zeros_like(centers)
    tiny = 1e-9

    # Pairwise active constraints
    for i in range(n):
        ci = centers[i]
        for j in range(i + 1, n):
            cj = centers[j]
            diff = ci - cj
            dist = float(np.hypot(diff[0], diff[1]))
            # Active if sum radii reaches distance within tolerance
            if radii[i] + radii[j] >= dist - tol_active:
                if dist > tiny:
                    n_ij = diff / dist
                    w = 1.0 / max(dist, tiny)
                    v[i] += w * n_ij
                    v[j] -= w * n_ij
                # If dist ~ 0, skip adding; boundary and other acts will regularize.

    # Boundary active constraints per center (corner-aware ties)
    boundary_weight = 2.0  # inward-biased weighting for boundary normals
    for i in range(n):
        x, y = centers[i]
        cl = x
        cb = y
        cr = 1.0 - x
        ct = 1.0 - y
        minc = min(cl, cb, cr, ct)
        if radii[i] >= minc - tol_active:
            # add inward normals for all sides tied at minimum
            if abs(cl - minc) <= tol_active:
                v[i] += boundary_weight * np.array([1.0, 0.0])
            if abs(cb - minc) <= tol_active:
                v[i] += boundary_weight * np.array([0.0, 1.0])
            if abs(cr - minc) <= tol_active:
                v[i] += boundary_weight * np.array([-1.0, 0.0])
            if abs(ct - minc) <= tol_active:
                v[i] += boundary_weight * np.array([0.0, -1.0])

    # Normalize per-center; replace non-finite with zero
    norms = np.linalg.norm(v, axis=1)
    for i in range(n):
        if norms[i] > 0.0 and np.isfinite(norms[i]):
            v[i] /= norms[i]
        else:
            v[i] = np.array([0.0, 0.0], dtype=float)
    return v
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

This implements a two-stage deterministic optimization:
  1) For fixed centers, solve an LP that maximizes the sum of radii under
     boundary and non-overlap constraints using a primal simplex with Bland's rule.
     A deterministic tableau simplex is used as a fallback to ensure robustness.
  2) Move centers guided by soft-active constraint normals (with an inward bias
     for boundary constraints and a conservative cap), using monotone backtracking.
     Directions are per-center normalized so weights steer direction, not step size.

All choices are deterministic; no randomness is used.
"""

import json
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    This constructor is deterministic and follows the described child algorithm:
    - Hexagonal seeding with 5 rows and counts [5, 5, 6, 5, 5], with the 6-column row
      centered vertically (improved geometry).
    - LP solve (maximize sum of radii) for fixed centers via primal simplex with
      Bland's rule (radii-only entering). Deterministic tableau Bland simplex fallback.
    - Soft-active guidance for motion: include near-binding constraints with slack-aware
      weights, inward-biased boundary normals with a conservative cap, and per-center
      normalization of direction vectors. Monotone backtracking with slightly expanded
      budget ensures non-decreasing objective progression.

    Parameters
    ----------
    num_circles : int
        Number of circles to place. This implementation targets 26 exactly.

    Returns
    -------
    centers : np.ndarray, shape (26, 2)
    radii : np.ndarray, shape (26,)
    """
    if num_circles != 26:
        # This candidate targets exactly 26 circles as required by the task.
        raise ValueError("This constructor is designed for exactly 26 circles.")

    # 1) Hexagonal edge-fitted seeding (deterministic)
    eps = 1e-3
    centers, dx, dy = _hex_seed_26(eps=eps)
    # Defensive clip, even though seed construction is already within bounds.
    centers = np.clip(centers, eps, 1.0 - eps)

    # 2) Initial LP solve for radii (fixed centers)
    radii, obj = _lp_maximize_sum_radii(centers)

    # 3) Soft-active guided motion with monotone backtracking
    # Step schedule
    s = 0.1 * min(dx, dy)
    rho = 0.95  # step decay per accepted outer iteration
    tol_obj = 1e-12
    tol_active = 1e-9
    # Soft-active thresholds
    tol_soft_pair = 1e-4
    tol_soft_bnd = 1e-4
    # Increased outer iterations/backtracking attempts
    max_outer = 60
    stagnation_count = 0

    for _ in range(max_outer):
        # Build normalized movement directions from active and soft-active constraints
        v = _soft_active_direction(
            centers,
            radii,
            tol_active=tol_active,
            tol_soft_pair=tol_soft_pair,
            tol_soft_bnd=tol_soft_bnd,
        )

        # If no active movement direction (all zero), stop
        if not np.any(np.linalg.norm(v, axis=1) > 0):
            break

        # Monotone backtracking line-search along v
        accepted = False
        step_len = s
        cand_obj = obj
        for _bt in range(16):
            cand_centers = centers + step_len * v
            # Keep centers inside open margins to avoid boundary drift out of domain.
            cand_centers = np.clip(cand_centers, eps, 1.0 - eps)

            cand_radii, cand_obj = _lp_maximize_sum_radii(cand_centers)

            # Accept if objective does not decrease
            if cand_obj + tol_obj >= obj:
                centers = cand_centers
                radii = cand_radii
                accepted = True
                break
            step_len *= 0.5

        if not accepted:
            # Could not find a non-decreasing step; stop
            break

        # Decrease base step size slightly for stability
        s *= rho

        # Stagnation check: if improvement is tiny, count and possibly stop
        if cand_obj - obj < 1e-8:
            stagnation_count += 1
        else:
            stagnation_count = 0
        obj = cand_obj
        if stagnation_count >= 3:
            break

    # Final LP and hygiene clamp
    radii, _ = _lp_maximize_sum_radii(centers)
    radii = np.clip(radii, 0.0, np.inf)

    return centers, radii


def _hex_seed_26(eps: float = 1e-3) -> Tuple[np.ndarray, float, float]:
    """Construct a deterministic hexagonal-lattice seed for exactly 26 centers.

    Layout:
      - 5 rows with counts [5, 5, 6, 5, 5] (place the 6-column row in the vertical middle)
      - dx = (1 - 2*eps)/5
      - dy = (sqrt(3)/2) * dx
      - rows centered vertically
      - Placement:
          * 6-column rows: x = eps + c*dx, c = 0..5
          * 5-column rows: x = eps + dx/2 + c*dx, c = 0..4

    Parameters
    ----------
    eps : float
        Open margin from the boundary.

    Returns
    -------
    centers : np.ndarray, shape (26, 2)
    dx : float
    dy : float
    """
    rows = 5
    counts = [5, 5, 6, 5, 5]
    assert sum(counts) == 26

    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx
    # Center rows vertically
    y0 = 0.5 - 0.5 * (rows - 1) * dy

    centers: List[List[float]] = []
    for k in range(rows):
        y = y0 + k * dy
        if counts[k] == 6:
            xs = [eps + c * dx for c in range(6)]
        else:
            xs = [eps + 0.5 * dx + c * dx for c in range(5)]
        for x in xs:
            centers.append([x, y])

    centers_arr = np.asarray(centers, dtype=float)
    centers_arr = np.clip(centers_arr, eps, 1.0 - eps)
    return centers_arr, dx, dy


def _lp_maximize_sum_radii(centers: np.ndarray) -> Tuple[np.ndarray, float]:
    """Solve LP: maximize sum_i r_i subject to
       - r_i >= 0
       - r_i <= min(x_i, y_i, 1-x_i, 1-y_i)
       - r_i + r_j <= ||c_i - c_j||   for all i < j

    Deterministic pipeline:
      - Primary: primal simplex with explicit basis matrix and Bland's rule
        (restrict entering to radii variables only).
      - Fallback: deterministic tableau-based Bland simplex.

    Parameters
    ----------
    centers : np.ndarray, shape (n, 2)

    Returns
    -------
    radii : np.ndarray, shape (n,)
    obj : float
        Optimal objective value (sum of radii).
    """
    n = centers.shape[0]
    # Build constraints in deterministic order:
    # First boundary constraints in index order, then pair constraints in lex order.
    # A r <= b
    x = centers[:, 0]
    y = centers[:, 1]
    up = np.minimum.reduce([x, y, 1.0 - x, 1.0 - y])  # upper bounds for r_i
    m1 = n

    # Pair constraints
    pairs: List[Tuple[int, int]] = []
    dists: List[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((i, j))
            d = float(np.linalg.norm(centers[i] - centers[j]))
            dists.append(d)
    m2 = len(pairs)
    m = m1 + m2

    # Build A and b for A r <= b
    A = np.zeros((m, n), dtype=float)
    b = np.zeros((m,), dtype=float)

    # Boundary constraints rows 0..n-1 : r_i <= up_i
    for i in range(n):
        A[i, i] = 1.0
        b[i] = max(0.0, float(up[i]))

    # Pair constraints rows n..n+m2-1 : r_i + r_j <= ||ci-cj||
    for k, (ij, dist) in enumerate(zip(pairs, dists), start=n):
        i, j = ij
        A[k, i] = 1.0
        A[k, j] = 1.0
        b[k] = max(0.0, float(dist))

    # Primary solver: basis-matrix primal simplex with Bland's rule
    radii, obj, ok = _simplex_primary(A, b)

    if not ok:
        # Fallback: deterministic tableau Bland simplex
        radii, obj = _simplex_tableau(A, b)

    # Numerical hygiene: clamp tiny negatives to zero
    radii = np.clip(radii, 0.0, np.inf)
    obj = float(np.sum(radii))
    return radii, obj


def _simplex_primary(A: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, float, bool]:
    """Primary LP solver: primal simplex using an explicit basis matrix B and Bland's rule.

    Variables: z = [r (n), s (m)], s are slacks. Start with slacks basic (identity).
    Entering variables restricted to r only.

    Returns radii, obj, success_flag.
    """
    m, n = A.shape
    total_vars = n + m

    # Basis starts as the slack variables (identity)
    B_vars = np.arange(n, n + m, dtype=int)  # indices into z
    # Precompute A column references for r-variables
    A_cols = [A[:, j].copy() for j in range(n)]

    # Objective costs: c for z
    c = np.zeros((total_vars,), dtype=float)
    c[:n] = 1.0  # maximize sum of radii

    # Tolerances
    tol_rc = 1e-12
    tol_dir = 1e-12
    ratio_tie_tol = 1e-12

    # Helper to build B matrix from current basis
    def build_B_matrix(Bv: np.ndarray) -> np.ndarray:
        B = np.zeros((m, m), dtype=float)
        for col, var in enumerate(Bv):
            if var < n:
                B[:, col] = A_cols[var]
            else:
                row = var - n
                B[row, col] = 1.0
        return B

    # Solve linear system with fallback to least-squares for robustness
    def solve_linear(mat: np.ndarray, rhs: np.ndarray) -> np.ndarray:
        try:
            sol = np.linalg.solve(mat, rhs)
        except np.linalg.LinAlgError:
            sol, *_ = np.linalg.lstsq(mat, rhs, rcond=None)
        return sol

    # Keep a set for faster "is in basis" checks among r variables (linear scan is fine)
    def in_basis_r(var_idx: int, Bv: np.ndarray) -> bool:
        for v in Bv:
            if v == var_idx:
                return True
        return False

    max_pivots = 2000
    pivots = 0
    success = True
    while True:
        pivots += 1
        if pivots > max_pivots:
            success = False
            break  # fallback will handle

        # Build B and compute x_B and dual multipliers w via B^T w = c_B
        B = build_B_matrix(B_vars)
        c_B = np.array([1.0 if var < n else 0.0 for var in B_vars], dtype=float)

        # Dual multipliers from B^T w = c_B
        try:
            w = solve_linear(B.T, c_B)
        except Exception:
            success = False
            break
        if not np.all(np.isfinite(w)):
            success = False
            break

        # Compute reduced costs for candidate entering variables (r vars not in basis)
        entering_candidates = []
        for j in range(n):
            if in_basis_r(j, B_vars):
                continue
            a_j = A_cols[j]
            # reduced cost: c_j - w^T a_j; here c_j = 1
            rc = 1.0 - float(np.dot(w, a_j))
            if rc > tol_rc:
                entering_candidates.append(j)

        if not entering_candidates:
            # Optimal: no positive reduced costs
            break

        # Bland's rule: pick smallest index amongst candidates
        entering_idx = min(entering_candidates)

        # Compute direction d = B^{-1} a_enter and current basic solution x_B = B^{-1} b
        a_enter = A_cols[entering_idx]
        d = solve_linear(B, a_enter)
        x_B = solve_linear(B, b)
        if not (np.all(np.isfinite(d)) and np.all(np.isfinite(x_B))):
            success = False
            break

        # Ratio test: theta = min_i x_B[i] / d[i] over d[i] > tol
        theta = np.inf
        leaving_row = None
        for i_row, d_i in enumerate(d):
            if d_i > tol_dir:
                ratio = x_B[i_row] / d_i
                if ratio < theta - ratio_tie_tol:
                    theta = ratio
                    leaving_row = i_row
                elif abs(ratio - theta) <= ratio_tie_tol:
                    # Tie on ratio: choose smallest basic var index (lexicographic)
                    if leaving_row is None or B_vars[i_row] < B_vars[leaving_row]:
                        leaving_row = i_row

        if leaving_row is None or not np.isfinite(theta):
            # Unbounded or numerical failure in this bounded problem -> fallback
            success = False
            break

        # Pivot: replace leaving basic var with entering var
        B_vars[leaving_row] = entering_idx

    # Recover primal solution z from final basis
    radii = np.zeros((n,), dtype=float)
    if success:
        B = build_B_matrix(B_vars)
        x_B = solve_linear(B, b)
        if not np.all(np.isfinite(x_B)):
            success = False
        else:
            z = np.zeros((n + m,), dtype=float)
            for col, var in enumerate(B_vars):
                z[var] = x_B[col]
            radii = z[:n].copy()

    obj = float(np.sum(np.clip(radii, 0.0, np.inf)))
    return radii, obj, success


def _simplex_tableau(A: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, float]:
    """Fallback solver: deterministic tableau-based Bland primal simplex.

    Standard form:
      maximize c^T r
      subject to A r + s = b, r >= 0, s >= 0
      with c_j = 1 for r-variables and 0 for slacks.

    Entering variables restricted to r only; Bland's rule with lexicographic
    leaving rule to avoid cycling.
    """
    m, n = A.shape
    total_vars = n + m

    # Build tableau T of shape (m+1) x (total_vars+1), last column is RHS
    # Initialize with identity slacks basis
    T = np.zeros((m + 1, total_vars + 1), dtype=float)

    # Constraint rows: [A | I | b]
    T[:m, :n] = A
    for i in range(m):
        T[i, n + i] = 1.0
    T[:m, -1] = b

    # Objective row (row m): reduced costs for non-basic variables.
    # With initial basis slacks (cost 0), reduced costs equal c (for r vars),
    # and 0 for slack vars. We'll store the reduced costs directly in row m.
    T[m, :n] = 1.0  # c for r-variables
    # Slack costs 0, RHS (objective value) initially 0

    # Basic variables per row (initially slacks)
    B = np.arange(n, n + m, dtype=int)

    tol_rc = 1e-12
    tol_dir = 1e-12
    ratio_tie_tol = 1e-12
    max_pivots = 10000
    pivots = 0

    def pivot(row: int, col: int):
        """Perform a pivot on (row, col): make T[row, col] == 1, zero it out elsewhere."""
        piv = T[row, col]
        # Scale pivot row
        T[row, :] = T[row, :] / piv
        # Eliminate column col in all other rows
        for r in range(m + 1):
            if r == row:
                continue
            factor = T[r, col]
            if factor != 0.0:
                T[r, :] -= factor * T[row, :]

    while True:
        pivots += 1
        if pivots > max_pivots:
            break  # give up; we'll read whatever solution we have

        # Identify entering variable among r-vars using Bland: smallest index j with rc_j > tol
        entering = None
        for j in range(n):
            rc = T[m, j]
            if rc > tol_rc:  # positive reduced cost improves objective
                entering = j
                break

        if entering is None:
            # Optimal: no positive reduced costs
            break

        # Determine leaving row via deterministic minimum ratio test
        best_ratio = np.inf
        leaving_row = None
        for i in range(m):
            a_ij = T[i, entering]
            if a_ij > tol_dir:
                ratio = T[i, -1] / a_ij
                if ratio < best_ratio - ratio_tie_tol:
                    best_ratio = ratio
                    leaving_row = i
                elif abs(ratio - best_ratio) <= ratio_tie_tol:
                    # lexicographic tie-breaking by basic index
                    if leaving_row is None or B[i] < B[leaving_row]:
                        leaving_row = i

        if leaving_row is None or not np.isfinite(best_ratio):
            # Unbounded or numerical issue; stop
            break

        # Pivot and update basis
        pivot(leaving_row, entering)
        B[leaving_row] = entering

    # Extract solution: basic vars have their RHS; nonbasic are zero
    z = np.zeros((total_vars,), dtype=float)
    for i in range(m):
        var = B[i]
        if 0 <= var < total_vars:
            z[var] = T[i, -1]

    radii = z[:n].copy()
    radii = np.clip(radii, 0.0, np.inf)
    obj = float(np.sum(radii))
    return radii, obj


def _soft_active_direction(
    centers: np.ndarray,
    radii: np.ndarray,
    tol_active: float = 1e-9,
    tol_soft_pair: float = 1e-4,
    tol_soft_bnd: float = 1e-4,
) -> np.ndarray:
    """Construct movement directions using soft-active guidance with inward boundary bias.

    Pairs (i, j):
      - Compute slack s_ij = ||c_i − c_j|| − (r_i + r_j).
      - If s_ij <= tol_active (tight), add separating normal weighted by w_base = 1/max(dist, 1e-9).
      - If tol_active < s_ij <= tol_soft_pair, add same normal with a mild boost:
            factor f_pair = 1 + (tol_soft_pair − s_ij)/tol_soft_pair ∈ (1, 2].
        Final weight = w_base * f_pair.

    Boundaries for circle i:
      - Let minc = min{x_i, y_i, 1 − x_i, 1 − y_i}, s_i = minc − r_i, and identify all sides
        tied at minc. For each tied side, add its inward normal.
      - Base inward bias weight w_bnd_base = 2.0.
      - Soft-active boost: if tol_active < s_i <= tol_soft_bnd, multiply by
            f_bnd = 1 + (tol_soft_bnd − s_i)/tol_soft_bnd ∈ (1, 2],
        and cap combined boundary weight by w_bnd_max = 3.0.
      - If s_i <= tol_active (tight), use w_bnd_base (no extra boost).

    Each center’s accumulated direction vector is normalized to unit length if nonzero.
    Determinism is preserved via fixed thresholds, lexicographic loops, and no randomness.

    Parameters
    ----------
    centers : np.ndarray, shape (n, 2)
    radii : np.ndarray, shape (n,)
    tol_active : float
    tol_soft_pair : float
    tol_soft_bnd : float

    Returns
    -------
    v : np.ndarray, shape (n, 2)
        Normalized movement vectors per center; zero rows mean no movement.
    """
    n = centers.shape[0]
    v = np.zeros_like(centers)
    tiny = 1e-9

    # Pairwise soft-active constraints
    for i in range(n):
        ci = centers[i]
        for j in range(i + 1, n):
            cj = centers[j]
            diff = ci - cj
            dist = float(np.hypot(diff[0], diff[1]))
            if dist <= tiny:
                continue
            slack = dist - (radii[i] + radii[j])
            if slack <= tol_active:
                # Tight: base weight only
                w = 1.0 / max(dist, tiny)
                n_ij = diff / dist
                v[i] += w * n_ij
                v[j] -= w * n_ij
            elif slack <= tol_soft_pair:
                # Near-active: mild boost up to 2x as it approaches tightness
                f_pair = 1.0 + (tol_soft_pair - slack) / tol_soft_pair
                w = (1.0 / max(dist, tiny)) * f_pair
                n_ij = diff / dist
                v[i] += w * n_ij
                v[j] -= w * n_ij
            # else: ignore non-threatening pairs

    # Boundary soft-active constraints with inward bias and conservative cap
    w_bnd_base = 2.0
    w_bnd_max = 3.0
    for i in range(n):
        x, y = centers[i]
        cl = x
        cb = y
        cr = 1.0 - x
        ct = 1.0 - y
        minc = min(cl, cb, cr, ct)
        slack_i = minc - radii[i]

        # Determine weight based on slack
        if slack_i <= tol_active:
            weight = w_bnd_base
        elif slack_i <= tol_soft_bnd:
            f_bnd = 1.0 + (tol_soft_bnd - slack_i) / tol_soft_bnd
            weight = min(w_bnd_base * f_bnd, w_bnd_max)
        else:
            weight = None  # not soft-active; skip

        if weight is not None:
            # Add inward normals for all sides tied at minimum
            if abs(cl - minc) <= tol_active:
                v[i] += weight * np.array([1.0, 0.0])
            if abs(cb - minc) <= tol_active:
                v[i] += weight * np.array([0.0, 1.0])
            if abs(cr - minc) <= tol_active:
                v[i] += weight * np.array([-1.0, 0.0])
            if abs(ct - minc) <= tol_active:
                v[i] += weight * np.array([0.0, -1.0])

    # Normalize per-center; replace non-finite with zero
    norms = np.linalg.norm(v, axis=1)
    for i in range(n):
        if norms[i] > 0.0 and np.isfinite(norms[i]):
            v[i] /= norms[i]
        else:
            v[i] = np.array([0.0, 0.0], dtype=float)
    return v
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

This implements:
 - Edge-anchored hexagonal seeding sized to the unit square
 - Deterministic primal simplex LP (Bland's rule) to maximize sum of radii for fixed centers
 - Active-set guided center relaxation with monotone backtracking acceptance

The LP structure is:
  maximize sum(r_i) subject to:
    r_i >= 0
    r_i <= x_i, r_i <= y_i, r_i <= (1 - x_i), r_i <= (1 - y_i)
    r_i + r_j <= ||c_i - c_j||_2   for all i < j

Constraints are ordered deterministically to ensure reproducibility.
"""

import json
import math
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    This implementation follows the described algorithm:
    1) Edge-anchored hexagonal seeding (26 centers).
    2) Fixed-center radii LP via deterministic primal simplex with Bland's rule.
    3) Active-set guided center relaxation with monotone backtracking.

    Mutation summary integrated here:
      - Fix small_improve_streak logic by computing improvement before updating prev_sum.
      - Add inward boundary bias with capped per-circle boundary weight in build_active_motion.
      - Normalize per-center motion directions to unit length after degree normalization.
      - Slightly soften active detection tolerance and enlarge accept budget deterministically.
    """
    if num_circles != 26:
        # Deterministic fallback: use the first 26 layout; the algorithm is tuned for 26.
        pass

    # 1) Edge-anchored hexagonal seed
    centers = hex_seed_26()

    # 2) Solve LP for initial radii
    radii = compute_max_radii(centers)

    # 3) Active-set guided center relaxation with monotone backtracking
    # Deterministic schedule and tolerances
    eps = 1e-3
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (math.sqrt(3.0) / 2.0) * dx
    s0 = 0.1 * min(dx, dy)
    rho = 0.95
    alpha_min = 1e-6 * s0
    # Increased budget to allow harvesting more improvements deterministically
    accept_budget = 120
    obj_tol = 1e-12
    # Softer active detection tolerance
    active_tol = 1e-10

    prev_sum = float(np.sum(radii))
    accepted = 0
    small_improve_streak = 0

    while accepted < accept_budget:
        # Build active-set motion directions
        v, deg = build_active_motion(centers, radii, active_tol)

        # If no active contributions, break
        if np.all(deg == 0) or np.allclose(v, 0.0):
            break

        # Normalize by degree to avoid dominance by high degree nodes
        deg_safe = np.maximum(deg[:, None], 1.0)
        v_norm = v / deg_safe

        # Per-center direction normalization: unit length if non-zero
        norms = np.linalg.norm(v_norm, axis=1)
        for i in range(v_norm.shape[0]):
            ni = norms[i]
            if ni > 0.0:
                v_norm[i] = v_norm[i] / ni

        # Trial backtracking along deterministic schedule
        alpha = s0
        accepted_this = False
        # Clip centers into [eps, 1-eps]^2 during proposals
        lo = eps
        hi = 1.0 - eps

        while alpha >= alpha_min:
            trial_centers = centers + alpha * v_norm
            trial_centers = np.clip(trial_centers, lo, hi)

            trial_radii = compute_max_radii(trial_centers)
            trial_sum = float(np.sum(trial_radii))
            if trial_sum + obj_tol >= prev_sum:
                # Compute improvement before updating prev_sum (bugfix)
                improvement = trial_sum - prev_sum
                # Monotone acceptance
                centers = trial_centers
                radii = trial_radii
                prev_sum = trial_sum
                accepted += 1
                accepted_this = True
                # Track small improvements using saved improvement
                if improvement < 1e-10:
                    small_improve_streak += 1
                else:
                    small_improve_streak = 0
                break
            else:
                alpha *= rho

        if not accepted_this:
            # Could not improve with this motion; stop iteration.
            break

        # Stop if progress has stalled for several steps
        if small_improve_streak >= 3:
            break

    return centers, radii


def hex_seed_26():
    """Edge-anchored hexagonal seeding for exactly 26 centers.

    Layout:
      - eps tiny margin
      - dx = (1 - 2*eps) / 5
      - dy = (sqrt(3)/2) * dx
      - 5 rows with counts [5, 5, 6, 5, 5] (6 in the middle row -> total 26)
      - y positions symmetric with center row at k=2
      - For middle row (k=2): x = eps + c*dx for c=0..5
      - Other rows: x = eps + dx/2 + c*dx for c=0..4
    """
    eps = 1e-3
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (math.sqrt(3.0) / 2.0) * dx

    y0 = 0.5 - 2.0 * dy  # k=0 row
    rows = []
    for k in range(5):
        yk = y0 + k * dy
        if k == 2:
            # 6 columns, edge-anchored
            xs = [eps + c * dx for c in range(6)]
        else:
            # 5 columns, offset by dx/2
            xs = [eps + 0.5 * dx + c * dx for c in range(5)]
        for x in xs:
            rows.append([x, yk])

    centers = np.array(rows, dtype=float)
    # Ensure centers are within [eps, 1-eps]
    centers = np.clip(centers, eps, 1.0 - eps)
    assert centers.shape == (26, 2)
    return centers


def compute_max_radii(centers: np.ndarray) -> np.ndarray:
    """Solve the fixed-center radii LP deterministically via primal simplex.

    Maximize sum_i r_i subject to:
      r_i >= 0
      r_i <= x_i
      r_i <= y_i
      r_i <= 1 - x_i
      r_i <= 1 - y_i
      r_i + r_j <= ||c_i - c_j||, for all i < j
    """
    n = centers.shape[0]
    # Deterministic constraint ordering: per circle boundaries (left, bottom, right, top), then pairs (i,j).
    A_rows: List[List[float]] = []
    b_vals: List[float] = []

    # Boundary constraints
    for i in range(n):
        x_i, y_i = float(centers[i, 0]), float(centers[i, 1])
        # left: r_i <= x_i
        row = [0.0] * n
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(x_i)
        # bottom: r_i <= y_i
        row = [0.0] * n
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(y_i)
        # right: r_i <= 1 - x_i
        row = [0.0] * n
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(1.0 - x_i)
        # top: r_i <= 1 - y_i
        row = [0.0] * n
        row[i] = 1.0
        A_rows.append(row)
        b_vals.append(1.0 - y_i)

    # Pairwise constraints
    for i in range(n):
        for j in range(i + 1, n):
            d = float(np.linalg.norm(centers[i] - centers[j]))
            row = [0.0] * n
            row[i] = 1.0
            row[j] = 1.0
            A_rows.append(row)
            b_vals.append(d)

    A = np.array(A_rows, dtype=float)
    b = np.array(b_vals, dtype=float)
    c = np.ones(n, dtype=float)

    # Simplex solve
    r = primal_simplex_blands(A, b, c, tol=1e-12)
    # Ensure non-negative finite
    r = np.clip(r, 0.0, np.inf)
    return r


def primal_simplex_blands(A: np.ndarray, b: np.ndarray, c: np.ndarray, tol: float = 1e-12) -> np.ndarray:
    """Deterministic primal simplex for maximize c^T x subject to A x <= b, x >= 0.

    We construct a tableau with slack variables and apply Bland's rule to prevent cycling.
    Constraint and variable ordering is preserved as-given.
    """
    m, n = A.shape
    # Ensure b >= 0 (feasible with slacks only). If any b < 0, we still can scale row by -1,
    # but in our construction b is non-negative.
    if np.any(b < -1e-15):
        # Defensive flip if any negative b due to numeric noise
        for i in range(m):
            if b[i] < 0.0:
                A[i, :] *= -1.0
                b[i] *= -1.0

    # Tableau of size (m+1) x (n + m + 1)
    # Columns: [x_0 .. x_{n-1} | s_0 .. s_{m-1} | RHS]
    T = np.zeros((m + 1, n + m + 1), dtype=float)
    # Fill constraint rows: [A | I | b]
    T[:m, :n] = A
    T[:m, n:n + m] = np.eye(m)
    T[:m, -1] = b
    # Objective row: [-c | 0 | 0]
    T[m, :n] = -c
    T[m, n:n + m] = 0.0
    T[m, -1] = 0.0

    # Basis: initial slacks
    basis = list(range(n, n + m))

    # Simplex iterations
    max_iters = 100000  # large safe cap
    iters = 0
    while True:
        iters += 1
        if iters > max_iters:
            break  # Should not happen; protects against infinite loops in degenerate cases

        # Bland's rule: choose smallest index entering variable with negative reduced cost
        obj_row = T[m, :-1]
        # Identify columns with negative reduced cost (strictly < -tol)
        entering_candidates = [j for j in range(n + m) if obj_row[j] < -tol]
        if not entering_candidates:
            # Optimal
            break
        enter = min(entering_candidates)

        # Ratio test: rows with positive pivot column coefficient
        col = T[:m, enter]
        ratios = []
        for i in range(m):
            a_ij = col[i]
            if a_ij > tol:
                ratios.append((T[i, -1] / a_ij, i))
        if not ratios:
            # Unbounded; for our problem with slacks and bounded distances, shouldn't occur
            # Break to avoid infinite loop
            break
        # Choose smallest ratio; Bland tie-break: smallest row index
        ratios.sort(key=lambda x: (x[0], x[1]))
        leave_row = ratios[0][1]

        # Pivot
        pivot = T[leave_row, enter]
        if abs(pivot) < tol:
            # Numerical issue; skip to prevent blow-up
            # Try next candidate by marking this candidate unusable
            # Remove current entering var and continue
            # But to keep determinism and progress, perturb slightly
            pivot = tol
        # Normalize leaving row
        T[leave_row, :] = T[leave_row, :] / pivot

        # Eliminate entering column in all other rows
        for i in range(m + 1):
            if i == leave_row:
                continue
            factor = T[i, enter]
            if factor != 0.0:
                T[i, :] -= factor * T[leave_row, :]

        # Update basis
        basis[leave_row] = enter

    # Extract solution: basic vars from RHS, non-basic = 0
    x = np.zeros(n, dtype=float)
    for i in range(m):
        var = basis[i]
        if var < n:
            # x-variable basic; set to RHS
            x[var] = T[i, -1]
    # Small negative noise clamp
    x = np.maximum(x, 0.0)
    return x


def build_active_motion(centers: np.ndarray, radii: np.ndarray, tol: float = 1e-10) -> Tuple[np.ndarray, np.ndarray]:
    """Compute motion vectors along normals of the active set constraints.

    Returns:
      v: (N,2) motion vector per center
      deg: (N,) number of active contributions per center (for normalization)

    Changes:
      - Boundary normals are upweighted with base factor and capped per-circle to steer inward.
      - Pairwise contributions unchanged.
    """
    n = centers.shape[0]
    v = np.zeros_like(centers)
    deg = np.zeros(n, dtype=float)

    # Boundary constraints per circle: r_i <= x_i, r_i <= y_i, r_i <= 1-x_i, r_i <= 1-y_i
    # Inward normals: left (+1,0), bottom (0,+1), right (-1,0), top (0,-1)
    # Apply inward boundary bias: base weight and max cap per circle
    w_bnd_base = 2.0
    w_bnd_max = 3.0

    for i in range(n):
        x_i, y_i = float(centers[i, 0]), float(centers[i, 1])
        r_i = float(radii[i])

        bvec = np.zeros(2, dtype=float)
        bdeg = 0.0

        # left
        if x_i - r_i <= tol:
            bvec += w_bnd_base * np.array([1.0, 0.0])
            bdeg += 1.0
        # bottom
        if y_i - r_i <= tol:
            bvec += w_bnd_base * np.array([0.0, 1.0])
            bdeg += 1.0
        # right
        if (1.0 - x_i) - r_i <= tol:
            bvec += w_bnd_base * np.array([-1.0, 0.0])
            bdeg += 1.0
        # top
        if (1.0 - y_i) - r_i <= tol:
            bvec += w_bnd_base * np.array([0.0, -1.0])
            bdeg += 1.0

        # Cap total boundary contribution magnitude per-circle
        norm_b = float(np.linalg.norm(bvec))
        if norm_b > w_bnd_max:
            bvec *= (w_bnd_max / norm_b)

        # Accumulate boundary contribution and degrees
        v[i] += bvec
        deg[i] += bdeg

    # Pairwise active contacts: r_i + r_j ≈ d_ij
    # Contribution: n_ij = (c_i - c_j)/d_ij; add weighted by 1/max(d_ij, δ)
    delta = 1e-9
    for i in range(n):
        for j in range(i + 1, n):
            ci = centers[i]
            cj = centers[j]
            diff = ci - cj
            d = math.hypot(diff[0], diff[1])
            if d <= 0.0:
                continue
            if d - (radii[i] + radii[j]) <= tol:
                w = 1.0 / max(d, delta)
                n_ij = diff / d
                v[i] += w * n_ij
                v[j] -= w * n_ij
                deg[i] += 1.0
                deg[j] += 1.0

    return v, deg
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
