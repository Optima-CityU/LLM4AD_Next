Two-phase soft-to-hard maximin with geometry-aware, pair-focused repairs and strict-feasibility projection in barycentric space.

- KSAM-AR: Pair-Aware Soft-to-Hard Maximin with Adaptive Worst-Band and Apex Repairs: The method first optimizes a soft-min surrogate over the K worst triangle areas with cooling on the temperature and τ, then hardens to the exact min-area objective using a decaying worst-set band and plateau-driven transitions; it targets bottlenecks via pair-aware updates that move the closest pair along oriented area gradients of their worst triangle and via apex-normal repairs with short backtracking line searches, while maintaining strict interior feasibility and distinctness by projecting barycentric coordinates with a small epsilon margin, as reflected by validity = 1.0 in the reported Metrics; to reuse this pattern in future maximin designs under hard geometric constraints, adopt a K-worst soft-min annealing followed by hard worst-set gradient ascent augmented with pair-aware and apex-normal repairs, all executed in a feasibility-preserving coordinate system with per-move projection.

```python
#!/usr/bin/env python3
"""Heuristic solver for the Heilbronn problem in an equilateral triangle.

Implements a two-phase, geometry-aware maximin optimization that operates
in barycentric coordinates to maintain strict feasibility and directly
attacks the worst (smallest area) triangles.

Entry point: run_search_point(n=11)
"""

import json
import itertools
import math
import random
from typing import Tuple, List

# EVOLVE_START
import numpy as np


def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    def find_best_placement(n: int) -> Tuple[np.ndarray, float]:
        # Geometry: equilateral triangle vertices
        V = np.array(
            [
                [0.0, 0.0],
                [1.0, 0.0],
                [0.5, math.sqrt(3.0) * 0.5],
            ],
            dtype=float,
        )  # shape (3, 2)
        area_big = math.sqrt(3.0) / 4.0

        rng = np.random.RandomState(12345)
        py_rng = random.Random(12345)

        # Simplex projection helper (row-wise)
        def project_simplex_rows(W: np.ndarray, eps: float = 1e-6) -> np.ndarray:
            # Project each row onto the unit simplex (sum 1, nonnegative), then
            # shrink into the interior by eps_margin
            # Reference: Efficient Projections onto the l1-Ball for Learning in High Dimensions (Duchi et al.)
            # and adaptation for row-wise operation.
            X = W.copy()
            # Flattening for row-wise operation
            U = np.sort(X, axis=1)[:, ::-1]
            cssv = np.cumsum(U, axis=1)
            ind = np.arange(1, U.shape[1] + 1)
            cond = U - (cssv - 1) / ind > 0
            rho = cond.sum(axis=1) - 1
            theta = (cssv[np.arange(X.shape[0]), rho] - 1) / (rho + 1)
            Wp = np.maximum(X - theta[:, None], 0)
            # Strict interior shrink
            eps_margin = max(eps, 1e-6)
            Wp = (1.0 - 3.0 * eps_margin) * Wp + eps_margin
            return Wp

        def random_dirichlet_points(m: int) -> np.ndarray:
            # Generate m random points inside triangle via Dirichlet
            # Use exponential trick: sample Exp(1) and normalize
            E = rng.exponential(scale=1.0, size=(m, 3))
            W = E / E.sum(axis=1, keepdims=True)
            # Strict interior shrink
            W = (1.0 - 3.0 * 1e-4) * W + 1e-4
            return W

        def halton_sequence_in_triangle(m: int, bases=(2, 3)) -> np.ndarray:
            # Generate m points using Halton sequence mapped into the triangle via simplex mapping
            def van_der_corput(n, base):
                vdc, denom = 0.0, 1.0
                while n:
                    n, remainder = divmod(n, base)
                    denom *= base
                    vdc += remainder / denom
                return vdc

            pts = []
            for i in range(1, m + 1):
                u = van_der_corput(i, bases[0])
                v = van_der_corput(i, bases[1])
                # map unit square to triangle by folding
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                w0 = 1.0 - u - v
                w1 = u
                w2 = v
                pts.append([w0, w1, w2])
            W = np.array(pts, dtype=float)
            # Strict interior shrink
            W = (1.0 - 3.0 * 1e-4) * W + 1e-4
            return W

        def lattice_rows(n: int) -> np.ndarray:
            # Create a simple row-based layout parallel to base
            # Distribute points into 3-4 rows
            if n <= 6:
                rows = [2, 2, 2]
            else:
                rows = [3, 3, 3, n - 9] if n >= 9 else [2, 2, 2, n - 6]
                rows = [r for r in rows if r > 0]
            total_rows = len(rows)
            y_levels = np.linspace(0.15, 0.85, total_rows)

            W = np.zeros((n, 3), dtype=float)
            idx = 0
            for r, count in enumerate(rows):
                t = y_levels[r]
                # Interpolate between vertex 0 (top-left) and vertex 1 (right)
                # Use barycentric param to set y roughly proportional
                # We'll set w2 ~ t (towards vertex C, apex), and distribute w0/w1 along base
                w2 = t * 0.9 + 0.05
                w01_sum = 1.0 - w2
                xs = np.linspace(0.1, 0.9, count)
                for x in xs:
                    w1 = x * w01_sum
                    w0 = w01_sum - w1
                    W[idx] = [w0, w1, w2]
                    idx += 1
            # If we didn't fill, pad with random
            if idx < n:
                W[idx:] = random_dirichlet_points(n - idx)
            # Strict interior shrink
            W = (1.0 - 3.0 * 1e-4) * W + 1e-4
            return W

        # Cartesian from barycentric
        def cart_from_bary(W: np.ndarray) -> np.ndarray:
            return W @ V  # shape (n,2)

        # Compute all triangle combinations once
        combs = np.array(list(itertools.combinations(range(n), 3)), dtype=int)
        I_all = combs[:, 0]
        J_all = combs[:, 1]
        K_all = combs[:, 2]
        m_tris = combs.shape[0]

        def oriented_areas(X: np.ndarray, I: np.ndarray, J: np.ndarray, K: np.ndarray) -> np.ndarray:
            # oriented area (signed), shape (len(I),)
            a = X[J] - X[I]
            b = X[K] - X[I]
            cross = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
            return 0.5 * cross

        def triangle_areas_abs(X: np.ndarray) -> np.ndarray:
            Ao = oriented_areas(X, I_all, J_all, K_all)
            return np.abs(Ao)

        def min_area_stats(X: np.ndarray):
            Aabs = triangle_areas_abs(X)
            idx_min = int(np.argmin(Aabs))
            return Aabs[idx_min], idx_min, Aabs

        def softmin_value(areas: np.ndarray, K: int, tau: float) -> float:
            # Consider K smallest areas
            if K >= len(areas):
                sel = areas
            else:
                # partial selection for performance
                kth = np.partition(areas, K - 1)[K - 1]
                sel = areas[areas <= kth]
                if sel.shape[0] > K:
                    # in case of equal near boundary, trim
                    sel = np.sort(sel)[:K]
            exps = np.exp(-sel / max(tau, 1e-9))
            mean_exp = exps.mean()
            return -max(tau, 1e-9) * math.log(max(mean_exp, 1e-300))

        def softmin_weights_for_K(X: np.ndarray, K: int, tau: float):
            # returns selected triangle indices and their normalized weights
            Aabs = triangle_areas_abs(X)
            if K >= len(Aabs):
                idxs = np.arange(len(Aabs))
                sel = Aabs
            else:
                kth = np.partition(Aabs, K - 1)[K - 1]
                mask = Aabs <= kth
                sel_idxs = np.nonzero(mask)[0]
                sel = Aabs[sel_idxs]
                if sel.shape[0] > K:
                    order = np.argsort(sel)[:K]
                    sel_idxs = sel_idxs[order]
                    sel = sel[order]
                idxs = sel_idxs
            exps = np.exp(-sel / max(tau, 1e-9))
            w = exps / max(exps.sum(), 1e-300)
            return idxs, w, sel

        def worst_band_indices(X: np.ndarray, beta: float):
            Aabs = triangle_areas_abs(X)
            amin = float(Aabs.min())
            thr = amin * (1.0 + beta)
            idxs = np.nonzero(Aabs <= thr)[0]
            return idxs, Aabs[idxs], amin

        def perp_cw(v: np.ndarray) -> np.ndarray:
            # Clockwise perpendicular: (y, -x)
            return np.stack([v[:, 1], -v[:, 0]], axis=1)

        def accumulate_gradients(X: np.ndarray, tri_idxs: np.ndarray, weights: np.ndarray = None) -> np.ndarray:
            # Aggregate gradient in Cartesian for all points from selected triangles with optional weights
            if tri_idxs.size == 0:
                return np.zeros_like(X)
            I = I_all[tri_idxs]
            J = J_all[tri_idxs]
            K = K_all[tri_idxs]
            Ao = oriented_areas(X, I, J, K)
            s = np.sign(Ao)  # shape (m,)
            # differences for g computations
            vJK = X[J] - X[K]
            vKI = X[K] - X[I]
            vIJ = X[I] - X[J]
            # base gradients per triangle
            gI = 0.5 * s[:, None] * perp_cw(vJK)
            gJ = 0.5 * s[:, None] * perp_cw(vKI)
            gK = 0.5 * s[:, None] * perp_cw(vIJ)
            if weights is not None:
                w = weights[:, None]
                gI *= w
                gJ *= w
                gK *= w
            grads = np.zeros_like(X)
            np.add.at(grads, I, gI)
            np.add.at(grads, J, gJ)
            np.add.at(grads, K, gK)
            return grads

        def normalize_rows(M: np.ndarray, eps=1e-12) -> np.ndarray:
            norms = np.linalg.norm(M, axis=1, keepdims=True)
            norms = np.maximum(norms, eps)
            return M / norms

        def bary_from_cart(X: np.ndarray) -> np.ndarray:
            # Compute barycentric coordinates for X relative to V
            # Solve for w in R^3 with sum=1: X = w0*A + w1*B + w2*C, w0+w1+w2=1
            # Use coordinates relative to A:
            A = V[0]
            B = V[1]
            C = V[2]
            M = np.column_stack([B - A, C - A])  # 2x2
            Minv = np.linalg.inv(M)
            rel = (X - A) @ Minv  # shape (n,2)
            w1 = rel[:, 0]
            w2 = rel[:, 1]
            w0 = 1.0 - w1 - w2
            W = np.stack([w0, w1, w2], axis=1)
            return W

        def ensure_distinct(W: np.ndarray):
            # If points are too close, jitter them slightly in barycentric space
            X = cart_from_bary(W)
            nloc = X.shape[0]
            # pairwise distance checks
            for i in range(nloc):
                for j in range(i + 1, nloc):
                    dx = X[i, 0] - X[j, 0]
                    dy = X[i, 1] - X[j, 1]
                    d2 = dx * dx + dy * dy
                    if d2 < 1e-7:
                        # apply small opposite jitters
                        delta = rng.normal(scale=2e-4, size=3)
                        delta -= delta.mean()
                        W[i] = W[i] + delta
                        W[j] = W[j] - delta
            return project_simplex_rows(W, eps=1e-4)

        # Move proposals
        def propose_softmin_step(W: np.ndarray, K: int, tau: float, alpha: float) -> np.ndarray:
            X = cart_from_bary(W)
            tri_idxs, tri_w, _ = softmin_weights_for_K(X, K, tau)
            gX = accumulate_gradients(X, tri_idxs, tri_w)
            # convert to barycentric gradient direction
            gW = gX @ V.T  # (n,3)
            # normalize per-row to avoid huge steps
            gWn = normalize_rows(gW)
            W_new = project_simplex_rows(W + alpha * gWn, eps=1e-4)
            W_new = ensure_distinct(W_new)
            return W_new

        def propose_band_step(W: np.ndarray, beta: float, alpha: float) -> np.ndarray:
            X = cart_from_bary(W)
            tri_idxs, _, _ = worst_band_indices(X, beta)
            gX = accumulate_gradients(X, tri_idxs, None)
            gW = gX @ V.T
            gWn = normalize_rows(gW)
            W_new = project_simplex_rows(W + alpha * gWn, eps=1e-4)
            W_new = ensure_distinct(W_new)
            return W_new

        def propose_pair_repulsion(W: np.ndarray, alpha: float) -> np.ndarray:
            X = cart_from_bary(W)
            # find closest pair
            nloc = X.shape[0]
            min_d2 = 1e9
            pair = (0, 1)
            for i in range(nloc):
                for j in range(i + 1, nloc):
                    dx = X[i, 0] - X[j, 0]
                    dy = X[i, 1] - X[j, 1]
                    d2 = dx * dx + dy * dy
                    if d2 < min_d2:
                        min_d2 = d2
                        pair = (i, j)
            i, j = pair
            # find k that minimizes area triangle (i,j,k)
            others = [k for k in range(nloc) if k != i and k != j]
            if not others:
                return W
            I = np.array([i] * len(others))
            J = np.array([j] * len(others))
            K = np.array(others)
            Ao = oriented_areas(X, I, J, K)
            Aabs = np.abs(Ao)
            idx = int(np.argmin(Aabs))
            k = K[idx]

            # Compute gradient for that triangle directly (avoid reliance on precomputed index order)
            # Gradient of absolute area is 0.5 * sign(Ao) * perp(edge)
            ao = oriented_areas(X, np.array([i]), np.array([j]), np.array([k]))[0]
            s = 0.0
            if ao > 0:
                s = 1.0
            elif ao < 0:
                s = -1.0
            else:
                s = 0.0  # degenerate; fallback to small separation only
            gX = np.zeros_like(X)
            if s != 0.0:
                vJK = (X[j] - X[k])[None, :]
                vKI = (X[k] - X[i])[None, :]
                # gI = 0.5 * s * perp_cw(vJK), gJ = 0.5 * s * perp_cw(vKI)
                gI = 0.5 * s * np.array([vJK[0, 1], -vJK[0, 0]])
                gJ = 0.5 * s * np.array([vKI[0, 1], -vKI[0, 0]])
                gX[i] += gI
                gX[j] += gJ

            # small separation term between the closest pair
            v = X[i] - X[j]
            norm_v = np.linalg.norm(v)
            if norm_v > 1e-12:
                sep = 0.02 * v / norm_v
                gX[i] += sep
                gX[j] -= sep

            gW = gX @ V.T
            gWn = gW.copy()
            # normalize rows of nonzero rows
            for idx_row in [i, j]:
                norm = np.linalg.norm(gWn[idx_row])
                if norm > 1e-15:
                    gWn[idx_row] /= norm
            W_new = W.copy()
            W_new[i] = W_new[i] + alpha * gWn[i]
            W_new[j] = W_new[j] + alpha * gWn[j]
            W_new = project_simplex_rows(W_new, eps=1e-4)
            W_new = ensure_distinct(W_new)
            return W_new

        def propose_apex_repair(W: np.ndarray, step0: float = 0.03) -> np.ndarray:
            # Push apex of one of the worst triangles outward along normal with backtracking
            X = cart_from_bary(W)
            # Find worst triangle(s)
            amin, idx_min, Aall = min_area_stats(X)
            # Work with a small set: take triangles within 1% of min
            kth = amin * 1.01
            tri_idxs = np.nonzero(Aall <= kth)[0]
            if tri_idxs.size == 0:
                tri_idxs = np.array([idx_min])
            # For the first triangle in set, perform apex push
            t = int(tri_idxs[0])
            i, j, k = I_all[t], J_all[t], K_all[t]
            # Identify apex: vertex with smallest distance to opposite edge
            def point_edge_distance(p, a, b):
                ab = b - a
                if np.allclose(ab, 0):
                    return np.linalg.norm(p - a)
                tproj = np.clip(((p - a) @ ab) / (ab @ ab), 0.0, 1.0)
                closest = a + tproj * ab
                return np.linalg.norm(p - closest)

            di = point_edge_distance(X[i], X[j], X[k])
            dj = point_edge_distance(X[j], X[i], X[k])
            dk = point_edge_distance(X[k], X[i], X[j])
            if di <= dj and di <= dk:
                apex = i
                base = (j, k)
            elif dj <= di and dj <= dk:
                apex = j
                base = (i, k)
            else:
                apex = k
                base = (i, j)
            # Gradient direction for apex from triangle gradient
            gX_tris = accumulate_gradients(X, np.array([t]), None)
            direction = gX_tris[apex]
            norm = np.linalg.norm(direction)
            if norm < 1e-15:
                return W
            dir_unit = direction / norm
            # Backtracking line search on min area
            bestW = W.copy()
            best_val = min_area_stats(cart_from_bary(bestW))[0]
            step = step0
            for _ in range(8):
                W_try = W.copy()
                dW_apex = (dir_unit @ V.T)  # project to barycentric direction
                W_try[apex] = W_try[apex] + step * dW_apex
                W_try = project_simplex_rows(W_try, eps=1e-4)
                W_try = ensure_distinct(W_try)
                val_try = min_area_stats(cart_from_bary(W_try))[0]
                if val_try > best_val + 1e-12:
                    best_val = val_try
                    bestW = W_try
                    break
                else:
                    step *= 0.5
            return bestW

        def evaluate_normalized_min_area(W: np.ndarray) -> float:
            X = cart_from_bary(W)
            amin, _, _ = min_area_stats(X)
            return float(amin / area_big)

        # Acceptance helpers
        def accept_soft(oldW, newW, K, tau, T):
            X_old = cart_from_bary(oldW)
            X_new = cart_from_bary(newW)
            A_old = triangle_areas_abs(X_old)
            A_new = triangle_areas_abs(X_new)
            f_old = softmin_value(A_old, K, tau)
            f_new = softmin_value(A_new, K, tau)
            if f_new >= f_old:
                return True, f_new, f_old
            else:
                # Metropolis criterion
                if T <= 1e-12:
                    return False, f_new, f_old
                prob = math.exp((f_new - f_old) / T)
                if rng.rand() < prob:
                    return True, f_new, f_old
                else:
                    return False, f_new, f_old

        def accept_hard(oldW, newW, T):
            # Greedy on min area with SA fallback
            X_old = cart_from_bary(oldW)
            X_new = cart_from_bary(newW)
            a_old, _, _ = min_area_stats(X_old)
            a_new, _, _ = min_area_stats(X_new)
            if a_new >= a_old:
                return True, a_new, a_old
            else:
                if T <= 1e-12:
                    return False, a_new, a_old
                prob = math.exp((a_new - a_old) / (T))
                if rng.rand() < prob:
                    return True, a_new, a_old
                else:
                    return False, a_new, a_old

        # Multi-start seeds
        seeds = []
        # Some Halton-based
        for _ in range(3):
            W = halton_sequence_in_triangle(n)
            # jitter
            W += rng.normal(scale=0.01, size=W.shape)
            W = project_simplex_rows(W, eps=1e-4)
            seeds.append(W)
        # Lattice style
        for _ in range(3):
            W = lattice_rows(n)
            W += rng.normal(scale=0.008, size=W.shape)
            W = project_simplex_rows(W, eps=1e-4)
            seeds.append(W)
        # Random Dirichlet
        for _ in range(6):
            W = random_dirichlet_points(n)
            seeds.append(W)

        best_W_global = None
        best_min_area_global = -1.0

        # Optimization parameters
        phaseA_iters = 2800
        phaseB_iters = 2600

        for seed_idx, W0 in enumerate(seeds):
            W = project_simplex_rows(W0, eps=1e-4)
            W = ensure_distinct(W)

            # Track best of this run
            best_W = W.copy()
            best_min_area = evaluate_normalized_min_area(W)

            # Phase A: soft-min annealing
            K_soft = min(50, m_tris)  # number of worst triangles to aggregate
            tau0, tau1 = 0.03, 0.005
            Tsoft0, Tsoft1 = 0.01, 0.0005
            alpha_soft = 0.08
            inc_rate, dec_rate = 1.02, 0.7

            last_accept = 0
            for it in range(phaseA_iters):
                frac = it / max(phaseA_iters - 1, 1)
                tau = tau0 * (1.0 - frac) + tau1 * frac
                Tsoft = Tsoft0 * (1.0 - frac) + Tsoft1 * frac

                move_type = rng.rand()
                if move_type < 0.65:
                    W_prop = propose_softmin_step(W, K_soft, tau, alpha_soft)
                elif move_type < 0.85:
                    # closest pair repulsion
                    W_prop = propose_pair_repulsion(W, alpha=0.05)
                else:
                    # small random jitter in barycentric with projection
                    dW = rng.normal(scale=0.01, size=W.shape)
                    dW -= dW.mean(axis=1, keepdims=True)
                    W_prop = project_simplex_rows(W + dW, eps=1e-4)
                    W_prop = ensure_distinct(W_prop)

                accepted, f_new, f_old = accept_soft(W, W_prop, K_soft, tau, Tsoft)
                if accepted:
                    W = W_prop
                    last_accept = it
                    # adapt
                    if move_type < 0.70:
                        alpha_soft *= inc_rate
                        alpha_soft = min(alpha_soft, 0.3)
                else:
                    if move_type < 0.70:
                        alpha_soft *= dec_rate
                        alpha_soft = max(alpha_soft, 0.005)

                # periodic apex repair if stalled
                if it - last_accept > 120:
                    W_repair = propose_apex_repair(W, step0=0.03)
                    W = W_repair
                    last_accept = it

                # track best by true min-area
                cur_min_area = evaluate_normalized_min_area(W)
                if cur_min_area > best_min_area + 1e-12:
                    best_min_area = cur_min_area
                    best_W = W.copy()

            # Phase B: hard maximin with banded worst-set and repairs
            beta0, beta1 = 0.18, 0.03
            Thard0, Thard1 = 0.004, 0.0002
            alpha_band = 0.06

            stall_counter = 0
            last_improve_it = 0
            best_min_area_phaseB = evaluate_normalized_min_area(W)
            W_best_phaseB = W.copy()

            for it in range(phaseB_iters):
                frac = it / max(phaseB_iters - 1, 1)
                beta = beta0 * (1.0 - frac) + beta1 * frac
                Thard = Thard0 * (1.0 - frac) + Thard1 * frac

                # Occasionally widen band if stalled
                if stall_counter > 150:
                    beta_wide = min(0.5, beta * 2.5)
                    W_prop = propose_band_step(W, beta=beta_wide, alpha=alpha_band * 1.2)
                    stall_counter = 0
                else:
                    move_type = rng.rand()
                    if move_type < 0.58:
                        W_prop = propose_band_step(W, beta=beta, alpha=alpha_band)
                    elif move_type < 0.80:
                        W_prop = propose_apex_repair(W, step0=0.025)
                    else:
                        W_prop = propose_pair_repulsion(W, alpha=0.045)

                accepted, a_new, a_old = accept_hard(W, W_prop, Thard)
                if accepted:
                    W = W_prop
                    stall_counter = 0
                else:
                    stall_counter += 1

                # adapt step size
                if accepted and rng.rand() < 0.25:
                    alpha_band = min(alpha_band * 1.03, 0.25)
                elif not accepted and rng.rand() < 0.25:
                    alpha_band = max(alpha_band * 0.8, 0.006)

                # occasional batch repair of worst triangle
                if it % 200 == 0:
                    W = propose_apex_repair(W, step0=0.035)

                cur_min_area = evaluate_normalized_min_area(W)
                if cur_min_area > best_min_area_phaseB + 1e-12:
                    best_min_area_phaseB = cur_min_area
                    W_best_phaseB = W.copy()

            # Choose better of end vs phaseB best
            W_run_best = W_best_phaseB
            min_area_run_best = best_min_area_phaseB

            # Compare to global best
            if min_area_run_best > best_min_area_global + 1e-12:
                best_min_area_global = min_area_run_best
                best_W_global = W_run_best.copy()

        # Fallback if somehow none improved
        if best_W_global is None:
            best_W_global = halton_sequence_in_triangle(n)
        X_best = cart_from_bary(best_W_global)
        # Ensure strict interior and uniqueness (already maintained)
        min_area_norm = evaluate_normalized_min_area(best_W_global)
        return X_best, float(min_area_norm)

    # Run search
    points_np, min_area = find_best_placement(n)
    # Convert to lists for output
    points = points_np.tolist()
    return points, min_area
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```

