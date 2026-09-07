Fit the horizontal hex seed exactly within the usable width and scale the initial step to the new pitch to avoid edge clipping and maintain deterministic, stable LP-relaxation updates.

- _hex_seed_26: Replaced the horizontal pitch with dx = (1 − 2*eps)/5 (keeping x0 = eps), placed the 6-column row at x = eps + c*dx for c = 0..5 and the 5-column rows at x = eps + dx/2 + c*dx for c = 0..4, and kept dy = sqrt(3)/2 * dx so that all initial x-coordinates lie within [eps, 1 − eps] without clipping.
- Edge-fit Hex Seed (no clipping) + Consistent step scale: Eliminating the prior edge clipping (where the last 6-column x exceeded 1.0 and was clipped) evened horizontal spacing, increased inter-center distances near the right edge, and yielded a more balanced contact graph that the LP converted into larger feasible radii and a higher objective.
- construct_packing step schedule s0: Set s0 = 0.1 * min((1 − 2*eps)/5, sqrt(3)/2 * (1 − 2*eps)/5) so the deterministic relocation step is consistent with the new hex pitch while leaving the LP/force-relaxation loop unchanged.
- Generation 6 outcome: The run achieved sum_radii = 2.5029905244467154 with validity = 1.0 and no errors under Event: good_algorithm.
- mutation_sampler: This change was produced from parent 76c3b0b36edc (parent_score = 2.5002760967885593) by a targeted mutation that modified the seed pitch and initial step scale.
- Edge-Anchored Hex Seeding for Full-Width Utilization: Anchor hex seed rows to the square’s margins to fully use the horizontal span: set dx = (1 − 2·eps)/5 and dy = (√3/2)·dx; use x_c = eps + c·dx for 6-column rows and x_c = eps + (dx/2) + c·dx for 5-column rows, with y_k = y0 + k·dy and y0 = 0.5 − 2·dy, and remove centering-based x_start and margin clipping. In unit-square circle packing with rows [6,5,5,5,5], this full-width allocation increases boundary clearance and reduces early boundary-capped radii, giving the fixed-center LP a better initial objective and active set. In Generation 13, this design achieved sum_radii = 2.623741750798679 with validity = 1.0, improving over the parent score 2.5909070569155115. Reuse edge anchoring for square-in-box hex seeds while keeping the LP backbone, Bland’s-rule solver, contact relaxation, micro SLP, warm starts, and monotone acceptance unchanged to preserve determinism.

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

This implementation follows the described algorithm:
1) Seed centers in a near-hexagonal pattern.
2) For fixed centers, solve a linear program (via deterministic primal simplex with Bland's rule)
   to maximize the sum of radii under boundary and pairwise non-overlap constraints.
3) Extract the active constraints (contact graph).
4) Move centers deterministically along the directions of active constraints.
5) Alternate LP and relocation with a decreasing step schedule and short backtracking to ensure
   non-decreasing objective, until convergence or iteration limit.
