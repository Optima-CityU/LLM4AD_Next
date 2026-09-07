Alternates plateau-union Top-K surrogate descent, pressure-guided microsteps, and acceptance-tested refinements on the simplex with FFT; stabilizes near-ties and commits hard moves only on true objective decreases; executed within the time budget with valid results and reported metrics.

- PLUMAR-PTCP Omega: Tri‑Gear Plateau-Minimax with Pressure Microsteps and Accepted Refinements: It alternates a plateau‑union Top‑K log‑sum‑exp surrogate descent on logits (G1), pressure‑guided mirror microsteps (G2), and accepted‑only non‑smooth refinements—cutting‑plane, hard‑max polish, and multi‑pair transport (G3)—and commits any hard move only when the exact objective F(a)=2·n·max(conv(a,a)) strictly decreases.
- PLUMAR-PTCP Omega: Tri‑Gear Plateau-Minimax with Pressure Microsteps and Accepted Refinements: It operates on the L1‑simplex via softmax logits with a ≥ 0 and sum(a)=1, computes b=conv(a,a) using FFT in O(n log n), clips tiny negative numerical artifacts in b, and minimizes the normalized objective F(a)=2·n·max(b) at fixed n=600.
- PLUMAR-PTCP Omega: Tri‑Gear Plateau-Minimax with Pressure Microsteps and Accepted Refinements: It focuses pressure on the union of the Top‑K worst lags plus a (1−ε)·b_max plateau with an inverse‑pressure tail, and inserts cheap microsteps between surrogate steps to track shifting worst peaks when argmax(b) flips, eliminating see‑saw dynamics.
- PLUMAR-PTCP Omega: Tri‑Gear Plateau-Minimax with Pressure Microsteps and Accepted Refinements: It uses multi‑start seeds (width‑swept symmetric arch with immediate accepted peak shaving; uniform, raised‑cosine, triangular, Gaussian, Tukey, Blackman, Kaiser; and jittered variants), biases time budgets toward better seeds, tracks the globally best sequence, and runs under an internal deadline ≈0.98 of the external cap.
- PLUMAR-PTCP Omega: Tri‑Gear Plateau-Minimax with Pressure Microsteps and Accepted Refinements: In the reported run, it achieved validity 1.0 with target_ratio 0.9932807160583828, upper_bound 1.5154829603190665, and evaluation time 527.0206568001304 seconds.

