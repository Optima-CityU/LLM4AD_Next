Interleave fixed-center LP allocations with barrier-based center moves and a monotone centered-uniform-inflation step under Σ r acceptance gates.

- LP preselection ranks multiple hexagonal-lattice seeds by solving a fixed-center LP that maximizes Σ r with boundary and pairwise constraints (ε≈1e−10) and keeps only the top K (e.g., K=2), cheaply focusing compute on high-potential basins.
- Centered uniform inflation uniformly scales (x, y, r) about (0.5, 0.5) by the largest t ≥ 1 that preserves boundary slacks, which preserves non-overlap and yields a monotone Σ r increase when boundaries are not saturated, followed by a fixed-center LP and a monotone acceptance gate.
- LP–mini-barrier–LP sandwich with epsilon annealing: The LP–mini-barrier–LP sandwich endgame, using small barrier weights (e.g., [2e−3, 1e−3, 5e−4]) and ε annealed down to ≈1e−12 (with tiny pre-shrink to keep slacks positive), nudges centers to open tiny slack and immediately captures it with an LP, improving near-tangential packings without collisions under a Σ r increase gate.
- TriHex LP-Preselect with Barrier–LP Cycling and Centered Uniform Inflation achieved sum_radii 2.3408763459449666 with validity 1.0 on packing 21 circles inside the unit square, supporting the effectiveness of the LP–barrier–inflation sequencing and acceptance gates.
- Early centered uniform inflation gate + fixed-center LP capture: In the 21-circle unit-square packing, scaling centers and radii about (0.5, 0.5) by the largest t>1 that preserves strictly positive wall slacks under ε_init, then immediately solving a fixed-center LP and accepting only if Σr strictly increases, reliably converted boundary slack at the start and delivered measured +0.001 to +0.005 Σr gains at N=21, contributing to a final Σr of 2.3577789172578774 with validity 1.0; this gate should be reused when circles are constrained inside a box (perimeter ≤ 4 by construction) to safely monetize wall slack early while maintaining strict feasibility and non-overlap under ε-annealing.

