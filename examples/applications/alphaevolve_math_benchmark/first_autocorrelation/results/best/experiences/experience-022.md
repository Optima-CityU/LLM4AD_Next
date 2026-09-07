One reusable mechanism from the ATK‑PTCP‑PIP run that drove good performance on the hard max‑autoconvolution objective.

- ATK‑PTCP with Plateau‑Union and Inverse‑Pressure Tail (ATK‑PTCP‑PIP): Unite a temperature‑aware plateau band P = {k : b[k] ≥ (1 − ε)·bmax} (with ε = max(5e−4, c·sqrt(τ/τ0))) with Top‑K and neighbor lags, then blend an inverse‑pressure tail t = 0.5·uniform + 0.5·v where v[k] ∝ 1/(b[k] − bmin + λ), using a dynamic α that grows with plateau width; this stabilizes updates on near‑tied maxima and spreads minimax pressure away from shoulders. In this run it achieved target_ratio 0.989655 and upper_bound 1.521035 at n = 600 in 26.80 s, supporting plateau‑union + inverse‑pressure mixing for reducing the true max of conv(a, a) under a ≥ 0 and sum(a) = 1. Reuse this pattern when multiple lags are near‑tied: construct the plateau‑union active set, schedule ε by temperature, and apply the inverse‑pressure tail with dynamic α, while keeping non‑smooth refinements acceptance‑tested against the true objective to secure monotone drops.