```python
#!/usr/bin/env python3
"""Heuristic solver for the Heilbronn problem in an equilateral triangle.

Implements a two-phase, geometry-aware maximin optimization that operates
in barycentric coordinates to maintain strict feasibility and directly
attacks the worst (smallest area) triangles.

Entry point: run_search_point(n=11)
"""

import json
import itertools
import math
import random
from typing import Tuple, List

# EVOLVE_START
import numpy as np


def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    def find_best_placement(n: int) -> Tuple[np.ndarray, float]:
        # Geometry: equilateral triangle vertices
        V = np.array(
            [
                [0.0, 0.0],
                [1.0, 0.0],
                [0.5, math.sqrt(3.0) * 0.5],
            ],
            dtype=float,
        )  # shape (3, 2)
        area_big = math.sqrt(3.0) / 4.0

        rng = np.random.RandomState(12345)
        py_rng = random.Random(12345)

        # Simplex projection helper (row-wise)
        def project_simplex_rows(W: np.ndarray, eps: float = 1e-6) -> np.ndarray:
            # Project each row onto the unit simplex (sum 1, nonnegative), then
            # shrink into the interior by eps_margin
            # Reference: Efficient Projections onto the l1-Ball for Learning in High Dimensions (Duchi et al.)
            # and adaptation for row-wise operation.
            X = W.copy()
            # Flattening for row-wise operation
            U = np.sort(X, axis=1)[:, ::-1]
            cssv = np.cumsum(U, axis=1)
            ind = np.arange(1, U.shape[1] + 1)
            cond = U - (cssv - 1) / ind > 0
            rho = cond.sum(axis=1) - 1
            theta = (cssv[np.arange(X.shape[0]), rho] - 1) / (rho + 1)
            Wp = np.maximum(X - theta[:, None], 0)
            # Strict interior shrink
            eps_margin = max(eps, 1e-6)
            Wp = (1.0 - 3.0 * eps_margin) * Wp + eps_margin
            return Wp

        def random_dirichlet_points(m: int) -> np.ndarray:
            # Generate m random points inside triangle via Dirichlet
            # Use exponential trick: sample Exp(1) and normalize
            E = rng.exponential(scale=1.0, size=(m, 3))
            W = E / E.sum(axis=1, keepdims=True)
            # Strict interior shrink
            W = (1.0 - 3.0 * 1e-4) * W + 1e-4
            return W

        def halton_sequence_in_triangle(m: int, bases=(2, 3)) -> np.ndarray:
            # Generate m points using Halton sequence mapped into the triangle via simplex mapping
            def van_der_corput(n, base):
                vdc, denom = 0.0, 1.0
                while n:
                    n, remainder = divmod(n, base)
                    denom *= base
                    vdc += remainder / denom
                return vdc

            pts = []
            for i in range(1, m + 1):
                u = van_der_corput(i, bases[0])
                v = van_der_corput(i, bases[1])
                # map unit square to triangle by folding
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                w0 = 1.0 - u - v
                w1 = u
                w2 = v
                pts.append([w0, w1, w2])
            W = np.array(pts, dtype=float)
            # Strict interior shrink
            W = (1.0 - 3.0 * 1e-4) * W + 1e-4
            return W

        def lattice_rows(n: int) -> np.ndarray:
            # Create a simple row-based layout parallel to base
            # Distribute points into 3-4 rows
            if n <= 6:
                rows = [2, 2, 2]
            else:
                rows = [3, 3, 3, n - 9] if n >= 9 else [2, 2, 2, n - 6]
                rows = [r for r in rows if r > 0]
            total_rows = len(rows)
            y_levels = np.linspace(0.15, 0.85, total_rows)

            W = np.zeros((n, 3), dtype=float)
            idx = 0
            for r, count in enumerate(rows):
                t = y_levels[r]
                # Interpolate between vertex 0 (top-left) and vertex 1 (right)
                # Use barycentric param to set y roughly proportional
                # We'll set w2 ~ t (towards vertex C, apex), and distribute w0/w1 along base
                w2 = t * 0.9 + 0.05
                w01_sum = 1.0 - w2
                xs = np.linspace(0.1, 0.9, count)
                for x in xs:
                    w1 = x * w01_sum
                    w0 = w01_sum - w1
                    W[idx] = [w0, w1, w2]
                    idx += 1
            # If we didn't fill, pad with random
            if idx < n:
                W[idx:] = random_dirichlet_points(n - idx)
            # Strict interior shrink
            W = (1.0 - 3.0 * 1e-4) * W + 1e-4
            return W

        # Cartesian from barycentric
        def cart_from_bary(W: np.ndarray) -> np.ndarray:
            return W @ V  # shape (n,2)

        # Compute all triangle combinations once
        combs = np.array(list(itertools.combinations(range(n), 3)), dtype=int)
        I_all = combs[:, 0]
        J_all = combs[:, 1]
        K_all = combs[:, 2]
        m_tris = combs.shape[0]

        def oriented_areas(X: np.ndarray, I: np.ndarray, J: np.ndarray, K: np.ndarray) -> np.ndarray:
            # oriented area (signed), shape (len(I),)
            a = X[J] - X[I]
            b = X[K] - X[I]
            cross = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
            return 0.5 * cross

        def triangle_areas_abs(X: np.ndarray) -> np.ndarray:
            Ao = oriented_areas(X, I_all, J_all, K_all)
            return np.abs(Ao)

        def min_area_stats(X: np.ndarray):
            Aabs = triangle_areas_abs(X)
            idx_min = int(np.argmin(Aabs))
            return Aabs[idx_min], idx_min, Aabs

        def softmin_value(areas: np.ndarray, K: int, tau: float) -> float:
            # Consider K smallest areas
            if K >= len(areas):
                sel = areas
            else:
                # partial selection for performance
                kth = np.partition(areas, K - 1)[K - 1]
                sel = areas[areas <= kth]
                if sel.shape[0] > K:
                    # in case of equal near boundary, trim
                    sel = np.sort(sel)[:K]
            exps = np.exp(-sel / max(tau, 1e-9))
            mean_exp = exps.mean()
            return -max(tau, 1e-9) * math.log(max(mean_exp, 1e-300))

        def softmin_weights_for_K(X: np.ndarray, K: int, tau: float):
            # returns selected triangle indices and their normalized weights
            Aabs = triangle_areas_abs(X)
            if K >= len(Aabs):
                idxs = np.arange(len(Aabs))
                sel = Aabs
            else:
                kth = np.partition(Aabs, K - 1)[K - 1]
                mask = Aabs <= kth
                sel_idxs = np.nonzero(mask)[0]
                sel = Aabs[sel_idxs]
                if sel.shape[0] > K:
                    order = np.argsort(sel)[:K]
                    sel_idxs = sel_idxs[order]
                    sel = sel[order]
                idxs = sel_idxs
            exps = np.exp(-sel / max(tau, 1e-9))
            w = exps / max(exps.sum(), 1e-300)
            return idxs, w, sel

        def worst_band_indices(X: np.ndarray, beta: float):
            Aabs = triangle_areas_abs(X)
            amin = float(Aabs.min())
            thr = amin * (1.0 + beta)
            idxs = np.nonzero(Aabs <= thr)[0]
            return idxs, Aabs[idxs], amin

        def perp_cw(v: np.ndarray) -> np.ndarray:
            # Clockwise perpendicular: (y, -x)
            return np.stack([v[:, 1], -v[:, 0]], axis=1)

        def accumulate_gradients(X: np.ndarray, tri_idxs: np.ndarray, weights: np.ndarray = None) -> np.ndarray:
            # Aggregate gradient in Cartesian for all points from selected triangles with optional weights
            if tri_idxs.size == 0:
                return np.zeros_like(X)
            I = I_all[tri_idxs]
            J = J_all[tri_idxs]
            K = K_all[tri_idxs]
            Ao = oriented_areas(X, I, J, K)
            s = np.sign(Ao)  # shape (m,)
            # differences for g computations
            vJK = X[J] - X[K]
            vKI = X[K] - X[I]
            vIJ = X[I] - X[J]
            # base gradients per triangle
            gI = 0.5 * s[:, None] * perp_cw(vJK)
            gJ = 0.5 * s[:, None] * perp_cw(vKI)
            gK = 0.5 * s[:, None] * perp_cw(vIJ)
            if weights is not None:
                w = weights[:, None]
                gI *= w
                gJ *= w
                gK *= w
            grads = np.zeros_like(X)
            np.add.at(grads, I, gI)
            np.add.at(grads, J, gJ)
            np.add.at(grads, K, gK)
            return grads

        def normalize_rows(M: np.ndarray, eps=1e-12) -> np.ndarray:
            norms = np.linalg.norm(M, axis=1, keepdims=True)
            norms = np.maximum(norms, eps)
            return M / norms

        def bary_from_cart(X: np.ndarray) -> np.ndarray:
            # Compute barycentric coordinates for X relative to V
            # Solve for w in R^3 with sum=1: X = w0*A + w1*B + w2*C, w0+w1+w2=1
            # Use coordinates relative to A:
            A = V[0]
            B = V[1]
            C = V[2]
            M = np.column_stack([B - A, C - A])  # 2x2
            Minv = np.linalg.inv(M)
            rel = (X - A) @ Minv  # shape (n,2)
            w1 = rel[:, 0]
            w2 = rel[:, 1]
            w0 = 1.0 - w1 - w2
            W = np.stack([w0, w1, w2], axis=1)
            return W

        def ensure_distinct(W: np.ndarray):
            # If points are too close, jitter them slightly in barycentric space
            X = cart_from_bary(W)
            nloc = X.shape[0]
            # pairwise distance checks
            for i in range(nloc):
                for j in range(i + 1, nloc):
                    dx = X[i, 0] - X[j, 0]
                    dy = X[i, 1] - X[j, 1]
                    d2 = dx * dx + dy * dy
                    if d2 < 1e-7:
                        # apply small opposite jitters
                        delta = rng.normal(scale=2e-4, size=3)
                        delta -= delta.mean()
                        W[i] = W[i] + delta
                        W[j] = W[j] - delta
            return project_simplex_rows(W, eps=1e-4)

        # Move proposals
        def propose_softmin_step(W: np.ndarray, K: int, tau: float, alpha: float) -> np.ndarray:
            X = cart_from_bary(W)
            tri_idxs, tri_w, _ = softmin_weights_for_K(X, K, tau)
            gX = accumulate_gradients(X, tri_idxs, tri_w)
            # convert to barycentric gradient direction
            gW = gX @ V.T  # (n,3)
            # normalize per-row to avoid huge steps
            gWn = normalize_rows(gW)
            W_new = project_simplex_rows(W + alpha * gWn, eps=1e-4)
            W_new = ensure_distinct(W_new)
            return W_new

        def propose_band_step(W: np.ndarray, beta: float, alpha: float) -> np.ndarray:
            X = cart_from_bary(W)
            tri_idxs, _, _ = worst_band_indices(X, beta)
            gX = accumulate_gradients(X, tri_idxs, None)
            gW = gX @ V.T
            gWn = normalize_rows(gW)
            W_new = project_simplex_rows(W + alpha * gWn, eps=1e-4)
            W_new = ensure_distinct(W_new)
            return W_new

        def propose_pair_repulsion(W: np.ndarray, alpha: float) -> np.ndarray:
            X = cart_from_bary(W)
            # find closest pair
            nloc = X.shape[0]
            min_d2 = 1e9
            pair = (0, 1)
            for i in range(nloc):
                for j in range(i + 1, nloc):
                    dx = X[i, 0] - X[j, 0]
                    dy = X[i, 1] - X[j, 1]
                    d2 = dx * dx + dy * dy
                    if d2 < min_d2:
                        min_d2 = d2
                        pair = (i, j)
            i, j = pair
            # find k that minimizes area triangle (i,j,k)
            others = [k for k in range(nloc) if k != i and k != j]
            if not others:
                return W
            I = np.array([i] * len(others))
            J = np.array([j] * len(others))
            K = np.array(others)
            Ao = oriented_areas(X, I, J, K)
            Aabs = np.abs(Ao)
            idx = int(np.argmin(Aabs))
            k = K[idx]

            # Compute gradient for that triangle directly (avoid reliance on precomputed index order)
            # Gradient of absolute area is 0.5 * sign(Ao) * perp(edge)
            ao = oriented_areas(X, np.array([i]), np.array([j]), np.array([k]))[0]
            s = 0.0
            if ao > 0:
                s = 1.0
            elif ao < 0:
                s = -1.0
            else:
                s = 0.0  # degenerate; fallback to small separation only
            gX = np.zeros_like(X)
            if s != 0.0:
                vJK = (X[j] - X[k])[None, :]
                vKI = (X[k] - X[i])[None, :]
                # gI = 0.5 * s * perp_cw(vJK), gJ = 0.5 * s * perp_cw(vKI)
                gI = 0.5 * s * np.array([vJK[0, 1], -vJK[0, 0]])
                gJ = 0.5 * s * np.array([vKI[0, 1], -vKI[0, 0]])
                gX[i] += gI
                gX[j] += gJ

            # small separation term between the closest pair
            v = X[i] - X[j]
            norm_v = np.linalg.norm(v)
            if norm_v > 1e-12:
                sep = 0.02 * v / norm_v
                gX[i] += sep
                gX[j] -= sep

            gW = gX @ V.T
            gWn = gW.copy()
            # normalize rows of nonzero rows
            for idx_row in [i, j]:
                norm = np.linalg.norm(gWn[idx_row])
                if norm > 1e-15:
                    gWn[idx_row] /= norm
            W_new = W.copy()
            W_new[i] = W_new[i] + alpha * gWn[i]
            W_new[j] = W_new[j] + alpha * gWn[j]
            W_new = project_simplex_rows(W_new, eps=1e-4)
            W_new = ensure_distinct(W_new)
            return W_new

        def propose_apex_repair(W: np.ndarray, step0: float = 0.03) -> np.ndarray:
            # Push apex of one of the worst triangles outward along normal with backtracking
            X = cart_from_bary(W)
            # Find worst triangle(s)
            amin, idx_min, Aall = min_area_stats(X)
            # Work with a small set: take triangles within 1% of min
            kth = amin * 1.01
            tri_idxs = np.nonzero(Aall <= kth)[0]
            if tri_idxs.size == 0:
                tri_idxs = np.array([idx_min])
            # For the first triangle in set, perform apex push
            t = int(tri_idxs[0])
            i, j, k = I_all[t], J_all[t], K_all[t]
            # Identify apex: vertex with smallest distance to opposite edge
            def point_edge_distance(p, a, b):
                ab = b - a
                if np.allclose(ab, 0):
                    return np.linalg.norm(p - a)
                tproj = np.clip(((p - a) @ ab) / (ab @ ab), 0.0, 1.0)
                closest = a + tproj * ab
                return np.linalg.norm(p - closest)

            di = point_edge_distance(X[i], X[j], X[k])
            dj = point_edge_distance(X[j], X[i], X[k])
            dk = point_edge_distance(X[k], X[i], X[j])
            if di <= dj and di <= dk:
                apex = i
                base = (j, k)
            elif dj <= di and dj <= dk:
                apex = j
                base = (i, k)
            else:
                apex = k
                base = (i, j)
            # Gradient direction for apex from triangle gradient
            gX_tris = accumulate_gradients(X, np.array([t]), None)
            direction = gX_tris[apex]
            norm = np.linalg.norm(direction)
            if norm < 1e-15:
                return W
            dir_unit = direction / norm
            # Backtracking line search on min area
            bestW = W.copy()
            best_val = min_area_stats(cart_from_bary(bestW))[0]
            step = step0
            for _ in range(8):
                W_try = W.copy()
                dW_apex = (dir_unit @ V.T)  # project to barycentric direction
                W_try[apex] = W_try[apex] + step * dW_apex
                W_try = project_simplex_rows(W_try, eps=1e-4)
                W_try = ensure_distinct(W_try)
                val_try = min_area_stats(cart_from_bary(W_try))[0]
                if val_try > best_val + 1e-12:
                    best_val = val_try
                    bestW = W_try
                    break
                else:
                    step *= 0.5
            return bestW

        def evaluate_normalized_min_area(W: np.ndarray) -> float:
            X = cart_from_bary(W)
            amin, _, _ = min_area_stats(X)
            return float(amin / area_big)

        # Acceptance helpers
        def accept_soft(oldW, newW, K, tau, T):
            X_old = cart_from_bary(oldW)
            X_new = cart_from_bary(newW)
            A_old = triangle_areas_abs(X_old)
            A_new = triangle_areas_abs(X_new)
            f_old = softmin_value(A_old, K, tau)
            f_new = softmin_value(A_new, K, tau)
            if f_new >= f_old:
                return True, f_new, f_old
            else:
                # Metropolis criterion
                if T <= 1e-12:
                    return False, f_new, f_old
                prob = math.exp((f_new - f_old) / T)
                if rng.rand() < prob:
                    return True, f_new, f_old
                else:
                    return False, f_new, f_old

        def accept_hard(oldW, newW, T):
            # Greedy on min area with SA fallback
            X_old = cart_from_bary(oldW)
            X_new = cart_from_bary(newW)
            a_old, _, _ = min_area_stats(X_old)
            a_new, _, _ = min_area_stats(X_new)
            if a_new >= a_old:
                return True, a_new, a_old
            else:
                if T <= 1e-12:
                    return False, a_new, a_old
                prob = math.exp((a_new - a_old) / (T))
                if rng.rand() < prob:
                    return True, a_new, a_old
                else:
                    return False, a_new, a_old

        # Multi-start seeds
        seeds = []
        # Some Halton-based
        for _ in range(3):
            W = halton_sequence_in_triangle(n)
            # jitter
            W += rng.normal(scale=0.01, size=W.shape)
            W = project_simplex_rows(W, eps=1e-4)
            seeds.append(W)
        # Lattice style
        for _ in range(3):
            W = lattice_rows(n)
            W += rng.normal(scale=0.008, size=W.shape)
            W = project_simplex_rows(W, eps=1e-4)
            seeds.append(W)
        # Random Dirichlet
        for _ in range(6):
            W = random_dirichlet_points(n)
            seeds.append(W)

        best_W_global = None
        best_min_area_global = -1.0

        # Optimization parameters
        phaseA_iters = 2800
        phaseB_iters = 2600

        for seed_idx, W0 in enumerate(seeds):
            W = project_simplex_rows(W0, eps=1e-4)
            W = ensure_distinct(W)

            # Track best of this run
            best_W = W.copy()
            best_min_area = evaluate_normalized_min_area(W)

            # Phase A: soft-min annealing
            K_soft = min(50, m_tris)  # number of worst triangles to aggregate
            tau0, tau1 = 0.03, 0.005
            Tsoft0, Tsoft1 = 0.01, 0.0005
            alpha_soft = 0.08
            inc_rate, dec_rate = 1.02, 0.7

            last_accept = 0
            for it in range(phaseA_iters):
                frac = it / max(phaseA_iters - 1, 1)
                tau = tau0 * (1.0 - frac) + tau1 * frac
                Tsoft = Tsoft0 * (1.0 - frac) + Tsoft1 * frac

                move_type = rng.rand()
                if move_type < 0.65:
                    W_prop = propose_softmin_step(W, K_soft, tau, alpha_soft)
                elif move_type < 0.85:
                    # closest pair repulsion
                    W_prop = propose_pair_repulsion(W, alpha=0.05)
                else:
                    # small random jitter in barycentric with projection
                    dW = rng.normal(scale=0.01, size=W.shape)
                    dW -= dW.mean(axis=1, keepdims=True)
                    W_prop = project_simplex_rows(W + dW, eps=1e-4)
                    W_prop = ensure_distinct(W_prop)

                accepted, f_new, f_old = accept_soft(W, W_prop, K_soft, tau, Tsoft)
                if accepted:
                    W = W_prop
                    last_accept = it
                    # adapt
                    if move_type < 0.70:
                        alpha_soft *= inc_rate
                        alpha_soft = min(alpha_soft, 0.3)
                else:
                    if move_type < 0.70:
                        alpha_soft *= dec_rate
                        alpha_soft = max(alpha_soft, 0.005)

                # periodic apex repair if stalled
                if it - last_accept > 120:
                    W_repair = propose_apex_repair(W, step0=0.03)
                    W = W_repair
                    last_accept = it

                # track best by true min-area
                cur_min_area = evaluate_normalized_min_area(W)
                if cur_min_area > best_min_area + 1e-12:
                    best_min_area = cur_min_area
                    best_W = W.copy()

            # Phase B: hard maximin with banded worst-set and repairs
            beta0, beta1 = 0.18, 0.03
            Thard0, Thard1 = 0.004, 0.0002
            alpha_band = 0.06

            stall_counter = 0
            last_improve_it = 0
            best_min_area_phaseB = evaluate_normalized_min_area(W)
            W_best_phaseB = W.copy()

            for it in range(phaseB_iters):
                frac = it / max(phaseB_iters - 1, 1)
                beta = beta0 * (1.0 - frac) + beta1 * frac
                Thard = Thard0 * (1.0 - frac) + Thard1 * frac

                # Occasionally widen band if stalled
                if stall_counter > 150:
                    beta_wide = min(0.5, beta * 2.5)
                    W_prop = propose_band_step(W, beta=beta_wide, alpha=alpha_band * 1.2)
                    stall_counter = 0
                else:
                    move_type = rng.rand()
                    if move_type < 0.58:
                        W_prop = propose_band_step(W, beta=beta, alpha=alpha_band)
                    elif move_type < 0.80:
                        W_prop = propose_apex_repair(W, step0=0.025)
                    else:
                        W_prop = propose_pair_repulsion(W, alpha=0.045)

                accepted, a_new, a_old = accept_hard(W, W_prop, Thard)
                if accepted:
                    W = W_prop
                    stall_counter = 0
                else:
                    stall_counter += 1

                # adapt step size
                if accepted and rng.rand() < 0.25:
                    alpha_band = min(alpha_band * 1.03, 0.25)
                elif not accepted and rng.rand() < 0.25:
                    alpha_band = max(alpha_band * 0.8, 0.006)

                # occasional batch repair of worst triangle
                if it % 200 == 0:
                    W = propose_apex_repair(W, step0=0.035)

                cur_min_area = evaluate_normalized_min_area(W)
                if cur_min_area > best_min_area_phaseB + 1e-12:
                    best_min_area_phaseB = cur_min_area
                    W_best_phaseB = W.copy()

            # Choose better of end vs phaseB best
            W_run_best = W_best_phaseB
            min_area_run_best = best_min_area_phaseB

            # Compare to global best
            if min_area_run_best > best_min_area_global + 1e-12:
                best_min_area_global = min_area_run_best
                best_W_global = W_run_best.copy()

        # Fallback if somehow none improved
        if best_W_global is None:
            best_W_global = halton_sequence_in_triangle(n)
        X_best = cart_from_bary(best_W_global)
        # Ensure strict interior and uniqueness (already maintained)
        min_area_norm = evaluate_normalized_min_area(best_W_global)
        return X_best, float(min_area_norm)

    # Run search
    points_np, min_area = find_best_placement(n)
    # Convert to lists for output
    points = points_np.tolist()
    return points, min_area
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```
