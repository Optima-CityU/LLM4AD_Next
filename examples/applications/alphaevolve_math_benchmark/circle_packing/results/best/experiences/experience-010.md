Deterministic LP radii with active-constraint center updates and hexagonal seeding achieved sum_radii 2.5002760967885593 with validity 1.0.

- HexSeed + LP Radii + Projected Force Relaxation: Seeding centers on a near-hexagonal grid that covers edges and corners, then at each iteration solving a deterministic LP (primal simplex with Bland’s rule) to maximize the sum of radii for fixed centers and moving centers along the unit normals of LP-active constraints with a projected force step and backtracking to enforce non-decreasing objective, produced a valid packing with sum_radii 2.5002760967885593 and validity 1.0; future designs for this bounded circle-packing objective should reuse this alternation (LP-optimal radii + active-constraint-guided deterministic relocation) to grow the contact-limited gaps while maintaining reproducibility.
- HexSeed + LP Radii + Active-Set Projected Relaxation: combined near-hexagonal seeding with rows [6, 5, 5, 5, 5], an LP maximizing the sum of radii under boundary and pairwise non-overlap constraints, and active-constraint–guided projected relaxation with monotone backtracking, achieving sum_radii 2.51089949328704 with validity 1.0 on packing 26 circles in the unit square.
- Active-constraint–guided projected relaxation: moved centers along separating normals of LP-identified tight constraints, normalized per-center updates, clipped positions to [eps, 1−eps], and accepted only steps that did not decrease the LP objective, providing stable, monotone improvements across iterations.
- Deterministic LP solver with Bland’s rule: used a primal simplex with Bland’s rule and lexicographic tie-breaking to produce reproducible pivots and outcomes when maximizing the radii sum, supporting deterministic alternation with the relaxation steps.
- Hexagonal seeding with rows [6, 5, 5, 5, 5]: distributed centers more uniformly than the anisotropic six-row layout [5, 4, 5, 4, 4, 4], reducing initial geometric bottlenecks and leaving more exploitable slack for the LP to increase the objective.

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
    s0 = 0.1 * min(1.0 / (6 - 1), np.sqrt(3) / 2 * (1.0 / (6 - 1)))  # ~0.0173 for our seed
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
    - horizontal pitch dx = 1/(6-1)
    - vertical pitch dy = sqrt(3)/2 * dx
    - y_r = y0 + r*dy with y0 centered so rows fit; clip to [eps, 1-eps]
    - 6-col row: x = eps + c*dx, c=0..5; 5-col rows offset by dx/2
    - Clip coordinates to [eps, 1 - eps]
    """
    counts = [6, 5, 5, 5, 5]
    n = sum(counts)
    assert n == 26
    dx = 1.0 / (6 - 1)
    dy = np.sqrt(3.0) / 2.0 * dx
    # Place rows centered vertically
    y0 = (1.0 - (len(counts) - 1) * dy) / 2.0
    x0 = eps
    centers: List[Tuple[float, float]] = []

    for r, cnt in enumerate(counts):
        y = y0 + r * dy
        if cnt == 6:
            # integer grid columns
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
    nonbasic_set = set(range(n))  # Only consider r variables for entering (deterministic and efficient)
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
"""Deterministic hex-seeded packing of 26 circles in the unit square."""

import json

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """
    Construct centers and radii for a valid packing of 26 circles using:
      - Deterministic hexagonal seeding (rows [6, 5, 5, 5, 5])
      - Deterministic LP for radii (maximize sum of radii with boundary and non-overlap)
      - Active-constraint-guided projected force relaxation with monotone backtracking

    Algorithm outline:
      1) Seed 26 centers on a near-hexagonal lattice:
         - Rows: [6, 5, 5, 5, 5]
         - dx = 1 / (6 - 1), dy = (sqrt(3)/2) * dx
         - Vertically center rows: y_r = y0 + r * dy, where y0 centers the stack
         - For 6-col row: x = eps + c * dx, c = 0..5
           For 5-col rows: x = eps + (dx/2) + c * dx, c = 0..4
         - Use an open-square margin eps and clip all centers into [eps, 1 - eps]^2
      2) For fixed centers, solve the LP:
           maximize   sum_i r_i
           subject to r_i >= 0
                      r_i <= min(x_i, y_i, 1 - x_i, 1 - y_i)
                      r_i + r_j <= ||c_i - c_j|| for all i < j
         using a deterministic primal simplex with Bland’s rule.
      3) Extract active constraints (pair distances and boundary clearances) and
         compute a movement vector by summing separating normals (pairs) and inward
         normals (boundaries) with deterministic tie-breaking.
      4) Take a small step along the normalized movement vectors with geometric
         decay and backtracking that accepts only non-decreasing LP objectives.
         After each accepted step, clip centers into [eps, 1 - eps]^2.
      5) Alternate LP and relaxation up to a fixed small number of iterations or
         stop early on degeneracy/monotone failure/small improvements. Perform a
         final LP and clamp tiny negative radii to zero.

    Returns:
        centers: shape (26, 2), array of (x, y) center coordinates
        radii:   shape (26,), array of LP-optimized radii (non-negative, finite)
    """
    if num_circles != 26:
        raise ValueError("This constructor is designed specifically for 26 circles.")

    # Deterministic hexagonal seeding for 26 circles.
    # - Row pattern: [6, 5, 5, 5, 5]
    # - dx, dy define the hex spacing; we clip into [eps, 1-eps] for robustness.
    eps = 1e-3
    row_counts = [6, 5, 5, 5, 5]
    rows = len(row_counts)
    # Horizontal spacing for the widest row (6 columns)
    dx = 1.0 / (6.0 - 1.0)
    dy = (np.sqrt(3.0) / 2.0) * dx
    # Vertically center the rows within [0,1]; we will clip with eps after placement.
    y0 = 0.5 - 0.5 * (rows - 1) * dy

    centers_list = []
    for r in range(rows):
        n_cols = row_counts[r]
        y_r = y0 + r * dy
        # 5-column rows are staggered by dx/2 relative to the 6-column row
        x_offset = 0.0 if n_cols == 6 else (dx / 2.0)
        for c in range(n_cols):
            x = eps + x_offset + c * dx
            y = y_r
            centers_list.append([x, y])

    centers = np.asarray(centers_list, dtype=float)
    if centers.shape != (num_circles, 2):
        raise RuntimeError(f"Expected {num_circles} centers, got {centers.shape[0]}")

    # Clip centers into the open-square margin to avoid degeneracies at the boundary.
    centers = np.clip(centers, eps, 1.0 - eps)

    # Initial LP solve to maximize sum of radii for these fixed centers.
    radii, info = _solve_lp_radii(centers)
    obj_prev = float(np.sum(radii))

    # Deterministic contact-relaxation parameters
    tol_active = 1e-9
    # Small step relative to lattice spacings
    s0 = 0.1 * float(min(dx, dy))
    rho = 0.95
    max_relax_iters = 35
    backtrack_max = 10
    backtrack_shrink = 0.5
    small_improve_tol = 1e-8
    small_improve_patience = 3
    no_improve_count = 0

    # Alternate LP with small deterministic center motions driven by active constraints.
    for t in range(max_relax_iters):
        # Extract active constraints and build movement vector
        v = _build_movement_vector(centers, radii, tol_active)
        if not np.isfinite(v).all():
            # Defensive guard: handle any NaN/Inf by zeroing them
            v = np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)

        # If the movement vector is all zeros, we're at a stationary configuration
        if np.allclose(v, 0.0, atol=0.0, rtol=0.0):
            break

        # Normalize v row-wise (per-center). Centers with zero v remain fixed.
        norms = np.linalg.norm(v, axis=1)
        move_mask = norms > 0.0
        if np.any(move_mask):
            v[move_mask] /= norms[move_mask][:, None]

        # Step size schedule
        step_len = s0 * (rho ** t)

        # Backtracking to ensure objective does not decrease
        accepted = False
        best_centers = centers
        best_radii = radii
        best_obj = obj_prev

        for _ in range(backtrack_max):
            candidate_centers = centers + step_len * v
            # Project back into [eps, 1 - eps]^2
            candidate_centers = np.clip(candidate_centers, eps, 1.0 - eps)
            # Re-solve LP for candidate centers
            cand_radii, cand_info = _solve_lp_radii(candidate_centers)
            cand_obj = float(np.sum(cand_radii))
            # Accept if objective does not decrease (deterministic)
            if cand_obj + 1e-12 >= obj_prev:
                best_centers = candidate_centers
                best_radii = cand_radii
                best_obj = cand_obj
                accepted = True
                break
            # Otherwise shrink step deterministically
            step_len *= backtrack_shrink

        # If we failed to accept any step, stop early
        if not accepted:
            break

        # Commit the accepted step
        improvement = best_obj - obj_prev
        centers = best_centers
        radii = best_radii
        obj_prev = best_obj

        # Track small improvements to stop early if stagnating
        if improvement < small_improve_tol:
            no_improve_count += 1
            if no_improve_count >= small_improve_patience:
                break
        else:
            no_improve_count = 0

    # Final LP solve for the last centers to ensure consistency
    radii, info = _solve_lp_radii(centers)
    # Light post-processing clamp: zero-out tiny negatives due to numerical noise.
    radii = np.clip(radii, 0.0, np.finfo(float).max)

    return centers, radii


def _build_movement_vector(centers: np.ndarray, radii: np.ndarray, tol: float) -> np.ndarray:
    """
    Build a per-center movement vector by summing:
      - normalized separating directions for active pairs (tight r_i + r_j constraints)
      - inward normals for active boundary constraints

    Active constraints (deterministic extraction):
      - Pair (i,j) active if r_i + r_j >= ||c_i - c_j|| - tol
      - Boundary for i active if r_i >= min(x_i, y_i, 1-x_i, 1-y_i) - tol
        Inward normal is away from the side attaining the minimum; ties broken by fixed order:
          left (x), bottom (y), right (1-x), top (1-y).

    Args:
        centers: (n,2) array
        radii: (n,) array
        tol: tolerance for activeness

    Returns:
        v: (n,2) movement vectors (sum of contributions; not yet normalized)
    """
    n = centers.shape[0]
    v = np.zeros_like(centers, dtype=float)

    # Pairwise distances and directions
    diffs = centers[:, None, :] - centers[None, :, :]
    dists = np.sqrt(np.sum(diffs * diffs, axis=2))

    # Active pairwise constraints; deterministic lexicographic order (i<j increasing)
    for i in range(n):
        for j in range(i + 1, n):
            dij = dists[i, j]
            if not np.isfinite(dij):
                continue
            if radii[i] + radii[j] >= dij - tol:
                # Push i and j apart along the line connecting them
                if dij > 1e-15:
                    direction = diffs[i, j] / dij  # from j to i
                    v[i] += direction
                    v[j] -= direction
                # If dij is extremely small, skip to maintain stability/determinism

    # Active boundary constraints with fixed tie-breaking order
    x = centers[:, 0]
    y = centers[:, 1]
    # Clearances to each side: left, bottom, right, top
    clear_left = x
    clear_bottom = y
    clear_right = 1.0 - x
    clear_top = 1.0 - y

    for i in range(n):
        # Minimum clearance and active check
        min_clearance = min(clear_left[i], clear_bottom[i], clear_right[i], clear_top[i])
        if radii[i] >= min_clearance - tol:
            # Determine which side(s) achieve the minimum; fixed order: left, bottom, right, top
            # We choose the first in this order for determinism.
            side = None
            if np.isclose(clear_left[i], min_clearance, rtol=0.0, atol=tol):
                side = 0  # left
            elif np.isclose(clear_bottom[i], min_clearance, rtol=0.0, atol=tol):
                side = 1  # bottom
            elif np.isclose(clear_right[i], min_clearance, rtol=0.0, atol=tol):
                side = 2  # right
            else:
                side = 3  # top (remaining)
            # Inward normals: left -> +x, bottom -> +y, right -> -x, top -> -y
            if side == 0:
                v[i, 0] += 1.0
            elif side == 1:
                v[i, 1] += 1.0
            elif side == 2:
                v[i, 0] -= 1.0
            else:
                v[i, 1] -= 1.0

    return v


def _solve_lp_radii(centers: np.ndarray):
    """
    Build and solve the LP:
        maximize   sum_i r_i
        subject to r_i >= 0
                   r_i <= edge_clearance_i               (boundary constraints)
                   r_i + r_j <= distance(c_i, c_j)       (pairwise constraints)
    using a deterministic primal simplex with Bland’s rule.

    Args:
        centers: array (n, 2)

    Returns:
        radii: optimal radii (n,)
        info:  dict with solver diagnostics (objective, iterations, status)
    """
    n = centers.shape[0]
    # Boundary clearances
    edge_clearance = np.minimum.reduce(
        [centers[:, 0], centers[:, 1], 1.0 - centers[:, 0], 1.0 - centers[:, 1]]
    )
    # Pairwise distances (i < j)
    diffs = centers[:, None, :] - centers[None, :, :]
    dists = np.sqrt(np.sum(diffs * diffs, axis=2))
    # Build A and b for A r <= b
    # First n rows: unit vectors for r_i <= edge_i
    # Next rows: for each i<j, row has 1 at i and 1 at j; RHS = d_ij
    pair_indices = []
    for i in range(n):
        for j in range(i + 1, n):
            pair_indices.append((i, j))
    m = n + len(pair_indices)

    # Allocate A and b
    A = np.zeros((m, n), dtype=float)
    b = np.zeros((m,), dtype=float)

    # Boundary constraints
    for i in range(n):
        A[i, i] = 1.0
        b[i] = edge_clearance[i]

    # Pairwise constraints
    for idx, (i, j) in enumerate(pair_indices, start=n):
        A[idx, i] = 1.0
        A[idx, j] = 1.0
        b[idx] = dists[i, j]

    # Objective c for r variables (maximize sum r_i)
    c = np.ones((n,), dtype=float)

    # Solve with primal simplex (standard form, add slack variables)
    radii, info = _primal_simplex_max_standard(A, b, c)

    return radii, info


def _primal_simplex_max_standard(A: np.ndarray, b: np.ndarray, c: np.ndarray):
    """
    Solve maximize c^T x subject to A x <= b, x >= 0 using primal simplex with slacks.

    Implementation details:
    - Add slack variables s >= 0: A x + s = b.
    - Initialize BFS with x = 0, s = b (requires b >= 0). This holds for our problem.
    - Use Bland's rule for deterministic pivot selection:
        * Entering variable: smallest index j with reduced cost r_j > tol.
        * Leaving variable: among rows with a_ij > tol, choose the smallest row index
          achieving the minimum ratio b_i / a_ij.
    - Maintain tableau with reduced costs r_j = c_j - z_j in the last row.

    Args:
        A: (m, n) constraint matrix
        b: (m,) RHS (must be >= 0 for feasibility)
        c: (n,) objective coefficients

    Returns:
        x: (n,) primal optimal solution
        info: dict with status and iterations
    """
    m, n = A.shape
    # Guard: ensure RHS non-negative (required for initial BFS)
    if np.any(b < -1e-12):
        raise ValueError("RHS b must be non-negative for initial BFS (got negative entries).")

    # Build initial tableau:
    # Columns: [x (n) | s (m) | rhs]
    total_vars = n + m
    tableau = np.zeros((m + 1, total_vars + 1), dtype=float)

    # Constraint rows: [A | I | b]
    tableau[:m, :n] = A
    tableau[:m, n:n + m] = np.eye(m, dtype=float)
    tableau[:m, -1] = b

    # Objective row: reduced costs initially r_j = c_j for x, 0 for slacks; RHS = 0
    tableau[m, :n] = c
    # Slacks have zero obj coeff; initial reduced costs zero
    tableau[m, n:n + m] = 0.0
    tableau[m, -1] = 0.0

    # Basis: start with slacks as basic variables
    basis = np.arange(n, n + m, dtype=int)  # indices of columns that are basic for each row
    # Tolerances
    tol_enter = 1e-12
    tol_pivot = 1e-12
    max_iters = 100000  # generous cap; problem is small

    iterations = 0
    status = "optimal"

    while True:
        iterations += 1
        if iterations > max_iters:
            status = "iteration_limit"
            break

        # Reduced costs in last row (excluding RHS)
        reduced_costs = tableau[m, :total_vars]
        # Bland's rule: pick smallest-index entering variable with r_j > tol_enter
        enter_candidates = [j for j in range(total_vars) if reduced_costs[j] > tol_enter]
        if not enter_candidates:
            # Optimal: no positive reduced cost
            break
        entering = min(enter_candidates)  # Bland's rule (smallest index)

        # Ratio test: rows i with a_ij > tol; compute b_i / a_ij
        col = tableau[:m, entering]
        positive_rows = np.where(col > tol_pivot)[0]
        if positive_rows.size == 0:
            # Unbounded (should not happen in our bounded geometry)
            status = "unbounded"
            break

        ratios = tableau[positive_rows, -1] / col[positive_rows]
        min_ratio = np.min(ratios)
        # Tie-breaking: smallest row index among those achieving min ratio (Bland's rule)
        tie_rows = positive_rows[np.where(np.isclose(ratios, min_ratio, rtol=1e-12, atol=1e-12))[0]]
        leaving_row = int(np.min(tie_rows))

        pivot_val = tableau[leaving_row, entering]
        # Normalize pivot row
        tableau[leaving_row, :] = tableau[leaving_row, :] / pivot_val

        # Eliminate column 'entering' from all other rows (including objective row)
        for i in range(m + 1):
            if i == leaving_row:
                continue
            factor = tableau[i, entering]
            if factor != 0.0:
                tableau[i, :] -= factor * tableau[leaving_row, :]

        # Update basis
        basis[leaving_row] = entering

    # Extract solution x (n,)
    x = np.zeros((n,), dtype=float)
    for i in range(m):
        col_idx = basis[i]
        if col_idx < n:
            # basic x variable
            x[col_idx] = tableau[i, -1]

    # Clamp tiny negatives to zero
    x = np.where(x < 0.0, 0.0, x)

    info = {
        "status": status,
        "iterations": iterations,
        "objective": float(np.sum(x)),
    }
    return x, info


def compute_uniform_radii(centers: np.ndarray, base_radius: float) -> np.ndarray:
    """
    Compute per-circle radii from:
    - base_radius r
    - edge clearance for each center (distance to the square boundary)
    - half of the nearest-neighbor distance

    In this construction, all radii equal base_radius.
    This function applies conservative min bounds to guard against floating-point effects.
    """
    n = centers.shape[0]
    # Edge clearance for each center (minimum distance to the square boundary)
    edge_clearance = np.minimum.reduce(
        [centers[:, 0], centers[:, 1], 1.0 - centers[:, 0], 1.0 - centers[:, 1]]
    )

    # Compute nearest-neighbor distances (O(n^2) is fine for n=26)
    diffs = centers[:, None, :] - centers[None, :, :]
    dists = np.sqrt(np.sum(diffs * diffs, axis=2))
    np.fill_diagonal(dists, np.inf)
    nearest = np.min(dists, axis=1)

    # Radius per circle: conservative minimum of all bounds
    radii = np.minimum(base_radius, edge_clearance)
    radii = np.minimum(radii, 0.5 * nearest)

    # Ensure non-negative finite radii
    radii = np.clip(radii, 0.0, np.finfo(float).max)
    return radii


def compute_max_radii(centers):
    """
    Deprecated greedy shrinker retained for backward compatibility.

    This function is not used by the constructor. It greedily scales
    overlapping radii and typically underperforms the analytical lattice bounds.
    """
    radii = np.minimum.reduce([centers[:, 0], centers[:, 1], 1 - centers[:, 0], 1 - centers[:, 1]])
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            distance = float(np.linalg.norm(centers[i] - centers[j]))
            radius_sum = float(radii[i] + radii[j])
            if radius_sum > distance and radius_sum > 0:
                scale = distance / radius_sum
                radii[i] *= scale
                radii[j] *= scale
    return radii
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