```python
#!/usr/bin/env python3
"""Improved candidate for packing 21 circles inside the unit square to maximize sum of radii.

Algorithm overview (implements the provided high-level description):
- Multi-start hexagonal-lattice seeding with jitter and multiple row-count patterns that sum to 21.
- LP preselection using a fixed-center LP (SciPy HiGHS if available, greedy fallback otherwise).
- Interior log-barrier ascent on (x, y, r) with a decreasing barrier weight schedule.
- Periodic fixed-center LP to reallocate radii exactly (accept only if sum(r) increases).
- Centered uniform inflation (monotone increase) followed by LP.
- Endgame LP–mini-barrier–LP sandwich with epsilon annealing to tighten near-tangencies.
- Strict feasibility polish: clip to box and eliminate residual overlaps by tiny shrinkage.

The packing is constrained to the unit square, which ensures the minimal circumscribing
axis-aligned rectangle has perimeter at most 4. We aim to maximize sum of radii.
"""

import json
from typing import List, Tuple, Optional

import numpy as np

# Try to import a linear programming solver (SciPy's HiGHS). If unavailable, we'll gracefully fall back.
try:
    from scipy.optimize import linprog  # type: ignore

    HAS_SCIPY = True
except Exception:
    HAS_SCIPY = False


# =========================
# Utility and core routines
# =========================

def compute_boundary_limits(xy: np.ndarray, eps: float) -> np.ndarray:
    """Compute per-circle maximum radius allowed by box boundaries with margin eps."""
    x = xy[:, 0]
    y = xy[:, 1]
    m = np.minimum(np.minimum(x, 1.0 - x), np.minimum(y, 1.0 - y)) - eps
    return np.maximum(m, 0.0)


def pairwise_dists(xy: np.ndarray) -> np.ndarray:
    """Compute Euclidean pairwise distances for centers; N x N symmetric with zeros on diagonal."""
    diff = xy[:, None, :] - xy[None, :, :]
    D = np.sqrt(np.maximum(np.sum(diff * diff, axis=2), 0.0))
    return D


def fixed_center_lp_radii(
    xy: np.ndarray, eps: float = 1e-10, prefer_scipy: bool = True
) -> Tuple[np.ndarray, float, bool]:
    """Solve the fixed-center LP: maximize sum(r) subject to
       - 0 <= r_i <= m_i (boundary)
       - r_i + r_j <= d_ij - eps for all i<j

    Returns (r, sum_r, ok). If no LP solver available, use a greedy feasible projection fallback.
    """
    N = xy.shape[0]
    m = compute_boundary_limits(xy, eps)

    # Quick infeasibility guard: if any m < 0, clamp to 0
    m = np.maximum(m, 0.0)

    # If SciPy is available, use HiGHS which is fast and robust for small LPs
    if HAS_SCIPY and prefer_scipy:
        D = pairwise_dists(xy)
        # Bounds: 0 <= r_i <= m_i
        bounds = [(0.0, float(m_i)) for m_i in m]
        # Objective: maximize sum r_i => minimize -sum r_i
        c = -np.ones(N, dtype=float)

        # Constraints: A_ub @ r <= b_ub
        # For every pair i<j: e_i + e_j <= D[i,j] - eps
        # We'll build rows on the fly.
        rows = []
        rhs = []
        for i in range(N):
            for j in range(i + 1, N):
                rhs_ij = D[i, j] - eps
                # If rhs_ij <= 0, then the only feasible solution is with very small radii near zero for i or j.
                # Keep the constraint anyway; the LP will set radii accordingly.
                row = np.zeros(N, dtype=float)
                row[i] = 1.0
                row[j] = 1.0
                rows.append(row)
                rhs.append(rhs_ij)

        if rows:
            A_ub = np.vstack(rows)
            b_ub = np.array(rhs, dtype=float)
        else:
            A_ub = None
            b_ub = None

        try:
            res = linprog(
                c=c,
                A_ub=A_ub,
                b_ub=b_ub,
                bounds=bounds,
                method="highs",
            )
            if res.success and res.x is not None:
                r = np.clip(res.x, 0.0, m)
                sum_r = float(np.sum(r))
                return r, sum_r, True
        except Exception:
            # fall back if solver errors
            pass

    # Greedy fallback: start from boundary limits, then reduce to satisfy pairwise constraints iteratively.
    # Not optimal but feasible and fast.
    r = m.copy()
    D = pairwise_dists(xy)
    # Iteratively enforce r_i + r_j <= D_ij - eps by shrinking the larger of the two
    for _ in range(4 * N * N):
        changed = False
        for i in range(N):
            for j in range(i + 1, N):
                rhs_ij = D[i, j] - eps
                if rhs_ij < 0:
                    rhs_ij = 0.0
                total = r[i] + r[j]
                if total > rhs_ij + 1e-15:
                    # shrink the larger one just enough
                    excess = total - rhs_ij
                    if r[i] >= r[j]:
                        r[i] = max(r[i] - excess, 0.0)
                    else:
                        r[j] = max(r[j] - excess, 0.0)
                    changed = True
        if not changed:
            break
    r = np.clip(r, 0.0, m)
    return r, float(np.sum(r)), True


def strictly_feasible_init_radii(xy: np.ndarray, eps: float) -> np.ndarray:
    """Construct a strictly feasible small initial radii vector for given centers."""
    m = compute_boundary_limits(xy, eps)
    # Small fraction of boundary limit to guarantee feasibility for pairwise constraints
    r = np.minimum(m, 0.01)
    # Slight uniform shrink to ensure strict positivity in logs
    r *= 0.95
    return r


def barrier_objective_and_grad(xy: np.ndarray, r: np.ndarray, mu: float, eps: float) -> Tuple[float, np.ndarray, np.ndarray, np.ndarray, bool]:
    """Compute f = sum(r) + mu * sum(log(slack)) and its gradient wrt x, y, r.

    Returns tuple (f, gx, gy, gr, ok) where ok indicates all slacks are positive.
    """
    N = xy.shape[0]
    x = xy[:, 0].copy()
    y = xy[:, 1].copy()
    # Initialize gradients
    gx = np.zeros(N, dtype=float)
    gy = np.zeros(N, dtype=float)
    gr = np.ones(N, dtype=float)  # d/d r of sum(r) contributes 1

    # Start with objective from sum(r)
    f = float(np.sum(r))

    # Boundary slacks
    s_left = x - r - eps
    s_right = 1.0 - x - r - eps
    s_bottom = y - r - eps
    s_top = 1.0 - y - r - eps

    if (
        np.min(s_left) <= 0.0
        or np.min(s_right) <= 0.0
        or np.min(s_bottom) <= 0.0
        or np.min(s_top) <= 0.0
    ):
        return -np.inf, gx, gy, gr, False

    # Add boundary logs
    f += mu * (
        np.sum(np.log(s_left))
        + np.sum(np.log(s_right))
        + np.sum(np.log(s_bottom))
        + np.sum(np.log(s_top))
    )

    inv_s_left = mu / s_left
    inv_s_right = mu / s_right
    inv_s_bottom = mu / s_bottom
    inv_s_top = mu / s_top

    # Gradients from boundary constraints
    gx += inv_s_left * 1.0
    gx += inv_s_right * (-1.0)
    gy += inv_s_bottom * 1.0
    gy += inv_s_top * (-1.0)

    gr += (-inv_s_left) + (-inv_s_right) + (-inv_s_bottom) + (-inv_s_top)

    # Pairwise slacks
    D = pairwise_dists(xy)
    ok = True
    for i in range(N):
        xi = x[i]
        yi = y[i]
        for j in range(i + 1, N):
            dx = xi - x[j]
            dy = yi - y[j]
            dij = D[i, j]
            if dij <= 0:
                dij = 1e-16  # avoid division by zero
            s_ij = dij - r[i] - r[j] - eps
            if s_ij <= 0.0:
                ok = False
                # No need to continue; but accumulate nothing further
                continue
            # Contribution to objective
            f += mu * np.log(s_ij)
            inv = mu / (s_ij * dij)
            # gradients on centers
            gx[i] += inv * dx
            gy[i] += inv * dy
            gx[j] -= inv * dx
            gy[j] -= inv * dy
            # gradients on radii
            inv_r = mu / s_ij
            gr[i] += -inv_r
            gr[j] += -inv_r

    if not ok:
        return -np.inf, gx, gy, gr, False

    return f, gx, gy, gr, True


def enforce_strict_feasibility(xy: np.ndarray, r: np.ndarray, eps: float) -> Tuple[np.ndarray, np.ndarray]:
    """Clip centers to [r+eps, 1-r-eps] and ensure radii minimally positive."""
    x = xy[:, 0]
    y = xy[:, 1]
    # Clip centers
    x = np.clip(x, r + eps, 1.0 - r - eps)
    y = np.clip(y, r + eps, 1.0 - r - eps)
    # Ensure radii are >= tiny
    r = np.maximum(r, 1e-9)
    return np.column_stack((x, y)), r


def overlap_polish(xy: np.ndarray, r: np.ndarray, eps: float, max_iters: int = 200) -> np.ndarray:
    """Resolve any lingering pairwise overlaps by shrinking radii slightly.
    This preserves boundary feasibility and yields strictly positive slacks."""
    N = len(r)
    for _ in range(max_iters):
        D = pairwise_dists(xy)
        worst = 0.0
        worst_pair = None
        for i in range(N):
            for j in range(i + 1, N):
                s_ij = D[i, j] - r[i] - r[j] - eps
                if s_ij < worst:
                    worst = s_ij
                    worst_pair = (i, j)
        if worst_pair is None or worst >= 0.0:
            break
        i, j = worst_pair
        deficit = -worst + 1e-12
        # shrink the larger radius by deficit
        if r[i] >= r[j]:
            r[i] = max(r[i] - deficit, 1e-9)
        else:
            r[j] = max(r[j] - deficit, 1e-9)
    return r


def backtracking_barrier_ascent(
    xy: np.ndarray,
    r: np.ndarray,
    mu: float,
    eps: float,
    max_iters: int = 120,
    init_step: float = 0.05,
    shrink: float = 0.5,
) -> Tuple[np.ndarray, np.ndarray]:
    """Run a simple gradient ascent on the barrier-augmented objective, maintaining strict feasibility."""
    xy = xy.copy()
    r = r.copy()
    N = xy.shape[0]

    # Tiny pre-shrink to ensure strict positivity for slacks at start of stage
    r *= 0.999999

    # Compute initial objective and gradient
    f, gx, gy, gr, ok = barrier_objective_and_grad(xy, r, mu, eps)
    if not ok:
        # If somehow infeasible, slightly shrink radii uniformly until feasible
        for _ in range(20):
            r *= 0.9
            f, gx, gy, gr, ok = barrier_objective_and_grad(xy, r, mu, eps)
            if ok:
                break
        if not ok:
            return xy, r

    step = init_step
    for _ in range(max_iters):
        # Normalize gradient vector to unit norm to set the direction
        g_norm = np.sqrt(np.sum(gx * gx) + np.sum(gy * gy) + np.sum(gr * gr))
        if not np.isfinite(g_norm) or g_norm < 1e-12:
            break
        dx = (gx / (g_norm + 1e-18)) * step
        dy = (gy / (g_norm + 1e-18)) * step
        dr = (gr / (g_norm + 1e-18)) * step

        # Try backtracking to ensure feasibility and objective increase
        improved = False
        current_xy = xy
        current_r = r
        for _bt in range(20):
            trial_xy = np.empty_like(xy)
            trial_xy[:, 0] = current_xy[:, 0] + dx
            trial_xy[:, 1] = current_xy[:, 1] + dy
            trial_r = current_r + dr

            # Enforce minimal radii positivity and keep centers within [r+eps, 1-r-eps] softly
            trial_r = np.maximum(trial_r, 1e-12)
            # Soft clip centers — if clipping is needed, we still consider it a valid step
            trial_xy[:, 0] = np.clip(trial_xy[:, 0], trial_r + eps, 1.0 - trial_r - eps)
            trial_xy[:, 1] = np.clip(trial_xy[:, 1], trial_r + eps, 1.0 - trial_r - eps)

            f_new, gx_new, gy_new, gr_new, ok_new = barrier_objective_and_grad(trial_xy, trial_r, mu, eps)
            if ok_new and np.isfinite(f_new) and f_new > f + 1e-12:
                # Accept step
                xy = trial_xy
                r = trial_r
                f = f_new
                gx, gy, gr = gx_new, gy_new, gr_new
                improved = True
                break
            else:
                # shrink step
                dx *= shrink
                dy *= shrink
                dr *= shrink
        if not improved:
            # Could not improve further
            break

    return xy, r


def centered_uniform_inflation(xy: np.ndarray, r: np.ndarray, eps: float) -> Tuple[np.ndarray, np.ndarray, float]:
    """Uniformly scale centers and radii about (0.5, 0.5) by the maximal t >= 1
    that preserves boundary feasibility. Pairwise feasibility is preserved and improves under uniform expansion.
    Returns (xy_new, r_new, t)."""
    x = xy[:, 0]
    y = xy[:, 1]
    N = len(r)

    # Boundary slacks at t=1 and their slopes in t
    # For each circle, four constraints of the form s(t) = s0 + b*(t-1) >= 0
    # Left: s0 = x - r - eps; b = (x - 0.5) - r
    # Right: s0 = 1 - x - r - eps; b = -((x - 0.5) + r)
    # Bottom: s0 = y - r - eps; b = (y - 0.5) - r
    # Top: s0 = 1 - y - r - eps; b = -((y - 0.5) + r)
    s0_left = x - r - eps
    b_left = (x - 0.5) - r
    s0_right = 1.0 - x - r - eps
    b_right = -((x - 0.5) + r)
    s0_bottom = y - r - eps
    b_bottom = (y - 0.5) - r
    s0_top = 1.0 - y - r - eps
    b_top = -((y - 0.5) + r)

    t_candidates = [np.inf]

    def add_bounds(s0: np.ndarray, b: np.ndarray):
        for si, bi in zip(s0, b):
            if bi < 0.0:
                # t <= 1 - si/bi
                t_lim = 1.0 - si / bi
                if t_lim > 1.0:
                    t_candidates.append(t_lim)
                else:
                    # Already limiting at or below 1, include it anyway
                    t_candidates.append(t_lim)

    add_bounds(s0_left, b_left)
    add_bounds(s0_right, b_right)
    add_bounds(s0_bottom, b_bottom)
    add_bounds(s0_top, b_top)

    t_max = min(t_candidates)
    if not np.isfinite(t_max):
        t_max = 1.0
    t = max(1.0, t_max)

    # Apply scaling
    if t <= 1.0 + 1e-12:
        return xy, r, 1.0

    xc = 0.5
    yc = 0.5
    xy_new = np.empty_like(xy)
    xy_new[:, 0] = xc + t * (x - xc)
    xy_new[:, 1] = yc + t * (y - yc)
    r_new = t * r

    # Final clip to maintain strict feasibility (should be fine given construction)
    xy_new, r_new = enforce_strict_feasibility(xy_new, r_new, eps)
    return xy_new, r_new, t


def generate_hex_seeds(num_circles: int = 21, rng: Optional[np.random.Generator] = None) -> List[np.ndarray]:
    """Generate multiple hexagonal-staggered seeds that sum to the given number of circles."""
    if rng is None:
        rng = np.random.default_rng(12345)

    patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
        [5, 4, 5, 4, 3],
    ]
    # Ensure patterns sum to num_circles (21)
    patterns = [p for p in patterns if sum(p) == num_circles]

    spacings = [0.2 * 0.98, 0.2, 0.2 * 1.02]
    seeds: List[np.ndarray] = []
    margin = 1e-3

    for counts in patterns:
        R = len(counts)  # number of rows
        for s in spacings:
            hy = s * np.sqrt(3) / 2.0
            ys = np.array([0.5 + (i - (R - 1) / 2.0) * hy for i in range(R)], dtype=float)
            # Prepare centers
            centers = []
            for i_row, c in enumerate(counts):
                # True hex staggering: alternate rows offset by half step in x
                offset = 0.5 * s if (i_row % 2 == 1) else 0.0
                xs = np.array([0.5 + (j - (c - 1) / 2.0) * s + offset for j in range(c)], dtype=float)
                for x in xs:
                    centers.append([x, ys[i_row]])
            centers = np.array(centers, dtype=float)
            # Jitter to break symmetry and avoid exact equal distances
            centers += rng.uniform(low=-1e-3, high=1e-3, size=centers.shape)
            # Clip to unit square with a tiny margin
            centers[:, 0] = np.clip(centers[:, 0], margin, 1.0 - margin)
            centers[:, 1] = np.clip(centers[:, 1], margin, 1.0 - margin)
            seeds.append(centers)

    # If no patterns matched, fall back to a 5x5 grid picking the first 21
    if not seeds:
        grid = np.array([[((c + 0.5) / 5.0), ((r + 0.5) / 5.0)] for r in range(5) for c in range(5)], dtype=float)
        seeds.append(grid[:num_circles])

    return seeds


def optimize_seed(
    centers: np.ndarray,
    rng: Optional[np.random.Generator] = None,
    K_preselect: int = 2,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """Run the full pipeline for one seed: LP preselection across all seeds happens outside.
    This function runs the barrier ascent, LP interleaves, uniform inflation, sandwich, and feasibility polish.
    Returns (centers, radii, sum_r)."""

    if rng is None:
        rng = np.random.default_rng(2024)

    N = centers.shape[0]

    # Epsilon schedule
    eps_init = 1e-6
    eps_lp_mid = 1e-9
    eps_final = 1e-12

    # Initialize radii strictly feasible
    r = strictly_feasible_init_radii(centers, eps_init)
    xy = centers.copy()

    # Initial LP to allocate radii (accept only if it increases sum)
    r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy, eps=eps_init)
    if ok_lp and sum_lp > float(np.sum(r)) + 1e-14:
        r = r_lp.copy()

    # Barrier ascent schedule
    mu_schedule = [5e-2, 2e-2, 1e-2, 5e-3, 3e-3, 1e-3]
    for mu in mu_schedule:
        xy, r = backtracking_barrier_ascent(xy, r, mu=mu, eps=eps_init, max_iters=120, init_step=0.05)
        # Re-allocate radii at fixed centers
        r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy, eps=eps_init)
        if ok_lp and sum_lp > float(np.sum(r)) + 1e-14:
            r = r_lp.copy()

    # Centered uniform inflation followed by LP
    xy2, r2, t = centered_uniform_inflation(xy, r, eps=eps_init)
    if t > 1.0 + 1e-12:
        # LP with same epsilon
        r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy2, eps=eps_init)
        sum_r2 = float(np.sum(r2))
        if ok_lp and sum_lp > sum_r2 + 1e-14:
            # Accept LP radii at inflated centers
            xy = xy2
            r = r_lp
        elif sum_r2 > float(np.sum(r)) + 1e-14:
            # Accept uniform inflation alone
            xy = xy2
            r = r2

    # Anneal epsilon down and run LP–mini-barrier–LP sandwich
    # First a tighter LP
    r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy, eps=eps_lp_mid)
    if ok_lp and sum_lp > float(np.sum(r)) + 1e-14:
        r = r_lp

    # Tiny pre-shrink to ensure strict feasibility for smaller eps barriers
    r *= 0.999999
    # Short mini-barrier at smaller eps with small mus
    mini_mus = [2e-3, 1e-3, 5e-4]
    for mu in mini_mus:
        xy, r = backtracking_barrier_ascent(xy, r, mu=mu, eps=eps_lp_mid, max_iters=80, init_step=0.03)

    # LP again with tighter eps
    r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy, eps=eps_lp_mid)
    if ok_lp and sum_lp > float(np.sum(r)) + 1e-14:
        r = r_lp

    # Optional: another centered uniform inflation + LP with tighter eps
    xy2, r2, t = centered_uniform_inflation(xy, r, eps=eps_lp_mid)
    if t > 1.0 + 1e-12:
        # LP at fixed centers
        r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy2, eps=eps_lp_mid)
        sum_r2 = float(np.sum(r2))
        if ok_lp and sum_lp > sum_r2 + 1e-14:
            xy = xy2
            r = r_lp
        elif sum_r2 > float(np.sum(r)) + 1e-14:
            xy = xy2
            r = r2

    # Final LP at very small epsilon to reclaim tiny slack
    r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy, eps=eps_final)
    if ok_lp and sum_lp > float(np.sum(r)) + 1e-14:
        r = r_lp

    # Strict feasibility polish
    xy, r = enforce_strict_feasibility(xy, r, eps_final)
    r = overlap_polish(xy, r, eps=eps_final, max_iters=200)

    return xy, r, float(np.sum(r))


def multi_start_optimize(num_circles: int = 21) -> Tuple[np.ndarray, np.ndarray]:
    """Generate seeds, preselect via LP, then run full optimization for the top seeds.
    Returns the best packing found (centers, radii)."""

    rng = np.random.default_rng(2025)
    seeds = generate_hex_seeds(num_circles=num_circles, rng=rng)

    # LP preselection: solve the fixed-center LP for each seed and score by sum(r)
    scores: List[Tuple[float, int, np.ndarray, np.ndarray]] = []
    for idx, seed in enumerate(seeds):
        r_lp, sum_lp, ok = fixed_center_lp_radii(seed, eps=1e-10)
        if ok:
            scores.append((sum_lp, idx, seed, r_lp))
        else:
            # Fallback scoring by boundary-only sum
            m = compute_boundary_limits(seed, eps=1e-10)
            scores.append((float(np.sum(m)), idx, seed, m))

    # Sort by score descending and keep top-K seeds for polishing
    K = min(2, len(scores))
    scores.sort(key=lambda t: t[0], reverse=True)
    top = scores[:K] if K > 0 else list()

    best_sum = -1.0
    best_xy = None
    best_r = None

    if not top:
        # No LP available, run all seeds
        top = [(0.0, i, s, strictly_feasible_init_radii(s, 1e-6)) for i, s in enumerate(seeds)]

    for _, _, seed_xy, _ in top:
        xy, r, sum_r = optimize_seed(seed_xy, rng=rng)
        if sum_r > best_sum + 1e-14:
            best_sum = sum_r
            best_xy = xy
            best_r = r

    assert best_xy is not None and best_r is not None
    return best_xy, best_r


# EVOLVE_START
def construct_packing(num_circles: int = 21):
    """Build the packing using multi-start hex-lattice seeding, LP preselection, barrier ascent, and polishing."""
    assert num_circles == 21, "This solver is tuned for exactly 21 circles."

    centers, radii = multi_start_optimize(num_circles=num_circles)
    circles = np.column_stack((centers, radii))
    return circles
# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```

