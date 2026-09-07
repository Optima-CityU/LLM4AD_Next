Actionable design pattern for maximin triangle-area optimization that targets the true bottleneck triangle and adapts worst-set aggregation width based on progress.

- Pair-Aware Skinny-Triangle Repulsion + Adaptive Worst-Band: Target the bottleneck directly in Phase A by, for the closest pair (i, j), identifying r* = argmin_r area(i, j, r) and moving i and j along the oriented area gradients of triangle (i, j, r*), blended with mild Euclidean separation; in Phase B, use an adaptive worst-set band beta that decays from 0.04 to 0.012 and widens to 0.06 for 60 iterations after 120 stagnant iterations to escape stalls. Reuse this pattern when maximin plateaus arise: replace generic pairwise repulsion with triangle-aware gradient pushes and modulate worst-set aggregation width with a decay-plus-stagnation-widening schedule. In this run the method maintained validity 1.0 and reached min_area 0.036023281867207484 (score 0.9869392292385613), indicating these mechanisms are compatible with strict interior constraints and effective near the benchmark.

```python
#!/usr/bin/env python3
"""Heilbronn 11-point search in an equilateral triangle.

This implementation uses KSAM-AR: a two-phase, geometry-aware maximin optimizer.

Highlights:
- Strict interior via barycentric coordinates with projection/margin.
- Multi-start seeding: lattice and blue-noise-like (Poisson-disc) layouts.
- Phase A: K-worst soft-min annealing with per-point adaptive steps, worst-set
  subgradient moves, local jitter, and skinny-triangle-aware repulsion.
- Phase B: Hard maximin with threshold-raising, focused worst-set subgradients,
  geometry-aware apex-normal repairs with backtracking, occasional batch repairs,
  and SA fallback acceptance. Uses an adaptive band beta schedule with
  stagnation-triggered widening.
- Final deterministic polish on the most culpable points (highest worst-set
  participation) using a small pattern search.

Entry point is run_search_point(n), which returns:
  points: list of [x,y] coordinates strictly inside the triangle
  min_area: smallest normalized triangle area across all triples
"""

import json
import itertools
import math
import random
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    # Main search function
    def find_best_placement(n: int):
        rng = np.random.default_rng(42)

        # Triangle vertices in Cartesian coordinates
        V0 = np.array([0.0, 0.0])
        V1 = np.array([1.0, 0.0])
        V2 = np.array([0.5, math.sqrt(3.0) * 0.5])
        V = np.vstack([V0, V1, V2])  # 3x2

        # Triangle area for normalization
        BIG_AREA = (math.sqrt(3.0) / 4.0)

        # Precompute combinations (all C(n,3) triangles)
        combos = np.array(list(itertools.combinations(range(n), 3)), dtype=np.int64)
        Tm = combos.shape[0]

        # Interior margin epsilon to keep strict containment
        eps = 1e-5

        # Conversion matrix for barycentric delta to Cartesian delta and inverse
        # Represent delta_b = [u, v, -u-v], then delta_x = u*(V0 - V2) + v*(V1 - V2)
        M = np.column_stack((V0 - V2, V1 - V2))  # 2x2
        Minv = np.linalg.inv(M)

        def bary_to_cart(B: np.ndarray) -> np.ndarray:
            # B shape (n,3); Cartesian X = B @ V (3x2)
            return B @ V

        def normalize_bary(B: np.ndarray) -> np.ndarray:
            """Project rows to strict interior of the simplex with an epsilon margin."""
            Z = B.astype(np.float64, copy=True)
            Bn = np.maximum(Z, 0.0)
            s = Bn.sum(axis=1, keepdims=True)
            zero_mask = (s[:, 0] <= 0.0)
            if np.any(zero_mask):
                Zm = Z[zero_mask]
                Zm = Zm - np.max(Zm, axis=1, keepdims=True)
                expZ = np.exp(Zm)
                p = expZ / np.sum(expZ, axis=1, keepdims=True)
                Bn[zero_mask] = p
                s[zero_mask] = 1.0
            nonzero_mask = ~zero_mask
            if np.any(nonzero_mask):
                Bn[nonzero_mask] /= s[nonzero_mask]
            # Blend with epsilon margin to ensure strict interior
            Bn = (1.0 - 3.0 * eps) * Bn + eps
            return Bn

        def random_bary_points(n: int, rng: np.random.Generator) -> np.ndarray:
            # Sample random points uniformly in the triangle using barycentric
            B = np.zeros((n, 3), dtype=np.float64)
            for i in range(n):
                u = rng.random()
                v = rng.random()
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                b = np.array([1.0 - u - v, u, v], dtype=np.float64)
                B[i] = b
            return normalize_bary(B)

        def blue_noise_seed(n: int, tries=2000) -> np.ndarray:
            # Simple Poisson-disc-like sampler in XY with a decreasing min separation
            min_d = 0.20
            B_pts = []
            for attempt in range(tries):
                u = rng.random()
                v = rng.random()
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                b = np.array([1.0 - u - v, u, v], dtype=np.float64)
                b = normalize_bary(b.reshape(1, 3))[0]
                if len(B_pts) == 0:
                    B_pts.append(b)
                else:
                    p = (b @ V)
                    ok = True
                    for prev in B_pts:
                        q = (prev @ V)
                        if np.linalg.norm(p - q) < min_d:
                            ok = False
                            break
                    if ok:
                        B_pts.append(b)
                if len(B_pts) == n:
                    break
                if attempt > 0 and attempt % (tries // 4 + 1) == 0:
                    min_d *= 0.88
            B = np.array(B_pts, dtype=np.float64)
            if len(B) < n:
                B_extra = random_bary_points(n - len(B), rng)
                B = np.vstack([B, B_extra])
            return normalize_bary(B)

        def lattice_seed(n: int) -> np.ndarray:
            # Construct a simple lattice-like arrangement: split into rows parallel to base.
            # For 11: [4,3,2,2]
            if n == 11:
                rows = [4, 3, 2, 2]
            else:
                R = max(3, int(math.sqrt(n) + 1))
                base = [max(2, n // R)] * R
                rem = n - sum(base)
                for i in range(rem):
                    base[i % R] += 1
                rows = base
            R = len(rows)
            B_list = []
            for r, cnt in enumerate(rows):
                frac = (r + 1) / (R + 2)  # stay inside
                b2 = eps + (1 - 3 * eps) * frac * 0.9
                rem = 1.0 - b2
                for k in range(cnt):
                    t = (k + 1) / (cnt + 1)
                    b1 = eps + (rem - 2 * eps) * t
                    b0 = rem - b1
                    b = np.array([b0, b1, b2], dtype=np.float64)
                    b = normalize_bary(b.reshape(1, 3))[0]
                    B_list.append(b)
            B = np.array(B_list, dtype=np.float64)
            if len(B) > n:
                B = B[:n]
            if len(B) < n:
                B_extra = random_bary_points(n - len(B), rng)
                B = np.vstack([B, B_extra])
            return normalize_bary(B)

        # Geometry helper: rotate a vector by +90 degrees
        def rot90p(v: np.ndarray) -> np.ndarray:
            return np.array([-v[1], v[0]], dtype=np.float64)

        # Evaluate areas and return per-triple arrays
        def eval_areas(X: np.ndarray):
            A = combos[:, 0]
            B_ = combos[:, 1]
            C_ = combos[:, 2]
            Pa = X[A]
            Pb = X[B_]
            Pc = X[C_]
            ba = Pb - Pa
            ca = Pc - Pa
            cross = ba[:, 0] * ca[:, 1] - ba[:, 1] * ca[:, 0]  # oriented
            areas = 0.5 * np.abs(cross)
            norm_areas = areas / BIG_AREA
            idx_min = int(np.argmin(norm_areas))
            min_area_norm = float(norm_areas[idx_min])
            return norm_areas, cross, idx_min

        # Compute K-worst soft-min surrogate and its per-point gradient
        def k_worst_softmin_grad(X: np.ndarray, K: int, tau: float):
            norm_areas, cross, idx_min = eval_areas(X)
            K_use = min(K, len(norm_areas))
            worst_idx = np.argpartition(norm_areas, K_use - 1)[:K_use]
            Ai = norm_areas[worst_idx]
            # Soft-min value
            m = Ai.min()
            z = np.exp(-(Ai - m) / max(tau, 1e-12))
            Z = z.sum()
            w = z / max(Z, 1e-300)  # weights sum to 1
            S_tau = float(m - tau * math.log((z.mean())))  # -τ log(mean(exp(-Ai/τ)))

            # Per-point gradient in XY
            grad = np.zeros((n, 2), dtype=np.float64)
            counts = np.zeros(n, dtype=np.int64)

            for t, wt in zip(worst_idx, w):
                i, j, k = combos[t]
                a = X[i]; b = X[j]; c = X[k]
                sgn = 1.0 if cross[t] >= 0.0 else -1.0  # orientation
                scale = (0.5 / BIG_AREA) * sgn * wt  # dAi/dX scale times weight
                # grads of oriented cross wrt points
                # ∂cross/∂a = rot90+(c - b)
                # ∂cross/∂b = rot90+(a - c)
                # ∂cross/∂c = rot90+(b - a)
                ga = rot90p(c - b) * scale
                gb = rot90p(a - c) * scale
                gc = rot90p(b - a) * scale
                grad[i] += ga
                grad[j] += gb
                grad[k] += gc
                counts[i] += 1
                counts[j] += 1
                counts[k] += 1

            return S_tau, float(norm_areas[idx_min]), idx_min, grad, worst_idx, counts, norm_areas

        # Hard-phase worst-set aggregated subgradient over triangles within a band from min
        def worst_set_hard_grad(X: np.ndarray, beta: float = 0.015):
            norm_areas, cross, idx_min = eval_areas(X)
            Amin = float(norm_areas[idx_min])
            thresh = Amin * (1.0 + beta)
            worst_mask = (norm_areas <= thresh + 1e-15)
            idxs = np.nonzero(worst_mask)[0]
            if idxs.size == 0:
                # fallback: just min triangle
                idxs = np.array([idx_min], dtype=np.int64)

            grad = np.zeros((n, 2), dtype=np.float64)
            counts = np.zeros(n, dtype=np.int64)

            for t in idxs:
                i, j, k = combos[t]
                a = X[i]; b = X[j]; c = X[k]
                sgn = 1.0 if cross[t] >= 0.0 else -1.0
                scale = (0.5 / BIG_AREA) * sgn / max(idxs.size, 1)
                ga = rot90p(c - b) * scale
                gb = rot90p(a - c) * scale
                gc = rot90p(b - a) * scale
                grad[i] += ga
                grad[j] += gb
                grad[k] += gc
                counts[i] += 1
                counts[j] += 1
                counts[k] += 1

            return Amin, idx_min, grad, idxs, counts, norm_areas

        # Apex and outward normal direction for the worst triangle
        def worst_triangle_apex_and_normal(X: np.ndarray, idx_min_combo: int):
            i, j, k = combos[idx_min_combo]
            pts = [X[i], X[j], X[k]]
            idxs = [i, j, k]

            def dist_signed(a, b, c):
                e = c - b
                norm = np.array([-e[1], e[0]], dtype=np.float64)
                en = np.linalg.norm(norm)
                if en == 0.0:
                    return 0.0, np.array([0.0, 0.0])
                n_hat = norm / en
                s = float(n_hat.dot(a - b))
                return s, n_hat

            d0, n0 = dist_signed(pts[0], pts[1], pts[2])
            d1, n1 = dist_signed(pts[1], pts[0], pts[2])
            d2, n2 = dist_signed(pts[2], pts[0], pts[1])
            absd = [abs(d0), abs(d1), abs(d2)]
            m_idx = int(np.argmin(absd))
            if m_idx == 0:
                a_idx = idxs[0]
                sgn = math.copysign(1.0, d0) if d0 != 0.0 else 1.0
                n_hat = n0
            elif m_idx == 1:
                a_idx = idxs[1]
                sgn = math.copysign(1.0, d1) if d1 != 0.0 else 1.0
                n_hat = n1
            else:
                a_idx = idxs[2]
                sgn = math.copysign(1.0, d2) if d2 != 0.0 else 1.0
                n_hat = n2
            dir_vec = sgn * n_hat
            return a_idx, dir_vec

        # Convert Cartesian delta to barycentric delta of form [u, v, -u-v]
        def cart_delta_to_bary_delta(delta_x: np.ndarray) -> np.ndarray:
            uv = Minv @ delta_x
            u, v = float(uv[0]), float(uv[1])
            return np.array([u, v, -u - v], dtype=np.float64)

        # Repulsion helper: find closest pair indices
        def closest_pair_indices(X: np.ndarray) -> Tuple[int, int]:
            n_ = X.shape[0]
            best = (0, 1)
            best_d2 = float("inf")
            for i in range(n_ - 1):
                diffs = X[i + 1:] - X[i]
                d2s = np.sum(diffs * diffs, axis=1)
                j_rel = int(np.argmin(d2s))
                d2 = float(d2s[j_rel])
                if d2 < best_d2:
                    best_d2 = d2
                    best = (i, i + 1 + j_rel)
            return best

        # Two-phase optimizer for a single seed: returns best B,X,min_area
        def optimize_from_seed(B_init: np.ndarray,
                               phaseA_budget: int = 9000,
                               phaseB_budget: int = 9000):
            # Initialize
            B = B_init.copy()
            X = bary_to_cart(B)

            # Per-point adaptive step sizes (XY space)
            step_xy = np.full(n, 0.055, dtype=np.float64)
            step_xy_hard = np.full(n, 0.035, dtype=np.float64)

            # Phase A: soft-min annealing
            # Adaptive K schedule parameters
            K_min = 7
            K_max = min(20, Tm)
            alpha_K = 0.85

            Tsoft0 = 0.02
            Tsoft_end = 1e-4
            cool_soft = (Tsoft_end / Tsoft0) ** (1.0 / max(1, phaseA_budget - 1))
            Tsoft = Tsoft0

            tau_init = 0.05
            tau_final = 0.005
            tau = tau_init
            cool_tau = (tau_final / tau_init) ** (1.0 / max(1, phaseA_budget - 1))

            # initial eval with adaptive K
            K_cur = int(round(K_min + (K_max - K_min) * ((Tsoft / Tsoft0) ** alpha_K)))
            K_cur = max(K_min, min(K_max, K_cur))
            S_tau, min_area, min_idx, grad_soft, worst_idx, counts, norm_areas = k_worst_softmin_grad(X, K_cur, tau)

            best_B = B.copy()
            best_X = X.copy()
            best_min_area = float(min_area)

            p_subgrad = 0.60
            p_jitter = 0.25
            p_repulse = 0.15

            rng_state = rng

            for it in range(phaseA_budget):
                # Update soft-min gradient and counts with current adaptive K
                K_cur = int(round(K_min + (K_max - K_min) * ((Tsoft / Tsoft0) ** alpha_K)))
                K_cur = max(K_min, min(K_max, K_cur))
                S_tau, min_area, min_idx, grad_soft, worst_idx, counts, norm_areas = k_worst_softmin_grad(X, K_cur, tau)
                tau *= cool_tau

                # Choose move type
                r = rng_state.random()
                B_prop = B.copy()

                if r < p_subgrad:
                    # Subgradient ascent step on a chosen point, biased by worst-set participation
                    probs = counts.astype(np.float64) + 1.0
                    probs /= probs.sum()
                    idx = int(rng_state.choice(np.arange(n), p=probs))

                    g = grad_soft[idx]
                    g_norm = np.linalg.norm(g)
                    if g_norm < 1e-14:
                        # fallback small random dir
                        dir_vec = rng_state.normal(size=2)
                        dir_vec /= (np.linalg.norm(dir_vec) + 1e-12)
                    else:
                        dir_vec = g / g_norm
                    # add tiny noise
                    noise = 0.20 * rng_state.normal(size=2)
                    dir_vec = dir_vec + 0.02 * noise
                    dir_vec /= (np.linalg.norm(dir_vec) + 1e-12)

                    delta_x = step_xy[idx] * dir_vec
                    delta_b = cart_delta_to_bary_delta(delta_x)
                    B_prop[idx] += delta_b
                    B_prop[idx] = normalize_bary(B_prop[idx].reshape(1, 3))[0]

                elif r < p_subgrad + p_jitter:
                    # Local jitter in (u,v) space -> as [du,dv,-du-dv]
                    idx = rng_state.integers(0, n)
                    s = 0.06 * math.sqrt(Tsoft / Tsoft0 + 1e-12)
                    du = rng_state.normal(0.0, s)
                    dv = rng_state.normal(0.0, s)
                    delta_b = np.array([du, dv, -du - dv], dtype=np.float64)
                    B_prop[idx] += delta_b
                    B_prop[idx] = normalize_bary(B_prop[idx].reshape(1, 3))[0]
                else:
                    # Skinny-triangle-aware repulsion for the closest pair (i, j)
                    i, j = closest_pair_indices(X)
                    # Find r* minimizing area(i, j, r)
                    a = X[i]; b = X[j]
                    best_r = None
                    best_area = float("inf")
                    best_cross = 0.0
                    for r_idx in range(n):
                        if r_idx == i or r_idx == j:
                            continue
                        c = X[r_idx]
                        ba = b - a
                        ca = c - a
                        cross = ba[0] * ca[1] - ba[1] * ca[0]
                        area_norm = 0.5 * abs(cross) / BIG_AREA
                        if area_norm < best_area:
                            best_area = area_norm
                            best_r = r_idx
                            best_cross = cross
                    # Compute oriented gradients for triangle (i, j, r*)
                    if best_r is None:
                        # Fallback to Euclidean separation if something went wrong
                        d = X[i] - X[j]
                        norm = np.linalg.norm(d)
                        if norm < 1e-18:
                            d_unit = rng_state.normal(size=2)
                            d_unit = d_unit / (np.linalg.norm(d_unit) + 1e-12)
                        else:
                            d_unit = d / norm
                        step = 0.030 * (0.5 + 0.5 * math.sqrt(Tsoft / Tsoft0 + 1e-12))
                        delta_x_i = step * d_unit
                        delta_x_j = -step * d_unit
                        B_prop[i] += cart_delta_to_bary_delta(delta_x_i)
                        B_prop[j] += cart_delta_to_bary_delta(delta_x_j)
                        B_prop[i] = normalize_bary(B_prop[i].reshape(1, 3))[0]
                        B_prop[j] = normalize_bary(B_prop[j].reshape(1, 3))[0]
                    else:
                        rstar = best_r
                        c = X[rstar]
                        # sgn for absolute area gradient
                        sgn = 1.0 if best_cross >= 0.0 else -1.0
                        scale = (0.5 / BIG_AREA) * sgn
                        # ∂cross/∂i = rot90+(c - b), ∂cross/∂j = rot90+(a - c)
                        g_i = rot90p(c - b) * scale
                        g_j = rot90p(a - c) * scale
                        # Normalize gradients and blend with Euclidean separation
                        gi_norm = np.linalg.norm(g_i)
                        gj_norm = np.linalg.norm(g_j)
                        if gi_norm < 1e-18 or gj_norm < 1e-18:
                            # fallback to Euclidean if gradients collapse
                            d = X[i] - X[j]
                            dn = np.linalg.norm(d)
                            if dn < 1e-18:
                                d_unit = rng_state.normal(size=2)
                                d_unit = d_unit / (np.linalg.norm(d_unit) + 1e-12)
                            else:
                                d_unit = d / dn
                            step = 0.030 * (0.5 + 0.5 * math.sqrt(Tsoft / Tsoft0 + 1e-12))
                            B_prop[i] += cart_delta_to_bary_delta(step * d_unit)
                            B_prop[j] += cart_delta_to_bary_delta(-step * d_unit)
                            B_prop[i] = normalize_bary(B_prop[i].reshape(1, 3))[0]
                            B_prop[j] = normalize_bary(B_prop[j].reshape(1, 3))[0]
                        else:
                            gi_hat = g_i / gi_norm
                            gj_hat = g_j / gj_norm
                            d = X[i] - X[j]
                            dn = np.linalg.norm(d)
                            if dn < 1e-18:
                                du = rng_state.normal(size=2)
                                du = du / (np.linalg.norm(du) + 1e-12)
                            else:
                                du = d / dn
                            # Blended directions
                            dir_i = 0.7 * gi_hat + 0.3 * du
                            dir_j = 0.7 * gj_hat - 0.3 * du
                            di_norm = np.linalg.norm(dir_i); dj_norm = np.linalg.norm(dir_j)
                            if di_norm < 1e-18:
                                dir_i = gi_hat
                            else:
                                dir_i = dir_i / di_norm
                            if dj_norm < 1e-18:
                                dir_j = gj_hat
                            else:
                                dir_j = dir_j / dj_norm
                            step = 0.030 * (0.5 + 0.5 * math.sqrt(Tsoft / Tsoft0 + 1e-12))
                            B_prop[i] += cart_delta_to_bary_delta(step * dir_i)
                            B_prop[j] += cart_delta_to_bary_delta(step * dir_j)
                            B_prop[i] = normalize_bary(B_prop[i].reshape(1, 3))[0]
                            B_prop[j] = normalize_bary(B_prop[j].reshape(1, 3))[0]

                # Evaluate proposal with same adaptive K for this iteration
                X_prop = bary_to_cart(B_prop)
                S_tau_prop, min_area_prop, min_idx_prop, _, _, _, _ = k_worst_softmin_grad(X_prop, K_cur, tau)

                accept = False
                if S_tau_prop > S_tau + 1e-12:
                    accept = True
                else:
                    # Metropolis on soft surrogate
                    if rng_state.random() < math.exp((S_tau_prop - S_tau) / max(Tsoft, 1e-12)):
                        accept = True

                if accept:
                    # Update state
                    # Adjust per-point step sizes for subgradient moves
                    if r < p_subgrad:
                        idx_update = idx
                        step_xy[idx_update] = min(0.12, step_xy[idx_update] * 1.06)
                    B = B_prop
                    X = X_prop
                    S_tau = S_tau_prop
                    min_area = min_area_prop
                    min_idx = min_idx_prop
                    if min_area > best_min_area + 1e-12:
                        best_min_area = min_area
                        best_B = B.copy()
                        best_X = X.copy()
                else:
                    if r < p_subgrad:
                        step_xy[idx] = max(0.003, step_xy[idx] * 0.93)

                # cool SA temp
                Tsoft *= cool_soft

            # Phase B: Hard maximin with threshold raising and apex repairs
            Thard0 = 0.0025
            Thard_end = 1e-6
            cool_hard = (Thard_end / Thard0) ** (1.0 / max(1, phaseB_budget - 1))
            Thard = Thard0

            # Adaptive worst-set band beta schedule
            beta_max_start = 0.04
            beta_min = 0.012
            widen_beta = 0.06
            no_improve_count = 0
            S_stagnate = 120
            J_widen = 60
            widen_countdown = 0

            # Initial worst-set eval with starting beta
            beta_cur = beta_max_start
            Amin, min_idx, grad_hard, worst_set_idx, counts_hard, norm_areas = worst_set_hard_grad(X, beta=beta_cur)
            target = max(best_min_area, Amin)
            stall = 0
            raise_every = max(200, phaseB_budget // 12)
            delta_raise = 0.004  # 0.4%
            Amin_best_phase = Amin  # track best true min area within Phase B

            for it in range(phaseB_budget):
                # Compute current beta from schedule or widened mode
                if widen_countdown > 0:
                    beta_cur = widen_beta
                    widen_countdown -= 1
                else:
                    tfrac = it / max(1, (phaseB_budget - 1))
                    beta_cur = beta_max_start + (beta_min - beta_max_start) * tfrac

                # refresh worst-set and gradients with current beta
                Amin, min_idx, grad_hard, worst_set_idx, counts_hard, norm_areas = worst_set_hard_grad(X, beta=beta_cur)

                r = rng_state.random()
                B_prop = B.copy()
                accepted = False

                if r < 0.50:
                    # Worst-set subgradient move (aggregated over beta_cur band)
                    probs = counts_hard.astype(np.float64) + 1.0
                    probs /= probs.sum()
                    idx = int(rng_state.choice(np.arange(n), p=probs))
                    g = grad_hard[idx]
                    g_norm = np.linalg.norm(g)
                    if g_norm < 1e-14:
                        dir_vec = rng_state.normal(size=2)
                        dir_vec /= (np.linalg.norm(dir_vec) + 1e-12)
                    else:
                        dir_vec = g / g_norm
                    delta_x = step_xy_hard[idx] * dir_vec
                    B_prop[idx] += cart_delta_to_bary_delta(delta_x)
                    B_prop[idx] = normalize_bary(B_prop[idx].reshape(1, 3))[0]

                elif r < 0.90:
                    # Aggregated apex-normal repairs over the current worst set with adaptive band
                    beta_band = beta_cur
                    thresh = Amin * (1.0 + beta_band)
                    # Aggregate per-point outward normals weighted by area deficiency
                    agg_dir = np.zeros((n, 2), dtype=np.float64)
                    agg_w = np.zeros(n, dtype=np.float64)

                    def dist_signed(a, b, c):
                        e = c - b
                        normv = np.array([-e[1], e[0]], dtype=np.float64)
                        en = np.linalg.norm(normv)
                        if en == 0.0:
                            return 0.0, np.array([0.0, 0.0])
                        n_hat = normv / en
                        s = float(n_hat.dot(a - b))
                        return s, n_hat

                    for t in worst_set_idx:
                        i, j, k = combos[t]
                        At = float(norm_areas[t])
                        w_t = max(0.0, thresh - At)
                        if w_t <= 0.0:
                            continue
                        # i vs edge (j,k)
                        si, ni = dist_signed(X[i], X[j], X[k])
                        # j vs edge (i,k)
                        sj, nj = dist_signed(X[j], X[i], X[k])
                        # k vs edge (i,j)
                        sk, nk = dist_signed(X[k], X[i], X[j])
                        agg_dir[i] += w_t * (math.copysign(1.0, si) * ni)
                        agg_dir[j] += w_t * (math.copysign(1.0, sj) * nj)
                        agg_dir[k] += w_t * (math.copysign(1.0, sk) * nk)
                        agg_w[i] += w_t
                        agg_w[j] += w_t
                        agg_w[k] += w_t

                    # Select top culprit points by total deficiency weight
                    order = np.argsort(-agg_w)
                    k_top = min(2, n)
                    top_points = [p for p in order[:k_top] if agg_w[p] > 0.0]

                    improved_any = False
                    X_work = X.copy()
                    B_work = B.copy()
                    Amin_work = Amin

                    for p in top_points:
                        dvec = agg_dir[p]
                        dnorm = np.linalg.norm(dvec)
                        if dnorm < 1e-14:
                            # fallback to gradient direction if aggregation cancels
                            g = grad_hard[p]
                            gnorm = np.linalg.norm(g)
                            if gnorm < 1e-14:
                                continue
                            dvec = g / gnorm
                        else:
                            dvec = dvec / dnorm
                        # Short backtracking line search
                        step = 0.05
                        local_improved = False
                        for _ in range(8):
                            B_try = B_work.copy()
                            B_try[p] += cart_delta_to_bary_delta(step * dvec)
                            B_try[p] = normalize_bary(B_try[p].reshape(1, 3))[0]
                            X_try = bary_to_cart(B_try)
                            Amin_try, _, _, _, _, _ = worst_set_hard_grad(X_try, beta=beta_band)
                            if Amin_try >= Amin_work + 1e-12:
                                B_work = B_try
                                X_work = X_try
                                Amin_work = Amin_try
                                local_improved = True
                                break
                            step *= 0.55
                        if not local_improved:
                            # small nudge anyway
                            B_work[p] += cart_delta_to_bary_delta(step * dvec)
                            B_work[p] = normalize_bary(B_work[p].reshape(1, 3))[0]
                            X_work = bary_to_cart(B_work)
                        improved_any = improved_any or local_improved

                    B_prop = B_work
                else:
                    # Repulsion on closest pair (mild)
                    i, j = closest_pair_indices(X)
                    d = X[i] - X[j]
                    norm = np.linalg.norm(d)
                    if norm < 1e-18:
                        d_unit = rng_state.normal(size=2)
                        d_unit = d_unit / (np.linalg.norm(d_unit) + 1e-12)
                    else:
                        d_unit = d / norm
                    step = 0.020
                    B_prop[i] += cart_delta_to_bary_delta(step * d_unit)
                    B_prop[j] += cart_delta_to_bary_delta(-step * d_unit)
                    B_prop[i] = normalize_bary(B_prop[i].reshape(1, 3))[0]
                    B_prop[j] = normalize_bary(B_prop[j].reshape(1, 3))[0]

                X_prop = bary_to_cart(B_prop)
                Amin_prop, min_idx_prop, _, _, _, _ = worst_set_hard_grad(X_prop, beta=beta_cur)

                if Amin_prop >= max(Amin, target) + 1e-12:
                    accepted = True
                else:
                    # SA fallback on hard objective
                    if Amin_prop >= Amin - 1e-12:
                        accepted = True
                    else:
                        if rng_state.random() < math.exp((Amin_prop - Amin) / max(Thard, 1e-12)):
                            accepted = True

                if accepted:
                    # Update state
                    if r < 0.50:
                        step_xy_hard[idx] = min(0.09, step_xy_hard[idx] * 1.05)
                    B = B_prop
                    X = X_prop
                    Amin = Amin_prop
                    min_idx = min_idx_prop
                    stall = 0
                    if Amin > best_min_area + 1e-12:
                        best_min_area = Amin
                        best_B = B.copy()
                        best_X = X.copy()
                        target = best_min_area
                else:
                    if r < 0.50:
                        step_xy_hard[idx] = max(0.002, step_xy_hard[idx] * 0.92)
                    stall += 1

                # Track phase-B improvements for adaptive beta widening
                if Amin > Amin_best_phase + 1e-12:
                    Amin_best_phase = Amin
                    no_improve_count = 0
                else:
                    no_improve_count += 1
                    if no_improve_count >= S_stagnate and widen_countdown == 0:
                        widen_countdown = J_widen
                        no_improve_count = 0  # reset counter after triggering widening

                # Occasional batch bottleneck repair
                if it > 0 and it % 800 == 0:
                    # Try to slightly repair all triangles in current worst set
                    Amin_curr = Amin
                    B_batch = B.copy()
                    X_batch = X.copy()
                    _, min_idx_tmp, _, worst_idx_set, _, _ = worst_set_hard_grad(X_batch, beta=beta_cur)
                    for t in worst_idx_set:
                        # For each triangle, push its apex outward along the perpendicular
                        def apex_for_triangle(Xloc, tidx):
                            ii, jj, kk = combos[tidx]
                            pts = [Xloc[ii], Xloc[jj], Xloc[kk]]
                            idxs = [ii, jj, kk]
                            def dist_signed2(a, b, c):
                                e = c - b
                                norm = np.array([-e[1], e[0]], dtype=np.float64)
                                en = np.linalg.norm(norm)
                                if en == 0.0:
                                    return 0.0, np.array([0.0, 0.0])
                                n_hat = norm / en
                                s = float(n_hat.dot(a - b))
                                return s, n_hat
                            d0, n0 = dist_signed2(pts[0], pts[1], pts[2])
                            d1, n1 = dist_signed2(pts[1], pts[0], pts[2])
                            d2, n2 = dist_signed2(pts[2], pts[0], pts[1])
                            absd = [abs(d0), abs(d1), abs(d2)]
                            m_idx = int(np.argmin(absd))
                            if m_idx == 0:
                                a_idx = idxs[0]; sgn = math.copysign(1.0, d0) if d0 != 0.0 else 1.0; n_hat = n0
                            elif m_idx == 1:
                                a_idx = idxs[1]; sgn = math.copysign(1.0, d1) if d1 != 0.0 else 1.0; n_hat = n1
                            else:
                                a_idx = idxs[2]; sgn = math.copysign(1.0, d2) if d2 != 0.0 else 1.0; n_hat = n2
                            return a_idx, sgn * n_hat
                        a_idx2, dir_vec2 = apex_for_triangle(X_batch, t)
                        step = 0.02
                        B_batch[a_idx2] += cart_delta_to_bary_delta(step * dir_vec2)
                        B_batch[a_idx2] = normalize_bary(B_batch[a_idx2].reshape(1, 3))[0]
                        X_batch = bary_to_cart(B_batch)
                    Amin_batch, _, _, _, _, _ = worst_set_hard_grad(X_batch, beta=beta_cur)
                    if Amin_batch >= Amin_curr - 1e-12:
                        B = B_batch
                        X = X_batch
                        Amin = Amin_batch
                        if Amin > best_min_area + 1e-12:
                            best_min_area = Amin
                            best_B = B.copy()
                            best_X = X.copy()
                            target = best_min_area

                # Raise threshold to push improvements after stalls
                if stall >= raise_every:
                    target = max(target, best_min_area * (1.0 + delta_raise))
                    stall = 0

                Thard *= cool_hard

            # Final deterministic polish on 2-3 most culpable points
            for _round in range(3):
                Amin, min_idx, grad_hard, worst_set_idx, counts_hard, _ = worst_set_hard_grad(X, beta=0.02)
                order = np.argsort(-counts_hard)  # most participating first
                focus = order[:min(3, n)]
                improved_any = False
                for idx in focus:
                    # directions: gradient and its perpendicular
                    g = grad_hard[idx]
                    g_norm = np.linalg.norm(g)
                    if g_norm < 1e-14:
                        continue
                    dirs = [g / g_norm, rot90p(g) / (np.linalg.norm(rot90p(g)) + 1e-12)]
                    step = 0.03
                    for _ in range(8):
                        best_local = Amin
                        best_dir = None
                        for dvec in dirs:
                            B_try = B.copy()
                            B_try[idx] += cart_delta_to_bary_delta(step * dvec)
                            B_try[idx] = normalize_bary(B_try[idx].reshape(1, 3))[0]
                            X_try = bary_to_cart(B_try)
                            Amin_try, _, _, _, _, _ = worst_set_hard_grad(X_try, beta=0.02)
                            if Amin_try > best_local + 1e-12:
                                best_local = Amin_try
                                best_dir = dvec
                        if best_dir is not None:
                            # accept
                            B[idx] += cart_delta_to_bary_delta(step * best_dir)
                            B[idx] = normalize_bary(B[idx].reshape(1, 3))[0]
                            X = bary_to_cart(B)
                            Amin = best_local
                            improved_any = True
                        else:
                            step *= 0.6
                if Amin > best_min_area + 1e-12:
                    best_min_area = Amin
                    best_B = B.copy()
                    best_X = X.copy()
                if not improved_any:
                    break

            return best_B, best_X, best_min_area

        # Multi-start seeds: lattice + blue-noise + random + jittered variants
        seeds = []
        seeds.append(lattice_seed(n))
        for _ in range(8):
            seeds.append(blue_noise_seed(n))
        for _ in range(4):
            seeds.append(random_bary_points(n, rng))
        # jittered versions of lattice and first blue-noise
        def jitter_seed(B_base: np.ndarray, sigma=0.02, num=4):
            out = []
            for _ in range(num):
                # ensure zero-sum per row change in barycentric form [du,dv,-du-dv]
                # We'll sample du,dv per point
                Bn = B_base.copy()
                for i in range(B_base.shape[0]):
                    du = rng.normal(0.0, sigma)
                    dv = rng.normal(0.0, sigma)
                    Bn[i] += np.array([du, dv, -du - dv], dtype=np.float64)
                out.append(normalize_bary(Bn))
            return out
        # add jitter variants
        seeds.extend(jitter_seed(seeds[0], sigma=0.02, num=3))
        seeds.extend(jitter_seed(seeds[1], sigma=0.015, num=3))

        # Optimize from each seed, keep best
        global_best_area = -1.0
        global_best_B = None
        global_best_X = None

        for idx, seed in enumerate(seeds):
            B_best, X_best, min_area_best = optimize_from_seed(seed, phaseA_budget=8500, phaseB_budget=8000)
            if min_area_best > global_best_area + 1e-12:
                global_best_area = min_area_best
                global_best_B = B_best
                global_best_X = X_best

        # Return points and min_area
        pts = global_best_X.tolist()
        return pts, float(global_best_area)

    # Run the search
    points, min_area = find_best_placement(n)
    return points, min_area
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```

