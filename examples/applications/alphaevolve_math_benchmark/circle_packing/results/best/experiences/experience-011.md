Actionable insight about why SLP-HexPack performed well on 26-circle unit-square packing and what to reuse.

- SLP-HexPack: Deterministic Sequential Linearized Packing: Seeding 26 centers with a deterministic near-hexagonal lattice (five staggered rows with counts [5, 5, 5, 5, 6]) and then computing radii via an exact LP for fixed centers directly targets the max-sum objective and beats greedy pairwise shrinking. The algorithm further applies a conservative SLP loop that linearizes pairwise distance constraints and bounds per-iteration moves (e.g., initial move budget ≈0.04 with 0.7 decay), ensuring feasibility while monotonically increasing the achievable sum of radii. On this instance it achieved sum_radii = 2.607924880131376 with validity = 1.0, evidencing the effectiveness and determinism of the approach. Reuse this pattern in packing/placement tasks: start from a high-quality deterministic lattice seed, optimize sizes with an exact LP, adjust positions via bounded-step linearized constraints, and finish with a final exact LP polish.

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 disjoint circles in the unit square.

This implementation follows a deterministic, optimization-driven approach:

1) Deterministic hexagonal-like seeding of 26 centers.
2) Exact (for fixed centers) linear program to maximize sum of radii.
3) Sequential linearized improvement (SLP): small center moves are optimized
   via LP with a conservative linearization of inter-center distances.
4) Final exact LP for radii at the improved centers.

All steps are deterministic and require only NumPy (no external solvers).
A small two-phase-simplex-like LP solver for <=-constraints with nonnegative
variables and nonnegative RHS is included.

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

    # 1) Deterministic hexagonal-like seeding
    centers = _hex_seed_26()

    # 2) Exact radii LP for fixed centers
    radii = _lp_radii_for_fixed_centers(centers)

    # 3) Sequential linearized improvement of centers (SLP)
    centers, radii = _slp_improve_centers(centers, iters=14, h0=0.04, decay=0.7)

    # 4) Final exact LP for radii and a light polish
    radii = _lp_radii_for_fixed_centers(centers)

    return centers, radii


