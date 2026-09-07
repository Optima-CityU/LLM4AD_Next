Why this run worked well and what to reuse in future designs that minimize max autoconvolution under nonnegativity constraints.

- TriPP-AWAPS Fusion: Coarse-to-Fine Plateau-Union Minimax with Finer Arch Sweep and 4D Peak Shaving: Strong seeding via an arch width sweep S = {0.70, 0.75, 0.80, 0.90, 0.95, 1.00, 1.05, 1.15, 1.30} followed by acceptance-tested four-direction micro peak-shaving with r = min(a[i*], a[j*], δ/2) and δ ≈ 3e−3 (with backtracking) reduced the dominant self-convolution peak before iterative optimization and produced a better initial objective.
- TriPP-AWAPS Fusion: Coarse-to-Fine Plateau-Union Minimax with Finer Arch Sweep and 4D Peak Shaving: A plateau-union Top-K minimax surrogate blended with an inverse-pressure tail and neighbor expansion maintained stable focus on the true worst lags under peak-identity swaps, and interleaving hard-max polish, cutting-plane averaging of top lags, and multi-peak mass transport yielded monotone, acceptance-tested improvements the smooth surrogate alone missed.
- TriPP-AWAPS Fusion: Coarse-to-Fine Plateau-Union Minimax with Finer Arch Sweep and 4D Peak Shaving: A coarse-to-fine schedule (optimize at n0, upsample, and refine at n = 600 with tightened surrogate parameters) plus strict best-so-far tracking and time safety achieved upper_bound = 1.512601889422432, target_ratio = 0.9951726297094472, validity = 1.0, and eval_time ≈ 42.7 s on this event.

