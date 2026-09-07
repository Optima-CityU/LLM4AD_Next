Successful 21-circle packing under w + h = 2 with acceptance-gated barrier+LP pipeline; measured sum_radii = 2.29463034731723 and validity = 1.0.

- TriHex-AR++ Barrier–Trust with LP Preselect, Dual Inflations, and LP–Mini-Barrier–LP Polish: Interleaving an interior log-barrier ascent for center moves with periodic fixed-center LP inflations, under acceptance gates requiring strict feasibility and a strictly increasing Σr, was key to success on this instance (sum_radii = 2.29463034731723, validity = 1.0); this tandem rapidly converts created slack into radii while preserving robustness. Dual uniform inflations about the box center, each followed by LP, reliably harvested boundary slack without violating non-overlap, and the aspect-ratio sweep constrained by w + h = 2 captured additional gains beyond the square baseline. For similar non-overlap circle-packing tasks with a rectangular perimeter budget, reuse the pipeline: LP-only preselection of hex-staggered seeds, barrier-guided center relocation with periodic LP, one or two uniform inflations plus LP, and a short LP–mini-barrier–LP polish, applying acceptance gates after every major step.

```python
#!/usr/bin/env python3
"""Candidate for packing 21 circles under perimeter-4 rectangle constraint.

We implement a multi-start hex-stagger seeding, fixed-center LP inflation,
trust-region growth with feasibility-aware moves, uniform global inflation,
a light interior-barrier ascent on centers with periodic LP inflations, and
a short aspect-ratio sweep that preserves w + h = 2.

All coordinates are produced inside an axis-aligned rectangle [0, w] x [0, h]
with w + h = 2 (start with w = h = 1) so the minimum circumscribing rectangle
perimeter is at most 4. We maintain a strict clearance epsilon > 0 in all
constraints and finally apply a tiny global shrink for strict feasibility.

No external outputs other than the 21 rows [x, y, r].
"""

import json
import math
from typing import List, Tuple

import numpy as np

# Try to import SciPy linprog; we will gracefully fall back if unavailable.
try:
    from scipy.optimize import linprog as _linprog  # type: ignore
    _HAVE_SCIPY = True
except Exception:
    _HAVE_SCIPY = False


# EVOLVE_START
def construct_packing(num_circles: int = 21):
    """Construct a feasible packing of 21 circles that fits in an axis-aligned
    rectangle with perimeter ≤ 4 (we use w + h = 2), approximately maximizing
    the sum of radii via a hybrid LP + local growth + interior-barrier strategy.

    Returns:
        np.ndarray shape (21, 3) with rows [x, y, r].
    """

    # ----------------------------
    # Tunable global hyperparameters
    # ----------------------------
    EPS = 1e-6           # Strict clearance in all linear constraints
    FINAL_SHRINK = 1e-9  # Tiny safety shrink on radii at the very end
    RNG = np.random.default_rng(0)  # Deterministic jitter

    # Rectangle dimensions (start with unit square; maintain w + h = 2)
    w, h = 1.0, 1.0

    # Patterns of row counts that sum to 21 (hex-stagger friendly)
    patterns = [
        [5, 4, 5, 4, 3],
        [5, 5, 4, 4, 3],
        [4, 5, 5, 4, 3],
        # "four-shift" variants with one 5-count row among 4-count rows
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
    ]

    # Seed jitter amplitude relative to local spacing (we'll scale by s)
    base_jitter = 0.002

    # Spacing sweep around a typical hex-lattice gap ~0.2
    s_base = 0.2
    s_factors = [0.96, 0.98, 1.00, 1.02, 1.04]
    spacings = [s_base * f for f in s_factors]

    # Number of jittered seeds per (pattern, spacing)
    seeds_per_ps = 2

    # Survivors to carry to local optimization (across all seeds)
    topK = 12

    # Local trust-region iterations
    local_iters = 32

    # Barrier ascent micro-iterations per μ stage
    barrier_steps = 6
    # μ schedule for interior-barrier ascent (small to keep moves conservative)
    mu_schedule = [0.02, 0.01]

    # "Mini polish" iterations for a final sandwich using tiny moves
    barrier_iters = 10

    # Aspect-ratio sweep alphas (w = 1 + α, h = 1 − α)
    aspect_alphas = [0.02, -0.02, 0.04, -0.04, 0.06, -0.06]

    # ----------------------------
    # Helper functions
    # ----------------------------

    def compute_caps(centers: np.ndarray, w_: float, h_: float, eps: float) -> np.ndarray:
        """Compute boundary caps for radii given fixed centers: r_i ≤ min distance to box edges minus eps."""
        x = centers[:, 0]
        y = centers[:, 1]
        caps = np.minimum.reduce([x - 0.0, y - 0.0, w_ - x, h_ - y])
        caps = np.maximum(caps - eps, 0.0)
        return caps

    def pairwise_indices(n: int) -> Tuple[np.ndarray, np.ndarray]:
        """Upper-triangular index arrays for pairs (i<j)."""
        I, J = np.triu_indices(n, k=1)
        return I, J

    def fixed_center_lp(centers: np.ndarray, w_: float, h_: float, eps: float) -> np.ndarray:
        """Solve the LP maximizing sum r with fixed centers.
        Constraints:
          0 ≤ r_i ≤ cap_i
          r_i + r_j ≤ d_ij − eps
        """
        n = centers.shape[0]
        caps = compute_caps(centers, w_, h_, eps)

        I, J = pairwise_indices(n)
        diffs = centers[I] - centers[J]
        dists = np.linalg.norm(diffs, axis=1)

        # Build A_ub r ≤ b_ub
        A_rows = []
        b_rows = []

        # r_i ≤ cap_i
        for i in range(n):
            row = np.zeros(n, dtype=float)
            row[i] = 1.0
            A_rows.append(row)
            b_rows.append(caps[i])

        # Pairwise: r_i + r_j ≤ d_ij − eps
        for k in range(len(I)):
            i, j = I[k], J[k]
            rhs = dists[k] - eps
            row = np.zeros(n, dtype=float)
            row[i] = 1.0
            row[j] = 1.0
            A_rows.append(row)
            b_rows.append(rhs)

        A_ub = np.vstack(A_rows) if A_rows else None
        b_ub = np.array(b_rows, dtype=float) if b_rows else None
        c = -np.ones(n, dtype=float)  # maximize sum r -> minimize -sum r
        bounds = [(0.0, None)] * n

        if _HAVE_SCIPY:
            res = _linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
            if res.success and res.x is not None:
                r = np.array(res.x, dtype=float)
                # Clip tiny negative numerical noise
                r = np.maximum(r, 0.0)
                # Also enforce caps to be safe
                r = np.minimum(r, caps)
                return r
            # fall back if HiGHS failed

        # Fallback greedy allocator
        return fixed_center_lp_greedy(centers, w_, h_, eps)

    def fixed_center_lp_greedy(centers: np.ndarray, w_: float, h_: float, eps: float) -> np.ndarray:
        """Greedy/coordinate-ascent fallback for fixed-center inflation.
        Iteratively sets r_i = min(cap_i, min_j d_ij - eps - r_j, 0 if min negative).
        Multiple passes to converge.
        """
        n = centers.shape[0]
        caps = compute_caps(centers, w_, h_, eps)
        I, J = pairwise_indices(n)
        diffs = centers[I] - centers[J]
        dists = np.linalg.norm(diffs, axis=1)

        # Precompute adjacency list for efficiency
        neigh = [[] for _ in range(n)]
        for k in range(len(I)):
            i, j = I[k], J[k]
            neigh[i].append((j, dists[k]))
            neigh[j].append((i, dists[k]))

        r = np.zeros(n, dtype=float)
        # Perform several sweeps
        for _ in range(5 * n + 15):
            order = np.arange(n)
            # Deterministic permutation to avoid pathological cycles
            RNG.shuffle(order)
            for i in order:
                # Allow as large as boundary cap and all pairwise sums
                ri_max = caps[i]
                for (j, dij) in neigh[i]:
                    ri_max = min(ri_max, max(0.0, dij - eps - r[j]))
                    if ri_max <= 0.0:
                        break
                r[i] = ri_max
        return r

    def compute_sum_r(r: np.ndarray) -> float:
        return float(np.sum(r))

    def trust_region_move(centers: np.ndarray, r: np.ndarray, w_: float, h_: float, eps: float,
                          trust: float) -> np.ndarray:
        """Compute a small center displacement that pushes away from near neighbors and boundaries.
        We respect a per-point trust radius cap on displacement.
        """
        n = centers.shape[0]
        disp = np.zeros_like(centers)

        # Pairwise repulsion
        I, J = pairwise_indices(n)
        delta = centers[I] - centers[J]
        d = np.linalg.norm(delta, axis=1)
        # Avoid zero divide
        d = np.maximum(d, 1e-12)
        # Slack s = d - r_i - r_j - eps
        s = d - (r[I] + r[J]) - eps
        # Force magnitude inversely proportional to slack
        s0 = 1e-3
        k_pair = 0.03  # strength
        mag = k_pair / (s + s0)
        # Unit directions
        u = delta / d[:, None]
        # Apply symmetric forces
        for idx in range(len(I)):
            i, j = I[idx], J[idx]
            f = mag[idx]
            vec = u[idx] * f
            disp[i] += vec
            disp[j] -= vec

        # Boundary repulsion
        # slack to left/right/top/bottom: push away inversely proportional to slack
        k_bound = 0.02
        s0b = 1e-3
        x = centers[:, 0]
        y = centers[:, 1]
        sL = (x - r - eps)
        sR = (w_ - (x + r) - eps)
        sB = (y - r - eps)
        sT = (h_ - (y + r) - eps)

        disp[:, 0] += k_bound / (sL + s0b)  # push right
        disp[:, 0] -= k_bound / (sR + s0b)  # push left
        disp[:, 1] += k_bound / (sB + s0b)  # push up
        disp[:, 1] -= k_bound / (sT + s0b)  # push down

        # Limit displacement per point to trust radius
        norms = np.linalg.norm(disp, axis=1)
        scale = np.ones(n, dtype=float)
        mask = norms > trust
        scale[mask] = trust / norms[mask]
        disp *= scale[:, None]

        # Apply move
        new_centers = centers + disp

        # Keep inside box roughly using current radii; final LP will readjust radii
        new_centers[:, 0] = np.clip(new_centers[:, 0], 0.0 + eps, w_ - eps)
        new_centers[:, 1] = np.clip(new_centers[:, 1], 0.0 + eps, h_ - eps)
        return new_centers

    def uniform_inflate_about_center(centers: np.ndarray, r: np.ndarray,
                                     w_: float, h_: float, eps: float) -> Tuple[np.ndarray, np.ndarray]:
        """Uniformly scale (x, y, r) about the rectangle center to saturate a boundary if possible.
        This preserves pairwise non-overlap since distances and radii scale by the same factor.
        We compute the maximum s ≥ 1 such that all boundary constraints are satisfied with eps.
        """
        cx, cy = w_ / 2.0, h_ / 2.0
        x = centers[:, 0]
        y = centers[:, 1]
        s_max = float('inf')

        # For each circle, derive upper bounds on s from each boundary
        # Right boundary: cx + s*(x - cx) + s*r ≤ w - eps => s*((x - cx) + r) ≤ w/2 - eps
        aR = (x - cx) + r
        mask = aR > 0
        if np.any(mask):
            bounds = (w_ / 2.0 - eps) / aR[mask]
            s_max = min(s_max, float(np.min(bounds)))

        # Left boundary: cx + s*(x - cx) - s*r ≥ eps => s*((x - cx) - r) ≥ eps - cx = -(w/2 - eps)
        # If ((x - cx) - r) < 0, increasing s lowers LHS → upper bound: s ≤ (w/2 - eps)/(-((x - cx) - r))
        bL = (x - cx) - r
        mask = bL < 0
        if np.any(mask):
            bounds = (w_ / 2.0 - eps) / (-bL[mask])
            s_max = min(s_max, float(np.min(bounds)))

        # Top boundary: cy + s*(y - cy) + s*r ≤ h - eps => s*((y - cy) + r) ≤ h/2 - eps
        aT = (y - cy) + r
        mask = aT > 0
        if np.any(mask):
            bounds = (h_ / 2.0 - eps) / aT[mask]
            s_max = min(s_max, float(np.min(bounds)))

        # Bottom boundary: cy + s*(y - cy) - s*r ≥ eps => s*((y - cy) - r) ≥ eps - cy = -(h/2 - eps)
        # If ((y - cy) - r) < 0 -> upper bound: s ≤ (h/2 - eps)/(-((y - cy) - r))
        bB = (y - cy) - r
        mask = bB < 0
        if np.any(mask):
            bounds = (h_ / 2.0 - eps) / (-bB[mask])
            s_max = min(s_max, float(np.min(bounds)))

        if not np.isfinite(s_max):
            s_max = 1.0
        # Ensure we do not shrink; only inflate if s_max > 1
        if s_max <= 1.0000001:
            return centers, r

        s = max(1.0, s_max * 0.999)  # leave a tiny gap
        # Scale
        new_centers = np.empty_like(centers)
        new_centers[:, 0] = cx + s * (x - cx)
        new_centers[:, 1] = cy + s * (y - cy)
        new_r = r * s
        # Final safety clip (just in case numerics)
        new_centers[:, 0] = np.clip(new_centers[:, 0], 0.0 + eps, w_ - eps)
        new_centers[:, 1] = np.clip(new_centers[:, 1], 0.0 + eps, h_ - eps)
        return new_centers, new_r

    def slacks_and_gradients(centers: np.ndarray, r: np.ndarray, w_: float, h_: float, eps: float
                             ) -> Tuple[dict, np.ndarray]:
        """Compute slacks for pairwise and boundary constraints and the gradient of
        the log-barrier sum wrt centers.

        Returns:
          slacks: dict with arrays for boundary and pair slacks
          grad: (n,2) gradient for centers of Σ log(slack) (no μ factor applied)
        """
        n = centers.shape[0]
        grad = np.zeros_like(centers)

        x = centers[:, 0]
        y = centers[:, 1]

        # Boundary slacks
        sL = x - r - eps
        sR = (w_ - (x + r) - eps)
        sB = y - r - eps
        sT = (h_ - (y + r) - eps)

        # Clamp denominators to avoid blow-up when extremely small
        s_floor = 1e-9
        inv_sL = 1.0 / np.maximum(sL, s_floor)
        inv_sR = 1.0 / np.maximum(sR, s_floor)
        inv_sB = 1.0 / np.maximum(sB, s_floor)
        inv_sT = 1.0 / np.maximum(sT, s_floor)

        # Boundary gradient contributions
        grad[:, 0] += inv_sL  # from left slack x - r - eps
        grad[:, 0] -= inv_sR  # from right slack w - (x + r) - eps
        grad[:, 1] += inv_sB  # from bottom slack y - r - eps
        grad[:, 1] -= inv_sT  # from top slack h - (y + r) - eps

        # Pairwise slacks and gradients
        I, J = pairwise_indices(n)
        delta = centers[I] - centers[J]
        d = np.linalg.norm(delta, axis=1)
        d = np.maximum(d, 1e-12)
        s_pairs = d - (r[I] + r[J]) - eps
        inv_s_pairs = 1.0 / np.maximum(s_pairs, s_floor)
        # Unit vectors u_ij pointing from j to i
        u = delta / d[:, None]
        # Each pair contributes inv_s * u to i and -inv_s * u to j
        for idx in range(len(I)):
            i, j = I[idx], J[idx]
            wgt = inv_s_pairs[idx]
            vec = wgt * u[idx]
            grad[i] += vec
            grad[j] -= vec

        slacks = {
            "sL": sL, "sR": sR, "sB": sB, "sT": sT,
            "pairs": s_pairs
        }
        return slacks, grad

    def barrier_ascent(centers: np.ndarray, r: np.ndarray, w_: float, h_: float,
                       mu: float, steps: int, base_sum: float
                       ) -> Tuple[np.ndarray, np.ndarray, float]:
        """Perform a few interior-barrier ascent steps on centers:
        maximize Σ log(slack) with a small μ weight, interleaving exact fixed-center LP
        to reallocate radii. Accept only if Σ r increases (monotone gate).
        """
        best_centers = centers.copy()
        best_r = r.copy()
        best_sum = base_sum

        # Initial gradient-based step size and per-point trust bound
        eta0 = 0.05
        max_move = 0.03

        for _ in range(steps):
            sl, grad = slacks_and_gradients(best_centers, best_r, w_, h_, EPS)
            # Scale gradient by μ and limit per-point norm
            g = mu * grad
            norms = np.linalg.norm(g, axis=1)
            scale = np.ones_like(norms)
            mask = norms > 0
            scale[mask] = np.minimum(1.0, max_move / norms[mask])
            g *= scale[:, None]

            # Backtracking line search to maintain strict feasibility and improvement
            eta = eta0
            improved = False
            for _bt in range(8):
                trial_centers = best_centers + eta * g
                # Check strict boundary feasibility with current radii
                x = trial_centers[:, 0]
                y = trial_centers[:, 1]
                # Enforce inside box (soft clip inside open set)
                # We'll clip to [eps, w-eps] which preserves strictness with current r as long as caps allow
                trial_centers[:, 0] = np.clip(x, 0.0 + EPS, w_ - EPS)
                trial_centers[:, 1] = np.clip(y, 0.0 + EPS, h_ - EPS)

                # Allocate radii exactly for these centers
                trial_r = fixed_center_lp(trial_centers, w_, h_, EPS)
                trial_sum = compute_sum_r(trial_r)
                if trial_sum > best_sum + 1e-12:
                    best_centers, best_r, best_sum = trial_centers, trial_r, trial_sum
                    improved = True
                    break
                eta *= 0.5  # shrink step
            if not improved:
                # If no improvement at this stage, reduce eta0 slightly for subsequent attempts
                eta0 *= 0.7

        return best_centers, best_r, best_sum

    def lp_then_local(centers: np.ndarray, w_: float, h_: float) -> Tuple[np.ndarray, np.ndarray, float]:
        """Run LP bootstrap followed by a sequence:
           - trust-region repulsive moves with LP reallocation
           - interior-barrier ascent with periodic LP
           - dual uniform inflations with LP polishes
           - a short sandwich polish
        """
        r = fixed_center_lp(centers, w_, h_, EPS)
        best_centers = centers.copy()
        best_r = r.copy()
        best_sum = compute_sum_r(r)

        # Trust-region local moves with acceptance gate and adaptive radius
        trust = 0.02  # initial trust radius
        for _ in range(local_iters):
            # Move centers slightly to unlock slack
            new_centers = trust_region_move(best_centers, best_r, w_, h_, EPS, trust)
            new_r = fixed_center_lp(new_centers, w_, h_, EPS)
            new_sum = compute_sum_r(new_r)
            if new_sum > best_sum + 1e-12:
                best_centers, best_r, best_sum = new_centers, new_r, new_sum
                trust = min(trust * 1.15, 0.06)  # expand trust slightly
            else:
                trust = max(trust * 0.7, 0.005)  # contract and try smaller moves

        # Interior-barrier ascent with periodic LP to remove structured slack
        for mu in mu_schedule:
            bcent, brad, bsum = barrier_ascent(best_centers, best_r, w_, h_, mu, barrier_steps, best_sum)
            if bsum > best_sum + 1e-12:
                best_centers, best_r, best_sum = bcent, brad, bsum

        # Dual uniform inflations with LP polishes (convert boundary slack to radii)
        infl_centers, infl_r = uniform_inflate_about_center(best_centers, best_r, w_, h_, EPS)
        infl_r = fixed_center_lp(infl_centers, w_, h_, EPS)
        infl_sum = compute_sum_r(infl_r)
        if infl_sum > best_sum + 1e-12:
            best_centers, best_r, best_sum = infl_centers, infl_r, infl_sum

        # Try a second inflation pass; sometimes becomes feasible after first LP
        infl_centers2, infl_r2 = uniform_inflate_about_center(best_centers, best_r, w_, h_, EPS)
        infl_r2 = fixed_center_lp(infl_centers2, w_, h_, EPS)
        infl_sum2 = compute_sum_r(infl_r2)
        if infl_sum2 > best_sum + 1e-12:
            best_centers, best_r, best_sum = infl_centers2, infl_r2, infl_sum2

        # LP–mini-barrier–LP sandwich polish: a few tiny force-based moves, then LP.
        trust_small = 0.01
        temp_centers = best_centers.copy()
        temp_r = best_r.copy()
        for _ in range(barrier_iters):
            temp_centers = trust_region_move(temp_centers, temp_r, w_, h_, EPS, trust_small)
            temp_r = fixed_center_lp(temp_centers, w_, h_, EPS)
        temp_sum = compute_sum_r(temp_r)
        if temp_sum > best_sum + 1e-12:
            best_centers, best_r, best_sum = temp_centers, temp_r, temp_sum

        return best_centers, best_r, best_sum

    def seed_from_pattern_hex(pattern: List[int], w_: float, h_: float,
                              s: float, jitter_scale: float) -> np.ndarray:
        """Create centers from row-count pattern using a true triangular lattice:
        - Horizontal spacing s, vertical spacing v = s*sqrt(3)/2
        - Alternate rows are staggered by s/2 horizontally
        - Rows are centered vertically about h_/2
        - Each row's points are centered horizontally about w_/2

        We then add small jitter proportional to s to break symmetry.
        """
        k = len(pattern)
        v = s * (math.sqrt(3.0) / 2.0)
        cx, cy = w_ / 2.0, h_ / 2.0

        # Compute vertical row coordinates centered at cy
        # y_j = cy + (j - (k-1)/2) * v, for j = 0..k-1
        y_coords = np.array([cy + (j - (k - 1) * 0.5) * v for j in range(k)], dtype=float)

        centers_list: List[Tuple[float, float]] = []
        for row_idx, count in enumerate(pattern):
            # Row horizontal positions: x_i = cx + (i - (count-1)/2) * s + shift
            shift = (s / 2.0) if (row_idx % 2 == 1) else 0.0
            for i in range(count):
                xi = cx + (i - (count - 1) * 0.5) * s + shift
                yi = y_coords[row_idx]
                # Clip inside the box to be safe
                xi = float(np.clip(xi, 0.0 + EPS, w_ - EPS))
                yi = float(np.clip(yi, 0.0 + EPS, h_ - EPS))
                centers_list.append((xi, yi))

        centers = np.array(centers_list, dtype=float)

        # Add tiny jitter to break symmetry (scaled by s)
        if jitter_scale > 0:
            jitter_amp = jitter_scale * s
            jitter = (RNG.uniform(low=-1.0, high=1.0, size=centers.shape)) * jitter_amp
            centers[:, 0] = np.clip(centers[:, 0] + jitter[:, 0], 0.0 + EPS, w_ - EPS)
            centers[:, 1] = np.clip(centers[:, 1] + jitter[:, 1], 0.0 + EPS, h_ - EPS)

        return centers

    # ----------------------------
    # 1) Generate seeds and LP preselection
    # ----------------------------
    all_seeds = []
    for patt in patterns:
        for s in spacings:
            for sidx in range(seeds_per_ps):
                jitter = base_jitter * (1.0 + 0.35 * sidx)
                centers = seed_from_pattern_hex(patt, w, h, s=s, jitter_scale=jitter)
                # LP bootstrap to score
                r0 = fixed_center_lp(centers, w, h, EPS)
                score = compute_sum_r(r0)
                all_seeds.append((score, centers))
    # Keep top K seeds globally
    all_seeds.sort(key=lambda x: -x[0])
    survivors = [centers for (_, centers) in all_seeds[:topK]]
    # Safety net: ensure at least one seed
    if not survivors:
        survivors = [seed_from_pattern_hex(patterns[0], w, h, s=s_base, jitter_scale=base_jitter)]

    # ----------------------------
    # 2) For each survivor: LP + local optimization + polish
    # ----------------------------
    best_overall = None
    best_r_overall = None
    best_sum_overall = -1.0
    best_w, best_h = w, h

    for centers in survivors:
        c_opt, r_opt, s_opt = lp_then_local(centers, w, h)
        if s_opt > best_sum_overall + 1e-12:
            best_overall = c_opt
            best_r_overall = r_opt
            best_sum_overall = s_opt
            best_w, best_h = w, h

    # Safety fallback
    if best_overall is None or best_r_overall is None:
        # Return a conservative grid if something went wrong (should not happen)
        radius = 0.099999
        centers = np.array(
            [[(column + 0.5) / 5, (row + 0.5) / 5] for row in range(5) for column in range(5)][:num_circles],
            dtype=float,
        )
        radii = np.full(num_circles, radius, dtype=float)
        return np.column_stack((centers, radii))

    # ----------------------------
    # 3) Aspect-ratio sweep with w + h = 2
    # ----------------------------
    base_centers = best_overall
    base_r = best_r_overall
    base_sum = best_sum_overall
    base_w, base_h = best_w, best_h

    for alpha in aspect_alphas:
        w2 = 1.0 + alpha
        h2 = 1.0 - alpha
        # map centers anisotropically about the old and new box centers
        cx1, cy1 = base_w / 2.0, base_h / 2.0
        cx2, cy2 = w2 / 2.0, h2 / 2.0
        sx = w2 / base_w
        sy = h2 / base_h
        c2 = np.empty_like(base_centers)
        c2[:, 0] = cx2 + sx * (base_centers[:, 0] - cx1)
        c2[:, 1] = cy2 + sy * (base_centers[:, 1] - cy1)
        # LP at new aspect and short local refinement with sandwich
        c2, r2, s2 = lp_then_local(c2, w2, h2)
        # Extra uniform inflation attempt
        c2i, r2i = uniform_inflate_about_center(c2, r2, w2, h2, EPS)
        r2i = fixed_center_lp(c2i, w2, h2, EPS)
        s2i = compute_sum_r(r2i)
        if s2i > s2 + 1e-12:
            c2, r2, s2 = c2i, r2i, s2i
        if s2 > base_sum + 1e-12:
            base_centers, base_r, base_sum = c2, r2, s2
            base_w, base_h = w2, h2

    # ----------------------------
    # 4) Finalize: tiny safety shrink and output
    # ----------------------------
    final_centers = base_centers.copy()
    final_r = np.maximum(base_r - FINAL_SHRINK, 0.0)  # strict feasibility
    # Clip centers to be inside the current box margin; this does not increase bounding perim
    final_centers[:, 0] = np.clip(final_centers[:, 0], 0.0 + EPS, base_w - EPS)
    final_centers[:, 1] = np.clip(final_centers[:, 1], 0.0 + EPS, base_h - EPS)

    circles = np.column_stack((final_centers, final_r))
    # Ensure correct count
    if circles.shape[0] != num_circles:
        circles = circles[:num_circles, :]

    return circles


# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```
