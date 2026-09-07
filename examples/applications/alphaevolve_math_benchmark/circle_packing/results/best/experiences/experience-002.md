Deterministic pipeline elements and parameterized choices that improved sum of radii for packing 26 circles while preserving validity and reproducibility.

- Edge-fit Hex Seed + Weighted Active-Set LP-Relaxation: For packing 26 circles in [0,1]^2, the algorithm seeded a hex grid with eps ≈ 1e−3, dx = (1 − 2·eps)/5, dy = (√3/2)·dx, and row counts [6, 5, 5, 5, 5], which avoided initial clipping and balanced horizontal spacing.
- Deterministic LP radii solver (Bland’s rule): The solver maximized the sum of radii with constraints r_i ≥ 0, r_i ≤ min{x_i, y_i, 1−x_i, 1−y_i}, and r_i + r_j ≤ ||c_i − c_j|| using a primal simplex with Bland’s rule and lexicographic tie-breaking, ensuring reproducible allocations.
- Weighted active-set force construction: Active pair constraints contributed inverse-distance–weighted unit normals while simultaneously active boundary sides contributed summed inward normals, yielding unbiased, corner-aware movement directions that prioritize the tightest contacts.
- Projected relaxation with monotone backtracking: Trial moves used step_t = s0·ρ^t with s0 = 0.1·min(dx, dy) and ρ ≈ 0.95, were clipped to [eps, 1−eps]^2, and were accepted only if the total sum of radii did not decrease, guaranteeing non-decreasing objectives across iterations.
- Edge-fit Hex Seed + Weighted Active-Set LP-Relaxation performance: On the 26-circle task, the algorithm achieved sum_radii = 2.515001799918381 with validity = 1.0, improving over Parent 1 (2.51089949328704) and Parent 2 (2.506866862513275).
- Reusable strategy for deterministic circle packing: When seeding dense circle packs in bounded boxes, reuse edge-fit hex seeding sized to the usable width together with inverse-distance–weighted active-set relaxation and a Bland’s-rule LP with monotone acceptance to reduce early degeneracy and increase achievable sum of radii without sacrificing determinism.
- Edge-fit Hex Seed + Weighted Active-Set LP-Relaxation: A deterministic two-stage loop—solving an exact LP with Bland’s rule to maximize the sum of radii for fixed centers, then moving centers along inverse-distance–weighted normals of LP-active pair and boundary constraints with monotone backtracking—consistently increases the tightest gaps instead of greedily shrinking, while guaranteeing non-decreasing objectives.
- Edge-fit Hex Seed + Weighted Active-Set LP-Relaxation: Edge-fitted hexagonal seeding inside a small open-square margin provides a balanced starting layout that the LP can immediately exploit, reducing early bottlenecks near boundaries.
- Edge-fit Hex Seed + Weighted Active-Set LP-Relaxation: In this run, the method achieved sum_radii 2.5154035642828236 with validity 1.0 and improved over ring+shrink baselines reported for earlier variants, supporting the benefit of the LP-plus-active-set synergy.
- Edge-fit Hex Seed + Weighted Active-Set LP-Relaxation: For future circle-in-square packings, reuse this pattern: initialize with edge-fitted hex seeding, solve the fixed-center LP with deterministic pivoting, and update centers only along LP-active normals with monotone acceptance to maintain reproducibility and steady objective gains.
- Edge-fit Hex Seed + Deterministic LP/Active-Set Relaxation: For fixed centers, it solves a linear program that maximizes the sum of radii under boundary and pairwise non-overlap constraints using a primal simplex with Bland’s rule, ensuring deterministic pivot selection and reproducibility.
- Edge-fit Hex Seed + Deterministic LP/Active-Set Relaxation: It updates centers only along normals of the LP’s active constraints (contacts and binding boundaries) and accepts a step only when the LP objective does not decrease, using backtracking to maintain monotone improvement.
- Edge-fit Hex Seed + Deterministic LP/Active-Set Relaxation: It seeds centers with an edge-fitted hexagonal lattice with a small open margin (eps ≈ 1e−3), providing a boundary-aware initial layout that the LP can exploit immediately.
- Edge-fit Hex Seed + Deterministic LP/Active-Set Relaxation: In this event it achieved sum_radii = 2.5152447105789904 with validity = 1.0 for 26 circles in the unit square, demonstrating effective use of global LP allocation plus active-set guided motions.
- Tightness-weighted near-active contact guidance: In the active-set center-relaxation, admit pair contacts with slack s_ij = d_ij − (r_i + r_j) ≤ tol_pair (tol_pair = max(tol, 1e-6)) and weight their unit normals by w = min(w_max, 1.0/(s_ij + sigma)) * 1.0/max(d_ij, delta) with caps (sigma = 1e-9, delta = 1e-9, w_max = 1e6), while keeping per-center unit direction normalization and monotone backtracking acceptance, to focus motion on the tightest gaps; this deterministic tweak produced a valid packing (validity 1.0) with sum_radii 2.5721113598362573, so reuse capped inverse-slack weighting of near-active contacts to guide motion in similar deterministic LP-guided relaxations.
- Edge‑fit 6-column seed + tightness-weighted walls with weight-sum normalization: On the 26-circle packing in the unit square (maximize sum of radii under non-overlap and boundary constraints), this design pairs an edge‑fitted hex seed [6,5,5,5,5] with dy = (√3/2)·dx and aligned/staggered x-offsets to reduce early boundary bottlenecks, replaces fixed wall weights with tightness-weighted inward pushes based on min_clear_i and tightness_i, and divides each node’s motion by (1 + wsum_i) before unit normalization to prevent hub dominance and scale pressure with constraint tightness; a deterministic step-size modulation also reduces s0 by 20% after three consecutive accepted improvements < 1e−8 to curb micro-oscillations. This run achieved sum_radii 2.6273226183762586 with validity 1.0 (parent 2.6222066834549853), supporting the reuse of tightness-weighted wall forces plus weight-sum normalization in this LP + active-contact pipeline under similar constraints.
- Soft-Active Boundary Push with Relative Tolerance: In build_active_motion, the boundary activeness test is relaxed from r_i ≥ min_clear − TOL to r_i ≥ min_clear − tau_bnd to admit near-active boundary sides earlier, with tau_bnd = max(5e−5, 0.02·min_clear, 10·TOL).
- Soft-Active Boundary Push with Relative Tolerance: Boundary push weights are scaled by BASE_W_BOUNDARY · tightness^1.5, where tightness = clamp01(1 − (min_clear − r_i)/max(min_clear, 1e−9)), which attenuates pushes for barely near-active cases while preserving strong pushes when tightness ≈ 1.
- Soft-Active Boundary Push with Relative Tolerance: All other mechanics remain unchanged to preserve determinism, including selecting only sides at the minimum clearance (ties allowed), capping per-node accumulation by CAP_PER_NODE, normalizing by v_i /= (1 + wsum_i), and applying final unit normalization.
- Soft-Active Boundary Push with Relative Tolerance: The run produced sum_radii = 2.6310935890983496 with validity = 1.0 and no errors, aligning with the intended effect of enabling small monotone improvements near boundaries without destabilizing acceptance.
- Deterministic Hex-LP Active Relaxation with Soft-Active Boundary Push: Combining a globally optimal fixed-center LP (maximize sum of radii) solved with Bland’s rule and a deterministic two-pass proportional pair repair with contact-guided center relaxation that only accepts monotone, non-decreasing objectives delivers strong feasibility and objective growth for 26 circles in [0,1]^2, yielding sum_radii 2.6245826963588947 with validity 1.0. Reuse the boundary-aware hexagonal seeding (EPS ≈ 1e−3; rows [6,5,5,5,5]; dx = (1 − 2·eps)/5; dy = (sqrt(3)/2)·dx) and active-set motion weighting (inverse-slack pair pushes and tightness-weighted inward wall pushes), together with backtracked step sizes and periodic micro-SLP trust-region LP updates, to coordinate moves, escape plateaus, and preserve determinism.

