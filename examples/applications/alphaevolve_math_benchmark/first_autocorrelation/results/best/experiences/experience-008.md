Actionable design pattern: alternate logit-space Adam and simplex PGD under a top-K blended smooth-max of the self-convolution, add peak-cutting heuristics when central peaks persist, and use FFTs to keep iterations O(n log n) within time budgets.

- Adaptive Dual-Phase Minimax Convolution Equalizer (ADPMCE): ADPMCE alternates Adam updates in softmax-logit space with projected gradient descent on the simplex using a top-K blended smooth-max surrogate of b = a * a to suppress multiple worst lags simultaneously and reduce peak swapping.
- Adaptive Dual-Phase Minimax Convolution Equalizer (ADPMCE): The algorithm invokes peak-specific heuristics—pairwise polarization of mirrored indices and a gentle tilt—when central or near-central peaks persist, then renormalizes on the simplex to maintain non-negativity and unit sum.
- Adaptive Dual-Phase Minimax Convolution Equalizer (ADPMCE): Real-FFT convolution is used throughout to keep each iteration O(n log n), enabling many A→B→C cycles within the fixed time budget.
- Adaptive Dual-Phase Minimax Convolution Equalizer (ADPMCE): In this run, the method achieved validity = 1.0 with upper_bound = 1.5339909776924197 and target_ratio = 0.9812965147059864, with evaluation completed in approximately 4.225 seconds.