# ----------------------------
# Seeding strategies
# ----------------------------
def _hex_seed_26() -> np.ndarray:
    """Place 26 points in a near-hexagonal lattice inside [0,1]^2 with margin."""
    N = 26
    centers = np.zeros((N, 2), dtype=float)

    # Layout: 5 rows with counts [5, 6, 5, 5, 5] = 26
    row_counts = [5, 6, 5, 5, 5]
    R = len(row_counts)
    assert sum(row_counts) == N

    margin = 0.045  # small safety margin from the square walls

    # Choose horizontal spacing sx and vertical spacing sy ~ sqrt(3)/2 * sx
    # Constraints so the widest row and the vertical stack fit within [margin, 1-margin].
    n_max = max(row_counts)
    horiz_cap = (1 - 2 * margin) / (n_max - 1)  # <= constraint for width
    # Vertical cap using hex ratio
    vert_cap = (1 - 2 * margin) / (R - 1)
    sx = min(horiz_cap, (2.0 / np.sqrt(3.0)) * vert_cap)
    sy = (np.sqrt(3.0) / 2.0) * sx

    # Vertically center rows (guaranteed within margins with chosen sx, sy)
    y0 = 0.5 - 0.5 * (R - 1) * sy

    # Horizontal center for each row, optionally with stagger offset
    # We'll alternate offsets 0 and sx/2 and keep the 6-count row aligned with 0 offset.
    # Pattern chosen: offsets = [sx/2, 0, sx/2, 0, sx/2]
    offsets = [0.5 * sx, 0.0, 0.5 * sx, 0.0, 0.5 * sx]

    idx = 0
    for k, n in enumerate(row_counts):
        yk = y0 + k * sy
        # Center each row horizontally
        row_width = (n - 1) * sx
        x_center = 0.5
        x_start = x_center - 0.5 * row_width + offsets[k]
        # Clamp start so the row remains within [margin, 1 - margin]
        x_start = max(margin, min(x_start, 1 - margin - row_width))
        for i in range(n):
            centers[idx, 0] = x_start + i * sx
            centers[idx, 1] = yk
            idx += 1

    # Just to be safe, clip to [margin, 1-margin]
    centers = np.clip(centers, margin, 1 - margin)
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
# LP Model Builders
# ----------------------------
def _lp_radii_for_fixed_centers(centers: np.ndarray) -> np.ndarray:
    """Solve the exact LP for radii with fixed centers:
        maximize sum r_i
        s.t. r_i >= 0
             r_i <= distances to walls
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


def _slp_improve_centers(
    centers: np.ndarray, iters: int = 12, h0: float = 0.04, decay: float = 0.7
) -> Tuple[np.ndarray, np.ndarray]:
    """Sequential Linear Programming to improve center positions and radii.

    LP variables: r_i, dxp_i, dxm_i, dyp_i, dym_i (all >= 0). Δx = dxp - dxm, Δy = dyp - dym.

    Constraints:
      - r_i <= x_i + Δx_i                (wall)
      - r_i <= y_i + Δy_i
      - r_i <= 1 - (x_i + Δx_i)
      - r_i <= 1 - (y_i + Δy_i)
      - r_i + r_j <= d_ij(c) + u_ij^T(Δc_i - Δc_j) (linearized pair distances)
      - |Δx_i| <= h_t, |Δy_i| <= h_t
      - 0 <= x_i + Δx_i <= 1, 0 <= y_i + Δy_i <= 1

    Objective: maximize sum r_i.
    """
    C = centers.copy()
    N = C.shape[0]
    r_best = _lp_radii_for_fixed_centers(C)

    # Pre-allocate variable indices
    def var_indices(N: int):
        r0 = 0
        dxp0 = r0 + N
        dxm0 = dxp0 + N
        dyp0 = dxm0 + N
        dym0 = dyp0 + N
        total = dym0 + N
        return r0, dxp0, dxm0, dyp0, dym0, total

    for t in range(iters):
        h_t = h0 * (decay ** t)
        r0, dxp0, dxm0, dyp0, dym0, nvar = var_indices(N)

        A_rows = []
        b_vals = []

        x = C[:, 0]
        y = C[:, 1]

        # Objective: maximize sum r_i
        c = np.zeros(nvar, dtype=float)
        c[r0:r0 + N] = 1.0

        # Helper to add a constraint row
        def add_row(coeffs: List[Tuple[int, float]], rhs: float):
            row = np.zeros(nvar, dtype=float)
            for j, v in coeffs:
                row[j] = v
            # Enforce nonnegative RHS
            b_vals.append(max(0.0, rhs))
            A_rows.append(row)

        # Wall constraints (linearized but exact since walls are affine)
        for i in range(N):
            # r_i <= x_i + Δx_i => r_i - dxp_i + dxm_i <= x_i
            add_row([(r0 + i, 1.0), (dxp0 + i, -1.0), (dxm0 + i, 1.0)], x[i])
            # r_i <= y_i + Δy_i
            add_row([(r0 + i, 1.0), (dyp0 + i, -1.0), (dym0 + i, 1.0)], y[i])
            # r_i <= 1 - (x_i + Δx_i) => r_i + dxp_i - dxm_i <= 1 - x_i
            add_row([(r0 + i, 1.0), (dxp0 + i, 1.0), (dxm0 + i, -1.0)], 1.0 - x[i])
            # r_i <= 1 - (y_i + Δy_i)
            add_row([(r0 + i, 1.0), (dyp0 + i, 1.0), (dym0 + i, -1.0)], 1.0 - y[i])

        # Pairwise constraints using a conservative linearization
        for i in range(N):
            for j in range(i + 1, N):
                diff = C[i] - C[j]
                dij = float(np.linalg.norm(diff))
                if dij > 1e-12:
                    u = diff / dij
                else:
                    # If centers coincide, direction is undefined; use zero direction (safe)
                    u = np.array([0.0, 0.0], dtype=float)
                ux, uy = float(u[0]), float(u[1])
                # r_i + r_j - u^T(Δc_i - Δc_j) <= d_ij
                # => r_i + r_j - ux*(dxp_i - dxm_i) - uy*(dyp_i - dym_i)
                #                 + ux*(dxp_j - dxm_j) + uy*(dyp_j - dym_j) <= d_ij
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

        # Move budget: |Δx_i| <= h_t, |Δy_i| <= h_t
        for i in range(N):
            add_row([(dxp0 + i, 1.0)], h_t)
            add_row([(dxm0 + i, 1.0)], h_t)
            add_row([(dyp0 + i, 1.0)], h_t)
            add_row([(dym0 + i, 1.0)], h_t)

        # Keep centers within the square after move: 0 <= x_i + Δx_i <= 1
        for i in range(N):
            # Δx_i = dxp_i - dxm_i
            # Upper bound: dxp_i - dxm_i <= 1 - x_i
            add_row([(dxp0 + i, 1.0), (dxm0 + i, -1.0)], 1.0 - x[i])
            # Lower bound: -(dxp_i - dxm_i) <= x_i  => -dxp_i + dxm_i <= x_i
            add_row([(dxp0 + i, -1.0), (dxm0 + i, 1.0)], x[i])
            # For y
            add_row([(dyp0 + i, 1.0), (dym0 + i, -1.0)], 1.0 - y[i])
            add_row([(dyp0 + i, -1.0), (dym0 + i, 1.0)], y[i])

        A = np.array(A_rows, dtype=float)
        b = np.array(b_vals, dtype=float)

        # Solve LP
        solver = SimplexLP(c=c, A=A, b=b, tol=1e-10, max_iters=400000)
        status, sol, obj = solver.solve()
        if status != "optimal" or sol is None:
            # If LP solve fails, stop improvement and return last known good
            break

        # Update centers with Δ
        dx = sol[dxp0:dxp0 + N] - sol[dxm0:dxm0 + N]
        dy = sol[dyp0:dyp0 + N] - sol[dym0:dym0 + N]
        C[:, 0] = np.clip(C[:, 0] + dx, 0.0, 1.0)
        C[:, 1] = np.clip(C[:, 1] + dy, 0.0, 1.0)

        # Track current radii from this LP (optimal for the linearized constraints)
        r_curr = sol[r0:r0 + N]
        # Keep the best radii we have seen (optional, usually monotone)
        if np.sum(r_curr) > np.sum(r_best) + 1e-12:
            r_best = r_curr

    # After SLP, recompute exact radii for the final centers
    r_final = _lp_radii_for_fixed_centers(C)
    return C, r_final


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