```python
#!/usr/bin/env python3
"""Deterministic hex-seeded packing of 26 circles in the unit square."""

import json

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """
    Construct centers and radii for a valid packing of 26 circles using:
      - Deterministic hexagonal edge-fit seeding (rows [6, 5, 5, 5, 5])
      - Deterministic LP for radii (maximize sum of radii with boundary and non-overlap)
      - Active-constraint-guided projected force relaxation with monotone backtracking

    Algorithm outline:
      1) Seed 26 centers on a hexagonal lattice that exactly fits the usable width:
         - Open-square margin eps ~ 1e-3
         - dx = (1 - 2*eps) / 5, dy = (sqrt(3)/2) * dx
         - Rows: [6, 5, 5, 5, 5], with 5-column rows staggered by dx/2
         - Vertically center rows; clip all centers into [eps, 1 - eps]^2 (defensive)
      2) For fixed centers, solve the LP:
           maximize   sum_i r_i
           subject to r_i >= 0
                      r_i <= min(x_i, y_i, 1 - x_i, 1 - y_i)
                      r_i + r_j <= ||c_i - c_j|| for all i < j
         using a deterministic primal simplex with Bland’s rule (restrict entering
         variables to r-variables for determinism/efficiency).
      3) Extract active constraints (pair distances and boundary clearances) and
         compute a movement vector by:
           - summing inverse-distance–weighted separating normals for active pairs
           - summing inward normals for all boundary sides tied for the minimum clearance
         Normalize per-center vectors outside this function.
      4) Take a small step along the movement vectors with geometric decay and
         backtracking that accepts only non-decreasing LP objectives. After each
         accepted step, clip centers into [eps, 1 - eps]^2.
      5) Alternate LP and relaxation up to a modest number of iterations or stop
         early on degeneracy/monotone failure/small improvements. Perform a final LP
         and clamp tiny negative radii to zero.

    Returns:
        centers: shape (26, 2), array of (x, y) center coordinates
        radii:   shape (26,), array of LP-optimized radii (non-negative, finite)
    """
    if num_circles != 26:
        raise ValueError("This constructor is designed specifically for 26 circles.")

    # Deterministic hexagonal edge-fit seeding for 26 circles.
    # - Row pattern: [6, 5, 5, 5, 5]
    # - dx, dy define the hex spacing; we clip into [eps, 1-eps] for robustness.
    eps = 1e-3
    row_counts = [6, 5, 5, 5, 5]
    rows = len(row_counts)

    # Edge-fit horizontal spacing:
    # 6-column row occupies positions at x = eps + c*dx, c=0..5, with dx = (1-2*eps)/5
    dx = (1.0 - 2.0 * eps) / 5.0
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
        if np.all(v == 0.0):
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
      - inverse-distance–weighted separating directions for active pairs
        (tight r_i + r_j constraints)
      - inward normals for active boundary constraints; if multiple sides tie for the
        minimum clearance within tol, sum all tied inward normals (corner-aware)

    Active constraints (deterministic extraction):
      - Pair (i,j) active if r_i + r_j >= ||c_i - c_j|| - tol
      - Boundary for i active if r_i >= min(x_i, y_i, 1-x_i, 1-y_i) - tol

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
            # Check activeness
            if radii[i] + radii[j] >= dij - tol:
                # Push i and j apart along the line connecting them, weighted by inverse distance
                if dij > 1e-15:
                    direction = diffs[i, j] / dij  # from j to i
                    w = 1.0 / max(dij, 1e-9)
                    v[i] += w * direction
                    v[j] -= w * direction
                # If dij is extremely small, skip to maintain stability/determinism

    # Active boundary constraints with multi-side tie handling
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
            # Determine which side(s) achieve the minimum; include all ties within tol
            if np.isclose(clear_left[i], min_clearance, rtol=0.0, atol=tol):
                v[i, 0] += 1.0  # inward from left -> +x
            if np.isclose(clear_bottom[i], min_clearance, rtol=0.0, atol=tol):
                v[i, 1] += 1.0  # inward from bottom -> +y
            if np.isclose(clear_right[i], min_clearance, rtol=0.0, atol=tol):
                v[i, 0] -= 1.0  # inward from right -> -x
            if np.isclose(clear_top[i], min_clearance, rtol=0.0, atol=tol):
                v[i, 1] -= 1.0  # inward from top -> -y

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
        * Entering variable: smallest index j among x-variables with reduced cost r_j > tol.
          (We restrict entering candidates to r-variables for determinism/efficiency.)
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
        # Bland's rule: pick smallest-index entering variable among x-columns with r_j > tol_enter
        enter_candidates = [j for j in range(n) if reduced_costs[j] > tol_enter]
        if not enter_candidates:
            # Optimal: no positive reduced cost among decision variables
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
        # Tie-breaking: smallest row index among those achieving min ratio (deterministic)
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


def compute_max_radii(centers: np.ndarray) -> np.ndarray:
    """
    Deterministic LP-based radii computation.

    For fixed centers, solve the LP:
        maximize   sum_i r_i
        subject to r_i >= 0
                   r_i <= min(x_i, y_i, 1 - x_i, 1 - y_i)
                   r_i + r_j <= ||c_i - c_j||    for all i < j

    The LP is solved with a primal simplex using Bland’s rule. Tiny negative
    radii from numerical noise are clamped to zero.
    """
    radii, info = _solve_lp_radii(centers)
    # Clamp to non-negative in case of tiny numerical negatives
    radii = np.clip(radii, 0.0, np.finfo(float).max)
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

```python
#!/usr/bin/env python3
"""Initial candidate for packing 26 circles in the unit square."""