```python
#!/usr/bin/env python3
"""High-quality candidate for packing 21 disjoint circles inside the unit square.

Implements a hybrid algorithm combining:
- multi-start hexagonal seeding,
- fixed-center LP preselection,
- strictly feasible log-barrier ascent (centers + radii) with adaptive LP cadence,
- dual centered uniform global inflations (early and mid-run) under strict acceptance gates,
- endgame LP–mini-barrier–LP sandwich,
- strict-feasibility polishing and micro-overlap removal,
and selection of the best candidate by Σr.

Any set of circles entirely inside the unit square [0,1]^2 has circumscribing
rectangle perimeter ≤ 4, so we optimize inside the square.

Notes:
- SciPy linprog is used if available for exact fixed-center LP steps; otherwise
  the algorithm gracefully falls back to barrier and inflation only (with a
  conservative fixed-center heuristic).
- Parameters follow prior successful configurations for 21 circles.

The output is a JSON containing rows [x, y, radius] for 21 circles.
"""

import json
import math
import random
from typing import List, Tuple, Optional

import numpy as np

try:
    # SciPy is optional; if unavailable we still produce strong packings.
    from scipy.optimize import linprog  # type: ignore

    HAS_SCIPY = True
except Exception:
    HAS_SCIPY = False


# EVOLVE_START
def construct_packing(num_circles: int = 21):
    """
    Construct an optimized packing of num_circles disjoint circles in the unit square.

    Returns:
        np.ndarray of shape (num_circles, 3): rows [x, y, radius].
    """

    # Global algorithm parameters (chosen from prior successful runs).
    rng = np.random.default_rng(42)
    EPS_INIT = 1e-6
    EPS_FINAL = 1e-12
    EPS_LP = 5e-10  # small ε to keep strict feasibility in LP capture
    MU_SCHEDULE = [5e-2, 2e-2, 1e-2, 5e-3, 3e-3, 1e-3]
    JITTER_SMALL = 1e-3
    JITTER_LARGE = 2e-3
    TAU_NEAR = 0.015  # near-active threshold for adaptive LP cadence and lazy constraints
    MAX_PRESELECT = 3  # number of best seeds to fully polish
    MAX_BARRIER_ITERS_PER_STAGE = 200
    K_NEIGHBORS_INIT = 6  # initial lazy adjacency neighbor count
    ANCHOR_LAMBDA = 3e-4  # penalty weight for second-anchor pinning
    SHRINK_ON_EPS_DROP = 0.999999
    MICRO_OVERLAP_ITERS = 8

    # 1) Multi-start true hexagonal seeding (TriHex+ patterns).
    seed_centers_list = generate_hex_seeds_21(rng)

    # Create jittered copies to break symmetry and widen basins.
    jittered_seeds = []
    for centers in seed_centers_list:
        for _ in range(2):  # small jitter copies
            C = centers.copy()
            C += rng.uniform(-JITTER_SMALL, JITTER_SMALL, size=C.shape)
            C = np.clip(C, 1e-3, 1 - 1e-3)
            jittered_seeds.append(C)
        # one larger jitter
        C = centers.copy()
        C += rng.uniform(-JITTER_LARGE, JITTER_LARGE, size=C.shape)
        C = np.clip(C, 1e-3, 1 - 1e-3)
        jittered_seeds.append(C)

    # 2) Optional two-scale seed: 5x4 base grid + 1 interstitial (clearance-aware).
    jittered_seeds.extend(generate_two_scale_seed_21(rng))

    # 3) LP-only preselection: solve fixed-center LP on each seed and keep top K.
    candidates: List[Tuple[np.ndarray, np.ndarray, float]] = []  # (centers, radii_lp, sum_r_lp)
    for centers in jittered_seeds:
        radii_lp = fixed_center_lp(centers, eps_lp=EPS_LP)
        sum_r = float(np.sum(radii_lp))
        candidates.append((centers, radii_lp, sum_r))

    # Rank by LP sum of radii and keep the top MAX_PRESELECT
    candidates.sort(key=lambda t: t[2], reverse=True)
    preselected = candidates[:MAX_PRESELECT] if len(candidates) > MAX_PRESELECT else candidates

    # 4) Growth-and-move via strictly-feasible log-barrier ascent w/ adaptive LP cadence.
    best_circles = None
    best_sum_r = -1.0

    for (centers0, radii0_lp, _) in preselected:
        centers = centers0.copy()
        # Initialize radii: use LP radii if SciPy available; otherwise a safe fraction of wall clearance.
        if HAS_SCIPY:
            radii = radii0_lp.copy()
            radii = np.maximum(radii, 1e-6)  # strictly positive
        else:
            m = wall_clearances(centers)
            radii = 0.45 * m

        # Strict feasibility: ensure all constraints have positive slack with EPS_INIT.
        epsilon = EPS_INIT
        radii, centers = enforce_strict_feasibility(centers, radii, epsilon)

        # 5) Early centered uniform inflation gate + LP capture, accepted only if Σr increases
        sum_r_before = float(np.sum(radii))
        c_infl, r_infl = uniform_global_inflation(centers, radii, epsilon)
        if HAS_SCIPY:
            r_lp = fixed_center_lp(c_infl, eps_lp=EPS_LP)
            if float(np.sum(r_lp)) > sum_r_before:
                centers, radii = c_infl, r_lp
                radii, centers = enforce_strict_feasibility(centers, radii, epsilon)
        else:
            if float(np.sum(r_infl)) > sum_r_before:
                centers, radii = c_infl, r_infl
                radii, centers = enforce_strict_feasibility(centers, radii, epsilon)

        # Lazy adjacency: k-nearest neighbor indices (start small, augment lazily).
        pair_idx = build_lazy_pairs(centers, k=K_NEIGHBORS_INIT)

        # Adaptive LP cadence control
        lp_period = 50
        last_sum_r = np.sum(radii)
        near_counts_hist = []

        # Set up anchors for second-anchor pinning (penalty stabilization).
        idx_anchor_bl = int(np.argmin(centers[:, 0] + centers[:, 1]))  # bottom-left-ish
        idx_anchor_br = int(np.argmax(centers[:, 0]))  # right-most-ish

        # Mid-run inflation flag
        mid_inflation_done = False

        for mu_idx, mu in enumerate(MU_SCHEDULE):
            # ε-annealed: gradually reduce epsilon and apply tiny shrink to radii to preserve strict slacks.
            if epsilon > EPS_FINAL:
                epsilon = max(EPS_FINAL, epsilon * 0.1)
                radii *= SHRINK_ON_EPS_DROP
                radii, centers = enforce_strict_feasibility(centers, radii, epsilon)

            # Main barrier iterations
            for it in range(MAX_BARRIER_ITERS_PER_STAGE):
                # Evaluate slacks
                walls = compute_wall_slacks(centers, radii, epsilon)
                pairs, dists = compute_pair_slacks(centers, radii, pair_idx, epsilon)

                # Strictly feasible guard: if any slack nonpositive due to numerical drift, shrink a tiny bit
                if min_over_walls(walls) <= 0 or (pairs.size > 0 and np.min(pairs) <= 0):
                    radii *= 0.999999
                    radii, centers = enforce_strict_feasibility(centers, radii, epsilon)
                    walls = compute_wall_slacks(centers, radii, epsilon)
                    pairs, dists = compute_pair_slacks(centers, radii, pair_idx, epsilon)

                # Objective and gradient
                f = float(np.sum(radii) + mu * (sum_log_walls(walls) + (np.sum(np.log(pairs)) if pairs.size else 0.0)))
                grad_x, grad_y, grad_r = barrier_gradient(centers, radii, walls, pairs, pair_idx, dists, mu, epsilon)

                # Second-anchor pinning penalties (stabilization):
                # Encourage x-r≈0 and y-r≈0 for bottom-left-ish; and y-r≈0 for right-most-ish.
                lambda_a = ANCHOR_LAMBDA
                if lambda_a > 0 and 0 <= idx_anchor_bl < len(radii):
                    ax = centers[idx_anchor_bl, 0] - radii[idx_anchor_bl]
                    ay = centers[idx_anchor_bl, 1] - radii[idx_anchor_bl]
                    grad_x[idx_anchor_bl] += 2 * lambda_a * ax
                    grad_y[idx_anchor_bl] += 2 * lambda_a * ay
                    grad_r[idx_anchor_bl] += -2 * lambda_a * (ax + ay)
                if lambda_a > 0 and 0 <= idx_anchor_br < len(radii):
                    ay = centers[idx_anchor_br, 1] - radii[idx_anchor_br]
                    grad_y[idx_anchor_br] += 2 * lambda_a * ay
                    grad_r[idx_anchor_br] += -2 * lambda_a * ay

                # Backtracking line search to keep slacks strictly positive and increase f
                step = 1e-1
                success = False
                x_try = centers[:, 0].copy()
                y_try = centers[:, 1].copy()
                r_try = radii.copy()
                for _ in range(25):
                    x_try = centers[:, 0] + step * grad_x
                    y_try = centers[:, 1] + step * grad_y
                    r_try = radii + step * grad_r

                    # Keep within unit square with strict slack via clipping of centers relative to r_try
                    x_try = np.clip(x_try, r_try + epsilon, 1 - r_try - epsilon)
                    y_try = np.clip(y_try, r_try + epsilon, 1 - r_try - epsilon)
                    r_try = np.maximum(r_try, 1e-12)

                    w_try = compute_wall_slacks(np.column_stack((x_try, y_try)), r_try, epsilon)
                    p_try, _ = compute_pair_slacks(np.column_stack((x_try, y_try)), r_try, pair_idx, epsilon)

                    if min_over_walls(w_try) > 0 and (p_try.size == 0 or np.min(p_try) > 0):
                        f_try = float(
                            np.sum(r_try) + mu * (sum_log_walls(w_try) + (np.sum(np.log(p_try)) if p_try.size else 0.0))
                        )
                        if f_try >= f:
                            success = True
                            break
                    step *= 0.5

                if success:
                    centers[:, 0] = x_try
                    centers[:, 1] = y_try
                    radii = r_try
                else:
                    # Unable to improve; make a tiny shrink to reset and continue.
                    radii *= 0.999999
                    radii, centers = enforce_strict_feasibility(centers, radii, epsilon)

                # Lazy constraint augmentation: add near-active pairs
                full_pairs, _ = compute_all_pair_slacks(centers, radii, epsilon)
                # Rebuild pair_idx to include near-active pairs under tau
                pair_idx = augment_lazy_pairs(centers, radii, pair_idx, TAU_NEAR)

                # Adaptive LP cadence updates
                if (it + 1) % lp_period == 0:
                    # compute near-active counts
                    S_pair = int(np.sum(full_pairs < TAU_NEAR))
                    w_all = compute_wall_slacks(centers, radii, epsilon)
                    wall_near = np.minimum.reduce([w_all[0], w_all[1], w_all[2], w_all[3]])
                    S_wall = int(np.sum(wall_near < TAU_NEAR))
                    near_counts_hist.append((S_pair, S_wall))
                    sum_r_now = float(np.sum(radii))
                    # Adjust LP period and perform LP capture if available
                    if HAS_SCIPY:
                        if sum_r_now <= last_sum_r or (len(near_counts_hist) >= 2 and (
                           (near_counts_hist[-1][0] + near_counts_hist[-1][1]) >
                           1.1 * (near_counts_hist[-2][0] + near_counts_hist[-2][1]))):
                            lp_period = max(20, int(0.6 * lp_period))
                        else:
                            lp_period = min(60, int(math.ceil(1.1 * lp_period)))
                        # Fixed-center LP polish at current centers
                        radii_lp = fixed_center_lp(centers, eps_lp=EPS_LP)
                        if np.sum(radii_lp) > np.sum(radii):
                            radii = radii_lp
                            # Reproject centers into [r+ε, 1-r-ε]
                            centers[:, 0] = np.clip(centers[:, 0], radii + epsilon, 1 - radii - epsilon)
                            centers[:, 1] = np.clip(centers[:, 1], radii + epsilon, 1 - radii - epsilon)
                            radii, centers = enforce_strict_feasibility(centers, radii, epsilon)
                        last_sum_r = float(np.sum(radii))

                # Mid-run centered uniform inflation gate + LP capture (one-time)
                if not mid_inflation_done and mu_idx >= max(1, len(MU_SCHEDULE) // 2):
                    sum_r_pre_mid = float(np.sum(radii))
                    c_mid, r_mid = uniform_global_inflation(centers, radii, epsilon)
                    if HAS_SCIPY:
                        r_lp_mid = fixed_center_lp(c_mid, eps_lp=EPS_LP)
                        if float(np.sum(r_lp_mid)) > sum_r_pre_mid:
                            centers, radii = c_mid, r_lp_mid
                            radii, centers = enforce_strict_feasibility(centers, radii, epsilon)
                            pair_idx = build_lazy_pairs(centers, k=K_NEIGHBORS_INIT)
                            mid_inflation_done = True
                    else:
                        if float(np.sum(r_mid)) > sum_r_pre_mid:
                            centers, radii = c_mid, r_mid
                            radii, centers = enforce_strict_feasibility(centers, radii, epsilon)
                            pair_idx = build_lazy_pairs(centers, k=K_NEIGHBORS_INIT)
                            mid_inflation_done = True

        # 6) Uniform global inflation about (0.5,0.5) + LP polish (late monetization of boundary slack)
        centers, radii = uniform_global_inflation(centers, radii, epsilon)
        if HAS_SCIPY:
            radii_lp = fixed_center_lp(centers, eps_lp=EPS_LP)
            if np.sum(radii_lp) > np.sum(radii):
                radii = radii_lp
                centers[:, 0] = np.clip(centers[:, 0], radii + epsilon, 1 - radii - epsilon)
                centers[:, 1] = np.clip(centers[:, 1], radii + epsilon, 1 - radii - epsilon)

        # 7) Endgame LP–mini-barrier–LP sandwich with ε-annealed gate
        if HAS_SCIPY:
            radii_lp1 = fixed_center_lp(centers, eps_lp=EPS_LP)
            if np.sum(radii_lp1) > np.sum(radii):
                radii = radii_lp1

        # Mini-barrier polish with small μ and strict slacks
        for mu in [2e-3, 1e-3, 5e-4]:
            epsilon = max(EPS_FINAL, epsilon * 0.1)
            radii *= SHRINK_ON_EPS_DROP
            radii, centers = enforce_strict_feasibility(centers, radii, epsilon)
            pair_idx = build_lazy_pairs(centers, k=K_NEIGHBORS_INIT)
            for _ in range(60):
                walls = compute_wall_slacks(centers, radii, epsilon)
                pairs, dists = compute_pair_slacks(centers, radii, pair_idx, epsilon)
                f = float(np.sum(radii) + mu * (sum_log_walls(walls) + (np.sum(np.log(pairs)) if pairs.size else 0.0)))
                grad_x, grad_y, grad_r = barrier_gradient(centers, radii, walls, pairs, pair_idx, dists, mu, epsilon)
                step = 5e-2
                success = False
                for _ in range(20):
                    x_try = centers[:, 0] + step * grad_x
                    y_try = centers[:, 1] + step * grad_y
                    r_try = radii + step * grad_r
                    x_try = np.clip(x_try, r_try + epsilon, 1 - r_try - epsilon)
                    y_try = np.clip(y_try, r_try + epsilon, 1 - r_try - epsilon)
                    r_try = np.maximum(r_try, 1e-12)
                    w_try = compute_wall_slacks(np.column_stack((x_try, y_try)), r_try, epsilon)
                    p_try, _ = compute_pair_slacks(np.column_stack((x_try, y_try)), r_try, pair_idx, epsilon)
                    if min_over_walls(w_try) > 0 and (p_try.size == 0 or np.min(p_try) > 0):
                        f_try = float(
                            np.sum(r_try) + mu * (sum_log_walls(w_try) + (np.sum(np.log(p_try)) if p_try.size else 0.0))
                        )
                        if f_try >= f:
                            centers[:, 0] = x_try
                            centers[:, 1] = y_try
                            radii = r_try
                            success = True
                            break
                    step *= 0.5
                if not success:
                    break  # stop mini-polish stage if not improving

        if HAS_SCIPY:
            radii_lp2 = fixed_center_lp(centers, eps_lp=EPS_LP)
            if np.sum(radii_lp2) > np.sum(radii):
                radii = radii_lp2

        # 8) Strict-feasibility polish and micro-overlap removal
        radii *= 0.999999
        radii, centers = enforce_strict_feasibility(centers, radii, EPS_FINAL)
        # Micro overlap removal
        for _ in range(MICRO_OVERLAP_ITERS):
            s_all, _ = compute_all_pair_slacks(centers, radii, EPS_FINAL)
            # Only consider i<j to avoid double shrinking the same pair
            n = len(radii)
            violation_pairs = []
            for i in range(n):
                for j in range(i + 1, n):
                    if s_all[i, j] < 0:
                        violation_pairs.append((i, j, -s_all[i, j]))
            if not violation_pairs:
                break
            for (i, j, overlap) in violation_pairs:
                if radii[i] >= radii[j]:
                    radii[i] = max(1e-12, radii[i] - 0.5 * overlap)
                else:
                    radii[j] = max(1e-12, radii[j] - 0.5 * overlap)
            # reproject centers strictly
            centers[:, 0] = np.clip(centers[:, 0], radii + EPS_FINAL, 1 - radii - EPS_FINAL)
            centers[:, 1] = np.clip(centers[:, 1], radii + EPS_FINAL, 1 - radii - EPS_FINAL)

        sum_r_final = float(np.sum(radii))
        if sum_r_final > best_sum_r:
            best_sum_r = sum_r_final
            best_circles = np.column_stack((centers, radii))

    if best_circles is None:
        # Fallback in unlikely case preselection list is empty
        centers = np.array([[((c + 0.5) / 5), ((r + 0.5) / 5)] for r in range(5) for c in range(5)][:num_circles])
        m = wall_clearances(centers)
        radii = 0.45 * m
        best_circles = np.column_stack((centers, radii))

    return best_circles


def generate_hex_seeds_21(rng: np.random.Generator) -> List[np.ndarray]:
    """Generate tri-hexagonal seeds with row patterns summing to 21."""
    patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
        [5, 4, 5, 4, 3],
        [5, 5, 4, 4, 3],
        [4, 5, 5, 4, 3],
    ]
    s_list = [0.2 * k for k in [0.96, 0.98, 1.00, 1.02, 1.04]]
    seeds: List[np.ndarray] = []
    for pattern in patterns:
        rows = len(pattern)
        for s in s_list:
            v = s * math.sqrt(3) / 2.0
            # Build row-by-row positions with alternating shift of 0.5*s
            coords = []
            y_vals = [i * v for i in range(rows)]
            # Compute combined bounding box if x0=0
            min_x = float("inf")
            max_x = -float("inf")
            for i, count in enumerate(pattern):
                shift = (0.5 * s) if (i % 2 == 1) else 0.0
                xs = [shift + j * s for j in range(count)]
                if xs:
                    min_x = min(min_x, min(xs))
                    max_x = max(max_x, max(xs))
            width = max_x - min_x if max_x > min_x else s
            height = (rows - 1) * v if rows > 1 else v
            # Place inside unit square by centering and scaling if needed
            # Target margins to allow growth: margin ≈ 0.07
            margin = 0.07
            if width + 2 * margin > 1.0:
                scale = (1.0 - 2 * margin) / max(width, 1e-12)
                s_eff = s * scale
                v_eff = v * scale
            else:
                s_eff = s
                v_eff = v
            # Recalculate widths with s_eff
            min_x = float("inf")
            max_x = -float("inf")
            for i, count in enumerate(pattern):
                shift = (0.5 * s_eff) if (i % 2 == 1) else 0.0
                xs = [shift + j * s_eff for j in range(count)]
                if xs:
                    min_x = min(min_x, min(xs))
                    max_x = max(max_x, max(xs))
            width = max_x - min_x if max_x > min_x else s_eff
            height = (rows - 1) * v_eff if rows > 1 else v_eff
            x0 = 0.5 - 0.5 * width
            y0 = 0.5 - 0.5 * height
            for i, count in enumerate(pattern):
                shift = (0.5 * s_eff) if (i % 2 == 1) else 0.0
                xs = [x0 + shift + j * s_eff for j in range(count)]
                y = y0 + i * v_eff
                for x in xs:
                    coords.append([x, y])
            coords_arr = np.array(coords, dtype=float)
            # Clip to slightly inside unit square
            coords_arr = np.clip(coords_arr, 1e-3, 1 - 1e-3)
            if coords_arr.shape[0] == 21:
                seeds.append(coords_arr)
    return seeds


def generate_two_scale_seed_21(rng: np.random.Generator) -> List[np.ndarray]:
    """Generate optional two-scale seed: a 5x4 base grid plus one interstitial via a simple clearance proxy."""
    seeds = []
    # 5x4 base grid (20 points)
    grid_points = np.array([[(c + 0.5) / 5, (r + 0.5) / 4] for r in range(4) for c in range(5)], dtype=float)
    # Candidate interstitial points: sample around square center
    candidates = []
    for dx in [-0.05, 0.0, 0.05]:
        for dy in [-0.05, 0.0, 0.05]:
            candidates.append([0.5 + dx, 0.5 + dy])
    candidates = np.array(candidates, dtype=float)
    # Choose interstitial by maximizing minimal distance to grid_points (clearance proxy)
    best_idx = None
    best_clear = -1.0
    for k in range(len(candidates)):
        dmin = np.min(np.linalg.norm(grid_points - candidates[k], axis=1))
        if dmin > best_clear:
            best_clear = dmin
            best_idx = k
    if best_idx is None:
        return seeds
    centers21 = np.vstack([grid_points, candidates[best_idx]])
    centers21 = np.clip(centers21, 1e-3, 1 - 1e-3)
    seeds.append(centers21)
    return seeds


def wall_clearances(centers: np.ndarray) -> np.ndarray:
    """Return distance to the nearest wall for each center."""
    x = centers[:, 0]
    y = centers[:, 1]
    return np.minimum.reduce([x, 1 - x, y, 1 - y])


def enforce_strict_feasibility(centers: np.ndarray, radii: np.ndarray, epsilon: float) -> Tuple[np.ndarray, np.ndarray]:
    """Project to strict feasibility: r>0; centers within [r+ε, 1-r-ε]."""
    r = np.maximum(radii, 1e-12)
    x = np.clip(centers[:, 0], r + epsilon, 1 - r - epsilon)
    y = np.clip(centers[:, 1], r + epsilon, 1 - r - epsilon)
    return r, np.column_stack((x, y))


def build_lazy_pairs(centers: np.ndarray, k: int = 6) -> List[Tuple[int, int]]:
    """Build initial lazy adjacency set via k-nearest neighbors for each point."""
    n = centers.shape[0]
    pair_set = set()
    dmat = np.linalg.norm(centers[None, :, :] - centers[:, None, :], axis=2)
    for i in range(n):
        idxs = np.argsort(dmat[i])
        for j in idxs[1 : min(n, 1 + k)]:
            a, b = (i, j) if i < j else (j, i)
            if a != b:
                pair_set.add((a, b))
    return sorted(list(pair_set))


def augment_lazy_pairs(centers: np.ndarray, radii: np.ndarray, pair_idx: List[Tuple[int, int]], tau: float) -> List[Tuple[int, int]]:
    """Augment lazy adjacency set with near-active pairs (s_ij < tau)."""
    n = centers.shape[0]
    s_all, _ = compute_all_pair_slacks(centers, radii, epsilon=1e-12)
    new_pairs = set(pair_idx)
    for i in range(n):
        for j in range(i + 1, n):
            if s_all[i, j] < tau:
                new_pairs.add((i, j))
    return sorted(list(new_pairs))


def compute_wall_slacks(centers: np.ndarray, radii: np.ndarray, epsilon: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute wall slacks s_xL, s_xR, s_yB, s_yT arrays of length n."""
    x = centers[:, 0]
    y = centers[:, 1]
    r = radii
    s_xL = x - r - epsilon
    s_xR = 1 - x - r - epsilon
    s_yB = y - r - epsilon
    s_yT = 1 - y - r - epsilon
    return s_xL, s_xR, s_yB, s_yT


def min_over_walls(walls: Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]) -> float:
    """Return the minimal slack across all wall constraints."""
    return float(min(np.min(walls[0]), np.min(walls[1]), np.min(walls[2]), np.min(walls[3])))


def sum_log_walls(walls: Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]) -> float:
    """Return sum of logs of all wall slacks."""
    return float(np.sum(np.log(walls[0])) + np.sum(np.log(walls[1])) + np.sum(np.log(walls[2])) + np.sum(np.log(walls[3])))


def compute_pair_slacks(
    centers: np.ndarray, radii: np.ndarray, pair_idx: List[Tuple[int, int]], epsilon: float
) -> Tuple[np.ndarray, np.ndarray]:
    """Compute slacks s_ij and distances d_ij for pairs in pair_idx; return arrays aligned with pair_idx order."""
    if not pair_idx:
        return np.array([]), np.array([])
    dists = np.array([math.dist(centers[i], centers[j]) for (i, j) in pair_idx], dtype=float)
    s = dists - (radii[[i for (i, _) in pair_idx]] + radii[[j for (_, j) in pair_idx]]) - epsilon
    return s, dists


def compute_all_pair_slacks(centers: np.ndarray, radii: np.ndarray, epsilon: float) -> Tuple[np.ndarray, np.ndarray]:
    """Compute slacks for all i<j pairs as a symmetric matrix s[i,j], and distance matrix d."""
    n = centers.shape[0]
    dmat = np.linalg.norm(centers[None, :, :] - centers[:, None, :], axis=2)
    # s_ij = d_ij - r_i - r_j - epsilon; fill symmetric matrix with s_ij (upper triangle)
    rsum = radii[:, None] + radii[None, :]
    s = dmat - rsum - epsilon
    # Set diagonal to +inf to avoid interpreting as constraints
    np.fill_diagonal(s, np.inf)
    return s, dmat


def barrier_gradient(
    centers: np.ndarray,
    radii: np.ndarray,
    walls: Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    pairs: np.ndarray,
    pair_idx: List[Tuple[int, int]],
    dists: np.ndarray,
    mu: float,
    epsilon: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute gradient of f = Σr + μ Σ log(slack)."""
    n = centers.shape[0]
    x = centers[:, 0]
    y = centers[:, 1]
    r = radii

    s_xL, s_xR, s_yB, s_yT = walls

    # Gradients for walls
    grad_x = mu * ((1.0 / s_xL) * 1.0 + (1.0 / s_xR) * (-1.0))
    grad_y = mu * ((1.0 / s_yB) * 1.0 + (1.0 / s_yT) * (-1.0))
    grad_r = np.ones(n, dtype=float) + mu * (
        (1.0 / s_xL) * (-1.0) + (1.0 / s_xR) * (-1.0) + (1.0 / s_yB) * (-1.0) + (1.0 / s_yT) * (-1.0)
    )

    # Pair contributions
    if pair_idx and pairs.size:
        inv_s = 1.0 / pairs
        # Avoid divide-by-zero or non-positive slacks
        inv_s = np.where(pairs > 0, inv_s, 0.0)
        # Dist gradients
        for k, (i, j) in enumerate(pair_idx):
            dij = dists[k]
            if dij <= 0:
                continue
            dx = (x[i] - x[j]) / dij
            dy = (y[i] - y[j]) / dij
            g = mu * inv_s[k]
            grad_x[i] += g * dx
            grad_y[i] += g * dy
            grad_x[j] -= g * dx
            grad_y[j] -= g * dy
            grad_r[i] += g * (-1.0)
            grad_r[j] += g * (-1.0)

    return grad_x, grad_y, grad_r


def fixed_center_lp(centers: np.ndarray, eps_lp: float = 5e-10) -> np.ndarray:
    """
    Solve the fixed-center LP:
    maximize Σ r_i
    subject to 0 ≤ r_i ≤ m_i (wall clearance), and r_i + r_j ≤ d_ij - ε_lp for all i<j.
    """
    n = centers.shape[0]
    m = wall_clearances(centers)
    if not HAS_SCIPY:
        # Fallback: simple feasible heuristic: start from wall clearances and shrink by pair constraints
        r = m.copy()
        # Apply pair constraints greedily
        for i in range(n):
            for j in range(i + 1, n):
                dij = math.dist(centers[i], centers[j])
                ub = max(0.0, dij - eps_lp)
                if r[i] + r[j] > ub:
                    # shrink larger one
                    delta = (r[i] + r[j] - ub)
                    if r[i] >= r[j]:
                        r[i] = max(0.0, r[i] - delta * 0.5)
                    else:
                        r[j] = max(0.0, r[j] - delta * 0.5)
        r = np.clip(r, 0.0, m)
        return r

    # Build LP matrices
    # Objective: maximize Σr -> minimize -Σr
    c = -np.ones(n, dtype=float)
    A_ub_rows = []
    b_ub_vals = []
    # Pairwise constraints r_i + r_j ≤ d_ij - ε_lp
    for i in range(n):
        for j in range(i + 1, n):
            dij = math.dist(centers[i], centers[j])
            ub = dij - eps_lp
            row = np.zeros(n, dtype=float)
            row[i] = 1.0
            row[j] = 1.0
            A_ub_rows.append(row)
            b_ub_vals.append(ub)
    # Wall constraints r_i ≤ m_i
    for i in range(n):
        row = np.zeros(n, dtype=float)
        row[i] = 1.0
        A_ub_rows.append(row)
        b_ub_vals.append(m[i])
    if A_ub_rows:
        A_ub = np.vstack(A_ub_rows)
        b_ub = np.array(b_ub_vals, dtype=float)
    else:
        A_ub = None
        b_ub = None
    # Bounds: r_i ≥ 0
    bounds = [(0.0, None) for _ in range(n)]
    res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if res.success and res.x is not None:
        r = np.array(res.x, dtype=float)
        r = np.maximum(r, 0.0)
        r = np.minimum(r, m)  # ensure within walls
        return r
    # Fallback if LP fails
    return np.zeros(n, dtype=float)


def uniform_global_inflation(centers: np.ndarray, radii: np.ndarray, epsilon: float) -> Tuple[np.ndarray, np.ndarray]:
    """
    Uniformly scale centers and radii about (0.5, 0.5) until a wall constraint becomes tight.
    Non-overlap is preserved by scaling, pairwise slack increases; walls may limit scaling.

    We compute the maximal λ>1 such that all four wall slacks remain strictly positive:
      L(λ) = 0.5 - ε + λ*(x - 0.5 - r) >= 0
      R(λ) = 0.5 - ε - λ*(x - 0.5 + r) >= 0
      B(λ) = 0.5 - ε + λ*(y - 0.5 - r) >= 0
      T(λ) = 0.5 - ε - λ*(y - 0.5 + r) >= 0
    """
    x = centers[:, 0]
    y = centers[:, 1]
    r = radii

    def bound_lambda(coeff: np.ndarray) -> float:
        # For constraints base + λ*coeff >= 0, base = 0.5 - ε
        base = 0.5 - epsilon
        denom = -coeff  # if denom>0 => coeff<0 => upper bound λ≤base/denom
        mask = denom > 0
        if not np.any(mask):
            return float("inf")
        vals = base / denom[mask]
        return float(np.min(vals))

    aL = x - 0.5 - r
    aR = x - 0.5 + r
    aB = y - 0.5 - r
    aT = y - 0.5 + r

    lam_bounds = [
        bound_lambda(aL),
        bound_lambda(aR),
        bound_lambda(aB),
        bound_lambda(aT),
    ]

    lam_max = min(lam_bounds)
    lam_max = max(1.0, lam_max)  # we only scale up
    lam = lam_max * 0.999  # keep strict slack

    if lam <= 1.0001:
        # Not enough slack to inflate
        return centers, radii

    x_new = 0.5 + lam * (x - 0.5)
    y_new = 0.5 + lam * (y - 0.5)
    r_new = lam * r
    # Strict feasibility clip
    x_new = np.clip(x_new, r_new + epsilon, 1 - r_new - epsilon)
    y_new = np.clip(y_new, r_new + epsilon, 1 - r_new - epsilon)
    return np.column_stack((x_new, y_new)), r_new


# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```
