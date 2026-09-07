Monotone backtracking line search in intensification plus active-set (TRIPLES) reuse delivered stable, non-worsening updates and reduced overhead within the SA loop for the n=11 Heilbronn setup.

- Backtracking Gradient Intensification with Active-Set Reuse: Replacing the prior accept-or-break policy with a monotone backtracking line search that halves the gradient step size on any observed drop in the true global min area and retries up to 4–5 times—accepting the first non-decreasing step—preserved feasibility while consistently lifting bottleneck triangles; coupled with passing a precomputed TRIPLES array to avoid recomputing combinations and tuning intensification (initial step reduced from 0.010 to 0.009 and substeps increased from 3 to 4), this design produced a target_ratio of 0.971244 (min_area 0.0354504, validity 1.0) versus the parent_score 0.924017 under the same benchmark.

```python
#!/usr/bin/env python3
"""Heuristic solver for the Heilbronn problem in an equilateral triangle.

This implements a projected simulated annealing with multi-start and
geometry-aware intensification in barycentric coordinates. It aims to
maximize the minimum triangle area among n points strictly inside the
unit-side equilateral triangle with vertices:
A=(0,0), B=(1,0), C=(0.5, sqrt(3)/2)

Entry point: run_search_point(n)
"""

import json
import itertools
import math
import random
from typing import List, Tuple

# Optional NumPy for efficient array ops
try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


# EVOLVE_START
def run_search_point(n=11):
    """Entry point for evaluator. Returns (points, min_area).

    points: list of [x, y] strictly inside the target triangle
    min_area: smallest triangle area normalized by the big triangle area
    """
    if n != 11:
        # The provided heuristic and tuning is for n=11
        raise ValueError("the benchmark evaluates n=11")

    # Solve using our search algorithm
    points, min_area = find_best_placement(n)
    return points, min_area


def find_best_placement(n: int) -> Tuple[List[List[float]], float]:
    """Main search routine combining multi-start SA and intensification."""
    assert n == 11
    if np is None:
        # Fallback to a simple random configuration if NumPy is not available
        # (should not happen in the judge).
        generator = random.Random(42)
        pts = []
        for _ in range(n):
            u = generator.random()
            v = generator.random()
            if u + v > 1.0:
                u, v = 1.0 - u, 1.0 - v
            x = u + 0.5 * v
            y = math.sqrt(3.0) * 0.5 * v
            pts.append([x, y])
        min_area = compute_min_area_plain(pts)
        return pts, min_area

    # Geometry constants
    A = np.array([0.0, 0.0], dtype=np.float64)
    B = np.array([1.0, 0.0], dtype=np.float64)
    C = np.array([0.5, math.sqrt(3.0) * 0.5], dtype=np.float64)
    TRI_VERTS = np.stack([A, B, C], axis=0)
    BIG_AREA = math.sqrt(3.0) / 4.0  # area of unit-side equilateral triangle

    # Precompute linear mapping for barycentric/cartesian transforms
    # For P = A + u*(B-A) + v*(C-A). Then barycentric (a,b,c) = (1-u-v, u, v)
    M = np.stack([B - A, C - A], axis=1)  # 2x2
    Minv = np.linalg.inv(M)

    # Helper functions
    def bary_to_cart(bary: np.ndarray) -> np.ndarray:
        # bary shape (n,3)
        return bary @ TRI_VERTS  # linear combination

    def cart_to_bary(P: np.ndarray) -> np.ndarray:
        # P shape (n,2) or (2,)
        # Compute (u,v) for P = A + u*(B-A) + v*(C-A)
        if P.ndim == 1:
            v2 = P - A
            uv = Minv @ v2
            u, v = uv[0], uv[1]
            a, b, c = 1.0 - u - v, u, v
            return np.array([a, b, c], dtype=np.float64)
        else:
            V2 = (P - A).T  # 2 x n
            UV = Minv @ V2  # 2 x n
            u = UV[0, :]
            v = UV[1, :]
            a = 1.0 - u - v
            b = u
            c = v
            return np.stack([a, b, c], axis=1)

    def project_to_simplex_with_lower(y: np.ndarray, eps: float) -> np.ndarray:
        # Project vector y (len=3) to the set {x >= eps, sum x = 1}
        z = y - eps
        s = 1.0 - 3.0 * eps
        v = np.sort(z)[::-1]
        cssv = np.cumsum(v)
        rho_idx = np.nonzero(v - (cssv - s) / (np.arange(1, len(v) + 1)) > 0)[0]
        if len(rho_idx) == 0:
            theta = (cssv[-1] - s) / len(v)
        else:
            rho = rho_idx[-1]
            theta = (cssv[rho] - s) / float(rho + 1)
        w = np.maximum(z - theta, 0.0)
        return w + eps

    def ensure_strict_interior(bary: np.ndarray, eps: float = 1e-6) -> np.ndarray:
        # Project every point to the interior simplex with margin eps
        out = np.empty_like(bary)
        for i in range(bary.shape[0]):
            out[i] = project_to_simplex_with_lower(bary[i], eps)
        return out

    def all_triangle_triples(n: int) -> List[Tuple[int, int, int]]:
        return list(itertools.combinations(range(n), 3))

    TRIPLES = all_triangle_triples(n)
    T_count = len(TRIPLES)
    # Triangle indices by point for incremental updates
    tris_by_point = [[] for _ in range(n)]
    for t_idx, (i, j, k) in enumerate(TRIPLES):
        tris_by_point[i].append(t_idx)
        tris_by_point[j].append(t_idx)
        tris_by_point[k].append(t_idx)
    tris_by_point = [np.array(lst, dtype=np.int32) for lst in tris_by_point]

    def compute_areas_from_points(P: np.ndarray) -> np.ndarray:
        """Return absolute areas normalized by BIG_AREA for all triples."""
        areas = np.empty(T_count, dtype=np.float64)
        idx = 0
        for (i, j, k) in TRIPLES:
            pi = P[i]
            pj = P[j]
            pk = P[k]
            s = (pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0])
            areas[idx] = 0.5 * abs(s) / BIG_AREA
            idx += 1
        return areas

    def compute_signed_areas(P: np.ndarray) -> np.ndarray:
        """Signed areas (not normalized). Useful for gradient sign."""
        s_areas = np.empty(T_count, dtype=np.float64)
        idx = 0
        for (i, j, k) in TRIPLES:
            pi = P[i]
            pj = P[j]
            pk = P[k]
            s = 0.5 * ((pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0]))
            s_areas[idx] = s
            idx += 1
        return s_areas

    def penalty_pairs(P: np.ndarray, d0: float = 0.012) -> float:
        """Soft penalty for pairs that are too close (to discourage duplicates)."""
        pen = 0.0
        for i in range(n):
            pi = P[i]
            for j in range(i + 1, n):
                d = np.linalg.norm(P[j] - pi)
                if d < d0:
                    pen += (d0 - d) ** 2
        return pen

    def objective_smoothed(areas: np.ndarray, K: int, alpha: float) -> Tuple[float, float, float]:
        """Return (F, min_area, mean_K_smallest) for current areas array."""
        # K smallest via partition
        if K >= len(areas):
            smallest = np.sort(areas)
        else:
            part = np.partition(areas, K - 1)[:K]
            smallest = np.sort(part)
        min_a = float(smallest[0])
        mean_k = float(np.mean(smallest))
        F = (1.0 - alpha) * min_a + alpha * mean_k
        return F, min_a, mean_k

    # Low-discrepancy Halton sequence utilities
    def van_der_corput(n: int, base: int) -> float:
        vdc, denom = 0.0, 1.0
        while n:
            n, remainder = divmod(n, base)
            denom *= base
            vdc += remainder / denom
        return vdc

    def halton_2d_sequence(count: int, start_index: int = 1, bases=(2, 3)) -> np.ndarray:
        pts = np.zeros((count, 2), dtype=np.float64)
        for i in range(count):
            pts[i, 0] = van_der_corput(start_index + i, bases[0])
            pts[i, 1] = van_der_corput(start_index + i, bases[1])
        return pts

    # Initializers (barycentric)
    rng = np.random.default_rng(12345)

    def init_uniform_barycentric(n: int, margin: float = 0.08) -> np.ndarray:
        # Sample uniform inside triangle via (u,v) and fold, then shrink towards center
        U = rng.random((n, 2), dtype=np.float64)
        # Fold
        mask = (U[:, 0] + U[:, 1]) > 1.0
        U[mask] = 1.0 - U[mask]
        a = 1.0 - U[:, 0] - U[:, 1]
        b = U[:, 0]
        c = U[:, 1]
        B = np.stack([a, b, c], axis=1)
        center = np.full((n, 3), 1.0 / 3.0)
        B = (1.0 - margin) * B + margin * center
        return ensure_strict_interior(B, eps=1e-5)

    def init_halton_barycentric(n: int, margin: float = 0.06, start_index: int = 3) -> np.ndarray:
        U = halton_2d_sequence(n, start_index=start_index)
        # Fold to triangle
        mask = (U[:, 0] + U[:, 1]) > 1.0
        U[mask] = 1.0 - U[mask]
        a = 1.0 - U[:, 0] - U[:, 1]
        b = U[:, 0]
        c = U[:, 1]
        B = np.stack([a, b, c], axis=1)
        center = np.full((n, 3), 1.0 / 3.0)
        B = (1.0 - margin) * B + margin * center
        return ensure_strict_interior(B, eps=1e-5)

    def init_nested_rings(n: int) -> np.ndarray:
        # Place 1 center + 5 on inner ring + 5 on outer ring
        # Barycentric center and small 2D oscillations within the simplex plane
        m1 = 5
        m2 = 5
        center = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0])
        # Two orthonormal basis vectors in the plane a+b+c=1:
        u = np.array([1.0, -1.0, 0.0]) / math.sqrt(2.0)
        v = np.array([1.0, 1.0, -2.0]) / math.sqrt(6.0)
        # Radii chosen to stay away from edges; will project anyway
        r1 = 0.18
        r2 = 0.28
        Bs = []
        Bs.append(center.copy())
        for k in range(m1):
            theta = 2.0 * math.pi * (k / m1)
            b = center + r1 * (math.cos(theta) * u + math.sin(theta) * v)
            Bs.append(b)
        for k in range(m2):
            theta = 2.0 * math.pi * ((k + 0.3) / m2)  # phase shift
            b = center + r2 * (math.cos(theta) * u + math.sin(theta) * v)
            Bs.append(b)
        B = np.vstack(Bs)
        if B.shape[0] > n:
            B = B[:n]
        elif B.shape[0] < n:
            extra = init_uniform_barycentric(n - B.shape[0])
            B = np.vstack([B, extra])
        return ensure_strict_interior(B, eps=1e-5)

    # Simulated annealing parameters
    alpha = 0.5  # smoothing between min and mean K smallest
    K_small = 10
    L_worst = 15  # for targeting point selection
    W_grad = 12  # worst triangles for gradient step
    eps_bary = 1e-6  # strict interior margin in barycentric
    pen_weight = 0.002  # weight for pairwise proximity penalty
    d0 = 0.012  # soft minimum pair distance
    # Steps and schedule
    islands = 20  # number of seeds
    iter_per_island = 40000  # SA moves per island
    intensify_every = 2000  # iterations between gradient intensification
    # Temperature schedule
    T0 = 0.015
    rho = 0.9985
    # Proposal step size for barycentric noise (adapted online)
    sigma_init = 0.07

    # Global best tracking
    global_best_points = None
    global_best_min_area = -1.0

    # Island loop (multi-start)
    for island_id in range(islands):
        # Initialize a seed configuration
        if island_id % 3 == 0:
            B = init_halton_barycentric(n, start_index=3 + 10 * island_id)
        elif island_id % 3 == 1:
            B = init_uniform_barycentric(n)
        else:
            B = init_nested_rings(n)

        # Tiny jitter to avoid symmetry traps
        B += rng.normal(0.0, 0.01, size=B.shape)
        B = ensure_strict_interior(B, eps=1e-5)
        P = bary_to_cart(B)

        areas = compute_areas_from_points(P)
        F_val, min_area, mean_k = objective_smoothed(areas, K_small, alpha)
        pen = penalty_pairs(P, d0=d0)
        F_val -= pen_weight * pen

        best_P_seed = P.copy()
        best_min_seed = float(min_area)

        # Precompute for incremental update: areas array and point->tri mapping ready
        T = T0
        sigma = sigma_init
        accept_count = 0
        window = 200  # for adaptive sigma
        # SA loop
        for it in range(iter_per_island):
            # Select a point to move, biased toward worst L triangles
            # Compute worst L triangle indices
            if L_worst >= T_count:
                worst_idx = np.argsort(areas)[:T_count]
            else:
                worst_part = np.argpartition(areas, L_worst - 1)[:L_worst]
                worst_idx = worst_part[np.argsort(areas[worst_part])]
            # Count involvement
            involvement = np.zeros(n, dtype=np.float64)
            for t_idx in worst_idx:
                i, j, k = TRIPLES[t_idx]
                involvement[i] += 1.0
                involvement[j] += 1.0
                involvement[k] += 1.0
            probs = involvement + 1.0  # baseline
            probs /= probs.sum()
            i_move = rng.choice(n, p=probs)

            # Propose a barycentric perturbation for point i_move
            b_old = B[i_move].copy()
            # Add Gaussian noise to 3 components, then project onto simplex with eps
            noise = rng.normal(0.0, sigma, size=3)
            b_new = b_old + noise
            b_new = project_to_simplex_with_lower(b_new, eps_bary)
            if not np.isfinite(b_new).all():
                b_new = b_old

            # Compute new cartesian position and update areas incrementally
            p_old = P[i_move].copy()
            # Correct computation using barycentric conversion for the single point
            p_new = (b_new @ TRI_VERTS)

            # Temporarily update
            B_tmp = b_new
            P_tmp = p_new
            # Update areas only for triangles involving i_move
            changed_tris = tris_by_point[i_move]
            areas_new = areas.copy()
            for t_idx in changed_tris:
                i, j, k = TRIPLES[t_idx]
                pi = P_tmp if i == i_move else P[i]
                pj = P_tmp if j == i_move else P[j]
                pk = P_tmp if k == i_move else P[k]
                s = (pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0])
                areas_new[t_idx] = 0.5 * abs(s) / BIG_AREA

            # New smoothed objective and penalty
            F_new, min_area_new, mean_k_new = objective_smoothed(areas_new, K_small, alpha)
            P_i_saved = P[i_move].copy()
            P[i_move] = P_tmp
            pen_new = penalty_pairs(P, d0=d0)
            P[i_move] = P_i_saved  # restore
            F_new -= pen_weight * pen_new

            dF = F_new - F_val
            if dF >= 0.0 or rng.random() < math.exp(dF / max(T, 1e-12)):
                # Accept
                B[i_move] = B_tmp
                P[i_move] = P_tmp
                areas = areas_new
                F_val = F_new
                min_area = min_area_new
                accept_count += 1

                # Update best seed result by true min area only (no smoothing)
                if min_area > best_min_seed + 1e-12:
                    best_min_seed = float(min_area)
                    best_P_seed = P.copy()

            # Temperature and sigma adaptation
            T *= rho
            if (it + 1) % window == 0:
                acc_rate = accept_count / window
                # Adjust sigma to keep acceptance in [0.25, 0.45]
                if acc_rate < 0.22:
                    sigma *= 0.8
                elif acc_rate > 0.5:
                    sigma *= 1.25
                sigma = min(max(sigma, 1e-4), 0.2)
                accept_count = 0

            # Intensification: gradient steps on worst triangles
            if (it + 1) % intensify_every == 0:
                # Tweaked initial step and substeps with backtracking line search
                gradient_intensification(P, B, areas, W=W_grad, step0=0.009, steps=4,
                                         TRI_VERTS=TRI_VERTS, eps_bary=eps_bary,
                                         BIG_AREA=BIG_AREA, TRIPLES=TRIPLES)

                # Re-evaluate objective after intensification
                areas = compute_areas_from_points(P)
                F_val, min_area, mean_k = objective_smoothed(areas, K_small, alpha)
                F_val -= pen_weight * penalty_pairs(P, d0=d0)
                if min_area > best_min_seed + 1e-12:
                    best_min_seed = float(min_area)
                    best_P_seed = P.copy()

        # Update global best
        if best_min_seed > global_best_min_area + 1e-12:
            global_best_min_area = best_min_seed
            global_best_points = best_P_seed.copy()

    # Final polish: tiny gradient steps starting from global best
    if global_best_points is None:
        # Fallback unlikely
        pts = bary_to_cart(init_uniform_barycentric(n)).tolist()
        return pts, compute_min_area(pts)

    P_best = global_best_points.copy()
    B_best = cart_to_bary(P_best)
    B_best = ensure_strict_interior(B_best, eps=1e-6)
    P_best = bary_to_cart(B_best)

    areas = compute_areas_from_points(P_best)
    gradient_intensification(P_best, B_best, areas, W=W_grad, step0=0.006, steps=4,
                             TRI_VERTS=TRI_VERTS, eps_bary=eps_bary,
                             BIG_AREA=BIG_AREA, TRIPLES=TRIPLES)
    areas = compute_areas_from_points(P_best)
    min_area_final = float(np.min(areas))

    # Return as plain Python lists
    return P_best.tolist(), min_area_final


def gradient_intensification(P: np.ndarray,
                             B: np.ndarray,
                             areas: np.ndarray,
                             W: int,
                             step0: float,
                             steps: int,
                             TRI_VERTS: np.ndarray,
                             eps_bary: float,
                             BIG_AREA: float,
                             TRIPLES: List[Tuple[int, int, int]]) -> None:
    """Push vertices of the W worst triangles along gradients to increase area.

    This variant uses a monotone backtracking line search per substep:
    - Compute aggregated gradients from the W worst triangles.
    - Attempt a projected step of size step0 * (0.5**s_step).
      If the true global min area decreases, halve the step and retry up to 5 times.
      Accept the first non-decreasing step and proceed to the next substep.
    The function updates P, B, and areas in-place on accepted steps.
    """
    n = P.shape[0]

    def compute_all_areas_norm(Q: np.ndarray) -> np.ndarray:
        arr = np.empty(len(TRIPLES), dtype=np.float64)
        for idx, (i, j, k) in enumerate(TRIPLES):
            pi = Q[i]
            pj = Q[j]
            pk = Q[k]
            s = (pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0])
            arr[idx] = 0.5 * abs(s) / BIG_AREA
        return arr

    for s_step in range(steps):
        # Identify W worst triangles based on current areas
        T_idx_sorted = np.argsort(areas)
        worst_idx = T_idx_sorted[:min(W, len(T_idx_sorted))]

        # Accumulate gradients per point from worst triangles
        grads = np.zeros_like(P)
        for t_idx in worst_idx:
            i, j, k = TRIPLES[t_idx]
            pi, pj, pk = P[i], P[j], P[k]
            s = 0.5 * ((pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0]))
            sign = 1.0 if s >= 0 else -1.0
            # grad_p s = 0.5 * perp(q - r)
            qmr = pj - pk
            rmq = pk - pi
            pmq = pi - pj
            grads[i] += 0.5 * sign * np.array([qmr[1], -qmr[0]])
            grads[j] += 0.5 * sign * np.array([rmq[1], -rmq[0]])
            grads[k] += 0.5 * sign * np.array([pmq[1], -pmq[0]])

        if not np.all(np.isfinite(grads)) or np.linalg.norm(grads) < 1e-18:
            continue

        base_step = step0 * (0.5 ** s_step)
        if base_step <= 0:
            continue

        min_current = float(np.min(areas))
        accepted = False

        # Backtracking line search: halve up to 5 times on decrease
        step_trial = base_step
        for _bt in range(5):
            # Trial positions by stepping along grads and projecting back to simplex
            P_trial = P + step_trial * grads
            # Convert to barycentric with projection to maintain strict interior
            B_trial = np.empty_like(B)
            Q = np.empty_like(P_trial)
            for i in range(n):
                b = cart_to_bary_single(P_trial[i], TRI_VERTS)
                b = project_to_simplex_with_lower_single(b, eps_bary)
                B_trial[i] = b
                Q[i] = b @ TRI_VERTS

            # Evaluate global min area after the trial
            areas_trial = compute_all_areas_norm(Q)
            min_trial = float(np.min(areas_trial))
            if min_trial + 1e-12 >= min_current:
                # Accept this trial
                P[:] = Q
                B[:] = B_trial
                areas[:] = areas_trial
                accepted = True
                break
            else:
                step_trial *= 0.5

        # Proceed to next substep regardless; if not accepted, we keep current P,B,areas

def cart_to_bary_single(p: np.ndarray, TRI_VERTS: np.ndarray) -> np.ndarray:
    """Convert single Cartesian point to barycentric for triangle TRI_VERTS."""
    A = TRI_VERTS[0]
    Bv = TRI_VERTS[1]
    Cv = TRI_VERTS[2]
    v0 = Bv - A
    v1 = Cv - A
    v2 = p - A
    M = np.stack([v0, v1], axis=1)  # 2x2
    Minv = np.linalg.inv(M)
    uv = Minv @ v2
    u, v = float(uv[0]), float(uv[1])
    a = 1.0 - u - v
    b = u
    c = v
    return np.array([a, b, c], dtype=np.float64)


def project_to_simplex_with_lower_single(y: np.ndarray, eps: float) -> np.ndarray:
    """Project a length-3 vector onto simplex {x>=eps, sum x = 1}."""
    z = y - eps
    s = 1.0 - 3.0 * eps
    v = np.sort(z)[::-1]
    cssv = np.cumsum(v)
    rho_idx = np.nonzero(v - (cssv - s) / (np.arange(1, len(v) + 1)) > 0)[0]
    if len(rho_idx) == 0:
        theta = (cssv[-1] - s) / len(v)
    else:
        rho = rho_idx[-1]
        theta = (cssv[rho] - s) / float(rho + 1)
    w = np.maximum(z - theta, 0.0)
    return w + eps


def compute_areas_from_points_single(P: np.ndarray, BIG_AREA: float) -> np.ndarray:
    """Compute all triangle areas for given points P, normalized."""
    n = P.shape[0]
    triples = list(itertools.combinations(range(n), 3))
    areas = np.empty(len(triples), dtype=np.float64)
    idx = 0
    for (i, j, k) in triples:
        pi = P[i]
        pj = P[j]
        pk = P[k]
        s = (pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0])
        areas[idx] = 0.5 * abs(s) / BIG_AREA
        idx += 1
    return areas


def compute_min_area_plain(pts: List[List[float]]) -> float:
    """Compute normalized min area among triples for a Python list of points."""
    A_big = math.sqrt(3.0) / 4.0
    min_area = float("inf")
    for (i, j, k) in itertools.combinations(range(len(pts)), 3):
        x1, y1 = pts[i]
        x2, y2 = pts[j]
        x3, y3 = pts[k]
        s = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
        a = 0.5 * abs(s)
        if a < min_area:
            min_area = a
    return min_area / A_big


def compute_min_area(pts: List[List[float]]) -> float:
    return compute_min_area_plain(pts)
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```