```python
#!/usr/bin/env python3
"""Optimized step function search for minimizing autocorrelation-based evaluation."""

import json
import time
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def search_for_best_sequence(time_budget: float = 990.0, random_seed: int = 42) -> np.ndarray:
    """
    Optimize a nonnegative step sequence on the simplex to minimize the evaluation function.

    Implements the ADPMCE algorithm (dual-phase optimization with heuristics):
      - Phase A: Adam updates in softmax-logit space using a smooth-max surrogate of the
        maximum of the self-convolution b = a * a, with multi-peak blending.
      - Phase B: Projected gradient descent (PGD) directly on the simplex for precise
        local corrections, with backtracking line search.
      - Phase C: Heuristic peak cuts: pairwise polarization, gentle tilt, and
        anti-alignment reallocation guided by participation scores.

    Includes small stabilizers (TV on a, Var(b)) and an entropy term early on.
    Uses FFT acceleration for all convolutions.

    Args:
        time_budget: Allowed wall-clock time in seconds before returning the best found.
        random_seed: Seed for reproducibility.

    Returns:
        Best found sequence as a NumPy array of length n, nonnegative and summing to 1.
    """
    rng = np.random.default_rng(random_seed)
    start_time = time.time()

    # Problem dimension suggested by current best
    n = 600

    # ---------------------
    # Hyperparameters
    # ---------------------
    # Annealed smooth-max temperatures (higher -> smoother). Move towards hard max.
    tau_schedule = [0.06, 0.03, 0.015, 0.008]
    # Number of Adam iterations per stage (moderate to fit in budget)
    iters_per_stage = [300, 300, 300, 300]
    # Blending with top-K worst lags increases over stages
    alpha_top_schedule = [0.2, 0.35, 0.45, 0.55]
    # Entropy regularization schedule (strong early, decays to zero)
    lambda_ent_schedule = [2e-3, 1e-3, 5e-4, 0.0]
    # Total-variation and Var(b) penalties (tiny stabilizers)
    lambda_tv = 5e-6
    lambda_var = 2e-5
    # Smooth-max top-K (number of worst convolution bins to co-target)
    top_k = 7

    # Adam parameters in logit space
    lr = 0.05
    beta1 = 0.9
    beta2 = 0.999
    eps_adam = 1e-8

    # PGD parameters
    pgd_steps_per_stage = 15
    pgd_init_lr = 0.10
    pgd_min_lr = 1e-4
    backtrack_factor = 0.5

    # Heuristic moves frequency and strengths
    heur_interval = 100  # apply heuristics every this many Adam iterations
    polarize_frac = 0.05  # fraction of pair mass to polarize
    tilt_strength = 0.02  # gentle tilt strength
    anti_align_frac = 0.02  # small reallocation mass fraction

    # Utility functions

    def softmax(w: np.ndarray) -> np.ndarray:
        """Stable softmax producing nonnegative sequence that sums to 1."""
        m = np.max(w)
        z = np.exp(w - m)
        return z / np.sum(z)

    def next_pow_two(m: int) -> int:
        """Return the smallest power-of-two >= m."""
        return 1 << ((m - 1).bit_length())

    # Precompute FFT length for repeated use
    L_b = 2 * n - 1
    # For conv(weights, a_rev), length is (2n-1) + n - 1 = 3n - 2
    L_c = (2 * n - 1) + n - 1
    fft_len_b = next_pow_two(L_b)
    fft_len_c = next_pow_two(L_c)

    def self_convolution(a: np.ndarray) -> np.ndarray:
        """
        Compute b = a * a (linear convolution) using real FFT.
        Returns length 2n-1 convolution.
        """
        A = np.fft.rfft(a, fft_len_b)
        b_full = np.fft.irfft(A * A, fft_len_b)
        return b_full[:L_b]

    def conv_weights_with_arev(weights: np.ndarray, a: np.ndarray) -> np.ndarray:
        """
        Compute c[j] = sum_k weights[k] * a[k - j] with index validity handled by convolution.

        Equivalent to: c[j] = (weights convolve a_rev)[j + (n - 1)],
        where a_rev is reversed a. This uses FFT for speed.

        Args:
            weights: Array of length 2n-1.
            a: Array of length n.

        Returns:
            c: Array of length n.
        """
        a_rev = a[::-1]
        Wr = np.fft.rfft(weights, fft_len_c)
        Ar = np.fft.rfft(a_rev, fft_len_c)
        d = np.fft.irfft(Wr * Ar, fft_len_c)[:L_c]
        return d[(n - 1):(n - 1 + n)]

    def evaluate_sequence(sequence: List[float]) -> float:
        """
        Secure evaluation function per the task specification.
        Returns np.inf if invalid.
        """
        # Verify that the input is a list
        if not isinstance(sequence, list):
            return np.inf

        # Reject empty lists
        if not sequence:
            return np.inf

        # Check each element in the list for validity
        for x in sequence:
            # Reject boolean types and any other non-integer/non-float types
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                return np.inf
            # Reject NaN or infinity
            if np.isnan(x) or np.isinf(x):
                return np.inf

        # Convert to floats
        seq = [float(x) for x in sequence]
        # Clamp negative and too-large values
        seq = [max(0.0, x) for x in seq]
        seq = [min(1000.0, x) for x in seq]

        total = np.sum(seq)
        if total < 0.01:
            return np.inf

        b = np.convolve(seq, seq)
        return float(2 * len(seq) * np.max(b) / (total**2))

    def logsumexp(x: np.ndarray, tau: float) -> float:
        """Compute tau * logsumexp(x / tau) in a numerically stable manner."""
        t = max(tau, 1e-9)
        m = np.max(x)
        return t * (np.log(np.sum(np.exp((x - m) / t))) + m / t)

    def topk_mask(b: np.ndarray, k: int) -> np.ndarray:
        """Return a uniform mask over the top-k entries of b."""
        idx = np.argsort(-b)[:k]
        m = np.zeros_like(b)
        m[idx] = 1.0 / max(k, 1)
        return m

    def compute_surrogate_and_grad(
        a: np.ndarray,
        tau: float,
        alpha_top: float,
        lambda_var_: float,
        lambda_tv_: float,
        lambda_ent_: float,
        k_top: int,
    ) -> Tuple[float, np.ndarray, np.ndarray, np.ndarray]:
        """
        Compute surrogate objective value and gradient wrt a, along with b and blended weights.

        Objective:
          F(a) = 2n * [(1 - alpha) * tau * logsumexp(b/tau) + alpha * mean_topK(b)]
                   + lambda_var * Var(b)
                   + 0.5 * lambda_tv * sum (a[i] - a[i-1])^2
                   + lambda_ent * sum a_i log a_i

        Gradients are computed accordingly using FFT-accelerated convolutions.

        Returns:
          F: scalar surrogate value
          g_a: gradient wrt a
          b: convolution a * a (length 2n-1)
          weights: blended weights (length 2n-1) used for gradient mapping
        """
        b = self_convolution(a)
        # Softmax weights over b/tau
        tau_eff = max(tau, 1e-9)
        b_shift = b - np.max(b)
        w_soft = np.exp(b_shift / tau_eff)
        w_soft_sum = np.sum(w_soft)
        if not np.isfinite(w_soft_sum) or w_soft_sum <= 0.0:
            w_soft = np.ones_like(b) / len(b)
        else:
            w_soft /= w_soft_sum

        # Top-K uniform weights
        w_top = topk_mask(b, k_top)

        # Combined weights used for gradient of blended objective
        weights = (1.0 - alpha_top) * w_soft + alpha_top * w_top

        # Surrogate main term value
        mean_topk = float(np.sum(w_top * b))  # since w_top uniform over top-k
        main_val = (1.0 - alpha_top) * logsumexp(b, tau_eff) + alpha_top * mean_topk

        # Gradient wrt a of main term:
        # grad_j = 2*n * 2 * conv(weights, reverse(a))[j]
        c = conv_weights_with_arev(weights, a)  # length n
        g_a_main = (2.0 * n) * 2.0 * c

        # Variance of b term
        Lb = b.shape[0]
        m_b = np.mean(b)
        var_b = float(np.mean((b - m_b) ** 2))
        # gradient wrt b: d/d b [(lambda_var) * Var(b)] = lambda_var * 2/L * (b - mean(b))
        v_b = (2.0 / Lb) * (b - m_b)
        c_var = conv_weights_with_arev(v_b, a)
        g_a_var = 2.0 * lambda_var_ * c_var  # factor 2 from ∂b/∂a = 2*a[k-j]

        # Total variation on a: 0.5 * lambda_tv * sum (a[i] - a[i-1])^2
        d = a[1:] - a[:-1]
        g_tv = np.zeros_like(a)
        g_tv[:-1] -= d
        g_tv[1:] += d
        tv_val = 0.5 * float(np.sum(d * d))
        g_a_tv = lambda_tv_ * g_tv

        # Entropy term: lambda_ent * sum a_i log a_i
        # gradient: lambda_ent * (1 + log(a_i))
        eps = 1e-12
        ent_val = float(np.sum(a * np.log(a + eps)))
        g_a_ent = lambda_ent_ * (1.0 + np.log(a + eps))

        # Total objective value with scaling 2n over main term
        F = (2.0 * n) * main_val + lambda_var_ * var_b + lambda_tv_ * tv_val + lambda_ent_ * ent_val

        # Total gradient
        g_a = g_a_main + g_a_var + g_a_tv + g_a_ent

        return F, g_a, b, weights

    def proj_simplex(v: np.ndarray) -> np.ndarray:
        """Project vector v onto the probability simplex {x >= 0, sum x = 1}."""
        # Algorithm from "Projection onto the Probability Simplex" (Wang & Carreira-Perpinan, 2013)
        u = np.sort(v)[::-1]
        cssv = np.cumsum(u)
        rho = np.nonzero(u * np.arange(1, len(u) + 1) > (cssv - 1))[0][-1]
        theta = (cssv[rho] - 1.0) / float(rho + 1)
        w = np.maximum(v - theta, 0.0)
        # Normalize in case of numerical drift
        s = np.sum(w)
        if s <= 0.0:
            w = np.ones_like(v) / len(v)
        else:
            w /= s
        return w

    # Heuristic moves (Phase C)

    def pairwise_polarize(a: np.ndarray, frac: float) -> np.ndarray:
        """Polarize mirrored pairs to reduce central-lag product by making pairs more unequal."""
        a2 = a.copy()
        half = n // 2
        for j in range(half):
            k = n - 1 - j
            x, y = a2[j], a2[k]
            if x == y:
                continue
            # Move small fraction of mass from smaller to larger to increase asymmetry
            if x < y:
                delta = min(frac * (y - x), x * 0.5)
                a2[j] -= delta
                a2[k] += delta
            else:
                delta = min(frac * (x - y), y * 0.5)
                a2[j] += delta
                a2[k] -= delta
        # Renormalize
        s = np.sum(a2)
        if s <= 0.0:
            return a
        return np.maximum(a2 / s, 0.0)

    def gentle_tilt(a: np.ndarray, strength: float) -> np.ndarray:
        """Apply a gentle linear tilt and renormalize."""
        idx = np.arange(n)
        mid = 0.5 * (n - 1)
        ramp = 1.0 + strength * (idx - mid) / max(n - 1, 1)
        tilted = a * ramp
        tilted = np.maximum(tilted, 0.0)
        s = np.sum(tilted)
        if s <= 0.0:
            return a
        return tilted / s

    def anti_alignment_reallocate(a: np.ndarray, weights: np.ndarray, frac: float) -> np.ndarray:
        """
        Move small mass from indices that heavily participate in top-K worst lags to those that don't.
        Participation score c[i] approximated as conv(weights, reverse(a))[i].
        """
        c = conv_weights_with_arev(weights, a)
        # High scores: donor set; low scores: receiver set
        k = max(1, int(0.1 * n))
        donors = np.argsort(-c)[:k]
        receivers = np.argsort(c)[:k]
        a2 = a.copy()
        mass = frac * np.sum(a2)
        if mass <= 0.0:
            return a
        # Distribute equally among pairs
        per_pair = mass / k
        for d_idx, r_idx in zip(donors, receivers):
            delta = min(per_pair, a2[d_idx] * 0.4)
            a2[d_idx] -= delta
            a2[r_idx] += delta
        a2 = np.maximum(a2, 0.0)
        s = np.sum(a2)
        if s <= 0.0:
            return a
        return a2 / s

    # Seed initial sequences (nonnegative, sum 1)
    def seed_sequences() -> List[np.ndarray]:
        seeds = []

        # Uniform
        a_uniform = np.ones(n, dtype=float)
        a_uniform /= np.sum(a_uniform)
        seeds.append(a_uniform)

        # Hann window
        i = np.arange(n, dtype=float)
        a_hann = 0.5 - 0.5 * np.cos(2.0 * np.pi * i / (n - 1))
        a_hann = np.maximum(a_hann, 0.0)
        a_hann /= np.sum(a_hann)
        seeds.append(a_hann)

        # Cosine squared centered
        x = (i - (n - 1) / 2.0) / (n - 1)
        a_cos2 = np.cos(np.pi * x) ** 2
        a_cos2 = np.maximum(a_cos2, 0.0)
        a_cos2 /= np.sum(a_cos2)
        seeds.append(a_cos2)

        # Beta(1.5, 1.5) shape on [0,1] mapped to indices
        t = i / (n - 1)
        a_beta = np.power(t + 1e-12, 0.5) * np.power(1.0 - t + 1e-12, 0.5)
        a_beta = np.maximum(a_beta, 0.0)
        a_beta /= np.sum(a_beta)
        seeds.append(a_beta)

        # Trapezoid: ramp up, plateau, ramp down
        plateau_frac = 0.4
        ramp_frac = (1.0 - plateau_frac) / 2.0
        ramp_len = int(np.floor(ramp_frac * n))
        plateau_len = n - 2 * ramp_len
        a_trap = np.zeros(n, dtype=float)
        # Rising ramp
        if ramp_len > 0:
            a_trap[:ramp_len] = np.linspace(0.0, 1.0, ramp_len, endpoint=False)
        # Plateau
        a_trap[ramp_len:ramp_len + plateau_len] = 1.0
        # Falling ramp
        if ramp_len > 0:
            a_trap[ramp_len + plateau_len:] = np.linspace(1.0, 0.0, ramp_len, endpoint=False)
        a_trap = np.maximum(a_trap, 0.0)
        a_trap /= np.sum(a_trap)
        seeds.append(a_trap)

        # Slightly perturbed symmetric seed to break symmetry traps
        a_pert = a_hann.copy()
        a_pert += 0.01 * rng.normal(size=n)
        a_pert = np.maximum(a_pert, 0.0)
        a_pert /= np.sum(a_pert)
        seeds.append(a_pert)

        # Baseline polynomial-like seed
        interval_start = -1 / 4
        interval_end = 1 / 4
        xgrid = np.linspace(interval_start, interval_end, n)
        a_poly = 1.0 + 4.0 * np.abs(xgrid) - 16.0 * xgrid**2
        a_poly = (a_poly + a_poly[::-1]) / 2.0
        a_poly = np.maximum(a_poly, 0.0)
        a_poly /= np.sum(a_poly)
        seeds.append(a_poly)

        # Gentle ramp up and down
        ramp_up = np.linspace(0.3, 1.0, n)
        ramp_up /= ramp_up.sum()
        seeds.append(ramp_up)
        ramp_down = ramp_up[::-1]
        seeds.append(ramp_down)

        return seeds

    # Track global best (by true evaluator)
    best_a = None
    best_val = np.inf

    # Elite set: store tuples (true_eval, a)
    elites: List[Tuple[float, np.ndarray]] = []

    # Evaluate a sequence through the evaluation function
    def eval_a(a: np.ndarray) -> float:
        return evaluate_sequence(list(a.tolist()))

    # -------------------------------------------
    # Optimization loop over seeds
    # -------------------------------------------
    for seed in seed_sequences():
        # Initialize w from seed via log
        a = seed.copy()
        w = np.log(a + 1e-12)
        m_adam = np.zeros_like(w)
        v_adam = np.zeros_like(w)
        t_adam = 0

        # Evaluate initial by true evaluator
        val = eval_a(a)
        if val < best_val:
            best_val = val
            best_a = a.copy()
        elites.append((val, a.copy()))
        elites = sorted(elites, key=lambda x: x[0])[:5]  # keep top 5 elites

        # Run continuation stages
        for stage_idx, (tau, iters, alpha_top, lambda_ent) in enumerate(
            zip(tau_schedule, iters_per_stage, alpha_top_schedule, lambda_ent_schedule)
        ):
            # Phase A: Adam updates in logit space
            last_heur_it = 0
            for it in range(iters):
                # Time check
                if time.time() - start_time > time_budget:
                    return best_a if best_a is not None else a

                # Current sequence
                a = softmax(w)

                # Compute surrogate and gradient wrt a
                F, g_a, b, weights = compute_surrogate_and_grad(
                    a, tau, alpha_top, lambda_var, lambda_tv, lambda_ent, top_k
                )

                # Convert gradient on a to gradient on w via softmax jacobian
                ga_dot_a = float(np.dot(g_a, a))
                g_w = a * (g_a - ga_dot_a)

                # Adam update
                t_adam += 1
                m_adam = beta1 * m_adam + (1.0 - beta1) * g_w
                v_adam = beta2 * v_adam + (1.0 - beta2) * (g_w * g_w)
                m_hat = m_adam / (1.0 - beta1**t_adam)
                v_hat = v_adam / (1.0 - beta2**t_adam)
                w -= lr * (m_hat / (np.sqrt(v_hat) + eps_adam))

                # Occasional mild smoothing on w to reduce tiny oscillations
                if (it + 1) % 150 == 0:
                    w = smooth_w(w)

                # Apply heuristics occasionally (Phase C)
                if (it - last_heur_it) >= heur_interval:
                    last_heur_it = it
                    a_curr = softmax(w)
                    # Try heuristic moves and accept if surrogate decreases
                    F_curr, _, _, weights_curr = compute_surrogate_and_grad(
                        a_curr, tau, alpha_top, lambda_var, lambda_tv, lambda_ent, top_k
                    )

                    # 1) Pairwise polarization
                    a_pol = pairwise_polarize(a_curr, polarize_frac)
                    F_pol, _, _, _ = compute_surrogate_and_grad(
                        a_pol, tau, alpha_top, lambda_var, lambda_tv, lambda_ent, top_k
                    )

                    if F_pol + 1e-12 < F_curr:
                        a_curr = a_pol
                        F_curr = F_pol

                    # 2) Gentle tilt
                    a_tilt = gentle_tilt(a_curr, tilt_strength)
                    F_tilt, _, _, _ = compute_surrogate_and_grad(
                        a_tilt, tau, alpha_top, lambda_var, lambda_tv, lambda_ent, top_k
                    )
                    if F_tilt + 1e-12 < F_curr:
                        a_curr = a_tilt
                        F_curr = F_tilt

                    # 3) Anti-alignment reallocation
                    a_anti = anti_alignment_reallocate(a_curr, weights_curr, anti_align_frac)
                    F_anti, _, _, _ = compute_surrogate_and_grad(
                        a_anti, tau, alpha_top, lambda_var, lambda_tv, lambda_ent, top_k
                    )
                    if F_anti + 1e-12 < F_curr:
                        a_curr = a_anti
                        F_curr = F_anti

                    # If any heuristic accepted, reset w to log(a)
                    if not np.allclose(a_curr, softmax(w)):
                        w = np.log(a_curr + 1e-12)

                # Track best solution occasionally by true evaluator
                if (it % 10) == 0:
                    a_curr = softmax(w)
                    val_curr = eval_a(a_curr)
                    if val_curr < best_val:
                        best_val = val_curr
                        best_a = a_curr.copy()
                    elites.append((val_curr, a_curr.copy()))
                    elites = sorted(elites, key=lambda x: x[0])[:5]

                # Time check
                if time.time() - start_time > time_budget:
                    return best_a if best_a is not None else a

            # Phase B: PGD on simplex (a) with backtracking
            a = softmax(w)
            F_base, g_a_base, _, weights_base = compute_surrogate_and_grad(
                a, tau, alpha_top, lambda_var, lambda_tv, lambda_ent, top_k
            )
            step = pgd_init_lr
            for pgd_step in range(pgd_steps_per_stage):
                if time.time() - start_time > time_budget:
                    return best_a if best_a is not None else a
                # Take tentative step
                descent_dir = g_a_base
                # Normalize direction to avoid overly large moves
                norm = np.linalg.norm(descent_dir) + 1e-12
                dir_unit = descent_dir / norm
                step_size = step
                accepted = False
                for _ in range(10):
                    a_new = a - step_size * dir_unit
                    a_new = proj_simplex(a_new)
                    F_new, g_a_new, _, _ = compute_surrogate_and_grad(
                        a_new, tau, alpha_top, lambda_var, lambda_tv, lambda_ent, top_k
                    )
                    if F_new + 1e-12 < F_base:
                        # Accept
                        a = a_new
                        F_base = F_new
                        g_a_base = g_a_new
                        accepted = True
                        break
                    else:
                        step_size *= backtrack_factor
                        if step_size < pgd_min_lr:
                            break
                # If no acceptance, reduce global step further
                if not accepted:
                    step *= backtrack_factor
                    if step < pgd_min_lr:
                        break
            # Sync w with a after PGD
            w = np.log(a + 1e-12)

            # True evaluator update
            val_curr = eval_a(a)
            if val_curr < best_val:
                best_val = val_curr
                best_a = a.copy()
            elites.append((val_curr, a.copy()))
            elites = sorted(elites, key=lambda x: x[0])[:5]

            # Time check
            if time.time() - start_time > time_budget:
                return best_a if best_a is not None else a

        # End of stages for this seed

    # -----------------------------
    # Final elite refinements (short, low tau)
    # -----------------------------
    # If time permits, refine elites at lower tau with brief runs
    if elites and (time.time() - start_time) < time_budget * 0.98:
        tau_refine = 0.006
        alpha_refine = 0.6
        lambda_ent_ref = 0.0
        for ev, a_init in elites:
            if time.time() - start_time > time_budget:
                break
            w = np.log(a_init + 1e-12)
            m_adam = np.zeros_like(w)
            v_adam = np.zeros_like(w)
            t_adam = 0
            for it in range(150):
                if time.time() - start_time > time_budget:
                    break
                a = softmax(w)
                F, g_a, _, _ = compute_surrogate_and_grad(
                    a, tau_refine, alpha_refine, lambda_var, lambda_tv, lambda_ent_ref, top_k
                )
                ga_dot_a = float(np.dot(g_a, a))
                g_w = a * (g_a - ga_dot_a)
                t_adam += 1
                m_adam = beta1 * m_adam + (1.0 - beta1) * g_w
                v_adam = beta2 * v_adam + (1.0 - beta2) * (g_w * g_w)
                m_hat = m_adam / (1.0 - beta1**t_adam)
                v_hat = v_adam / (1.0 - beta2**t_adam)
                w -= lr * (m_hat / (np.sqrt(v_hat) + eps_adam))
                if (it + 1) % 75 == 0:
                    w = smooth_w(w)
                if (it % 10) == 0:
                    a_curr = softmax(w)
                    val_curr = eval_a(a_curr)
                    if val_curr < best_val:
                        best_val = val_curr
                        best_a = a_curr.copy()

    # Return the best found
    return best_a


def smooth_w(w: np.ndarray) -> np.ndarray:
    """
    Apply a small 3-point moving average smoothing to parameter vector w.
    Keeps endpoints smoothed with adjacent values.
    """
    n = w.shape[0]
    if n < 3:
        return w.copy()
    w_sm = w.copy()
    w_sm[1:-1] = 0.25 * w[:-2] + 0.5 * w[1:-1] + 0.25 * w[2:]
    # Endpoints: simple average with neighbor
    w_sm[0] = 0.6 * w[0] + 0.4 * w[1]
    w_sm[-1] = 0.6 * w[-1] + 0.4 * w[-2]
    return w_sm
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
    sequence = [max(0.0, x) for x in sequence]

    # Protect against numbers that are too large
    sequence = [min(1000.0, x) for x in sequence]

    n = len(sequence)
    b_sequence = np.convolve(sequence, sequence)
    max_b = float(np.max(b_sequence))
    sum_a = float(np.sum(sequence))

    # Protect against the case where the sum is too close to zero
    if sum_a < 0.01:
        return np.inf

    return float(2.0 * n * max_b / (sum_a**2))


def run_search_for_best_sequence() -> list[float]:
    """
    Run the optimizer with a 1000-second budget and return the best sequence found.
    """
    # Leave a small margin below 1000 seconds to ensure timely return
    best_sequence = search_for_best_sequence(time_budget=990.0)
    return list(best_sequence.tolist())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```