"""

import json
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    Deterministic algorithm:
    - Hexagonal-like seed in the unit square
    - Alternate between LP-optimal radii and projected force relocation guided by
      the active constraints at the LP optimum
    - Return final LP-optimal radii and centers
    """
    assert num_circles == 26, "This constructor is specialized for 26 circles."

    # Parameters
    eps = 1e-3  # open-square margin to keep centers away from exact boundary
    # Hexagonal seeding parameters
    centers = _hex_seed_26(eps=eps)

    # Alternating optimization parameters
    max_iters = 40
    rho = 0.95
    # Step schedule scaled by the exact horizontal/vertical pitch as per the seed fix:
    # s0 = 0.1 * min(dx, dy) with dx = (1 - 2*eps)/5 and dy = sqrt(3)/2 * dx
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = np.sqrt(3.0) / 2.0 * dx
    s0 = 0.1 * min(dx, dy)
    tol_improve = 1e-8
    active_tol = 1e-9  # tolerance for identifying active constraints

    # Optimization loop
    best_centers = centers.copy()
    radii, obj = _solve_lp_radii(centers)
    best_radii = radii.copy()
    best_obj = float(np.sum(radii))

    for t in range(max_iters):
        # 1) Solve LP for current centers
        radii, obj = _solve_lp_radii(centers)
        obj_sum = float(np.sum(radii))

        # Track best
        improved = obj_sum > best_obj + tol_improve
        if improved:
            best_obj = obj_sum
            best_centers = centers.copy()
            best_radii = radii.copy()

        # 2) Extract contact graph (active constraints)
        active_pairs, active_boundaries = _extract_active_constraints(centers, radii, tol=active_tol)

        # 3) Build movement vectors from active constraints
        v = _build_movement_vectors(centers, active_pairs, active_boundaries)

        # If no forces, we are locally stable; stop
        if not np.any(v):
            break

        # 4) Deterministic step size with backtracking to ensure non-decreasing objective
        base_step = s0 * (rho**t)
        step = base_step
        accepted = False
        for _ in range(10):
            trial_centers = centers + step * v
            # Project back to the open square [eps, 1 - eps]^2
            trial_centers = np.clip(trial_centers, eps, 1.0 - eps)
            trial_radii, _ = _solve_lp_radii(trial_centers)
            trial_obj = float(np.sum(trial_radii))
            if trial_obj >= obj_sum - 1e-12:
                centers = trial_centers
                radii = trial_radii
                obj_sum = trial_obj
                accepted = True
                break
            step *= 0.5

        if not accepted:
            # Could not improve; stop if minimal progress or vectors too small
            break

        # Early stopping if improvement is tiny
        if abs(obj_sum - best_obj) < tol_improve:
            # One more iteration might still improve; otherwise break in next
            pass

    # Final LP to be safe
    final_radii, _ = _solve_lp_radii(centers)
    return centers, final_radii


def _hex_seed_26(eps: float = 1e-3) -> np.ndarray:
    """Hexagonal-like deterministic seeding for 26 points inside unit square.

    Pattern:
    - 5 rows with counts [6, 5, 5, 5, 5]
    - horizontal pitch dx = (1 - 2*eps)/5 (exactly fits the 6-column row within [eps, 1-eps])
    - vertical pitch dy = sqrt(3)/2 * dx
    - y_r = y0 + r*dy with y0 centered so rows fit; clip to [eps, 1-eps]
    - 6-col row: x = eps + c*dx, c=0..5; 5-col rows offset by dx/2
    - Clip coordinates to [eps, 1 - eps]
    """
    counts = [6, 5, 5, 5, 5]
    n = sum(counts)
    assert n == 26
    # Seed fix: compute dx to exactly fit width [eps, 1-eps] for 6 columns
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = np.sqrt(3.0) / 2.0 * dx
    # Place rows centered vertically (use full unit height; clipping retains the margin)
    y0 = (1.0 - (len(counts) - 1) * dy) / 2.0
    x0 = eps
    centers: List[Tuple[float, float]] = []

    for r, cnt in enumerate(counts):
        y = y0 + r * dy
        if cnt == 6:
            # integer grid columns anchored at x0
            xs = [x0 + c * dx for c in range(cnt)]
        else:
            # hex offset by dx/2
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


