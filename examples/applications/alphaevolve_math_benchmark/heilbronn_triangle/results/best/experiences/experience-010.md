Stall-breaking and thin-triangle control mechanisms that stabilized feasibility and lifted the hard min via targeted coordination moves.

- KSAM-AR Hybrid++ couples an adaptive worst-band hard phase with a stall-triggered local subspace CMA-ES: when the hard min Amin shows no improvement for ~S_stagnate iterations in Phase B, it selects the top 2–3 most culpable points and runs a small 16–24 evaluation CMA-ES over zero-sum barycentric deltas with strict interior projection, keeping the best candidate—reuse this targeted subspace CMA as a reliable stall-breaker in maximin geometric searches; in tandem, a triangle-aware closest-pair repulsion that moves the closest pair along oriented area gradients blended with mild Euclidean separation (≈0.7–0.8 gradient, 0.2–0.3 separation), together with barycentric ε-margin projection (ε≈1e−5–1e−4), maintained spacing and feasibility (validity=1.0 in this run), a pattern worth reusing to prevent near-collinearity around close pairs while optimizing the true bottleneck.

```python
#!/usr/bin/env python3
"""KSAM-AR Hybrid++: Geometry-aware maximin optimizer for the Heilbronn triangle problem (n=11).

This implementation searches for placements of n points inside an equilateral triangle
to maximize the minimum area formed by any triple of points, normalized by the area of
the large triangle. It follows a two-phase approach with soft-min annealing (Phase A)
and hard maximin refinement (Phase B), operating with strict interior projection via
barycentric coordinates. Multiple structured and random seeds are used to explore
diverse basins, and a final deterministic polish refines the best result.

Major upgrades in this Hybrid++ version:
- Triangle-aware skinny-triangle repulsion: choose the pair (i, j) that admits the smallest-area triangle
  over all third points r*, and move i and j along the true area-increasing gradients blended with mild
  Euclidean separation.
- Adaptive worst-band hard-phase with apex-normal aggregated repairs and short backtracking.
- Short local subspace CMA-like search on top-culprit points when Phase B stalls, to escape narrow basins.
- Stronger seeding via template (row-layout) island and diversified seeds.

Notes:
- All moves are projected strictly into the triangle interior with a small epsilon margin.
- The code avoids degeneracy and duplicates via repulsion and projection.
- For n=11, the search aims to surpass the 0.0365 benchmark.

Author: Evolved solution.
"""

import json
import itertools
import math
import random
from typing import Tuple, List

import numpy as np


# EVOLVE_START
def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    # Core geometry constants for the equilateral triangle
    h = math.sqrt(3.0) * 0.5  # altitude of unit-side equilateral triangle
    A0 = math.sqrt(3.0) / 4.0  # area of the triangle
    V0 = np.array([0.0, 0.0], dtype=float)
    V1 = np.array([1.0, 0.0], dtype=float)
    V2 = np.array([0.5, h], dtype=float)
    V = np.stack([V0, V1, V2], axis=0)

    rng = np.random.default_rng(123456)
    pyr = random.Random(123456)

    # Utility: rotate vectors by 90 degrees
    def rot90_cw(xy: np.ndarray) -> np.ndarray:
        # (x, y) -> (y, -x)
        return np.stack([xy[..., 1], -xy[..., 0]], axis=-1)

    def rot90_ccw(xy: np.ndarray) -> np.ndarray:
        # (x, y) -> (-y, x)
        return np.stack([-xy[..., 1], xy[..., 0]], axis=-1)

    # Barycentric utility functions
    def to_barycentric(P: np.ndarray) -> np.ndarray:
        # P shape (m, 2). For triangle with V0 = (0,0), V1 = (1,0), V2 = (0.5, h)
        # b2 = y/h, b1 = x - 0.5*b2, b0 = 1 - b1 - b2
        b2 = P[:, 1] / h
        b1 = P[:, 0] - 0.5 * b2
        b0 = 1.0 - b1 - b2
        B = np.stack([b0, b1, b2], axis=1)
        return B

    def from_barycentric(B: np.ndarray) -> np.ndarray:
        # P = b0*V0 + b1*V1 + b2*V2 = (b1 + 0.5*b2, h*b2)
        x = B[:, 1] + 0.5 * B[:, 2]
        y = h * B[:, 2]
        return np.stack([x, y], axis=1)

    def project_inside(P: np.ndarray, eps: float = 1e-4) -> np.ndarray:
        # Project points strictly inside the simplex by clamping barycentric and renormalizing.
        B = to_barycentric(P)
        # Ensure strictly positive coordinates by pushing into the interior
        B = np.maximum(B, eps)
        s = B.sum(axis=1, keepdims=True)
        B = B / s
        # Contract a bit toward interior to avoid touching edges
        B = (1.0 - 3.0 * eps) * B + eps
        return from_barycentric(B)

    # Precompute all triangle index triples
    def all_triples(n: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        comb = list(itertools.combinations(range(n), 3))
        I = np.array([c[0] for c in comb], dtype=int)
        J = np.array([c[1] for c in comb], dtype=int)
        K = np.array([c[2] for c in comb], dtype=int)
        return I, J, K

    TI, TJ, TK = all_triples(n)
    num_tris = len(TI)

    # Area computations
    def triangle_areas(P: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        # Returns (abs_area, signed_area)
        pa = P[TI]
        pb = P[TJ]
        pc = P[TK]
        v1 = pb - pa
        v2 = pc - pa
        # oriented area = 0.5 * cross(v1, v2) = 0.5*(v1_x*v2_y - v1_y*v2_x)
        cross = v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0]
        A_signed = 0.5 * cross
        A_abs = np.abs(A_signed)
        return A_abs, A_signed

    def normalized_min_area(P: np.ndarray) -> float:
        A_abs, _ = triangle_areas(P)
        return float(np.min(A_abs) / A0)

    # Gradient accumulation for a set of triangles with weights
    def accumulate_area_gradients(P: np.ndarray,
                                  tri_indices: np.ndarray,
                                  weights: np.ndarray,
                                  use_abs: bool = True) -> np.ndarray:
        # P (n,2), tri_indices (m,), weights (m,), return gradients dS/dP (n,2)
        G = np.zeros_like(P)
        if tri_indices.size == 0:
            return G
        # Extract points
        i = TI[tri_indices]
        j = TJ[tri_indices]
        k = TK[tri_indices]
        pa = P[i]
        pb = P[j]
        pc = P[k]
        # Oriented area sign for absolute
        _, A_signed_full = triangle_areas(P)
        sgn = np.sign(A_signed_full[tri_indices])
        if not use_abs:
            sgn[:] = 1.0
        # Gradients of oriented area Ao = 0.5 * cross(pb - pa, pc - pa)
        # dAo/dpb = 0.5 * R90_cw(pc - pa)
        # dAo/dpc = 0.5 * R90_ccw(pb - pa)
        # dAo/dpa = - (dAo/dpb + dAo/dpc)
        dA_dpb = 0.5 * rot90_cw(pc - pa)
        dA_dpc = 0.5 * rot90_ccw(pb - pa)
        dA_dpa = - (dA_dpb + dA_dpc)
        # If using absolute area, multiply by sign
        sgn = sgn.reshape(-1, 1)
        dA_dpa *= sgn
        dA_dpb *= sgn
        dA_dpc *= sgn
        # Apply weights
        w = weights.reshape(-1, 1)
        dA_dpa *= w
        dA_dpb *= w
        dA_dpc *= w
        # Scatter-add to G
        np.add.at(G, i, dA_dpa)
        np.add.at(G, j, dA_dpb)
        np.add.at(G, k, dA_dpc)
        # Normalize by A0 to match normalized areas (constant factor)
        G /= A0
        return G

    # Soft-min objective over K-worst triangles
    def softmin_score(P: np.ndarray, K: int, tau: float) -> Tuple[float, np.ndarray, np.ndarray, np.ndarray]:
        A_abs, _ = triangle_areas(P)
        A_norm = A_abs / A0
        idx_sorted = np.argsort(A_norm)
        worst_idx = idx_sorted[:K]
        Ak = A_norm[worst_idx]
        m = float(np.min(Ak))
        z = np.exp(-(Ak - m) / max(tau, 1e-9))
        S = m - tau * float(np.log(np.mean(z)))
        return S, worst_idx, Ak, A_norm

    # Hard min objective and worst band
    def hardmin_band(P: np.ndarray, beta: float) -> Tuple[float, np.ndarray, np.ndarray]:
        A_abs, _ = triangle_areas(P)
        A_norm = A_abs / A0
        Amin = float(np.min(A_norm))
        thr = Amin * (1.0 + beta)
        worst_idx = np.nonzero(A_norm <= thr + 1e-12)[0]
        return Amin, worst_idx, A_norm

    # Closest pair finder (Euclidean)
    def closest_pair(P: np.ndarray) -> Tuple[int, int, float, np.ndarray]:
        nloc = P.shape[0]
        dmin = float('inf')
        idx = (0, 1)
        vec = None
        for ii in range(nloc):
            di = P[ii+1:] - P[ii]
            dsq = np.sum(di * di, axis=1)
            if dsq.size == 0:
                continue
            jrel = int(np.argmin(dsq))
            if dsq[jrel] < dmin:
                dmin = dsq[jrel]
                idx = (ii, ii + 1 + jrel)
                vec = di[jrel]
        dist = math.sqrt(max(dmin, 1e-16))
        if vec is None:
            vec = P[1] - P[0]
        return idx[0], idx[1], dist, vec

    # Oriented + absolute area gradients for a single triangle (i, j, k)
    def single_triangle_abs_gradients(pa: np.ndarray, pb: np.ndarray, pc: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        # Oriented cross
        cross = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
        sgn = 1.0 if cross >= 0.0 else -1.0
        dA_dpb = 0.5 * rot90_cw(pc - pa)
        dA_dpc = 0.5 * rot90_ccw(pb - pa)
        dA_dpa = - (dA_dpb + dA_dpc)
        dA_dpa *= sgn
        dA_dpb *= sgn
        dA_dpc *= sgn
        # Normalize by A0
        return dA_dpa / A0, dA_dpb / A0, dA_dpc / A0

    # Pair-aware skinny-triangle repulsion: find pair (i, j) whose minimum-area triangle over all r is minimal
    def worst_pair_with_third(P: np.ndarray) -> Tuple[int, int, int, float]:
        nloc = P.shape[0]
        best_area = float('inf')
        best = (0, 1, 2, best_area)
        for i in range(nloc - 1):
            for j in range(i + 1, nloc):
                pa = P[i]
                pb = P[j]
                # Vectorized over all r
                mask = np.ones(nloc, dtype=bool)
                mask[i] = False
                mask[j] = False
                r_idx = np.where(mask)[0]
                pr = P[r_idx]
                # area(i, j, r) = 0.5 * |cross(pb - pa, pr - pa)|
                v1 = pb - pa
                v2 = pr - pa
                cross = v1[0] * v2[:, 1] - v1[1] * v2[:, 0]
                areas = 0.5 * np.abs(cross) / A0
                krel = int(np.argmin(areas))
                amin = float(areas[krel])
                if amin < best_area:
                    best_area = amin
                    best = (i, j, int(r_idx[krel]), amin)
        return best

    # Skinny-triangle-aware move blending true area gradients with mild Euclidean separation
    def skinny_triangle_move(P: np.ndarray, step_scale: float = 1.0) -> Tuple[np.ndarray, List[int]]:
        i, j, k, _ = worst_pair_with_third(P)
        Q_new = np.copy(P)
        pa, pb, pc = P[i], P[j], P[k]
        # Gradients for absolute area w.r.t. pa and pb in triangle (i, j, k)
        g_ai, g_bi, _ = single_triangle_abs_gradients(pa, pb, pc)
        # Normalize and blend with Euclidean separation
        gij = pb - pa
        dij = np.linalg.norm(gij)
        if dij < 1e-12:
            sep_dir = rng.normal(0.0, 1.0, size=2)
            sep_dir /= np.linalg.norm(sep_dir) + 1e-12
        else:
            sep_dir = gij / dij
        # Normalize gradients
        if np.linalg.norm(g_ai) < 1e-12:
            g_ai_n = rng.normal(0.0, 1.0, 2)
            g_ai_n /= np.linalg.norm(g_ai_n) + 1e-12
        else:
            g_ai_n = g_ai / (np.linalg.norm(g_ai) + 1e-12)
        if np.linalg.norm(g_bi) < 1e-12:
            g_bi_n = -g_ai_n
        else:
            g_bi_n = g_bi / (np.linalg.norm(g_bi) + 1e-12)
        # Blend parameters: mostly area-gradient, some separation
        alpha = 0.77
        beta = 0.23
        dir_i = alpha * g_ai_n - beta * sep_dir
        dir_j = alpha * g_bi_n + beta * sep_dir
        # Normalize blended directions
        dir_i /= (np.linalg.norm(dir_i) + 1e-12)
        dir_j /= (np.linalg.norm(dir_j) + 1e-12)
        # Adaptive step sizes based on local geometry
        step_i = step_scale
        step_j = step_scale
        Q_new[i] = Q_new[i] + step_i * dir_i
        Q_new[j] = Q_new[j] + step_j * dir_j
        Q_new = project_inside(Q_new, eps=1e-4)
        return Q_new, [i, j]

    # Seeding strategies
    def sample_uniform_triangle(m: int) -> np.ndarray:
        # Uniform in triangle via barycentric sampling
        U = rng.random((m, 2))
        s = U[:, 0]
        t = U[:, 1]
        mask = (s + t) > 1.0
        s[mask] = 1.0 - s[mask]
        t[mask] = 1.0 - t[mask]
        P = (V0 + np.outer(s, (V1 - V0)) + np.outer(t, (V2 - V0)))
        # Strict interior projection
        return project_inside(P, eps=2e-3)

    def seed_row_layout(counts: List[int],
                        margin: float = 0.03,
                        jitter: float = 0.01) -> np.ndarray:
        # Build points on k rows parallel to base, inside triangle
        k = len(counts)
        # Row heights in barycentric b2 (0 at base, 1 at apex)
        # Avoid very close to base or apex
        b2_levels = np.linspace(0.10, 0.85, k) + rng.normal(0.0, 0.01, size=k)
        b2_levels = np.clip(b2_levels, 0.08, 0.88)
        P = []
        for r, m in enumerate(counts):
            b2 = float(b2_levels[r])
            y = h * b2
            x_left = 0.5 * b2
            x_right = 1.0 - 0.5 * b2
            width = x_right - x_left
            # inner margin
            inner = margin * width
            # random offset inside one step
            if m > 1:
                step = (width - 2 * inner) / (m - 1)
                offset = rng.uniform(-0.4, 0.4) * step
                xs = x_left + inner + offset + step * np.arange(m)
            else:
                xs = np.array([0.5 * (x_left + x_right)], dtype=float)
            for xi in xs:
                xi += rng.normal(0.0, jitter * width)
                yi = y + rng.normal(0.0, jitter * h)
                P.append([xi, yi])
        P = np.array(P, dtype=float)
        return project_inside(P, eps=2e-3)

    def seed_blue_noise(m: int, dmin_init: float = 0.18, attempts: int = 5000) -> np.ndarray:
        # Simple dart throwing with relaxation
        pts = []
        dmin = dmin_init
        for it in range(attempts):
            p = sample_uniform_triangle(1)[0]
            ok = True
            for q in pts:
                if np.linalg.norm(p - q) < dmin:
                    ok = False
                    break
            if ok:
                pts.append(p)
                if len(pts) >= m:
                    break
            if (it + 1) % 500 == 0:
                dmin *= 0.90
                dmin = max(dmin, 0.08)
        if len(pts) < m:
            rem = m - len(pts)
            Pextra = sample_uniform_triangle(rem)
            for p in Pextra:
                pts.append(p)
        P = np.array(pts[:m], dtype=float)
        return project_inside(P, eps=2e-3)

    # Template "island": random row-parameter sampling (lightweight surrogate)
    def template_island_seeds(m: int, n_candidates: int = 60, top_k: int = 8) -> List[np.ndarray]:
        seeds = []
        cand = []
        for _ in range(n_candidates):
            # Choose 4 or 5 rows
            nrows = int(rng.choice([4, 5]))
            # Random composition of m into nrows positive integers
            counts = [1] * nrows
            for _r in range(m - nrows):
                counts[rng.integers(0, nrows)] += 1
            pyr.shuffle(counts)
            P = seed_row_layout(counts, margin=0.03, jitter=0.01)
            # Evaluate surrogate: mean of K smallest normalized areas (K ~ 16)
            A_abs, _ = triangle_areas(P)
            A_norm = A_abs / A0
            A_sorted = np.sort(A_norm)
            Ksur = min(16, len(A_sorted))
            surrogate = float(np.mean(A_sorted[:Ksur]))
            cand.append((surrogate, P))
        cand.sort(key=lambda x: -x[0])
        for i in range(min(top_k, len(cand))):
            seeds.append(cand[i][1])
            # Jitter elite to diversify
            for _ in range(2):
                Q = np.copy(seeds[-1])
                Q += rng.normal(0.0, 0.01, size=Q.shape)
                Q = project_inside(Q, eps=2e-3)
                seeds.append(Q)
        return seeds

    # Simple per-point step size adaptation
    def adapt_step_size(s: np.ndarray, idxs: List[int], accept: bool):
        if accept:
            s[idxs] *= 1.05
        else:
            s[idxs] *= 0.7
        np.clip(s, 5e-4, 0.12, out=s)

    # Phase A: Smoothed K-worst soft-min annealing
    def optimize_phase_A(P: np.ndarray,
                         iters: int = 1700,
                         K_start: int = None,
                         K_end: int = None,
                         tau_start: float = 0.020,
                         tau_end: float = 0.004,
                         T_soft_start: float = 0.002,
                         T_soft_end: float = 0.00015) -> Tuple[np.ndarray, float]:
        Q = np.copy(P)
        steps = np.full((n,), 0.02, dtype=float)
        K_start = K_start or max(22, num_tris // 3)
        K_end = K_end or max(9, num_tris // 8)
        best_P = np.copy(Q)
        best_min = normalized_min_area(Q)

        for t in range(iters):
            frac = t / max(1, iters - 1)
            tau = tau_start * (1 - frac) + tau_end * frac
            T_soft = T_soft_start * (1 - frac) + T_soft_end * frac
            K = int(round(K_start * (1 - frac) + K_end * frac))
            S_old, worst_idx, Ak, _ = softmin_score(Q, K, tau)

            move_type = rng.choice([0, 1, 2], p=[0.58, 0.22, 0.20])  # grad, jitter, skinny-repel
            Q_new = np.copy(Q)
            changed = []

            if move_type == 0:
                # Gradient ascent on soft-min over K worst triangles
                mA = float(np.min(Ak))
                w = np.exp(-(Ak - mA) / max(tau, 1e-9))
                if np.sum(w) == 0:
                    w = np.ones_like(w)
                w = w / np.sum(w)
                G = accumulate_area_gradients(Q, worst_idx, w, use_abs=True)
                # Select a point with probability ~ gradient norm among those in worst set
                points_involved = np.zeros(n, dtype=int)
                np.add.at(points_involved, TI[worst_idx], 1)
                np.add.at(points_involved, TJ[worst_idx], 1)
                np.add.at(points_involved, TK[worst_idx], 1)
                norms = np.linalg.norm(G, axis=1)
                mask = points_involved > 0
                candidates = np.where(mask)[0]
                if candidates.size == 0:
                    candidates = np.arange(n)
                weights_pts = norms[candidates]
                if np.sum(weights_pts) <= 1e-12:
                    pidx = int(rng.integers(0, n))
                    d = rng.normal(0.0, 1.0, size=2)
                    d /= np.linalg.norm(d) + 1e-12
                else:
                    weights_pts = weights_pts / (np.sum(weights_pts) + 1e-16)
                    pidx = int(rng.choice(candidates, p=weights_pts))
                    g = G[pidx]
                    d = g / (np.linalg.norm(g) + 1e-12)
                step = float(steps[pidx])
                Q_new[pidx] = Q_new[pidx] + step * d
                Q_new = project_inside(Q_new, eps=1e-4)
                changed = [pidx]
            elif move_type == 1:
                # Random local jitter on a random point
                pidx = int(rng.integers(0, n))
                angle = rng.uniform(0.0, 2 * math.pi)
                direction = np.array([math.cos(angle), math.sin(angle)])
                step = float(0.7 * steps[pidx])
                Q_new[pidx] = Q_new[pidx] + step * direction
                Q_new = project_inside(Q_new, eps=1e-4)
                changed = [pidx]
            else:
                # Triangle-aware skinny repulsion
                # Use a moderate step scale based on average step
                avg_step = float(np.mean(steps))
                Q_new, changed = skinny_triangle_move(Q, step_scale=0.6 * avg_step)

            # Acceptance by soft-min objective (Metropolis)
            S_new, _, _, _ = softmin_score(Q_new, K, tau)
            dS = S_new - S_old
            if dS >= 1e-12 or rng.uniform() < math.exp(dS / max(T_soft, 1e-12)):
                Q = Q_new
                adapt_step_size(steps, changed, accept=True)
                # Track best by true min-area
                curr_min = normalized_min_area(Q)
                if curr_min > best_min + 1e-12:
                    best_min = curr_min
                    best_P = np.copy(Q)
            else:
                adapt_step_size(steps, changed, accept=False)

        return best_P, best_min

    # Local subspace CMA-like exploration focusing on the most culpable 2-3 points
    def local_subspace_search(P: np.ndarray,
                              worst_idx: np.ndarray,
                              steps_hint: np.ndarray,
                              budget: int = 24,
                              n_points: int = 2) -> Tuple[np.ndarray, float, bool]:
        Q = np.copy(P)
        Amin0 = normalized_min_area(Q)
        if len(worst_idx) == 0:
            return Q, Amin0, False
        # Participation counts to pick culprit points
        counts = np.zeros(n, dtype=int)
        np.add.at(counts, TI[worst_idx], 1)
        np.add.at(counts, TJ[worst_idx], 1)
        np.add.at(counts, TK[worst_idx], 1)
        culprits = list(np.argsort(-counts)[:max(2, n_points)])

        # Aggregated gradient on worst set (uniform weights)
        w = np.ones(len(worst_idx), dtype=float) / len(worst_idx)
        G = accumulate_area_gradients(Q, worst_idx, w, use_abs=True)

        # Build basis directions per culprit: gradient dir and its perpendicular
        basis = {}
        for pidx in culprits:
            g = G[pidx]
            if np.linalg.norm(g) < 1e-12:
                v1 = rng.normal(0.0, 1.0, size=2)
                v1 /= np.linalg.norm(v1) + 1e-12
            else:
                v1 = g / (np.linalg.norm(g) + 1e-12)
            v2 = rot90_ccw(v1)
            basis[pidx] = (v1, v2)

        # Sampling scale based on step hints
        sigmas = {p: max(0.5 * float(steps_hint[p]), 0.006) for p in culprits}

        best_Q = np.copy(Q)
        best_A = Amin0
        improved = False

        for _ in range(budget):
            Qcand = np.copy(Q)
            for pidx in culprits:
                v1, v2 = basis[pidx]
                # Gaussian coefficients
                a1 = rng.normal(0.0, sigmas[pidx])
                a2 = rng.normal(0.0, 0.6 * sigmas[pidx])
                step_vec = a1 * v1 + a2 * v2
                Qcand[pidx] = Qcand[pidx] + step_vec
            Qcand = project_inside(Qcand, eps=1e-4)
            Amin_c = normalized_min_area(Qcand)
            if Amin_c > best_A + 1e-12:
                best_A = Amin_c
                best_Q = Qcand
                improved = True

        return best_Q, best_A, improved

    # Phase B: Hard maximin refinement with adaptive worst-band, apex-normal repairs, and subspace search
    def optimize_phase_B(P: np.ndarray,
                         iters: int = 2200,
                         beta_start: float = 0.045,
                         beta_end: float = 0.012,
                         T_hard_start: float = 0.00035,
                         T_hard_end: float = 0.00003) -> Tuple[np.ndarray, float]:
        Q = np.copy(P)
        steps = np.full((n,), 0.017, dtype=float)
        best_P = np.copy(Q)
        best_min = normalized_min_area(Q)
        no_improve = 0
        widen_band_timer = 0

        for t in range(iters):
            frac = t / max(1, iters - 1)
            beta = beta_start * (1 - frac) + beta_end * frac
            Th = T_hard_start * (1 - frac) + T_hard_end * frac
            # Widen band if stagnating
            if no_improve >= 180:
                widen_band_timer = 60
                no_improve = 0
            if widen_band_timer > 0:
                beta = max(beta, 0.06)
                widen_band_timer -= 1

            Amin, worst_idx, A_all = hardmin_band(Q, beta=beta)
            # Aggregated deficiency weights
            thr = Amin * (1.0 + beta)
            deficiency = (thr - (A_all[worst_idx]))
            if deficiency.size == 0:
                deficiency = np.array([1.0])
            w = deficiency / (np.sum(deficiency) + 1e-16)

            move_type = rng.choice([0, 1, 2, 3], p=[0.5, 0.30, 0.12, 0.08])
            Q_new = np.copy(Q)
            changed = []

            if move_type == 0:
                # Subgradient ascent on worst-band triangles
                G = accumulate_area_gradients(Q, worst_idx, w, use_abs=True)
                norms = np.linalg.norm(G, axis=1)
                if np.all(norms < 1e-12):
                    pidx = int(rng.integers(0, n))
                    d = rng.normal(0.0, 1.0, size=2)
                    d /= np.linalg.norm(d) + 1e-12
                else:
                    candidates = np.argsort(-norms)[:max(4, n // 3)]
                    probs = norms[candidates]
                    probs = probs / (np.sum(probs) + 1e-16)
                    pidx = int(rng.choice(candidates, p=probs))
                    g = G[pidx]
                    d = g / (np.linalg.norm(g) + 1e-12)
                step = float(steps[pidx])
                Q_new[pidx] = Q_new[pidx] + step * d
                Q_new = project_inside(Q_new, eps=1e-4)
                changed = [pidx]
            elif move_type == 1:
                # Apex-normal repairs aggregated with short backtracking over top culprits
                G = np.zeros_like(Q)
                # Identify apex per triangle in worst set by smallest height to opposite edge
                i = TI[worst_idx]
                j = TJ[worst_idx]
                k = TK[worst_idx]
                pa = Q[i]
                pb = Q[j]
                pc = Q[k]
                e_ij = pb - pa
                e_jk = pc - pb
                e_ki = pa - pc
                # Heights
                h_k = np.abs(e_ij[:, 0] * (pc[:, 1] - pa[:, 1]) - e_ij[:, 1] * (pc[:, 0] - pa[:, 0])) / (
                    np.linalg.norm(e_ij, axis=1) + 1e-12)
                h_i = np.abs(e_jk[:, 0] * (pa[:, 1] - pb[:, 1]) - e_jk[:, 1] * (pa[:, 0] - pb[:, 0])) / (
                    np.linalg.norm(e_jk, axis=1) + 1e-12)
                h_j = np.abs(e_ki[:, 0] * (pb[:, 1] - pc[:, 1]) - e_ki[:, 1] * (pb[:, 0] - pc[:, 0])) / (
                    np.linalg.norm(e_ki, axis=1) + 1e-12)
                heights = np.stack([h_i, h_j, h_k], axis=1)
                apex = np.argmin(heights, axis=1)
                # Oriented area signs for abs gradients
                _, A_signed_full = triangle_areas(Q)
                sgn = np.sign(A_signed_full[worst_idx]).reshape(-1, 1)
                dirs = np.zeros((len(worst_idx), 2))
                # For apex == i: base jk
                mask_i = apex == 0
                if np.any(mask_i):
                    dirs[mask_i] = 0.5 * rot90_ccw(pc[mask_i] - pb[mask_i])
                # For apex == j: base ki
                mask_j = apex == 1
                if np.any(mask_j):
                    dirs[mask_j] = 0.5 * rot90_ccw(pa[mask_j] - pc[mask_j])
                # For apex == k: base ij
                mask_k = apex == 2
                if np.any(mask_k):
                    dirs[mask_k] = 0.5 * rot90_ccw(pb[mask_k] - pa[mask_k])
                dirs *= sgn
                # Weight by deficiency
                dirs *= deficiency.reshape(-1, 1)
                # Scatter aggregate directions to point gradients
                if np.any(mask_i):
                    np.add.at(G, i[mask_i], dirs[mask_i])
                if np.any(mask_j):
                    np.add.at(G, j[mask_j], dirs[mask_j])
                if np.any(mask_k):
                    np.add.at(G, k[mask_k], dirs[mask_k])
                G /= A0
                # Select top culprits and do short backtracking
                norms = np.linalg.norm(G, axis=1)
                culprits = np.argsort(-norms)[:max(2, n // 4)]
                accepted_any = False
                for pidx in culprits:
                    g = G[pidx]
                    gnorm = np.linalg.norm(g)
                    if gnorm < 1e-12:
                        continue
                    d = g / (gnorm + 1e-12)
                    step = float(0.95 * steps[pidx])
                    # Backtracking line search
                    found = False
                    for _ls in range(6):
                        Q_try = np.copy(Q_new)
                        Q_try[pidx] = Q_try[pidx] + step * d
                        Q_try = project_inside(Q_try, eps=1e-4)
                        if normalized_min_area(Q_try) >= normalized_min_area(Q_new) - 1e-15:
                            Q_new = Q_try
                            found = True
                            accepted_any = True
                            break
                        step *= 0.5
                if accepted_any:
                    changed = list(culprits)
                else:
                    # If nothing accepted, fall back to a tiny nudge along top culprit
                    if len(culprits) > 0:
                        pidx = int(culprits[0])
                        g = G[pidx]
                        if np.linalg.norm(g) > 1e-12:
                            d = g / (np.linalg.norm(g) + 1e-12)
                            Q_new[pidx] = Q_new[pidx] + 0.3 * steps[pidx] * d
                            Q_new = project_inside(Q_new, eps=1e-4)
                            changed = [pidx]
            elif move_type == 2:
                # Mild triangle-aware skinny repulsion
                avg_step = float(np.mean(steps))
                Q_new, changed = skinny_triangle_move(Q, step_scale=0.45 * avg_step)
            else:
                # Small random jitter around worst culprits
                Amin2, worst_idx2, _ = hardmin_band(Q, beta=max(beta * 0.6, 0.01))
                counts = np.zeros(n, dtype=int)
                np.add.at(counts, TI[worst_idx2], 1)
                np.add.at(counts, TJ[worst_idx2], 1)
                np.add.at(counts, TK[worst_idx2], 1)
                culprits = np.argsort(-counts)[:2]
                for pidx in culprits:
                    angle = rng.uniform(0.0, 2 * math.pi)
                    direction = np.array([math.cos(angle), math.sin(angle)])
                    step = float(0.5 * steps[pidx])
                    Q_new[pidx] = Q_new[pidx] + step * direction
                Q_new = project_inside(Q_new, eps=1e-4)
                changed = list(culprits)

            # Acceptance on hard min-area
            Amin_old = normalized_min_area(Q)
            Amin_new = normalized_min_area(Q_new)
            dA = Amin_new - Amin_old
            if dA >= 1e-12 or rng.uniform() < math.exp(dA / max(Th, 1e-12)):
                Q = Q_new
                adapt_step_size(steps, changed, accept=True)
                if Amin_new > best_min + 1e-12:
                    best_min = Amin_new
                    best_P = np.copy(Q)
                    no_improve = 0
                else:
                    no_improve += 1
            else:
                adapt_step_size(steps, changed, accept=False)
                no_improve += 1

            # When stalling, invoke local subspace CMA-like exploration
            if no_improve > 120 and (t % 7 == 0):
                _, worst_idx_now, _ = hardmin_band(Q, beta=max(beta, 0.02))
                Q_loc, A_loc, improved = local_subspace_search(Q, worst_idx_now, steps, budget=24, n_points=2)
                if improved and A_loc > best_min + 1e-12:
                    Q = Q_loc
                    best_min = A_loc
                    best_P = np.copy(Q)
                    no_improve = 0

        return best_P, best_min

    # Final deterministic polish on top culpable points
    def final_polish(P: np.ndarray, iters: int = 100) -> Tuple[np.ndarray, float]:
        Q = np.copy(P)
        best_P = np.copy(Q)
        best_min = normalized_min_area(Q)

        for _ in range(iters):
            Amin, worst_idx, _ = hardmin_band(Q, beta=0.02)
            if len(worst_idx) == 0:
                break
            counts = np.zeros(n, dtype=int)
            np.add.at(counts, TI[worst_idx], 1)
            np.add.at(counts, TJ[worst_idx], 1)
            np.add.at(counts, TK[worst_idx], 1)
            culprits = np.argsort(-counts)[:3]

            # Aggregated gradient on worst set (uniform weights)
            w = np.ones(len(worst_idx), dtype=float) / len(worst_idx)
            G = accumulate_area_gradients(Q, worst_idx, w, use_abs=True)

            improved = False
            for pidx in culprits:
                g = G[pidx]
                if np.linalg.norm(g) < 1e-12:
                    continue
                d1 = g / (np.linalg.norm(g) + 1e-12)
                d2 = rot90_ccw(d1)
                for d in [d1, d2, -d1, -d2]:
                    step = 0.02
                    for _ls in range(9):
                        Q_try = np.copy(Q)
                        Q_try[pidx] = Q_try[pidx] + step * d
                        Q_try = project_inside(Q_try, eps=1e-4)
                        Amin_try = normalized_min_area(Q_try)
                        if Amin_try > best_min + 1e-12:
                            Q = Q_try
                            best_min = Amin_try
                            best_P = np.copy(Q)
                            improved = True
                            break
                        step *= 0.5
                    if improved:
                        break
                if improved:
                    break
            if not improved:
                break
        return best_P, best_min

    # Seed generation
    all_seeds: List[np.ndarray] = []

    # Some structured row partitions for n=11
    row_partitions = [
        [3, 3, 3, 2],
        [3, 2, 3, 3],
        [2, 3, 3, 3],
        [2, 2, 2, 2, 3],
        [2, 2, 3, 2, 2],
        [2, 3, 2, 2, 2],
    ]
    for counts in row_partitions:
        all_seeds.append(seed_row_layout(counts, margin=0.03, jitter=0.012))
        all_seeds.append(seed_row_layout(counts, margin=0.02, jitter=0.008))

    # Blue-noise seeds
    for _ in range(6):
        all_seeds.append(seed_blue_noise(n, dmin_init=0.17))
    for _ in range(4):
        all_seeds.append(seed_blue_noise(n, dmin_init=0.14))

    # Random uniform seeds
    for _ in range(6):
        all_seeds.append(sample_uniform_triangle(n))

    # Template island elite seeds
    island_seeds = template_island_seeds(n, n_candidates=80, top_k=7)
    all_seeds.extend(island_seeds)

    # Elite management
    best_global_P = None
    best_global_min = -1.0

    # Optimize each seed
    for si, seed in enumerate(all_seeds):
        P0 = project_inside(seed, eps=1e-3)

        # Phase A
        Pa, mA = optimize_phase_A(P0, iters=1500)
        # Phase B
        Pb, mB = optimize_phase_B(Pa, iters=2000)
        # Final polish
        Pf, mF = final_polish(Pb, iters=120)

        # Track best
        if mF > best_global_min + 1e-12:
            best_global_min = mF
            best_global_P = Pf

        # Cross-seed jittering of elites
        if si % 6 == 0:
            # jitter current best and reoptimize briefly
            if best_global_P is not None:
                J = np.copy(best_global_P)
                J += rng.normal(0.0, 0.006, size=J.shape)
                J = project_inside(J, eps=1e-4)
                # Short refine
                Pa2, _ = optimize_phase_A(J, iters=400)
                Pb2, _ = optimize_phase_B(Pa2, iters=700)
                Pf2, mF2 = final_polish(Pb2, iters=60)
                if mF2 > best_global_min + 1e-12:
                    best_global_min = mF2
                    best_global_P = Pf2

    # If somehow no best found, fallback to uniform sampling
    if best_global_P is None:
        best_global_P = sample_uniform_triangle(n)
        best_global_min = normalized_min_area(best_global_P)

    # Return points as Python lists for compatibility and the achieved min area
    points_list = best_global_P.tolist()
    return points_list, best_global_min
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```

