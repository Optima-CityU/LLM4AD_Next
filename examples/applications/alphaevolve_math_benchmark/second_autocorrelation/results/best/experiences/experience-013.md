Insight on replacing the one-hot Linf gradient with a softmax (log-sum-exp) surrogate and annealing its sharpness to handle the max term in g = h ∗ h, as implemented in solve.py and evaluated in Generation 9.

- Softmax-Surrogate Linf Gradient with Annealed Sharpness: When optimizing the non-smooth Linf max in the autocorrelation objective g = h ∗ h, replacing the one-hot Linf gradient with soft weights w_k = exp(α (g_k − g_max)) / Σ_j exp(α (g_j − g_max)) and annealing α over iterations (e.g., starting near α0 ≈ 12 and increasing toward α_max ≈ 80 with small steps) spreads gradient across near-maximum bins to form flat-top plateaus, reduces jitter from argmax oscillations, and stabilizes multiplicative updates; in Generation 9 this configuration completed stably (validity 1.0) with target_ratio 0.8073832881504286 and c_lower_bound 0.7236576411692291, indicating this surrogate-plus-annealing pattern is a reusable strategy whenever max-driven terms make gradients brittle.

```python
#!/usr/bin/env python3
"""Optimized step function search for the second autocorrelation inequality.

This implements a structured multiplicative ascent tailored to the discrete
autocorrelation geometry of step functions. It enforces symmetry and unimodality,
and handles the non-smooth Linf term via a smooth log-sum-exp (softmax) surrogate
with annealed sharpness for stability and better plateau formation.
"""

import json
import math
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def optimize_lower_bound():
    """
    Search for a nonnegative, symmetric, unimodal height vector of length 50
    that maximizes the ratio L2^2(f*f) / (L1(f*f) * Linf(f*f)), using a
    multiplicative gradient ascent over the autocorrelation structure.

    The key enhancement is a smooth surrogate for the Linf term based on a
    softmax (log-sum-exp) approximation with annealed sharpness, replacing
    the previous one-hot gradient. This distributes gradient signal across
    near-maximum bins of g = h * h, encouraging flat-top plateaus that
    empirically improve the target ratio.

    Returns:
        heights: list of 50 nonnegative floats (symmetric, unimodal)
        c_lower_bound: float, ratio L2^2 / (L1 * Linf)
    """
    n = 50

    # Core objective computation consistent with the project's verification
    def compute_metrics(h: np.ndarray) -> Tuple[np.ndarray, float, float, float, float]:
        """
        Given height vector h (length n), compute:
          - convolution g = h * h (length 2n - 1)
          - L2^2 using piecewise-linear trapezoid-like integration
          - L1 and Linf consistent with baseline
          - ratio = L2^2 / (L1 * Linf)
        """
        g = np.convolve(h, h)  # length 2n-1
        # widths and values as in baseline (uniform partition of [-0.5, 0.5])
        widths = np.diff(np.linspace(-0.5, 0.5, len(g) + 2))  # length len(g)+1
        values = np.concatenate(([0.0], g, [0.0]))  # length len(g)+2 = 2n+1
        # L2^2 via quadratic over piecewise linear segments
        l2_squared = 0.0
        for i in range(len(g) + 1):
            v_i = values[i]
            v_ip1 = values[i + 1]
            l2_squared += widths[i] / 3.0 * (v_i * v_i + v_i * v_ip1 + v_ip1 * v_ip1)
        # L1 and Linf consistent with baseline
        l1 = float(np.sum(np.abs(g)) / (len(g) + 1))
        linf = float(np.max(np.abs(g)))
        ratio = float(l2_squared / (l1 * linf))
        return g, float(l2_squared), l1, linf, ratio

    # Derivative of L2^2 wrt g_k under the trapezoid-like integration scheme
    def dL2sq_dg(g: np.ndarray) -> np.ndarray:
        # widths are uniform but we retain the general formula
        widths = np.diff(np.linspace(-0.5, 0.5, len(g) + 2))
        values = np.concatenate(([0.0], g, [0.0]))
        # Derivative wrt g_k corresponds to derivative wrt values[j] with j = k+1
        n_g = len(g)
        deriv = np.zeros_like(g, dtype=float)
        # For j from 1..n_g, corresponding to k = j-1
        for j in range(1, n_g + 1):
            # Contribution from segment i = j-1 (as v_{i+1} = v_j)
            term1 = widths[j - 1] / 3.0 * (values[j - 1] + 2.0 * values[j])
            # Contribution from segment i = j (as v_i = v_j), guarded for i <= n_g
            term2 = 0.0
            if j <= n_g:
                term2 = widths[j] / 3.0 * (2.0 * values[j] + values[j + 1])
            deriv[j - 1] = term1 + term2
        return deriv

    # Gradient of F = log(L2^2) - log(L1) - log(Linf) wrt h via autocorrelation Jacobian
    # Softmax-based Linf surrogate with annealed alpha (>0)
    def grad_F_wrt_h(h: np.ndarray, alpha: float) -> np.ndarray:
        """
        Compute gradient of F wrt h using:
          dF/dg = (1/l2sq) dL2^2/dg - (1/l1) dL1/dg - (1/linf) w,
        where w is the softmax-based surrogate weights over g:
          w_k = exp(alpha * (g_k - g_max)) / sum_j exp(alpha * (g_j - g_max)).
        """
        # Compute base quantities
        g, l2sq, l1, linf, _ = compute_metrics(h)

        # dL2^2/dg
        dL2dg = dL2sq_dg(g)

        # dL1/dg: since h>=0 => g>=0, derivative is constant 1/(len(g)+1) for each component
        n_g = len(g)
        dL1dg = np.full(n_g, 1.0 / (n_g + 1.0))

        # Softmax surrogate for dLinf/dg
        # Stabilize by subtracting max to avoid overflow; note g_k - g_max <= 0
        g_max = float(np.max(g))
        diffs = g - g_max
        # Clip alpha to positive range
        alpha_eff = max(1e-6, float(alpha))
        # Compute soft weights; exps <= 1 due to diffs <= 0
        z = np.exp(alpha_eff * diffs)
        z_sum = float(np.sum(z)) + 1e-300
        w = z / z_sum  # sums to 1.0

        # Assemble dF/dg
        dFdg = (dL2dg / max(l2sq, 1e-16)) - (dL1dg / max(l1, 1e-16)) - (w / max(linf, 1e-16))

        # Chain rule: g = h * h, so d g[m]/d h[p] = 2 h[m - p] when valid
        n_h = len(h)
        G = np.zeros_like(h, dtype=float)
        # O(n^2) accumulation
        for p in range(n_h):
            s = 0.0
            # m in [0, 2n-2], q = m - p in [0, n-1]
            for m in range(n_g):
                q = m - p
                if 0 <= q < n_h:
                    s += dFdg[m] * h[q]
            G[p] = 2.0 * s
        return G

    # Enforce symmetry and unimodality about the center (even n case).
    # We represent radial values r[j] at distance j from the central interface (between indices 24 and 25).
    def enforce_sym_unimodal(h: np.ndarray) -> np.ndarray:
        m = len(h) // 2  # 25
        left_center = m - 1  # 24
        right_center = m  # 25
        # Build radial average profile r[j] = avg(h[24-j], h[25+j]) for j=0..24
        r = np.zeros(m, dtype=float)
        for j in range(m):
            r[j] = 0.5 * (h[left_center - j] + h[right_center + j])
        # Enforce nonincreasing r[j] in j via pool-adjacent-violators (isotonic regression)
        # We want r[0] >= r[1] >= ... >= r[24]
        r_adj = pav_nonincreasing(r)
        # Reconstruct symmetric unimodal h
        out = np.zeros_like(h)
        for j in range(m):
            v = max(0.0, float(r_adj[j]))
            out[left_center - j] = v
            out[right_center + j] = v
        return out

    def pav_nonincreasing(a: np.ndarray) -> np.ndarray:
        # Pool adjacent violators to enforce a[0] >= a[1] >= ... >= a[-1]
        # Implement by applying isotonic regression on -a in nondecreasing order.
        y = -a.astype(float).copy()
        n_y = len(y)
        # Blocks with cumulative sums
        blocks = []
        for i in range(n_y):
            # Start new block
            blocks.append([i, i, y[i], 1])  # [start, end, sum, count]
            # Merge while decreasing constraint violated (we need nondecreasing in y)
            while len(blocks) >= 2 and (blocks[-2][2] / blocks[-2][3]) > (blocks[-1][2] / blocks[-1][3]):
                b2 = blocks.pop()
                b1 = blocks.pop()
                merged = [b1[0], b2[1], b1[2] + b2[2], b1[3] + b2[3]]
                blocks.append(merged)
        # Unpack to array
        y_fit = np.zeros_like(y)
        for start, end, s, c in blocks:
            val = s / c
            y_fit[start : end + 1] = val
        # Return -y_fit to get nonincreasing a
        return -y_fit

    # Normalization: keep mean(h)=1 for numerical stability (objective scale-invariant)
    def normalize(h: np.ndarray) -> np.ndarray:
        mean = float(np.mean(h))
        if mean <= 0:
            return np.ones_like(h)
        return h / mean

    # Multiplicative ascent from an initial seed with annealed softmax sharpness
    def refine_from_seed(h0: np.ndarray, iters: int = 2500, freeze_T: int = 7) -> Tuple[np.ndarray, float]:
        h = h0.copy()
        h = enforce_sym_unimodal(h)
        h = normalize(h)
        # Initial metrics
        g, l2sq, l1, linf, ratio = compute_metrics(h)
        F = math.log(max(ratio, 1e-30))
        # Track current max index (not strictly needed with softmax, but kept for diagnostics/stability)
        k_star = int(np.argmax(g))

        # Step size parameters
        eta_base = 0.9
        best_h = h.copy()
        best_ratio = ratio
        no_improve_count = 0

        # Softmax sharpness schedule: start smooth, anneal towards hard
        alpha0 = 12.0
        alpha_step = 0.025
        alpha_max = 80.0

        for it in range(1, iters + 1):
            # Periodically refresh k_star (kept for optional diagnostics)
            if it % freeze_T == 1:
                g, _, _, _, _ = compute_metrics(h)
                k_star = int(np.argmax(g))  # not used directly by gradient anymore

            # Compute current alpha via annealed schedule
            alpha = min(alpha_max, alpha0 + it * alpha_step)

            # Compute gradient with softmax Linf surrogate
            G = grad_F_wrt_h(h, alpha=alpha)

            # Stabilize gradient scale
            scale = np.mean(np.abs(G)) + 1e-12
            Gn = G / scale

            # Backtracking line search on multiplicative step
            eta = eta_base
            accepted = False
            for _ in range(20):
                # Exponentiated update preserves positivity; clamp to avoid overshoot
                step = np.clip(eta * Gn, -0.6, 0.6)
                h_try = h * np.exp(step)
                # Project to symmetric unimodal and normalize
                h_try = enforce_sym_unimodal(h_try)
                h_try = normalize(h_try)
                # Evaluate
                _, _, _, _, ratio_try = compute_metrics(h_try)
                F_try = math.log(max(ratio_try, 1e-30))
                if F_try >= F - 1e-12:
                    h = h_try
                    ratio = ratio_try
                    F = F_try
                    accepted = True
                    break
                else:
                    eta *= 0.5
            if not accepted:
                # If we cannot improve, damp and add a tiny symmetric noise to escape flats
                rng = np.random.RandomState(1234 + it)
                noise = (rng.randn(len(h)) * 1e-4)
                h = enforce_sym_unimodal(np.maximum(1e-12, h + noise))
                h = normalize(h)
                no_improve_count += 1
            else:
                no_improve_count = 0

            # Update best-so-far
            if ratio > best_ratio + 1e-14:
                best_ratio = ratio
                best_h = h.copy()

            # Slightly reduce eta_base over time (annealing)
            if it % 100 == 0:
                eta_base = max(0.2, eta_base * 0.97)

            # Early stopping if stuck
            if no_improve_count > 100:
                break

        return best_h, best_ratio

    # Construct a variety of structured seeds
    def seed_cosine(power: float) -> np.ndarray:
        # Cosine bell on radial index j=0..24 (center outward), raised to power
        m = n // 2
        r = np.zeros(m, dtype=float)
        # sample midpoints in (0,1): t = (j+0.5)/m
        for j in range(m):
            t = (j + 0.5) / m  # in (0,1)
            val = math.cos(math.pi * t * 0.5)  # cos from center to boundary
            r[j] = max(0.0, val) ** power
        h = radial_to_full(r)
        return normalize(h)

    def seed_triangular() -> np.ndarray:
        m = n // 2
        r = np.maximum(0.0, 1.0 - np.arange(m) / float(m))
        h = radial_to_full(r)
        return normalize(h)

    def seed_plateau(width: int, tail_level: float) -> np.ndarray:
        # width: number of radial rings (j from 0) included in high plateau
        # tail_level in [0,1], rest of rings set to this level
        m = n // 2
        r = np.full(m, max(0.0, min(1.0, tail_level)), dtype=float)
        cut = max(0, min(m - 1, width))
        r[: cut + 1] = 1.0
        h = radial_to_full(r)
        return normalize(h)

    def seed_quadratic() -> np.ndarray:
        # Inverted quadratic profile: r[j] ~ (1 - (j/m)^2)+
        m = n // 2
        j = np.arange(m, dtype=float)
        r = np.maximum(0.0, 1.0 - (j / m) ** 2)
        h = radial_to_full(r)
        return normalize(h)

    def radial_to_full(r: np.ndarray) -> np.ndarray:
        # Given radial profile r[0..24], reconstruct h (length 50) symmetric about center
        m = len(r)  # 25
        left_center = m - 1
        right_center = m
        h = np.zeros(2 * m, dtype=float)
        for j in range(m):
            v = max(0.0, float(r[j]))
            h[left_center - j] = v
            h[right_center + j] = v
        return h

    # Create seed pool
    seeds: List[np.ndarray] = []

    # Cosine-bell seeds with various powers
    for pwr in [1.5, 2.0, 3.0, 4.0, 6.0]:
        seeds.append(seed_cosine(pwr))

    # Triangular and quadratic seeds
    seeds.append(seed_triangular())
    seeds.append(seed_quadratic())

    # Plateau seeds with different widths and tail levels
    for width in [0, 1, 2, 3, 4, 5, 6, 8, 10, 12]:
        for tail in [0.0, 0.2, 0.4, 0.6, 0.8]:
            seeds.append(seed_plateau(width, tail))

    # Evaluate all seeds and sort by initial ratio
    seed_scores = []
    for s in seeds:
        _, _, _, _, r0 = compute_metrics(s)
        seed_scores.append(r0)
    # Choose top seeds
    order = np.argsort(seed_scores)[::-1]
    top_k = min(12, len(seeds))
    chosen_seeds = [seeds[i] for i in order[:top_k]]

    # Refine each top seed
    best_heights = None
    best_ratio = -1.0
    for idx, s in enumerate(chosen_seeds):
        # More iterations for the best few initial seeds
        iters = 3600 if idx < 5 else 2400
        h_refined, r_refined = refine_from_seed(s, iters=iters, freeze_T=7)
        if r_refined > best_ratio:
            best_ratio = r_refined
            best_heights = h_refined

    # As a final polishing step, run a short refinement from the best result with tighter refresh
    best_heights, best_ratio = refine_from_seed(best_heights, iters=1400, freeze_T=5)

    # Compute final c_lower_bound consistent with baseline
    g_final = np.convolve(best_heights, best_heights)
    widths = np.diff(np.linspace(-0.5, 0.5, len(g_final) + 2))
    values = np.concatenate(([0.0], g_final, [0.0]))
    l2_squared = sum(
        widths[index] / 3.0 * (values[index] ** 2 + values[index] * values[index + 1] + values[index + 1] ** 2)
        for index in range(len(g_final) + 1)
    )
    l1 = float(np.sum(np.abs(g_final)) / (len(g_final) + 1))
    linf = float(np.max(np.abs(g_final)))
    c_lower_bound = float(l2_squared / (l1 * linf))

    # Return in the required format: list and float
    return [float(x) for x in best_heights], float(c_lower_bound)


# EVOLVE_END


if __name__ == "__main__":
    heights, c_lower_bound = optimize_lower_bound()
    print(json.dumps({"heights": heights, "c_lower_bound": c_lower_bound}))
```
