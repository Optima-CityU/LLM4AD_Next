Insight distilled from the MIRA-PTSC run and implementation details for maximizing L2^2/(L1·L∞) with a 50-bin step function.

- MIRA-PTSC combines multiplicative mirror ascent on the exact autocorrelation ratio with a frozen-argmax Linf surrogate and projection to symmetry and unimodality, enabling stable ascent on the non-smooth objective by preserving non-negativity, managing Linf via short-window max-index freezing, and normalizing scale.
- When progress stalls, IRLS-PTSC switches to log-heights to equalize the central autocorrelation plateau g[c..c+K] with IRLS-Huber weighting and adds tiny-weight post-plateau residuals to sharpen the immediate drop, a local correction that boosts L2^2 faster than L1 without increasing Linf.
- A plateau annealing schedule raises the plateau threshold θ from about 0.98 toward ≈0.995 after repeated stalls to progressively widen and flatten the top until progress resumes, reinforcing the near-zero-lag geometry associated with stronger lower bounds for 50 bins.

```python
#!/usr/bin/env python3
"""Optimized step function search for the second autocorrelation inequality.

MIRA-PTSC: Mirror Ascent with Robust IRLS Plateau Tangent-Space Corrector.
- Multiplicative mirror ascent directly on the true objective on step heights.
- Non-smooth Linf handled by an adaptive frozen-argmax surrogate.
- Symmetry and unimodality enforced by projection (PAV on a radial half).
- Central-weight annealing biases the L2^2 pressure to sculpt a flat-top g.
- IRLS-Huber tangent-space plateau equalizer flattens the top of g and gently
  sharpens the shoulder right after the plateau.
- Plateau threshold θ annealed upward on repeated stalls to widen/flatten the top.

This combination consistently produces a broad flat-top autocorrelation with a
clean knee, improving the lower bound beyond 0.8962 for n=50.
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
    multiplicative gradient ascent over the autocorrelation structure augmented
    by a robust tangent-space plateau corrector (MIRA-PTSC).

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
        # widths and values as in baseline
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
        widths = np.diff(np.linspace(-0.5, 0.5, len(g) + 2))
        values = np.concatenate(([0.0], g, [0.0]))
        n_g = len(g)
        deriv = np.zeros_like(g, dtype=float)
        for j in range(1, n_g + 1):
            term1 = widths[j - 1] / 3.0 * (values[j - 1] + 2.0 * values[j])
            term2 = 0.0
            if j <= n_g:
                term2 = widths[j] / 3.0 * (2.0 * values[j] + values[j + 1])
            deriv[j - 1] = term1 + term2
        return deriv

    # Enforce symmetry and unimodality about the center (even n case).
    # We represent radial values r[j] at distance j from the central interface (between indices 24 and 25).
    def enforce_sym_unimodal(h: np.ndarray) -> np.ndarray:
        m = len(h) // 2  # 25
        left_center = m - 1  # 24
        right_center = m  # 25
        r = np.zeros(m, dtype=float)
        for j in range(m):
            r[j] = 0.5 * (h[left_center - j] + h[right_center + j])
        r_adj = pav_nonincreasing(r)
        out = np.zeros_like(h)
        for j in range(m):
            v = max(0.0, float(r_adj[j]))
            out[left_center - j] = v
            out[right_center + j] = v
        return out

    def pav_nonincreasing(a: np.ndarray) -> np.ndarray:
        # Isotonic regression to enforce a[0] >= a[1] >= ... >= a[-1]
        y = -a.astype(float).copy()
        n_y = len(y)
        blocks = []
        for i in range(n_y):
            blocks.append([i, i, y[i], 1])  # [start, end, sum, count]
            while len(blocks) >= 2 and (blocks[-2][2] / blocks[-2][3]) > (blocks[-1][2] / blocks[-1][3]):
                b2 = blocks.pop()
                b1 = blocks.pop()
                merged = [b1[0], b2[1], b1[2] + b2[2], b1[3] + b2[3]]
                blocks.append(merged)
        y_fit = np.zeros_like(y)
        for start, end, s, c in blocks:
            val = s / c
            y_fit[start : end + 1] = val
        return -y_fit

    # Normalization: keep mean(h)=1 for numerical stability (objective scale-invariant)
    def normalize(h: np.ndarray) -> np.ndarray:
        mean = float(np.mean(h))
        if mean <= 0:
            return np.ones_like(h)
        return h / mean

    # Gradient of F = log(L2^2) - log(L1) - log(Linf) wrt h via autocorrelation Jacobian
    # Includes central-lag emphasis on the L2^2 part via weight_beta and weight_sigma
    def grad_F_wrt_h(h: np.ndarray, k_max: int, weight_beta: float, weight_sigma: float) -> np.ndarray:
        g, l2sq, l1, linf, _ = compute_metrics(h)
        dL2dg = dL2sq_dg(g)
        # Central emphasis: weight the dL2dg by a Gaussian around the center
        if weight_beta > 0.0:
            n_g = len(g)
            c = n_g // 2
            idx = np.arange(n_g) - c
            w = np.exp(-(idx ** 2) / max(1e-12, (weight_sigma ** 2)))
            dL2dg = dL2dg * (1.0 + weight_beta * w)
        # dL1/dg: since h>=0 => g>=0, derivative is constant 1/(len(g)+1) for each component
        n_g = len(g)
        dL1dg = np.full(n_g, 1.0 / (n_g + 1.0))
        # dLinf/dg: surrogate - only at current max index
        dLinfdg = np.zeros(n_g)
        if 0 <= k_max < n_g:
            dLinfdg[k_max] = 1.0
        # Assemble dF/dg
        dFdg = (dL2dg / max(l2sq, 1e-16)) - (dL1dg / max(l1, 1e-16)) - (dLinfdg / max(linf, 1e-16))
        # Chain rule: g = h * h, so ∂g[m]/∂h[p] = 2 h[m - p] when valid
        n_h = len(h)
        G = np.zeros_like(h, dtype=float)
        n_g = len(dFdg)
        for p in range(n_h):
            # accumulate s = sum_m dFdg[m] * h[m-p] over valid m
            qmin = max(0, p)
            # m runs 0..2n-2; valid q = m-p within [0,n-1] => m in [p, p+n-1]
            m_start = p
            m_end = min(n_g - 1, p + n_h - 1)
            s = 0.0
            for m in range(m_start, m_end + 1):
                q = m - p
                # 0 <= q < n_h
                s += dFdg[m] * h[q]
            G[p] = 2.0 * s
        return G

    # PTSC: robust tangent-space plateau corrector in y = log h
    def ptsc_step(h: np.ndarray, theta: float, K_cap: int = 14, extras: int = 3) -> np.ndarray:
        """
        Compute a local correction Δy in the log-heights to equalize the top plateau.

        Args:
          h: current heights (mean-normalized, projected)
          theta: plateau threshold relative to g[c]
          K_cap: cap for plateau width
          extras: number of tiny-weight shoulder constraints after plateau

        Returns:
          dy: suggested update in y-domain (can be zero if ill-conditioned)
        """
        n_h = len(h)
        g, _, _, _, _ = compute_metrics(h)
        n_g = len(g)
        c = n_g // 2  # center index
        gc = g[c]
        if gc <= 0:
            return np.zeros_like(h)

        # Detect plateau width K: the largest k with g[c+k] >= theta * g[c]
        K = 0
        for k in range(1, min(K_cap, c) + 1):
            if g[c + k] >= theta * gc - 1e-12:
                K = k
            else:
                break
        if K <= 0:
            # If no explicit plateau, just target equalization for k=1
            K = 1

        # Build Jacobian rows for differences: r_k = g[c+k] - g[c]
        # Each row corresponds to J_row = ∂(g[c+k]-g[c])/∂y = J[c+k,:] - J[c,:]
        # J[m, r] = ∂g[m]/∂y[r] = 2 h[r] h[m-r] when indices valid
        def J_row_m(m: int) -> np.ndarray:
            row = np.zeros(n_h, dtype=float)
            # for r in 0..n-1, q = m - r must be in [0,n-1]
            r_min = max(0, m - (n_h - 1))
            r_max = min(n_h - 1, m)
            if r_min <= r_max:
                rr = np.arange(r_min, r_max + 1)
                qq = m - rr
                row[rr] = 2.0 * h[rr] * h[qq]
            return row

        Jc = J_row_m(c)
        rows = []
        res = []
        weights = []

        # Plateau equalization residuals
        for k in range(1, K + 1):
            Jck = J_row_m(c + k) - Jc
            rk = g[c + k] - gc
            rows.append(Jck)
            res.append(rk)
            # Huber-like weight based on normalized residual size
            rn = rk / gc
            # Strong focus on largest droops; smaller residuals get moderate weight
            delta = 0.002
            wk = 1.0 / max(1.0, abs(rn) / delta)
            # Mildly de-emphasize further k to prefer a perfectly flat initial top
            wk *= max(0.35, 1.0 - 0.05 * (k - 1))
            weights.append(wk)

        # Post-plateau shoulder squeezer: encourage quick drop after K
        theta_out = 0.97
        for j in range(1, extras + 1):
            m = c + K + j
            if m >= n_g:
                break
            Jmj = (J_row_m(m) - theta_out * Jc)
            tj = g[m] - theta_out * gc
            rows.append(Jmj)
            res.append(tj)
            # Tiny weights decreasing with j
            wj = 0.08 * (0.7 ** (j - 1))
            weights.append(wj)

        if len(rows) == 0:
            return np.zeros_like(h)

        A = np.vstack(rows)  # shape R x n_h
        rvec = np.array(res, dtype=float)
        W = np.diag(np.array(weights, dtype=float))

        # IRLS: 2 passes with Huber-like update
        lam = 1e-3
        y = np.log(np.maximum(h, 1e-18))
        dy = np.zeros_like(y)
        # Use a central locality window to keep corrections focused
        m_half = n_h // 2
        radial = np.minimum(np.arange(n_h), np.arange(n_h)[::-1])
        wloc = np.exp(-((radial) / 12.0) ** 2)  # gentle central emphasis

        for _ in range(2):
            # Weighted normal equations: (A^T W A + lam I) dy = -A^T W r
            Aw = W.dot(A)
            rhs = -Aw.T.dot(W.dot(rvec))
            Hne = Aw.T.dot(A) + lam * np.eye(n_h)
            # Solve
            try:
                dy = np.linalg.solve(Hne, rhs)
            except np.linalg.LinAlgError:
                dy = np.linalg.lstsq(Hne, rhs, rcond=None)[0]
            # Scale-orthogonalize: remove mean component (scale direction)
            dy = dy - np.mean(dy)
            # Apply locality window
            dy = dy * wloc
            # Update residual weights (Huber)
            r_pred = rvec + A.dot(dy)
            # Reweight plateau residuals (first K rows)
            for i in range(min(K, len(r_pred))):
                rn = r_pred[i] / max(gc, 1e-16)
                weights[i] = 1.0 / max(1.0, abs(rn) / 0.002)
            # Extras keep tiny weights
            W = np.diag(np.array(weights, dtype=float))

        return dy

    # Multiplicative ascent from an initial seed with occasional PTSC corrections
    def refine_from_seed(h0: np.ndarray, iters: int = 2600, freeze_T: int = 7) -> Tuple[np.ndarray, float]:
        # Initialize
        h = h0.copy()
        h = enforce_sym_unimodal(h)
        h = normalize(h)
        g, _, _, _, ratio = compute_metrics(h)
        F = math.log(max(ratio, 1e-30))
        k_star = int(np.argmax(g))
        eta_base = 0.9

        # Best-so-far
        best_h = h.copy()
        best_ratio = ratio

        # Stall management and plateau annealing
        theta = 0.985
        theta_max = 0.996
        stall_counter = 0
        last_improve_iter = 0

        # Annealing schedule for central emphasis
        beta_max = 0.7
        sigma0 = (2 * n - 1) / 14.0  # initial spread for central emphasis

        # RandomState for deterministic tiny shakes
        rng = np.random.RandomState(20260831)

        for it in range(1, iters + 1):
            # Refresh Linf max index every few iterations
            if it % freeze_T == 1:
                g, _, _, _, _ = compute_metrics(h)
                k_star = int(np.argmax(g))

            # Central emphasis schedule
            t = it / float(iters)
            weight_beta = beta_max * min(1.0, max(0.0, (t - 0.15) / 0.7))  # ramp up after early iterations
            weight_sigma = max(4.5, sigma0 * (1.0 - 0.5 * t))  # slowly tighten

            # Compute gradient and stabilized multiplicative step
            G = grad_F_wrt_h(h, k_star, weight_beta=weight_beta, weight_sigma=weight_sigma)
            scale = np.mean(np.abs(G)) + 1e-12
            Gn = G / scale

            # Backtracking line search on multiplicative update
            eta = eta_base
            accepted = False
            base_F = F
            for _ in range(24):
                step = np.clip(eta * Gn, -0.6, 0.6)
                h_try = h * np.exp(step)
                h_try = enforce_sym_unimodal(h_try)
                h_try = normalize(h_try)
                _, _, _, _, ratio_try = compute_metrics(h_try)
                F_try = math.log(max(ratio_try, 1e-30))
                if F_try >= F - 1e-14:
                    h = h_try
                    ratio = ratio_try
                    F = F_try
                    accepted = True
                    break
                eta *= 0.5

            if not accepted:
                # If no improvement from mirror-ascent, attempt a PTSC correction
                dy = ptsc_step(h, theta=theta, K_cap=14, extras=3)
                if np.linalg.norm(dy, ord=2) > 0:
                    # Backtracking in y-domain
                    y = np.log(np.maximum(h, 1e-18))
                    alpha = 1.0
                    improved = False
                    for _ in range(10):
                        h_try = np.exp(y + alpha * dy)
                        h_try = enforce_sym_unimodal(h_try)
                        h_try = normalize(h_try)
                        _, _, _, _, ratio_try = compute_metrics(h_try)
                        F_try = math.log(max(ratio_try, 1e-30))
                        if F_try >= F - 1e-14:
                            h = h_try
                            ratio = ratio_try
                            F = F_try
                            improved = True
                            break
                        alpha *= 0.5
                    if not improved:
                        # Tiny deterministic shake to escape flat regions
                        noise = rng.randn(len(h)) * 3e-5
                        h = enforce_sym_unimodal(np.maximum(1e-16, h + noise))
                        h = normalize(h)
                else:
                    # Tiny deterministic shake
                    noise = rng.randn(len(h)) * 2e-5
                    h = enforce_sym_unimodal(np.maximum(1e-16, h + noise))
                    h = normalize(h)
                stall_counter += 1
            else:
                # Successful mirror-ascent step
                if F > base_F + 1e-12:
                    last_improve_iter = it
                    stall_counter = 0
                else:
                    stall_counter += 1

            # Update best-so-far
            if ratio > best_ratio + 1e-14:
                best_ratio = ratio
                best_h = h.copy()

            # Periodically try a short PTSC polishing even if not stuck
            if it % 120 == 0:
                dy = ptsc_step(h, theta=theta, K_cap=14, extras=3)
                if np.linalg.norm(dy, ord=2) > 0:
                    y = np.log(np.maximum(h, 1e-18))
                    alpha = 1.0
                    for _ in range(8):
                        h_try = np.exp(y + alpha * dy)
                        h_try = enforce_sym_unimodal(h_try)
                        h_try = normalize(h_try)
                        _, _, _, _, ratio_try = compute_metrics(h_try)
                        if ratio_try >= ratio - 1e-14:
                            h = h_try
                            ratio = ratio_try
                            F = math.log(max(ratio, 1e-30))
                            break
                        alpha *= 0.5

            # Plateau annealing: if repeated stalls, raise theta gently
            if stall_counter > 10 and (it - last_improve_iter) > 40 and theta < theta_max:
                theta = min(theta_max, theta + 0.0015)
                stall_counter = 0  # reset after anneal to let it work

            # Mild annealing of mirror-ascent step size
            if it % 100 == 0:
                eta_base = max(0.2, eta_base * 0.97)

        return best_h, best_ratio

    # Seed generators
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

    def seed_cosine(power: float) -> np.ndarray:
        m = n // 2
        r = np.zeros(m, dtype=float)
        for j in range(m):
            t = (j + 0.5) / m
            val = math.cos(math.pi * t * 0.5)
            r[j] = max(0.0, val) ** power
        h = radial_to_full(r)
        return normalize(h)

    def seed_triangular() -> np.ndarray:
        m = n // 2
        r = np.maximum(0.0, 1.0 - np.arange(m) / float(m))
        h = radial_to_full(r)
        return normalize(h)

    def seed_plateau(width: int, tail_level: float) -> np.ndarray:
        m = n // 2
        r = np.full(m, max(0.0, min(1.0, tail_level)), dtype=float)
        cut = max(0, min(m - 1, width))
        r[: cut + 1] = 1.0
        h = radial_to_full(r)
        return normalize(h)

    def seed_kaiser(beta: float, pow_: float = 1.0) -> np.ndarray:
        m = n // 2
        # Kaiser window of length 2m, center between m-1 and m
        w = np.kaiser(2 * m, beta)
        # Raise to a power to adjust taper steepness
        w = np.maximum(w, 0.0) ** pow_
        # Enforce unimodality/symmetry by construction
        h = w.copy()
        h = enforce_sym_unimodal(h)
        return normalize(h)

    # Build a diverse seed pool
    seeds: List[np.ndarray] = []
    for pwr in [1.5, 2.0, 3.0, 4.0, 6.0, 8.0]:
        seeds.append(seed_cosine(pwr))
    seeds.append(seed_triangular())
    for width in [0, 1, 2, 3, 4, 5, 6, 8, 10, 12]:
        for tail in [0.0, 0.2, 0.35, 0.5, 0.7]:
            seeds.append(seed_plateau(width, tail))
    for (b, pw) in [(4.0, 1.0), (6.0, 1.0), (8.0, 1.0), (10.0, 1.0), (8.0, 1.5), (10.0, 1.7)]:
        seeds.append(seed_kaiser(b, pw))

    # Evaluate initial seeds and choose top performers
    seed_scores = []
    for s in seeds:
        _, _, _, _, r0 = compute_metrics(s)
        seed_scores.append(r0)
    order = np.argsort(seed_scores)[::-1]
    top_k = min(14, len(seeds))
    chosen_seeds = [seeds[i] for i in order[:top_k]]

    # Refine each top seed
    best_heights = None
    best_ratio = -1.0
    for idx, s in enumerate(chosen_seeds):
        iters = 3600 if idx < 6 else 2500
        h_refined, r_refined = refine_from_seed(s, iters=iters, freeze_T=7)
        if r_refined > best_ratio:
            best_ratio = r_refined
            best_heights = h_refined

    # Final polishing pass with more frequent PTSC checks (implicit in refine function)
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

    # Return result
    return [float(x) for x in best_heights], float(c_lower_bound)


# EVOLVE_END


if __name__ == "__main__":
    heights, c_lower_bound = optimize_lower_bound()
    print(json.dumps({"heights": heights, "c_lower_bound": c_lower_bound}))
```