```python
#!/usr/bin/env python3
"""KSAM-AR Hybrid++: Geometry-aware maximin optimizer for the Heilbronn triangle problem (n=11).

This implementation searches for placements of n points inside an equilateral triangle
to maximize the minimum area formed by any triple of points, normalized by the area of
the large triangle. It follows a two-phase approach with soft-min annealing (Phase A)
and hard maximin refinement (Phase B), operating with strict interior projection via
barycentric coordinates. Multiple structured and random seeds are used to explore
diverse basins, and a final deterministic polish refines the best result.

Major upgrades in this Hybrid++ version:
- Triangle-aware skinny-triangle repulsion: choose the pair (i, j) that admits the smallest-area triangle
  over all third points r*, and move i and j along the true area-increasing gradients blended with mild
  Euclidean separation.
- Adaptive worst-band hard-phase with apex-normal aggregated repairs and short backtracking.
- Short local subspace CMA-like search on top-culprit points when Phase B stalls, to escape narrow basins.
- Stronger seeding via template (row-layout) island and diversified seeds.

Notes:
- All moves are projected strictly into the triangle interior with a small epsilon margin.
- The code avoids degeneracy and duplicates via repulsion and projection.
- For n=11, the search aims to surpass the 0.0365 benchmark.

Author: Evolved solution.
"""

import json
import itertools
import math
import random
from typing import Tuple, List

import numpy as np


# EVOLVE_START
def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    # Core geometry constants for the equilateral triangle
    h = math.sqrt(3.0) * 0.5  # altitude of unit-side equilateral triangle
    A0 = math.sqrt(3.0) / 4.0  # area of the triangle
    V0 = np.array([0.0, 0.0], dtype=float)
    V1 = np.array([1.0, 0.0], dtype=float)
    V2 = np.array([0.5, h], dtype=float)
    V = np.stack([V0, V1, V2], axis=0)

    rng = np.random.default_rng(123456)
    pyr = random.Random(123456)

    # Utility: rotate vectors by 90 degrees
    def rot90_cw(xy: np.ndarray) -> np.ndarray:
        # (x, y) -> (y, -x)
        return np.stack([xy[..., 1], -xy[..., 0]], axis=-1)

    def rot90_ccw(xy: np.ndarray) -> np.ndarray:
        # (x, y) -> (-y, x)
        return np.stack([-xy[..., 1], xy[..., 0]], axis=-1)

    # Barycentric utility functions
    def to_barycentric(P: np.ndarray) -> np.ndarray:
        # P shape (m, 2). For triangle with V0 = (0,0), V1 = (1,0), V2 = (0.5, h)
        # b2 = y/h, b1 = x - 0.5*b2, b0 = 1 - b1 - b2
        b2 = P[:, 1] / h
        b1 = P[:, 0] - 0.5 * b2
        b0 = 1.0 - b1 - b2
        B = np.stack([b0, b1, b2], axis=1)
        return B

    def from_barycentric(B: np.ndarray) -> np.ndarray:
        # P = b0*V0 + b1*V1 + b2*V2 = (b1 + 0.5*b2, h*b2)
        x = B[:, 1] + 0.5 * B[:, 2]
        y = h * B[:, 2]
        return np.stack([x, y], axis=1)

    def project_inside(P: np.ndarray, eps: float = 1e-4) -> np.ndarray:
        # Project points strictly inside the simplex by clamping barycentric and renormalizing.
        B = to_barycentric(P)
        # Ensure strictly positive coordinates by pushing into the interior
        B = np.maximum(B, eps)
        s = B.sum(axis=1, keepdims=True)
        B = B / s
        # Contract a bit toward interior to avoid touching edges
        B = (1.0 - 3.0 * eps) * B + eps
        return from_barycentric(B)

    # Precompute all triangle index triples
    def all_triples(n: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        comb = list(itertools.combinations(range(n), 3))
        I = np.array([c[0] for c in comb], dtype=int)
        J = np.array([c[1] for c in comb], dtype=int)
        K = np.array([c[2] for c in comb], dtype=int)
        return I, J, K

    TI, TJ, TK = all_triples(n)
    num_tris = len(TI)

    # Area computations
    def triangle_areas(P: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        # Returns (abs_area, signed_area)
        pa = P[TI]
        pb = P[TJ]
        pc = P[TK]
        v1 = pb - pa
        v2 = pc - pa
        # oriented area = 0.5 * cross(v1, v2) = 0.5*(v1_x*v2_y - v1_y*v2_x)
        cross = v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0]
        A_signed = 0.5 * cross
        A_abs = np.abs(A_signed)
        return A_abs, A_signed

    def normalized_min_area(P: np.ndarray) -> float:
        A_abs, _ = triangle_areas(P)
        return float(np.min(A_abs) / A0)

    # Gradient accumulation for a set of triangles with weights
    def accumulate_area_gradients(P: np.ndarray,
                                  tri_indices: np.ndarray,
                                  weights: np.ndarray,
                                  use_abs: bool = True) -> np.ndarray:
        # P (n,2), tri_indices (m,), weights (m,), return gradients dS/dP (n,2)
        G = np.zeros_like(P)
        if tri_indices.size == 0:
            return G
        # Extract points
        i = TI[tri_indices]
        j = TJ[tri_indices]
        k = TK[tri_indices]
        pa = P[i]
        pb = P[j]
        pc = P[k]
        # Oriented area sign for absolute
        _, A_signed_full = triangle_areas(P)
        sgn = np.sign(A_signed_full[tri_indices])
        if not use_abs:
            sgn[:] = 1.0
        # Gradients of oriented area Ao = 0.5 * cross(pb - pa, pc - pa)
        # dAo/dpb = 0.5 * R90_cw(pc - pa)
        # dAo/dpc = 0.5 * R90_ccw(pb - pa)
        # dAo/dpa = - (dAo/dpb + dAo/dpc)
        dA_dpb = 0.5 * rot90_cw(pc - pa)
        dA_dpc = 0.5 * rot90_ccw(pb - pa)
        dA_dpa = - (dA_dpb + dA_dpc)
        # If using absolute area, multiply by sign
        sgn = sgn.reshape(-1, 1)
        dA_dpa *= sgn
        dA_dpb *= sgn
        dA_dpc *= sgn
        # Apply weights
        w = weights.reshape(-1, 1)
        dA_dpa *= w
        dA_dpb *= w
        dA_dpc *= w
        # Scatter-add to G
        np.add.at(G, i, dA_dpa)
        np.add.at(G, j, dA_dpb)
        np.add.at(G, k, dA_dpc)
        # Normalize by A0 to match normalized areas (constant factor)
        G /= A0
        return G

    # Soft-min objective over K-worst triangles
    def softmin_score(P: np.ndarray, K: int, tau: float) -> Tuple[float, np.ndarray, np.ndarray, np.ndarray]:
        A_abs, _ = triangle_areas(P)
        A_norm = A_abs / A0
        idx_sorted = np.argsort(A_norm)
        worst_idx = idx_sorted[:K]
        Ak = A_norm[worst_idx]
        m = float(np.min(Ak))
        z = np.exp(-(Ak - m) / max(tau, 1e-9))
        S = m - tau * float(np.log(np.mean(z)))
        return S, worst_idx, Ak, A_norm

    # Hard min objective and worst band
    def hardmin_band(P: np.ndarray, beta: float) -> Tuple[float, np.ndarray, np.ndarray]:
        A_abs, _ = triangle_areas(P)
        A_norm = A_abs / A0
        Amin = float(np.min(A_norm))
        thr = Amin * (1.0 + beta)
        worst_idx = np.nonzero(A_norm <= thr + 1e-12)[0]
        return Amin, worst_idx, A_norm

    # Closest pair finder (Euclidean)
    def closest_pair(P: np.ndarray) -> Tuple[int, int, float, np.ndarray]:
        nloc = P.shape[0]
        dmin = float('inf')
        idx = (0, 1)
        vec = None
        for ii in range(nloc):
            di = P[ii+1:] - P[ii]
            dsq = np.sum(di * di, axis=1)
            if dsq.size == 0:
                continue
            jrel = int(np.argmin(dsq))
            if dsq[jrel] < dmin:
                dmin = dsq[jrel]
                idx = (ii, ii + 1 + jrel)
                vec = di[jrel]
        dist = math.sqrt(max(dmin, 1e-16))
        if vec is None:
            vec = P[1] - P[0]
        return idx[0], idx[1], dist, vec

    # Oriented + absolute area gradients for a single triangle (i, j, k)
    def single_triangle_abs_gradients(pa: np.ndarray, pb: np.ndarray, pc: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        # Oriented cross
        cross = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
        sgn = 1.0 if cross >= 0.0 else -1.0
        dA_dpb = 0.5 * rot90_cw(pc - pa)
        dA_dpc = 0.5 * rot90_ccw(pb - pa)
        dA_dpa = - (dA_dpb + dA_dpc)
        dA_dpa *= sgn
        dA_dpb *= sgn
        dA_dpc *= sgn
        # Normalize by A0
        return dA_dpa / A0, dA_dpb / A0, dA_dpc / A0

    # Pair-aware skinny-triangle repulsion: find pair (i, j) whose minimum-area triangle over all r is minimal
    def worst_pair_with_third(P: np.ndarray) -> Tuple[int, int, int, float]:
        nloc = P.shape[0]
        best_area = float('inf')
        best = (0, 1, 2, best_area)
        for i in range(nloc - 1):
            for j in range(i + 1, nloc):
                pa = P[i]
                pb = P[j]
                # Vectorized over all r
                mask = np.ones(nloc, dtype=bool)
                mask[i] = False
                mask[j] = False
                r_idx = np.where(mask)[0]
                pr = P[r_idx]
                # area(i, j, r) = 0.5 * |cross(pb - pa, pr - pa)|
                v1 = pb - pa
                v2 = pr - pa
                cross = v1[0] * v2[:, 1] - v1[1] * v2[:, 0]
                areas = 0.5 * np.abs(cross) / A0
                krel = int(np.argmin(areas))
                amin = float(areas[krel])
                if amin < best_area:
                    best_area = amin
                    best = (i, j, int(r_idx[krel]), amin)
        return best

    # Skinny-triangle-aware move blending true area gradients with mild Euclidean separation
    def skinny_triangle_move(P: np.ndarray, step_scale: float = 1.0) -> Tuple[np.ndarray, List[int]]:
        i, j, k, _ = worst_pair_with_third(P)
        Q_new = np.copy(P)
        pa, pb, pc = P[i], P[j], P[k]
        # Gradients for absolute area w.r.t. pa and pb in triangle (i, j, k)
        g_ai, g_bi, _ = single_triangle_abs_gradients(pa, pb, pc)
        # Normalize and blend with Euclidean separation
        gij = pb - pa
        dij = np.linalg.norm(gij)
        if dij < 1e-12:
            sep_dir = rng.normal(0.0, 1.0, size=2)
            sep_dir /= np.linalg.norm(sep_dir) + 1e-12
        else:
            sep_dir = gij / dij
        # Normalize gradients
        if np.linalg.norm(g_ai) < 1e-12:
            g_ai_n = rng.normal(0.0, 1.0, 2)
            g_ai_n /= np.linalg.norm(g_ai_n) + 1e-12
        else:
            g_ai_n = g_ai / (np.linalg.norm(g_ai) + 1e-12)
        if np.linalg.norm(g_bi) < 1e-12:
            g_bi_n = -g_ai_n
        else:
            g_bi_n = g_bi / (np.linalg.norm(g_bi) + 1e-12)
        # Blend parameters: mostly area-gradient, some separation
        alpha = 0.77
        beta = 0.23
        dir_i = alpha * g_ai_n - beta * sep_dir
        dir_j = alpha * g_bi_n + beta * sep_dir
        # Normalize blended directions
        dir_i /= (np.linalg.norm(dir_i) + 1e-12)
        dir_j /= (np.linalg.norm(dir_j) + 1e-12)
        # Adaptive step sizes based on local geometry
        step_i = step_scale
        step_j = step_scale
        Q_new[i] = Q_new[i] + step_i * dir_i
        Q_new[j] = Q_new[j] + step_j * dir_j
        Q_new = project_inside(Q_new, eps=1e-4)
        return Q_new, [i, j]

    # Seeding strategies
    def sample_uniform_triangle(m: int) -> np.ndarray:
        # Uniform in triangle via barycentric sampling
        U = rng.random((m, 2))
        s = U[:, 0]
        t = U[:, 1]
        mask = (s + t) > 1.0
        s[mask] = 1.0 - s[mask]
        t[mask] = 1.0 - t[mask]
        P = (V0 + np.outer(s, (V1 - V0)) + np.outer(t, (V2 - V0)))
        # Strict interior projection
        return project_inside(P, eps=2e-3)

    def seed_row_layout(counts: List[int],
                        margin: float = 0.03,
                        jitter: float = 0.01) -> np.ndarray:
        # Build points on k rows parallel to base, inside triangle
        k = len(counts)
        # Row heights in barycentric b2 (0 at base, 1 at apex)
        # Avoid very close to base or apex
        b2_levels = np.linspace(0.10, 0.85, k) + rng.normal(0.0, 0.01, size=k)
        b2_levels = np.clip(b2_levels, 0.08, 0.88)
        P = []
        for r, m in enumerate(counts):
            b2 = float(b2_levels[r])
            y = h * b2
            x_left = 0.5 * b2
            x_right = 1.0 - 0.5 * b2
            width = x_right - x_left
            # inner margin
            inner = margin * width
            # random offset inside one step
            if m > 1:
                step = (width - 2 * inner) / (m - 1)
                offset = rng.uniform(-0.4, 0.4) * step
                xs = x_left + inner + offset + step * np.arange(m)
            else:
                xs = np.array([0.5 * (x_left + x_right)], dtype=float)
            for xi in xs:
                xi += rng.normal(0.0, jitter * width)
                yi = y + rng.normal(0.0, jitter * h)
                P.append([xi, yi])
        P = np.array(P, dtype=float)
        return project_inside(P, eps=2e-3)

    def seed_blue_noise(m: int, dmin_init: float = 0.18, attempts: int = 5000) -> np.ndarray:
        # Simple dart throwing with relaxation
        pts = []
        dmin = dmin_init
        for it in range(attempts):
            p = sample_uniform_triangle(1)[0]
            ok = True
            for q in pts:
                if np.linalg.norm(p - q) < dmin:
                    ok = False
                    break
            if ok:
                pts.append(p)
                if len(pts) >= m:
                    break
            if (it + 1) % 500 == 0:
                dmin *= 0.90
                dmin = max(dmin, 0.08)
        if len(pts) < m:
            rem = m - len(pts)
            Pextra = sample_uniform_triangle(rem)
            for p in Pextra:
                pts.append(p)
        P = np.array(pts[:m], dtype=float)
        return project_inside(P, eps=2e-3)

    # Template "island": random row-parameter sampling (lightweight surrogate)
    def template_island_seeds(m: int, n_candidates: int = 60, top_k: int = 8) -> List[np.ndarray]:
        seeds = []
        cand = []
        for _ in range(n_candidates):
            # Choose 4 or 5 rows
            nrows = int(rng.choice([4, 5]))
            # Random composition of m into nrows positive integers
            counts = [1] * nrows
            for _r in range(m - nrows):
                counts[rng.integers(0, nrows)] += 1
            pyr.shuffle(counts)
            P = seed_row_layout(counts, margin=0.03, jitter=0.01)
            # Evaluate surrogate: mean of K smallest normalized areas (K ~ 16)
            A_abs, _ = triangle_areas(P)
            A_norm = A_abs / A0
            A_sorted = np.sort(A_norm)
            Ksur = min(16, len(A_sorted))
            surrogate = float(np.mean(A_sorted[:Ksur]))
            cand.append((surrogate, P))
        cand.sort(key=lambda x: -x[0])
        for i in range(min(top_k, len(cand))):
            seeds.append(cand[i][1])
            # Jitter elite to diversify
            for _ in range(2):
                Q = np.copy(seeds[-1])
                Q += rng.normal(0.0, 0.01, size=Q.shape)
                Q = project_inside(Q, eps=2e-3)
                seeds.append(Q)
        return seeds

    # Simple per-point step size adaptation
    def adapt_step_size(s: np.ndarray, idxs: List[int], accept: bool):
        if accept:
            s[idxs] *= 1.05
        else:
            s[idxs] *= 0.7
        np.clip(s, 5e-4, 0.12, out=s)

    # Phase A: Smoothed K-worst soft-min annealing
    def optimize_phase_A(P: np.ndarray,
                         iters: int = 1700,
                         K_start: int = None,
                         K_end: int = None,
                         tau_start: float = 0.020,
                         tau_end: float = 0.004,
                         T_soft_start: float = 0.002,
                         T_soft_end: float = 0.00015) -> Tuple[np.ndarray, float]:
        Q = np.copy(P)
        steps = np.full((n,), 0.02, dtype=float)
        K_start = K_start or max(22, num_tris // 3)
        K_end = K_end or max(9, num_tris // 8)
        best_P = np.copy(Q)
        best_min = normalized_min_area(Q)

        for t in range(iters):
            frac = t / max(1, iters - 1)
            tau = tau_start * (1 - frac) + tau_end * frac
            T_soft = T_soft_start * (1 - frac) + T_soft_end * frac
            K = int(round(K_start * (1 - frac) + K_end * frac))
            S_old, worst_idx, Ak, _ = softmin_score(Q, K, tau)

            move_type = rng.choice([0, 1, 2], p=[0.58, 0.22, 0.20])  # grad, jitter, skinny-repel
            Q_new = np.copy(Q)
            changed = []

            if move_type == 0:
                # Gradient ascent on soft-min over K worst triangles
                mA = float(np.min(Ak))
                w = np.exp(-(Ak - mA) / max(tau, 1e-9))
                if np.sum(w) == 0:
                    w = np.ones_like(w)
                w = w / np.sum(w)
                G = accumulate_area_gradients(Q, worst_idx, w, use_abs=True)
                # Select a point with probability ~ gradient norm among those in worst set
                points_involved = np.zeros(n, dtype=int)
                np.add.at(points_involved, TI[worst_idx], 1)
                np.add.at(points_involved, TJ[worst_idx], 1)
                np.add.at(points_involved, TK[worst_idx], 1)
                norms = np.linalg.norm(G, axis=1)
                mask = points_involved > 0
                candidates = np.where(mask)[0]
                if candidates.size == 0:
                    candidates = np.arange(n)
                weights_pts = norms[candidates]
                if np.sum(weights_pts) <= 1e-12:
                    pidx = int(rng.integers(0, n))
                    d = rng.normal(0.0, 1.0, size=2)
                    d /= np.linalg.norm(d) + 1e-12
                else:
                    weights_pts = weights_pts / (np.sum(weights_pts) + 1e-16)
                    pidx = int(rng.choice(candidates, p=weights_pts))
                    g = G[pidx]
                    d = g / (np.linalg.norm(g) + 1e-12)
                step = float(steps[pidx])
                Q_new[pidx] = Q_new[pidx] + step * d
                Q_new = project_inside(Q_new, eps=1e-4)
                changed = [pidx]
            elif move_type == 1:
                # Random local jitter on a random point
                pidx = int(rng.integers(0, n))
                angle = rng.uniform(0.0, 2 * math.pi)
                direction = np.array([math.cos(angle), math.sin(angle)])
                step = float(0.7 * steps[pidx])
                Q_new[pidx] = Q_new[pidx] + step * direction
                Q_new = project_inside(Q_new, eps=1e-4)
                changed = [pidx]
            else:
                # Triangle-aware skinny repulsion
                # Use a moderate step scale based on average step
                avg_step = float(np.mean(steps))
                Q_new, changed = skinny_triangle_move(Q, step_scale=0.6 * avg_step)

            # Acceptance by soft-min objective (Metropolis)
            S_new, _, _, _ = softmin_score(Q_new, K, tau)
            dS = S_new - S_old
            if dS >= 1e-12 or rng.uniform() < math.exp(dS / max(T_soft, 1e-12)):
                Q = Q_new
                adapt_step_size(steps, changed, accept=True)
                # Track best by true min-area
                curr_min = normalized_min_area(Q)
                if curr_min > best_min + 1e-12:
                    best_min = curr_min
                    best_P = np.copy(Q)
            else:
                adapt_step_size(steps, changed, accept=False)

        return best_P, best_min

    # Local subspace CMA-like exploration focusing on the most culpable 2-3 points
    def local_subspace_search(P: np.ndarray,
                              worst_idx: np.ndarray,
                              steps_hint: np.ndarray,
                              budget: int = 24,
                              n_points: int = 2) -> Tuple[np.ndarray, float, bool]:
        Q = np.copy(P)
        Amin0 = normalized_min_area(Q)
        if len(worst_idx) == 0:
            return Q, Amin0, False
        # Participation counts to pick culprit points
        counts = np.zeros(n, dtype=int)
        np.add.at(counts, TI[worst_idx], 1)
        np.add.at(counts, TJ[worst_idx], 1)
        np.add.at(counts, TK[worst_idx], 1)
        culprits = list(np.argsort(-counts)[:max(2, n_points)])

        # Aggregated gradient on worst set (uniform weights)
        w = np.ones(len(worst_idx), dtype=float) / len(worst_idx)
        G = accumulate_area_gradients(Q, worst_idx, w, use_abs=True)

        # Build basis directions per culprit: gradient dir and its perpendicular
        basis = {}
        for pidx in culprits:
            g = G[pidx]
            if np.linalg.norm(g) < 1e-12:
                v1 = rng.normal(0.0, 1.0, size=2)
                v1 /= np.linalg.norm(v1) + 1e-12
            else:
                v1 = g / (np.linalg.norm(g) + 1e-12)
            v2 = rot90_ccw(v1)
            basis[pidx] = (v1, v2)

        # Sampling scale based on step hints
        sigmas = {p: max(0.5 * float(steps_hint[p]), 0.006) for p in culprits}

        best_Q = np.copy(Q)
        best_A = Amin0
        improved = False

        for _ in range(budget):
            Qcand = np.copy(Q)
            for pidx in culprits:
                v1, v2 = basis[pidx]
                # Gaussian coefficients
                a1 = rng.normal(0.0, sigmas[pidx])
                a2 = rng.normal(0.0, 0.6 * sigmas[pidx])
                step_vec = a1 * v1 + a2 * v2
                Qcand[pidx] = Qcand[pidx] + step_vec
            Qcand = project_inside(Qcand, eps=1e-4)
            Amin_c = normalized_min_area(Qcand)
            if Amin_c > best_A + 1e-12:
                best_A = Amin_c
                best_Q = Qcand
                improved = True

        return best_Q, best_A, improved

    # Phase B: Hard maximin refinement with adaptive worst-band, apex-normal repairs, and subspace search
    def optimize_phase_B(P: np.ndarray,
                         iters: int = 2200,
                         beta_start: float = 0.045,
                         beta_end: float = 0.012,
                         T_hard_start: float = 0.00035,
                         T_hard_end: float = 0.00003) -> Tuple[np.ndarray, float]:
        Q = np.copy(P)
        steps = np.full((n,), 0.017, dtype=float)
        best_P = np.copy(Q)
        best_min = normalized_min_area(Q)
        no_improve = 0
        widen_band_timer = 0

        for t in range(iters):
            frac = t / max(1, iters - 1)
            beta = beta_start * (1 - frac) + beta_end * frac
            Th = T_hard_start * (1 - frac) + T_hard_end * frac
            # Widen band if stagnating
            if no_improve >= 180:
                widen_band_timer = 60
                no_improve = 0
            if widen_band_timer > 0:
                beta = max(beta, 0.06)
                widen_band_timer -= 1

            Amin, worst_idx, A_all = hardmin_band(Q, beta=beta)
            # Aggregated deficiency weights
            thr = Amin * (1.0 + beta)
            deficiency = (thr - (A_all[worst_idx]))
            if deficiency.size == 0:
                deficiency = np.array([1.0])
            w = deficiency / (np.sum(deficiency) + 1e-16)

            move_type = rng.choice([0, 1, 2, 3], p=[0.5, 0.30, 0.12, 0.08])
            Q_new = np.copy(Q)
            changed = []

            if move_type == 0:
                # Subgradient ascent on worst-band triangles
                G = accumulate_area_gradients(Q, worst_idx, w, use_abs=True)
                norms = np.linalg.norm(G, axis=1)
                if np.all(norms < 1e-12):
                    pidx = int(rng.integers(0, n))
                    d = rng.normal(0.0, 1.0, size=2)
                    d /= np.linalg.norm(d) + 1e-12
                else:
                    candidates = np.argsort(-norms)[:max(4, n // 3)]
                    probs = norms[candidates]
                    probs = probs / (np.sum(probs) + 1e-16)
                    pidx = int(rng.choice(candidates, p=probs))
                    g = G[pidx]
                    d = g / (np.linalg.norm(g) + 1e-12)
                step = float(steps[pidx])
                Q_new[pidx] = Q_new[pidx] + step * d
                Q_new = project_inside(Q_new, eps=1e-4)
                changed = [pidx]
            elif move_type == 1:
                # Apex-normal repairs aggregated with short backtracking over top culprits
                G = np.zeros_like(Q)
                # Identify apex per triangle in worst set by smallest height to opposite edge
                i = TI[worst_idx]
                j = TJ[worst_idx]
                k = TK[worst_idx]
                pa = Q[i]
                pb = Q[j]
                pc = Q[k]
                e_ij = pb - pa
                e_jk = pc - pb
                e_ki = pa - pc
                # Heights
                h_k = np.abs(e_ij[:, 0] * (pc[:, 1] - pa[:, 1]) - e_ij[:, 1] * (pc[:, 0] - pa[:, 0])) / (
                    np.linalg.norm(e_ij, axis=1) + 1e-12)
                h_i = np.abs(e_jk[:, 0] * (pa[:, 1] - pb[:, 1]) - e_jk[:, 1] * (pa[:, 0] - pb[:, 0])) / (
                    np.linalg.norm(e_jk, axis=1) + 1e-12)
                h_j = np.abs(e_ki[:, 0] * (pb[:, 1] - pc[:, 1]) - e_ki[:, 1] * (pb[:, 0] - pc[:, 0])) / (
                    np.linalg.norm(e_ki, axis=1) + 1e-12)
                heights = np.stack([h_i, h_j, h_k], axis=1)
                apex = np.argmin(heights, axis=1)
                # Oriented area signs for abs gradients
                _, A_signed_full = triangle_areas(Q)
                sgn = np.sign(A_signed_full[worst_idx]).reshape(-1, 1)
                dirs = np.zeros((len(worst_idx), 2))
                # For apex == i: base jk
                mask_i = apex == 0
                if np.any(mask_i):
                    dirs[mask_i] = 0.5 * rot90_ccw(pc[mask_i] - pb[mask_i])
                # For apex == j: base ki
                mask_j = apex == 1
                if np.any(mask_j):
                    dirs[mask_j] = 0.5 * rot90_ccw(pa[mask_j] - pc[mask_j])
                # For apex == k: base ij
                mask_k = apex == 2
                if np.any(mask_k):
                    dirs[mask_k] = 0.5 * rot90_ccw(pb[mask_k] - pa[mask_k])
                dirs *= sgn
                # Weight by deficiency
                dirs *= deficiency.reshape(-1, 1)
                # Scatter aggregate directions to point gradients
                if np.any(mask_i):
                    np.add.at(G, i[mask_i], dirs[mask_i])
                if np.any(mask_j):
                    np.add.at(G, j[mask_j], dirs[mask_j])
                if np.any(mask_k):
                    np.add.at(G, k[mask_k], dirs[mask_k])
                G /= A0
                # Select top culprits and do short backtracking
                norms = np.linalg.norm(G, axis=1)
                culprits = np.argsort(-norms)[:max(2, n // 4)]
                accepted_any = False
                for pidx in culprits:
                    g = G[pidx]
                    gnorm = np.linalg.norm(g)
                    if gnorm < 1e-12:
                        continue
                    d = g / (gnorm + 1e-12)
                    step = float(0.95 * steps[pidx])
                    # Backtracking line search
                    found = False
                    for _ls in range(6):
                        Q_try = np.copy(Q_new)
                        Q_try[pidx] = Q_try[pidx] + step * d
                        Q_try = project_inside(Q_try, eps=1e-4)
                        if normalized_min_area(Q_try) >= normalized_min_area(Q_new) - 1e-15:
                            Q_new = Q_try
                            found = True
                            accepted_any = True
                            break
                        step *= 0.5
                if accepted_any:
                    changed = list(culprits)
                else:
                    # If nothing accepted, fall back to a tiny nudge along top culprit
                    if len(culprits) > 0:
                        pidx = int(culprits[0])
                        g = G[pidx]
                        if np.linalg.norm(g) > 1e-12:
                            d = g / (np.linalg.norm(g) + 1e-12)
                            Q_new[pidx] = Q_new[pidx] + 0.3 * steps[pidx] * d
                            Q_new = project_inside(Q_new, eps=1e-4)
                            changed = [pidx]
            elif move_type == 2:
                # Mild triangle-aware skinny repulsion
                avg_step = float(np.mean(steps))
                Q_new, changed = skinny_triangle_move(Q, step_scale=0.45 * avg_step)
            else:
                # Small random jitter around worst culprits
                Amin2, worst_idx2, _ = hardmin_band(Q, beta=max(beta * 0.6, 0.01))
                counts = np.zeros(n, dtype=int)
                np.add.at(counts, TI[worst_idx2], 1)
                np.add.at(counts, TJ[worst_idx2], 1)
                np.add.at(counts, TK[worst_idx2], 1)
                culprits = np.argsort(-counts)[:2]
                for pidx in culprits:
                    angle = rng.uniform(0.0, 2 * math.pi)
                    direction = np.array([math.cos(angle), math.sin(angle)])
                    step = float(0.5 * steps[pidx])
                    Q_new[pidx] = Q_new[pidx] + step * direction
                Q_new = project_inside(Q_new, eps=1e-4)
                changed = list(culprits)

            # Acceptance on hard min-area
            Amin_old = normalized_min_area(Q)
            Amin_new = normalized_min_area(Q_new)
            dA = Amin_new - Amin_old
            if dA >= 1e-12 or rng.uniform() < math.exp(dA / max(Th, 1e-12)):
                Q = Q_new
                adapt_step_size(steps, changed, accept=True)
                if Amin_new > best_min + 1e-12:
                    best_min = Amin_new
                    best_P = np.copy(Q)
                    no_improve = 0
                else:
                    no_improve += 1
            else:
                adapt_step_size(steps, changed, accept=False)
                no_improve += 1

            # When stalling, invoke local subspace CMA-like exploration
            if no_improve > 120 and (t % 7 == 0):
                _, worst_idx_now, _ = hardmin_band(Q, beta=max(beta, 0.02))
                Q_loc, A_loc, improved = local_subspace_search(Q, worst_idx_now, steps, budget=24, n_points=2)
                if improved and A_loc > best_min + 1e-12:
                    Q = Q_loc
                    best_min = A_loc
                    best_P = np.copy(Q)
                    no_improve = 0

        return best_P, best_min

    # Final deterministic polish on top culpable points
    def final_polish(P: np.ndarray, iters: int = 100) -> Tuple[np.ndarray, float]:
        Q = np.copy(P)
        best_P = np.copy(Q)
        best_min = normalized_min_area(Q)

        for _ in range(iters):
            Amin, worst_idx, _ = hardmin_band(Q, beta=0.02)
            if len(worst_idx) == 0:
                break
            counts = np.zeros(n, dtype=int)
            np.add.at(counts, TI[worst_idx], 1)
            np.add.at(counts, TJ[worst_idx], 1)
            np.add.at(counts, TK[worst_idx], 1)
            culprits = np.argsort(-counts)[:3]

            # Aggregated gradient on worst set (uniform weights)
            w = np.ones(len(worst_idx), dtype=float) / len(worst_idx)
            G = accumulate_area_gradients(Q, worst_idx, w, use_abs=True)

            improved = False
            for pidx in culprits:
                g = G[pidx]
                if np.linalg.norm(g) < 1e-12:
                    continue
                d1 = g / (np.linalg.norm(g) + 1e-12)
                d2 = rot90_ccw(d1)
                for d in [d1, d2, -d1, -d2]:
                    step = 0.02
                    for _ls in range(9):
                        Q_try = np.copy(Q)
                        Q_try[pidx] = Q_try[pidx] + step * d
                        Q_try = project_inside(Q_try, eps=1e-4)
                        Amin_try = normalized_min_area(Q_try)
                        if Amin_try > best_min + 1e-12:
                            Q = Q_try
                            best_min = Amin_try
                            best_P = np.copy(Q)
                            improved = True
                            break
                        step *= 0.5
                    if improved:
                        break
                if improved:
                    break
            if not improved:
                break
        return best_P, best_min

    # Seed generation
    all_seeds: List[np.ndarray] = []

    # Some structured row partitions for n=11
    row_partitions = [
        [3, 3, 3, 2],
        [3, 2, 3, 3],
        [2, 3, 3, 3],
        [2, 2, 2, 2, 3],
        [2, 2, 3, 2, 2],
        [2, 3, 2, 2, 2],
    ]
    for counts in row_partitions:
        all_seeds.append(seed_row_layout(counts, margin=0.03, jitter=0.012))
        all_seeds.append(seed_row_layout(counts, margin=0.02, jitter=0.008))

    # Blue-noise seeds
    for _ in range(6):
        all_seeds.append(seed_blue_noise(n, dmin_init=0.17))
    for _ in range(4):
        all_seeds.append(seed_blue_noise(n, dmin_init=0.14))

    # Random uniform seeds
    for _ in range(6):
        all_seeds.append(sample_uniform_triangle(n))

    # Template island elite seeds
    island_seeds = template_island_seeds(n, n_candidates=80, top_k=7)
    all_seeds.extend(island_seeds)

    # Elite management
    best_global_P = None
    best_global_min = -1.0

    # Optimize each seed
    for si, seed in enumerate(all_seeds):
        P0 = project_inside(seed, eps=1e-3)

        # Phase A
        Pa, mA = optimize_phase_A(P0, iters=1500)
        # Phase B
        Pb, mB = optimize_phase_B(Pa, iters=2000)
        # Final polish
        Pf, mF = final_polish(Pb, iters=120)

        # Track best
        if mF > best_global_min + 1e-12:
            best_global_min = mF
            best_global_P = Pf

        # Cross-seed jittering of elites
        if si % 6 == 0:
            # jitter current best and reoptimize briefly
            if best_global_P is not None:
                J = np.copy(best_global_P)
                J += rng.normal(0.0, 0.006, size=J.shape)
                J = project_inside(J, eps=1e-4)
                # Short refine
                Pa2, _ = optimize_phase_A(J, iters=400)
                Pb2, _ = optimize_phase_B(Pa2, iters=700)
                Pf2, mF2 = final_polish(Pb2, iters=60)
                if mF2 > best_global_min + 1e-12:
                    best_global_min = mF2
                    best_global_P = Pf2

    # If somehow no best found, fallback to uniform sampling
    if best_global_P is None:
        best_global_P = sample_uniform_triangle(n)
        best_global_min = normalized_min_area(best_global_P)

    # Return points as Python lists for compatibility and the achieved min area
    points_list = best_global_P.tolist()
    return points_list, best_global_min
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```