import json

import numpy as np


# EVOLVE_START
# Deterministic LP + active-set motion solver for packing 26 circles in [0,1]^2.
# Overview:
# 1) Deterministic hexagonal-lattice seeding inside a small open-square margin.
# 2) For fixed centers, solve the exact LP that maximizes the sum of radii
#    subject to boundary and pairwise non-overlap constraints using a primal
#    simplex with Bland's rule (deterministic).
# 3) Extract LP-active constraints (tight pairs and sides), build a separating
#    motion direction per center, take a small projected step with monotone
#    backtracking, re-solve LP, and accept non-decreasing objective only.
# 4) Iterate until movement stalls or improvements diminish. Return final centers
#    and LP-optimal radii. All steps deterministic with lexicographic tie breaks.


def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    The design is tailored to n=26, using a 5-row edge-fitted hexagonal lattice
    with row counts [6, 5, 5, 5, 5]. For other n, it falls back to a square grid
    seed and still performs a few LP-relaxation steps deterministically.
    """
    # Deterministic seed parameters
    eps = 1e-3  # open-square margin for center clipping
    if num_circles == 26:
        centers = seed_hex_26(eps)
    else:
        centers = seed_generic(num_circles, eps)

    # Build constant LP matrix A structure once (does not depend on centers),
    # and reusable pair-index list for fast RHS updates.
    A_template, pair_index_list = build_A_structure(num_circles)

    # Solve LP for initial radii
    radii, obj = solve_lp_max_sum_radii(centers, A_template, pair_index_list)

    # Parameters for deterministic active-set relaxation
    dx, dy = seed_lattice_spacings(eps)
    s0 = 0.1 * min(dx, dy)
    rho = 0.95
    tol_active = 1e-8
    accept_tol = 1e-12
    improve_tol = 1e-7
    max_outer = 50
    max_backtrack = 10
    backtrack_shrink = 0.5

    prev_obj = obj
    stagnant_iters = 0

    for it in range(max_outer):
        # Determine active constraints at current LP optimum
        active_pairs, active_bound_sides = extract_active_constraints(
            centers, radii, tol_active
        )

        # Build deterministic movement vectors from actives
        v = build_motion_vectors(centers, active_pairs, active_bound_sides)

        # Stop if no movement
        norms = np.linalg.norm(v, axis=1)
        if not np.any(norms > 0):
            break

        # Normalize motion vectors to unit (per circle)
        nz = norms > 0
        v[nz] /= norms[nz][:, None]

        # Try a monotone step with backtracking; decrease baseline step by rho^it
        step = s0 * (rho ** it)
        accepted = False
        best_centers = centers
        best_radii = radii
        best_obj = prev_obj

        for _ in range(max_backtrack):
            cand = np.clip(centers + step * v, eps, 1.0 - eps)
            # Re-solve LP on candidate centers
            cand_r, cand_obj = solve_lp_max_sum_radii(cand, A_template, pair_index_list)
            if cand_obj + accept_tol >= prev_obj:
                # Accept non-decreasing objective
                accepted = True
                best_centers, best_radii, best_obj = cand, cand_r, cand_obj
                break
            step *= backtrack_shrink

        if not accepted:
            # Could not find a non-decreasing move; stop
            break

        # Update state
        centers, radii, obj = best_centers, best_radii, best_obj

        # Check improvement
        if obj - prev_obj < improve_tol:
            stagnant_iters += 1
            if stagnant_iters >= 3:
                break
        else:
            stagnant_iters = 0
        prev_obj = obj

    # Final LP solve (safety) and hygiene: clamp tiny negative radii to zero
    radii, _ = solve_lp_max_sum_radii(centers, A_template, pair_index_list)
    radii[radii < 0] = 0.0
    # Clip centers to the open square margin for robustness
    centers = np.clip(centers, eps, 1.0 - eps)

    return centers, radii


def seed_hex_26(eps: float) -> np.ndarray:
    """Deterministic edge-fitted hexagonal seed for 26 centers with rows [6,5,5,5,5]."""
    counts = [6, 5, 5, 5, 5]  # total = 26
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx
    # Vertically center 5 rows: total height = 4*dy gaps
    y0 = 0.5 - 0.5 * (4.0 * dy)

    centers = []
    for r, cnt in enumerate(counts):
        y = y0 + r * dy
        if cnt == 6:
            xs = [eps + c * dx for c in range(6)]
        else:  # cnt == 5
            xs = [eps + dx / 2.0 + c * dx for c in range(5)]
        for x in xs:
            centers.append([x, y])

    centers = np.array(centers, dtype=float)
    centers = np.clip(centers, eps, 1.0 - eps)
    return centers


def seed_lattice_spacings(eps: float) -> tuple[float, float]:
    """Return dx, dy used by the hex lattice spacing, used to set step scale."""
    dx = (1.0 - 2.0 * eps) / 5.0
    dy = (np.sqrt(3.0) / 2.0) * dx
    return dx, dy


def seed_generic(n: int, eps: float) -> np.ndarray:
    """Fallback deterministic grid seed for arbitrary n."""
    # Small square grid, fill by rows
    m = int(np.ceil(np.sqrt(n)))
    xs = np.linspace(eps, 1.0 - eps, m)
    ys = np.linspace(eps, 1.0 - eps, m)
    pts = []
    for j in range(m):
        for i in range(m):
            pts.append([xs[i], ys[j]])
            if len(pts) == n:
                return np.asarray(pts, dtype=float)
    return np.asarray(pts[:n], dtype=float)


def build_A_structure(n: int):
    """Build constant A matrix structure and pair index list for the LP.

    Constraints layout (m rows):
      - First n rows: boundary caps r_i <= min{x_i, y_i, 1-x_i, 1-y_i}
                      Implemented as unit rows e_i.
      - Next rows: pairwise disjointness r_i + r_j <= ||c_i - c_j|| for all i < j
                   Implemented as rows with 1 at columns i and j.

    Returns:
      A_template: (m, n) array with constant 0/1 sparsity pattern
      pair_list: list of (i, j) pairs in lexicographic order matching A rows
    """
    pair_list = []
    for i in range(n):
        for j in range(i + 1, n):
            pair_list.append((i, j))
    m = n + len(pair_list)
    A = np.zeros((m, n), dtype=float)

    # Boundary rows: identity
    for i in range(n):
        A[i, i] = 1.0

    # Pairwise rows
    for k, (i, j) in enumerate(pair_list):
        row = n + k
        A[row, i] = 1.0
        A[row, j] = 1.0

    return A, pair_list


def rhs_vector_for_centers(centers: np.ndarray, pair_list: list[tuple[int, int]]) -> np.ndarray:
    """Compute RHS b for the LP constraints given centers.

    b layout:
      - First n entries: min boundary clearances per point.
      - Remaining entries: pairwise distances ||c_i - c_j|| for pairs in pair_list.
    """
    n = centers.shape[0]
    x = centers[:, 0]
    y = centers[:, 1]
    boundary_caps = np.minimum.reduce([x, y, 1.0 - x, 1.0 - y])

    # Pairwise distances in lexicographic pair_list order
    dists = np.empty(len(pair_list), dtype=float)
    for k, (i, j) in enumerate(pair_list):
        dists[k] = float(np.linalg.norm(centers[i] - centers[j]))

    b = np.concatenate([boundary_caps, dists], axis=0)
    # Ensure non-negativity (numerical safety)
    b[b < 0.0] = 0.0
    return b


def solve_lp_max_sum_radii(
    centers: np.ndarray,
    A_template: np.ndarray,
    pair_index_list: list[tuple[int, int]],
) -> tuple[np.ndarray, float]:
    """Solve LP: maximize sum(r_i) s.t. A r <= b, r >= 0. Deterministic primal simplex.

    A r + s = b, s >= 0. Initialize with basic slacks s = b, r = 0.
    Pivot using Bland's rule, allowing entering variables only among r-columns.

    Returns:
      r: optimal radii vector (n,)
      obj: optimal objective value (sum of radii)
    """
    n = centers.shape[0]
    # Build RHS for current centers
    b = rhs_vector_for_centers(centers, pair_index_list)

    # Construct initial simplex tableau:
    # Rows: m constraints + 1 objective
    # Cols: n original vars + m slack vars + 1 RHS
    m = A_template.shape[0]
    T = np.zeros((m + 1, n + m + 1), dtype=float)

    # Objective row: z - sum(r_i) = 0 -> coefficients are -1 on r columns
    T[0, 0:n] = -1.0
    # The slack coefficients in objective are 0; RHS at 0 (since z starts at 0)

    # Constraint rows: [A | I | b]
    T[1 : m + 1, 0:n] = A_template
    for i in range(m):
        T[1 + i, n + i] = 1.0
    T[1 : m + 1, -1] = b

    # Track basis: initial basis are slacks at indices [n .. n+m-1]
    basis = np.arange(n, n + m, dtype=int)

    # Simplex parameters
    tol_enter = 1e-12  # positivity threshold for entering (row0 < -tol)
    tol_pivot = 1e-12  # positivity threshold for pivot elements
    max_pivots = 200000  # safety cap for termination

    pivots = 0
    while True:
        # Choose entering variable using Bland's rule among r-columns (0..n-1)
        entering = None
        for j in range(n):
            if T[0, j] < -tol_enter:
                entering = j
                break
        if entering is None:
            break  # optimal (no improving entering variables)

        # Ratio test for leaving variable (min RHS / coeff), with Bland tie-break
        min_ratio = None
        leaving_row = None
        col = entering
        for i in range(1, m + 1):
            a = T[i, col]
            if a > tol_pivot:
                ratio = T[i, -1] / a
                if (min_ratio is None) or (ratio < min_ratio - 0) or (
                    abs(ratio - min_ratio) <= 0 and i < leaving_row
                ):
                    min_ratio = ratio
                    leaving_row = i

        if leaving_row is None:
            # Unbounded in this direction; should not happen for our packing LP.
            # To be safe, break and return current basic feasible solution.
            break

        # Pivot on (leaving_row, entering)
        pivot_val = T[leaving_row, entering]
        if abs(pivot_val) < tol_pivot:
            # Degenerate pivot (should not occur with above check); abort pivoting
            break

        # Normalize leaving row
        T[leaving_row, :] = T[leaving_row, :] / pivot_val

        # Eliminate entering column from all other rows
        for i in range(m + 1):
            if i == leaving_row:
                continue
            factor = T[i, entering]
            if factor != 0.0:
                T[i, :] -= factor * T[leaving_row, :]

        # Update basis
        basis[leaving_row - 1] = entering

        pivots += 1
        if pivots > max_pivots:
            break

    # Extract radii r from the final tableau and basis
    r = np.zeros(n, dtype=float)
    for i in range(m):
        var_idx = basis[i]
        if var_idx < n:
            # basic original variable => take RHS of its row
            r[var_idx] = T[1 + i, -1]

    # Objective value is at T[0, -1]
    obj = float(T[0, -1])
    # Numerical hygiene
    r[r < 0] = 0.0
    r[~np.isfinite(r)] = 0.0
    if not np.isfinite(obj):
        obj = float(np.sum(r))
    return r, obj


def extract_active_constraints(
    centers: np.ndarray,
    radii: np.ndarray,
    tol_active: float = 1e-8,
) -> tuple[list[tuple[int, int]], dict[int, list[str]]]:
    """Identify LP-active pair constraints and boundary sides.

    Returns:
      active_pairs: list of (i,j) with r_i + r_j >= d_ij - tol
      active_bound_sides: dict i -> list of sides among {"left","right","bottom","top"}
                          for which r_i >= min_clearance(x_i,y_i) - tol and that side
                          attains the min clearance within tol.
    """
    n = centers.shape[0]
    # Boundary min and sides
    x = centers[:, 0]
    y = centers[:, 1]
    left = x
    bottom = y
    right = 1.0 - x
    top = 1.0 - y
    mins = np.minimum.reduce([left, bottom, right, top])

    # Active boundary sides per circle
    active_bound_sides: dict[int, list[str]] = {}
    for i in range(n):
        if radii[i] >= mins[i] - tol_active:
            sides = []
            # Include all sides within tol of the min (corner-aware)
            if abs(left[i] - mins[i]) <= tol_active:
                sides.append("left")
            if abs(bottom[i] - mins[i]) <= tol_active:
                sides.append("bottom")
            if abs(right[i] - mins[i]) <= tol_active:
                sides.append("right")
            if abs(top[i] - mins[i]) <= tol_active:
                sides.append("top")
            active_bound_sides[i] = sides

    # Active pairs
    active_pairs: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            d = float(np.linalg.norm(centers[i] - centers[j]))
            if radii[i] + radii[j] >= d - tol_active:
                active_pairs.append((i, j))

    return active_pairs, active_bound_sides


def build_motion_vectors(
    centers: np.ndarray,
    active_pairs: list[tuple[int, int]],
    active_bound_sides: dict[int, list[str]],
) -> np.ndarray:
    """Construct deterministic motion vectors from active constraints.

    - For each active pair (i,j), add to v[i] the unit vector from j->i weighted by
      inverse distance, and add the opposite to v[j].
    - For each i with active boundary sides, add corresponding inward normals.
    """
    n = centers.shape[0]
    v = np.zeros((n, 2), dtype=float)
    eps_d = 1e-12

    # Pair contributions
    for (i, j) in active_pairs:
        diff = centers[i] - centers[j]
        d = float(np.linalg.norm(diff))
        if d <= eps_d:
            # Degenerate co-location: use fixed axis to break tie deterministically
            u = np.array([1.0, 0.0])
            w = 1.0
        else:
            u = diff / d
            w = 1.0 / max(d, eps_d)
        v[i] += w * u
        v[j] -= w * u

    # Boundary inward normals
    for i, sides in active_bound_sides.items():
        for s in sides:
            if s == "left":
                v[i] += np.array([+1.0, 0.0])
            elif s == "right":
                v[i] += np.array([-1.0, 0.0])
            elif s == "bottom":
                v[i] += np.array([0.0, +1.0])
            elif s == "top":
                v[i] += np.array([0.0, -1.0])

    return v


def compute_max_radii(centers: np.ndarray) -> np.ndarray:
    """Compute exact LP-optimal radii for fixed centers (wrapper for compatibility)."""
    A_template, pair_index_list = build_A_structure(centers.shape[0])
    r, _ = solve_lp_max_sum_radii(centers, A_template, pair_index_list)
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
        # Build normalized movement directions from active constraints
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
    """Construct movement directions along the normals of active constraints.

    Active pair constraints: r_i + r_j >= ||c_i - c_j|| - tol_active
    Active boundary constraints: r_i >= min_clearance - tol_active

    The direction accumulates inverse-distance–weighted separating normals for pairs,
    and inward normals for active boundaries. Each center's vector is normalized.

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

    # Pairwise active constraints
    for i in range(n):
        ci = centers[i]
        for j in range(i + 1, n):
            cj = centers[j]
            diff = ci - cj
            dist = float(np.hypot(diff[0], diff[1]))
            # Active if sum radii reaches distance within tolerance
            if radii[i] + radii[j] >= dist - tol_active:
                if dist > 1e-15:
                    n_ij = diff / dist
                    w = 1.0 / max(dist, 1e-9)
                    v[i] += w * n_ij
                    v[j] -= w * n_ij
                # If dist ~ 0, skip adding; clipping and boundary act will help spread.

    # Boundary active constraints per center
    for i in range(n):
        x, y = centers[i]
        # clearances to left, bottom, right, top
        cl = x
        cb = y
        cr = 1.0 - x
        ct = 1.0 - y
        minc = min(cl, cb, cr, ct)
        if radii[i] >= minc - tol_active:
            # add inward normals for all sides tied at minimum
            if abs(cl - minc) <= tol_active:
                v[i] += np.array([1.0, 0.0])
            if abs(cb - minc) <= tol_active:
                v[i] += np.array([0.0, 1.0])
            if abs(cr - minc) <= tol_active:
                v[i] += np.array([-1.0, 0.0])
            if abs(ct - minc) <= tol_active:
                v[i] += np.array([0.0, -1.0])

    # Normalize per-center
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
      - Modify build_active_motion pair handling to include a near-active window and inverse-slack weighting.
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
    """Compute motion vectors along normals of the active/near-active constraints.

    Returns:
      v: (N,2) motion vector per center
      deg: (N,) number of active contributions per center (for normalization)

    Boundary handling:
      - Boundary normals are upweighted with base factor and capped per-circle to steer inward.

    Pair handling (modified per algorithm description):
      - Introduce a near-active window with slack s_ij = d_ij - (r_i + r_j) <= tol_pair,
        where tol_pair = max(tol, 1e-6) deterministically.
      - For included pairs, weight the unit-normal contribution by
          w = min(w_max, 1.0 / (s_ij + sigma)) * (1.0 / max(d_ij, delta)),
        with sigma=1e-9, delta=1e-9, w_max=1e6.
      - Add +/- w * n_ij to centers i/j, respectively.
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

    # Pairwise near-active contacts:
    # Include pairs with slack s_ij = d_ij - (r_i + r_j) <= tol_pair,
    # weight by inverse slack and 1/d with caps to maintain stability.
    sigma = 1e-9
    delta = 1e-9
    w_max = 1e6
    tol_pair = max(tol, 1e-6)

    for i in range(n):
        for j in range(i + 1, n):
            ci = centers[i]
            cj = centers[j]
            diff = ci - cj
            d = math.hypot(diff[0], diff[1])
            if d <= 0.0:
                continue
            s_ij = d - (float(radii[i]) + float(radii[j]))
            if s_ij <= tol_pair:
                # Unit normal
                n_ij = diff / d
                # Tightness-based weight, capped
                w_tight = 1.0 / (s_ij + sigma)
                w_dist = 1.0 / max(d, delta)
                w = min(w_max, w_tight) * w_dist
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

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

Implements:
- Deterministic edge-fitted hexagonal seeding for 26 centers.
- Exact LP for maximum-sum radii at fixed centers via primal simplex (Bland's rule).
- A deterministic two-pass proportional pair repair immediately after each LP to remove
  residual pair overlaps from numerical effects (lexicographic order).
- Iterative active/near-active contact-guided center relaxation with monotone backtracking.
- Frequent micro-SLP trust-region linearized step (deterministic, guarded acceptance) used
  periodically and as a plateau escape if contact motion cannot progress.
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
# Mutation: slightly stronger inward boundary bias
BASE_W_BOUNDARY = 2.3  # base weight for boundary inward normal contributions
CAP_PER_NODE = 3.0     # cap on accumulated magnitude per node before normalization

# Backtracking parameters
RHO = 0.95
ALPHA_MIN_SCALE = 1e-6

# Acceptance limits
# Mutation: longer acceptance budget
MAX_ACCEPTS = 160
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

    Edge-fitted hex seed layout:
      - rows = [6, 5, 5, 5, 5]
      - dx = (1 - 2*eps)/5
      - dy = (sqrt(3)/2)*dx
      - row y positions: yk = (0.5 - 2*dy) + k*dy for k=0..4
      - 6-column row aligned with x columns at xs = eps + c*dx (no 0.5*dx offset)
      - 5-column rows staggered by +0.5*dx offset

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
                if (min_ratio is None or ratio < min_ratio - tol or
                        (abs(ratio - (min_ratio or 0.0)) <= tol and basis[i] < basis[leave_row])):  # type: ignore
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


def two_pass_pair_repair(centers: np.ndarray, radii: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    """Deterministic two-pass proportional pair repair to remove residual overlaps.

    For pairs (i<j) in lexicographic order, if r_i + r_j > d_ij + eps, scale both:
        factor = d_ij / (r_i + r_j)
        r_i <- factor * r_i
        r_j <- factor * r_j
    Repeat exactly two passes to mitigate order effects. This preserves feasibility
    w.r.t. pair constraints and never increases any radius.

    Args:
        centers: (N,2)
        radii: (N,)
        eps: small tolerance
    Returns:
        repaired radii array (N,)
    """
    N = centers.shape[0]
    r = np.array(radii, dtype=float, copy=True)
    for _ in range(2):
        for i in range(N):
            ci = centers[i]
            ri = r[i]
            for j in range(i + 1, N):
                cj = centers[j]
                d = float(np.linalg.norm(ci - cj))
                s = ri + r[j] - d
                if s > eps:
                    # scale both radii proportionally
                    denom = ri + r[j]
                    if denom > 0.0:
                        factor = d / denom
                        ri *= factor
                        r[j] *= factor
            r[i] = ri
    # Ensure non-negative and finite
    r = np.where(np.isfinite(r) & (r >= 0.0), r, 0.0)
    return r


def compute_max_radii_lp(centers: np.ndarray) -> Tuple[np.ndarray, float, List[int]]:
    """Solve the LP for maximum sum of radii with fixed centers, then two-pass repair.

    Constraints:
        r_i >= 0 (implicit via LP nonnegativity)
        r_i <= x_i, r_i <= y_i, r_i <= 1-x_i, r_i <= 1-y_i
        r_i + r_j <= ||c_i - c_j|| for all i<j

    After solving, apply a deterministic two-pass proportional pair repair in lexicographic
    order to ensure strict feasibility w.r.t. pair constraints and reduce numerical order bias.

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
    # Post-LP two-pass pair repair
    radii = two_pass_pair_repair(centers, res.x, eps=1e-12)
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

    Boundary contributions:
      - Soft near-active boundary detection per node with tolerance
        tau_bnd = max(5e-5, 0.02*min_clear, 10*TOL).
      - For sides achieving the minimum clearance (ties allowed), add inward normals.
      - Tightness = clamp01(1 - (min_clear - r_i) / max(min_clear, 1e-9)).
      - Boundary inward push magnitude scaled by BASE_W_BOUNDARY * tightness^1.5 (convex taper)
        to attenuate merely near-active cases and preserve strong pushes when truly tight.

    Pair contributions:
      - Near-active pairs s_ij <= max(TOL, PAIR_TOL_MIN) push along their separating direction,
        weighted by inverse slack (with stabilizers) and inverse distance.

    Accumulation:
      - Per-node cap on vector magnitude before normalization (CAP_PER_NODE).
      - Accumulate scalar weights per node (boundary + pair); divide accumulated vector by (1+wsum_i)
        before final unit normalization to avoid hub dominance.

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
        # Near-active boundary tolerance per node as specified:
        # tau_bnd = max(5e-5, 0.02 * min_clear, 10*TOL)
        tau_bnd = max(5e-5, 0.02 * min_clear, 10.0 * TOL)
        # Admit boundary contributions when within tau_bnd of activeness
        if radii[i] >= min_clear - tau_bnd:
            # tightness in [0,1]: higher when r is closer to min_clear
            denom = max(min_clear, 1e-9)
            tightness = _clamp01(1.0 - (min_clear - float(radii[i])) / denom)
            # Convex taper of boundary push magnitude (exponent 1.5)
            w_bound = BASE_W_BOUNDARY * (tightness ** 1.5)
            if w_bound > 0.0:
                # For each side achieving the minimum, add inward normal
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
    Trust region: dx+ <= h; dx- <= h; dy+ <= h; d y- <= h => |Δ| <= h.

    Adaptive policy (deterministic):
      - Initialize h = MICRO_SLP_H_INIT_SCALE * min(dx,dy), with floor MICRO_SLP_MIN_H_SCALE.
      - Try to accept a non-decreasing step; if rejected, shrink h by MICRO_SLP_BACKTRACK.
      - If accepted with meaningful improvement, try modestly increasing h (up to ~0.12*base) and
        attempt again for potentially larger gain. Otherwise, return after first acceptance.

    Returns:
        centers_new, sum_radii_new if an accepted improvement (non-decreasing); else return original.
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

    # Trust region loop with adaptive growth/shrink (deterministic)
    base_scale = min(base_dx, base_dy)
    h = MICRO_SLP_H_INIT_SCALE * base_scale
    h_min = MICRO_SLP_MIN_H_SCALE * base_scale
    h_max = 0.12 * base_scale  # gentle cap
    improv_thresh = 1e-9

    # Current objective
    _, obj_curr, _ = compute_max_radii_lp(centers)
    best_centers = centers
    best_obj = obj_curr

    while h >= h_min:
        delta_c, _ = solve_micro_lp(h)
        trial_centers = centers + delta_c
        np.clip(trial_centers, HEX_EPS, 1.0 - HEX_EPS, out=trial_centers)
        _radii_trial, obj_trial, _ = compute_max_radii_lp(trial_centers)
        if obj_trial >= obj_curr - OBJ_TOL:
            # Accept this trial; if strong enough improvement, try to gently grow h once more
            if obj_trial - obj_curr > improv_thresh and h < h_max:
                centers = trial_centers
                obj_curr = obj_trial
                h = min(h * 1.2, h_max)
                # loop to try larger step from updated centers
                best_centers, best_obj = centers, obj_curr
                continue
            else:
                return trial_centers, obj_trial
        # else backtrack and try smaller h
        h *= MICRO_SLP_BACKTRACK

    # If not accepted, return unchanged
    return best_centers, best_obj


