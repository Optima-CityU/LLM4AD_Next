Actionable insight from a run that combined hexagonal seeding, LP-style radii projection, and deterministic center relaxation, supported by observed metrics and the attached implementation.

- LP-style pairwise projection for radii: For fixed centers in circle packing, iteratively enforcing r_i + r_j <= d_ij by reducing only the larger of (r_i, r_j) by the exact excess and clamping within boundary limits preserves the sum objective better than symmetric shrinkage and, in this run, yielded a valid solution (validity 1.0) with total sum_radii 2.540084072039342.
- Hexagonal seeding of centers: Initializing 26 centers in a staggered hexagonal pattern with five rows of counts [5, 6, 5, 5, 5], offsetting alternate rows by half spacing and using a small deterministic interior margin, provides near-uniform coverage that avoids the crowding and wasted space near corners and edges caused by concentric-ring seeding.
- Deterministic barrier relaxation of centers: Moving centers along summed ascent directions from active boundaries and active neighbor pairs with a decaying step size, projecting back into [m, 1 − m]^2 each iteration, and recomputing LP radii after every move redistributes centers into slack regions and increases the achievable sum of radii while keeping the pipeline fully deterministic and reproducible.
- Per-Index Commit Fix for LP-Style Radii Projection: Moving the assignment r[i] = max(0, min(ri, b[i])) inside the outer loop of compute_lp_radii so each i’s trimmed radius is committed after processing all j > i turned each sweep into a true projection for fixed centers while preserving clamping and early-stop tolerance; in this run it produced a valid packing with sum_radii = 2.550842809220581 (parent = 2.540084072039342), showing that in-place per-index commits reduce residual violations and provide more accurate active-set signals for center relaxation.
- Deterministic LP-style per-index radii projection: With fixed centers, enforce r_i ≤ b_i and r_i + r_j ≤ d_ij by sweeping pairs in lexicographic (i, j) order, reducing only the larger radius by the exact excess and committing r_i after finishing all j, which preserves total radii better than symmetric shrink and is reproducible.
- Edge-fitted hexagonal seeding (26 centers): Seed 26 centers as a near-hex lattice with rows [6, 5, 5, 5, 5], spacing dx = (1 − 2·eps)/(6 − 1) and dy = (√3/2)·dx with eps ≈ 1e-3 and row offsets for 5-count rows, to utilize area uniformly and fit edges safely.
- Active-constraint–guided center relaxation with monotone backtracking: Build an active set using τ ≈ 1e-10, move centers along summed contact normals, recompute radii after a trial step s0 = 0.1·min(dx, dy), and accept only if total radii increases by ≥ 1e-12, else reduce the step by ρ ≈ 0.95 until a small threshold, guaranteeing a non-decreasing objective.
- Edge-fit Hex Seed + Per-Index LP-Projection + Active-Set Monotone Relaxation: On the 26-circle unit-square task, the method produced a valid packing with sum_radii = 2.4994736958426764 (validity = 1.0), aligning with prior observations that the per-index projection variant achieves ≈ 2.55 and exact LP > 2.51 for 26 circles.

