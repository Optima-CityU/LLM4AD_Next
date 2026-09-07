#!/usr/bin/env python3
"""Optimized step-function search for minimizing 2 n max(conv(a,a)) / (sum(a)^2).

Implements a plateau-union active-set smooth minimax with inverse-pressure tail
and FFT acceleration, interleaved with hard-max polishing, cutting-plane refine,
and transport updates.

We maintain feasibility by working on the simplex via softmax logits (a >= 0, sum(a) = 1).
The search continuously keeps the current best feasible sequence and returns it in time.
"""

import json
import math
import os
import time
from typing import Tuple, List

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """
    Run an adaptive search on the simplex a >= 0, sum(a) = 1 to minimize:
        F(a) = 2 * n * max(conv(a, a)) / (sum(a)^2)
    With simplex enforcement, sum(a) = 1, so F(a) = 2 * n * max(conv(a, a)).

    AW-PLUMAR-X initializer mutation (dual-gated):
      1) Coarse width sweep arch selection: s in {0.7, 0.8, ..., 1.3} evaluated via evaluate_sequence().
      2) Single accepted micro peak-shaving (evaluate-sequence gated) on the selected arch.
      3) Fine width sweep around s* (±0.08 in steps of 0.02), evaluated via evaluate_sequence(),
         choose the best among fine sweep and shaved candidate as the primary seed.

    The optimizer uses a plateau-union minimax backbone on the simplex with FFT acceleration,
    interleaving polishing, cutting-plane refine, and transport updates, all strictly
    acceptance-tested by the true objective.
    """
    # Dimension and time budget
    n = 600

    # Budget: up to 980s safeguard; allow override via env SEARCH_SECONDS
    max_seconds_env = float(os.environ.get("SEARCH_SECONDS", "980"))
    max_seconds = float(min(980.0, max_seconds_env))
    t_start = time.time()
    deadline = t_start + max_seconds

    # FFT helpers -------------------------------------------------------------
    def next_pow2(x: int) -> int:
        return 1 << (x - 1).bit_length()

    def conv_full(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """
        Full linear convolution using real FFT.
        Length = len(a) + len(b) - 1.
        """
        la, lb = len(a), len(b)
        L = la + lb - 1
        nfft = next_pow2(L)
        fa = np.fft.rfft(a, nfft)
        fb = np.fft.rfft(b, nfft)
        c = np.fft.irfft(fa * fb, nfft)[:L]
        return c

    def autocorr_full(a: np.ndarray) -> np.ndarray:
        """
        Autoconvolution b = conv(a, a), length 2n-1.
        """
        return conv_full(a, a)

    def corr_w_a(w: np.ndarray, a: np.ndarray) -> np.ndarray:
        """
        Compute c[j] = sum_m w[m + j] * a[m] for j = 0..n-1,
        i.e., correlation between w (length Lw) and a (length n).
        Implement via convolution conv(rev(w), a) and index mapping.
        """
        Lw = len(w)
        wr = w[::-1]
        d = conv_full(wr, a)  # length = Lw + n - 1
        idx = (Lw - 1) - np.arange(len(a))
        return d[idx]

    # Objective and simplex projection ---------------------------------------
    def true_objective(a: np.ndarray) -> float:
        # On simplex sum(a)=1, F(a) = 2 * n * max(b)
        b = autocorr_full(a)
        return float(2.0 * n * float(np.max(b)))

    def ensure_simplex_from_logits(s: np.ndarray) -> np.ndarray:
        # Stable softmax projection
        s_max = np.max(s)
        exps = np.exp(s - s_max)
        a = exps / np.sum(exps)
        return a

    # Arch builder ------------------------------------------------------------
    def build_arch(n: int, s: float) -> np.ndarray:
        """
        Build symmetric arch a_s(x) sampled on x in [-1/4, 1/4] with parameter s:
            a_s(x) = max(0, 1 + 4|x/s| - 16 (x/s)^2)
        Returns normalized nonnegative array of length n (sum = 1) if nonzero, else uniform.
        """
        x = np.linspace(-0.25, 0.25, n)
        x_s = x / float(s)
        a = 1.0 + 4.0 * np.abs(x_s) - 16.0 * (x_s ** 2)
        # Enforce symmetry by averaging with reverse
        a = (a + a[::-1]) / 2.0
        # Clip to nonnegative and L1-normalize
        a = np.maximum(a, 0.0)
        ssum = float(a.sum())
        if ssum <= 0:
            return np.ones(n) / n
        return a / ssum

    # Width sweep using evaluate_sequence ------------------------------------
    def select_best_arch_by_eval(n: int, s_values: List[float]) -> Tuple[np.ndarray, float, float]:
        """
        Grid-search over width parameter s to select the best arch seed evaluated
        via evaluate_sequence() (strictly following the requested mutation).

        For each s:
          - Build a_s(x) on [-1/4, 1/4] with symmetry, nonnegativity, normalization.
          - Evaluate using evaluate_sequence() on the Python list version.
        Returns (best_arch, best_s, best_score).
        """
        best_a = None
        best_score = float('inf')
        best_s = None
        for s in s_values:
            a = build_arch(n, s)
            score = evaluate_sequence(list(map(float, a.tolist())))
            if score < best_score:
                best_score = score
                best_a = a
                best_s = float(s)
        if best_a is None:
            best_a = build_arch(n, 1.0)
            best_s = 1.0
            best_score = evaluate_sequence(list(map(float, best_a.tolist())))
        return best_a, best_s, best_score

    # Single accepted peak shaving using evaluate_sequence --------------------
    def micro_peak_shaving(a_init: np.ndarray, resymmetrize: bool = True) -> np.ndarray:
        """
        One-step accepted-only peak-shaving: find worst lag k* (argmax of conv),
        then identify top contributing index pair (i*, j*) maximizing a[i]*a[k*-i].
        Propose up to four neighbor redistribution moves:
            (i*±1, j*±1) and (i*±1, j*∓1),
        moving r = min(0.002, 0.5*min(a[i*], a[j*])) from (i*, j*) to the two neighbors.
        Accept the first candidate that strictly reduces evaluate_sequence().
        If none improve, return the original sequence.

        Always reclip to >=0, renormalize to sum 1; optionally re-symmetrize by averaging
        with its reverse prior to evaluation.
        """
        a = np.clip(a_init.astype(float), 0.0, None)
        ssum = float(a.sum())
        if ssum <= 0:
            a = np.ones_like(a) / len(a)
        else:
            a /= ssum

        # Current score via evaluation function
        base_score = evaluate_sequence(list(map(float, a.tolist())))

        # Compute worst lag and top contributing index-pair
        b = autocorr_full(a)
        k_star = int(np.argmax(b))
        n_local = len(a)
        i_min = max(0, k_star - (n_local - 1))
        i_max = min(n_local - 1, k_star)
        i_idx = np.arange(i_min, i_max + 1, dtype=int)
        j_idx = k_star - i_idx
        contrib = a[i_idx] * a[j_idx]
        max_pos = int(np.argmax(contrib))
        i_star = int(i_idx[max_pos])
        j_star = int(j_idx[max_pos])

        # Proposed neighbor pairs
        candidates = [(+1, +1), (-1, -1), (+1, -1), (-1, +1)]

        # Amount to move
        r = min(0.002, 0.5 * float(min(a[i_star], a[j_star])))

        if r <= 0:
            return a

        for di, dj in candidates:
            ii = i_star + di
            jj = j_star + dj
            if ii < 0 or ii >= n_local or jj < 0 or jj >= n_local:
                continue

            a_cand = a.copy()
            a_cand[i_star] = max(0.0, a_cand[i_star] - r)
            a_cand[j_star] = max(0.0, a_cand[j_star] - r)
            a_cand[ii] += r
            a_cand[jj] += r

            # Re-normalize and optional re-symmetrize
            ssum = float(a_cand.sum())
            if ssum <= 0:
                continue
            a_cand /= ssum
            if resymmetrize:
                a_cand = 0.5 * (a_cand + a_cand[::-1])
                a_cand = np.maximum(a_cand, 0.0)
                a_cand /= max(1e-18, float(a_cand.sum()))

            # Evaluate using the evaluation function
            cand_score = evaluate_sequence(list(map(float, a_cand.tolist())))
            if cand_score + 1e-14 < base_score:
                return a_cand

        # No improvement found, keep original
        return a

    # Arch builder and width sweep (true objective for internal optimizer) ----
    def width_sweep_best_arch(n: int, s_values: List[float]) -> np.ndarray:
        """
        Grid-search over width parameter s to select the best arch seed by true objective.
        (Retained for internal fallback usage; initializer uses evaluate_sequence as above.)
        """
        best_a = None
        best_F = float('inf')
        for s in s_values:
            a = build_arch(n, s)
            F = true_objective(a)
            if F < best_F:
                best_F = F
                best_a = a
        if best_a is None:
            best_a = build_arch(n, 1.0)
        return best_a

    # Accepted-only peak-shaving transport (iterative variant retained) -------
    def accepted_peak_shaving(a_init: np.ndarray,
                              max_iters: int = 20,
                              init_delta: float = 2e-3,
                              min_delta: float = 1e-6) -> Tuple[np.ndarray, float]:
        """
        Iteratively reduce the current worst autoconvolution peak by small mass redistributions
        away from the top contributing pair, accepting only improvements according to the true objective.
        """
        a = np.clip(a_init.copy(), 0.0, None)
        ssum = a.sum()
        if ssum <= 0:
            a = np.ones_like(a) / len(a)
        else:
            a /= ssum

        F_curr = true_objective(a)
        delta = float(init_delta)
        n_local = len(a)
        noimprove_rounds = 0

        for _ in range(max_iters):
            b = autocorr_full(a)
            k_star = int(np.argmax(b))
            i_min = max(0, k_star - (n_local - 1))
            i_max = min(n_local - 1, k_star)
            i_idx = np.arange(i_min, i_max + 1, dtype=int)
            j_idx = k_star - i_idx
            contrib = a[i_idx] * a[j_idx]
            max_pos = int(np.argmax(contrib))
            i_star = int(i_idx[max_pos])
            j_star = int(j_idx[max_pos])

            improved = False
            for di, dj in [(+1, +1), (-1, -1), (+1, -1), (-1, +1)]:
                ii = i_star + di
                jj = j_star + dj
                if ii < 0 or ii >= n_local or jj < 0 or jj >= n_local:
                    continue
                r = min(a[i_star], a[j_star], delta / 2.0)
                if r <= 0:
                    continue
                a_cand = a.copy()
                a_cand[i_star] -= r
                a_cand[j_star] -= r
                a_cand[ii] += r
                a_cand[jj] += r
                if a_cand[i_star] < 0 or a_cand[j_star] < 0:
                    continue
                ssum = float(a_cand.sum())
                if ssum <= 0:
                    continue
                a_cand /= ssum

                F_new = true_objective(a_cand)
                if F_new + 1e-14 < F_curr:
                    a = a_cand
                    F_curr = F_new
                    improved = True
                    delta = min(init_delta, delta * 1.2)
                    break

            if not improved:
                delta *= 0.5
                noimprove_rounds += 1
                if delta < min_delta or noimprove_rounds >= 4:
                    break
            else:
                noimprove_rounds = 0

        a = np.clip(a, 0.0, None)
        ssum = a.sum()
        if ssum > 0:
            a /= ssum
        else:
            a = np.ones_like(a) / len(a)
        return a, F_curr

    # Seeds -------------------------------------------------------------------
    def seeds_collection(n: int) -> List[np.ndarray]:
        seeds: List[np.ndarray] = []

        # 1) Coarse width sweep initializer: pick best arch over s in {0.7, 0.8, ..., 1.3}
        s_values_coarse = [round(0.7 + 0.1 * k, 2) for k in range(7)]  # 0.7..1.3
        a_arch_best, s_star, score_star = select_best_arch_by_eval(n, s_values_coarse)

        # 2) Single accepted peak-shaving transport on the best arch seed (evaluate_sequence gating)
        a_arch_shaved = micro_peak_shaving(a_arch_best, resymmetrize=True)
        score_shaved = evaluate_sequence(list(map(float, a_arch_shaved.tolist())))

        # 3) Fine width sweep around s* with ±0.08 in steps of 0.02, evaluated via evaluate_sequence
        s_fine_vals = []
        for ds in np.arange(-0.08, 0.081, 0.02):
            sv = float(s_star + ds)
            if 0.6 <= sv <= 1.4:
                s_fine_vals.append(round(sv, 3))
        # Ensure uniqueness and reasonable size
        s_fine_vals = sorted(set(s_fine_vals))
        a_arch_fine, s_star_fine, score_fine = select_best_arch_by_eval(n, s_fine_vals if s_fine_vals else [s_star])

        # Choose the best seed among shaved and fine sweep
        if score_shaved + 1e-14 < score_fine:
            a_arch_refined = a_arch_shaved
        else:
            # Optionally shave once on fine candidate too and accept if improves
            a_arch_fine_shaved = micro_peak_shaving(a_arch_fine, resymmetrize=True)
            score_fine_shaved = evaluate_sequence(list(map(float, a_arch_fine_shaved.tolist())))
            if score_fine_shaved + 1e-14 < min(score_fine, score_shaved):
                a_arch_refined = a_arch_fine_shaved
            else:
                a_arch_refined = a_arch_fine

        seeds.append(a_arch_refined)

        # Additional diverse seeds
        x = np.linspace(-1.0, 1.0, n)

        # Uniform
        a_u = np.ones(n) / n
        seeds.append(a_u)

        # Raised cosine (Hann)
        a_rc = 0.5 * (1.0 + np.cos(np.pi * x))
        a_rc = a_rc / a_rc.sum()
        seeds.append(a_rc)

        # Blackman window (standard coefficients)
        n_idx = np.arange(n)
        a0, a1, a2 = 0.42, 0.5, 0.08
        a_bl = a0 - a1 * np.cos(2 * np.pi * n_idx / (n - 1)) + a2 * np.cos(4 * np.pi * n_idx / (n - 1))
        a_bl = np.maximum(a_bl, 0.0)
        a_bl /= a_bl.sum()
        seeds.append(a_bl)

        # Tukey window with alpha 0.5
        def tukey_window(N: int, alpha: float = 0.5) -> np.ndarray:
            if alpha <= 0:
                return np.ones(N)
            if alpha >= 1:
                return np.hanning(N)
            w = np.ones(N)
            # Use smooth cosine taper length determined by alpha
            p = int(round(alpha * (N - 1) / 2.0))
            if p <= 0:
                return w
            n_arr = np.arange(p + 1)
            w[: p + 1] = 0.5 * (1 + np.cos(np.pi * (2 * n_arr / (alpha * (N - 1)) - 1)))
            w[-(p + 1):] = w[: p + 1][::-1]
            return w

        a_tk = tukey_window(n, 0.5)
        a_tk = np.maximum(a_tk, 0.0)
        a_tk /= a_tk.sum()
        seeds.append(a_tk)

        # Gaussian (two widths)
        center = (n - 1) / 2.0
        sigma1 = 0.35 * n
        g1 = np.exp(-((np.arange(n) - center) ** 2) / (2 * (sigma1 ** 2)))
        g1 = g1 / g1.sum()
        seeds.append(g1)

        sigma2 = 0.22 * n
        g2 = np.exp(-((np.arange(n) - center) ** 2) / (2 * (sigma2 ** 2)))
        g2 = g2 / g2.sum()
        seeds.append(g2)

        # Triangular (tent)
        tri = 1.0 - np.abs(np.linspace(-1, 1, n))
        tri = tri / tri.sum()
        seeds.append(tri)

        # Welch
        k = np.arange(n)
        wel = 1.0 - ((k - center) / center) ** 2
        wel = np.maximum(wel, 0.0)
        wel /= wel.sum()
        seeds.append(wel)

        # Symmetric ramp (mild)
        ramp = np.linspace(0.1, 1.0, n)
        ramp = (ramp + ramp[::-1]) / 2.0
        ramp = ramp / ramp.sum()
        seeds.append(ramp)

        # Kaiser windows (various betas)
        for beta in [4.0, 6.0, 8.0, 10.0, 12.0, 14.0]:
            kai = np.kaiser(n, beta)
            kai = np.maximum(kai, 0.0)
            ssum = kai.sum()
            if ssum <= 0:
                continue
            kai /= ssum
            seeds.append(kai)

        # Central rectangular windows (two widths)
        rect1 = np.zeros(n)
        half_w1 = max(1, int(0.14 * n))
        lo1 = int(center) - half_w1 // 2
        hi1 = lo1 + half_w1
        rect1[lo1:hi1] = 1.0
        rect1 /= rect1.sum()
        seeds.append(rect1)

        rect2 = np.zeros(n)
        half_w2 = max(1, int(0.22 * n))
        lo2 = int(center) - half_w2 // 2
        hi2 = lo2 + half_w2
        rect2[lo2:hi2] = 1.0
        rect2 /= rect2.sum()
        seeds.append(rect2)

        return seeds

    # Helper: expand index set by neighbor radius -----------------------------
    def expand_with_neighbors(idx: np.ndarray, L: int, radius: int) -> np.ndarray:
        if radius <= 0 or len(idx) == 0:
            return np.unique(idx)
        out = []
        for k in idx:
            lo = max(0, k - radius)
            hi = min(L - 1, k + radius)
            out.extend(range(lo, hi + 1))
        return np.unique(np.asarray(out, dtype=int))

    # Surrogate construction ---------------------------------------------------
    def surrogate_weights(b: np.ndarray,
                          frac_tau: float,
                          eps_plateau: float,
                          base_top_k: int,
                          alpha_base: float,
                          alpha_bounds: Tuple[float, float],
                          neighbor_radius_base: int,
                          neighbor_radius_max: int) -> np.ndarray:
        """
        Build weights over lags (length 2n-1) focusing on active top-K and plateau-union,
        with inverse-pressure tail mixing. Returns a probability vector over lags.

        The plateau epsilon is coupled to tau via the caller: eps_plateau ≈ O(frac_tau).
        """
        L = len(b)
        bmax = float(b.max())
        if not np.isfinite(bmax) or bmax <= 0:
            return np.full(L, 1.0 / L)

        # Plateau band P = {k : b[k] >= (1 - eps) * bmax}
        plateau_mask = (b >= (1.0 - eps_plateau) * bmax)
        idx_plateau = np.nonzero(plateau_mask)[0]
        plateau_size = int(idx_plateau.size)

        # Adaptive Top-K increases with plateau width
        K = min(L, base_top_k + max(0, plateau_size // 3))
        idx_top = np.argpartition(b, -K)[-K:]

        # Adaptive neighbor expansion radius
        extra = min(neighbor_radius_max,
                    neighbor_radius_base + max(0, plateau_size // max(1, L // 40)))
        S = np.unique(np.concatenate([idx_top, idx_plateau]))
        S = expand_with_neighbors(S, L, extra)

        # Temperature and active-set softmax
        tau = max(1e-12, frac_tau * max(1e-12, bmax))
        logits = (b[S] - bmax) / tau
        logits -= logits.max()
        exp_logits = np.exp(logits)
        wS = exp_logits / max(1e-16, np.sum(exp_logits))

        # Inverse-pressure tail blended with uniform
        lam = 1e-6 * bmax + 1e-12
        invp = 1.0 / (np.maximum(b - b.min(), 0.0) + lam)  # stabilize across plateau
        invp = invp / invp.sum()
        unif = np.full(L, 1.0 / L)
        tail = 0.5 * unif + 0.5 * invp

        # Dynamic alpha: increase with plateau width and when top-2 gap is tiny
        top2_idx = np.argpartition(b, -2)[-2:]
        top2_vals = np.sort(b[top2_idx])
        if top2_vals.size == 1:
            gap_rel = 1.0
        else:
            b1 = float(top2_vals[-1])
            b2 = float(top2_vals[-2])
            gap_rel = (b1 - b2) / max(1e-12, b1)

        plateau_ratio = plateau_size / max(1, L)
        c_p = 1.5  # plateau strength factor
        c_gap = 1.2  # sensitivity to small gaps
        gap_boost = 1.0 + c_gap * max(0.0, (0.03 - gap_rel) / 0.03)
        alpha_eff = alpha_base * (1.0 + c_p * plateau_ratio) * gap_boost
        alpha_eff = float(np.clip(alpha_eff, alpha_bounds[0], alpha_bounds[1]))

        # Combine active-set softmax with tail
        w = np.zeros(L)
        w[S] += (1.0 - alpha_eff) * wS
        w += alpha_eff * tail

        # Normalize
        s = w.sum()
        if not np.isfinite(s) or s <= 0:
            w = np.full(L, 1.0 / L)
        else:
            w /= s
        return w

    # Peak-aware entropy weights ---------------------------------------------
    def peak_participation_weights(a: np.ndarray, b: np.ndarray, top_r: int = 2) -> np.ndarray:
        """
        Compute per-index participation weights based on contributions to the top-r lags.
        For lag k: b[k] = sum_i a[i] a[k-i]. Contribution at index i is a[i] * a[k-i].
        We sum contributions across top_r worst lags and normalize to sum 1.
        """
        n_local = len(a)
        contrib = np.zeros(n_local)
        idx_sorted = np.argpartition(b, -top_r)[-top_r:]
        for k in idx_sorted:
            i_min = max(0, k - (n_local - 1))
            i_max = min(n_local - 1, k)
            i_idx = np.arange(i_min, i_max + 1)
            j_idx = k - i_idx
            contrib[i_idx] += a[i_idx] * a[j_idx]
        contrib = np.maximum(contrib, 0.0)
        s = contrib.sum()
        if s <= 0:
            w = np.ones_like(contrib) / len(contrib)
        else:
            w = contrib / s
        return w

    # Smooth surrogate objective and gradient --------------------------------
    def lse_grad(a: np.ndarray, stage_params: dict) -> Tuple[float, np.ndarray]:
        """
        Compute surrogate loss (monitor only) and gradient wrt a.
        Returns (loss_value, grad_a).
        """
        b = autocorr_full(a)

        # Couple eps_plateau to frac_tau (ε ≈ O(τ/bmax) ≈ O(frac_tau))
        eps_eff = max(5e-4, min(0.08, 1.2 * stage_params['frac_tau']))

        # Build plateau-union surrogate weights with inverse-pressure tail
        w = surrogate_weights(
            b=b,
            frac_tau=stage_params['frac_tau'],
            eps_plateau=eps_eff,
            base_top_k=stage_params['top_k'],
            alpha_base=stage_params['alpha_tail_base'],
            alpha_bounds=(stage_params['alpha_tail_min'], stage_params['alpha_tail_max']),
            neighbor_radius_base=stage_params['neighbor_radius_base'],
            neighbor_radius_max=stage_params['neighbor_radius_max'],
        )

        # Surrogate loss ~ 2n * <w, b>
        loss = float(2.0 * n * float(np.dot(w, b)))

        # Gradient wrt a: d/d a = 2 * corr(w, a), then scale by 2n
        grad_corr = 2.0 * corr_w_a(w, a)
        grad_a = 2.0 * n * grad_corr

        # Regularization: annealed TV and peak-aware weighted entropy
        beta_tv = stage_params['beta_tv']
        beta_ent = stage_params['beta_ent']
        if beta_tv > 0.0:
            # TV^2: sum (a[i] - a[i-1])^2, gradient is discrete Laplacian-like
            da = np.zeros_like(a)
            da[1:-1] += 2.0 * (2.0 * a[1:-1] - a[:-2] - a[2:])
            da[0] += 2.0 * (a[0] - a[1])
            da[-1] += 2.0 * (a[-1] - a[-2])
            grad_a += beta_tv * da
            loss += float(beta_tv * np.sum((np.diff(a)) ** 2))

        if beta_ent > 0.0:
            # Peak-aware entropy: choose R based on top-gap
            btmp = b
            top2_idx = np.argpartition(btmp, -2)[-2:]
            top2_vals = np.sort(btmp[top2_idx])
            if top2_vals.size == 1:
                gap_rel = 1.0
            else:
                b1 = float(top2_vals[-1])
                b2 = float(top2_vals[-2])
                gap_rel = (b1 - b2) / max(1e-12, b1)
            if gap_rel < 0.015:
                R = 3
            elif gap_rel < 0.05:
                R = 2
            else:
                R = 1
            w_part = peak_participation_weights(a, btmp, top_r=R)
            eps = 1e-12
            # Gradient of -sum w[i] log(a[i]) = -w[i]/a[i]
            grad_a += -beta_ent * (w_part / (a + eps))
            loss += float(-beta_ent * np.sum(w_part * np.log(a + eps)))

        return loss, grad_a

    # Hard-max polishing ------------------------------------------------------
    def hardmax_polish(a: np.ndarray, steps: int = 3, top_r: int = 2, init_lr: float = 0.4) -> Tuple[np.ndarray, float]:
        """
        Short projected subgradient steps minimizing the average of the top-r maxima.
        Accept only if the true objective improves.
        """
        best_a = a.copy()
        best_F = true_objective(best_a)
        lr = init_lr
        for _ in range(steps):
            b = autocorr_full(best_a)
            idx_sorted = np.argpartition(b, -top_r)[-top_r:]
            w = np.zeros_like(b)
            w[idx_sorted] = 1.0 / top_r
            grad_a = 2.0 * corr_w_a(w, best_a) * 2.0 * n
            s = np.log(best_a + 1e-12)
            a_now = ensure_simplex_from_logits(s)
            g = grad_a
            ag = float(np.dot(a_now, g))
            grad_s = a_now * (g - ag)
            improved = False
            inner_lr = lr
            for _bt in range(12):
                s_new = s - inner_lr * grad_s
                a_new = ensure_simplex_from_logits(s_new)
                F_new = true_objective(a_new)
                if F_new + 1e-14 < best_F:
                    best_a = a_new
                    best_F = F_new
                    improved = True
                    break
                inner_lr *= 0.5
            if not improved:
                lr *= 0.5
        return best_a, best_F

    # Cutting-plane refinement -------------------------------------------------
    def cutplane_refine(a: np.ndarray, steps: int = 3, top_m: int = 24, init_lr: float = 0.25) -> Tuple[np.ndarray, float]:
        """
        Minimize the average of the top-m lags via short projected gradient steps on logits.
        Accept only if the true objective improves each step (backtracking included).
        """
        best_a = a.copy()
        best_F = true_objective(best_a)
        lr = init_lr
        for _ in range(steps):
            b = autocorr_full(best_a)
            idx_sorted = np.argpartition(b, -top_m)[-top_m:]
            w = np.zeros_like(b)
            w[idx_sorted] = 1.0 / top_m
            grad_a = 2.0 * corr_w_a(w, best_a) * 2.0 * n

            s = np.log(best_a + 1e-12)
            a_now = ensure_simplex_from_logits(s)
            ag = float(np.dot(a_now, grad_a))
            grad_s = a_now * (grad_a - ag)

            improved = False
            inner_lr = lr
            for _bt in range(12):
                s_new = s - inner_lr * grad_s
                a_new = ensure_simplex_from_logits(s_new)
                F_new = true_objective(a_new)
                if F_new + 1e-14 < best_F:
                    best_a = a_new
                    best_F = F_new
                    improved = True
                    break
                inner_lr *= 0.6
            if not improved:
                lr *= 0.7
        return best_a, best_F

    # Mass transport (pair-shaving) ------------------------------------------
    def transport_shave(a: np.ndarray,
                        b: np.ndarray,
                        total_delta: float,
                        top_pairs_q: int = 16,
                        top_r_lags: int = 2) -> Tuple[np.ndarray, bool]:
        """
        Heuristic: identify indices contributing most to top lags and move
        a small total mass to low-influence indices. Returns (new_a, accepted?).
        """
        idx_sorted = np.argpartition(b, -top_r_lags)[-top_r_lags:]
        n_local = len(a)
        contrib = np.zeros(n_local)
        for k in idx_sorted:
            i_min = max(0, k - (n_local - 1))
            i_max = min(n_local - 1, k)
            i_idx = np.arange(i_min, i_max + 1)
            j_idx = k - i_idx
            contrib[i_idx] += a[i_idx] * a[j_idx]

        if top_pairs_q <= 0:
            return a.copy(), False

        src_idx = np.argpartition(contrib, -top_pairs_q)[-top_pairs_q:]
        # targets: low contrib and relatively low a
        target_metric = contrib + 0.2 * a
        dst_idx = np.argpartition(target_metric, top_pairs_q)[:top_pairs_q]

        a_new = a.copy()
        removable = np.minimum(a_new[src_idx], total_delta / top_pairs_q)
        total_removed = 0.0
        for i, rm in zip(src_idx, removable):
            prev = a_new[i]
            a_new[i] = max(0.0, a_new[i] - rm)
            total_removed += (prev - a_new[i])

        if total_removed <= 0:
            return a.copy(), False

        cap = np.maximum(1.0 - a_new[dst_idx], 0.0)
        weights = cap + 1e-12
        weights /= weights.sum()
        add = total_removed * weights
        a_new[dst_idx] += add

        s = a_new.sum()
        if s > 0:
            a_new /= s
        else:
            return a.copy(), False

        return a_new, True

    # Optimizer: Adam on logits ----------------------------------------------
    class Adam:
        def __init__(self, lr=0.14, b1=0.9, b2=0.999, eps=1e-8):
            self.lr = lr
            self.b1 = b1
            self.b2 = b2
            self.eps = eps
            self.m = None
            self.v = None
            self.t = 0

        def step(self, params: np.ndarray, grad: np.ndarray) -> np.ndarray:
            if self.m is None:
                self.m = np.zeros_like(grad)
                self.v = np.zeros_like(grad)
                self.t = 0
            self.t += 1
            self.m = self.b1 * self.m + (1 - self.b1) * grad
            self.v = self.b2 * self.v + (1 - self.b2) * (grad * grad)
            m_hat = self.m / (1 - self.b1 ** self.t)
            v_hat = self.v / (1 - self.b2 ** self.t)
            update = self.lr * m_hat / (np.sqrt(v_hat) + self.eps)
            return params - update

        def set_lr(self, lr: float):
            self.lr = lr

    # Core single-seed optimization ------------------------------------------
    def optimize_seed(a0: np.ndarray, seed_time_budget: float) -> Tuple[np.ndarray, float]:
        # Initialize logits from seed
        s = np.log(a0 + 1e-12)
        a = ensure_simplex_from_logits(s)
        best_a = a.copy()
        best_F = true_objective(a)

        # Optimizer and schedules
        adam = Adam(lr=0.14, b1=0.9, b2=0.999, eps=1e-8)
        max_iters = 20000
        check_every = 20
        polish_every = 200
        cutplane_every = 150
        transport_every = 250
        symmetry_mix_every = 60

        # Stage parameters (annealed)
        stage_params = {
            # Plateau-union and temperature schedule
            'frac_tau': 0.03,        # scaled by bmax, starts smoother then anneals
            'top_k': 72,
            'alpha_tail_base': 0.10,
            'alpha_tail_min': 0.02,
            'alpha_tail_max': 0.40,
            'neighbor_radius_base': 1,
            'neighbor_radius_max': 6,
            # Regularizers (annealed)
            'beta_tv': 2.0e-3,
            'beta_ent': 5.0e-4,
        }

        # LR stabilization: track argmax identity changes
        last_argmax_idx = None
        argmax_change_count = 0
        changes_window = 0

        no_improve_count = 0
        last_eval_F = best_F
        t0 = time.time()

        for it in range(1, max_iters + 1):
            now = time.time()
            if now >= deadline or (now - t0) >= seed_time_budget:
                break

            # Anneal temperature slowly and derive plateau epsilon from it
            stage_params['frac_tau'] = max(8e-5, stage_params['frac_tau'] * 0.9995)

            # Mild decay of regularizers
            stage_params['beta_tv'] *= 0.9995
            stage_params['beta_ent'] *= 0.9995

            # Surrogate gradient
            _loss, grad_a = lse_grad(a, stage_params)

            # Convert to logits gradient: grad_s = a * (grad_a - <grad_a, a>)
            ag = float(np.dot(a, grad_a))
            grad_s = a * (grad_a - ag)

            # Gradient clipping for stability
            gnorm = float(np.linalg.norm(grad_s))
            if not np.isfinite(gnorm) or gnorm > 200.0:
                scale = 200.0 / (gnorm + 1e-12)
                grad_s *= scale

            # Adam step
            s = adam.step(s, grad_s)
            a = ensure_simplex_from_logits(s)

            # Symmetry mix with acceptance
            if it % symmetry_mix_every == 0:
                gamma = max(0.0, 0.12 * (1.0 - it / 2500.0))
                if gamma > 0:
                    a_prop = (1.0 - gamma) * a + gamma * a[::-1]
                    a_prop /= a_prop.sum()
                    F_prop = true_objective(a_prop)
                    if F_prop + 1e-14 < last_eval_F:
                        a = a_prop
                        s = np.log(a + 1e-12)
                        last_eval_F = F_prop
                        if F_prop + 1e-12 < best_F:
                            best_F = F_prop
                            best_a = a.copy()
                            no_improve_count = 0

            # Periodic evaluation and bookkeeping
            if it % check_every == 0:
                b_now = autocorr_full(a)
                F = float(2.0 * n * float(np.max(b_now)))
                # Track argmax identity changes
                current_argmax = int(np.argmax(b_now))
                if last_argmax_idx is None:
                    last_argmax_idx = current_argmax
                else:
                    if current_argmax != last_argmax_idx:
                        argmax_change_count += 1
                        changes_window += 1
                    else:
                        changes_window += 1
                    last_argmax_idx = current_argmax

                # Stabilize lr if argmax identity flaps frequently
                if changes_window >= 10:
                    if argmax_change_count >= 3:
                        adam.set_lr(max(0.02, adam.lr * 0.85))
                    argmax_change_count = 0
                    changes_window = 0

                if F + 1e-12 < best_F:
                    best_F = F
                    best_a = a.copy()
                    no_improve_count = 0
                else:
                    no_improve_count += 1
                last_eval_F = F

            # Hard-max polishing occasionally
            if it % polish_every == 0:
                a_polish, F_polish = hardmax_polish(a, steps=2, top_r=2, init_lr=0.25)
                if F_polish + 1e-12 < last_eval_F:
                    a = a_polish
                    s = np.log(a + 1e-12)
                    last_eval_F = F_polish
                    if F_polish + 1e-12 < best_F:
                        best_F = F_polish
                        best_a = a.copy()
                        no_improve_count = 0

            # Cutting-plane (top-M average) refine
            if it % cutplane_every == 0:
                a_cp, F_cp = cutplane_refine(a, steps=2, top_m=28, init_lr=0.22)
                if F_cp + 1e-12 < last_eval_F:
                    a = a_cp
                    s = np.log(a + 1e-12)
                    last_eval_F = F_cp
                    if F_cp + 1e-12 < best_F:
                        best_F = F_cp
                        best_a = a.copy()
                        no_improve_count = 0

            # Mass transport if stagnating
            if it % transport_every == 0 and no_improve_count >= (transport_every // check_every):
                b = autocorr_full(a)
                a_try = a.copy()
                delta = 5e-3  # total mass to move
                accepted_any = False
                for _ in range(5):
                    a_new, ok = transport_shave(a_try, b, total_delta=delta, top_pairs_q=24, top_r_lags=2)
                    if not ok:
                        break
                    F_new = true_objective(a_new)
                    if F_new + 1e-12 < last_eval_F:
                        a = a_new
                        s = np.log(a + 1e-12)
                        last_eval_F = F_new
                        accepted_any = True
                        if F_new + 1e-12 < best_F:
                            best_F = F_new
                            best_a = a.copy()
                            no_improve_count = 0
                        break
                    delta *= 0.5
                if not accepted_any:
                    # Reduce lr to stabilize
                    adam.set_lr(max(0.02, adam.lr * 0.7))
                    no_improve_count = 0

            # Occasional lr decay if repeated stagnation
            if no_improve_count > 25:
                adam.set_lr(max(0.02, adam.lr * 0.9))
                no_improve_count = 0

        return best_a, best_F

    # Final polishing continuation --------------------------------------------
    def final_polish(a_init: np.ndarray, time_budget: float) -> Tuple[np.ndarray, float]:
        """
        Short continuation stage with harder focus and non-smooth refinements, acceptance-gated.
        """
        a_best = np.clip(a_init.copy(), 0.0, None)
        a_best /= max(1e-18, a_best.sum())
        F_best = true_objective(a_best)

        t0 = time.time()
        while time.time() - t0 < time_budget * 0.98 and time.time() < deadline:
            # Hard-max polish with stronger top_r
            a_cand, F_cand = hardmax_polish(a_best, steps=3, top_r=3, init_lr=0.22)
            if F_cand + 1e-12 < F_best:
                a_best, F_best = a_cand, F_cand

            # Cutting-plane with broader top-M
            a_cand, F_cand = cutplane_refine(a_best, steps=3, top_m=36, init_lr=0.20)
            if F_cand + 1e-12 < F_best:
                a_best, F_best = a_cand, F_cand

            # Accepted-only peak shaving (micro)
            a_cand, F_cand = accepted_peak_shaving(a_best, max_iters=16, init_delta=1.5e-3, min_delta=5e-7)
            if F_cand + 1e-12 < F_best:
                a_best, F_best = a_cand, F_cand

            # Symmetry mix acceptance
            a_sym = 0.5 * (a_best + a_best[::-1])
            a_sym /= a_sym.sum()
            F_sym = true_objective(a_sym)
            if F_sym + 1e-12 < F_best:
                a_best, F_best = a_sym, F_sym

            # Mild transport
            b = autocorr_full(a_best)
            a_tr, ok = transport_shave(a_best, b, total_delta=2.5e-3, top_pairs_q=20, top_r_lags=2)
            if ok:
                F_tr = true_objective(a_tr)
                if F_tr + 1e-12 < F_best:
                    a_best, F_best = a_tr, F_tr

            # Exit early if very good
            if F_best < 1.503:
                break

        return a_best, F_best

    # Multi-start orchestration ----------------------------------------------
    all_seeds = seeds_collection(n)

    # Slight perturbations on primary seed to escape symmetry traps
    if len(all_seeds) > 0:
        base = all_seeds[0]
        rng = np.random.default_rng(123)
        for _ in range(3):
            noise = rng.normal(0, 8e-4, size=n)
            pert = np.clip(base + noise, 0, None)
            ssum = pert.sum()
            if ssum <= 0:
                pert = np.ones(n) / n
            else:
                pert = pert / ssum
            # Tiny smoothing to avoid razor spikes from noise
            pert = (pert + 0.5 * np.roll(pert, 1) + 0.5 * np.roll(pert, -1)) / 2.0
            pert = np.clip(pert, 0.0, None)
            pert /= pert.sum()
            all_seeds.append(pert)

    # Time allocation per seed
    time_left = max(0.0, deadline - time.time())
    if len(all_seeds) == 0 or time_left <= 0.1:
        return np.ones(n) / n

    # Bias more time to first few seeds
    n_seeds = len(all_seeds)
    # emphasize first 4 seeds (arch-refined, uniform, raised cosine, blackman)
    emph = [3.0, 2.2, 1.8, 1.5]
    seed_weights = np.array(emph + [1.0] * max(0, n_seeds - len(emph)))
    seed_weights = seed_weights[:n_seeds]
    seed_weights = np.maximum(seed_weights, 0.1)
    seed_weights /= seed_weights.sum()

    # Use 92% of time for per-seed optimization, leave buffer for final polish
    global_budget = 0.92 * time_left
    per_seed_budgets = global_budget * seed_weights

    global_best_a = None
    global_best_F = float('inf')

    for i, seed in enumerate(all_seeds):
        if time.time() >= deadline:
            break
        a_best_seed, F_best_seed = optimize_seed(seed, seed_time_budget=float(per_seed_budgets[i]))
        if F_best_seed + 1e-12 < global_best_F:
            global_best_F = F_best_seed
            global_best_a = a_best_seed

    # Safeguard fallback
    if global_best_a is None:
        # Use width-sweep best arch as a safe fallback (true objective selection)
        a_fb = width_sweep_best_arch(n, [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3])
        global_best_a = a_fb
        global_best_F = true_objective(global_best_a)

    # Final polish continuation with remaining time
    time_left = max(0.0, deadline - time.time())
    if time_left > 0.2:
        a_pol, F_pol = final_polish(global_best_a, time_budget=0.95 * time_left)
        if F_pol + 1e-12 < global_best_F:
            global_best_a = a_pol
            global_best_F = F_pol

    # Ensure nonnegativity and normalization
    global_best_a = np.clip(global_best_a, 0.0, None)
    ssum = global_best_a.sum()
    if ssum <= 0:
        global_best_a = np.ones(n) / n
    else:
        global_best_a /= ssum

    return global_best_a
# EVOLVE_END


def evaluate_sequence(sequence: list[float]) -> float:
    """
    Evaluates a sequence of coefficients with enhanced security checks.
    Returns np.inf if the input is invalid.
    """
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
    Entry point: runs the search and returns the best sequence found as a list.
    """
    return list(search_for_best_sequence())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))