def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    Pipeline:
      1) Deterministic hexagonal seeding (26 centers).
      2) Fixed-center radii via deterministic LP (primal simplex with Bland's rule) followed
         by deterministic two-pass proportional pair repair.
      3) Active/near-active contact-guided relaxation with monotone backtracking.
      4) Frequent micro-SLP trust-region step for coordinated moves (periodically and as plateau escape).

    Mutations integrated:
      - Edge-fitted hex seed layout: [6, 5, 5, 5, 5] with dy=(sqrt(3)/2)*dx; 6-row aligned, 5-rows staggered.
      - Tightness-weighted boundary pushes and weight-sum normalization in active motion, stronger BASE_W_BOUNDARY=2.3.
      - Deterministic one-time step-size modulation: after three consecutive small-contact accepts, s0 <- 0.8*s0.
      - Increased micro-SLP cadence (K=2), enlarged initial trust region (h_scale=0.08), gentler backtracking (0.75),
        and extended small-improvement streak limit (5).
      - Deterministic two-pass pair repair after each LP solve to reduce residual pair violations.
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

    # Initial LP radii (with post-LP repair)
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

        # If no direction from contacts, try micro-SLP as a plateau escape
        if not np.any(np.linalg.norm(v, axis=1) > 0.0):
            trial_centers, obj_trial = micro_slp_step(centers, radii, dx, dy)
            if obj_trial >= obj - OBJ_TOL and (obj_trial > obj or np.any(trial_centers != centers)):
                # Accept micro-SLP plateau escape
                centers = trial_centers
                radii, obj, _ = compute_max_radii_lp(centers)
                accepts += 1
                since_micro = 0
                if obj - last_improv_obj < SMALL_IMPROV_THRESH:
                    small_improv_streak += 1
                else:
                    small_improv_streak = 0
                last_improv_obj = obj
                continue
            # Neither contact nor micro-SLP can progress
            break

        # Backtracking line search for contact-guided motion
        alpha = s0
        accepted_contact = False
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
                accepted_contact = True
                break
            alpha *= RHO

        if not accepted_contact:
            # Could not find non-decreasing contact step: try micro-SLP as plateau escape
            trial_centers, obj_trial = micro_slp_step(centers, radii, dx, dy)
            if obj_trial >= obj - OBJ_TOL and (obj_trial > obj or np.any(trial_centers != centers)):
                # Accept micro-SLP plateau escape
                centers = trial_centers
                radii, obj, _ = compute_max_radii_lp(centers)
                accepts += 1
                since_micro = 0
                if obj - last_improv_obj < SMALL_IMPROV_THRESH:
                    small_improv_streak += 1
                else:
                    small_improv_streak = 0
                last_improv_obj = obj
            else:
                # Neither contact nor micro-SLP can improve
                break

        # Periodically attempt micro-SLP
        if since_micro >= MICRO_SLP_K:
            trial_centers, obj_trial = micro_slp_step(centers, radii, dx, dy)
            if obj_trial >= obj - OBJ_TOL and (obj_trial > obj or np.any(trial_centers != centers)):
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
SMALL_IMPROVEMENT_STREAK_LIMIT = 5


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
                current_centers = trial_centers
                current_radii = trial_radii
                # Determine improvement size
                improvement = trial_obj - current_obj
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