```python
#!/usr/bin/env python3
"""Heuristic solver for the Heilbronn problem in an equilateral triangle.

This implements a projected simulated annealing with multi-start and
geometry-aware intensification in barycentric coordinates. It aims to
maximize the minimum triangle area among n points strictly inside the
unit-side equilateral triangle with vertices:
A=(0,0), B=(1,0), C=(0.5, sqrt(3)/2)

Entry point: run_search_point(n)
"""

import json
import itertools
import math
import random
from typing import List, Tuple

# Optional NumPy for efficient array ops
try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


# EVOLVE_START
def run_search_point(n=11):
    """Entry point for evaluator. Returns (points, min_area).

    points: list of [x, y] strictly inside the target triangle
    min_area: smallest triangle area normalized by the big triangle area
    """
    if n != 11:
        # The provided heuristic and tuning is for n=11
        raise ValueError("the benchmark evaluates n=11")

    # Solve using our search algorithm
    points, min_area = find_best_placement(n)
    return points, min_area


def find_best_placement(n: int) -> Tuple[List[List[float]], float]:
    """Main search routine combining multi-start SA and intensification."""
    assert n == 11
    if np is None:
        # Fallback to a simple random configuration if NumPy is not available
        # (should not happen in the judge).
        generator = random.Random(42)
        pts = []
        for _ in range(n):
            u = generator.random()
            v = generator.random()
            if u + v > 1.0:
                u, v = 1.0 - u, 1.0 - v
            x = u + 0.5 * v
            y = math.sqrt(3.0) * 0.5 * v
            pts.append([x, y])
        min_area = compute_min_area_plain(pts)
        return pts, min_area

    # Geometry constants
    A = np.array([0.0, 0.0], dtype=np.float64)
    B = np.array([1.0, 0.0], dtype=np.float64)
    C = np.array([0.5, math.sqrt(3.0) * 0.5], dtype=np.float64)
    TRI_VERTS = np.stack([A, B, C], axis=0)
    BIG_AREA = math.sqrt(3.0) / 4.0  # area of unit-side equilateral triangle

    # Precompute linear mapping for barycentric/cartesian transforms
    # For P = A + u*(B-A) + v*(C-A). Then barycentric (a,b,c) = (1-u-v, u, v)
    M = np.stack([B - A, C - A], axis=1)  # 2x2
    Minv = np.linalg.inv(M)

    # Helper functions
    def bary_to_cart(bary: np.ndarray) -> np.ndarray:
        # bary shape (n,3)
        return bary @ TRI_VERTS  # linear combination

    def cart_to_bary(P: np.ndarray) -> np.ndarray:
        # P shape (n,2) or (2,)
        # Compute (u,v) for P = A + u*(B-A) + v*(C-A)
        if P.ndim == 1:
            v2 = P - A
            uv = Minv @ v2
            u, v = uv[0], uv[1]
            a, b, c = 1.0 - u - v, u, v
            return np.array([a, b, c], dtype=np.float64)
        else:
            V2 = (P - A).T  # 2 x n
            UV = Minv @ V2  # 2 x n
            u = UV[0, :]
            v = UV[1, :]
            a = 1.0 - u - v
            b = u
            c = v
            return np.stack([a, b, c], axis=1)

    def project_to_simplex_with_lower(y: np.ndarray, eps: float) -> np.ndarray:
        # Project vector y (len=3) to the set {x >= eps, sum x = 1}
        z = y - eps
        s = 1.0 - 3.0 * eps
        v = np.sort(z)[::-1]
        cssv = np.cumsum(v)
        rho_idx = np.nonzero(v - (cssv - s) / (np.arange(1, len(v) + 1)) > 0)[0]
        if len(rho_idx) == 0:
            theta = (cssv[-1] - s) / len(v)
        else:
            rho = rho_idx[-1]
            theta = (cssv[rho] - s) / float(rho + 1)
        w = np.maximum(z - theta, 0.0)
        return w + eps

    def ensure_strict_interior(bary: np.ndarray, eps: float = 1e-6) -> np.ndarray:
        # Project every point to the interior simplex with margin eps
        out = np.empty_like(bary)
        for i in range(bary.shape[0]):
            out[i] = project_to_simplex_with_lower(bary[i], eps)
        return out

    def all_triangle_triples(n: int) -> List[Tuple[int, int, int]]:
        return list(itertools.combinations(range(n), 3))

    TRIPLES = all_triangle_triples(n)
    T_count = len(TRIPLES)
    # Triangle indices by point for incremental updates
    tris_by_point = [[] for _ in range(n)]
    for t_idx, (i, j, k) in enumerate(TRIPLES):
        tris_by_point[i].append(t_idx)
        tris_by_point[j].append(t_idx)
        tris_by_point[k].append(t_idx)
    tris_by_point = [np.array(lst, dtype=np.int32) for lst in tris_by_point]

    def compute_areas_from_points(P: np.ndarray) -> np.ndarray:
        """Return absolute areas normalized by BIG_AREA for all triples."""
        areas = np.empty(T_count, dtype=np.float64)
        idx = 0
        for (i, j, k) in TRIPLES:
            pi = P[i]
            pj = P[j]
            pk = P[k]
            s = (pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0])
            areas[idx] = 0.5 * abs(s) / BIG_AREA
            idx += 1
        return areas

    def compute_signed_areas(P: np.ndarray) -> np.ndarray:
        """Signed areas (not normalized). Useful for gradient sign."""
        s_areas = np.empty(T_count, dtype=np.float64)
        idx = 0
        for (i, j, k) in TRIPLES:
            pi = P[i]
            pj = P[j]
            pk = P[k]
            s = 0.5 * ((pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0]))
            s_areas[idx] = s
            idx += 1
        return s_areas

    def penalty_pairs(P: np.ndarray, d0: float = 0.012) -> float:
        """Soft penalty for pairs that are too close (to discourage duplicates)."""
        pen = 0.0
        for i in range(n):
            pi = P[i]
            for j in range(i + 1, n):
                d = np.linalg.norm(P[j] - pi)
                if d < d0:
                    pen += (d0 - d) ** 2
        return pen

    def objective_smoothed(areas: np.ndarray, K: int, alpha: float) -> Tuple[float, float, float]:
        """Return (F, min_area, mean_K_smallest) for current areas array."""
        # K smallest via partition
        if K >= len(areas):
            smallest = np.sort(areas)
        else:
            part = np.partition(areas, K - 1)[:K]
            smallest = np.sort(part)
        min_a = float(smallest[0])
        mean_k = float(np.mean(smallest))
        F = (1.0 - alpha) * min_a + alpha * mean_k
        return F, min_a, mean_k

    # Low-discrepancy Halton sequence utilities
    def van_der_corput(n: int, base: int) -> float:
        vdc, denom = 0.0, 1.0
        while n:
            n, remainder = divmod(n, base)
            denom *= base
            vdc += remainder / denom
        return vdc

    def halton_2d_sequence(count: int, start_index: int = 1, bases=(2, 3)) -> np.ndarray:
        pts = np.zeros((count, 2), dtype=np.float64)
        for i in range(count):
            pts[i, 0] = van_der_corput(start_index + i, bases[0])
            pts[i, 1] = van_der_corput(start_index + i, bases[1])
        return pts

    # Initializers (barycentric)
    rng = np.random.default_rng(12345)

    def init_uniform_barycentric(n: int, margin: float = 0.08) -> np.ndarray:
        # Sample uniform inside triangle via (u,v) and fold, then shrink towards center
        U = rng.random((n, 2), dtype=np.float64)
        # Fold
        mask = (U[:, 0] + U[:, 1]) > 1.0
        U[mask] = 1.0 - U[mask]
        a = 1.0 - U[:, 0] - U[:, 1]
        b = U[:, 0]
        c = U[:, 1]
        B = np.stack([a, b, c], axis=1)
        center = np.full((n, 3), 1.0 / 3.0)
        B = (1.0 - margin) * B + margin * center
        return ensure_strict_interior(B, eps=1e-5)

    def init_halton_barycentric(n: int, margin: float = 0.06, start_index: int = 3) -> np.ndarray:
        U = halton_2d_sequence(n, start_index=start_index)
        # Fold to triangle
        mask = (U[:, 0] + U[:, 1]) > 1.0
        U[mask] = 1.0 - U[mask]
        a = 1.0 - U[:, 0] - U[:, 1]
        b = U[:, 0]
        c = U[:, 1]
        B = np.stack([a, b, c], axis=1)
        center = np.full((n, 3), 1.0 / 3.0)
        B = (1.0 - margin) * B + margin * center
        return ensure_strict_interior(B, eps=1e-5)

    def init_nested_rings(n: int) -> np.ndarray:
        # Place 1 center + 5 on inner ring + 5 on outer ring
        # Barycentric center and small 2D oscillations within the simplex plane
        m1 = 5
        m2 = 5
        center = np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0])
        # Two orthonormal basis vectors in the plane a+b+c=1:
        u = np.array([1.0, -1.0, 0.0]) / math.sqrt(2.0)
        v = np.array([1.0, 1.0, -2.0]) / math.sqrt(6.0)
        # Radii chosen to stay away from edges; will project anyway
        r1 = 0.18
        r2 = 0.28
        Bs = []
        Bs.append(center.copy())
        for k in range(m1):
            theta = 2.0 * math.pi * (k / m1)
            b = center + r1 * (math.cos(theta) * u + math.sin(theta) * v)
            Bs.append(b)
        for k in range(m2):
            theta = 2.0 * math.pi * ((k + 0.3) / m2)  # phase shift
            b = center + r2 * (math.cos(theta) * u + math.sin(theta) * v)
            Bs.append(b)
        B = np.vstack(Bs)
        if B.shape[0] > n:
            B = B[:n]
        elif B.shape[0] < n:
            extra = init_uniform_barycentric(n - B.shape[0])
            B = np.vstack([B, extra])
        return ensure_strict_interior(B, eps=1e-5)

    # Simulated annealing parameters
    alpha = 0.5  # smoothing between min and mean K smallest
    K_small = 10
    L_worst = 15  # for targeting point selection
    W_grad = 12  # worst triangles for gradient step
    eps_bary = 1e-6  # strict interior margin in barycentric
    pen_weight = 0.002  # weight for pairwise proximity penalty
    d0 = 0.012  # soft minimum pair distance
    # Steps and schedule
    islands = 20  # number of seeds
    iter_per_island = 40000  # SA moves per island
    intensify_every = 2000  # iterations between gradient intensification
    # Temperature schedule
    T0 = 0.015
    rho = 0.9985
    # Proposal step size for barycentric noise (adapted online)
    sigma_init = 0.07

    # Global best tracking
    global_best_points = None
    global_best_min_area = -1.0

    # Island loop (multi-start)
    for island_id in range(islands):
        # Initialize a seed configuration
        if island_id % 3 == 0:
            B = init_halton_barycentric(n, start_index=3 + 10 * island_id)
        elif island_id % 3 == 1:
            B = init_uniform_barycentric(n)
        else:
            B = init_nested_rings(n)

        # Tiny jitter to avoid symmetry traps
        B += rng.normal(0.0, 0.01, size=B.shape)
        B = ensure_strict_interior(B, eps=1e-5)
        P = bary_to_cart(B)

        areas = compute_areas_from_points(P)
        F_val, min_area, mean_k = objective_smoothed(areas, K_small, alpha)
        pen = penalty_pairs(P, d0=d0)
        F_val -= pen_weight * pen

        best_P_seed = P.copy()
        best_min_seed = float(min_area)

        # Precompute for incremental update: areas array and point->tri mapping ready
        T = T0
        sigma = sigma_init
        accept_count = 0
        window = 200  # for adaptive sigma
        # SA loop
        for it in range(iter_per_island):
            # Select a point to move, biased toward worst L triangles
            # Compute worst L triangle indices
            if L_worst >= T_count:
                worst_idx = np.argsort(areas)[:T_count]
            else:
                worst_part = np.argpartition(areas, L_worst - 1)[:L_worst]
                worst_idx = worst_part[np.argsort(areas[worst_part])]
            # Count involvement
            involvement = np.zeros(n, dtype=np.float64)
            for t_idx in worst_idx:
                i, j, k = TRIPLES[t_idx]
                involvement[i] += 1.0
                involvement[j] += 1.0
                involvement[k] += 1.0
            probs = involvement + 1.0  # baseline
            probs /= probs.sum()
            i_move = rng.choice(n, p=probs)

            # Propose a barycentric perturbation for point i_move
            b_old = B[i_move].copy()
            # Add Gaussian noise to 3 components, then project onto simplex with eps
            noise = rng.normal(0.0, sigma, size=3)
            b_new = b_old + noise
            b_new = project_to_simplex_with_lower(b_new, eps_bary)
            if not np.isfinite(b_new).all():
                b_new = b_old

            # Compute new cartesian position and update areas incrementally
            p_old = P[i_move].copy()
            # Correct computation using barycentric conversion for the single point
            p_new = (b_new @ TRI_VERTS)

            # Temporarily update
            B_tmp = b_new
            P_tmp = p_new
            # Update areas only for triangles involving i_move
            changed_tris = tris_by_point[i_move]
            areas_new = areas.copy()
            for t_idx in changed_tris:
                i, j, k = TRIPLES[t_idx]
                pi = P_tmp if i == i_move else P[i]
                pj = P_tmp if j == i_move else P[j]
                pk = P_tmp if k == i_move else P[k]
                s = (pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0])
                areas_new[t_idx] = 0.5 * abs(s) / BIG_AREA

            # New smoothed objective and penalty
            F_new, min_area_new, mean_k_new = objective_smoothed(areas_new, K_small, alpha)
            P_i_saved = P[i_move].copy()
            P[i_move] = P_tmp
            pen_new = penalty_pairs(P, d0=d0)
            P[i_move] = P_i_saved  # restore
            F_new -= pen_weight * pen_new

            dF = F_new - F_val
            if dF >= 0.0 or rng.random() < math.exp(dF / max(T, 1e-12)):
                # Accept
                B[i_move] = B_tmp
                P[i_move] = P_tmp
                areas = areas_new
                F_val = F_new
                min_area = min_area_new
                accept_count += 1

                # Update best seed result by true min area only (no smoothing)
                if min_area > best_min_seed + 1e-12:
                    best_min_seed = float(min_area)
                    best_P_seed = P.copy()

            # Temperature and sigma adaptation
            T *= rho
            if (it + 1) % window == 0:
                acc_rate = accept_count / window
                # Adjust sigma to keep acceptance in [0.25, 0.45]
                if acc_rate < 0.22:
                    sigma *= 0.8
                elif acc_rate > 0.5:
                    sigma *= 1.25
                sigma = min(max(sigma, 1e-4), 0.2)
                accept_count = 0

            # Intensification: gradient steps on worst triangles
            if (it + 1) % intensify_every == 0:
                # Tweaked initial step and substeps with backtracking line search
                gradient_intensification(P, B, areas, W=W_grad, step0=0.009, steps=4,
                                         TRI_VERTS=TRI_VERTS, eps_bary=eps_bary,
                                         BIG_AREA=BIG_AREA, TRIPLES=TRIPLES)

                # Re-evaluate objective after intensification
                areas = compute_areas_from_points(P)
                F_val, min_area, mean_k = objective_smoothed(areas, K_small, alpha)
                F_val -= pen_weight * penalty_pairs(P, d0=d0)
                if min_area > best_min_seed + 1e-12:
                    best_min_seed = float(min_area)
                    best_P_seed = P.copy()

        # Update global best
        if best_min_seed > global_best_min_area + 1e-12:
            global_best_min_area = best_min_seed
            global_best_points = best_P_seed.copy()

    # Final polish: tiny gradient steps starting from global best
    if global_best_points is None:
        # Fallback unlikely
        pts = bary_to_cart(init_uniform_barycentric(n)).tolist()
        return pts, compute_min_area(pts)

    P_best = global_best_points.copy()
    B_best = cart_to_bary(P_best)
    B_best = ensure_strict_interior(B_best, eps=1e-6)
    P_best = bary_to_cart(B_best)

    areas = compute_areas_from_points(P_best)
    gradient_intensification(P_best, B_best, areas, W=W_grad, step0=0.006, steps=4,
                             TRI_VERTS=TRI_VERTS, eps_bary=eps_bary,
                             BIG_AREA=BIG_AREA, TRIPLES=TRIPLES)
    areas = compute_areas_from_points(P_best)
    min_area_final = float(np.min(areas))

    # Return as plain Python lists
    return P_best.tolist(), min_area_final


def gradient_intensification(P: np.ndarray,
                             B: np.ndarray,
                             areas: np.ndarray,
                             W: int,
                             step0: float,
                             steps: int,
                             TRI_VERTS: np.ndarray,
                             eps_bary: float,
                             BIG_AREA: float,
                             TRIPLES: List[Tuple[int, int, int]]) -> None:
    """Push vertices of the W worst triangles along gradients to increase area.

    This variant uses a monotone backtracking line search per substep:
    - Compute aggregated gradients from the W worst triangles.
    - Attempt a projected step of size step0 * (0.5**s_step).
      If the true global min area decreases, halve the step and retry up to 5 times.
      Accept the first non-decreasing step and proceed to the next substep.
    The function updates P, B, and areas in-place on accepted steps.
    """
    n = P.shape[0]

    def compute_all_areas_norm(Q: np.ndarray) -> np.ndarray:
        arr = np.empty(len(TRIPLES), dtype=np.float64)
        for idx, (i, j, k) in enumerate(TRIPLES):
            pi = Q[i]
            pj = Q[j]
            pk = Q[k]
            s = (pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0])
            arr[idx] = 0.5 * abs(s) / BIG_AREA
        return arr

    for s_step in range(steps):
        # Identify W worst triangles based on current areas
        T_idx_sorted = np.argsort(areas)
        worst_idx = T_idx_sorted[:min(W, len(T_idx_sorted))]

        # Accumulate gradients per point from worst triangles
        grads = np.zeros_like(P)
        for t_idx in worst_idx:
            i, j, k = TRIPLES[t_idx]
            pi, pj, pk = P[i], P[j], P[k]
            s = 0.5 * ((pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0]))
            sign = 1.0 if s >= 0 else -1.0
            # grad_p s = 0.5 * perp(q - r)
            qmr = pj - pk
            rmq = pk - pi
            pmq = pi - pj
            grads[i] += 0.5 * sign * np.array([qmr[1], -qmr[0]])
            grads[j] += 0.5 * sign * np.array([rmq[1], -rmq[0]])
            grads[k] += 0.5 * sign * np.array([pmq[1], -pmq[0]])

        if not np.all(np.isfinite(grads)) or np.linalg.norm(grads) < 1e-18:
            continue

        base_step = step0 * (0.5 ** s_step)
        if base_step <= 0:
            continue

        min_current = float(np.min(areas))
        accepted = False

        # Backtracking line search: halve up to 5 times on decrease
        step_trial = base_step
        for _bt in range(5):
            # Trial positions by stepping along grads and projecting back to simplex
            P_trial = P + step_trial * grads
            # Convert to barycentric with projection to maintain strict interior
            B_trial = np.empty_like(B)
            Q = np.empty_like(P_trial)
            for i in range(n):
                b = cart_to_bary_single(P_trial[i], TRI_VERTS)
                b = project_to_simplex_with_lower_single(b, eps_bary)
                B_trial[i] = b
                Q[i] = b @ TRI_VERTS

            # Evaluate global min area after the trial
            areas_trial = compute_all_areas_norm(Q)
            min_trial = float(np.min(areas_trial))
            if min_trial + 1e-12 >= min_current:
                # Accept this trial
                P[:] = Q
                B[:] = B_trial
                areas[:] = areas_trial
                accepted = True
                break
            else:
                step_trial *= 0.5

        # Proceed to next substep regardless; if not accepted, we keep current P,B,areas

def cart_to_bary_single(p: np.ndarray, TRI_VERTS: np.ndarray) -> np.ndarray:
    """Convert single Cartesian point to barycentric for triangle TRI_VERTS."""
    A = TRI_VERTS[0]
    Bv = TRI_VERTS[1]
    Cv = TRI_VERTS[2]
    v0 = Bv - A
    v1 = Cv - A
    v2 = p - A
    M = np.stack([v0, v1], axis=1)  # 2x2
    Minv = np.linalg.inv(M)
    uv = Minv @ v2
    u, v = float(uv[0]), float(uv[1])
    a = 1.0 - u - v
    b = u
    c = v
    return np.array([a, b, c], dtype=np.float64)


def project_to_simplex_with_lower_single(y: np.ndarray, eps: float) -> np.ndarray:
    """Project a length-3 vector onto simplex {x>=eps, sum x = 1}."""
    z = y - eps
    s = 1.0 - 3.0 * eps
    v = np.sort(z)[::-1]
    cssv = np.cumsum(v)
    rho_idx = np.nonzero(v - (cssv - s) / (np.arange(1, len(v) + 1)) > 0)[0]
    if len(rho_idx) == 0:
        theta = (cssv[-1] - s) / len(v)
    else:
        rho = rho_idx[-1]
        theta = (cssv[rho] - s) / float(rho + 1)
    w = np.maximum(z - theta, 0.0)
    return w + eps


def compute_areas_from_points_single(P: np.ndarray, BIG_AREA: float) -> np.ndarray:
    """Compute all triangle areas for given points P, normalized."""
    n = P.shape[0]
    triples = list(itertools.combinations(range(n), 3))
    areas = np.empty(len(triples), dtype=np.float64)
    idx = 0
    for (i, j, k) in triples:
        pi = P[i]
        pj = P[j]
        pk = P[k]
        s = (pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0])
        areas[idx] = 0.5 * abs(s) / BIG_AREA
        idx += 1
    return areas


def compute_min_area_plain(pts: List[List[float]]) -> float:
    """Compute normalized min area among triples for a Python list of points."""
    A_big = math.sqrt(3.0) / 4.0
    min_area = float("inf")
    for (i, j, k) in itertools.combinations(range(len(pts)), 3):
        x1, y1 = pts[i]
        x2, y2 = pts[j]
        x3, y3 = pts[k]
        s = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
        a = 0.5 * abs(s)
        if a < min_area:
            min_area = a
    return min_area / A_big


def compute_min_area(pts: List[List[float]]) -> float:
    return compute_min_area_plain(pts)
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```
