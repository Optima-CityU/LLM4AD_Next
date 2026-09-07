Actionable mechanism that repeatedly converts boundary slack into Σr gains under strict acceptance.

- Applying a triple-anchor uniform scaling gate at preselection, early, mid-run, and late phases—using anchors C0 = (0.5,0.5), Cr (radii-weighted barycenter), and Crm (slack-weighted barycenter with weights r_i·m_i)—and for each anchor selecting the maximal scale t ≥ 1 that preserves all wall slacks ≥ ε_init, followed by a fixed-center LP capture and a strict Σr acceptance gate (>1e−14), consistently monetized boundary slack and delivered measurable improvements (+0.001 to +0.005 in Σr at N=21), culminating in a valid score of 2.357778925836878. Future designs should reuse this multi-anchor, multi-phase gate with LP captures (ε_lp ≈ 1e−12 to 5e−10) and immediate feasibility reprojection to safely harvest slack without regressions.

```python
#!/usr/bin/env python3
"""High-quality candidate for packing 21 disjoint circles inside the unit square.

Implements a strengthened hybrid algorithm combining:
- multi-start true hexagonal seeding,
- LP-only preselection with a triple-anchor uniform inflation gate (two-stage LP ε),
- strictly feasible log-barrier ascent (centers + radii) with adaptive LP cadence and lazy adjacency,
- second-anchor stabilization to improve conditioning,
- triple-anchor uniform inflations at early, mid-run, and late phases under strict Σr acceptance gates,
- endgame LP–mini-barrier–LP sandwich,
- strict-feasibility polishing and micro-overlap removal,
and selection of the best candidate by Σr.

Any set of circles entirely inside the unit square [0,1]^2 has circumscribing
rectangle perimeter ≤ 4, so we optimize inside the square.

Notes:
- SciPy linprog is used if available for exact fixed-center LP steps; otherwise
  the algorithm gracefully falls back with a robust fixed-center heuristic and
  barrier + inflation only.
- Parameters follow successful configurations for 21 circles.

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

    # Global algorithm parameters (chosen from prior successful runs and enhanced for triple-anchor gates).
    rng = np.random.default_rng(42)
    EPS_INIT = 1e-6
    EPS_FINAL = 1e-12
    EPS_LP = 5e-10  # small ε for strict feasibility in LP capture (main runs)
    EPS_LP_PRE1 = 1e-10  # looser LP ε for preselection (first-stage)
    EPS_LP_PRE2 = 1e-12  # tighter LP ε for preselection (second-stage on scaled centers)
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
    ACCEPT_TOL = 1e-14  # strict Σr acceptance gate tolerance

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

    # 2) Optional two-scale seed: 5x4 base grid plus one interstitial (clearance-aware).
    jittered_seeds.extend(generate_two_scale_seed_21(rng))

    # 3) LP-only preselection with triple-anchor inflation:
    #    For each seed: LP (looser ε) -> triple-anchor uniform scale -> LP capture (tighter ε).
    candidates: List[Tuple[np.ndarray, np.ndarray, float]] = []  # (centers, radii, sum_r)
    for centers in jittered_seeds:
        # First fixed-center LP capture
        radii_lp1 = fixed_center_lp(centers, eps_lp=EPS_LP_PRE1)
        sum_r_lp1 = float(np.sum(radii_lp1))

        # Triple-anchor inflation gate with tighter LP capture
        c_best, r_best = triple_anchor_inflation_gate(
            centers, radii_lp1, epsilon=EPS_INIT, eps_lp=EPS_LP_PRE2
        )
        sum_r_best = float(np.sum(r_best))

        # Accept scaled variant only if Σr improves strictly (gate)
        if sum_r_best > sum_r_lp1 + ACCEPT_TOL:
            # Enforce strict feasibility
            r_best, c_best = enforce_strict_feasibility(c_best, r_best, EPS_INIT)
            candidates.append((c_best, r_best, sum_r_best))
        else:
            radii_lp1, centers = enforce_strict_feasibility(centers, radii_lp1, EPS_INIT)
            candidates.append((centers, radii_lp1, sum_r_lp1))

    # Rank by Σr and keep the top MAX_PRESELECT as warm starts
    candidates.sort(key=lambda t: t[2], reverse=True)
    preselected = candidates[:MAX_PRESELECT] if len(candidates) > MAX_PRESELECT else candidates

    # 4) Growth-and-move via strictly-feasible log-barrier ascent w/ adaptive LP cadence.
    best_circles = None
    best_sum_r = -1.0

    for (centers0, radii0, _) in preselected:
        centers = centers0.copy()
        # Initialize radii: use preselected radii; otherwise a safe fraction of wall clearance.
        if radii0 is not None and radii0.shape[0] == centers.shape[0]:
            radii = np.maximum(radii0.copy(), 1e-6)  # strictly positive
        else:
            m = wall_clearances(centers)
            radii = 0.45 * m

        # Strict feasibility: ensure all constraints have positive slack with EPS_INIT.
        epsilon = EPS_INIT
        radii, centers = enforce_strict_feasibility(centers, radii, epsilon)

        # 5) Early triple-anchor uniform inflation gate + LP capture, accepted only if Σr increases
        sum_r_before = float(np.sum(radii))
        c_early, r_early = triple_anchor_inflation_gate(centers, radii, epsilon=epsilon, eps_lp=EPS_LP)
        if float(np.sum(r_early)) > sum_r_before + ACCEPT_TOL:
            centers, radii = c_early, r_early
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

        # Mid-run triple-anchor inflation flag
        mid_inflation_done = False

        for mu_idx, mu in enumerate(MU_SCHEDULE):
            # ε-annealing: gradually reduce epsilon and apply tiny shrink to radii to preserve strict slacks.
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
                    # Adjust LP period based on progress and near-active counts
                    if sum_r_now <= last_sum_r or (len(near_counts_hist) >= 2 and (
                       (near_counts_hist[-1][0] + near_counts_hist[-1][1]) >
                       1.1 * (near_counts_hist[-2][0] + near_counts_hist[-2][1]))):
                        lp_period = max(20, int(0.6 * lp_period))
                    else:
                        lp_period = min(60, int(math.ceil(1.1 * lp_period)))
                    # Fixed-center LP polish at current centers
                    radii_lp = fixed_center_lp(centers, eps_lp=EPS_LP)
                    if np.sum(radii_lp) > np.sum(radii) + ACCEPT_TOL:
                        radii = radii_lp
                        # Reproject centers into [r+ε, 1-r-ε]
                        centers[:, 0] = np.clip(centers[:, 0], radii + epsilon, 1 - radii - epsilon)
                        centers[:, 1] = np.clip(centers[:, 1], radii + epsilon, 1 - radii - epsilon)
                        radii, centers = enforce_strict_feasibility(centers, radii, epsilon)
                    last_sum_r = float(np.sum(radii))

                # Mid-run triple-anchor uniform inflation gate + LP capture (one-time)
                if not mid_inflation_done and mu_idx >= max(1, len(MU_SCHEDULE) // 2):
                    sum_r_pre_mid = float(np.sum(radii))
                    c_mid, r_mid = triple_anchor_inflation_gate(centers, radii, epsilon=epsilon, eps_lp=EPS_LP)
                    if float(np.sum(r_mid)) > sum_r_pre_mid + ACCEPT_TOL:
                        centers, radii = c_mid, r_mid
                        radii, centers = enforce_strict_feasibility(centers, radii, epsilon)
                        pair_idx = build_lazy_pairs(centers, k=K_NEIGHBORS_INIT)
                        mid_inflation_done = True

        # 6) Late triple-anchor gate (monetize boundary slack) + LP polish
        c_late, r_late = triple_anchor_inflation_gate(centers, radii, epsilon=epsilon, eps_lp=EPS_LP)
        if np.sum(r_late) > np.sum(radii) + ACCEPT_TOL:
            centers, radii = c_late, r_late
            radii, centers = enforce_strict_feasibility(centers, radii, epsilon)

        # Optional LP polish
        if HAS_SCIPY:
            radii_lp = fixed_center_lp(centers, eps_lp=EPS_LP)
            if np.sum(radii_lp) > np.sum(radii) + ACCEPT_TOL:
                radii = radii_lp
                centers[:, 0] = np.clip(centers[:, 0], radii + epsilon, 1 - radii - epsilon)
                centers[:, 1] = np.clip(centers[:, 1], radii + epsilon, 1 - radii - epsilon)

        # 7) Endgame LP–mini-barrier–LP sandwich with ε-annealed gate
        if HAS_SCIPY:
            radii_lp1 = fixed_center_lp(centers, eps_lp=EPS_LP)
            if np.sum(radii_lp1) > np.sum(radii) + ACCEPT_TOL:
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
            if np.sum(radii_lp2) > np.sum(radii) + ACCEPT_TOL:
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
    # s_ij = d_ij - r_i - r_j - epsilon; fill symmetric matrix with s_ij
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
    subject to 0 ≤ r_i ≤ m_i (wall clearance), and r_i + r_j ≤ d_ij − ε_lp for all i<j.
    """
    n = centers.shape[0]
    m = wall_clearances(centers)
    if not HAS_SCIPY:
        # Robust fallback: feasible heuristic: start from wall clearances and shrink by pair constraints greedily.
        r = m.copy()
        for i in range(n):
            for j in range(i + 1, n):
                dij = math.dist(centers[i], centers[j])
                ub = max(0.0, dij - eps_lp)
                if r[i] + r[j] > ub:
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
    # Pairwise constraints r_i + r_j ≤ d_ij − ε_lp
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
    Legacy centered inflation (about (0.5,0.5)) retained for compatibility.
    Prefer triple_anchor_inflation_gate for improved performance.
    """
    C0 = np.array([0.5, 0.5], dtype=float)
    return apply_uniform_scale_about_anchor(centers, radii, C0, epsilon)[0:2]


def apply_uniform_scale_about_anchor(
    centers: np.ndarray, radii: np.ndarray, anchor: np.ndarray, epsilon: float, keep_margin: float = 0.999
) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    Uniformly scale centers and radii about a given anchor until a wall constraint becomes tight.
    Pairwise non-overlap is preserved by scaling; walls limit scaling.
    Returns (centers_new, radii_new, scale_t).
    """
    cx, cy = float(anchor[0]), float(anchor[1])
    x = centers[:, 0]
    y = centers[:, 1]
    r = radii

    # For constraints of form base + t*coeff >= 0, an upper bound exists only when coeff < 0:
    # t <= base / (-coeff). Otherwise coeff >= 0 implies slack grows with t and no upper bound (∞).
    base_L = cx - epsilon
    base_R = 1.0 - cx - epsilon
    base_B = cy - epsilon
    base_T = 1.0 - cy - epsilon

    coeff_L = (x - cx) - r
    coeff_R = -((x - cx) + r)
    coeff_B = (y - cy) - r
    coeff_T = -((y - cy) + r)

    def bound_t(base: float, coeff_vec: np.ndarray) -> float:
        mask = coeff_vec < 0.0
        if not np.any(mask):
            return float("inf")
        vals = base / (-coeff_vec[mask])
        # Only consider positive bounds (base>0 typically); any nonpositive implies infeasible scaling
        vals = vals[vals > 0]
        if vals.size == 0:
            return float("inf")
        return float(np.min(vals))

    t_bounds = [
        bound_t(base_L, coeff_L),
        bound_t(base_R, coeff_R),
        bound_t(base_B, coeff_B),
        bound_t(base_T, coeff_T),
    ]
    t_max = min(t_bounds)
    t_max = max(1.0, t_max)
    t = t_max * keep_margin  # keep strict slack

    if t <= 1.0001:
        # Not enough slack to inflate
        return centers, radii, 1.0

    x_new = cx + t * (x - cx)
    y_new = cy + t * (y - cy)
    r_new = t * r
    # Strict feasibility clip
    x_new = np.clip(x_new, r_new + epsilon, 1 - r_new - epsilon)
    y_new = np.clip(y_new, r_new + epsilon, 1 - r_new - epsilon)
    return np.column_stack((x_new, y_new)), r_new, t


def compute_triple_anchors(centers: np.ndarray, radii: np.ndarray, epsilon: float) -> List[np.ndarray]:
    """
    Compute triple anchors:
      C0 = (0.5, 0.5),
      Cr = radii-weighted barycenter,
      Crm = slack-weighted barycenter with weights w_i = r_i * m_i, m_i = wall clearance at ε_init (approx via wall_clearances).
    """
    n = centers.shape[0]
    C0 = np.array([0.5, 0.5], dtype=float)

    # Radii-weighted barycenter
    weights_r = np.maximum(radii, 0.0)
    sum_w_r = float(np.sum(weights_r))
    if sum_w_r > 0.0:
        Cr = np.array(
            [
                float(np.sum(weights_r * centers[:, 0]) / sum_w_r),
                float(np.sum(weights_r * centers[:, 1]) / sum_w_r),
            ],
            dtype=float,
        )
    else:
        Cr = C0.copy()

    # Slack-weighted barycenter: w_i = r_i * m_i, m_i = wall clearance
    m = wall_clearances(centers)
    weights_rm = np.maximum(radii * m, 0.0)
    sum_w_rm = float(np.sum(weights_rm))
    if sum_w_rm > 0.0:
        Crm = np.array(
            [
                float(np.sum(weights_rm * centers[:, 0]) / sum_w_rm),
                float(np.sum(weights_rm * centers[:, 1]) / sum_w_rm),
            ],
            dtype=float,
        )
    else:
        Crm = C0.copy()

    # Clip anchors to strictly inside [ε, 1-ε] to avoid degenerate bounds
    def clip_anchor(C: np.ndarray) -> np.ndarray:
        return np.array([float(np.clip(C[0], epsilon, 1.0 - epsilon)), float(np.clip(C[1], epsilon, 1.0 - epsilon))], dtype=float)

    return [clip_anchor(C0), clip_anchor(Cr), clip_anchor(Crm)]


def triple_anchor_inflation_gate(
    centers: np.ndarray, radii: np.ndarray, epsilon: float, eps_lp: float
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Run triple-anchor uniform scaling gate:
      - Compute anchors C0, Cr, Crm (weights r_i and r_i*m_i).
      - For each anchor, compute maximal uniform scale t >= 1, apply scaling,
        and run a fixed-center LP capture (ε_lp).
      - Keep the variant with largest Σr; accept strictly by returning the best variant.
    If SciPy is unavailable, compare sums from post-inflation radii without LP.
    """
    anchors = compute_triple_anchors(centers, radii, epsilon)
    best_sum = float(np.sum(radii))
    best_centers = centers.copy()
    best_radii = radii.copy()

    for C in anchors:
        c_scaled, r_scaled, t = apply_uniform_scale_about_anchor(centers, radii, C, epsilon)
        if HAS_SCIPY:
            r_lp = fixed_center_lp(c_scaled, eps_lp=eps_lp)
            sum_variant = float(np.sum(r_lp))
            if sum_variant > best_sum:
                best_sum = sum_variant
                best_centers = c_scaled
                best_radii = r_lp
        else:
            sum_variant = float(np.sum(r_scaled))
            if sum_variant > best_sum:
                best_sum = sum_variant
                best_centers = c_scaled
                best_radii = r_scaled

    return best_centers, best_radii


# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```