```python
#!/usr/bin/env python3
"""Optimized step function search for minimizing a max-autoconvolution-based objective.

This script implements a specialized optimizer targeting the quantity:
    F(a) = 2 * n * max(conv(a, a)) / (sum(a)^2)
under the constraint a[i] >= 0. We work on the simplex sum(a) = 1 via a softmax
parameterization of logits to maintain nonnegativity and unit sum. The algorithm
uses an FFT-accelerated correlation for gradients, a plateau-aware Top-K minimax
surrogate, peak-participation weighted entropy, and interleaved acceptance-tested
refinements to robustly reduce the true objective. It continuously tracks and returns
the best sequence found within a strict time budget discipline, with a coarse-to-fine
multi-resolution refinement that finishes at length 600.
"""

import json
import time
from typing import List, Tuple, Optional

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """
    Generate an optimized nonnegative sequence (step function heights) of length 600.

    Algorithm outline (TriPP‑CTF + multi-pair transport + richer seeds + global-sink transport):
    - Work on the simplex via logits s and a = softmax(s).
    - Coarse-to-fine multi-resolution: optimize at n=512, upsample, then refine at n=600.
    - Multi-start initialization with arch-like and diverse smooth seeds (+ accepted micro peak-shaving).
    - Enriched seed portfolio: Beta(α, α), flat-top raised cosines, Gaussians, triangular, uniform, and perturbed symmetric seeds.
    - Plateau-aware Top-K minimax surrogate optimized via Adam/AMSGrad.
    - FFT-accelerated autoconvolution and correlation for efficient updates.
    - Peak-participation weighted entropy regularization (annealed).
    - Interleaved non-smooth refinements (cutting-plane, hard-max polish, mass transport including multi-pair and global-sink).
    - Pressure microsteps (Gear G2) with acceptance testing to stabilize oscillations.
    - Symmetry encouragement (acceptance-tested) and mild regularization to stabilize optimization.
    - Strict time management with best-so-far tracking and acceptance testing.

    Returns:
        np.ndarray of shape (600,) with nonnegative entries summing to 1.
    """
    # ----------------------------
    # Global time budget
    # ----------------------------
    hard_time_budget_sec = 995.0
    t_start = time.time()

    def remaining_time() -> float:
        return hard_time_budget_sec - (time.time() - t_start)

    # ----------------------------
    # Utility functions independent of n
    # ----------------------------
    def softmax(s: np.ndarray) -> np.ndarray:
        """Stable softmax producing a probability vector."""
        m = np.max(s)
        e = np.exp(s - m)
        sum_e = np.sum(e)
        if sum_e <= 0:
            return np.ones_like(s) / s.size
        return e / sum_e

    def next_pow_two(m: int) -> int:
        """Next power of two >= m."""
        return 1 << (m - 1).bit_length()

    def conv_full_fft(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """
        Full linear convolution using real FFT padding.
        Length = len(x) + len(y) - 1.
        """
        L = len(x) + len(y) - 1
        size = next_pow_two(L)
        X = np.fft.rfft(x, size)
        Y = np.fft.rfft(y, size)
        Z = X * Y
        z = np.fft.irfft(Z, size)
        return z[:L]

    def project_to_simplex(v: np.ndarray, s: float = 1.0) -> np.ndarray:
        """
        Euclidean projection of v onto the probability simplex {x >= 0, sum x = s}.
        Implements the algorithm of Wang & Carreira-Perpinan (2013).
        """
        if s <= 0:
            return np.zeros_like(v)
        n = v.size
        u = np.sort(v)[::-1]
        cssv = np.cumsum(u) - s
        ind = np.arange(1, n + 1)
        cond = u - cssv / ind > 0
        if not np.any(cond):
            # If all elements are negative, return uniform
            return np.ones_like(v) * (s / n)
        rho = ind[cond][-1]
        theta = cssv[cond][-1] / rho
        w = np.maximum(v - theta, 0)
        return w

    def upsample_linear(a_src: np.ndarray, n_dst: int) -> np.ndarray:
        """
        Linear upsampling of a_src to length n_dst over index domain [0, 1].
        """
        n_src = len(a_src)
        if n_src == n_dst:
            return a_src.copy()
        x_src = np.linspace(0.0, 1.0, n_src)
        x_dst = np.linspace(0.0, 1.0, n_dst)
        a_dst = np.interp(x_dst, x_src, a_src)
        a_dst = np.maximum(a_dst, 0.0)
        a_dst_sum = np.sum(a_dst)
        if a_dst_sum <= 0:
            return np.ones(n_dst) / n_dst
        return a_dst / a_dst_sum

    # ----------------------------
    # Global-sink mass transport helper (visible to final_polish)
    # ----------------------------
    def mass_transport_global_sink(a: np.ndarray, b: np.ndarray, total_delta: float, Q: int, sink_count: int, backtracks: int = 4) -> Tuple[np.ndarray, bool]:
        """
        Global-sink mass transport (top-level helper):
        - Identify worst lag k* and the per-index contribution q[i] = a[i] * a[k*-i].
        - Remove small mass across the top-Q contributors proportionally (bounded by availability).
        - Redistribute to `sink_count` indices with smallest q[i], distance-biased away from the k* center.
        - Accept only if the true objective decreases; backtrack total_delta on failure up to `backtracks`.
        """
        n_local = len(a)
        if b is None or b.size != (2 * n_local - 1):
            # Recompute b if not provided correctly
            b = np.convolve(a, a)

        k_star = int(np.argmax(b))
        i_min = max(0, k_star - (n_local - 1))
        i_max = min(n_local - 1, k_star)
        if i_max < i_min:
            return a, False

        idx = np.arange(i_min, i_max + 1)
        q = np.zeros(n_local, dtype=float)
        q[i_min:i_max + 1] = a[i_min:i_max + 1] * a[k_star - idx]

        valid_contrib_idx = np.where(q > 0)[0]
        if valid_contrib_idx.size == 0:
            return a, False
        Q_eff = min(Q, valid_contrib_idx.size)
        top_idx = np.argpartition(-q[valid_contrib_idx], Q_eff - 1)[:Q_eff]
        top_global_idx = valid_contrib_idx[top_idx]
        top_sorted = top_global_idx[np.argsort(-q[top_global_idx])]

        sink_eff = min(sink_count, n_local)
        sink_idx = np.argpartition(q, sink_eff - 1)[:sink_eff]
        # Distance bias away from center i_center ~ k_star/2
        i_center = k_star / 2.0
        dist = np.abs(np.arange(n_local) - i_center) + 1e-6
        sink_weights = 1.0 / dist
        sink_weights = sink_weights[sink_idx]
        sink_weights = sink_weights / max(np.sum(sink_weights), 1e-12)

        def true_obj(a_vec: np.ndarray) -> float:
            sa = np.sum(a_vec)
            if sa <= 0:
                return np.inf
            bb = np.convolve(a_vec, a_vec)
            return float(2.0 * n_local * np.max(bb) / (sa ** 2))

        delta_total = total_delta
        F_old = true_obj(a)

        for _ in range(backtracks):
            a_candidate = a.copy()
            removed = 0.0
            per_rem = delta_total / max(Q_eff, 1)
            for i_star in top_sorted:
                j_star = k_star - i_star
                if j_star < 0 or j_star >= n_local:
                    continue
                d = min(per_rem, a_candidate[i_star], a_candidate[j_star])
                if d <= 0:
                    continue
                a_candidate[i_star] -= d
                a_candidate[j_star] -= d
                removed += 2.0 * d

            if removed <= 0:
                delta_total *= 0.5
                continue

            add_each = removed / 2.0
            add_vec = np.zeros(n_local, dtype=float)
            add_vec[sink_idx] += add_each * sink_weights

            add_neighbors = np.zeros(n_local, dtype=float)
            for si, wsi in zip(sink_idx, sink_weights):
                if si - 1 >= 0:
                    add_neighbors[si - 1] += (add_each * wsi) * 0.5
                if si + 1 < n_local:
                    add_neighbors[si + 1] += (add_each * wsi) * 0.5

            a_candidate += add_vec + add_neighbors
            a_candidate = np.maximum(a_candidate, 0.0)
            a_candidate /= max(np.sum(a_candidate), 1e-12)

            F_new = true_obj(a_candidate)
            if F_new < F_old:
                return a_candidate, True
            delta_total *= 0.5

        return a, False

    # ----------------------------
    # Seed generators (depend on n via interval sampling)
    # ----------------------------
    def generate_seeds(n: int, rng: np.random.Generator) -> List[np.ndarray]:
        """Create diverse symmetric seeds normalized to sum 1 and apply a micro peak-shaving to the best seed."""
        interval_start = -1.0 / 4.0
        interval_end = 1.0 / 4.0
        x = np.linspace(interval_start, interval_end, n)

        def normalize_nonneg(v: np.ndarray) -> np.ndarray:
            v = np.maximum(v, 0.0)
            s = np.sum(v)
            if s <= 0 or not np.isfinite(s):
                return np.ones_like(v) / v.size
            return v / s

        def arch_shape(xarr: np.ndarray, scale: float = 1.0) -> np.ndarray:
            # a(x; s) = max(0, 1 + 4|s·x| − 16 (s·x)^2), symmetrized
            x_scaled = xarr * scale
            shape = 1.0 + 4.0 * np.abs(x_scaled) - 16.0 * x_scaled ** 2
            shape = (shape + shape[::-1]) / 2.0
            return normalize_nonneg(shape)

        def raised_cosine(xarr: np.ndarray, width: float = 1.0) -> np.ndarray:
            denom = max(interval_end * width, 1e-6)
            c = np.cos(np.pi * (xarr / denom))
            c = np.maximum(c, 0.0)
            c = (c + c[::-1]) / 2.0
            return normalize_nonneg(c)

        def flat_top_raised_cosine(n_: int, plateau_frac: float = 0.25) -> np.ndarray:
            # Symmetric flat top with raised cosine taper to zero at edges
            t = np.linspace(-1.0, 1.0, n_)
            pf = min(max(plateau_frac, 0.0), 0.9)
            plateau_mask = np.abs(t) <= pf
            taper = np.zeros_like(t)
            # For |t| in (pf, 1]: use raised cosine falling from 1 at pf to 0 at 1
            mask_taper = ~plateau_mask
            u = (np.abs(t[mask_taper]) - pf) / max(1.0 - pf, 1e-6)
            taper[mask_taper] = 0.5 * (1 + np.cos(np.pi * u))
            base = np.where(plateau_mask, 1.0, taper)
            base = (base + base[::-1]) / 2.0
            return normalize_nonneg(base)

        def gaussian(xarr: np.ndarray, sigma: float = 0.06) -> np.ndarray:
            g = np.exp(-(xarr ** 2) / (2.0 * sigma ** 2))
            g = (g + g[::-1]) / 2.0
            return normalize_nonneg(g)

        def triangle(n_: int) -> np.ndarray:
            t = np.arange(n_, dtype=float)
            center = (n_ - 1) / 2.0
            tri = 1.0 - np.abs(t - center) / max(center, 1e-9)
            tri = np.maximum(tri, 0.0)
            tri = (tri + tri[::-1]) / 2.0
            return normalize_nonneg(tri)

        def beta_sym(n_: int, alpha: float) -> np.ndarray:
            # Symmetric beta density around mid-point: f(u) ∝ u^(α-1) (1-u)^(α-1), u in (0,1)
            # Use midpoint sampling to avoid endpoints 0 and 1 for α < 1.
            u = (np.arange(n_, dtype=float) + 0.5) / float(n_)
            a_ = max(alpha, 1e-6)
            v = (u ** (a_ - 1.0)) * ((1.0 - u) ** (a_ - 1.0))
            v = (v + v[::-1]) / 2.0
            return normalize_nonneg(v)

        # Width sweep over arch shapes (seed selection), plus a richer set of smooth seeds
        seeds = []
        # Refined arch-width sweep S per description
        for scale in [0.70, 0.75, 0.80, 0.90, 0.95, 1.00, 1.05, 1.15, 1.30]:
            seeds.append(arch_shape(x, scale))
        seeds.append(np.ones(n, dtype=float) / n)        # uniform
        seeds.append(raised_cosine(x, width=1.0))        # raised cosine
        seeds.append(raised_cosine(x, width=0.8))        # slightly narrower
        seeds.append(gaussian(x, sigma=0.045))           # narrow Gaussian
        seeds.append(gaussian(x, sigma=0.085))           # wide Gaussian
        seeds.append(triangle(n))                        # triangular
        # Flat-top raised cosines with varying plateau widths
        seeds.append(flat_top_raised_cosine(n, plateau_frac=0.20))
        seeds.append(flat_top_raised_cosine(n, plateau_frac=0.35))
        # Symmetric Beta distributions (α=β)
        for alpha in [0.7, 1.2, 2.0, 3.5, 5.0]:
            seeds.append(beta_sym(n, alpha))

        # Score seeds and pick best
        seed_scores = [float(2.0 * n * np.max(np.convolve(s, s)) / (np.sum(s) ** 2)) for s in seeds]
        best_idx = int(np.argmin(seed_scores))
        best_seed = seeds[best_idx].copy()

        # Accepted-only micro peak-shaving on the chosen seed:
        # Immediate four-direction shaving with up to three accepted shaves (slightly increased).
        def true_objective(a: np.ndarray) -> float:
            return float(2.0 * n * np.max(np.convolve(a, a)) / (np.sum(a) ** 2))

        F_old = true_objective(best_seed)
        b_best = np.convolve(best_seed, best_seed)
        k_star = int(np.argmax(b_best))
        i_min = max(0, k_star - (n - 1))
        i_max = min(n - 1, k_star)
        if i_max >= i_min:
            idx = np.arange(i_min, i_max + 1)
            products = best_seed[i_min:i_max + 1] * best_seed[k_star - idx]
            if products.size > 0:
                i_star = i_min + int(np.argmax(products))
                j_star = k_star - i_star
                delta = 3.5e-3
                attempts = 0
                accepted_shaves = 0
                while attempts < 12 and delta >= 1e-6 and accepted_shaves < 3:
                    proposals = []
                    # Cross moves (flatten active lag)
                    if i_star - 1 >= 0 and j_star + 1 < n:
                        proposals.append((i_star, i_star - 1, j_star, j_star + 1))
                    if i_star + 1 < n and j_star - 1 >= 0:
                        proposals.append((i_star, i_star + 1, j_star, j_star - 1))
                    # Co-moves (bleed mass off active lag)
                    if i_star - 1 >= 0 and j_star - 1 >= 0:
                        proposals.append((i_star, i_star - 1, j_star, j_star - 1))
                    if i_star + 1 < n and j_star + 1 < n:
                        proposals.append((i_star, i_star + 1, j_star, j_star + 1))
                    improved = False
                    for (i_from, i_to, j_from, j_to) in proposals:
                        d = min(delta, best_seed[i_from], best_seed[j_from])
                        if d > 0:
                            candidate = best_seed.copy()
                            candidate[i_from] -= d
                            candidate[j_from] -= d
                            candidate[i_to] += d
                            candidate[j_to] += d
                            candidate = normalize_nonneg(candidate)
                            F_new = true_objective(candidate)
                            if F_new < F_old:
                                best_seed = candidate
                                F_old = F_new
                                accepted_shaves += 1
                                improved = True
                                # Recompute i_star/j_star based on updated seed and worst lag
                                b_best = np.convolve(best_seed, best_seed)
                                k_star = int(np.argmax(b_best))
                                i_min = max(0, k_star - (n - 1))
                                i_max = min(n - 1, k_star)
                                if i_max >= i_min:
                                    idx = np.arange(i_min, i_max + 1)
                                    products = best_seed[i_min:i_max + 1] * best_seed[k_star - idx]
                                    if products.size > 0:
                                        i_star = i_min + int(np.argmax(products))
                                        j_star = k_star - i_star
                                break
                    if not improved:
                        delta *= 0.5
                        attempts += 1

                # Optional tiny symmetry averaging (acceptance-tested)
                a_sym = 0.5 * (best_seed + best_seed[::-1])
                a_sym = normalize_nonneg(a_sym)
                F_sym = true_objective(a_sym)
                if F_sym < F_old:
                    best_seed = a_sym
                    F_old = F_sym

        # Add small symmetric perturbations around the shaved best seed
        for _ in range(4):
            noise = rng.normal(0.0, 0.02, size=n)
            noise = 0.5 * (noise + noise[::-1])  # symmetric perturbation
            pert = best_seed + noise
            pert = normalize_nonneg(pert)
            seeds.append(pert)

        # Place the shaved best seed first
        seeds.insert(0, best_seed)
        return seeds

    # ----------------------------
    # Optimizer at a given resolution n
    # ----------------------------
    def optimize_at_resolution(
        n: int,
        init_seeds: List[np.ndarray],
        time_budget_sec: float,
        rng: np.random.Generator,
        carry_best_a: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Run the optimizer at resolution n using given seeds, within time_budget_sec,
        and return the best a found (sum=1, a>=0).
        """
        t_local_start = time.time()

        def time_left_local() -> float:
            used = time.time() - t_local_start
            # Cap by local budget and also by global remaining time
            return min(time_budget_sec - used, remaining_time())

        # Iteration and optimizer hyperparameters
        adam_lr_init = 0.10
        adam_beta1 = 0.9
        adam_beta2 = 0.999
        adam_eps = 1e-8
        grad_clip_norm = 6.0
        use_amsgrad = True

        # Surrogate and regularization schedules (annealed)
        plateau_eps_initial = 6e-3
        plateau_eps_final = 5e-4
        tau_frac_initial = 0.030
        tau_frac_final = 8e-5
        tail_alpha_initial = 0.12
        tail_alpha_final = 0.02
        lambda_tv_initial = 2e-3
        lambda_tv_final = 0.0
        lambda_went_initial = 5e-4   # peak-participation weighted entropy
        lambda_went_final = 0.0
        symmetry_mix_initial = 0.10
        symmetry_mix_final = 0.02

        # Top-K controls
        L_b = 2 * n - 1
        topk_frac_early = 0.12   # refined per description (~12% early)
        topk_frac_late = 0.02    # ~2% late
        neighbor_radius_initial = 1
        neighbor_radius_max = 6  # tightened cap

        # Polishing and refinements
        hard_polish_every = 50
        hard_polish_lr = 0.08
        top_r_for_hard = 3

        mass_transport_every = 70
        mass_transport_attempts = 6
        mass_transport_initial_r = 0.0035
        mass_transport_decay = 0.5

        mass_transport_multi_every = 45
        mass_transport_multi_groups = 3
        mass_transport_multi_r = 0.0025

        # New: global-sink mass transport (uses top-level helper)
        mass_transport_global_every = 90
        mass_transport_global_total_delta = 0.0045
        mass_transport_global_Q = 6
        mass_transport_global_sinks = 8
        mass_transport_global_backtracks = 4

        cutting_plane_every = 80
        cutting_plane_M = 28
        cutting_plane_lr_initial = 0.12  # step in a-space with projection
        cutting_plane_lr_decay = 0.5
        cutting_plane_max_steps = 3

        # Pressure microsteps (Gear G2)
        microstep_every = 30
        micro_eta_initial = 0.06  # as a fraction relative to Adam's effective scale

        max_iterations_per_seed = 1000

        # Local helpers that depend on n
        def corr_w_a(w: np.ndarray, a: np.ndarray) -> np.ndarray:
            """
            Compute correlation g[p] = sum_m w[m+p] * a[m], p = 0..n-1.
            Achieve via conv_full_fft(w, reverse(a)) and extract segment at indices n-1 : n-1 + n.
            """
            rev_a = a[::-1]
            c = conv_full_fft(w, rev_a)  # length (len(w) + n - 1)
            start = n - 1
            end = start + n
            return c[start:end]

        def true_objective(a: np.ndarray) -> Tuple[float, np.ndarray]:
            """Compute the true objective F(a) and return F plus autoconvolution vector b."""
            sum_a = np.sum(a)
            if sum_a <= 0:
                return np.inf, None
            b = np.convolve(a, a)  # exact convolution for decision/monitoring
            F = float(2.0 * len(a) * np.max(b) / (sum_a ** 2))
            return F, b

        def build_active_mask(
            b: np.ndarray,
            topk: int,
            plateau_eps: float,
            neighbor_radius: int,
        ) -> np.ndarray:
            """
            Build active mask S: union of Top-K(b) and plateau band {k : b[k] >= (1 - eps) bmax}
            expanded by neighbor radius.
            """
            L = len(b)
            bmax = np.max(b)
            topk = max(1, min(L, int(topk)))
            topk_idx = np.argpartition(-b, topk - 1)[:topk]
            plateau_mask = b >= (1.0 - plateau_eps) * bmax
            active_mask = plateau_mask.copy()
            active_mask[topk_idx] = True
            if neighbor_radius > 0:
                idxs = np.where(active_mask)[0]
                for idx in idxs:
                    lo = max(0, idx - neighbor_radius)
                    hi = min(L, idx + neighbor_radius + 1)
                    active_mask[lo:hi] = True
            return active_mask

        def plateau_weights(
            b: np.ndarray,
            topk: int,
            plateau_eps: float,
            tau_frac: float,
            tail_alpha: float,
            neighbor_radius: int,
        ) -> np.ndarray:
            """
            Build plateau-aware Top-K + neighbor band weights over lags, blended with a tail.
            Returns a weight vector w over len(b) lags that sums to 1.
            """
            L = len(b)
            bmax = np.max(b)
            bmin = np.min(b)

            active_mask = build_active_mask(b, topk=topk, plateau_eps=plateau_eps, neighbor_radius=neighbor_radius)
            active_idx = np.where(active_mask)[0]

            # Softmax over active using logits proportional to (b[k] - bmax)/tau
            tau = max(tau_frac * max(bmax, 1e-12), 1e-12)
            logits = (b[active_idx] - bmax) / tau
            exp_logits = np.exp(logits - np.max(logits))
            w_active_sum = np.sum(exp_logits)
            w_active = np.zeros(L, dtype=float)
            if w_active_sum > 0:
                w_active[active_idx] = exp_logits / w_active_sum

            # Tail blend: mix of uniform and inverse-pressure weights
            uniform = np.ones(L, dtype=float)
            inv_press = 1.0 / (b - bmin + 1e-12)
            tail = 0.5 * uniform + 0.5 * inv_press
            tail /= max(1e-12, np.sum(tail))

            w = (1.0 - tail_alpha) * w_active + tail_alpha * tail
            w_sum = np.sum(w)
            if w_sum > 0:
                w /= w_sum
            else:
                w = np.ones(L, dtype=float) / L
            return w

        def symmetry_mix_accept(a: np.ndarray, s: np.ndarray, weight: float) -> Tuple[np.ndarray, np.ndarray]:
            """
            Attempt to mix a with its reversal to encourage symmetry, accept only if it reduces F.
            Returns potentially updated (a, s).
            """
            if weight <= 0:
                return a, s
            F_before, _ = true_objective(a)
            a_mix = (1.0 - weight) * a + weight * a[::-1]
            a_mix = np.maximum(a_mix, 0.0)
            a_mix /= max(np.sum(a_mix), 1e-12)
            F_after, _ = true_objective(a_mix)
            if F_after < F_before:
                return a_mix, np.log(np.maximum(a_mix, 1e-12))
            return a, s

        def participation_weighted_entropy_grad(a: np.ndarray, b: np.ndarray, eps: float = 1e-12) -> np.ndarray:
            """
            Peak-participation weighted entropy gradient:
            - Determine R in {1,2,3} based on top-2 relative gap.
            - For each selected lag k among top-R of b, accumulate q[i] += a[i] * a[k - i] for valid i.
            - Normalize q to q̃ and return grad = -q̃ / (a + eps).
            """
            L = len(b)
            b_sorted = np.sort(b)[::-1]
            bmax = b_sorted[0]
            bsecond = b_sorted[1] if L > 1 else bmax
            rel_gap = (bmax - bsecond) / (bmax + 1e-12)
            # Adapt R: larger when spectrum is flatter
            if rel_gap > 0.05:
                R = 1
            elif rel_gap > 0.015:
                R = 2
            else:
                R = 3

            top_idx = np.argpartition(-b, R - 1)[:R]
            q = np.zeros_like(a)
            for k in top_idx:
                i_min = max(0, k - (n - 1))
                i_max = min(n - 1, k)
                if i_max >= i_min:
                    idx = np.arange(i_min, i_max + 1)
                    j = k - idx
                    q[i_min:i_max + 1] += a[i_min:i_max + 1] * a[j]
            q_sum = np.sum(q)
            if q_sum <= 0:
                return np.zeros_like(a)
            q_tilde = q / q_sum
            return -q_tilde / (a + eps)

        def hard_max_polish(s: np.ndarray, a: np.ndarray, r: int, lr: float) -> Tuple[np.ndarray, np.ndarray, bool]:
            """
            Perform a short step targeting the average of the top-r b-lags (hard max surrogate).
            Accept only if it reduces the true objective F(a).
            """
            F_before, b_before = true_objective(a)
            if not np.isfinite(F_before):
                return s, a, False
            L = len(b_before)
            top_idx = np.argpartition(-b_before, r - 1)[:r]
            w = np.zeros(L, dtype=float)
            w[top_idx] = 1.0 / r

            g_a = 4.0 * n * corr_w_a(w, a)
            ga_dot_a = float(np.dot(g_a, a))
            g_s = a * (g_a - ga_dot_a)
            s_new = s - lr * g_s
            a_new = softmax(s_new)

            F_after, _ = true_objective(a_new)
            if F_after < F_before:
                return s_new, a_new, True
            else:
                return s, a, False

        def mass_transport_peak_shaving(a: np.ndarray, b: np.ndarray, r_init: float, attempts: int) -> Tuple[np.ndarray, bool]:
            """
            Identify worst lag k*, find i* maximizing a[i]*a[k*-i], and attempt small symmetric mass reassignments.
            Accept the first move that strictly reduces the true objective; otherwise decay radius and retry.
            """
            L = len(b)
            k_star = int(np.argmax(b))
            i_min = max(0, k_star - (n - 1))
            i_max = min(n - 1, k_star)
            if i_max < i_min:
                return a, False
            idx = np.arange(i_min, i_max + 1)
            products = a[i_min:i_max + 1] * a[k_star - idx]
            if products.size == 0:
                return a, False
            idx_rel = int(np.argmax(products))
            i_star = i_min + idx_rel
            j_star = k_star - i_star

            r = r_init
            F_old, _ = true_objective(a)
            for _ in range(attempts):
                proposals = []
                if i_star - 1 >= 0 and j_star + 1 < n:
                    proposals.append((i_star, i_star - 1, j_star, j_star + 1))
                if i_star + 1 < n and j_star - 1 >= 0:
                    proposals.append((i_star, i_star + 1, j_star, j_star - 1))
                if i_star - 1 >= 0 and j_star - 1 >= 0:
                    proposals.append((i_star, i_star - 1, j_star, j_star - 1))
                if i_star + 1 < n and j_star + 1 < n:
                    proposals.append((i_star, i_star + 1, j_star, j_star + 1))
                for (i_from, i_to, j_from, j_to) in proposals:
                    delta = min(r, a[i_from], a[j_from])
                    if delta > 0:
                        a_candidate = a.copy()
                        a_candidate[i_from] -= delta
                        a_candidate[j_from] -= delta
                        a_candidate[i_to] += delta
                        a_candidate[j_to] += delta
                        a_candidate = np.maximum(a_candidate, 0.0)
                        a_candidate /= max(np.sum(a_candidate), 1e-12)
                        F_new, _ = true_objective(a_candidate)
                        if F_new < F_old:
                            return a_candidate, True
                r *= mass_transport_decay
            return a, False

        def mass_transport_multi(a: np.ndarray, b: np.ndarray, groups: int, delta_each: float) -> Tuple[np.ndarray, bool]:
            """
            Multi-pair mass transport: for worst lag k*, take top `groups` contributing pairs (i, j=k*-i),
            and move small mass from each towards neighbor indices to reduce the worst lag contribution.
            Aggregate moves and accept only if F decreases.
            """
            k_star = int(np.argmax(b))
            i_min = max(0, k_star - (n - 1))
            i_max = min(n - 1, k_star)
            if i_max < i_min:
                return a, False

            idx = np.arange(i_min, i_max + 1)
            contrib = a[i_min:i_max + 1] * a[k_star - idx]
            if contrib.size == 0:
                return a, False

            # Select top contributing indices
            top_indices_rel = np.argpartition(-contrib, min(groups - 1, contrib.size - 1))[:groups]
            top_indices_rel = top_indices_rel[np.argsort(-contrib[top_indices_rel])]  # sort by contribution
            a_candidate = a.copy()
            any_move = False

            for rel in top_indices_rel:
                i_star = i_min + int(rel)
                j_star = k_star - i_star
                d = min(delta_each, a_candidate[i_star], a_candidate[j_star])
                if d <= 0:
                    continue
                moved = False
                # Prefer moving away from center: try (i-1, j+1) and (i+1, j-1)
                if i_star - 1 >= 0 and j_star + 1 < n:
                    a_candidate[i_star] -= d
                    a_candidate[j_star] -= d
                    a_candidate[i_star - 1] += d
                    a_candidate[j_star + 1] += d
                    moved = True
                elif i_star + 1 < n and j_star - 1 >= 0:
                    a_candidate[i_star] -= d
                    a_candidate[j_star] -= d
                    a_candidate[i_star + 1] += d
                    a_candidate[j_star - 1] += d
                    moved = True
                if moved:
                    any_move = True

            if not any_move:
                return a, False

            a_candidate = np.maximum(a_candidate, 0.0)
            a_candidate /= max(np.sum(a_candidate), 1e-12)
            F_old, _ = true_objective(a)
            F_new, _ = true_objective(a_candidate)
            if F_new < F_old:
                return a_candidate, True
            return a, False

        def cutting_plane_refine(a: np.ndarray, M: int, lr0: float, max_steps: int) -> Tuple[np.ndarray, bool]:
            """
            Projected gradient descent on the average of the top-M peaks S_M(a) = (1/M) Σ_{k∈TopM} b[k].
            Operates in a-space with Euclidean projection to the simplex.
            Accepts only if F strictly decreases; backtracks step size otherwise.
            """
            F_before, b_before = true_objective(a)
            if not np.isfinite(F_before):
                return a, False
            L = len(b_before)
            M = max(1, min(L, int(M)))
            top_idx = np.argpartition(-b_before, M - 1)[:M]
            w = np.zeros(L, dtype=float)
            w[top_idx] = 1.0 / M

            lr = lr0
            a_candidate = a.copy()
            accepted = False
            for _ in range(max_steps):
                # Gradient of Σ w[k] b[k] wrt a is 2 * corr(w, a)
                g_surr = 2.0 * corr_w_a(w, a_candidate)
                a_next = a_candidate - lr * g_surr
                a_next = project_to_simplex(a_next, s=1.0)
                F_next, _ = true_objective(a_next)
                if F_next < F_before:
                    a_candidate = a_next
                    F_before = F_next
                    accepted = True
                    lr *= 0.8
                else:
                    lr *= cutting_plane_lr_decay
            return a_candidate, accepted

        def pressure_microstep(s: np.ndarray, a: np.ndarray, b_fft: np.ndarray, topk: int, plateau_eps: float, neighbor_radius: int, eta: float) -> Tuple[np.ndarray, np.ndarray, bool]:
            """
            Gear G2: Cheap mirror update using uniform weights over the active plateau union S.
            Accept only if the true objective decreases; otherwise backtrack eta up to a few times.
            """
            active_mask = build_active_mask(b_fft, topk=topk, plateau_eps=plateau_eps, neighbor_radius=neighbor_radius)
            active_idx = np.where(active_mask)[0]
            if active_idx.size == 0:
                return s, a, False
            w_plateau = np.zeros_like(b_fft)
            w_plateau[active_idx] = 1.0 / active_idx.size

            g_a = 4.0 * n * corr_w_a(w_plateau, a)
            ga_dot_a = float(np.dot(g_a, a))
            g_s = a * (g_a - ga_dot_a)

            F_before, _ = true_objective(a)
            eta_try = eta
            for _ in range(3):
                s_new = s - eta_try * g_s
                a_new = softmax(s_new)
                F_after, _ = true_objective(a_new)
                if F_after < F_before:
                    return s_new, a_new, True
                eta_try *= 0.5
            return s, a, False

        # Evaluate initial seeds and prepare ordering to focus time on promising starts
        seeds = init_seeds.copy()
        if carry_best_a is not None and len(carry_best_a) == n:
            seeds.insert(0, carry_best_a.copy())
        seed_scores = []
        for s0 in seeds:
            F0 = float(2.0 * n * np.max(np.convolve(s0, s0)) / (np.sum(s0) ** 2))
            seed_scores.append(F0)
        order = np.argsort(seed_scores)  # best-first

        global_best_a = seeds[order[0]].copy()
        global_best_F = seed_scores[order[0]]

        # Optimizer over seeds
        for seed_idx in order:
            if time_left_local() <= 1.0:
                break

            a = seeds[seed_idx].copy()
            s = np.log(np.maximum(a, 1e-12))

            # Adam/AMSGrad states
            m1 = np.zeros(n, dtype=float)
            m2 = np.zeros(n, dtype=float)
            vhat_max = np.zeros(n, dtype=float)  # AMSGrad max of second moment
            t_adam = 0

            # Dynamic parameters per seed
            lr = adam_lr_init
            plateau_eps = plateau_eps_initial
            tau_frac = tau_frac_initial
            tail_alpha = tail_alpha_initial
            lambda_tv = lambda_tv_initial
            lambda_went = lambda_went_initial
            sym_mix = symmetry_mix_initial
            micro_eta = micro_eta_initial

            # Top-K and neighbor radius controllers
            topk = int(max(3, topk_frac_early * L_b))
            neighbor_radius = neighbor_radius_initial

            # Monitoring
            hard_polish_ctr = 0
            mass_transport_ctr = 0
            mass_transport_multi_ctr = 0
            mass_transport_global_ctr = 0
            cutting_plane_ctr = 0
            microstep_ctr = 0
            last_argmax = None
            flap_count = 0

            # First evaluation
            F_cur, _ = true_objective(a)
            if F_cur < global_best_F:
                global_best_F = F_cur
                global_best_a = a.copy()

            for it in range(max_iterations_per_seed):
                if time_left_local() <= 0.5:
                    break

                # Anneal schedules
                progress = it / max(1, max_iterations_per_seed)
                tau_frac = tau_frac_initial * (1 - progress) + tau_frac_final * progress
                plateau_eps = plateau_eps_initial * (1 - progress) + plateau_eps_final * progress
                tail_alpha = tail_alpha_initial * (1 - progress) + tail_alpha_final * progress
                lambda_tv = lambda_tv_initial * (1 - progress) + lambda_tv_final * progress
                lambda_went = lambda_went_initial * (1 - progress) + lambda_went_final * progress
                sym_mix = symmetry_mix_initial * (1 - progress) + symmetry_mix_final * progress

                # Dynamic Top-K target from progress
                topk_target = int(max(3, (topk_frac_early * (1 - progress) + topk_frac_late * progress) * L_b))

                # FFT autoconvolution for the surrogate building
                b_fft = conv_full_fft(a, a)
                bmax = float(np.max(b_fft))

                # Active-set calibration and LR stabilization based on plateau width and top-2 gap
                b_sorted = np.sort(b_fft)[::-1]
                gap = float(b_sorted[0] - (b_sorted[1] if len(b_sorted) > 1 else b_sorted[0]))
                rel_gap = gap / (b_sorted[0] + 1e-12)
                plateau_width = np.sum(b_fft >= (1.0 - plateau_eps) * bmax) / len(b_fft)

                # Adjust Top-K and neighbor radius adaptively
                if plateau_width > 0.05 or rel_gap < 0.005:
                    topk = min(int(1.25 * topk), int(1.25 * topk_target))
                    neighbor_radius = min(neighbor_radius + 1, neighbor_radius_max)
                else:
                    topk = max(int(0.9 * topk), topk_target)
                    neighbor_radius = max(1, neighbor_radius - (1 if neighbor_radius > 1 else 0))

                # Track argmax flapping to stabilize LR
                argmax_idx = int(np.argmax(b_fft))
                if last_argmax is not None and argmax_idx != last_argmax:
                    flap_count += 1
                last_argmax = argmax_idx
                if flap_count > 3 and rel_gap < 1e-3:
                    lr *= 0.98
                    flap_count = 0

                # Plateau-union Top-K weights with tail blend
                w = plateau_weights(
                    b_fft, topk=topk, plateau_eps=plateau_eps,
                    tau_frac=tau_frac, tail_alpha=tail_alpha,
                    neighbor_radius=neighbor_radius
                )

                # Gradient of surrogate objective 2n * <w, b(a)> wrt a is 4n * corr(w, a)
                g_a = 4.0 * n * corr_w_a(w, a)

                # Regularization: H1 smoothness
                if lambda_tv > 0.0:
                    g_tv = np.zeros_like(a)
                    g_tv[1:-1] = 2.0 * (2.0 * a[1:-1] - a[:-2] - a[2:])
                    g_tv[0] = 2.0 * (a[0] - a[1])
                    g_tv[-1] = 2.0 * (a[-1] - a[-2])
                    g_a += lambda_tv * g_tv

                # Peak-participation weighted entropy gradient
                if lambda_went > 0.0:
                    g_went = participation_weighted_entropy_grad(a, b_fft)
                    g_a += lambda_went * g_went

                # Convert to logits gradient through softmax Jacobian
                ga_dot_a = float(np.dot(g_a, a))
                g_s = a * (g_a - ga_dot_a)

                # Gradient clipping
                g_norm = float(np.linalg.norm(g_s))
                if g_norm > grad_clip_norm:
                    g_s *= (grad_clip_norm / (g_norm + 1e-12))

                # Adam/AMSGrad update
                t_adam += 1
                m1 = np.copy(m1) * adam_beta1 + (1.0 - adam_beta1) * g_s
                m2 = np.copy(m2) * adam_beta2 + (1.0 - adam_beta2) * (g_s ** 2)
                m1_hat = m1 / (1.0 - adam_beta1 ** t_adam)
                m2_hat = m2 / (1.0 - adam_beta2 ** t_adam)
                if use_amsgrad:
                    vhat_max = np.maximum(vhat_max, m2_hat)
                    denom = np.sqrt(vhat_max) + adam_eps
                else:
                    denom = np.sqrt(m2_hat) + adam_eps
                s = s - lr * (m1_hat / denom)

                # Get new a via softmax, then attempt symmetry mix (acceptance-tested)
                a = softmax(s)
                a, s = symmetry_mix_accept(a, s, sym_mix)

                # Interleaved non-smooth refinements with acceptance testing
                hard_polish_ctr += 1
                if hard_polish_ctr >= hard_polish_every:
                    hard_polish_ctr = 0
                    s_pol, a_pol, acc = hard_max_polish(s, a, r=top_r_for_hard, lr=hard_polish_lr)
                    if acc:
                        s, a = s_pol, a_pol
                    else:
                        lr *= 0.985

                mass_transport_ctr += 1
                if mass_transport_ctr >= mass_transport_every:
                    mass_transport_ctr = 0
                    F_now, b_now = true_objective(a)
                    a_mt, acc_mt = mass_transport_peak_shaving(a, b_now, r_init=mass_transport_initial_r, attempts=mass_transport_attempts)
                    if acc_mt:
                        a = a_mt
                        s = np.log(np.maximum(a, 1e-12))

                mass_transport_multi_ctr += 1
                if mass_transport_multi_ctr >= mass_transport_multi_every:
                    mass_transport_multi_ctr = 0
                    F_now, b_now = true_objective(a)
                    a_mm, acc_mm = mass_transport_multi(a, b_now, groups=mass_transport_multi_groups, delta_each=mass_transport_multi_r)
                    if acc_mm:
                        a = a_mm
                        s = np.log(np.maximum(a, 1e-12))

                mass_transport_global_ctr += 1
                if mass_transport_global_ctr >= mass_transport_global_every:
                    mass_transport_global_ctr = 0
                    F_now, b_now = true_objective(a)
                    a_mg, acc_mg = mass_transport_global_sink(
                        a, b_now,
                        total_delta=mass_transport_global_total_delta,
                        Q=mass_transport_global_Q,
                        sink_count=mass_transport_global_sinks,
                        backtracks=mass_transport_global_backtracks
                    )
                    if acc_mg:
                        a = a_mg
                        s = np.log(np.maximum(a, 1e-12))

                cutting_plane_ctr += 1
                if cutting_plane_ctr >= cutting_plane_every:
                    cutting_plane_ctr = 0
                    a_cp, acc_cp = cutting_plane_refine(a, M=cutting_plane_M, lr0=cutting_plane_lr_initial, max_steps=cutting_plane_max_steps)
                    if acc_cp:
                        a = a_cp
                        s = np.log(np.maximum(a, 1e-12))

                # Gear G2: pressure microstep (acceptance-tested)
                microstep_ctr += 1
                if microstep_ctr >= microstep_every:
                    microstep_ctr = 0
                    s_m, a_m, acc_m = pressure_microstep(s, a, b_fft, topk=topk, plateau_eps=plateau_eps, neighbor_radius=neighbor_radius, eta=micro_eta)
                    if acc_m:
                        s, a = s_m, a_m
                        # gradually shrink micro eta to keep stability
                        micro_eta *= 0.98

                # Monitoring and global best tracking
                F_it, _ = true_objective(a)
                if F_it < global_best_F:
                    global_best_F = F_it
                    global_best_a = a.copy()

                # Mild LR damping when top-2 gap is tiny
                if rel_gap < 1e-6:
                    lr *= 0.98
                elif rel_gap < 1e-4:
                    lr *= 0.99

                # Safety break when very low local time remains
                if it % 100 == 99 and time_left_local() <= 1.0:
                    break

        final_a = global_best_a
        final_a = np.maximum(final_a, 0.0)
        sum_a = np.sum(final_a)
        if sum_a <= 0:
            final_a = np.ones(n, dtype=float) / n
        else:
            final_a /= sum_a
        return final_a

    # ----------------------------
    # Orchestration: coarse-to-fine (512 -> 600) with final polish
    # ----------------------------
    rng = np.random.default_rng(123456)

    # Allocate time split: coarse about 65%, fine about remaining, with buffer
    total_remaining = remaining_time()
    coarse_budget = max(1.0, min(total_remaining * 0.65, 680.0))  # cap coarse phase
    fine_budget = max(1.0, remaining_time() - coarse_budget - 2.0)  # leave small buffer

    # Coarse phase at n=512
    n_coarse = 512
    seeds_512 = generate_seeds(n_coarse, rng)
    best_512 = optimize_at_resolution(n_coarse, seeds_512, time_budget_sec=coarse_budget, rng=rng)

    # Upsample to n=600 and refine
    n_final = 600
    best_600_init = upsample_linear(best_512, n_final)
    seeds_600 = generate_seeds(n_final, rng)
    # Prepend the upsampled coarse best
    seeds_600.insert(0, best_600_init)
    best_600 = optimize_at_resolution(n_final, seeds_600, time_budget_sec=fine_budget, rng=rng, carry_best_a=best_600_init)

    # Final accept-tested polish with strict time safety (use remaining buffer)
    def final_polish(a_in: np.ndarray, max_steps: int = 8, time_cap_sec: float = 1.5) -> np.ndarray:
        t_fp = time.time()

        def time_left_fp() -> float:
            return min(time_cap_sec - (time.time() - t_fp), remaining_time())

        a = a_in.copy()

        def true_objective(a: np.ndarray) -> float:
            return float(2.0 * n_final * np.max(np.convolve(a, a)) / (np.sum(a) ** 2))

        F_best = true_objective(a)
        s = np.log(np.maximum(a, 1e-12))

        for step in range(max_steps):
            if time_left_fp() <= 0.2:
                break
            F_cur = true_objective(a)

            # Try a small hard-max polish
            b = np.convolve(a, a)
            top_idx = np.argpartition(-b, 2)[:2]
            w = np.zeros_like(b)
            w[top_idx] = 0.5
            # Gradient: 4n corr(w, a) converted through softmax Jacobian
            # Use small step in logits space
            def corr_w_a_local(wvec: np.ndarray, avec: np.ndarray) -> np.ndarray:
                rev_a = avec[::-1]
                c = conv_full_fft(wvec, rev_a)
                start = n_final - 1
                end = start + n_final
                return c[start:end]

            g_a = 4.0 * n_final * corr_w_a_local(w, a)
            ga_dot_a = float(np.dot(g_a, a))
            g_s = a * (g_a - ga_dot_a)
            s_try = s - 0.06 * g_s
            a_try = softmax(s_try)
            F_try = true_objective(a_try)

            improved = False
            if F_try < F_cur:
                a = a_try
                s = s_try
                F_cur = F_try
                improved = True

            # Try global-sink mass transport tiny move (uses top-level helper)
            b = np.convolve(a, a)
            a_gs = a.copy()
            a_gs2, acc = (lambda a0, b0: mass_transport_global_sink(
                a0, b0,
                total_delta=0.0035,
                Q=5,
                sink_count=8,
                backtracks=3
            ))(a_gs, b)
            if acc:
                F_gs = true_objective(a_gs2)
                if F_gs < F_cur:
                    a = a_gs2
                    s = np.log(np.maximum(a, 1e-12))
                    F_cur = F_gs
                    improved = True

            # Tiny symmetry mix test
            a_sym = 0.5 * (a + a[::-1])
            a_sym = np.maximum(a_sym, 0.0)
            a_sym /= max(np.sum(a_sym), 1e-12)
            F_sym = true_objective(a_sym)
            if F_sym < F_cur:
                a = a_sym
                s = np.log(np.maximum(a, 1e-12))
                F_cur = F_sym
                improved = True

            if F_cur < F_best:
                F_best = F_cur

            # Stop if no improvement in this step
            if not improved:
                break

        return a

    final_a = np.maximum(best_600, 0.0)
    sum_a = np.sum(final_a)
    if sum_a <= 0:
        final_a = np.ones(n_final, dtype=float) / n_final
    else:
        final_a /= sum_a

    # Run final polish using any remaining buffer time
    if remaining_time() > 0.6:
        final_a = final_polish(final_a, max_steps=8, time_cap_sec=min(1.5, remaining_time() - 0.1))

    # Normalize and return
    final_a = np.maximum(final_a, 0.0)
    s_final = np.sum(final_a)
    if s_final <= 0:
        final_a = np.ones(n_final, dtype=float) / n_final
    else:
        final_a /= s_final

    return final_a
# EVOLVE_END


def evaluate_sequence(sequence: list[float]) -> float:
    """
    Evaluates a sequence of coefficients with enhanced security checks.
    Returns np.inf if the input is invalid.
    """
    # --- Security Checks ---

    # Verify that the input is a list
    if not isinstance(sequence, list):
        return np.inf

    # Reject empty lists
    if not sequence:
        return np.inf

    # Check each element in the list for validity
    for x in sequence:
        # Reject boolean types (as they are a subclass of int) and
        # any other non-integer/non-float types (like strings or complex numbers).
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return np.inf

        # Reject Not-a-Number (NaN) and infinity values.
        if np.isnan(x) or np.isinf(x):
            return np.inf

    # Convert all elements to float for consistency
    sequence = [float(x) for x in sequence]

    # Protect against negative numbers
    sequence = [max(0, x) for x in sequence]

    # Protect against numbers that are too large
    sequence = [min(1000.0, x) for x in sequence]

    n = len(sequence)
    b_sequence = np.convolve(sequence, sequence)
    max_b = max(b_sequence)
    sum_a = np.sum(sequence)

    # Protect against the case where the sum is too close to zero
    if sum_a < 0.01:
        return np.inf

    return float(2 * n * max_b / (sum_a**2))


def run_search_for_best_sequence():
    """
    Entry point function that runs the search and returns the best sequence found
    as a Python list. This will be terminated after 1000 seconds by the judge if
    not returned, so the search implements a strict internal budget discipline.
    """
    return list(search_for_best_sequence())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```