def _extract_active_constraints(
    centers: np.ndarray,
    radii: np.ndarray,
    tol: float = 1e-9,
) -> Tuple[List[Tuple[int, int]], List[Tuple[int, str]]]:
    """Identify active constraints at the LP solution.

    Returns:
      - active_pairs: list of (i, j) with r_i + r_j >= d_ij - tol
      - active_boundaries: list of (i, side) for side in {"left","right","bottom","top"} with r_i >= b_i - tol
    """
    n = centers.shape[0]
    dists = _pairwise_distances(centers)
    active_pairs: List[Tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if dists[i, j] <= radii[i] + radii[j] + tol:
                active_pairs.append((i, j))

    active_boundaries: List[Tuple[int, str]] = []
    # b_i = min distance to boundaries
    xs = centers[:, 0]
    ys = centers[:, 1]
    bnd_left = xs
    bnd_right = 1.0 - xs
    bnd_bottom = ys
    bnd_top = 1.0 - ys
    b_i = np.minimum.reduce([bnd_left, bnd_right, bnd_bottom, bnd_top])

    for i in range(n):
        # If boundary constraint is tight
        if radii[i] >= b_i[i] - tol:
            # Determine which side(s) are binding. Deterministic tie-breaking: left, bottom, right, top
            if abs(bnd_left[i] - b_i[i]) <= 1e-12:
                active_boundaries.append((i, "left"))
            if abs(bnd_bottom[i] - b_i[i]) <= 1e-12:
                active_boundaries.append((i, "bottom"))
            if abs(bnd_right[i] - b_i[i]) <= 1e-12:
                active_boundaries.append((i, "right"))
            if abs(bnd_top[i] - b_i[i]) <= 1e-12:
                active_boundaries.append((i, "top"))

    return active_pairs, active_boundaries


def _build_movement_vectors(
    centers: np.ndarray,
    active_pairs: List[Tuple[int, int]],
    active_boundaries: List[Tuple[int, str]],
) -> np.ndarray:
    """Build deterministic movement vectors from the active constraints.

    For active pair (i, j): push i away from j and j away from i along the unit normal (c_i - c_j)/||...||.
    For active boundary: push inward by a unit vector normal to that side.
    Uses unit weights for all contributions.

    Returns:
      v: array of shape (n, 2) with the movement vectors.
    """
    n = centers.shape[0]
    v = np.zeros_like(centers)

    # Active pairs
    for (i, j) in active_pairs:
        diff = centers[i] - centers[j]
        dist = float(np.linalg.norm(diff))
        if dist > 0.0:
            u = diff / dist
            v[i] += u
            v[j] -= u
        else:
            # If exactly coincident (unlikely), push deterministically along x
            u = np.array([1.0, 0.0])
            v[i] += u
            v[j] -= u

    # Active boundaries
    for (i, side) in active_boundaries:
        if side == "left":
            v[i] += np.array([+1.0, 0.0])
        elif side == "right":
            v[i] += np.array([-1.0, 0.0])
        elif side == "bottom":
            v[i] += np.array([0.0, +1.0])
        elif side == "top":
            v[i] += np.array([0.0, -1.0])

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

This implementation follows a deterministic, optimization-driven approach
combining an exact radii LP with two complementary center-move mechanisms:

- Dual/tightness- and degree-weighted contact relaxation steps.
- Periodic micro SLP steps with conservative linearization and small move budgets.

Key features:
- Edge-fitted hexagonal seeding (rows [6,5,5,5,5]) in an open-square margin.
- Exact LP for fixed centers with prebuilt template and lexicographic pair order.
- Deterministic, monotone acceptance with backtracking and non-decreasing objective.
- Scaled, decaying micro SLP budgets with line search and near-active pair prefilter.

All steps are deterministic and require only NumPy (no external solvers).
A small simplex-like LP solver for <=-constraints with nonnegative variables
and nonnegative RHS is included.
"""

import json
from typing import Optional, Tuple, List

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    For N=26, run the full algorithm. For other N, fall back to a simple grid.
    """
    if num_circles != 26:
        centers = _fallback_grid(num_circles)
        radii = _lp_radii_for_fixed_centers(centers)  # safe fallback
        return centers, radii

    # 1) Deterministic edge-fitted hexagonal seeding
    eps = 1e-3
    centers, dx_seed, dy_seed = _edge_fit_hex_seed_26(eps=eps)

    # Prebuild radii LP template and lexicographic pair list
    N = 26
    pair_idx = [(i, j) for i in range(N) for j in range(i + 1, N)]
    radii_lp = _ExactRadiiLPTemplate(N=N, pair_idx=pair_idx)

    # 2) Exact radii LP for fixed centers (deterministic solver)
    radii = radii_lp.solve(centers)

    # 3) Alternating improvement loop:
    #    Contact relaxation steps with monotone backtracking,
    #    plus periodic micro SLP steps that coordinate small global displacements.
    centers, radii = _improve_centers_contact_plus_slp(
        centers,
        radii_lp,
        eps=eps,
        s0=0.1 * min(dx_seed, dy_seed),
        rho=0.95,
        max_outer=50,
        accept_tol=1e-12,
        tol_active=1e-9,
        h0=0.04,
        h_decay=0.7,
        K_slp=3,
    )

    # 4) Final exact LP for radii and clamp
    radii = radii_lp.solve(centers)
    radii = np.nan_to_num(radii, nan=0.0, posinf=0.0, neginf=0.0)
    radii[radii < 0] = 0.0

    return centers, radii


# ----------------------------
# Seeding strategies
# ----------------------------
def _edge_fit_hex_seed_26(eps: float = 1e-3) -> Tuple[np.ndarray, float, float]:
    """Place 26 points in an edge-anchored hexagonal pattern inside [0,1]^2 with margin eps.

    Layout: 5 rows with counts [6, 5, 5, 5, 5].
    Horizontal spacing dx = (1 - 2*eps)/5,
    Vertical spacing dy = (sqrt(3)/2) * dx.

    Vertical positioning is centered using y_k = y0 + k*dy with y0 = 0.5 - 2*dy.

    Edge anchoring in x:
      - For 6-column rows: x_c = eps + c*dx, for c = 0..5 (exactly spans [eps, 1-eps]).
      - For 5-column rows: x_c = eps + (dx/2) + c*dx, for c = 0..4 (staggered by dx/2).

    This replaces prior centering-and-clip logic to allocate the full usable span.
    """
    N = 26
    row_counts = [6, 5, 5, 5, 5]
    assert sum(row_counts) == N

    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx
    y0 = 0.5 - 2.0 * dy

    centers = np.zeros((N, 2), dtype=float)
    idx = 0
    for k, n in enumerate(row_counts):
        yk = y0 + k * dy
        if n == 6:
            # Anchored to [eps, 1-eps] inclusive at both margins
            for c in range(6):
                centers[idx, 0] = eps + c * dx
                centers[idx, 1] = yk
                idx += 1
        else:
            # Staggered by dx/2 relative to 6-column rows
            for c in range(5):
                centers[idx, 0] = eps + 0.5 * dx + c * dx
                centers[idx, 1] = yk
                idx += 1

    return centers, dx, dy


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
# Exact radii LP (template-based)
# ----------------------------
class _ExactRadiiLPTemplate:
    """Prebuilt template for the exact radii LP with fixed centers.

    LP:
      maximize sum r_i
      s.t. r_i >= 0
           r_i <= x_i, r_i <= y_i, r_i <= 1-x_i, r_i <= 1-y_i
           r_i + r_j <= ||c_i - c_j|| for all i<j
    A is constant; only b depends on centers.
    """

    def __init__(self, N: int, pair_idx: List[Tuple[int, int]]):
        self.N = N
        self.pair_idx = pair_idx
        # Build constant A:
        # Wall constraints: 4N rows, each is unit vector e_i
        A_rows = []
        # r_i <= x_i
        for i in range(N):
            row = np.zeros(N, dtype=float)
            row[i] = 1.0
            A_rows.append(row)
        # r_i <= y_i
        for i in range(N):
            row = np.zeros(N, dtype=float)
            row[i] = 1.0
            A_rows.append(row)
        # r_i <= 1 - x_i
        for i in range(N):
            row = np.zeros(N, dtype=float)
            row[i] = 1.0
            A_rows.append(row)
        # r_i <= 1 - y_i
        for i in range(N):
            row = np.zeros(N, dtype=float)
            row[i] = 1.0
            A_rows.append(row)
        # Pairwise constraints
        for (i, j) in pair_idx:
            row = np.zeros(N, dtype=float)
            row[i] = 1.0
            row[j] = 1.0
            A_rows.append(row)

        self.A = np.array(A_rows, dtype=float)
        self.c = np.ones(N, dtype=float)

        # Index offsets for ease in building b
        self.off_x = 0
        self.off_y = self.off_x + N
        self.off_1mx = self.off_y + N
        self.off_1my = self.off_1mx + N
        self.off_pairs = self.off_1my + N
        self.n_rows = self.off_pairs + len(pair_idx)

    def _b_from_centers(self, centers: np.ndarray) -> np.ndarray:
        x = centers[:, 0]
        y = centers[:, 1]
        b = np.zeros(self.n_rows, dtype=float)
        N = self.N
        # r_i <= x_i
        b[self.off_x:self.off_x + N] = x
        # r_i <= y_i
        b[self.off_y:self.off_y + N] = y
        # r_i <= 1 - x_i
        b[self.off_1mx:self.off_1mx + N] = 1.0 - x
        # r_i <= 1 - y_i
        b[self.off_1my:self.off_1my + N] = 1.0 - y
        # pairwise distances
        idx = self.off_pairs
        for (i, j) in self.pair_idx:
            d = float(np.linalg.norm(centers[i] - centers[j]))
            b[idx] = d
            idx += 1
        # Clip to nonnegative (to be very safe)
        b[b < 0] = 0.0
        return b

    def solve(self, centers: np.ndarray) -> np.ndarray:
        """Solve the exact radii LP for given centers."""
        b = self._b_from_centers(centers)
        solver = SimplexLP(c=self.c, A=self.A, b=b, tol=1e-10, max_iters=400000)
        status, r, obj = solver.solve()
        if status != "optimal" or r is None:
            # Fallback: conservative greedy (should not happen).
            r = _greedy_radii_fallback(centers)
        r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
        r[r < 0] = 0.0
        return r


# ----------------------------
# Improvement loop: contact relaxation + micro SLP
# ----------------------------
def _improve_centers_contact_plus_slp(
    centers: np.ndarray,
    radii_lp: _ExactRadiiLPTemplate,
    eps: float = 1e-3,
    s0: float = 0.02,
    rho: float = 0.95,
    max_outer: int = 50,
    accept_tol: float = 1e-12,
    tol_active: float = 1e-9,
    h0: float = 0.04,
    h_decay: float = 0.7,
    K_slp: int = 3,
) -> Tuple[np.ndarray, np.ndarray]:
    """Alternate contact relaxation and micro SLP steps with monotone acceptance."""
    C = centers.copy()
    N = C.shape[0]
    # Exact radii at start
    r = radii_lp.solve(C)
    best_obj = float(np.sum(r))
    best_C = C.copy()
    best_r = r.copy()

    # SLP budgeting
    slp_step_count = 0
    h_t = h0

    # Tracking improvements
    last_improvements: List[float] = []
    accepted_since_slp = 0
    s0_scaled = float(s0)
    reduced_once = False

    for t in range(max_outer):
        # 3a) Contact-based motion proposal
        v = _contact_motion_vectors(C, r, radii_lp.pair_idx, tol_active=tol_active)
        # Normalize v to unit for nonzero vectors
        norms = np.linalg.norm(v, axis=1)
        nz = norms > 0
        if not np.any(nz):
            # Nothing to do
            break
        v[nz] /= norms[nz][:, None]

        # Step schedule
        step_len = s0_scaled * (rho ** t)
        # Backtracking line search (deterministic factors)
        accepted = False
        scale = 1.0
        for _try in range(10):
            C_prop = C + (step_len * scale) * v
            C_prop = np.clip(C_prop, eps, 1.0 - eps)
            r_prop = radii_lp.solve(C_prop)
            obj_prop = float(np.sum(r_prop))
            if obj_prop + accept_tol >= best_obj:
                # Accept monotone move
                C = C_prop
                r = r_prop
                if obj_prop > best_obj + 1e-16:
                    best_obj = obj_prop
                    best_C = C.copy()
                    best_r = r.copy()
                accepted = True
                break
            scale *= 0.5

        if accepted:
            imp = float(np.sum(r) - np.sum(best_r))  # nominal improvement relative to last best snapshot
            last_improvements.append(max(0.0, obj_prop - best_obj))  # store nonnegative
            accepted_since_slp += 1
            # Reduce s0 slightly if stagnating (based on last three accepted improvements)
            if len(last_improvements) >= 3:
                recent = last_improvements[-3:]
                if all(d < 1e-8 for d in recent) and not reduced_once:
                    s0_scaled *= 0.8
                    reduced_once = True
        else:
            # No contact step accepted; will consider SLP next
            pass

        # 3b) Periodic micro SLP step
        trigger_slp = (accepted_since_slp >= K_slp) or (len(last_improvements) >= 3 and all(d < 1e-8 for d in last_improvements[-3:]))
        if trigger_slp:
            accepted_since_slp = 0
            reduced_once = False
            # Build and solve micro SLP with prefilter on near-active pairs
            delta = _micro_slp_displacement(C, r, radii_lp.pair_idx, h_t=h_t)
            # Deterministic line search scales
            ok = False
            for lam in [1.0, 0.5, 0.25, 0.125, 0.0625]:
                if np.allclose(delta, 0.0):
                    break
                C_try = C + lam * delta
                C_try = np.clip(C_try, eps, 1.0 - eps)
                r_try = radii_lp.solve(C_try)
                obj_try = float(np.sum(r_try))
                if obj_try + accept_tol >= best_obj:
                    C = C_try
                    r = r_try
                    if obj_try > best_obj + 1e-16:
                        best_obj = obj_try
                        best_C = C.copy()
                        best_r = r.copy()
                    ok = True
                    break
            # Update budget even if not accepted (as per schedule)
            slp_step_count += 1
            h_t = h0 * (h_decay ** slp_step_count)

            # Early stopping heuristic: if recent improvements tiny and SLP also fails
            if not ok and (len(last_improvements) >= 3 and all(d < 1e-8 for d in last_improvements[-3:])):
                break

        # If we neither accepted a contact step nor improved by SLP in this round, and vectors were zero, break
        # (handled by v==0 check earlier). Otherwise continue.

    # Final exact LP for safety
    r_final = radii_lp.solve(C)
    return C, r_final


def _contact_motion_vectors(
    centers: np.ndarray,
    radii: np.ndarray,
    pair_idx: List[Tuple[int, int]],
    tol_active: float = 1e-9,
) -> np.ndarray:
    """Compute degree-normalized, tightness-weighted contact motion vectors."""
    C = centers
    r = radii
    N = C.shape[0]
    v = np.zeros_like(C)
    deg = np.zeros(N, dtype=float)

    # Active pairs and weights
    for (i, j) in pair_idx:
        ci = C[i]
        cj = C[j]
        diff = ci - cj
        dij = float(np.linalg.norm(diff))
        if dij < 1e-12:
            u = np.array([0.0, 0.0], dtype=float)
            invd = 1.0
        else:
            u = diff / dij
            invd = 1.0 / max(dij, 1e-9)

        if r[i] + r[j] >= dij - tol_active:
            # Tightness measure: how close the pair is to active (clamped)
            gap = dij - (r[i] + r[j])
            tau = _clamp01(1.0 - gap / max(dij, 1e-9))
            # Use tau also as a surrogate for dual weight gamma (no duals available)
            gamma = tau
            w = tau * (1.0 + 0.5 * gamma) * invd
            v[i] += w * u
            v[j] -= w * u
            deg[i] += 1.0
            deg[j] += 1.0

    # Active boundary ties
    x = C[:, 0]
    y = C[:, 1]
    dists = np.vstack([x, y, 1.0 - x, 1.0 - y]).T  # [left, bottom, right, top] distances
    normals = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0], [0.0, -1.0]], dtype=float)

    for i in range(N):
        min_clear = float(np.min(dists[i]))
        # Include all sides within tol of min_clear if boundary is active
        if r[i] >= min_clear - tol_active:
            gap_i = max(0.0, min_clear - r[i])
            # Weight is proportional to how tight to the boundary we are
            denom = max(min_clear, 1e-9)
            w_bi = _clamp01(1.0 - (gap_i / denom))
            # Determine which sides are at the minimum within tol
            for s in range(4):
                if abs(dists[i, s] - min_clear) <= tol_active:
                    v[i] += w_bi * normals[s]
                    deg[i] += 1.0

    # Degree normalization
    for i in range(N):
        if deg[i] > 0:
            v[i] /= (1.0 + deg[i])

    return v