```python
#!/usr/bin/env python3
"""Heilbronn 11-point search in an equilateral triangle.

This implementation uses KSAM-AR: a two-phase, geometry-aware maximin optimizer.

Highlights:
- Strict interior via barycentric coordinates with projection/margin.
- Multi-start seeding: lattice and blue-noise-like (Poisson-disc) layouts.
- Phase A: K-worst soft-min annealing with per-point adaptive steps, worst-set
  subgradient moves, local jitter, and skinny-triangle-aware repulsion.
- Phase B: Hard maximin with threshold-raising, focused worst-set subgradients,
  geometry-aware apex-normal repairs with backtracking, occasional batch repairs,
  and SA fallback acceptance. Uses an adaptive band beta schedule with
  stagnation-triggered widening.
- Final deterministic polish on the most culpable points (highest worst-set
  participation) using a small pattern search.

Entry point is run_search_point(n), which returns:
  points: list of [x,y] coordinates strictly inside the triangle
  min_area: smallest normalized triangle area across all triples
"""

import json
import itertools
import math
import random
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    # Main search function
    def find_best_placement(n: int):
        rng = np.random.default_rng(42)

        # Triangle vertices in Cartesian coordinates
        V0 = np.array([0.0, 0.0])
        V1 = np.array([1.0, 0.0])
        V2 = np.array([0.5, math.sqrt(3.0) * 0.5])
        V = np.vstack([V0, V1, V2])  # 3x2

        # Triangle area for normalization
        BIG_AREA = (math.sqrt(3.0) / 4.0)

        # Precompute combinations (all C(n,3) triangles)
        combos = np.array(list(itertools.combinations(range(n), 3)), dtype=np.int64)
        Tm = combos.shape[0]

        # Interior margin epsilon to keep strict containment
        eps = 1e-5

        # Conversion matrix for barycentric delta to Cartesian delta and inverse
        # Represent delta_b = [u, v, -u-v], then delta_x = u*(V0 - V2) + v*(V1 - V2)
        M = np.column_stack((V0 - V2, V1 - V2))  # 2x2
        Minv = np.linalg.inv(M)

        def bary_to_cart(B: np.ndarray) -> np.ndarray:
            # B shape (n,3); Cartesian X = B @ V (3x2)
            return B @ V

        def normalize_bary(B: np.ndarray) -> np.ndarray:
            """Project rows to strict interior of the simplex with an epsilon margin."""
            Z = B.astype(np.float64, copy=True)
            Bn = np.maximum(Z, 0.0)
            s = Bn.sum(axis=1, keepdims=True)
            zero_mask = (s[:, 0] <= 0.0)
            if np.any(zero_mask):
                Zm = Z[zero_mask]
                Zm = Zm - np.max(Zm, axis=1, keepdims=True)
                expZ = np.exp(Zm)
                p = expZ / np.sum(expZ, axis=1, keepdims=True)
                Bn[zero_mask] = p
                s[zero_mask] = 1.0
            nonzero_mask = ~zero_mask
            if np.any(nonzero_mask):
                Bn[nonzero_mask] /= s[nonzero_mask]
            # Blend with epsilon margin to ensure strict interior
            Bn = (1.0 - 3.0 * eps) * Bn + eps
            return Bn

        def random_bary_points(n: int, rng: np.random.Generator) -> np.ndarray:
            # Sample random points uniformly in the triangle using barycentric
            B = np.zeros((n, 3), dtype=np.float64)
            for i in range(n):
                u = rng.random()
                v = rng.random()
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                b = np.array([1.0 - u - v, u, v], dtype=np.float64)
                B[i] = b
            return normalize_bary(B)

        def blue_noise_seed(n: int, tries=2000) -> np.ndarray:
            # Simple Poisson-disc-like sampler in XY with a decreasing min separation
            min_d = 0.20
            B_pts = []
            for attempt in range(tries):
                u = rng.random()
                v = rng.random()
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                b = np.array([1.0 - u - v, u, v], dtype=np.float64)
                b = normalize_bary(b.reshape(1, 3))[0]
                if len(B_pts) == 0:
                    B_pts.append(b)
                else:
                    p = (b @ V)
                    ok = True
                    for prev in B_pts:
                        q = (prev @ V)
                        if np.linalg.norm(p - q) < min_d:
                            ok = False
                            break
                    if ok:
                        B_pts.append(b)
                if len(B_pts) == n:
                    break
                if attempt > 0 and attempt % (tries // 4 + 1) == 0:
                    min_d *= 0.88
            B = np.array(B_pts, dtype=np.float64)
            if len(B) < n:
                B_extra = random_bary_points(n - len(B), rng)
                B = np.vstack([B, B_extra])
            return normalize_bary(B)

        def lattice_seed(n: int) -> np.ndarray:
            # Construct a simple lattice-like arrangement: split into rows parallel to base.
            # For 11: [4,3,2,2]
            if n == 11:
                rows = [4, 3, 2, 2]
            else:
                R = max(3, int(math.sqrt(n) + 1))
                base = [max(2, n // R)] * R
                rem = n - sum(base)
                for i in range(rem):
                    base[i % R] += 1
                rows = base
            R = len(rows)
            B_list = []
            for r, cnt in enumerate(rows):
                frac = (r + 1) / (R + 2)  # stay inside
                b2 = eps + (1 - 3 * eps) * frac * 0.9
                rem = 1.0 - b2
                for k in range(cnt):
                    t = (k + 1) / (cnt + 1)
                    b1 = eps + (rem - 2 * eps) * t
                    b0 = rem - b1
                    b = np.array([b0, b1, b2], dtype=np.float64)
                    b = normalize_bary(b.reshape(1, 3))[0]
                    B_list.append(b)
            B = np.array(B_list, dtype=np.float64)
            if len(B) > n:
                B = B[:n]
            if len(B) < n:
                B_extra = random_bary_points(n - len(B), rng)
                B = np.vstack([B, B_extra])
            return normalize_bary(B)

        # Geometry helper: rotate a vector by +90 degrees
        def rot90p(v: np.ndarray) -> np.ndarray:
            return np.array([-v[1], v[0]], dtype=np.float64)

        # Evaluate areas and return per-triple arrays
        def eval_areas(X: np.ndarray):
            A = combos[:, 0]
            B_ = combos[:, 1]
            C_ = combos[:, 2]
            Pa = X[A]
            Pb = X[B_]
            Pc = X[C_]
            ba = Pb - Pa
            ca = Pc - Pa
            cross = ba[:, 0] * ca[:, 1] - ba[:, 1] * ca[:, 0]  # oriented
            areas = 0.5 * np.abs(cross)
            norm_areas = areas / BIG_AREA
            idx_min = int(np.argmin(norm_areas))
            min_area_norm = float(norm_areas[idx_min])
            return norm_areas, cross, idx_min

        # Compute K-worst soft-min surrogate and its per-point gradient
        def k_worst_softmin_grad(X: np.ndarray, K: int, tau: float):
            norm_areas, cross, idx_min = eval_areas(X)
            K_use = min(K, len(norm_areas))
            worst_idx = np.argpartition(norm_areas, K_use - 1)[:K_use]
            Ai = norm_areas[worst_idx]
            # Soft-min value
            m = Ai.min()
            z = np.exp(-(Ai - m) / max(tau, 1e-12))
            Z = z.sum()
            w = z / max(Z, 1e-300)  # weights sum to 1
            S_tau = float(m - tau * math.log((z.mean())))  # -τ log(mean(exp(-Ai/τ)))

            # Per-point gradient in XY
            grad = np.zeros((n, 2), dtype=np.float64)
            counts = np.zeros(n, dtype=np.int64)

            for t, wt in zip(worst_idx, w):
                i, j, k = combos[t]
                a = X[i]; b = X[j]; c = X[k]
                sgn = 1.0 if cross[t] >= 0.0 else -1.0  # orientation
                scale = (0.5 / BIG_AREA) * sgn * wt  # dAi/dX scale times weight
                # grads of oriented cross wrt points
                # ∂cross/∂a = rot90+(c - b)
                # ∂cross/∂b = rot90+(a - c)
                # ∂cross/∂c = rot90+(b - a)
                ga = rot90p(c - b) * scale
                gb = rot90p(a - c) * scale
                gc = rot90p(b - a) * scale
                grad[i] += ga
                grad[j] += gb
                grad[k] += gc
                counts[i] += 1
                counts[j] += 1
                counts[k] += 1

            return S_tau, float(norm_areas[idx_min]), idx_min, grad, worst_idx, counts, norm_areas

        # Hard-phase worst-set aggregated subgradient over triangles within a band from min
        def worst_set_hard_grad(X: np.ndarray, beta: float = 0.015):
            norm_areas, cross, idx_min = eval_areas(X)
            Amin = float(norm_areas[idx_min])
            thresh = Amin * (1.0 + beta)
            worst_mask = (norm_areas <= thresh + 1e-15)
            idxs = np.nonzero(worst_mask)[0]
            if idxs.size == 0:
                # fallback: just min triangle
                idxs = np.array([idx_min], dtype=np.int64)

            grad = np.zeros((n, 2), dtype=np.float64)
            counts = np.zeros(n, dtype=np.int64)

            for t in idxs:
                i, j, k = combos[t]
                a = X[i]; b = X[j]; c = X[k]
                sgn = 1.0 if cross[t] >= 0.0 else -1.0
                scale = (0.5 / BIG_AREA) * sgn / max(idxs.size, 1)
                ga = rot90p(c - b) * scale
                gb = rot90p(a - c) * scale
                gc = rot90p(b - a) * scale
                grad[i] += ga
                grad[j] += gb
                grad[k] += gc
                counts[i] += 1
                counts[j] += 1
                counts[k] += 1

            return Amin, idx_min, grad, idxs, counts, norm_areas

        # Apex and outward normal direction for the worst triangle
        def worst_triangle_apex_and_normal(X: np.ndarray, idx_min_combo: int):
            i, j, k = combos[idx_min_combo]
            pts = [X[i], X[j], X[k]]
            idxs = [i, j, k]

            def dist_signed(a, b, c):
                e = c - b
                norm = np.array([-e[1], e[0]], dtype=np.float64)
                en = np.linalg.norm(norm)
                if en == 0.0:
                    return 0.0, np.array([0.0, 0.0])
                n_hat = norm / en
                s = float(n_hat.dot(a - b))
                return s, n_hat

            d0, n0 = dist_signed(pts[0], pts[1], pts[2])
            d1, n1 = dist_signed(pts[1], pts[0], pts[2])
            d2, n2 = dist_signed(pts[2], pts[0], pts[1])
            absd = [abs(d0), abs(d1), abs(d2)]
            m_idx = int(np.argmin(absd))
            if m_idx == 0:
                a_idx = idxs[0]
                sgn = math.copysign(1.0, d0) if d0 != 0.0 else 1.0
                n_hat = n0
            elif m_idx == 1:
                a_idx = idxs[1]
                sgn = math.copysign(1.0, d1) if d1 != 0.0 else 1.0
                n_hat = n1
            else:
                a_idx = idxs[2]
                sgn = math.copysign(1.0, d2) if d2 != 0.0 else 1.0
                n_hat = n2
            dir_vec = sgn * n_hat
            return a_idx, dir_vec

        # Convert Cartesian delta to barycentric delta of form [u, v, -u-v]
        def cart_delta_to_bary_delta(delta_x: np.ndarray) -> np.ndarray:
            uv = Minv @ delta_x
            u, v = float(uv[0]), float(uv[1])
            return np.array([u, v, -u - v], dtype=np.float64)

        # Repulsion helper: find closest pair indices
        def closest_pair_indices(X: np.ndarray) -> Tuple[int, int]:
            n_ = X.shape[0]
            best = (0, 1)
            best_d2 = float("inf")
            for i in range(n_ - 1):
                diffs = X[i + 1:] - X[i]
                d2s = np.sum(diffs * diffs, axis=1)
                j_rel = int(np.argmin(d2s))
                d2 = float(d2s[j_rel])
                if d2 < best_d2:
                    best_d2 = d2
                    best = (i, i + 1 + j_rel)
            return best

        # Two-phase optimizer for a single seed: returns best B,X,min_area
        def optimize_from_seed(B_init: np.ndarray,
                               phaseA_budget: int = 9000,
                               phaseB_budget: int = 9000):
            # Initialize
            B = B_init.copy()
            X = bary_to_cart(B)

            # Per-point adaptive step sizes (XY space)
            step_xy = np.full(n, 0.055, dtype=np.float64)
            step_xy_hard = np.full(n, 0.035, dtype=np.float64)

            # Phase A: soft-min annealing
            # Adaptive K schedule parameters
            K_min = 7
            K_max = min(20, Tm)
            alpha_K = 0.85

            Tsoft0 = 0.02
            Tsoft_end = 1e-4
            cool_soft = (Tsoft_end / Tsoft0) ** (1.0 / max(1, phaseA_budget - 1))
            Tsoft = Tsoft0

            tau_init = 0.05
            tau_final = 0.005
            tau = tau_init
            cool_tau = (tau_final / tau_init) ** (1.0 / max(1, phaseA_budget - 1))

            # initial eval with adaptive K
            K_cur = int(round(K_min + (K_max - K_min) * ((Tsoft / Tsoft0) ** alpha_K)))
            K_cur = max(K_min, min(K_max, K_cur))
            S_tau, min_area, min_idx, grad_soft, worst_idx, counts, norm_areas = k_worst_softmin_grad(X, K_cur, tau)

            best_B = B.copy()
            best_X = X.copy()
            best_min_area = float(min_area)

            p_subgrad = 0.60
            p_jitter = 0.25
            p_repulse = 0.15

            rng_state = rng

            for it in range(phaseA_budget):
                # Update soft-min gradient and counts with current adaptive K
                K_cur = int(round(K_min + (K_max - K_min) * ((Tsoft / Tsoft0) ** alpha_K)))
                K_cur = max(K_min, min(K_max, K_cur))
                S_tau, min_area, min_idx, grad_soft, worst_idx, counts, norm_areas = k_worst_softmin_grad(X, K_cur, tau)
                tau *= cool_tau

                # Choose move type
                r = rng_state.random()
                B_prop = B.copy()

                if r < p_subgrad:
                    # Subgradient ascent step on a chosen point, biased by worst-set participation
                    probs = counts.astype(np.float64) + 1.0
                    probs /= probs.sum()
                    idx = int(rng_state.choice(np.arange(n), p=probs))

                    g = grad_soft[idx]
                    g_norm = np.linalg.norm(g)
                    if g_norm < 1e-14:
                        # fallback small random dir
                        dir_vec = rng_state.normal(size=2)
                        dir_vec /= (np.linalg.norm(dir_vec) + 1e-12)
                    else:
                        dir_vec = g / g_norm
                    # add tiny noise
                    noise = 0.20 * rng_state.normal(size=2)
                    dir_vec = dir_vec + 0.02 * noise
                    dir_vec /= (np.linalg.norm(dir_vec) + 1e-12)

                    delta_x = step_xy[idx] * dir_vec
                    delta_b = cart_delta_to_bary_delta(delta_x)
                    B_prop[idx] += delta_b
                    B_prop[idx] = normalize_bary(B_prop[idx].reshape(1, 3))[0]

                elif r < p_subgrad + p_jitter:
                    # Local jitter in (u,v) space -> as [du,dv,-du-dv]
                    idx = rng_state.integers(0, n)
                    s = 0.06 * math.sqrt(Tsoft / Tsoft0 + 1e-12)
                    du = rng_state.normal(0.0, s)
                    dv = rng_state.normal(0.0, s)
                    delta_b = np.array([du, dv, -du - dv], dtype=np.float64)
                    B_prop[idx] += delta_b
                    B_prop[idx] = normalize_bary(B_prop[idx].reshape(1, 3))[0]
                else:
                    # Skinny-triangle-aware repulsion for the closest pair (i, j)
                    i, j = closest_pair_indices(X)
                    # Find r* minimizing area(i, j, r)
                    a = X[i]; b = X[j]
                    best_r = None
                    best_area = float("inf")
                    best_cross = 0.0
                    for r_idx in range(n):
                        if r_idx == i or r_idx == j:
                            continue
                        c = X[r_idx]
                        ba = b - a
                        ca = c - a
                        cross = ba[0] * ca[1] - ba[1] * ca[0]
                        area_norm = 0.5 * abs(cross) / BIG_AREA
                        if area_norm < best_area:
                            best_area = area_norm
                            best_r = r_idx
                            best_cross = cross
                    # Compute oriented gradients for triangle (i, j, r*)
                    if best_r is None:
                        # Fallback to Euclidean separation if something went wrong
                        d = X[i] - X[j]
                        norm = np.linalg.norm(d)
                        if norm < 1e-18:
                            d_unit = rng_state.normal(size=2)
                            d_unit = d_unit / (np.linalg.norm(d_unit) + 1e-12)
                        else:
                            d_unit = d / norm
                        step = 0.030 * (0.5 + 0.5 * math.sqrt(Tsoft / Tsoft0 + 1e-12))
                        delta_x_i = step * d_unit
                        delta_x_j = -step * d_unit
                        B_prop[i] += cart_delta_to_bary_delta(delta_x_i)
                        B_prop[j] += cart_delta_to_bary_delta(delta_x_j)
                        B_prop[i] = normalize_bary(B_prop[i].reshape(1, 3))[0]
                        B_prop[j] = normalize_bary(B_prop[j].reshape(1, 3))[0]
                    else:
                        rstar = best_r
                        c = X[rstar]
                        # sgn for absolute area gradient
                        sgn = 1.0 if best_cross >= 0.0 else -1.0
                        scale = (0.5 / BIG_AREA) * sgn
                        # ∂cross/∂i = rot90+(c - b), ∂cross/∂j = rot90+(a - c)
                        g_i = rot90p(c - b) * scale
                        g_j = rot90p(a - c) * scale
                        # Normalize gradients and blend with Euclidean separation
                        gi_norm = np.linalg.norm(g_i)
                        gj_norm = np.linalg.norm(g_j)
                        if gi_norm < 1e-18 or gj_norm < 1e-18:
                            # fallback to Euclidean if gradients collapse
                            d = X[i] - X[j]
                            dn = np.linalg.norm(d)
                            if dn < 1e-18:
                                d_unit = rng_state.normal(size=2)
                                d_unit = d_unit / (np.linalg.norm(d_unit) + 1e-12)
                            else:
                                d_unit = d / dn
                            step = 0.030 * (0.5 + 0.5 * math.sqrt(Tsoft / Tsoft0 + 1e-12))
                            B_prop[i] += cart_delta_to_bary_delta(step * d_unit)
                            B_prop[j] += cart_delta_to_bary_delta(-step * d_unit)
                            B_prop[i] = normalize_bary(B_prop[i].reshape(1, 3))[0]
                            B_prop[j] = normalize_bary(B_prop[j].reshape(1, 3))[0]
                        else:
                            gi_hat = g_i / gi_norm
                            gj_hat = g_j / gj_norm
                            d = X[i] - X[j]
                            dn = np.linalg.norm(d)
                            if dn < 1e-18:
                                du = rng_state.normal(size=2)
                                du = du / (np.linalg.norm(du) + 1e-12)
                            else:
                                du = d / dn
                            # Blended directions
                            dir_i = 0.7 * gi_hat + 0.3 * du
                            dir_j = 0.7 * gj_hat - 0.3 * du
                            di_norm = np.linalg.norm(dir_i); dj_norm = np.linalg.norm(dir_j)
                            if di_norm < 1e-18:
                                dir_i = gi_hat
                            else:
                                dir_i = dir_i / di_norm
                            if dj_norm < 1e-18:
                                dir_j = gj_hat
                            else:
                                dir_j = dir_j / dj_norm
                            step = 0.030 * (0.5 + 0.5 * math.sqrt(Tsoft / Tsoft0 + 1e-12))
                            B_prop[i] += cart_delta_to_bary_delta(step * dir_i)
                            B_prop[j] += cart_delta_to_bary_delta(step * dir_j)
                            B_prop[i] = normalize_bary(B_prop[i].reshape(1, 3))[0]
                            B_prop[j] = normalize_bary(B_prop[j].reshape(1, 3))[0]

                # Evaluate proposal with same adaptive K for this iteration
                X_prop = bary_to_cart(B_prop)
                S_tau_prop, min_area_prop, min_idx_prop, _, _, _, _ = k_worst_softmin_grad(X_prop, K_cur, tau)

                accept = False
                if S_tau_prop > S_tau + 1e-12:
                    accept = True
                else:
                    # Metropolis on soft surrogate
                    if rng_state.random() < math.exp((S_tau_prop - S_tau) / max(Tsoft, 1e-12)):
                        accept = True

                if accept:
                    # Update state
                    # Adjust per-point step sizes for subgradient moves
                    if r < p_subgrad:
                        idx_update = idx
                        step_xy[idx_update] = min(0.12, step_xy[idx_update] * 1.06)
                    B = B_prop
                    X = X_prop
                    S_tau = S_tau_prop
                    min_area = min_area_prop
                    min_idx = min_idx_prop
                    if min_area > best_min_area + 1e-12:
                        best_min_area = min_area
                        best_B = B.copy()
                        best_X = X.copy()
                else:
                    if r < p_subgrad:
                        step_xy[idx] = max(0.003, step_xy[idx] * 0.93)

                # cool SA temp
                Tsoft *= cool_soft

            # Phase B: Hard maximin with threshold raising and apex repairs
            Thard0 = 0.0025
            Thard_end = 1e-6
            cool_hard = (Thard_end / Thard0) ** (1.0 / max(1, phaseB_budget - 1))
            Thard = Thard0

            # Adaptive worst-set band beta schedule
            beta_max_start = 0.04
            beta_min = 0.012
            widen_beta = 0.06
            no_improve_count = 0
            S_stagnate = 120
            J_widen = 60
            widen_countdown = 0

            # Initial worst-set eval with starting beta
            beta_cur = beta_max_start
            Amin, min_idx, grad_hard, worst_set_idx, counts_hard, norm_areas = worst_set_hard_grad(X, beta=beta_cur)
            target = max(best_min_area, Amin)
            stall = 0
            raise_every = max(200, phaseB_budget // 12)
            delta_raise = 0.004  # 0.4%
            Amin_best_phase = Amin  # track best true min area within Phase B

            for it in range(phaseB_budget):
                # Compute current beta from schedule or widened mode
                if widen_countdown > 0:
                    beta_cur = widen_beta
                    widen_countdown -= 1
                else:
                    tfrac = it / max(1, (phaseB_budget - 1))
                    beta_cur = beta_max_start + (beta_min - beta_max_start) * tfrac

                # refresh worst-set and gradients with current beta
                Amin, min_idx, grad_hard, worst_set_idx, counts_hard, norm_areas = worst_set_hard_grad(X, beta=beta_cur)

                r = rng_state.random()
                B_prop = B.copy()
                accepted = False

                if r < 0.50:
                    # Worst-set subgradient move (aggregated over beta_cur band)
                    probs = counts_hard.astype(np.float64) + 1.0
                    probs /= probs.sum()
                    idx = int(rng_state.choice(np.arange(n), p=probs))
                    g = grad_hard[idx]
                    g_norm = np.linalg.norm(g)
                    if g_norm < 1e-14:
                        dir_vec = rng_state.normal(size=2)
                        dir_vec /= (np.linalg.norm(dir_vec) + 1e-12)
                    else:
                        dir_vec = g / g_norm
                    delta_x = step_xy_hard[idx] * dir_vec
                    B_prop[idx] += cart_delta_to_bary_delta(delta_x)
                    B_prop[idx] = normalize_bary(B_prop[idx].reshape(1, 3))[0]

                elif r < 0.90:
                    # Aggregated apex-normal repairs over the current worst set with adaptive band
                    beta_band = beta_cur
                    thresh = Amin * (1.0 + beta_band)
                    # Aggregate per-point outward normals weighted by area deficiency
                    agg_dir = np.zeros((n, 2), dtype=np.float64)
                    agg_w = np.zeros(n, dtype=np.float64)

                    def dist_signed(a, b, c):
                        e = c - b
                        normv = np.array([-e[1], e[0]], dtype=np.float64)
                        en = np.linalg.norm(normv)
                        if en == 0.0:
                            return 0.0, np.array([0.0, 0.0])
                        n_hat = normv / en
                        s = float(n_hat.dot(a - b))
                        return s, n_hat

                    for t in worst_set_idx:
                        i, j, k = combos[t]
                        At = float(norm_areas[t])
                        w_t = max(0.0, thresh - At)
                        if w_t <= 0.0:
                            continue
                        # i vs edge (j,k)
                        si, ni = dist_signed(X[i], X[j], X[k])
                        # j vs edge (i,k)
                        sj, nj = dist_signed(X[j], X[i], X[k])
                        # k vs edge (i,j)
                        sk, nk = dist_signed(X[k], X[i], X[j])
                        agg_dir[i] += w_t * (math.copysign(1.0, si) * ni)
                        agg_dir[j] += w_t * (math.copysign(1.0, sj) * nj)
                        agg_dir[k] += w_t * (math.copysign(1.0, sk) * nk)
                        agg_w[i] += w_t
                        agg_w[j] += w_t
                        agg_w[k] += w_t

                    # Select top culprit points by total deficiency weight
                    order = np.argsort(-agg_w)
                    k_top = min(2, n)
                    top_points = [p for p in order[:k_top] if agg_w[p] > 0.0]

                    improved_any = False
                    X_work = X.copy()
                    B_work = B.copy()
                    Amin_work = Amin

                    for p in top_points:
                        dvec = agg_dir[p]
                        dnorm = np.linalg.norm(dvec)
                        if dnorm < 1e-14:
                            # fallback to gradient direction if aggregation cancels
                            g = grad_hard[p]
                            gnorm = np.linalg.norm(g)
                            if gnorm < 1e-14:
                                continue
                            dvec = g / gnorm
                        else:
                            dvec = dvec / dnorm
                        # Short backtracking line search
                        step = 0.05
                        local_improved = False
                        for _ in range(8):
                            B_try = B_work.copy()
                            B_try[p] += cart_delta_to_bary_delta(step * dvec)
                            B_try[p] = normalize_bary(B_try[p].reshape(1, 3))[0]
                            X_try = bary_to_cart(B_try)
                            Amin_try, _, _, _, _, _ = worst_set_hard_grad(X_try, beta=beta_band)
                            if Amin_try >= Amin_work + 1e-12:
                                B_work = B_try
                                X_work = X_try
                                Amin_work = Amin_try
                                local_improved = True
                                break
                            step *= 0.55
                        if not local_improved:
                            # small nudge anyway
                            B_work[p] += cart_delta_to_bary_delta(step * dvec)
                            B_work[p] = normalize_bary(B_work[p].reshape(1, 3))[0]
                            X_work = bary_to_cart(B_work)
                        improved_any = improved_any or local_improved

                    B_prop = B_work
                else:
                    # Repulsion on closest pair (mild)
                    i, j = closest_pair_indices(X)
                    d = X[i] - X[j]
                    norm = np.linalg.norm(d)
                    if norm < 1e-18:
                        d_unit = rng_state.normal(size=2)
                        d_unit = d_unit / (np.linalg.norm(d_unit) + 1e-12)
                    else:
                        d_unit = d / norm
                    step = 0.020
                    B_prop[i] += cart_delta_to_bary_delta(step * d_unit)
                    B_prop[j] += cart_delta_to_bary_delta(-step * d_unit)
                    B_prop[i] = normalize_bary(B_prop[i].reshape(1, 3))[0]
                    B_prop[j] = normalize_bary(B_prop[j].reshape(1, 3))[0]

                X_prop = bary_to_cart(B_prop)
                Amin_prop, min_idx_prop, _, _, _, _ = worst_set_hard_grad(X_prop, beta=beta_cur)

                if Amin_prop >= max(Amin, target) + 1e-12:
                    accepted = True
                else:
                    # SA fallback on hard objective
                    if Amin_prop >= Amin - 1e-12:
                        accepted = True
                    else:
                        if rng_state.random() < math.exp((Amin_prop - Amin) / max(Thard, 1e-12)):
                            accepted = True

                if accepted:
                    # Update state
                    if r < 0.50:
                        step_xy_hard[idx] = min(0.09, step_xy_hard[idx] * 1.05)
                    B = B_prop
                    X = X_prop
                    Amin = Amin_prop
                    min_idx = min_idx_prop
                    stall = 0
                    if Amin > best_min_area + 1e-12:
                        best_min_area = Amin
                        best_B = B.copy()
                        best_X = X.copy()
                        target = best_min_area
                else:
                    if r < 0.50:
                        step_xy_hard[idx] = max(0.002, step_xy_hard[idx] * 0.92)
                    stall += 1

                # Track phase-B improvements for adaptive beta widening
                if Amin > Amin_best_phase + 1e-12:
                    Amin_best_phase = Amin
                    no_improve_count = 0
                else:
                    no_improve_count += 1
                    if no_improve_count >= S_stagnate and widen_countdown == 0:
                        widen_countdown = J_widen
                        no_improve_count = 0  # reset counter after triggering widening

                # Occasional batch bottleneck repair
                if it > 0 and it % 800 == 0:
                    # Try to slightly repair all triangles in current worst set
                    Amin_curr = Amin
                    B_batch = B.copy()
                    X_batch = X.copy()
                    _, min_idx_tmp, _, worst_idx_set, _, _ = worst_set_hard_grad(X_batch, beta=beta_cur)
                    for t in worst_idx_set:
                        # For each triangle, push its apex outward along the perpendicular
                        def apex_for_triangle(Xloc, tidx):
                            ii, jj, kk = combos[tidx]
                            pts = [Xloc[ii], Xloc[jj], Xloc[kk]]
                            idxs = [ii, jj, kk]
                            def dist_signed2(a, b, c):
                                e = c - b
                                norm = np.array([-e[1], e[0]], dtype=np.float64)
                                en = np.linalg.norm(norm)
                                if en == 0.0:
                                    return 0.0, np.array([0.0, 0.0])
                                n_hat = norm / en
                                s = float(n_hat.dot(a - b))
                                return s, n_hat
                            d0, n0 = dist_signed2(pts[0], pts[1], pts[2])
                            d1, n1 = dist_signed2(pts[1], pts[0], pts[2])
                            d2, n2 = dist_signed2(pts[2], pts[0], pts[1])
                            absd = [abs(d0), abs(d1), abs(d2)]
                            m_idx = int(np.argmin(absd))
                            if m_idx == 0:
                                a_idx = idxs[0]; sgn = math.copysign(1.0, d0) if d0 != 0.0 else 1.0; n_hat = n0
                            elif m_idx == 1:
                                a_idx = idxs[1]; sgn = math.copysign(1.0, d1) if d1 != 0.0 else 1.0; n_hat = n1
                            else:
                                a_idx = idxs[2]; sgn = math.copysign(1.0, d2) if d2 != 0.0 else 1.0; n_hat = n2
                            return a_idx, sgn * n_hat
                        a_idx2, dir_vec2 = apex_for_triangle(X_batch, t)
                        step = 0.02
                        B_batch[a_idx2] += cart_delta_to_bary_delta(step * dir_vec2)
                        B_batch[a_idx2] = normalize_bary(B_batch[a_idx2].reshape(1, 3))[0]
                        X_batch = bary_to_cart(B_batch)
                    Amin_batch, _, _, _, _, _ = worst_set_hard_grad(X_batch, beta=beta_cur)
                    if Amin_batch >= Amin_curr - 1e-12:
                        B = B_batch
                        X = X_batch
                        Amin = Amin_batch
                        if Amin > best_min_area + 1e-12:
                            best_min_area = Amin
                            best_B = B.copy()
                            best_X = X.copy()
                            target = best_min_area

                # Raise threshold to push improvements after stalls
                if stall >= raise_every:
                    target = max(target, best_min_area * (1.0 + delta_raise))
                    stall = 0

                Thard *= cool_hard

            # Final deterministic polish on 2-3 most culpable points
            for _round in range(3):
                Amin, min_idx, grad_hard, worst_set_idx, counts_hard, _ = worst_set_hard_grad(X, beta=0.02)
                order = np.argsort(-counts_hard)  # most participating first
                focus = order[:min(3, n)]
                improved_any = False
                for idx in focus:
                    # directions: gradient and its perpendicular
                    g = grad_hard[idx]
                    g_norm = np.linalg.norm(g)
                    if g_norm < 1e-14:
                        continue
                    dirs = [g / g_norm, rot90p(g) / (np.linalg.norm(rot90p(g)) + 1e-12)]
                    step = 0.03
                    for _ in range(8):
                        best_local = Amin
                        best_dir = None
                        for dvec in dirs:
                            B_try = B.copy()
                            B_try[idx] += cart_delta_to_bary_delta(step * dvec)
                            B_try[idx] = normalize_bary(B_try[idx].reshape(1, 3))[0]
                            X_try = bary_to_cart(B_try)
                            Amin_try, _, _, _, _, _ = worst_set_hard_grad(X_try, beta=0.02)
                            if Amin_try > best_local + 1e-12:
                                best_local = Amin_try
                                best_dir = dvec
                        if best_dir is not None:
                            # accept
                            B[idx] += cart_delta_to_bary_delta(step * best_dir)
                            B[idx] = normalize_bary(B[idx].reshape(1, 3))[0]
                            X = bary_to_cart(B)
                            Amin = best_local
                            improved_any = True
                        else:
                            step *= 0.6
                if Amin > best_min_area + 1e-12:
                    best_min_area = Amin
                    best_B = B.copy()
                    best_X = X.copy()
                if not improved_any:
                    break

            return best_B, best_X, best_min_area

        # Multi-start seeds: lattice + blue-noise + random + jittered variants
        seeds = []
        seeds.append(lattice_seed(n))
        for _ in range(8):
            seeds.append(blue_noise_seed(n))
        for _ in range(4):
            seeds.append(random_bary_points(n, rng))
        # jittered versions of lattice and first blue-noise
        def jitter_seed(B_base: np.ndarray, sigma=0.02, num=4):
            out = []
            for _ in range(num):
                # ensure zero-sum per row change in barycentric form [du,dv,-du-dv]
                # We'll sample du,dv per point
                Bn = B_base.copy()
                for i in range(B_base.shape[0]):
                    du = rng.normal(0.0, sigma)
                    dv = rng.normal(0.0, sigma)
                    Bn[i] += np.array([du, dv, -du - dv], dtype=np.float64)
                out.append(normalize_bary(Bn))
            return out
        # add jitter variants
        seeds.extend(jitter_seed(seeds[0], sigma=0.02, num=3))
        seeds.extend(jitter_seed(seeds[1], sigma=0.015, num=3))

        # Optimize from each seed, keep best
        global_best_area = -1.0
        global_best_B = None
        global_best_X = None

        for idx, seed in enumerate(seeds):
            B_best, X_best, min_area_best = optimize_from_seed(seed, phaseA_budget=8500, phaseB_budget=8000)
            if min_area_best > global_best_area + 1e-12:
                global_best_area = min_area_best
                global_best_B = B_best
                global_best_X = X_best

        # Return points and min_area
        pts = global_best_X.tolist()
        return pts, float(global_best_area)

    # Run the search
    points, min_area = find_best_placement(n)
    return points, min_area
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```
