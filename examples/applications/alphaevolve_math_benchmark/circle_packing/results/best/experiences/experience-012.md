Proactive, deterministic guidance by including near-active constraints with slack-aware weights when constructing center-motion directions.

- Soft-Active Guidance for Center Motion: Expands the active-constraint direction to include near-active pair and boundary constraints with positive slack s ≤ tol_soft_pair or s ≤ tol_soft_bnd, weighting them to linearly boost influence up to 2× as slack approaches zero while keeping per-center normalization and fixed thresholds to preserve determinism.
- _active_constraint_direction: For any soft-active pair (i, j), it adds the separating normal to v_i and v_j scaled by w = (1 / max(dist, 1e−9)) · (1 + (tol_soft_pair − s_ij)/tol_soft_pair), and for soft-active boundaries it adds inward normals with factor (1 + (tol_soft_bnd − s_i)/tol_soft_bnd).
- Integration details for Soft-Active Guidance for Center Motion: Uses tol_soft_pair = 1e−4 and tol_soft_bnd = 1e−4, modifies only the pairwise and boundary loops in _active_constraint_direction, and leaves the LP solver, monotone backtracking, and seeding unchanged to keep the mutation minimal and deterministic.
- Generation 8 result for Soft-Active Guidance for Center Motion: Achieved sum_radii 2.515512731603428 with validity 1.0 and no errors on the 26-circle unit-square packing task.
- Parent run 42c06212c5b0: Recorded a parent_score of 2.5152447105789904 for the same task, providing the baseline for the mutation’s outcome.

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

This implements a two-stage deterministic optimization:
  1) For fixed centers, solve an LP that maximizes the sum of radii under
     boundary and non-overlap constraints using a primal simplex with Bland's rule.
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

    This constructor is deterministic and follows the algorithm described in the prompt:
    - Hexagonal edge-fitted seeding.
    - LP solve (maximize sum of radii) for fixed centers via primal simplex with Bland's rule.
    - Active-constraint-guided center motions with monotone backtracking.

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
        # We fail fast to avoid producing invalid layouts for other counts.
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
    max_outer = 40
    stagnation_count = 0

    for _ in range(max_outer):
        # Build normalized movement directions from active constraints, with soft-active guidance
        v = _active_constraint_direction(centers, radii, tol_active=tol_active)

        # If no active movement direction (all zero), stop
        if not np.any(np.linalg.norm(v, axis=1) > 0):
            break

        # Monotone backtracking line-search along v
        accepted = False
        step_len = s
        for _bt in range(10):
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
      - 5 rows with counts [6, 5, 5, 5, 5]
      - dx = (1 - 2*eps)/5
      - dy = (sqrt(3)/2) * dx
      - rows centered vertically

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
    counts = [6, 5, 5, 5, 5]
    assert sum(counts) == 26

    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx
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

    For determinism, use a primal simplex with Bland's rule (lexicographically
    smallest index selection). We restrict entering variables to the radii variables
    only, for speed and determinism.

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
    # Boundary constraints
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
    # Build A matrix
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

    # Simplex data
    # Variables: z = [r (n), s (m)], with s the slacks.
    total_vars = n + m
    # Basis starts as the slack variables (identity)
    B_vars = np.arange(n, n + m, dtype=int)  # indices into z
    # Precompute A column references for r-variables
    A_cols = [A[:, j].copy() for j in range(n)]

    # Objective costs: c for z
    c = np.zeros((total_vars,), dtype=float)
    c[:n] = 1.0  # objective maximize sum of r_i

    # Tolerances
    tol_rc = 1e-12
    tol_dir = 1e-12

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
            return np.linalg.solve(mat, rhs)
        except np.linalg.LinAlgError:
            # Degenerate basis might occur numerically; fall back to least squares
            sol, *_ = np.linalg.lstsq(mat, rhs, rcond=None)
            return sol

    # Keep a set for faster "is in basis" checks among r variables
    def in_basis_r(var_idx: int, Bv: np.ndarray) -> bool:
        # Only for 0 <= var_idx < n
        # linear search is OK since n <= 26, m ~ 351
        for v in Bv:
            if v == var_idx:
                return True
        return False

    # Main primal simplex loop
    max_pivots = 2000  # generous upper bound
    pivots = 0
    while True:
        pivots += 1
        if pivots > max_pivots:
            break  # safety; should converge much earlier

        # Build B and compute x_B and dual multipliers w via B^T w = c_B
        B = build_B_matrix(B_vars)
        c_B = np.array([1.0 if var < n else 0.0 for var in B_vars], dtype=float)

        # Dual multipliers (row vector) through solving B^T w = c_B
        w = solve_linear(B.T, c_B)

        # Compute reduced costs for candidate entering variables (r vars not in basis)
        entering_candidates = []
        red_costs = []
        for j in range(n):
            if in_basis_r(j, B_vars):
                continue
            a_j = A_cols[j]
            # reduced cost: c_j - w^T a_j; here c_j = 1
            rc = 1.0 - float(np.dot(w, a_j))
            if rc > tol_rc:
                entering_candidates.append(j)
                red_costs.append(rc)

        if not entering_candidates:
            # Optimal: no positive reduced costs
            break

        # Bland's rule: pick smallest index amongst candidates
        entering_idx = min(entering_candidates)

        # Compute direction d = B^{-1} a_enter
        a_enter = A_cols[entering_idx]
        d = solve_linear(B, a_enter)

        # Compute x_B = B^{-1} b
        x_B = solve_linear(B, b)

        # Ratio test: theta = min_i x_B[i] / d[i] over d[i] > 1e-12
        theta = np.inf
        leaving_row = None
        # For Bland’s tie-breaking, track rows and their basic var indices
        for i_row, d_i in enumerate(d):
            if d_i > tol_dir:
                ratio = x_B[i_row] / d_i
                if ratio < theta - 0.0:  # strict less
                    theta = ratio
                    leaving_row = i_row
                elif abs(ratio - theta) <= 0.0:
                    # Tie on ratio: choose smallest basic var index
                    if leaving_row is None:
                        leaving_row = i_row
                    else:
                        if B_vars[i_row] < B_vars[leaving_row]:
                            leaving_row = i_row

        if leaving_row is None:
            # Unbounded (shouldn't happen in this bounded problem)
            break

        # Pivot: replace leaving basic var with entering var
        B_vars[leaving_row] = entering_idx

    # Recover primal solution z from final basis
    B = build_B_matrix(B_vars)
    x_B = solve_linear(B, b)

    z = np.zeros((total_vars,), dtype=float)
    for col, var in enumerate(B_vars):
        z[var] = x_B[col]

    # Radii are first n entries
    radii = z[:n].copy()
    # Numerical hygiene: clamp tiny negatives to zero
    radii = np.clip(radii, 0.0, np.inf)
    obj = float(np.sum(radii))
    return radii, obj