def _clamp01(x: float) -> float:
    return 0.0 if x <= 0.0 else (1.0 if x >= 1.0 else x)


def _micro_slp_displacement(
    centers: np.ndarray,
    radii: np.ndarray,
    pair_idx: List[Tuple[int, int]],
    h_t: float = 0.02,
) -> np.ndarray:
    """Build and solve a single micro SLP to compute a small global displacement.

    Variables: r_i and split nonnegative moves dxp_i, dxm_i, dyp_i, dym_i.
      Δx_i = dxp_i - dxm_i, Δy_i = dyp_i - dym_i.

    Constraints:
      - Wall caps (exact affine):
          r_i <= x_i + Δx_i
          r_i <= y_i + Δy_i
          r_i <= 1 - (x_i + Δx_i)
          r_i <= 1 - (y_i + Δy_i)
      - Pair linearization (conservative):
          r_i + r_j <= d_ij + u_ij^T (Δc_i - Δc_j)
      - Move budget:
          0 <= dxp_i,dxm_i,dyp_i,dym_i <= h_t
      - Box for post-move:
          Δx_i <= 1 - x_i,  -Δx_i <= x_i
          Δy_i <= 1 - y_i,  -Δy_i <= y_i

    Objective: maximize sum r_i.

    Pair prefilter: include only pairs with current slack <= 3*h_t (safe since acceptance
    is conditioned on the exact radii LP).
    """
    C = centers
    r = radii
    N = C.shape[0]

    # Variable indices
    def var_indices(N: int):
        r0 = 0
        dxp0 = r0 + N
        dxm0 = dxp0 + N
        dyp0 = dxm0 + N
        dym0 = dyp0 + N
        total = dym0 + N
        return r0, dxp0, dxm0, dyp0, dym0, total

    r0, dxp0, dxm0, dyp0, dym0, nvar = var_indices(N)

    A_rows = []
    b_vals = []

    # Objective
    c = np.zeros(nvar, dtype=float)
    c[r0:r0 + N] = 1.0

    x = C[:, 0]
    y = C[:, 1]

    def add_row(coeffs: List[Tuple[int, float]], rhs: float):
        row = np.zeros(nvar, dtype=float)
        for j, v in coeffs:
            row[j] = v
        b_vals.append(max(0.0, float(rhs)))
        A_rows.append(row)

    # Walls (affine and exact)
    for i in range(N):
        # r <= x + Δx -> r - dxp + dxm <= x
        add_row([(r0 + i, 1.0), (dxp0 + i, -1.0), (dxm0 + i, 1.0)], x[i])
        # r <= y + Δy
        add_row([(r0 + i, 1.0), (dyp0 + i, -1.0), (dym0 + i, 1.0)], y[i])
        # r <= 1 - (x + Δx) -> r + dxp - dxm <= 1 - x
        add_row([(r0 + i, 1.0), (dxp0 + i, 1.0), (dxm0 + i, -1.0)], 1.0 - x[i])
        # r <= 1 - (y + Δy)
        add_row([(r0 + i, 1.0), (dyp0 + i, 1.0), (dym0 + i, -1.0)], 1.0 - y[i])

    # Pairwise constraints (prefilter near-active)
    # Precompute slack to prefilter
    # slack_ij = d_ij - (r_i + r_j)
    for (i, j) in pair_idx:
        diff = C[i] - C[j]
        dij = float(np.linalg.norm(diff))
        if dij > 1e-12:
            u = diff / dij
        else:
            # Deterministic fixed axis if nearly coincident
            u = np.array([1.0, 0.0], dtype=float)
        slack_ij = dij - (r[i] + r[j])
        if slack_ij <= 3.0 * h_t:
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

    # Move budget bounds
    for i in range(N):
        add_row([(dxp0 + i, 1.0)], h_t)
        add_row([(dxm0 + i, 1.0)], h_t)
        add_row([(dyp0 + i, 1.0)], h_t)
        add_row([(dym0 + i, 1.0)], h_t)

    # Keep centers inside [0,1] post-move (linear)
    for i in range(N):
        # Δx <= 1 - x
        add_row([(dxp0 + i, 1.0), (dxm0 + i, -1.0)], 1.0 - x[i])
        # -Δx <= x -> -dxp + dxm <= x
        add_row([(dxp0 + i, -1.0), (dxm0 + i, 1.0)], x[i])
        # Δy <= 1 - y
        add_row([(dyp0 + i, 1.0), (dym0 + i, -1.0)], 1.0 - y[i])
        # -Δy <= y
        add_row([(dyp0 + i, -1.0), (dym0 + i, 1.0)], y[i])

    A = np.array(A_rows, dtype=float)
    b = np.array(b_vals, dtype=float)

    solver = SimplexLP(c=c, A=A, b=b, tol=1e-10, max_iters=400000)
    status, sol, obj = solver.solve()
    if status != "optimal" or sol is None:
        return np.zeros_like(C)

    # Extract Δ
    dx = sol[dxp0:dxp0 + N] - sol[dxm0:dxm0 + N]
    dy = sol[dyp0:dyp0 + N] - sol[dym0:dym0 + N]
    delta = np.stack([dx, dy], axis=1)
    return delta


# ----------------------------
# Fallback exact radii (used for non-26 N or as LP fallback)
# ----------------------------
def _lp_radii_for_fixed_centers(centers: np.ndarray) -> np.ndarray:
    """Solve the exact LP for radii with fixed centers (generic fallback)."""
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

    solver = SimplexLP(c=c, A=A, b=b, tol=1e-10, max_iters=200000)
    status, r, obj = solver.solve()
    if status != "optimal" or r is None:
        r = _greedy_radii_fallback(centers)
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