```python
#!/usr/bin/env python3
"""Optimized step-function search for minimizing 2 n max(conv(a,a)) / (sum(a)^2).

Implements a plateau-union active-set smooth minimax with inverse-pressure tail
and FFT acceleration, interleaved with hard-max polishing and transport updates.

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

    Returns:
        numpy.ndarray: best found nonnegative sequence summing to 1 (length n=600).
    """
    # Dimension and time budget
    n = 600

    # Budget: up to 980s safeguard; allow override via env SEARCH_SECONDS
    max_seconds_env = float(os.environ.get("SEARCH_SECONDS", "30"))
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
        # c[j] = d[Lw - 1 - j]
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

    # Seeds -------------------------------------------------------------------
    def seeds_collection(n: int) -> List[np.ndarray]:
        x = np.linspace(-1.0, 1.0, n)
        seeds: List[np.ndarray] = []

        # Baseline quadratic-ish symmetric shape (a smooth arch over [-1/4, 1/4])
        x_bl = np.linspace(-0.25, 0.25, n)
        a_bl = 1.0 + 4.0 * np.abs(x_bl) - 16.0 * (x_bl**2)
        a_bl = (a_bl + a_bl[::-1]) / 2.0
        a_bl = np.maximum(a_bl, 0)
        if a_bl.sum() > 0:
            a_bl = a_bl / a_bl.sum()
            seeds.append(a_bl)

        # Uniform
        a_u = np.ones(n) / n
        seeds.append(a_u)

        # Raised cosine
        a_rc = 0.5 * (1.0 + np.cos(np.pi * x))
        a_rc = a_rc / a_rc.sum()
        seeds.append(a_rc)

        # Gaussian of moderate width
        center = (n - 1) / 2.0
        sigma = 0.35 * n
        g = np.exp(-((np.arange(n) - center) ** 2) / (2 * (sigma ** 2)))
        g = g / g.sum()
        seeds.append(g)

        # Triangular (tent)
        tri = 1.0 - np.abs(np.linspace(-1, 1, n))
        tri = tri / tri.sum()
        seeds.append(tri)

        # Symmetric ramp (mild)
        ramp = np.linspace(0.1, 1.0, n)
        ramp = (ramp + ramp[::-1]) / 2.0
        ramp = ramp / ramp.sum()
        seeds.append(ramp)

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
        """
        L = len(b)
        bmax = float(b.max())
        if not np.isfinite(bmax) or bmax <= 0:
            return np.full(L, 1.0 / L)

        # Top-K indices
        K = min(base_top_k, L)
        idx_top = np.argpartition(b, -K)[-K:]

        # Plateau band P = {k : b[k] >= (1 - eps) * bmax}
        plateau_mask = (b >= (1.0 - eps_plateau) * bmax)
        idx_plateau = np.nonzero(plateau_mask)[0]
        plateau_size = int(idx_plateau.size)

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
        # Estimate top-two peaks
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
        # When gap_rel < 0.03, boost alpha significantly; otherwise mild
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
        # Normalize to sum 1, avoid zeros by adding small epsilon then renormalizing
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
        bmax = float(b.max())

        # Build plateau-union surrogate weights with inverse-pressure tail
        w = surrogate_weights(
            b=b,
            frac_tau=stage_params['frac_tau'],
            eps_plateau=stage_params['eps_plateau'],
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
            # interior
            da[1:-1] += 2.0 * (2.0 * a[1:-1] - a[:-2] - a[2:])
            # boundaries
            da[0] += 2.0 * (a[0] - a[1])
            da[-1] += 2.0 * (a[-1] - a[-2])
            grad_a += beta_tv * da
            loss += float(beta_tv * np.sum((np.diff(a)) ** 2))

        if beta_ent > 0.0:
            # Peak-aware entropy: weight indices by participation in top peaks
            w_part = peak_participation_weights(a, b, top_r=2)
            # Huberized: taper weights by clamping a-floor to discourage hitting hard zeros
            eps = 1e-12
            # Gradient of -sum w[i] log(a[i]) = -w[i]/a[i]
            grad_a += -beta_ent * (w_part / (a + eps))
            # Corresponding surrogate loss term
            # Using average weight to keep scale modest
            loss += float(-beta_ent * np.sum(w_part * np.log(a + eps)))

        return loss, grad_a

    # Hard-max polishing ------------------------------------------------------
    def hardmax_polish(a: np.ndarray, steps: int = 3, top_r: int = 2, init_lr: float = 0.5) -> Tuple[np.ndarray, float]:
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
            # Work in logits via softmax Jacobian
            s = np.log(best_a + 1e-12)
            a_now = ensure_simplex_from_logits(s)
            g = grad_a
            ag = float(np.dot(a_now, g))
            grad_s = a_now * (g - ag)
            # Backtracking line-search on logits
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
        L = len(b)
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
        def __init__(self, lr=0.1, b1=0.9, b2=0.999, eps=1e-8):
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
        adam = Adam(lr=0.16, b1=0.9, b2=0.999, eps=1e-8)
        max_iters = 20000
        check_every = 20
        polish_every = 200
        transport_every = 250
        symmetry_mix_every = 60

        # Stage parameters (annealed)
        stage_params = {
            # Plateau-union and temperature schedule
            'frac_tau': 0.02,        # scaled by bmax
            'eps_plateau': 5e-3,
            'top_k': 56,
            'alpha_tail_base': 0.10,
            'alpha_tail_min': 0.02,
            'alpha_tail_max': 0.35,
            'neighbor_radius_base': 1,
            'neighbor_radius_max': 4,
            # Regularizers (annealed)
            'beta_tv': 2.5e-3,
            'beta_ent': 6e-4,
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

            # Anneal temperature and plateau epsilon slowly
            stage_params['frac_tau'] = max(1e-4, stage_params['frac_tau'] * 0.9995)
            stage_params['eps_plateau'] = max(5e-4, stage_params['eps_plateau'] * 0.999)

            # Mild decay of regularizers
            stage_params['beta_tv'] *= 0.9995
            stage_params['beta_ent'] *= 0.9995

            # Surrogate gradient
            loss, grad_a = lse_grad(a, stage_params)

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

            # Encourage symmetry mildly early
            if it % symmetry_mix_every == 0:
                gamma = max(0.0, 0.1 * (1.0 - it / 2500.0))
                if gamma > 0:
                    a = (1.0 - gamma) * a + gamma * a[::-1]
                    a /= a.sum()
                    s = np.log(a + 1e-12)

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
                a_polish, F_polish = hardmax_polish(a, steps=2, top_r=2, init_lr=0.2)
                if F_polish + 1e-12 < last_eval_F:
                    a = a_polish
                    s = np.log(a + 1e-12)
                    last_eval_F = F_polish
                    if F_polish + 1e-12 < best_F:
                        best_F = F_polish
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

    # Multi-start orchestration ----------------------------------------------
    all_seeds = seeds_collection(n)
    # Slight perturbations on baseline seed to escape symmetry traps
    if len(all_seeds) > 0:
        base = all_seeds[0]
        rng = np.random.default_rng(123)
        for _ in range(2):
            noise = rng.normal(0, 1e-3, size=n)
            pert = np.clip(base + noise, 0, None)
            pert = pert / pert.sum()
            all_seeds.append(pert)

    # Time allocation per seed
    time_left = max(0.0, deadline - time.time())
    if len(all_seeds) == 0 or time_left <= 0.1:
        return np.ones(n) / n

    # Bias more time to first few seeds
    n_seeds = len(all_seeds)
    seed_weights = np.array([3.0, 2.0, 1.5] + [1.0] * (n_seeds - 3))
    seed_weights = seed_weights[:n_seeds]
    seed_weights = np.maximum(seed_weights, 0.1)
    seed_weights /= seed_weights.sum()

    # Use 95% of time for optimization, leave 5% buffer
    global_budget = 0.95 * time_left
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
        x_bl = np.linspace(-0.25, 0.25, n)
        a_bl = 1.0 + 4.0 * np.abs(x_bl) - 16.0 * (x_bl ** 2)
        a_bl = (a_bl + a_bl[::-1]) / 2.0
        a_bl = np.maximum(a_bl, 0)
        if a_bl.sum() > 0:
            a_bl = a_bl / a_bl.sum()
            global_best_a = a_bl
        else:
            global_best_a = np.ones(n) / n

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
```