def _active_constraint_direction(centers: np.ndarray, radii: np.ndarray, tol_active: float = 1e-9) -> np.ndarray:
    """Construct movement directions along the normals of (soft-)active constraints.

    We include exactly active constraints (within tol_active) and also near-active
    ones with small positive slack using a mild slack-aware weighting.

    Soft-active envelopes:
      - Pair constraints with s_ij = ||c_i − c_j|| − (r_i + r_j) ≤ tol_soft_pair
      - Boundary constraints with s_i = min{x_i, y_i, 1 − x_i, 1 − y_i} − r_i ≤ tol_soft_bnd

    Weighting:
      - For pairs: base weight w_base = 1 / max(dist, 1e-9); for soft-active
        pairs (tol_active < s_ij ≤ tol_soft_pair), scale by a factor
        (1 + (tol_soft_pair − s_ij)/tol_soft_pair), which ranges from 1 to 2.
        For exactly active (s_ij ≤ tol_active), fall back to original behavior (factor 1).
      - For boundaries: add inward normals for all sides tied at the minimum
        clearance. For soft-active (tol_active < s_i ≤ tol_soft_bnd), scale by the
        same linear factor; for exactly active (s_i ≤ tol_active), factor 1.

    Per-center normalization is applied at the end so only directions are affected.

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

    # Soft-active thresholds
    tol_soft_pair = 1e-4
    tol_soft_bnd = 1e-4

    # Pairwise (soft-)active constraints
    for i in range(n):
        ci = centers[i]
        for j in range(i + 1, n):
            cj = centers[j]
            diff = ci - cj
            dist = float(np.hypot(diff[0], diff[1]))
            if dist <= 1e-15:
                continue  # degenerate; skip (boundary forces will separate)
            # Slack: s_ij = ||ci - cj|| - (ri + rj)
            s_ij = dist - (radii[i] + radii[j])
            if s_ij <= tol_active:
                # Exactly active: original behavior
                n_ij = diff / dist
                w = 1.0 / max(dist, 1e-9)
                v[i] += w * n_ij
                v[j] -= w * n_ij
            elif s_ij <= tol_soft_pair:
                # Soft-active: apply slack-aware boost up to 2x
                n_ij = diff / dist
                w_base = 1.0 / max(dist, 1e-9)
                factor = 1.0 + (tol_soft_pair - s_ij) / tol_soft_pair  # in (1, 2]
                # Clamp for numerical safety
                if factor < 1.0:
                    factor = 1.0
                elif factor > 2.0:
                    factor = 2.0
                w = w_base * factor
                v[i] += w * n_ij
                v[j] -= w * n_ij
            # else: not near-active; no contribution

    # Boundary (soft-)active constraints per center
    for i in range(n):
        x, y = centers[i]
        # clearances to left, bottom, right, top
        cl = x
        cb = y
        cr = 1.0 - x
        ct = 1.0 - y
        minc = min(cl, cb, cr, ct)
        # Slack for boundary: s_i = min_clearance - r_i
        s_i = minc - radii[i]

        # Identify all sides tied at minimum (deterministic, with tiny tolerance)
        is_left_min = abs(cl - minc) <= tol_active
        is_bottom_min = abs(cb - minc) <= tol_active
        is_right_min = abs(cr - minc) <= tol_active
        is_top_min = abs(ct - minc) <= tol_active

        if s_i <= tol_active:
            # Exactly active: original inward normals
            if is_left_min:
                v[i] += np.array([1.0, 0.0])
            if is_bottom_min:
                v[i] += np.array([0.0, 1.0])
            if is_right_min:
                v[i] += np.array([-1.0, 0.0])
            if is_top_min:
                v[i] += np.array([0.0, -1.0])
        elif s_i <= tol_soft_bnd:
            # Soft-active: apply slack-aware boost up to 2x
            factor = 1.0 + (tol_soft_bnd - s_i) / tol_soft_bnd  # in (1, 2]
            if factor < 1.0:
                factor = 1.0
            elif factor > 2.0:
                factor = 2.0
            if is_left_min:
                v[i] += factor * np.array([1.0, 0.0])
            if is_bottom_min:
                v[i] += factor * np.array([0.0, 1.0])
            if is_right_min:
                v[i] += factor * np.array([-1.0, 0.0])
            if is_top_min:
                v[i] += factor * np.array([0.0, -1.0])

    # Normalize per-center so only directions (not magnitudes) are modified
    norms = np.linalg.norm(v, axis=1)
    for i in range(n):
        if norms[i] > 0.0:
            v[i] /= norms[i]
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
