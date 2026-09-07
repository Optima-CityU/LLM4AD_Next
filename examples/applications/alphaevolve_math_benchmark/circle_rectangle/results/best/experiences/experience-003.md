Why specific LP-centric selection and inflation policies improved Σr with validity 1.0, and how to reuse them.

- LP-Preselect Elite Pool with Event-Triggered LP Inflations: Use a global LP-only elite pool across all pattern–spacing seeds to retain strong initializations (keep per-pair top-K=2 and additionally keep global top-M=6 by Σr_LP, then deduplicate), and inside growth-and-move trigger a fixed-center LP inflation early whenever max per-circle slack falls below 5e−3 or the block Σr gain is <1e−4, accepting the LP only if Σr strictly increases; in this 21-circle unit-square run this configuration achieved Σr=2.2803735531870566 with validity=1.0 and is a reusable template for near-stall phases that converts structured slack with minimal overhead.

```python
#!/usr/bin/env python3
"""TriHex++ candidate for packing 21 circles with perimeter-four rectangle constraint.

We pack circles strictly inside the unit square [0,1]^2. That guarantees the
minimum axis-aligned circumscribing rectangle of the circles has width ≤ 1 and
height ≤ 1, hence perimeter ≤ 4.

Pipeline overview:
- Multi-start hexagonal seeding with several five-row patterns that sum to 21,
  varied lattice spacings near 0.2, and small random jitter for symmetry breaking.
- LP-only preselection with fixed centers to keep the best seeds (by sum of radii).
- For each survivor, a growth-and-move phase with adaptive trust region that
  alternates gentle center motions and monotone-radius growth, with periodic
  fixed-center LP inflations to remove structured slack.
- Dual global uniform inflation about (0.5, 0.5) to convert boundary slack into Σr.
- LP–mini-barrier–LP sandwich polish to exploit micro-slack via center nudges.
- Strict monotone acceptance gates and a final tiny shrink ensure feasibility.

The LP solver uses scipy.optimize.linprog if available and falls back to a
deterministic greedy inflation when SciPy is not installed.
"""

import json
import math
import random
from typing import List, Tuple, Optional

import numpy as np

# Try to import SciPy's linprog; fall back gracefully if unavailable.
try:
    from scipy.optimize import linprog as scipy_linprog  # type: ignore

    HAVE_SCIPY = True
except Exception:  # pragma: no cover - environment-dependent
    scipy_linprog = None
    HAVE_SCIPY = False


# ===========================
# Utility and core primitives
# ===========================

def clamp01(x: np.ndarray) -> np.ndarray:
    """Clamp array values into [0,1]."""
    return np.minimum(1.0, np.maximum(0.0, x))


def pairwise_distances(centers: np.ndarray) -> np.ndarray:
    """Compute pairwise Euclidean distances between centers. NxN matrix."""
    # centers: (N, 2)
    diff = centers[:, None, :] - centers[None, :, :]
    d2 = np.sum(diff * diff, axis=2)
    # Avoid tiny negative due to float
    return np.sqrt(np.maximum(d2, 0.0))


def boundary_upper_bounds(centers: np.ndarray) -> np.ndarray:
    """Compute per-circle upper bounds due to square boundary: u_i = min(x,1-x,y,1-y)."""
    x = centers[:, 0]
    y = centers[:, 1]
    return np.minimum(np.minimum(x, 1.0 - x), np.minimum(y, 1.0 - y))


def compute_pairwise_slacks(centers: np.ndarray, radii: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """Compute pairwise slack matrix s_ij = d_ij - (r_i + r_j) - eps."""
    d = pairwise_distances(centers)
    Rij = radii[:, None] + radii[None, :]
    return d - Rij - eps


def compute_boundary_slacks(centers: np.ndarray, radii: np.ndarray) -> np.ndarray:
    """For each circle i, returns [left, right, bottom, top] slacks."""
    x = centers[:, 0]
    y = centers[:, 1]
    return np.column_stack([x - radii, 1.0 - x - radii, y - radii, 1.0 - y - radii])


def feasibility_projection(centers: np.ndarray, radii: np.ndarray, eps: float = 1e-12) -> Tuple[np.ndarray, np.ndarray]:
    """Project to satisfy boundary constraints: clamp centers in [0,1], shrink radii to boundary bounds if needed."""
    centers = clamp01(centers)
    u = boundary_upper_bounds(centers)
    radii = np.minimum(radii, np.maximum(0.0, u - eps))
    return centers, radii


def resolve_overlaps(
    centers: np.ndarray,
    radii: np.ndarray,
    eps: float = 1e-9,
    max_iter: int = 300,
    step: float = 0.25,
) -> Tuple[np.ndarray, np.ndarray]:
    """Resolve overlaps by moving centers apart slightly, while keeping inside [0,1].
    Does not change radii except for boundary projection (very small).
    """
    n = centers.shape[0]
    for _ in range(max_iter):
        moved = False
        d = pairwise_distances(centers)
        diff = centers[:, None, :] - centers[None, :, :]

        # Push away from neighbors where overlap exists
        for i in range(n):
            force = np.array([0.0, 0.0], dtype=float)
            for j in range(n):
                if i == j:
                    continue
                dij = d[i, j]
                target = radii[i] + radii[j] + eps
                overlap = target - dij
                if overlap > 0.0:
                    moved = True
                    # Direction away from j
                    if dij > 1e-12:
                        dir_vec = diff[i, j] / dij
                    else:
                        # Same position: pick a random small direction
                        ang = (i * 37 + j * 101) % 360
                        dir_vec = np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
                    # Repulsive force proportional to overlap
                    force += dir_vec * overlap
            # Boundary repulsion if near/trespass
            slack = compute_boundary_slacks(centers[[i]], radii[[i]])[0]
            # If violating, push back harder
            if slack[0] < 0:  # left
                force[0] += -slack[0] * 2.0
            if slack[1] < 0:  # right
                force[0] += slack[1] * -2.0
            if slack[2] < 0:  # bottom
                force[1] += -slack[2] * 2.0
            if slack[3] < 0:  # top
                force[1] += slack[3] * -2.0

            if np.any(force != 0.0):
                centers[i] += step * force

        if not moved:
            break
        centers = clamp01(centers)
        centers, radii = feasibility_projection(centers, radii, eps=eps)
    return centers, radii


# =====================
# Hexagonal seed design
# =====================

def hex_seed(pattern: List[int], s: float) -> np.ndarray:
    """Generate centers from a true hexagonal lattice with given row counts pattern and spacing s."""
    # Vertical spacing for hex (triangular) lattice
    v = s * math.sqrt(3.0) / 2.0
    R = len(pattern)
    # Center rows vertically
    total_h = v * (R - 1) if R > 1 else 0.0
    y0 = 0.5 - total_h / 2.0
    centers = []
    for r in range(R):
        cnt = pattern[r]
        # Horizontal positions centered around 0.5
        total_w = s * (cnt - 1) if cnt > 1 else 0.0
        x0 = 0.5 - total_w / 2.0
        y = y0 + r * v
        # Stagger alternate rows by s/2
        x_shift = (s / 2.0) if (r % 2 == 1) else 0.0
        for k in range(cnt):
            x = x0 + k * s + x_shift
            centers.append([x, y])
    centers = np.array(centers, dtype=float)
    # Clamp into the unit square to be safe
    return clamp01(centers)


# =====================
# LP inflation routines
# =====================

def linprog_fixed_centers(
    centers: np.ndarray,
    eps: float = 1e-9,
) -> Optional[np.ndarray]:
    """Solve fixed-center LP: maximize sum r subject to:
       0 <= r_i <= u_i  (boundary upper bounds)
       r_i + r_j <= d_ij - eps
       Returns radii (np.ndarray) or None if SciPy not available.
    """
    if not HAVE_SCIPY:
        return None

    n = centers.shape(0) if callable(getattr(centers, "shape", None)) else centers.shape[0]  # robust shape
    d = pairwise_distances(centers)
    u = boundary_upper_bounds(centers)
    # Handle degenerate cases: if u_i < 0, set to 0
    u = np.maximum(0.0, u)
    # Objective: maximize sum r -> minimize -sum r
    c = -np.ones(n)

    # Build inequalities A_ub * r <= b_ub
    rows = []
    rhs = []
    # Upper bounds r_i <= u_i
    for i in range(n):
        row = np.zeros(n)
        row[i] = 1.0
        rows.append(row)
        rhs.append(u[i])
    # Pairwise constraints r_i + r_j <= d_ij - eps
    for i in range(n):
        for j in range(i + 1, n):
            rhs_ij = d[i, j] - eps
            if rhs_ij < 0.0:
                rhs_ij = 0.0
            row = np.zeros(n)
            row[i] = 1.0
            row[j] = 1.0
            rows.append(row)
            rhs.append(rhs_ij)

    A_ub = np.array(rows, dtype=float)
    b_ub = np.array(rhs, dtype=float)
    bounds = [(0.0, None) for _ in range(n)]

    try:
        res = scipy_linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        if res.success and res.x is not None:
            r = np.array(res.x, dtype=float)
            # Numerical clean-up
            r = np.maximum(0.0, r)
            r = np.minimum(r, u)
            return r
        return None
    except Exception:
        return None


def greedy_inflate_fixed_centers(
    centers: np.ndarray,
    eps: float = 1e-9,
    passes: int = 120,
) -> np.ndarray:
    """Greedy fixed-center inflation without SciPy.
    Iteratively increases radii by consuming available boundary and pairwise slack.
    Deterministic and fast, not exact LP but good approximation for small n.
    """
    n = centers.shape[0]
    r = np.zeros(n, dtype=float)
    u = boundary_upper_bounds(centers)
    d = pairwise_distances(centers)

    # Precompute pair indices
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]

    for _ in range(passes):
        improved = False
        # Order by available slack descending
        boundary_slack = u - r
        pair_slack_min = np.full(n, np.inf)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                s_ij = d[i, j] - eps - (r[i] + r[j])
                pair_slack_min[i] = min(pair_slack_min[i], s_ij)
        avail = np.maximum(0.0, np.minimum(boundary_slack, pair_slack_min))
        order = np.argsort(-avail)
        for i in order:
            inc = avail[i]
            if inc > 0.0:
                r[i] += inc
                improved = True
        if not improved:
            break
    # Final clipping for safety
    r = np.minimum(r, u)
    r = np.maximum(0.0, r)
    # Ensure pairwise feasibility by a final small safety shrink on offending pairs
    for (i, j) in pairs:
        s_ij = d[i, j] - eps - (r[i] + r[j])
        if s_ij < 0:
            # Reduce both slightly to satisfy
            delta = -s_ij / 2.0
            r[i] = max(0.0, r[i] - delta)
            r[j] = max(0.0, r[j] - delta)
    return r


def lp_inflate(centers: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """Fixed-center inflation: try exact LP via SciPy, else greedy fallback."""
    if HAVE_SCIPY:
        r = linprog_fixed_centers(centers, eps=eps)
        if r is not None:
            return r
    # Fallback
    return greedy_inflate_fixed_centers(centers, eps=eps)


# ==================
# Growth and movement
# ==================

def growth_and_move(
    centers: np.ndarray,
    radii: np.ndarray,
    total_iters: int = 400,
    beta: float = 0.4,
    near_thresh: float = 0.03,
    trust_init: float = 0.01,
    trust_min: float = 1e-4,
    trust_max: float = 0.05,
    block: int = 50,
    eps: float = 1e-9,
) -> Tuple[np.ndarray, np.ndarray]:
    """Iteratively grow radii and move centers with adaptive trust region.
    Periodically apply fixed-center LP inflation; accept only if Σr increases.
    """
    n = centers.shape[0]
    best_centers = centers.copy()
    best_r = radii.copy()
    best_sum = float(np.sum(best_r))

    trust = trust_init
    last_block_sum = best_sum
    # EVOLVE START: event-triggered LP inflation enhancements
    # Track the start-of-block Σr to decide when an early LP inflation is useful
    block_start_sum = best_sum
    # EVOLVE END

    for it in range(1, total_iters + 1):
        # 1) Grow radii by consuming a fraction of available slack
        d = pairwise_distances(centers)
        boundary_slack = boundary_upper_bounds(centers) - radii
        pair_slack_min = np.full(n, np.inf)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                s_ij = d[i, j] - eps - (radii[i] + radii[j])
                if s_ij < pair_slack_min[i]:
                    pair_slack_min[i] = s_ij
        avail = np.maximum(0.0, np.minimum(boundary_slack, pair_slack_min))
        order = np.argsort(-avail)
        for i in order:
            inc = beta * avail[i]
            if inc > 0.0:
                radii[i] += inc

        # 2) Gentle center moves driven by near constraints
        forces = np.zeros_like(centers)
        # Pairwise forces
        s_mat = d - (radii[:, None] + radii[None, :]) - eps
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                s_ij = s_mat[i, j]
                if s_ij < near_thresh:
                    # Push away from j
                    vec = centers[i] - centers[j]
                    dij = d[i, j]
                    if dij > 1e-12:
                        dir_vec = vec / dij
                    else:
                        # Degenerate direction
                        ang = (i * 97 + j * 131) % 360
                        dir_vec = np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
                    weight = (near_thresh - s_ij) / max(near_thresh, 1e-6)
                    forces[i] += dir_vec * weight
        # Boundary forces
        bsl = compute_boundary_slacks(centers, radii)
        # left, right, bottom, top
        for i in range(n):
            # push +x if near left
            if bsl[i, 0] < near_thresh:
                forces[i, 0] += (near_thresh - bsl[i, 0]) / max(near_thresh, 1e-6)
            # push -x if near right
            if bsl[i, 1] < near_thresh:
                forces[i, 0] -= (near_thresh - bsl[i, 1]) / max(near_thresh, 1e-6)
            # push +y if near bottom
            if bsl[i, 2] < near_thresh:
                forces[i, 1] += (near_thresh - bsl[i, 2]) / max(near_thresh, 1e-6)
            # push -y if near top
            if bsl[i, 3] < near_thresh:
                forces[i, 1] -= (near_thresh - bsl[i, 3]) / max(near_thresh, 1e-6)

        # Apply move
        centers += trust * forces
        centers = clamp01(centers)
        centers, radii = feasibility_projection(centers, radii, eps=eps)
        centers, radii = resolve_overlaps(centers, radii, eps=eps, max_iter=8, step=0.2)

        # EVOLVE START: Event-triggered fixed-center LP inflations near stagnation
        # Trigger early LP inflation if:
        # (a) the maximum available per-circle slack falls below 5e-3, OR
        # (b) Σr improvement over the current block is < 1e-4.
        current_sum = float(np.sum(radii))
        trigger_a = (np.max(avail) < 5e-3)
        trigger_b = ((current_sum - block_start_sum) < 1e-4)
        if trigger_a or trigger_b:
            r_lp_ev = lp_inflate(centers, eps=eps)
            sum_lp_ev = float(np.sum(r_lp_ev))
            if sum_lp_ev > best_sum + 1e-12:
                radii = r_lp_ev
                best_centers = centers.copy()
                best_r = radii.copy()
                best_sum = sum_lp_ev
        # EVOLVE END

        # 3) Periodic exact LP inflation on fixed centers
        if it % block == 0:
            r_lp = lp_inflate(centers, eps=eps)
            sum_lp = float(np.sum(r_lp))
            if sum_lp > best_sum + 1e-12:
                radii = r_lp
                best_centers = centers.copy()
                best_r = radii.copy()
                best_sum = sum_lp

            # Trust region adaptation
            if best_sum <= last_block_sum + 1e-12:
                trust = max(trust_min, trust * 0.7)
            else:
                trust = min(trust_max, trust * 1.1)
            last_block_sum = best_sum
            # EVOLVE START: reset block start tracker at block boundary
            block_start_sum = best_sum
            # EVOLVE END

    # Return best encountered
    return best_centers, best_r


# ==========================
# Global uniform inflation
# ==========================

def global_uniform_inflation(
    centers: np.ndarray,
    radii: np.ndarray,
    eps: float = 1e-9,
) -> Tuple[np.ndarray, np.ndarray]:
    """Uniformly scale (x, y, r) about (0.5, 0.5) to saturate a boundary without changing pairwise tangency ratios.
    Monotone acceptance is done outside.
    """
    c0 = np.array([0.5, 0.5], dtype=float)
    s = centers - c0
    x = s[:, 0]
    y = s[:, 1]
    r = radii.copy()

    # Determine maximum scale a >= 1 such that boundary constraints hold:
    # For each circle, constraints:
    # a*(r - x) <= 0.5
    # a*(r + x) <= 0.5
    # a*(r - y) <= 0.5
    # a*(r + y) <= 0.5
    # Only consider terms with positive multipliers; otherwise inequality is non-restrictive for a>=1.
    a_candidates = [1.0]
    for i in range(len(r)):
        terms = [
            r[i] - x[i],
            r[i] + x[i],
            r[i] - y[i],
            r[i] + y[i],
        ]
        for t in terms:
            if t > 1e-18:
                a_candidates.append(0.5 / t)
    a_max = min(a_candidates)
    a_max = max(1.0, a_max)
    # Apply scale slightly under the max to ensure strict feasibility
    a = max(1.0, a_max * (1.0 - 1e-12))

    centers2 = c0 + a * s
    radii2 = a * r
    centers2, radii2 = feasibility_projection(centers2, radii2, eps=eps)
    # No need to resolve overlaps: pairwise constraints are scaled equally
    return centers2, radii2


# ===========================
# Mini-barrier polish routine
# ===========================

def mini_barrier_polish(
    centers: np.ndarray,
    radii: np.ndarray,
    mu_schedule: List[float] = [5e-4, 2e-4, 1e-4],
    inner_steps: int = 40,
    eps: float = 1e-9,
) -> np.ndarray:
    """Keep radii fixed, perform short barrier ascent moving centers to increase slack:
    maximize sum log(boundary slacks) + sum log(pairwise slacks).
    Returns new centers.
    """
    n = centers.shape[0]
    C = centers.copy()

    # EVOLVE START: barrier polish micro-tuning with adaptive iterations
    base_steps = inner_steps
    extra_factor = 0.0  # cumulative extra factor to apply for remaining μ-stages
    prev_near_active_pairs: Optional[int] = None
    # EVOLVE END

    for mu_idx, mu in enumerate(mu_schedule):
        # EVOLVE START: use adapted steps for this stage
        steps_this_mu = int(max(1, round(base_steps * (1.0 + extra_factor))))
        step = 0.02
        for _ in range(steps_this_mu):
            # EVOLVE END
            # Compute slacks
            d = pairwise_distances(C)
            s_pairs = d - (radii[:, None] + radii[None, :]) - eps
            bsl = compute_boundary_slacks(C, radii)
            if np.any(s_pairs <= 0.0) or np.any(bsl <= 0.0):
                # infeasible — shrink step and continue
                step *= 0.5
                if step < 1e-6:
                    break
                continue

            # Gradient of sum log slacks wrt centers
            grad = np.zeros_like(C)
            # Pairwise: for i<j, grad_i += (1/s_ij) * d(d_ij)/dx_i = (1/s_ij) * (x_i-x_j)/d_ij
            for i in range(n):
                for j in range(n):
                    if i == j:
                        continue
                    s_ij = s_pairs[i, j]
                    if s_ij <= 0:
                        continue
                    dij = d[i, j]
                    if dij <= 1e-12:
                        continue
                    dir_vec = (C[i] - C[j]) / dij
                    grad[i] += dir_vec * (1.0 / s_ij)

            # Boundary logs: log(x - r), log(1 - x - r), log(y - r), log(1 - y - r)
            # Gradients are +1/(x - r) for x, -1/(1 - x - r) for x, similarly for y
            x = C[:, 0]
            y = C[:, 1]
            # Avoid division by zero by clamping denominators (should be positive already)
            grad[:, 0] += 1.0 / np.maximum(bsl[:, 0], 1e-18)  # left
            grad[:, 0] += -1.0 / np.maximum(bsl[:, 1], 1e-18)  # right
            grad[:, 1] += 1.0 / np.maximum(bsl[:, 2], 1e-18)  # bottom
            grad[:, 1] += -1.0 / np.maximum(bsl[:, 3], 1e-18)  # top

            # Scale by mu
            grad *= mu

            # Backtracking line search to keep feasibility
            ok = False
            for _bt in range(10):
                C_try = C + step * grad
                C_try = clamp01(C_try)
                bsl_try = compute_boundary_slacks(C_try, radii)
                d_try = pairwise_distances(C_try)
                s_pairs_try = d_try - (radii[:, None] + radii[None, :]) - eps
                if np.all(bsl_try > 0.0) and np.all(s_pairs_try > 0.0):
                    C = C_try
                    ok = True
                    break
                step *= 0.5
            if not ok:
                # no feasible move
                break

        # EVOLVE START: after each μ stage, measure near-active pairs and adapt remaining iterations if needed
        # Count near-active pair constraints s_ij < 2e-4 (i<j)
        d_after = pairwise_distances(C)
        s_pairs_after = d_after - (radii[:, None] + radii[None, :]) - eps
        # Consider only upper triangle to avoid double-counting and ignore self
        i_upper, j_upper = np.triu_indices(n, k=1)
        near_active_count = int(np.sum(s_pairs_after[i_upper, j_upper] < 2e-4))
        if prev_near_active_pairs is not None:
            if near_active_count > prev_near_active_pairs * 1.05:
                # Increase iterations for remaining μ stages by +20%, capped at +40% total
                extra_factor = min(0.4, extra_factor + 0.2)
        prev_near_active_pairs = near_active_count
        # EVOLVE END

    return C


# =====================
# Seed running pipeline
# =====================

def run_seed(centers: np.ndarray, eps: float = 1e-9) -> Tuple[np.ndarray, np.ndarray]:
    """Run the full TriHex++ pipeline from an initial center seed."""
    n = centers.shape[0]
    # Initialization: LP bootstrap (fixed centers)
    r = lp_inflate(centers, eps=eps)
    centers, r = feasibility_projection(centers, r, eps=eps)

    best_centers = centers.copy()
    best_r = r.copy()
    best_sum = float(np.sum(best_r))

    # Growth-and-move with periodic LP
    centers2, r2 = growth_and_move(centers.copy(), r.copy(), eps=eps)
    if np.sum(r2) > best_sum + 1e-12:
        best_centers, best_r = centers2, r2
        best_sum = float(np.sum(best_r))

    # Global uniform inflation and follow-up LP
    c_g, r_g = global_uniform_inflation(best_centers, best_r, eps=eps)
    r_lp = lp_inflate(c_g, eps=eps)
    if np.sum(r_lp) > best_sum + 1e-12:
        best_centers, best_r = c_g, r_lp
        best_sum = float(np.sum(best_r))

    # Optional second uniform pass
    c_g2, r_g2 = global_uniform_inflation(best_centers, best_r, eps=eps)
    r_lp2 = lp_inflate(c_g2, eps=eps)
    if np.sum(r_lp2) > best_sum + 1e-12:
        best_centers, best_r = c_g2, r_lp2
        best_sum = float(np.sum(best_r))

    # LP–mini-barrier–LP sandwich polish
    r_lp3 = lp_inflate(best_centers, eps=eps)
    if np.sum(r_lp3) > best_sum + 1e-12:
        best_r = r_lp3
        best_sum = float(np.sum(best_r))

    C_pol = mini_barrier_polish(best_centers.copy(), best_r.copy(), eps=eps)
    r_pol = lp_inflate(C_pol, eps=eps)
    if np.sum(r_pol) > best_sum + 1e-12:
        best_centers, best_r = C_pol, r_pol
        best_sum = float(np.sum(best_r))

    # Final feasibility and tiny shrink
    best_centers, best_r = feasibility_projection(best_centers, best_r, eps=eps)
    best_centers, best_r = resolve_overlaps(best_centers, best_r, eps=eps, max_iter=30, step=0.2)
    best_r *= (1.0 - 1e-9)

    return best_centers, best_r


# ====================
# Multi-start strategy
# ====================

def construct_packing(num_circles: int = 21):
    """Main entry: construct 21 disjoint circles inside the unit square maximizing sum of radii.

    Returns:
        np.ndarray of shape (n, 3): rows [x, y, r]
    """
    assert num_circles == 21, "This solver is tuned for exactly 21 circles."

    # EVOLVE START: seed diversification and global-elite LP-only preselection
    # Patterns: five-row counts summing to 21 (augmented with two additional validated patterns)
    patterns = [
        [5, 4, 5, 4, 3],
        [5, 5, 4, 4, 3],
        [4, 5, 5, 4, 3],
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
        # Added patterns validated in prior elites
        [6, 4, 4, 4, 3],
        [5, 5, 5, 3, 3],
    ]
    # Lattice spacings to explore around ~0.2
    spacings = [0.2 * f for f in [0.96, 0.98, 1.00, 1.02, 1.04]]

    rng = np.random.default_rng(123456)  # deterministic for reproducibility
    # Jitters: three with 1e-3 and three with 2e-3 per (pattern, spacing)
    jitters = [1e-3] * 3 + [2e-3] * 3

    per_combo_keep = 2  # K survivors per (pattern, spacing)
    global_elite_M = 6  # global elite pool size

    # Collect per-combo survivors and all candidates for the global elite pool
    per_combo_survivors: List[Tuple[float, np.ndarray]] = []
    all_candidates: List[Tuple[float, np.ndarray]] = []

    for pat in patterns:
        for s in spacings:
            # Generate a base seed
            base = hex_seed(pat, s)
            # If more than 21 due to pattern mismatch, trim; if fewer, pad with random points
            if base.shape[0] > num_circles:
                base = base[:num_circles]
            elif base.shape[0] < num_circles:
                # pad random points near center
                extra = num_circles - base.shape[0]
                extra_pts = rng.uniform(0.35, 0.65, size=(extra, 2))
                base = np.vstack([base, extra_pts])

            # Generate jittered variants with prescribed magnitudes
            candidates: List[Tuple[float, np.ndarray]] = []
            for js in jitters:
                jitter = rng.uniform(-js, js, size=base.shape)
                seed = clamp01(base + jitter)
                # LP-only preselection on fixed centers
                r_lp = lp_inflate(seed, eps=1e-9)
                sum_r = float(np.sum(r_lp))
                candidates.append((sum_r, seed))
                all_candidates.append((sum_r, seed))

            # Keep top-K per combo
            candidates.sort(key=lambda t: t[0], reverse=True)
            per_combo_survivors.extend(candidates[:per_combo_keep])

    # Build global elite pool across all (pattern, spacing) pairs
    all_candidates.sort(key=lambda t: t[0], reverse=True)
    global_elite = all_candidates[:global_elite_M]

    # Deduplicate and pass union of per-combo survivors and the global elite pool to the heavy pipeline
    def seed_key(arr: np.ndarray) -> Tuple:
        # Round to 9 decimals to be robust against floating noise
        return tuple(np.round(arr.flatten(), 9).tolist())

    union_map = {}
    for score, seed in per_combo_survivors + global_elite:
        k = seed_key(seed)
        # Keep the best score for duplicate seeds
        if (k not in union_map) or (score > union_map[k][0]):
            union_map[k] = (score, seed)
    survivors: List[Tuple[float, np.ndarray]] = sorted(union_map.values(), key=lambda t: t[0], reverse=True)
    # EVOLVE END

    # Run heavy pipeline and keep the best result
    best_sum = -1.0
    best_centers = None
    best_r = None

    for pre_sum, seed in survivors:
        centers, radii = run_seed(seed, eps=1e-9)
        ssum = float(np.sum(radii))
        if ssum > best_sum + 1e-12:
            best_sum = ssum
            best_centers = centers
            best_r = radii

    # As a fallback (shouldn't happen), if survivors empty or best is None, default to simple grid
    if best_centers is None or best_r is None:
        radius = 0.099999
        centers = np.array(
            [[(column + 0.5) / 5, (row + 0.5) / 5] for row in range(5) for column in range(5)][:num_circles],
            dtype=float,
        )
        radii = np.full(num_circles, radius, dtype=float)
        circles = np.column_stack((centers, radii))
        return circles

    circles = np.column_stack((best_centers, best_r))
    # Final safeguard: ensure strict feasibility and shape (21,3)
    circles[:, :2] = clamp01(circles[:, :2])
    ub = boundary_upper_bounds(circles[:, :2])
    circles[:, 2] = np.minimum(circles[:, 2], ub * (1.0 - 1e-12))
    # Return exactly 21 circles
    if circles.shape[0] > num_circles:
        circles = circles[:num_circles]
    return circles


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```
