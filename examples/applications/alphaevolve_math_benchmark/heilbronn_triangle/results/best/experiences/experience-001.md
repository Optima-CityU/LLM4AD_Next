Use a guided-proposal branch inside SA that steers a moved point along the aggregated area-increasing gradient of its current worst triangles, while preserving feasibility via barycentric/simplex projection and retaining random proposals for exploration.

- SA with Gradient-Biased Proposals: In each simulated annealing iteration, the algorithm switches to a gradient-biased proposal with probability about 0.4, moving the selected point along the normalized aggregated area-increasing gradient from the worst triangles it participates in with step size step_g = kappa * sigma where kappa is about 1.2, then projects the trial to the interior barycentric simplex and maps back to Cartesian for evaluation.
- SA with Gradient-Biased Proposals: If the gradient is tiny or NaN, the proposal falls back to the existing random barycentric perturbation, and acceptance uses the existing smoothed objective and SA rule while leaving incremental area updates, penalties, temperature and sigma adaptation, and periodic intensification unchanged.
- SA with Gradient-Biased Proposals: On n=11, the run achieved min_area 0.035700439196802235 with validity 1.0, target_ratio 0.9780942245699243, and eval_time 178.392959601013.

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
    Bv = np.array([1.0, 0.0], dtype=np.float64)
    Cv = np.array([0.5, math.sqrt(3.0) * 0.5], dtype=np.float64)
    TRI_VERTS = np.stack([A, Bv, Cv], axis=0)
    BIG_AREA = math.sqrt(3.0) / 4.0  # area of unit-side equilateral triangle

    # Precompute linear mapping for barycentric/cartesian transforms
    # For P = A + u*(B-A) + v*(C-A). Then barycentric (a,b,c) = (1-u-v, u, v)
    M = np.stack([Bv - A, Cv - A], axis=1)  # 2x2
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
    # Guided proposal hyperparameters
    p_guided = 0.4
    kappa = 1.2

    # Global best tracking
    global_best_points = None
    global_best_min_area = -1.0

    # Helper: compute aggregate ascent gradient for a point over a subset of triangles
    def compute_point_gradient_from_worst(i_move: int, worst_idx: np.ndarray, P: np.ndarray) -> np.ndarray:
        """Aggregate gradient of |area| wrt point i_move over triangles in worst_idx that involve it."""
        g = np.zeros(2, dtype=np.float64)
        has = False
        for t_idx in worst_idx:
            i, j, k = TRIPLES[t_idx]
            if i_move != i and i_move != j and i_move != k:
                continue
            pi, pj, pk = P[i], P[j], P[k]
            # Signed area s = 0.5 * cross(pj - pi, pk - pi)
            s = 0.5 * ((pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0]))
            sign = 1.0 if s >= 0.0 else -1.0
            if i_move == i:
                qmr = pj - pk
                g += 0.5 * sign * np.array([qmr[1], -qmr[0]])
                has = True
            elif i_move == j:
                rmq = pk - pi
                g += 0.5 * sign * np.array([rmq[1], -rmq[0]])
                has = True
            else:  # i_move == k
                pmq = pi - pj
                g += 0.5 * sign * np.array([pmq[1], -pmq[0]])
                has = True
        if not has:
            return np.zeros(2, dtype=np.float64)
        return g

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

            # Propose a move for point i_move: guided by gradient with prob p_guided, else random barycentric
            b_old = B[i_move].copy()
            guided = (rng.random() < p_guided)
            b_new = None

            if guided:
                # Compute gradient aggregated over worst triangles involving i_move
                g = compute_point_gradient_from_worst(i_move, worst_idx, P)
                g_norm = float(np.linalg.norm(g))
                if not np.isfinite(g_norm) or g_norm < 1e-16:
                    guided = False  # fallback to random
                else:
                    # Gradient-ascent step in Cartesian, then project back to interior simplex
                    g_dir = g / g_norm
                    step_g = kappa * sigma
                    p_old = P[i_move].copy()
                    p_trial = p_old + step_g * g_dir
                    # Map to barycentric and project to strictly interior
                    try:
                        b_trial = cart_to_bary_single(p_trial, TRI_VERTS)
                        b_trial = project_to_simplex_with_lower_single(b_trial, eps_bary)
                        b_new = b_trial
                    except Exception:
                        guided = False  # fallback
            if not guided or b_new is None:
                # Random barycentric Gaussian proposal with projection
                noise = rng.normal(0.0, sigma, size=3)
                b_rand = b_old + noise
                b_new = project_to_simplex_with_lower(b_rand, eps_bary)
                if not np.isfinite(b_new).all():
                    b_new = b_old  # degenerate fallback (no move)

            # Compute new cartesian position and update areas incrementally
            p_new = (b_new @ TRI_VERTS)

            # Update areas only for triangles involving i_move
            changed_tris = tris_by_point[i_move]
            areas_new = areas.copy()
            for t_idx in changed_tris:
                i, j, k = TRIPLES[t_idx]
                pi = p_new if i == i_move else P[i]
                pj = p_new if j == i_move else P[j]
                pk = p_new if k == i_move else P[k]
                s = (pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0])
                areas_new[t_idx] = 0.5 * abs(s) / BIG_AREA

            # New smoothed objective and penalty
            F_new, min_area_new, mean_k_new = objective_smoothed(areas_new, K_small, alpha)
            P_i_saved = P[i_move].copy()
            P[i_move] = p_new
            pen_new = penalty_pairs(P, d0=d0)
            P[i_move] = P_i_saved  # restore
            F_new -= pen_weight * pen_new

            dF = F_new - F_val
            if dF >= 0.0 or rng.random() < math.exp(dF / max(T, 1e-12)):
                # Accept
                B[i_move] = b_new
                P[i_move] = p_new
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
                gradient_intensification(P, B, areas, W=W_grad, step0=0.010, steps=3,
                                         TRI_VERTS=TRI_VERTS, eps_bary=eps_bary,
                                         BIG_AREA=BIG_AREA)

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
                             BIG_AREA=BIG_AREA)
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
                             BIG_AREA: float) -> None:
    """Push vertices of the W worst triangles along gradients to increase area."""
    n = P.shape[0]
    T_idx_sorted = np.argsort(areas)
    worst_idx = T_idx_sorted[:min(W, len(T_idx_sorted))]

    # Compute signed areas to obtain gradient direction
    # s = 0.5 * cross(q - p, r - p)
    signed = []
    for t_idx in worst_idx:
        i, j, k = list(itertools.combinations(range(n), 3))[t_idx]
        pi, pj, pk = P[i], P[j], P[k]
        s = 0.5 * ((pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0]))
        signed.append((t_idx, s))
    # Accumulate gradients per point
    for s_step in range(steps):
        grads = np.zeros_like(P)
        # Recompute worst triangles based on current areas each substep
        T_idx_sorted = np.argsort(areas)
        worst_idx = T_idx_sorted[:min(W, len(T_idx_sorted))]
        for t_idx in worst_idx:
            # Map triangle index to tuple
            i, j, k = list(itertools.combinations(range(n), 3))[t_idx]
            pi, pj, pk = P[i], P[j], P[k]
            s = 0.5 * ((pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0]))
            sign = 1.0 if s >= 0 else -1.0
            # Gradient wrt p, q, r for |s| is sign * grad(s)
            # grad_p s = 0.5 * perp(q - r)
            qmr = pj - pk
            rmq = pk - pi
            pmq = pi - pj
            grads[i] += 0.5 * sign * np.array([qmr[1], -qmr[0]])
            grads[j] += 0.5 * sign * np.array([rmq[1], -rmq[0]])
            grads[k] += 0.5 * sign * np.array([pmq[1], -pmq[0]])

        # Take a step and project back to barycentric domain
        step = step0 * (0.5 ** s_step)
        if step <= 0:
            break
        if np.linalg.norm(grads) < 1e-18:
            break

        P_old = P.copy()
        B_old = B.copy()
        P_new = P + step * grads
        # Project each moved point back via barycentric conversion and projection
        for i in range(n):
            b = cart_to_bary_single(P_new[i], TRI_VERTS)
            b = project_to_simplex_with_lower_single(b, eps_bary)
            B[i] = b
            P[i] = b @ TRI_VERTS

        # Accept only if min area does not decrease
        areas_new = compute_areas_from_points_single(P, BIG_AREA)
        if float(np.min(areas_new)) + 1e-12 < float(np.min(areas)):
            # Revert if worse
            P[:] = P_old
            B[:] = B_old
            break
        else:
            areas[:] = areas_new


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
    Bv = np.array([1.0, 0.0], dtype=np.float64)
    Cv = np.array([0.5, math.sqrt(3.0) * 0.5], dtype=np.float64)
    TRI_VERTS = np.stack([A, Bv, Cv], axis=0)
    BIG_AREA = math.sqrt(3.0) / 4.0  # area of unit-side equilateral triangle

    # Precompute linear mapping for barycentric/cartesian transforms
    # For P = A + u*(B-A) + v*(C-A). Then barycentric (a,b,c) = (1-u-v, u, v)
    M = np.stack([Bv - A, Cv - A], axis=1)  # 2x2
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
    # Guided proposal hyperparameters
    p_guided = 0.4
    kappa = 1.2

    # Global best tracking
    global_best_points = None
    global_best_min_area = -1.0

    # Helper: compute aggregate ascent gradient for a point over a subset of triangles
    def compute_point_gradient_from_worst(i_move: int, worst_idx: np.ndarray, P: np.ndarray) -> np.ndarray:
        """Aggregate gradient of |area| wrt point i_move over triangles in worst_idx that involve it."""
        g = np.zeros(2, dtype=np.float64)
        has = False
        for t_idx in worst_idx:
            i, j, k = TRIPLES[t_idx]
            if i_move != i and i_move != j and i_move != k:
                continue
            pi, pj, pk = P[i], P[j], P[k]
            # Signed area s = 0.5 * cross(pj - pi, pk - pi)
            s = 0.5 * ((pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0]))
            sign = 1.0 if s >= 0.0 else -1.0
            if i_move == i:
                qmr = pj - pk
                g += 0.5 * sign * np.array([qmr[1], -qmr[0]])
                has = True
            elif i_move == j:
                rmq = pk - pi
                g += 0.5 * sign * np.array([rmq[1], -rmq[0]])
                has = True
            else:  # i_move == k
                pmq = pi - pj
                g += 0.5 * sign * np.array([pmq[1], -pmq[0]])
                has = True
        if not has:
            return np.zeros(2, dtype=np.float64)
        return g

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

            # Propose a move for point i_move: guided by gradient with prob p_guided, else random barycentric
            b_old = B[i_move].copy()
            guided = (rng.random() < p_guided)
            b_new = None

            if guided:
                # Compute gradient aggregated over worst triangles involving i_move
                g = compute_point_gradient_from_worst(i_move, worst_idx, P)
                g_norm = float(np.linalg.norm(g))
                if not np.isfinite(g_norm) or g_norm < 1e-16:
                    guided = False  # fallback to random
                else:
                    # Gradient-ascent step in Cartesian, then project back to interior simplex
                    g_dir = g / g_norm
                    step_g = kappa * sigma
                    p_old = P[i_move].copy()
                    p_trial = p_old + step_g * g_dir
                    # Map to barycentric and project to strictly interior
                    try:
                        b_trial = cart_to_bary_single(p_trial, TRI_VERTS)
                        b_trial = project_to_simplex_with_lower_single(b_trial, eps_bary)
                        b_new = b_trial
                    except Exception:
                        guided = False  # fallback
            if not guided or b_new is None:
                # Random barycentric Gaussian proposal with projection
                noise = rng.normal(0.0, sigma, size=3)
                b_rand = b_old + noise
                b_new = project_to_simplex_with_lower(b_rand, eps_bary)
                if not np.isfinite(b_new).all():
                    b_new = b_old  # degenerate fallback (no move)

            # Compute new cartesian position and update areas incrementally
            p_new = (b_new @ TRI_VERTS)

            # Update areas only for triangles involving i_move
            changed_tris = tris_by_point[i_move]
            areas_new = areas.copy()
            for t_idx in changed_tris:
                i, j, k = TRIPLES[t_idx]
                pi = p_new if i == i_move else P[i]
                pj = p_new if j == i_move else P[j]
                pk = p_new if k == i_move else P[k]
                s = (pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0])
                areas_new[t_idx] = 0.5 * abs(s) / BIG_AREA

            # New smoothed objective and penalty
            F_new, min_area_new, mean_k_new = objective_smoothed(areas_new, K_small, alpha)
            P_i_saved = P[i_move].copy()
            P[i_move] = p_new
            pen_new = penalty_pairs(P, d0=d0)
            P[i_move] = P_i_saved  # restore
            F_new -= pen_weight * pen_new

            dF = F_new - F_val
            if dF >= 0.0 or rng.random() < math.exp(dF / max(T, 1e-12)):
                # Accept
                B[i_move] = b_new
                P[i_move] = p_new
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
                gradient_intensification(P, B, areas, W=W_grad, step0=0.010, steps=3,
                                         TRI_VERTS=TRI_VERTS, eps_bary=eps_bary,
                                         BIG_AREA=BIG_AREA)

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
                             BIG_AREA=BIG_AREA)
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
                             BIG_AREA: float) -> None:
    """Push vertices of the W worst triangles along gradients to increase area."""
    n = P.shape[0]
    T_idx_sorted = np.argsort(areas)
    worst_idx = T_idx_sorted[:min(W, len(T_idx_sorted))]

    # Compute signed areas to obtain gradient direction
    # s = 0.5 * cross(q - p, r - p)
    signed = []
    for t_idx in worst_idx:
        i, j, k = list(itertools.combinations(range(n), 3))[t_idx]
        pi, pj, pk = P[i], P[j], P[k]
        s = 0.5 * ((pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0]))
        signed.append((t_idx, s))
    # Accumulate gradients per point
    for s_step in range(steps):
        grads = np.zeros_like(P)
        # Recompute worst triangles based on current areas each substep
        T_idx_sorted = np.argsort(areas)
        worst_idx = T_idx_sorted[:min(W, len(T_idx_sorted))]
        for t_idx in worst_idx:
            # Map triangle index to tuple
            i, j, k = list(itertools.combinations(range(n), 3))[t_idx]
            pi, pj, pk = P[i], P[j], P[k]
            s = 0.5 * ((pj[0] - pi[0]) * (pk[1] - pi[1]) - (pj[1] - pi[1]) * (pk[0] - pi[0]))
            sign = 1.0 if s >= 0 else -1.0
            # Gradient wrt p, q, r for |s| is sign * grad(s)
            # grad_p s = 0.5 * perp(q - r)
            qmr = pj - pk
            rmq = pk - pi
            pmq = pi - pj
            grads[i] += 0.5 * sign * np.array([qmr[1], -qmr[0]])
            grads[j] += 0.5 * sign * np.array([rmq[1], -rmq[0]])
            grads[k] += 0.5 * sign * np.array([pmq[1], -pmq[0]])

        # Take a step and project back to barycentric domain
        step = step0 * (0.5 ** s_step)
        if step <= 0:
            break
        if np.linalg.norm(grads) < 1e-18:
            break

        P_old = P.copy()
        B_old = B.copy()
        P_new = P + step * grads
        # Project each moved point back via barycentric conversion and projection
        for i in range(n):
            b = cart_to_bary_single(P_new[i], TRI_VERTS)
            b = project_to_simplex_with_lower_single(b, eps_bary)
            B[i] = b
            P[i] = b @ TRI_VERTS

        # Accept only if min area does not decrease
        areas_new = compute_areas_from_points_single(P, BIG_AREA)
        if float(np.min(areas_new)) + 1e-12 < float(np.min(areas)):
            # Revert if worse
            P[:] = P_old
            B[:] = B_old
            break
        else:
            areas[:] = areas_new


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