```python
#!/usr/bin/env python3
"""Deterministic constructor for packing 26 circles in the unit square.

This implementation follows a three-stage deterministic pipeline:

1) Hexagonal seeding (staggered rows) for near-uniform initial centers.
2) LP-like projection for radii: maximize sum r subject to boundary and
   pairwise non-overlap constraints by iterative pairwise projections
   that trim only the larger radius.
3) Deterministic center relaxation: move centers away from active constraints
   (binding walls and neighboring circles) with a decaying step, re-solving
   the LP radii each iteration to guide ascent. Centers are projected back
   into an interior margin to maintain feasibility.

The result is a reproducible arrangement with disjoint circles entirely inside
the unit square and a high sum of radii.
"""

import json

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    This function constructs a deterministic packing using:
    - Hexagonal-like staggered seeding of centers.
    - LP-style projection to compute optimal radii for fixed centers.
    - Deterministic relaxation of centers guided by active constraints.

    Parameters
    ----------
    num_circles : int
        Number of circles to place. The algorithm is tuned for 26; for other
        counts, a best-effort subset/superset of the seeded points is used.

    Returns
    -------
    centers : ndarray, shape (num_circles, 2)
        Center coordinates within [0, 1]^2.
    radii : ndarray, shape (num_circles,)
        Non-negative radii satisfying boundary and non-overlap constraints.
    """
    # Interior margin to keep centers away from the boundary during relaxation.
    margin = 0.03

    # Seed centers in a 5-row staggered pattern, counts [5, 6, 5, 5, 5] -> 26 total.
    base_centers = _hex_seed_centers(margin=margin)

    # Adjust to requested number by slicing or tiling deterministically.
    centers = _adjust_count(base_centers, num_circles)

    # Deterministic relaxation of centers with decaying step size.
    centers = _relax_centers(centers, margin=margin, iterations=120, alpha0=0.012, decay=0.985)

    # Final radii via the LP-style projection with extra sweeps for cleanliness.
    radii = compute_lp_radii(centers, sweeps=20, tol=1e-12)

    return centers, radii


def _hex_seed_centers(margin: float = 0.03) -> np.ndarray:
    """Create a staggered (hex-like) seeding of 26 centers inside [0,1]^2.

    Pattern: 5 rows with counts [5, 6, 5, 5, 5], where the 6-count row is centered
    and 5-count rows are offset by half the base column spacing relative to the 6-count row.

    We deliberately keep all x positions strictly inside [margin, 1 - margin] by leaving
    a little room near the borders (we do not span the full width with the extreme centers).
    """
    counts = [5, 6, 5, 5, 5]  # total = 26
    n_rows = len(counts)
    width = 1.0 - 2.0 * margin

    # Base x spacing derived from 6 columns (widest row).
    max_cols = max(counts)
    s = width / float(max_cols)  # base column spacing

    # y positions evenly spaced across the interior margin band.
    # Use midpoints of 5 equal vertical bands: y_j = margin + (j + 0.5) * vy
    vy = (1.0 - 2.0 * margin) / float(n_rows)
    y_positions = margin + (np.arange(n_rows) + 0.5) * vy

    centers = []
    # We put the 6-count row on the second row (index 1) to mimic a hex stack.
    for row_idx, k in enumerate(counts):
        y = y_positions[row_idx]
        if k == max_cols:
            # 6-count row: positions centered at m + s/2 + n*s, n=0..5
            xs = margin + (0.5 + np.arange(k)) * s
        else:
            # 5-count rows: offset by +s relative to 6-count row grid; n=0..4
            xs = margin + (1.0 + np.arange(k)) * s
        for x in xs:
            centers.append([float(x), float(y)])

    centers = np.array(centers, dtype=float)
    # Safety clamp to keep centers within the margin box.
    np.clip(centers, margin, 1.0 - margin, out=centers)
    return centers


def _adjust_count(centers: np.ndarray, num_circles: int) -> np.ndarray:
    """Adjust a base list of centers to exactly num_circles deterministically.

    - If more are needed, we tile from the beginning and add a tiny deterministic
      jitter-free nudging within the margin to avoid duplicates exactly on top.
    - If fewer are needed, truncate.
    """
    base_n = centers.shape[0]
    if num_circles == base_n:
        return centers.copy()
    elif num_circles < base_n:
        return centers[:num_circles].copy()
    else:
        # Tile and then add a tiny deterministic offset pattern within a very small bound.
        reps = int(np.ceil(num_circles / base_n))
        tiled = np.vstack([centers for _ in range(reps)])[:num_circles].copy()
        # Deterministic micro-offsets (still within numeric safety margin).
        # This keeps determinism while preventing exact coincidence if ever used.
        eps = 1e-6
        for i in range(base_n, num_circles):
            off = ((i - base_n) % 4)
            if off == 0:
                tiled[i, 0] += eps
            elif off == 1:
                tiled[i, 0] -= eps
            elif off == 2:
                tiled[i, 1] += eps
            else:
                tiled[i, 1] -= eps
        np.clip(tiled, 0.0, 1.0, out=tiled)
        return tiled


def compute_lp_radii(centers: np.ndarray, sweeps: int = 12, tol: float = 1e-12) -> np.ndarray:
    """Compute radii via iterative LP-style projection for fixed centers.

    Objective: maximize sum r_i
    Subject to: 0 <= r_i <= b_i (boundary), r_i + r_j <= d_ij for all i < j

    Algorithm: initialize r = b. For K sweeps over all pairs, if r_i + r_j > d_ij,
    reduce only the larger of r_i, r_j by exactly the excess (minimal change to
    satisfy that pairwise constraint). Clamp r back to [0, b] after each sweep.

    Parameters
    ----------
    centers : ndarray, shape (n, 2)
        Circle centers inside the unit square.
    sweeps : int
        Number of pairwise projection sweeps.
    tol : float
        Early stop tolerance on maximal violation per sweep.

    Returns
    -------
    radii : ndarray, shape (n,)
        Radii satisfying all constraints (within numerical tolerance).
    """
    n = centers.shape[0]
    # Boundary limits: min distance to the square boundaries.
    left = centers[:, 0]
    bottom = centers[:, 1]
    right = 1.0 - centers[:, 0]
    top = 1.0 - centers[:, 1]
    b = np.minimum.reduce([left, bottom, right, top])
    b = np.maximum(b, 0.0)  # Safety

    r = b.copy()
    # Iterative pairwise projections.
    for _ in range(max(1, sweeps)):
        max_violation = 0.0
        for i in range(n - 1):
            ci = centers[i]
            ri = r[i]
            for j in range(i + 1, n):
                cj = centers[j]
                d = float(np.linalg.norm(ci - cj))
                # Constraint: r_i + r_j <= d
                s = ri + r[j]
                if s > d:
                    excess = s - d
                    max_violation = max(max_violation, excess)
                    # Reduce only the larger of the two radii by the excess.
                    if ri >= r[j]:
                        ri = max(0.0, ri - excess)
                    else:
                        r[j] = max(0.0, r[j] - excess)
        # commit ri updates
        r[i] = ri  # i from last loop persisted; commit last ri
        # Clamp to boundary and non-negative
        np.minimum(r, b, out=r)
        np.maximum(r, 0.0, out=r)
        if max_violation <= tol:
            break

    # Final cleanup: one more pass to eliminate any residual violations.
    for i in range(n - 1):
        for j in range(i + 1, n):
            d = float(np.linalg.norm(centers[i] - centers[j]))
            s = r[i] + r[j]
            if s > d:
                excess = s - d
                if r[i] >= r[j]:
                    r[i] = max(0.0, r[i] - excess)
                else:
                    r[j] = max(0.0, r[j] - excess)
    # Final clamp to boundary
    np.minimum(r, b, out=r)
    np.maximum(r, 0.0, out=r)
    return r


def _relax_centers(
    centers: np.ndarray,
    margin: float = 0.03,
    iterations: int = 120,
    alpha0: float = 0.012,
    decay: float = 0.985,
) -> np.ndarray:
    """Deterministic relaxation of centers guided by active constraints.

    At each iteration:
    - Compute LP-optimal radii for current centers.
    - Identify active boundary constraints (r_i ~ boundary limit b_i).
    - Identify active pair constraints (r_i + r_j ~ d_ij).
    - Move each center a small step along the ascent direction formed by the
      inward normals of active walls and unit directions away from active neighbors.

    Parameters
    ----------
    centers : ndarray, shape (n, 2)
        Initial centers inside [0,1]^2.
    margin : float
        Interior margin to project centers back into (strictly inside the square).
    iterations : int
        Number of deterministic relaxation steps.
    alpha0 : float
        Initial step size in coordinate units.
    decay : float
        Multiplicative decay factor applied to step size each iteration.

    Returns
    -------
    centers : ndarray
        Relaxed centers, deterministically updated.
    """
    c = centers.copy()
    n = c.shape[0]
    # Weights for combining boundary and pair contributions to the ascent direction.
    w_bound = 0.7
    w_pair = 0.5

    # Activation tolerances (units in coordinates).
    # Boundary activation when b_i - r_i <= eps_b, pair activation when d_ij - (r_i + r_j) <= eps_p.
    eps_b = 0.0015
    eps_p = 0.0015

    alpha = alpha0
    for _ in range(max(0, iterations)):
        # LP radii for current centers
        r = compute_lp_radii(c, sweeps=10, tol=1e-12)

        # Precompute boundary limits
        left = c[:, 0]
        bottom = c[:, 1]
        right = 1.0 - c[:, 0]
        top = 1.0 - c[:, 1]
        b = np.minimum.reduce([left, bottom, right, top])

        # Gradients initialized to zero
        g = np.zeros_like(c)

        # Boundary contributions: push inward from walls that are active.
        # If boundary is binding (r ~ b), include the inward normal.
        # Identify all walls that attain the minimum b_i (ties possible).
        # Add contributions only if r is close to b (binding).
        binding_mask = (b - r) <= eps_b
        if np.any(binding_mask):
            # For active i, collect all walls that tie to b
            for i in np.where(binding_mask)[0]:
                di = np.array([left[i], bottom[i], right[i], top[i]], dtype=float)
                bi = b[i]
                # Activate walls that are within a tiny tolerance of bi
                # This helps when equidistant to two walls (e.g., near corners).
                wall_tol = 1e-9
                walls = np.where(di - bi <= wall_tol)[0]
                # Inward normals for the four walls:
                # left (x=0): inward +x, bottom (y=0): +y,
                # right (x=1): inward -x, top (y=1): -y.
                for w in walls:
                    if w == 0:
                        g[i] += np.array([1.0, 0.0])
                    elif w == 1:
                        g[i] += np.array([0.0, 1.0])
                    elif w == 2:
                        g[i] += np.array([-1.0, 0.0])
                    elif w == 3:
                        g[i] += np.array([0.0, -1.0])

        # Pair contributions: move away from neighbors where r_i + r_j ~= d_ij
        # We add for both i and j, opposite directions along the line connecting centers.
        for i in range(n - 1):
            ci = c[i]
            for j in range(i + 1, n):
                cj = c[j]
                v = ci - cj
                d = float(np.linalg.norm(v))
                if d <= 0.0:
                    # Exact coincidence (shouldn't happen in our seed). Use a fixed direction.
                    u = np.array([1.0, 0.0], dtype=float)
                    d = 0.0
                else:
                    u = v / d
                # Active if slack is small
                if d - (r[i] + r[j]) <= eps_p:
                    g[i] += u
                    g[j] -= u

        # Scale and apply gradients: combine pair and boundary contributions.
        # We first weight, then normalize per-center to keep step bounded.
        if np.any(g != 0.0):
            # Weight boundary and pair terms by multiplying the total gradient
            # because we've already summed contributions appropriately.
            # To balance, we scale boundary adds earlier via magnitude of contributions;
            # here we just scale the total with combined weight.
            # Apply weights by decomposing g into b and p parts would be more precise,
            # but for simplicity we scale the total g by an average factor.
            # Instead, we approximate: normalize then apply common alpha.
            pass

        # Normalize each gradient vector and update centers
        for i in range(n):
            gi = g[i]
            norm = float(np.linalg.norm(gi))
            if norm > 0.0:
                # Weight control: blend boundary/pair scaling
                # We apply the two weights by scaling the step magnitude.
                # Use a blended scaling factor based on relative contribution norms.
                step_dir = gi / norm
                # Apply combined scaling; keeping small step sizes ensures stability.
                c[i] = c[i] + alpha * (w_bound + w_pair) * step_dir

        # Project centers back into the interior margin box
        np.clip(c, margin, 1.0 - margin, out=c)

        # Decay the step size
        alpha *= decay

    return c


# Backward-compatible wrapper name preserved from baseline
def compute_max_radii(centers: np.ndarray) -> np.ndarray:
    """Wrapper for LP-style radii computation to maintain compatibility."""
    return compute_lp_radii(centers, sweeps=20, tol=1e-12)
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

This implementation follows a three-stage deterministic pipeline:

1) Hexagonal seeding (staggered rows) for near-uniform initial centers.
2) LP-like projection for radii: maximize sum r subject to boundary and
   pairwise non-overlap constraints by iterative pairwise projections
   that trim only the larger radius.
3) Deterministic center relaxation: move centers away from active constraints
   (binding walls and neighboring circles) with a decaying step, re-solving
   the LP radii each iteration to guide ascent. Centers are projected back
   into an interior margin to maintain feasibility.

The result is a reproducible arrangement with disjoint circles entirely inside
the unit square and a high sum of radii.
"""

import json

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of ``num_circles`` circles.

    This function constructs a deterministic packing using:
    - Hexagonal-like staggered seeding of centers.
    - LP-style projection to compute optimal radii for fixed centers.
    - Deterministic relaxation of centers guided by active constraints.

    Parameters
    ----------
    num_circles : int
        Number of circles to place. The algorithm is tuned for 26; for other
        counts, a best-effort subset/superset of the seeded points is used.

    Returns
    -------
    centers : ndarray, shape (num_circles, 2)
        Center coordinates within [0, 1]^2.
    radii : ndarray, shape (num_circles,)
        Non-negative radii satisfying boundary and non-overlap constraints.
    """
    # Interior margin to keep centers away from the boundary during relaxation.
    margin = 0.03

    # Seed centers in a 5-row staggered pattern, counts [5, 6, 5, 5, 5] -> 26 total.
    base_centers = _hex_seed_centers(margin=margin)

    # Adjust to requested number by slicing or tiling deterministically.
    centers = _adjust_count(base_centers, num_circles)

    # Deterministic relaxation of centers with decaying step size.
    centers = _relax_centers(centers, margin=margin, iterations=120, alpha0=0.012, decay=0.985)

    # Final radii via the LP-style projection with extra sweeps for cleanliness.
    radii = compute_lp_radii(centers, sweeps=20, tol=1e-12)

    return centers, radii


def _hex_seed_centers(margin: float = 0.03) -> np.ndarray:
    """Create a staggered (hex-like) seeding of 26 centers inside [0,1]^2.

    Pattern: 5 rows with counts [5, 6, 5, 5, 5], where the 6-count row is centered
    and 5-count rows are offset by half the base column spacing relative to the 6-count row.

    We deliberately keep all x positions strictly inside [margin, 1 - margin] by leaving
    a little room near the borders (we do not span the full width with the extreme centers).
    """
    counts = [5, 6, 5, 5, 5]  # total = 26
    n_rows = len(counts)
    width = 1.0 - 2.0 * margin

    # Base x spacing derived from 6 columns (widest row).
    max_cols = max(counts)
    s = width / float(max_cols)  # base column spacing

    # y positions evenly spaced across the interior margin band.
    # Use midpoints of 5 equal vertical bands: y_j = margin + (j + 0.5) * vy
    vy = (1.0 - 2.0 * margin) / float(n_rows)
    y_positions = margin + (np.arange(n_rows) + 0.5) * vy

    centers = []
    # We put the 6-count row on the second row (index 1) to mimic a hex stack.
    for row_idx, k in enumerate(counts):
        y = y_positions[row_idx]
        if k == max_cols:
            # 6-count row: positions centered at m + s/2 + n*s, n=0..5
            xs = margin + (0.5 + np.arange(k)) * s
        else:
            # 5-count rows: offset by +s relative to 6-count row grid; n=0..4
            xs = margin + (1.0 + np.arange(k)) * s
        for x in xs:
            centers.append([float(x), float(y)])

    centers = np.array(centers, dtype=float)
    # Safety clamp to keep centers within the margin box.
    np.clip(centers, margin, 1.0 - margin, out=centers)
    return centers


def _adjust_count(centers: np.ndarray, num_circles: int) -> np.ndarray:
    """Adjust a base list of centers to exactly num_circles deterministically.

    - If more are needed, we tile from the beginning and add a tiny deterministic
      jitter-free nudging within the margin to avoid duplicates exactly on top.
    - If fewer are needed, truncate.
    """
    base_n = centers.shape[0]
    if num_circles == base_n:
        return centers.copy()
    elif num_circles < base_n:
        return centers[:num_circles].copy()
    else:
        # Tile and then add a tiny deterministic offset pattern within a very small bound.
        reps = int(np.ceil(num_circles / base_n))
        tiled = np.vstack([centers for _ in range(reps)])[:num_circles].copy()
        # Deterministic micro-offsets (still within numeric safety margin).
        eps = 1e-6
        for i in range(base_n, num_circles):
            off = ((i - base_n) % 4)
            if off == 0:
                tiled[i, 0] += eps
            elif off == 1:
                tiled[i, 0] -= eps
            elif off == 2:
                tiled[i, 1] += eps
            else:
                tiled[i, 1] -= eps
        np.clip(tiled, 0.0, 1.0, out=tiled)
        return tiled


def compute_lp_radii(centers: np.ndarray, sweeps: int = 12, tol: float = 1e-12) -> np.ndarray:
    """Compute radii via iterative LP-style projection for fixed centers.

    Objective: maximize sum r_i
    Subject to: 0 <= r_i <= b_i (boundary), r_i + r_j <= d_ij for all i < j

    Algorithm: initialize r = b. For K sweeps over all pairs, if r_i + r_j > d_ij,
    reduce only the larger of r_i, r_j by exactly the excess (minimal change to
    satisfy that pairwise constraint). Clamp r back to [0, b] after each sweep.

    Parameters
    ----------
    centers : ndarray, shape (n, 2)
        Circle centers inside the unit square.
    sweeps : int
        Number of pairwise projection sweeps.
    tol : float
        Early stop tolerance on maximal violation per sweep.

    Returns
    -------
    radii : ndarray, shape (n,)
        Radii satisfying all constraints (within numerical tolerance).
    """
    n = centers.shape[0]
    # Boundary limits: min distance to the square boundaries.
    left = centers[:, 0]
    bottom = centers[:, 1]
    right = 1.0 - centers[:, 0]
    top = 1.0 - centers[:, 1]
    b = np.minimum.reduce([left, bottom, right, top])
    b = np.maximum(b, 0.0)  # Safety

    r = b.copy()
    # Iterative pairwise projections.
    for _ in range(max(1, sweeps)):
        max_violation = 0.0
        for i in range(n - 1):
            ci = centers[i]
            ri = r[i]
            for j in range(i + 1, n):
                cj = centers[j]
                d = float(np.linalg.norm(ci - cj))
                # Constraint: r_i + r_j <= d
                s = ri + r[j]
                if s > d:
                    excess = s - d
                    max_violation = max(max_violation, excess)
                    # Reduce only the larger of the two radii by the excess.
                    if ri >= r[j]:
                        ri = max(0.0, ri - excess)
                    else:
                        r[j] = max(0.0, r[j] - excess)
            # Critical fix: commit the locally updated ri for this i before moving on.
            # Clamp to boundary and non-negativity here to keep updates tight.
            r[i] = max(0.0, min(ri, b[i]))
        # Clamp to boundary and non-negative after the sweep as a safety pass.
        np.minimum(r, b, out=r)
        np.maximum(r, 0.0, out=r)
        if max_violation <= tol:
            break

    # Final cleanup: one more pass to eliminate any residual violations.
    for i in range(n - 1):
        for j in range(i + 1, n):
            d = float(np.linalg.norm(centers[i] - centers[j]))
            s = r[i] + r[j]
            if s > d:
                excess = s - d
                if r[i] >= r[j]:
                    r[i] = max(0.0, r[i] - excess)
                else:
                    r[j] = max(0.0, r[j] - excess)
    # Final clamp to boundary
    np.minimum(r, b, out=r)
    np.maximum(r, 0.0, out=r)
    return r


def _relax_centers(
    centers: np.ndarray,
    margin: float = 0.03,
    iterations: int = 120,
    alpha0: float = 0.012,
    decay: float = 0.985,
) -> np.ndarray:
    """Deterministic relaxation of centers guided by active constraints.

    At each iteration:
    - Compute LP-optimal radii for current centers.
    - Identify active boundary constraints (r_i ~ boundary limit b_i).
    - Identify active pair constraints (r_i + r_j ~ d_ij).
    - Move each center a small step along the ascent direction formed by the
      inward normals of active walls and unit directions away from active neighbors.

    Parameters
    ----------
    centers : ndarray, shape (n, 2)
        Initial centers inside [0,1]^2.
    margin : float
        Interior margin to project centers back into (strictly inside the square).
    iterations : int
        Number of deterministic relaxation steps.
    alpha0 : float
        Initial step size in coordinate units.
    decay : float
        Multiplicative decay factor applied to step size each iteration.

    Returns
    -------
    centers : ndarray
        Relaxed centers, deterministically updated.
    """
    c = centers.copy()
    n = c.shape[0]
    # Weights for combining boundary and pair contributions to the ascent direction.
    w_bound = 0.7
    w_pair = 0.5

    # Activation tolerances (units in coordinates).
    # Boundary activation when b_i - r_i <= eps_b, pair activation when d_ij - (r_i + r_j) <= eps_p.
    eps_b = 0.0015
    eps_p = 0.0015

    alpha = alpha0
    for _ in range(max(0, iterations)):
        # LP radii for current centers
        r = compute_lp_radii(c, sweeps=10, tol=1e-12)

        # Precompute boundary limits
        left = c[:, 0]
        bottom = c[:, 1]
        right = 1.0 - c[:, 0]
        top = 1.0 - c[:, 1]
        b = np.minimum.reduce([left, bottom, right, top])

        # Gradients initialized to zero
        g = np.zeros_like(c)

        # Boundary contributions: push inward from walls that are active.
        # If boundary is binding (r ~ b), include the inward normal.
        # Identify all walls that attain the minimum b_i (ties possible).
        # Add contributions only if r is close to b (binding).
        binding_mask = (b - r) <= eps_b
        if np.any(binding_mask):
            # For active i, collect all walls that tie to b
            for i in np.where(binding_mask)[0]:
                di = np.array([left[i], bottom[i], right[i], top[i]], dtype=float)
                bi = b[i]
                # Activate walls that are within a tiny tolerance of bi
                # This helps when equidistant to two walls (e.g., near corners).
                wall_tol = 1e-9
                walls = np.where(di - bi <= wall_tol)[0]
                # Inward normals for the four walls:
                # left (x=0): inward +x, bottom (y=0): +y,
                # right (x=1): inward -x, top (y=1): -y.
                for w in walls:
                    if w == 0:
                        g[i] += np.array([1.0, 0.0])
                    elif w == 1:
                        g[i] += np.array([0.0, 1.0])
                    elif w == 2:
                        g[i] += np.array([-1.0, 0.0])
                    elif w == 3:
                        g[i] += np.array([0.0, -1.0])

        # Pair contributions: move away from neighbors where r_i + r_j ~= d_ij
        # We add for both i and j, opposite directions along the line connecting centers.
        for i in range(n - 1):
            ci = c[i]
            for j in range(i + 1, n):
                cj = c[j]
                v = ci - cj
                d = float(np.linalg.norm(v))
                if d <= 0.0:
                    # Exact coincidence (shouldn't happen in our seed). Use a fixed direction.
                    u = np.array([1.0, 0.0], dtype=float)
                    d = 0.0
                else:
                    u = v / d
                # Active if slack is small
                if d - (r[i] + r[j]) <= eps_p:
                    g[i] += u
                    g[j] -= u

        # Scale and apply gradients: combine pair and boundary contributions.
        # We first weight, then normalize per-center to keep step bounded.
        if np.any(g != 0.0):
            # The combined gradient assembly above already balances counts of active
            # constraints; we apply a simple scalar magnitude via step size below.
            pass

        # Normalize each gradient vector and update centers
        for i in range(n):
            gi = g[i]
            norm = float(np.linalg.norm(gi))
            if norm > 0.0:
                # Weight control: blend boundary/pair scaling
                step_dir = gi / norm
                c[i] = c[i] + alpha * (w_bound + w_pair) * step_dir

        # Project centers back into the interior margin box
        np.clip(c, margin, 1.0 - margin, out=c)

        # Decay the step size
        alpha *= decay

    return c


# Backward-compatible wrapper name preserved from baseline
def compute_max_radii(centers: np.ndarray) -> np.ndarray:
    """Wrapper for LP-style radii computation to maintain compatibility."""
    return compute_lp_radii(centers, sweeps=20, tol=1e-12)
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
"""Deterministic constructor for packing 26 circles in the unit square."""

import json
import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 26):
    """Return centers and radii for a valid packing of 26 circles.

    Implements:
      1) Edge-fitted hexagonal lattice seeding (26 centers).
      2) Deterministic LP-style per-index radii projection (fixed centers).
      3) Active-constraint guided center relaxation with monotone backtracking.
      4) Final radii projection and serialization.

    The process is fully deterministic with fixed orders and tie-breaking.
    """
    if num_circles != 26:
        raise ValueError("This constructor is designed for exactly 26 circles.")

    # Small open margin to keep centers away from clipping the unit square.
    eps = 1e-3

    # Hexagonal seeding: 5 rows with counts [6, 5, 5, 5, 5]
    centers, dx, dy = _hex_seed_26(eps=eps)

    # Outer active-relaxation loop with monotone backtracking.
    centers = _relax_centers_monotone(
        centers=centers,
        dx=dx,
        dy=dy,
        eps=eps,
        sweeps=12,
        tau=1e-10,
        tiny=1e-12,
        outer_iters=60,
        alpha0_factor=0.1,
        rho=0.95,
        alpha_min_factor=1e-6,
    )

    # Final deterministic LP-style radii projection on the last centers.
    radii = _lp_project_radii(centers, sweeps=12, tol=1e-12, tiny=1e-12)

    return centers, radii


def _hex_seed_26(eps: float = 1e-3):
    """Deterministic edge-fitted hexagonal lattice that places 26 centers.

    Layout:
      - max_cols = 6; rows = [6, 5, 5, 5, 5]
      - Horizontal spacing dx fits edges tightly within [eps, 1-eps].
      - Vertical spacing dy = (sqrt(3)/2) * dx, near-hex spacing.
      - Rows with 5 columns are offset by dx/2 for a hex-like stagger.

    Returns:
      centers: (26, 2) array
      dx, dy: spacings used
    """
    rows = [6, 5, 5, 5, 5]
    max_cols = 6

    # Spacings derived from the target fit within [eps, 1-eps].
    dx = (1.0 - 2.0 * eps) / (max_cols - 1)
    dy = (np.sqrt(3.0) / 2.0) * dx

    # Vertically center the stack of rows.
    y_start = 0.5 - 0.5 * (len(rows) - 1) * dy

    centers = np.zeros((sum(rows), 2), dtype=float)
    idx = 0
    for k, count in enumerate(rows):
        y_k = y_start + k * dy
        if count == max_cols:
            # Full row aligned with edges.
            xs = np.linspace(eps, 1.0 - eps, count)
        else:
            # Staggered row offset by dx/2 to emulate hex lattice.
            xs = np.linspace(eps + dx / 2.0, 1.0 - eps - dx / 2.0, count)
        for x in xs:
            centers[idx, 0] = x
            centers[idx, 1] = y_k
            idx += 1

    # Clip to margin just in case; lattice should already respect margins.
    centers = np.clip(centers, eps, 1.0 - eps)

    return centers, dx, dy


def _lp_project_radii(centers: np.ndarray, sweeps: int = 12, tol: float = 1e-12, tiny: float = 1e-12):
    """Deterministic LP-style per-index radii projection for fixed centers.

    Constraints enforced:
      - Boundary caps: r_i <= b_i, where b_i = min(x_i, y_i, 1-x_i, 1-y_i).
      - Pairwise disjointness: r_i + r_j <= d_ij, where d_ij = ||c_i - c_j||.

    Update rule (lexicographic, per-index commit):
      - Initialize r := b (boundary caps).
      - For sweep in 1..S:
        For i in 0..n-1:
          For j in i+1..n-1:
            If r_i + r_j > d_ij:
              Reduce only the larger radius by the exact excess:
                If r_i >= r_j:
                  r_i <- min(b_i, max(0, d_ij - r_j))
                Else:
                  r_j <- min(b_j, max(0, d_ij - r_i))
          Commit boundary cap for i:
            r_i <- min(r_i, b_i)

      Stop early if max change < tol.
    """
    n = centers.shape[0]
    # Boundary caps.
    b = np.minimum.reduce(
        [centers[:, 0], centers[:, 1], 1.0 - centers[:, 0], 1.0 - centers[:, 1]]
    )
    r = b.copy()

    for _ in range(sweeps):
        max_change = 0.0
        for i in range(n):
            # Pair constraints with j > i
            for j in range(i + 1, n):
                # Euclidean distance (use tiny only in active-set computations).
                dij = float(np.linalg.norm(centers[i] - centers[j]))
                # If overlap in radii sum, reduce only the larger radius.
                if r[i] + r[j] > dij:
                    if r[i] >= r[j]:
                        new_ri = min(b[i], max(0.0, dij - r[j]))
                        change = abs(new_ri - r[i])
                        if change > max_change:
                            max_change = change
                        r[i] = new_ri
                    else:
                        new_rj = min(b[j], max(0.0, dij - r[i]))
                        change = abs(new_rj - r[j])
                        if change > max_change:
                            max_change = change
                        r[j] = new_rj
            # Per-index boundary commit (stabilizes projection).
            new_ri = min(r[i], b[i])
            change = abs(new_ri - r[i])
            if change > max_change:
                max_change = change
            r[i] = new_ri

        if max_change < tol:
            break

    # Final caps, just in case.
    r = np.clip(r, 0.0, b)
    return r


def _build_active_directions(centers: np.ndarray, radii: np.ndarray, tau: float = 1e-10, tiny: float = 1e-12):
    """Build ascent directions from active constraints for each center.

    Active constraints:
      - Boundary-active if r_i is within tau of b_i. Inward normals:
        left (+x), right (-x), bottom (+y), top (-y).
      - Pair-active if |r_i + r_j - d_ij| <= tau, with contact normal
        n_ij = (c_i - c_j)/max(d_ij, tiny) from j to i.
    Weights:
      - Unit weights for boundary normals.
      - Pair contacts use inverse-distance weights: w_ij = 1/max(d_ij, tiny).

    The direction for each i is the normalized sum of its active normals.
    """
    n = centers.shape[0]
    v = np.zeros_like(centers)

    # Boundary caps per index.
    dist_left = centers[:, 0]              # x
    dist_bottom = centers[:, 1]            # y
    dist_right = 1.0 - centers[:, 0]       # 1-x
    dist_top = 1.0 - centers[:, 1]         # 1-y

    # b_i is the limiting boundary distance.
    b = np.minimum.reduce([dist_left, dist_bottom, dist_right, dist_top])

    # Boundary normals mapping: 0->(+x), 1->(+y), 2->(-x), 3->(-y)
    boundary_normals = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0], [0.0, -1.0]], dtype=float)
    boundary_dists = np.stack([dist_left, dist_bottom, dist_right, dist_top], axis=1)

    # Boundary-active contributions.
    for i in range(n):
        if radii[i] >= b[i] - tau:
            # Include all boundaries within tolerance of the minimum.
            for k in range(4):
                if abs(boundary_dists[i, k] - b[i]) <= tau:
                    v[i] += boundary_normals[k]

    # Pair-active contributions (lexicographic order).
    for i in range(n):
        ci = centers[i]
        for j in range(i + 1, n):
            cj = centers[j]
            diff = ci - cj
            dij = float(np.linalg.norm(diff))
            # Active if tight within tolerance.
            if abs((radii[i] + radii[j]) - dij) <= tau:
                # Contact normal from j to i.
                denom = max(dij, tiny)
                nij = diff / denom
                w = 1.0 / denom
                v[i] += w * nij
                v[j] -= w * nij

    # Normalize nonzero vectors.
    for i in range(n):
        norm = float(np.linalg.norm(v[i]))
        if norm > tiny:
            v[i] /= norm

    return v


def _relax_centers_monotone(
    centers: np.ndarray,
    dx: float,
    dy: float,
    eps: float,
    sweeps: int = 12,
    tau: float = 1e-10,
    tiny: float = 1e-12,
    outer_iters: int = 60,
    alpha0_factor: float = 0.1,
    rho: float = 0.95,
    alpha_min_factor: float = 1e-6,
):
    """Active-constraint guided center relaxation with monotone backtracking.

    Steps per outer iteration:
      - Compute radii via deterministic LP-style projection.
      - Build active set and per-index ascent directions.
      - Perform projected backtracking on centers with initial step s0
        and shrinking factor rho until the total sum of radii strictly increases
        by >= 1e-12. If no improvement is found, stop.

    The objective (sum of radii) is guaranteed non-decreasing across iterations
    due to monotone acceptance.
    """
    n = centers.shape[0]
    s0 = alpha0_factor * min(dx, dy)

    # Compute initial radii and sum.
    radii = _lp_project_radii(centers, sweeps=sweeps, tol=1e-12, tiny=tiny)
    prev_sum = float(np.sum(radii))

    for _ in range(outer_iters):
        # Active-constraint guided directions.
        v = _build_active_directions(centers, radii, tau=tau, tiny=tiny)

        # Monotone backtracking line search.
        alpha = s0
        improved = False

        while alpha >= alpha_min_factor * s0:
            trial_centers = centers + alpha * v
            # Keep centers within the open margin box.
            trial_centers = np.clip(trial_centers, eps, 1.0 - eps)

            trial_radii = _lp_project_radii(trial_centers, sweeps=sweeps, tol=1e-12, tiny=tiny)
            trial_sum = float(np.sum(trial_radii))

            # Strictly accept only if sum improves by >= 1e-12.
            if trial_sum >= prev_sum + 1e-12:
                centers = trial_centers
                radii = trial_radii
                prev_sum = trial_sum
                improved = True
                break
            else:
                alpha *= rho

        # Stop if no improvement found this iteration.
        if not improved:
            break

    return centers


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