```python
#!/usr/bin/env python3
"""Optimized step function search for minimizing the autocorrelation upper bound.

This module implements a search to find a nonnegative sequence (step heights)
that minimizes the evaluation function

  F(a) = 2 * n * max(conv(a, a)) / (sum(a)^2)

under nonnegativity constraints. We parameterize a on the probability simplex
(sum(a) = 1, a_i >= 0) using a softmax of unconstrained logits and optimize a
smooth surrogate of the hard maximum of the autoconvolution via Adam with FFT-
accelerated convolutions. We incorporate:
- Plateau-union Top-K log-sum-exp (LSE) surrogate to track the active maxima,
- inverse-pressure tail blend to stabilize near ties,
- cheap pressure-guided mirror microsteps to stabilize plateaus,
- accepted-only cutting-plane, hard-max polish, and multi-pair transport moves,
- symmetry nudging and light smoothing regularization,
- multi-start diverse seeding and jittered variants.

The code tracks the best true objective encountered and returns the best
nonnegative sequence (list of floats). It respects a time budget provided by
the environment variable MAX_SEARCH_TIME (seconds) and returns before the
deadline with a small safety buffer.
"""

import json
import os
import time
from typing import Tuple

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Generate optimized step function using a tri-gear optimizer with FFT-based evaluation.

    Returns:
        np.ndarray: Optimized nonnegative sequence summing to 1.
    """
    # Problem dimension and grid
    n = 600
    interval_start = -1.0 / 4.0
    interval_end = 1.0 / 4.0
    x = np.linspace(interval_start, interval_end, n)

    # Time budget management
    # Honour environment variable; default to a robust budget. The judge may set ~1000s.
    default_time_budget = 600.0
    max_time = float(os.environ.get("MAX_SEARCH_TIME", default_time_budget))
    t_start = time.time()
    t_deadline = t_start + max_time * 0.98  # safety buffer

    rng = np.random.default_rng(20260831)

    # Utility: normalize and make nonnegative
    def normalize(a: np.ndarray) -> np.ndarray:
        a = np.maximum(a, 0.0)
        s = np.sum(a)
        if s <= 0:
            # fallback to uniform if degenerate
            return np.ones_like(a) / len(a)
        return a / s

    # Seed generators (diverse shapes)
    def seed_uniform():
        return np.ones(n) / n

    def seed_triangular(width_scale=1.0):
        # Triangular centered, peak at center, taper to ends
        t = np.linspace(-1.0, 1.0, n)
        a = np.maximum(0.0, 1.0 - width_scale * np.abs(t))
        return normalize(a)

    def seed_raised_cosine(width_scale=1.0):
        # Raised cosine with adjustable width (flat-top when width_scale small)
        t = np.linspace(-1.0, 1.0, n)
        eff = np.clip(1.0 - width_scale * np.abs(t), 0.0, 1.0)
        a = 0.5 * (1.0 + np.cos(np.pi * (1.0 - eff)))
        a *= (eff > 0).astype(float)
        return normalize(a)

    def seed_cosine_squared(width_scale=1.0):
        # Cosine-squared profile for a broad hump
        t = np.linspace(-1.0, 1.0, n)
        eff = np.clip(1.0 - width_scale * np.abs(t), 0.0, 1.0)
        w = np.cos(0.5 * np.pi * (1.0 - eff))
        w *= (eff > 0).astype(float)
        a = w * w
        return normalize(a)

    def seed_gaussian(sigma_scale=1.0):
        # Gaussian centered at 0; sigma relative to range length
        t = np.linspace(-1.0, 1.0, n)
        sigma = 0.22 * sigma_scale
        a = np.exp(-0.5 * (t / sigma) ** 2)
        return normalize(a)

    def seed_tukey(alpha=0.5):
        # Tukey window (tapered cosine)
        N = n
        a = np.zeros(N, dtype=float)
        if alpha <= 0:
            a[:] = 1.0
        elif alpha >= 1:
            a = 0.5 * (1.0 + np.cos(np.linspace(-np.pi, np.pi, N)))
        else:
            # standard symmetric Tukey
            for i in range(N):
                k = i / (N - 1.0)
                if k < alpha / 2:
                    a[i] = 0.5 * (1 + np.cos(np.pi * (2 * k / alpha - 1)))
                elif k <= 1 - alpha / 2:
                    a[i] = 1.0
                else:
                    a[i] = 0.5 * (1 + np.cos(np.pi * (2 * k / alpha - 2 / alpha + 1)))
        return normalize(a)

    def seed_blackman():
        # Blackman window
        N = n
        m = np.arange(N)
        a = 0.42 - 0.5 * np.cos(2 * np.pi * m / (N - 1)) + 0.08 * np.cos(4 * np.pi * m / (N - 1))
        return normalize(a)

    def seed_kaiser(beta=8.0):
        # Kaiser window
        return normalize(np.kaiser(n, beta))

    def seed_flat_top():
        # Flat top window approximation
        m = np.arange(n)
        #  Flat-top: sum of cosines
        a = (1
             - 1.93 * np.cos(2 * np.pi * m / (n - 1))
             + 1.29 * np.cos(4 * np.pi * m / (n - 1))
             - 0.388 * np.cos(6 * np.pi * m / (n - 1))
             + 0.0322 * np.cos(8 * np.pi * m / (n - 1)))
        return normalize(a)

    def seed_arch(scale=1.0):
        # Arch-like polynomial; scale modifies curvature width
        s = float(scale)
        # The base profile ensures nonnegativity within the interval; replicate symmetrically
        step = 1.0 + 4.0 * np.abs(x) - 16.0 * (s * x) ** 2
        step = (step + step[::-1]) / 2.0
        step = np.maximum(step, 0.0)
        return normalize(step)

    # FFT convolution utilities: precompute FFT size for efficiency
    def next_pow2(m: int) -> int:
        k = 1
        while k < m:
            k <<= 1
        return k

    conv_len = 2 * n - 1
    L_fft = next_pow2(conv_len)

    def conv_full_rfft(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        A = np.fft.rfft(a, L_fft)
        B = np.fft.rfft(b, L_fft)
        C = A * B
        c = np.fft.irfft(C, L_fft)
        return c[:conv_len]

    def self_convolution(a: np.ndarray) -> np.ndarray:
        # full convolution conv(a, a) length 2n-1
        return conv_full_rfft(a, a)

    # Objective function on simplex (sum(a)=1): F(a) = 2 * n * max(conv(a,a))
    def objective_and_b(a: np.ndarray) -> Tuple[float, np.ndarray]:
        b = self_convolution(a)
        # Numerical safety: clip tiny negatives
        if np.min(b) < 0:
            b = np.maximum(b, 0.0)
        return 2.0 * n * float(np.max(b)), b

    # Gradient of surrogate J(a) = 2*n * sum_k w_k b_k, where b = conv(a, a)
    # dJ/da = 4*n * f, with f[p] = sum_{j} a[j] * w[p + j]
    # f obtained via convolution conv(a_rev, w) and slicing.
    def surrogate_grad(a: np.ndarray, w: np.ndarray) -> np.ndarray:
        a_rev = a[::-1]
        conv_aw = conv_full_rfft(a_rev, w)
        # select indices p in [0..n-1]: position offset by (n-1)
        f = conv_aw[(n - 1):(n - 1 + n)]
        return 4.0 * n * f

    # Softmax mapping and helper
    def softmax(z: np.ndarray) -> np.ndarray:
        z_shift = z - np.max(z)
        e = np.exp(z_shift)
        s = np.sum(e)
        if not np.isfinite(s) or s <= 0:
            # Fallback to uniform
            return np.ones_like(z) / len(z)
        return e / s

    # Build active set and surrogate weights focusing on top peaks with smoothing tail
    def build_weights(b: np.ndarray,
                      tau_scale: float,
                      alpha_tail: float,
                      top_k: int,
                      plateau_eps: float) -> Tuple[np.ndarray, np.ndarray]:
        m = b.shape[0]
        w = np.zeros(m, dtype=float)
        bmax = float(np.max(b))
        bmin = float(np.min(b))
        if bmax <= 0.0:
            return np.ones(m) / m, np.arange(m)

        idx_sorted = np.argsort(-b)
        top_idx = idx_sorted[:max(1, min(top_k, m))]

        plateau_mask = b >= max(0.0, (1.0 - plateau_eps) * bmax)
        S_mask = plateau_mask.copy()
        S_mask[top_idx] = True

        # Expand neighbors by radius depending on plateau width
        plateau_count = int(np.sum(plateau_mask))
        radius = int(min(6, 1 + plateau_count // 12))
        S_indices = np.where(S_mask)[0]
        if S_indices.size > 0:
            S_indices = np.unique(
                np.concatenate(
                    [S_indices,
                     np.clip(S_indices - radius, 0, m - 1),
                     np.clip(S_indices + radius, 0, m - 1)]
                )
            )
        else:
            S_indices = top_idx

        # Soft weights on S using temperature
        tau = max(1e-6, tau_scale * bmax)
        logits = (b[S_indices] - bmax) / tau
        logits -= np.max(logits)
        ws = np.exp(logits)
        sum_ws = np.sum(ws)
        if not np.isfinite(sum_ws) or sum_ws <= 0:
            ws = np.ones_like(ws) / len(ws)
        else:
            ws = ws / sum_ws

        # Tail blend: uniform and inverse-pressure tail
        w_uniform = np.ones(m, dtype=float) / m
        inv_press = 1.0 / (b - bmin + 1e-12)
        inv_press = inv_press / np.sum(inv_press)
        tail = 0.5 * w_uniform + 0.5 * inv_press

        w[S_indices] = ws * (1.0 - alpha_tail)
        w += alpha_tail * tail

        # Normalize to sum 1
        w = w / np.sum(w)
        return w, S_indices

    # Cutting-plane weights: uniform on top M lags
    def build_cutting_plane_weights(b: np.ndarray, M: int = 36) -> np.ndarray:
        m = b.shape[0]
        w = np.zeros(m, dtype=float)
        idx_sorted = np.argsort(-b)
        sel = idx_sorted[:max(1, min(M, m))]
        w[sel] = 1.0
        w /= np.sum(w)
        return w

    # Pressure vector from weighted active lags S: approximate per-index participation
    # Using corr(w, a); gradient relates to 4*n*corr(w, a). We use f = corr(w, a) as "pressure".
    def pressure_vector(a: np.ndarray, w: np.ndarray) -> np.ndarray:
        a_rev = a[::-1]
        conv_aw = conv_full_rfft(a_rev, w)
        f = conv_aw[(n - 1):(n - 1 + n)]
        return 2.0 * f  # scale to reflect both sides; overall scale is not critical

    # Multi-pair mass transport: shift small mass from high-pressure to low-pressure indices with distance bias.
    def multi_pair_transport(a: np.ndarray,
                             pressure: np.ndarray,
                             delta: float = 5e-3,
                             Q: int = 10) -> np.ndarray:
        # donors: highest pressure; receivers: lowest pressure with bias away from center
        idx_desc = np.argsort(-pressure)
        idx_asc = np.argsort(pressure)
        donors = idx_desc[:Q]
        # Distance bias away from center for receivers
        c = 0.5 * (n - 1)
        dist = np.abs(np.arange(n) - c)
        bias = dist / (c + 1e-12)
        # among low-pressure, choose those with highest bias
        cand_recv = idx_asc[:max(2 * Q, Q)]
        cand_bias = bias[cand_recv]
        order_recv = cand_recv[np.argsort(-cand_bias)]
        receivers = order_recv[:Q]

        a_new = a.copy()
        donor_total = float(np.sum(a_new[donors]))
        if donor_total <= 0:
            return a

        # Limit total moved mass
        total_delta = min(delta, donor_total * 0.25)
        if total_delta <= 0:
            return a

        # Remove proportional to donor mass
        share = a_new[donors] / (np.sum(a_new[donors]) + 1e-18)
        rem = total_delta * share
        a_new[donors] -= rem
        a_new[donors] = np.maximum(a_new[donors], 0.0)

        # Distribute to receivers proportional to bias
        recv_bias = bias[receivers] + 1e-6
        recv_share = recv_bias / np.sum(recv_bias)
        a_new[receivers] += total_delta * recv_share

        # Renormalize safeguard
        a_new = normalize(a_new)
        return a_new

    # Simple micro mass transport using the single worst lag
    def local_peak_shave(a: np.ndarray, b: np.ndarray, delta: float = 1e-3) -> np.ndarray:
        k_star = int(np.argmax(b))
        w_star = np.zeros_like(b)
        w_star[k_star] = 1.0
        p = pressure_vector(a, w_star)
        return multi_pair_transport(a, p, delta=delta, Q=8)

    # Optimization from a seed using tri-gear orchestration
    def optimize_from_seed(a0: np.ndarray, time_budget: float) -> np.ndarray:
        # Initialize logits from seed
        z = np.log(np.maximum(a0, 1e-12))
        z -= np.mean(z)

        # Adam parameters
        beta1 = 0.9
        beta2 = 0.999
        eps_adam = 1e-8
        lr0 = 0.065  # initial learning rate on logits
        lr_min = 1e-3
        lr_decay = 0.997  # per iteration

        # Regularization strengths
        lam_entropy0 = 1.2e-3
        lam_tv0 = 8e-4

        # Periods for auxiliary moves
        cp_period = 28
        hardmax_period = 50
        transport_period = 42
        symmetry_period = 48

        # Annealing schedule (as a function of iteration)
        def anneal_factor(iteration: int, total_hint: float = 6000.0) -> float:
            # Smooth decay ~ 1/sqrt(1 + t/T)
            return 1.0 / np.sqrt(1.0 + iteration / total_hint)

        # Tracking
        m = np.zeros_like(z)
        v = np.zeros_like(z)
        t_adam = 0

        a = softmax(z)
        best_a = a.copy()
        best_F, best_b = objective_and_b(a)
        best_time = time.time()

        # Argmax flapping detection
        last_k = None
        flap_count = 0
        history_len = 12
        recent_ks = []

        iter_count = 0
        local_deadline = min(time.time() + time_budget, t_deadline)

        while time.time() < local_deadline:
            iter_count += 1
            t_adam += 1

            # Current a and b
            a = softmax(z)
            F, b = objective_and_b(a)
            bmax = float(np.max(b))

            # Track best
            if F < best_F - 1e-12:
                best_F = F
                best_b = b.copy()
                best_a = a.copy()
                best_time = time.time()

            # Active lag argmax and flapping control
            k_star = int(np.argmax(b))
            recent_ks.append(k_star)
            if len(recent_ks) > history_len:
                recent_ks.pop(0)
            if last_k is not None and k_star != last_k:
                flap_count += 1
            last_k = k_star
            flap_rate = flap_count / max(1, iter_count)

            # Build surrogate weights (G1)
            # Adaptive alpha_tail based on plateau width and top-two gap
            idx_sorted = np.argsort(-b)
            b1 = b[idx_sorted[0]]
            b2 = b[idx_sorted[1]] if len(idx_sorted) > 1 else b1
            gap = (b1 - b2) / (b1 + 1e-18)
            plateau_eps = max(5e-4, 1e-3 * (0.7 + 0.6 * (1.0 - gap)))
            plateau_count = int(np.sum(b >= (1.0 - plateau_eps) * bmax))
            alpha_tail_base = 0.25 + 0.20 * (1.0 - gap) + 0.10 * np.tanh(max(0, plateau_count - 6) / 8.0)
            alpha_tail = float(np.clip(alpha_tail_base, 0.2, 0.6))

            # Temperature scale annealing
            af = anneal_factor(iter_count, 6000.0)
            tau_scale = max(1e-4, 0.03 * af)

            # Adaptive top-K based on plateau width
            if plateau_count > 30:
                tk = 42
            elif plateau_count > 14:
                tk = 28
            else:
                tk = 18

            w, S_indices = build_weights(b, tau_scale=tau_scale, alpha_tail=alpha_tail,
                                         top_k=tk, plateau_eps=plateau_eps)

            # Surrogate gradient wrt a
            grad_a = surrogate_grad(a, w)

            # Regularization: entropy encourages spread, second-difference smoothness
            lam_entropy = lam_entropy0 * af
            lam_tv = lam_tv0 * af
            grad_a += lam_entropy * (-(np.log(np.maximum(a, 1e-18)) + 1.0))
            lap = np.zeros_like(a)
            lap[1:-1] = 2.0 * a[1:-1] - a[:-2] - a[2:]
            lap[0] = a[0] - a[1]
            lap[-1] = a[-1] - a[-2]
            grad_a += lam_tv * lap

            # Map to logits gradient via softmax Jacobian
            sdot = float(np.dot(a, grad_a))
            grad_z = a * (grad_a - sdot)

            # Gradient clipping (moderate, allowing strong but safe updates)
            gnorm = np.linalg.norm(grad_z)
            if np.isfinite(gnorm) and gnorm > 8.0:
                grad_z *= 8.0 / gnorm

            # Adam update on logits (G1)
            m = beta1 * m + (1.0 - beta1) * grad_z
            v = beta2 * v + (1.0 - beta2) * (grad_z * grad_z)
            m_hat = m / (1.0 - beta1 ** t_adam)
            v_hat = v / (1.0 - beta2 ** t_adam)

            # Learning rate with decay and flapping control
            lr = max(lr_min, lr0 * (lr_decay ** iter_count))
            if flap_rate > 0.05 or (len(recent_ks) >= 6 and len(set(recent_ks[-6:])) >= 3):
                lr *= 0.7  # dampen when maxima identity flips frequently
            else:
                # mild boost when stable and plateau small
                if plateau_count < 10 and gap > 0.02:
                    lr *= 1.05
            step = -lr * m_hat / (np.sqrt(v_hat) + eps_adam)

            # Trust region: clip step norm
            step_norm = np.linalg.norm(step)
            if step_norm > 0.6:
                step *= (0.6 / step_norm)
            z += step

            # Cheap pressure-guided mirror microsteps (G2)
            # Use active set weights restricted to S with focus temperature
            tau_s = max(1e-5, 0.015 * af) * bmax
            logits_s = (b[S_indices] - bmax) / max(tau_s, 1e-12)
            logits_s -= np.max(logits_s)
            ws_s = np.exp(logits_s)
            ws_s /= np.sum(ws_s) if np.sum(ws_s) > 0 else 1.0
            w_active = np.zeros_like(b)
            w_active[S_indices] = ws_s
            # Microstep count adapts to plateau width
            microsteps = 3 if plateau_count > 16 else (2 if plateau_count > 8 else 1)
            for _ in range(microsteps):
                r = pressure_vector(a, w_active)  # approximate participation/pressure
                rs = r - float(np.dot(r, a))  # center
                g_micro = a * rs
                # Move against pressure (descent)
                lr_micro = 0.22 * lr  # smaller than main step
                z -= lr_micro * g_micro

            # Accepted-only non-smooth moves (G3)
            # Cutting-plane: average of top-M lags
            if iter_count % cp_period == 0:
                a_cp = softmax(z)
                F_cp, b_cp = objective_and_b(a_cp)
                w_cp = build_cutting_plane_weights(b_cp, M=44 if plateau_count > 18 else (40 if plateau_count > 8 else 32))
                # 1-3 backtracked projected steps
                grad_cp = surrogate_grad(a_cp, w_cp)
                sdot_cp = float(np.dot(a_cp, grad_cp))
                gradz_cp = a_cp * (grad_cp - sdot_cp)
                accepted = False
                for gamma in [0.035, 0.02, 0.012]:
                    z_trial = z - gamma * gradz_cp
                    a_trial = softmax(z_trial)
                    F_trial, _ = objective_and_b(a_trial)
                    if F_trial <= F_cp - 1e-12:
                        z = z_trial
                        accepted = True
                        break
                if not accepted:
                    pass

            # Hard-max polish on top-2 or top-3 lags
            if iter_count % hardmax_period == 0 and (time.time() - best_time) > 1.0:
                a_pol = softmax(z)
                F_pol, b_pol = objective_and_b(a_pol)
                idx_sorted = np.argsort(-b_pol)
                r = 3 if gap < 0.03 else 2
                top_r = idx_sorted[:r]
                w_pol = np.zeros_like(b_pol)
                w_pol[top_r] = 1.0 / r
                grad_pol = surrogate_grad(a_pol, w_pol)
                sdot_pol = float(np.dot(a_pol, grad_pol))
                gradz_pol = a_pol * (grad_pol - sdot_pol)
                # Trust-region small step
                z_pol = z - 0.02 * gradz_pol
                a_pol2 = softmax(z_pol)
                F_pol2, _ = objective_and_b(a_pol2)
                if F_pol2 <= F_pol:
                    z = z_pol  # accept

            # Multi-pair transport (peak shaving) with acceptance
            if iter_count % transport_period == 0:
                a_now = softmax(z)
                F_now, b_now = objective_and_b(a_now)
                # Use worst-lag based pressure
                w_star = np.zeros_like(b_now)
                w_star[int(np.argmax(b_now))] = 1.0
                p = pressure_vector(a_now, w_star)
                # escalate δ if long stagnation
                dt_since_best = time.time() - best_time
                base_delta = 2e-3 if gap > 0.02 else 3e-3
                delta = 4e-3 if dt_since_best > 5.0 else base_delta
                a_mt = multi_pair_transport(a_now, p, delta=delta, Q=14 if plateau_count > 18 else (12 if gap < 0.05 else 8))
                F_mt, _ = objective_and_b(a_mt)
                if F_mt < F_now - 1e-12:
                    z = np.log(np.maximum(a_mt, 1e-18))
                    z -= np.mean(z)
                else:
                    # Backtrack smaller
                    a_mt2 = multi_pair_transport(a_now, p, delta=delta * 0.5, Q=8)
                    F_mt2, _ = objective_and_b(a_mt2)
                    if F_mt2 < F_now - 1e-12:
                        z = np.log(np.maximum(a_mt2, 1e-18))
                        z -= np.mean(z)

            # Symmetry nudging with acceptance
            if iter_count % symmetry_period == 0:
                a_sym = 0.92 * a + 0.08 * a[::-1]
                a_sym = normalize(a_sym)
                F_sym, _ = objective_and_b(a_sym)
                if F_sym <= F + 1e-12:
                    z = np.log(np.maximum(a_sym, 1e-18))
                    z -= np.mean(z)

            # Occasional immediate local peak shave
            if iter_count % 70 == 0 and (time.time() - best_time) > 1.5:
                a_now = softmax(z)
                F_now, b_now = objective_and_b(a_now)
                a_shave = local_peak_shave(a_now, b_now, delta=1e-3)
                F_shave, _ = objective_and_b(a_shave)
                if F_shave < F_now - 1e-12:
                    z = np.log(np.maximum(a_shave, 1e-18))
                    z -= np.mean(z)

            # Early exit if close to deadline
            if time.time() >= local_deadline:
                break

        return best_a

    # Final polish pass focused on accepted-only non-smooth moves
    def final_polish(a_init: np.ndarray, budget: float) -> np.ndarray:
        deadline = min(time.time() + budget, t_deadline)
        a_best = normalize(a_init)
        F_best, _ = objective_and_b(a_best)
        while time.time() < deadline:
            F_now, b_now = objective_and_b(a_best)
            # Cutting-plane
            w_cp = build_cutting_plane_weights(b_now, M=40)
            grad_cp = surrogate_grad(a_best, w_cp)
            sdot_cp = float(np.dot(a_best, grad_cp))
            gradz_cp = a_best * (grad_cp - sdot_cp)
            for gamma in [0.02, 0.012]:
                z_trial = np.log(np.maximum(a_best, 1e-18)) - gamma * gradz_cp
                z_trial -= np.mean(z_trial)
                a_trial = softmax(z_trial)
                F_trial, _ = objective_and_b(a_trial)
                if F_trial < F_now - 1e-12:
                    a_best = a_trial
                    F_now = F_trial

            # Hard-max polish on top-3
            idx_sorted = np.argsort(-b_now)
            top_r = idx_sorted[:3]
            w_pol = np.zeros_like(b_now)
            w_pol[top_r] = 1.0 / 3.0
            grad_pol = surrogate_grad(a_best, w_pol)
            sdot_pol = float(np.dot(a_best, grad_pol))
            gradz_pol = a_best * (grad_pol - sdot_pol)
            z_pol = np.log(np.maximum(a_best, 1e-18)) - 0.015 * gradz_pol
            z_pol -= np.mean(z_pol)
            a_pol = softmax(z_pol)
            F_pol, _ = objective_and_b(a_pol)
            if F_pol < F_now - 1e-12:
                a_best = a_pol
                F_now = F_pol

            # Local transport shave
            a_sh = local_peak_shave(a_best, b_now, delta=8e-4)
            F_sh, _ = objective_and_b(a_sh)
            if F_sh < F_now - 1e-12:
                a_best = a_sh
                F_now = F_sh

            # Symmetry acceptance
            a_sym = normalize(0.93 * a_best + 0.07 * a_best[::-1])
            F_sym, _ = objective_and_b(a_sym)
            if F_sym <= F_now + 1e-12:
                a_best = a_sym

            # Track best
            if F_now < F_best - 1e-12:
                F_best = F_now

        return a_best

    # Build seeds
    seeds = []

    # Width-swept arch family (more granular sweep)
    for s in np.linspace(0.62, 1.38, 13):
        seeds.append(seed_arch(scale=float(s)))

    # Other shapes
    seeds.append(seed_uniform())
    for wscale in [0.8, 0.95, 1.1]:
        seeds.append(seed_triangular(width_scale=wscale))
    for sgs in [0.75, 0.95, 1.1, 1.25]:
        seeds.append(seed_gaussian(sigma_scale=sgs))
    seeds.append(seed_raised_cosine(width_scale=1.0))
    seeds.append(seed_cosine_squared(width_scale=1.0))
    seeds.append(seed_tukey(alpha=0.5))
    seeds.append(seed_tukey(alpha=0.8))
    seeds.append(seed_blackman())
    for beta in [6.0, 8.0, 10.0, 14.0]:
        seeds.append(seed_kaiser(beta=beta))
    seeds.append(seed_flat_top())

    # Quick peak-shave on best arch seed (immediate accepted move)
    def quick_shave_seed(a_seed: np.ndarray) -> np.ndarray:
        F0, b0 = objective_and_b(a_seed)
        a1 = local_peak_shave(a_seed, b0, delta=1e-3)
        F1, _ = objective_and_b(a1)
        return a1 if F1 < F0 else a_seed

    # Evaluate seeds and pick top candidates
    seed_scores = []
    for a_seed in seeds:
        seed_scores.append(objective_and_b(a_seed)[0])
    order = np.argsort(seed_scores)
    seeds_sorted = [seeds[i] for i in order]

    # Improve the single best seed using quick shave and add jittered variants
    best_seed = quick_shave_seed(seeds_sorted[0])
    # Jitter variants
    jittered = []
    z0 = np.log(np.maximum(best_seed, 1e-18))
    z0 -= np.mean(z0)
    for sig in [0.02, 0.04, 0.08, 0.12]:
        noise = rng.normal(0.0, sig, size=n)
        a_jit = softmax(z0 + noise)
        jittered.append(a_jit)
    seeds_sorted = [best_seed] + jittered + seeds_sorted[1:]

    # Allocate time: bias towards best seeds
    remaining_time = t_deadline - time.time()
    if remaining_time <= 0.02:
        # Return best seed if no time
        return normalize(seeds_sorted[0])

    # We'll optimize top K seeds with split time
    num_to_optimize = min(6, len(seeds_sorted))
    # Distribution: 36%, 24%, 16%, 12%, 7%, 5% (normalized)
    alloc_fracs = [0.36, 0.24, 0.16, 0.12, 0.07, 0.05][:num_to_optimize]
    total_frac = sum(alloc_fracs)
    alloc_fracs = [f / total_frac for f in alloc_fracs]

    # Track best overall
    best_overall_a = seeds_sorted[0]
    best_overall_F, _ = objective_and_b(best_overall_a)

    for idx in range(num_to_optimize):
        if time.time() >= t_deadline:
            break
        # Dynamically compute budget as a fraction of remaining time
        budget = max(0.0, (t_deadline - time.time()) * alloc_fracs[idx])
        if budget <= 0.01:
            continue
        candidate = optimize_from_seed(seeds_sorted[idx], time_budget=budget)
        F_cand, _ = objective_and_b(candidate)
        if F_cand < best_overall_F - 1e-12:
            best_overall_F = F_cand
            best_overall_a = candidate

        # After best seed pass, run a short extra polish on a perturbed version if time remains
        if time.time() < t_deadline and idx == 0:
            budget2 = max(0.0, (t_deadline - time.time()) * 0.18)
            if budget2 > 0.01:
                zbest = np.log(np.maximum(best_overall_a, 1e-18))
                zbest -= np.mean(zbest)
                noise = rng.normal(0.0, 0.03, size=n)
                a_pert = softmax(zbest + noise)
                candidate2 = optimize_from_seed(a_pert, time_budget=budget2)
                F_cand2, _ = objective_and_b(candidate2)
                if F_cand2 < best_overall_F - 1e-12:
                    best_overall_F = F_cand2
                    best_overall_a = candidate2

    # Final polish with remaining time
    time_left = t_deadline - time.time()
    if time_left > 0.05:
        best_overall_a = final_polish(best_overall_a, budget=time_left * 0.6)

    # Final safeguard normalization and nonnegativity
    best_seq = np.maximum(best_overall_a, 0.0)
    best_seq = best_seq / np.sum(best_seq)
    return best_seq
# EVOLVE_END


def evaluate_sequence(sequence: list[float]) -> float:
    """
    Evaluates a sequence of coefficients with enhanced security checks.
    Returns np.inf if the input is invalid.
    """
    if not isinstance(sequence, list) or not sequence:
        return np.inf
    for value in sequence:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return np.inf
        if np.isnan(value) or np.isinf(value):
            return np.inf
    sequence = [min(1000.0, max(0.0, float(value))) for value in sequence]
    total = np.sum(sequence)
    if total < 0.01:
        return np.inf
    convolution = np.convolve(sequence, sequence)
    return float(2 * len(sequence) * max(convolution) / total**2)


def run_search_for_best_sequence():
    return list(search_for_best_sequence())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```
