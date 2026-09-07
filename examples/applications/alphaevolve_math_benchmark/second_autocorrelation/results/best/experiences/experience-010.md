Key design choices in TEDS that improved plateau flattening under symmetry/monotonicity constraints, supported by an observed target_ratio of 0.8073832880433902 and validity 1.0.

- Softmax-L∞ Toeplitz Jacobian Descent (STJ): STJ replaces heuristic influence weights with an exact local Toeplitz Jacobian for the convolution and drives mass-neutral, monotone updates via a softmax-based Linf surrogate with adaptive τ in the range [0.015, 0.04], projecting through PAV and using a short backtracking line search to accept only updates that increase the true ratio.
- Dual-Window Plateau Equalization (CPE-Exact++): CPE-Exact++ equalizes a central plateau window of size Kc=7 and a shoulder ring of size Ks=4 using Lc as the median in the center and Ls as the mean on the shoulder with Ls ≤ Lc, computes influences via the same Toeplitz Jacobian, solves a small constrained least squares, and applies mass-preserving PAV-projected updates accepted only when the exact ratio improves.
- TEDS integration strategy: The algorithm inserts STJ between K-SMT and CPE-Exact, replaces approximate influence weights with Toeplitz-exact Jacobians for the chosen windows, and gates CCR based on whether Linf increased, producing targeted flattening of the convolution peak that lowers Linf while maintaining or lifting the L2 term.
- TEDS performance metrics: In the reported run, TEDS achieved target_ratio 0.8073832880433902 with validity 1.0, c_lower_bound 0.7236576410732907, and eval_time 739.5040860299487, supporting the effectiveness of the exact-Jacobian, softmax-Linf guided plateau strategy.
- Action for future designs: When optimizing monotone symmetric step-function autocorrelation ratios, reuse an STJ step on a small peak-centered window (e.g., r ∈ {c−6,…,c+6}) with an exact Toeplitz Jacobian and a softmax-Linf surrogate (τ≈0.02 adaptively within 0.015–0.04), enforce mass neutrality via PAV, and accept updates via a brief line search to reliably reduce Linf while preserving L2.

```python
#!/usr/bin/env python3
"""Structure-aware optimizer for the second autocorrelation inequality.

This implements a deterministic, structure-aware optimizer that
constructs a nonnegative, symmetric, monotone step function f
on 50 equally spaced intervals over [-1/4, 1/4], tailored to
maximize the ratio

    R = ||f * f||_2^2 / ( ||f * f||_1 * ||f * f||_∞ )

under the convolution structure of a piecewise-constant f.

Key features:
- Symmetric, unimodal (monotone from the center outward) step function.
- Exact evaluation for the L2 term of the convolution via piecewise linear integration.
- Sensitivity-guided prioritized redistribution over tri-scale distances.
- Ridge flattening with extended active set and soft shoulder taper.
- Tail sparsification with sensitivity-weighted redistribution.
- Coarse-to-fine seeding strategy with plateau+taper templates.

The function optimize_lower_bound returns a heights list of length 50 and the
corresponding lower bound estimate as a float. The heights satisfy the expected
constraints and are designed to pass verification checks.

Note:
- We do not modify external cal_lower_bound or verify_heights_sequence; the
  internal objective here mirrors the baseline structure (piecewise linear model).
"""

import json
import math
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def optimize_lower_bound() -> Tuple[List[float], float]:
    """
    Optimize a symmetric, monotone, nonnegative step function (50 bins)
    to maximize the autocorrelation ratio R. Returns:
    - heights: list of 50 floats (nonnegative, symmetric, monotone), normalized sum=50
    - c_lower_bound: float ratio computed with the same formula structure as the baseline
    """

    n = 50  # number of bins
    half_len = n // 2  # 25
    assert half_len * 2 == n

    # Core objective computation consistent with baseline logic:
    # - Use discrete convolution of heights with itself.
    # - Treat g(t) as piecewise linear between uniform grid nodes with zero endpoints.
    # - L2^2 via exact integral for linear segments: sum w/3*(v_i^2 + v_i v_{i+1} + v_{i+1}^^2)
    # - L1 via trapezoid reduces to width * sum of internal node values because endpoints are zero.
    # - Linf via max node value.
    def compute_ratio(heights: np.ndarray) -> float:
        conv = np.convolve(heights, heights)  # node values on inner grid (length 2n-1)
        widths = np.diff(np.linspace(-0.5, 0.5, len(conv) + 2))  # uniform
        values = np.concatenate(([0.0], conv.astype(float), [0.0]))
        l2_sq = 0.0
        for i in range(len(conv) + 1):
            vi = values[i]
            vj = values[i + 1]
            w = widths[i]
            l2_sq += w / 3.0 * (vi * vi + vi * vj + vj * vj)
        l1 = float(np.sum(np.abs(conv)) / (len(conv) + 1))
        linf = float(np.max(np.abs(conv)))
        if l1 <= 0.0 or linf <= 0.0:
            return 0.0
        return float(l2_sq / (l1 * linf))

    # Build full heights from half-sequence (nonincreasing outward)
    def build_full_from_half(a_half: np.ndarray) -> np.ndarray:
        left = a_half[1:][::-1]  # 24 values for indices 0..23
        center_two = np.array([a_half[0], a_half[0]])  # indices 24 and 25
        right = a_half[1:]  # 24 values for indices 26..49
        return np.concatenate([left, center_two, right])

    # Normalize heights to have sum = n (scale-invariant but keeps consistency)
    def normalize_to_sum_n(h: np.ndarray) -> np.ndarray:
        s = np.sum(h)
        if s <= 0:
            return np.ones_like(h)
        return h * (n / s)

    # Projection: enforce monotone nonincreasing for the half-sequence via PAV
    # and nonnegativity. a_half length is 25.
    def project_monotone_nonincreasing(a_half: np.ndarray) -> np.ndarray:
        a = a_half.copy()
        a[a < 0] = 0.0
        starts = list(range(len(a)))
        ends = list(range(len(a)))
        vals = a.tolist()
        k = 0
        while k < len(vals) - 1:
            if vals[k] < vals[k + 1]:
                total_len = (ends[k] - starts[k] + 1) + (ends[k + 1] - starts[k + 1] + 1)
                new_val = (vals[k] * (ends[k] - starts[k] + 1) + vals[k + 1] * (ends[k + 1] - starts[k + 1] + 1)) / total_len
                vals[k] = new_val
                ends[k] = ends[k + 1]
                del vals[k + 1]
                del starts[k + 1]
                del ends[k + 1]
                if k > 0:
                    k -= 1
            else:
                k += 1
        a_proj = np.zeros_like(a)
        for b in range(len(vals)):
            a_proj[starts[b] : ends[b] + 1] = vals[b]
        a_proj[a_proj < 0] = 0.0
        return a_proj

    # Construct initial seeds: plateau + geometric taper templates, plus a few simple shapes.
    def make_seed_half(P: int, r: float) -> np.ndarray:
        a = np.zeros(half_len, dtype=float)
        for k in range(half_len):
            if k < P:
                a[k] = 1.0
            else:
                a[k] = (r ** (k - P + 1))
        return a

    def make_linear_taper_half(width: int) -> np.ndarray:
        a = np.zeros(half_len, dtype=float)
        for k in range(half_len):
            val = max(0.0, 1.0 - k / max(1, width))
            a[k] = val
        return a

    # Generate initial seeds
    seeds = []
    r_grid = [0.70, 0.75, 0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98]
    for P in range(1, 13):
        for r in r_grid:
            seeds.append(make_seed_half(P, r))
    for W in [4, 6, 8, 10, 12, 16]:
        seeds.append(make_linear_taper_half(W))
    seeds.append(np.ones(half_len, dtype=float))  # flat seed

    def seed_to_full(hhalf: np.ndarray) -> np.ndarray:
        hhalf = project_monotone_nonincreasing(hhalf)
        full = build_full_from_half(hhalf)
        return normalize_to_sum_n(full)

    # Evaluate seeds and keep the top K
    seed_pairs = []
    for a in seeds:
        h = seed_to_full(a)
        seed_pairs.append((h, compute_ratio(h)))
    seed_pairs.sort(key=lambda t: t[1], reverse=True)
    topK = min(16, len(seed_pairs))
    seed_pairs = seed_pairs[:topK]

    # Sensitivity weights: quantify influence of half indices on central conv nodes
    # We compute influence weights for each half index i by summing the contributions
    # to the top convolution nodes (around the peak) via linearized derivative.
    def compute_influence_weights(a_half: np.ndarray) -> np.ndarray:
        h = build_full_from_half(a_half)
        h = normalize_to_sum_n(h)
        conv = np.convolve(h, h)
        # Identify central peak nodes: take argmax index and include ±3 neighbors
        c_idx = int(np.argmax(conv))
        # Clamp window within [0, len(conv)-1]
        r_idxs = list(range(max(0, c_idx - 3), min(len(conv), c_idx + 4)))
        # Map half index i to two full positions p1 and p2
        weights = np.zeros_like(a_half)
        for i in range(len(a_half)):
            if i == 0:
                positions = [24, 25]
            else:
                positions = [24 - i, 25 + i]
            w_sum = 0.0
            for r in r_idxs:
                for p in positions:
                    q = r - p
                    if 0 <= q < len(h):
                        w_sum += h[q]
            weights[i] = w_sum
        # Normalize weights to sum to 1 for stability; add epsilon to avoid zeros
        total = float(np.sum(weights))
        if total <= 0:
            return np.ones_like(weights)
        weights = weights / total
        return weights

    # Golden-section line search helper for a single nonadjacent pair (j, k)
    # Move mass δ from k (tail) to j (center), δ in [0, UB].
    def line_search_pair_triscale(a_half: np.ndarray, j: int, k: int, current_ratio: float, max_iter: int = 16) -> Tuple[float, float]:
        # Feasible UB
        UB = float("inf")
        # Ensure a[j-1] >= a[j] + δ
        if j > 0:
            UB = min(UB, a_half[j - 1] - a_half[j])
        # Ensure a[k] - δ >= a[k+1]
        if k + 1 < len(a_half):
            UB = min(UB, a_half[k] - a_half[k + 1])
        else:
            UB = min(UB, a_half[k])
        if not (UB > 1e-18):
            return 0.0, current_ratio

        # Golden section in [0, UB]
        aL = 0.0
        aR = UB
        phi = (1 + math.sqrt(5)) / 2.0
        invphi = 1.0 / phi
        x1 = aR - (aR - aL) * invphi
        x2 = aL + (aR - aL) * invphi

        def eval_delta(d: float) -> float:
            b = a_half.copy()
            b[j] += d
            b[k] -= d
            # Feasibility ensured by bounds; normalize and evaluate
            b = project_monotone_nonincreasing(b)
            h = build_full_from_half(b)
            h = normalize_to_sum_n(h)
            return compute_ratio(h)

        r1 = eval_delta(x1)
        r2 = eval_delta(x2)
        best_r = current_ratio
        best_d = 0.0
        if r1 > best_r:
            best_r = r1
            best_d = x1
        if r2 > best_r:
            best_r = r2
            best_d = x2

        for _ in range(max_iter):
            if (aR - aL) <= 1e-12:
                break
            if r1 < r2:
                aL = x1
                x1 = x2
                r1 = r2
                x2 = aL + (aR - aL) * invphi
                r2 = eval_delta(x2)
                if r2 > best_r:
                    best_r = r2
                    best_d = x2
            else:
                aR = x2
                x2 = x1
                r2 = r1
                x1 = aR - (aR - aL) * invphi
                r1 = eval_delta(x1)
                if r1 > best_r:
                    best_r = r1
                    best_d = x1

        # Endpoints
        rL = eval_delta(0.0)
        if rL > best_r:
            best_r = rL
            best_d = 0.0
        rU = eval_delta(UB)
        if rU > best_r:
            best_r = rU
            best_d = UB
        return best_d, best_r

    # Adjacent pair line search allowing both directions (fallback fine-tune)
    def line_search_pair_adjacent(a_half: np.ndarray, j: int, ratio_current: float, max_iter: int = 18) -> Tuple[float, float]:
        LB = -float("inf")
        UB = float("inf")
        # Nonnegativity
        LB = max(LB, -a_half[j])
        UB = min(UB, a_half[j + 1])
        if j > 0:
            UB = min(UB, a_half[j - 1] - a_half[j])
        if j + 2 < len(a_half):
            UB = min(UB, a_half[j + 1] - a_half[j + 2])
        # Pair-wise monotone after move: a[j] + delta >= a[j+1] - delta => delta >= (a[j+1] - a[j])/2
        LB = max(LB, (a_half[j + 1] - a_half[j]) * 0.5)
        if not (LB < UB):
            return 0.0, ratio_current

        phi = (1 + math.sqrt(5)) / 2.0
        invphi = 1.0 / phi
        aL = LB
        aR = UB
        x1 = aR - (aR - aL) * invphi
        x2 = aL + (aR - aL) * invphi

        def eval_delta(d: float) -> float:
            b = a_half.copy()
            b[j] += d
            b[j + 1] -= d
            b = project_monotone_nonincreasing(b)
            h = build_full_from_half(b)
            h = normalize_to_sum_n(h)
            return compute_ratio(h)

        r1 = eval_delta(x1)
        r2 = eval_delta(x2)
        best_r = ratio_current
        best_d = 0.0
        if r1 > best_r:
            best_r = r1
            best_d = x1
        if r2 > best_r:
            best_r = r2
            best_d = x2

        for _ in range(max_iter):
            if (aR - aL) <= 1e-12:
                break
            if r1 < r2:
                aL = x1
                x1 = x2
                r1 = r2
                x2 = aL + (aR - aL) * invphi
                r2 = eval_delta(x2)
                if r2 > best_r:
                    best_r = r2
                    best_d = x2
            else:
                aR = x2
                x2 = x1
                r2 = r1
                x1 = aR - (aR - aL) * invphi
                r1 = eval_delta(x1)
                if r1 > best_r:
                    best_r = r1
                    best_d = x1

        # Endpoints
        rL = eval_delta(LB)
        if rL > best_r:
            best_r = rL
            best_d = LB
        rU = eval_delta(UB)
        if rU > best_r:
            best_r = rU
            best_d = UB
        return best_d, best_r

    # Ridge Flattening with extended active set (RF-EAS)
    def ridge_flatten(a_half: np.ndarray, ratio_current: float) -> Tuple[np.ndarray, float, bool]:
        best_ratio = ratio_current
        best_a = a_half.copy()
        improved = False
        m_candidates = [3, 4, 5, 6]
        eta_candidates = [0.15, 0.20, 0.25, 0.30]

        for m in m_candidates:
            for eta in eta_candidates:
                # Determine α_max to keep a0 - m*α >= 0
                if a_half[0] <= 1e-12:
                    continue
                alpha_max = a_half[0] / (m + 1e-12)
                alpha_max = max(0.0, alpha_max)
                if alpha_max <= 1e-12:
                    continue

                # Golden section over α ∈ [0, alpha_max]
                aL = 0.0
                aR = alpha_max
                phi = (1 + math.sqrt(5)) / 2.0
                invphi = 1.0 / phi
                x1 = aR - (aR - aL) * invphi
                x2 = aL + (aR - aL) * invphi

                def eval_alpha(alpha: float) -> Tuple[float, np.ndarray]:
                    b = a_half.copy()
                    b[0] = max(0.0, b[0] - m * alpha)
                    upto = min(m, len(b) - 1)
                    for i in range(1, upto + 1):
                        b[i] += alpha
                    if upto + 1 < len(b):
                        b[upto + 1] += eta * alpha
                    if upto + 2 < len(b):
                        b[upto + 2] += eta * alpha
                    # Project to monotone cone to enforce feasibility
                    b = project_monotone_nonincreasing(b)
                    h = build_full_from_half(b)
                    h = normalize_to_sum_n(h)
                    return compute_ratio(h), b

                r1, b1 = eval_alpha(x1)
                r2, b2 = eval_alpha(x2)
                local_best_r = ratio_current
                local_best_b = a_half.copy()
                if r1 > local_best_r:
                    local_best_r = r1
                    local_best_b = b1
                if r2 > local_best_r:
                    local_best_r = r2
                    local_best_b = b2

                for _ in range(14):
                    if (aR - aL) <= 1e-12:
                        break
                    if r1 < r2:
                        aL = x1
                        x1 = x2
                        r1 = r2
                        b1 = b2
                        x2 = aL + (aR - aL) * invphi
                        r2, b2 = eval_alpha(x2)
                        if r2 > local_best_r:
                            local_best_r = r2
                            local_best_b = b2
                    else:
                        aR = x2
                        x2 = x1
                        r2 = r1
                        b2 = b1
                        x1 = aR - (aR - aL) * invphi
                        r1, b1 = eval_alpha(x1)
                        if r1 > local_best_r:
                            local_best_r = r1
                            local_best_b = b1

                # Endpoints
                rL, bL = eval_alpha(0.0)
                if rL > local_best_r:
                    local_best_r = rL
                    local_best_b = bL
                rU, bU = eval_alpha(alpha_max)
                if rU > local_best_r:
                    local_best_r = rU
                    local_best_b = bU

                if local_best_r > best_ratio + 1e-12:
                    best_ratio = local_best_r
                    best_a = local_best_b
                    improved = True

        return best_a, best_ratio, improved

    # Compute gradient of L2^2 w.r.t. conv nodes (piecewise-linear model)
    def grad_l2sq_wrt_nodes(conv: np.ndarray) -> Tuple[np.ndarray, float]:
        m = len(conv)
        values = np.concatenate(([0.0], conv.astype(float), [0.0]))
        w = 1.0 / (m + 1)  # uniform width used by the trapezoidal-like integration
        l2_sq = 0.0
        for i in range(m + 1):
            vi = values[i]
            vj = values[i + 1]
            l2_sq += w / 3.0 * (vi * vi + vi * vj + vj * vj)
        grad_l2 = np.zeros_like(conv, dtype=float)
        for i in range(m):
            vi = conv[i]
            vm1 = 0.0 if i == 0 else conv[i - 1]
            vp1 = 0.0 if i == m - 1 else conv[i + 1]
            grad_l2[i] = (w / 3.0) * (4.0 * vi + vm1 + vp1)
        return grad_l2, l2_sq

    # STJ: Softmax-L∞ Toeplitz Jacobian Descent
    def stj_step(a_half: np.ndarray, ratio_current: float, max_steps: int = 2) -> Tuple[np.ndarray, float, bool]:
        improved_any = False
        best_ratio = ratio_current
        best_a = a_half.copy()

        for _ in range(max_steps):
            # Build normalized current state
            h = build_full_from_half(best_a)
            h = normalize_to_sum_n(h)
            g = np.convolve(h, h)
            m = len(g)
            if m <= 0:
                break
            # indices window around the maximum
            c_idx = int(np.argmax(g))
            rL = max(0, c_idx - 6)
            rR = min(m, c_idx + 7)  # exclusive upper bound
            win_inds = list(range(rL, rR))

            # Adaptive temperature τ
            gmax = float(g[c_idx])
            gside = max(1e-12, float(max(g[c_idx - 1] if c_idx - 1 >= 0 else 0.0,
                                          g[c_idx + 1] if c_idx + 1 < m else 0.0)))
            curvature = max(1.0, (gmax - gside) / max(1e-12, gmax))
            tau = 0.02 * curvature
            tau = max(0.015, min(0.04, tau))

            # Smooth Linf surrogate and its gradient
            exps = np.exp((g - np.max(g)) / tau)  # stabilize
            softZ = float(np.sum(exps))
            linf_soft = float(tau * (np.log(softZ) + (np.max(g) / tau)))  # re-add shift
            softmax_vec = exps / max(softZ, 1e-18)

            # L1 (average) and gradient
            l1 = float(np.sum(g) / (m + 1))
            dl1 = 1.0 / (m + 1)

            # L2^2 gradient (piecewise integral model)
            grad_l2, l2_sq = grad_l2sq_wrt_nodes(g)

            # Gradient of R_tau wrt g
            denom = l1 * linf_soft
            if denom <= 1e-18:
                break
            # Compose gradient: component-wise
            # dR = (dl2*l1*Linf - L2^2*(dl1*Linf + l1*dLinf)) / (l1^2 * Linf^2)
            grad_R_g = (grad_l2 * (l1 * linf_soft) - l2_sq * (dl1 * linf_soft + l1 * softmax_vec)) / (l1 * l1 * linf_soft * linf_soft)

            # Focus gradient to window (stabilize)
            mask = np.zeros_like(grad_R_g)
            mask[rL:rR] = 1.0
            grad_R_g *= mask

            # Map to gradient wrt full heights using Toeplitz Jacobian: d g[r]/d h[p] = 2*h[r - p]
            grad_h = np.zeros_like(h)
            for p in range(len(h)):
                # r such that q = r - p in [0, len(h)-1]:
                r_lo = max(0, p)
                r_hi = min(m - 1, p + len(h) - 1)
                # sum grad_R_g[r] * 2*h[q]
                s = 0.0
                for r in range(r_lo, r_hi + 1):
                    q = r - p
                    if 0 <= q < len(h):
                        s += grad_R_g[r] * 2.0 * h[q]
                grad_h[p] = s

            # Map to half gradient by summing symmetric positions
            grad_half = np.zeros_like(best_a)
            for i in range(len(best_a)):
                if i == 0:
                    positions = [24, 25]
                else:
                    positions = [24 - i, 25 + i]
                grad_half[i] = sum(grad_h[p] for p in positions)

            # Form mass-neutral, monotone update Δa = -η ∇, subtract mean to preserve mass
            grad_half = np.asarray(grad_half, dtype=float)
            mean_grad = float(np.mean(grad_half))
            dir_vec = -(grad_half - mean_grad)

            # Short backtracking line search on η
            step_candidates = [0.08, 0.06, 0.045, 0.035, 0.028, 0.022, 0.018]
            accepted = False
            curr_best_local_ratio = best_ratio
            curr_best_local_a = best_a.copy()
            for eta in step_candidates:
                b = best_a.copy()
                b += eta * dir_vec
                # Project to monotone cone with nonnegativity
                b = project_monotone_nonincreasing(b)
                h2 = build_full_from_half(b)
                h2 = normalize_to_sum_n(h2)
                r2 = compute_ratio(h2)
                if r2 > curr_best_local_ratio + 1e-12:
                    curr_best_local_ratio = r2
                    curr_best_local_a = b
                    accepted = True
                    break  # accept first improving step

            if accepted and curr_best_local_ratio > best_ratio + 1e-12:
                best_ratio = curr_best_local_ratio
                best_a = curr_best_local_a
                improved_any = True
            else:
                # no improvement; stop further STJ steps for this cycle
                break

        return best_a, best_ratio, improved_any

    # Small NNLS via active set: solve min ||A x - b||2 with x >= 0
    def nnls_active_set(A: np.ndarray, b: np.ndarray, max_iter: int = 20) -> np.ndarray:
        # initial unconstrained
        x, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
        x = x.reshape(-1)
        x[x < 0] = 0.0
        for _ in range(max_iter):
            active = [i for i in range(len(x)) if x[i] > 0]
            if not active:
                # Try single-coordinate fits
                grads = A.T @ (A @ x - b)
                idx = int(np.argmin(grads))
                active = [idx]
            A_active = A[:, active] if active else np.zeros((A.shape[0], 0))
            if A_active.shape[1] == 0:
                break
            xa, _, _, _ = np.linalg.lstsq(A_active, b, rcond=None)
            xa = xa.reshape(-1)
            x_new = np.zeros_like(x)
            for j, idx in enumerate(active):
                x_new[idx] = xa[j]
            x_new[x_new < 0] = 0.0
            if np.linalg.norm(x_new - x) < 1e-9:
                x = x_new
                break
            x = x_new
        x[x < 0] = 0.0
        return x

    # Convolution Plateau Equalization (CPE-Exact++) with dual windows and Toeplitz Jacobian
    def cpe_equalize_dual(a_half: np.ndarray, ratio_current: float) -> Tuple[np.ndarray, float, bool]:
        # Build normalized current state
        h = build_full_from_half(a_half)
        h = normalize_to_sum_n(h)
        conv = np.convolve(h, h)
        c_idx = int(np.argmax(conv))
        improved = False
        best_ratio = ratio_current
        best_a = a_half.copy()

        # Dual windows: central plateau Kc and shoulder ring Ks
        Kc_choices = [7]
        Ks_choices = [4]

        for Kc in Kc_choices:
            offset_c = Kc // 2
            r0c = max(0, c_idx - offset_c)
            r1c = min(len(conv), r0c + Kc)
            r0c = r1c - Kc
            central_inds = list(range(r0c, r1c))
            g_central = conv[r0c:r1c]
            # Lc = median of central window
            Lc = float(np.median(g_central))

            for Ks in Ks_choices:
                # shoulder ring: next two nodes on each side of the central window
                left_ring = []
                right_ring = []
                for j in range(1, (Ks // 2) + 1):
                    li = c_idx - offset_c - j
                    ri = c_idx + offset_c + j
                    if 0 <= li < len(conv):
                        left_ring.append(li)
                    if 0 <= ri < len(conv):
                        right_ring.append(ri)
                ring_inds = left_ring + right_ring
                ring_inds.sort()
                if not ring_inds:
                    continue
                g_ring = conv[ring_inds]
                # Ls = mean over shoulder ring, capped by Lc
                Ls = float(np.mean(g_ring))
                if Ls > Lc:
                    Ls = Lc

                # Build combined window indices
                all_inds = central_inds + ring_inds
                # Targets vector b: Lc for central, Ls for ring; b = target - g
                b = []
                for r in all_inds:
                    target = Lc if r in central_inds else Ls
                    b.append(target - conv[r])
                b = np.array(b, dtype=float)

                # influence matrix S for i = 0..M (half positions)
                for M in [5, 6, 7]:
                    M_cap = min(M, len(a_half) - 1)
                    cols = M_cap + 1  # including i=0
                    if cols <= 0:
                        continue
                    S = np.zeros((len(all_inds), cols), dtype=float)
                    # Fill S: S[row, i] = sum over p∈pos(i) of 2*h[r - p]
                    for ri, r in enumerate(all_inds):
                        for i in range(cols):
                            if i == 0:
                                pos = [24, 25]
                            else:
                                pos = [24 - i, 25 + i]
                            val = 0.0
                            for p in pos:
                                q = r - p
                                if 0 <= q < len(h):
                                    val += 2.0 * h[q]
                            S[ri, i] = val

                    # Mass-preserving compression: δ0 = -sum_{i>=1} δi, so Δg ≈ Σ_i>=1 (S[:,i] - S[:,0]) δi
                    if cols - 1 <= 0:
                        continue
                    S_pos = S[:, 1:cols]
                    S0 = S[:, [0]]
                    A = S_pos - S0  # effective matrix for positive deltas
                    # Solve NNLS for δ_pos
                    delta_pos = nnls_active_set(A, b)
                    delta_pos = np.asarray(delta_pos, dtype=float)
                    # Build Δa template
                    delta_a = np.zeros(len(a_half))
                    for i in range(1, cols):
                        delta_a[i] = delta_pos[i - 1]
                    delta_sum = float(np.sum(delta_pos))
                    delta_a[0] = -delta_sum  # mass-preserving

                    # Feasibility bound s_max from a0 >= 0
                    s_max = 1.0
                    if delta_a[0] < 0:
                        s_max = min(s_max, a_half[0] / max(1e-12, -delta_a[0]))

                    # line search for scale s
                    phi = (1 + math.sqrt(5)) / 2.0
                    invphi = 1.0 / phi
                    aL = 0.0
                    aR = s_max
                    x1 = aR - (aR - aL) * invphi
                    x2 = aL + (aR - aL) * invphi

                    def eval_scale(s: float) -> Tuple[float, np.ndarray]:
                        b_half = a_half.copy()
                        b_half += s * delta_a
                        b_half = project_monotone_nonincreasing(b_half)
                        h2 = build_full_from_half(b_half)
                        h2 = normalize_to_sum_n(h2)
                        return compute_ratio(h2), b_half

                    r1_val, b1 = eval_scale(x1)
                    r2_val, b2 = eval_scale(x2)
                    local_best_r = ratio_current
                    local_best_b = a_half.copy()
                    if r1_val > local_best_r:
                        local_best_r = r1_val
                        local_best_b = b1
                    if r2_val > local_best_r:
                        local_best_r = r2_val
                        local_best_b = b2
                    for _ in range(12):
                        if (aR - aL) <= 1e-12:
                            break
                        if r1_val < r2_val:
                            aL = x1
                            x1 = x2
                            r1_val = r2_val
                            b1 = b2
                            x2 = aL + (aR - aL) * invphi
                            r2_val, b2 = eval_scale(x2)
                            if r2_val > local_best_r:
                                local_best_r = r2_val
                                local_best_b = b2
                        else:
                            aR = x2
                            x2 = x1
                            r2_val = r1_val
                            b2 = b1
                            x1 = aR - (aR - aL) * invphi
                            r1_val, b1 = eval_scale(x1)
                            if r1_val > local_best_r:
                                local_best_r = r1_val
                                local_best_b = b1
                    # endpoints
                    rL, bL = eval_scale(0.0)
                    if rL > local_best_r:
                        local_best_r = rL
                        local_best_b = bL
                    rU, bU = eval_scale(s_max)
                    if rU > local_best_r:
                        local_best_r = rU
                        local_best_b = bU

                    if local_best_r > best_ratio + 1e-12:
                        best_ratio = local_best_r
                        best_a = local_best_b
                        improved = True
        return best_a, best_ratio, improved

    # Tail Sparsification and Redistribute (TSR)
    def tail_sparsify(a_half: np.ndarray, weights: np.ndarray, ratio_current: float) -> Tuple[np.ndarray, float, bool]:
        best_ratio = ratio_current
        best_a = a_half.copy()
        improved = False
        T_candidates = [3, 4, 5]
        M_candidates = [3, 4, 5]

        # Ensure weights normalized and positive on 1..M
        w = weights.copy()
        for T in T_candidates:
            for M in M_candidates:
                # Prepare indices
                tail_start = len(a_half) - T
                tail_indices = list(range(tail_start, len(a_half)))
                shoulder_indices = list(range(1, min(M, len(a_half) - 1) + 1))
                # s in [0, s_max], s_max <= 0.8 to avoid aggressive cuts
                s_max = 0.8

                # Golden section over s
                aL = 0.0
                aR = s_max
                phi = (1 + math.sqrt(5)) / 2.0
                invphi = 1.0 / phi
                x1 = aR - (aR - aL) * invphi
                x2 = aL + (aR - aL) * invphi

                def eval_s(s: float) -> Tuple[float, np.ndarray]:
                    b = a_half.copy()
                    # shrink tail bins
                    removed_mass = 0.0
                    for idx in tail_indices:
                        dec = s * b[idx]
                        b[idx] -= dec
                        removed_mass += dec
                    if removed_mass <= 0.0:
                        h = build_full_from_half(b)
                        h = normalize_to_sum_n(h)
                        return compute_ratio(h), b
                    # redistribute to shoulder indices proportionally to weights
                    ww = np.array([w[i] if i in shoulder_indices else 0.0 for i in range(len(b))])
                    sumw = float(np.sum(ww))
                    if sumw <= 1e-18:
                        # fall back to uniform among shoulder
                        for i in shoulder_indices:
                            b[i] += removed_mass / max(1, len(shoulder_indices))
                    else:
                        for i in shoulder_indices:
                            share = (w[i] / sumw) * removed_mass
                            b[i] += share
                    # projection to ensure monotone feasibility
                    b = project_monotone_nonincreasing(b)
                    h = build_full_from_half(b)
                    h = normalize_to_sum_n(h)
                    return compute_ratio(h), b

                r1, b1 = eval_s(x1)
                r2, b2 = eval_s(x2)
                local_best_r = ratio_current
                local_best_b = a_half.copy()
                if r1 > local_best_r:
                    local_best_r = r1
                    local_best_b = b1
                if r2 > local_best_r:
                    local_best_r = r2
                    local_best_b = b2

                for _ in range(12):
                    if (aR - aL) <= 1e-12:
                        break
                    if r1 < r2:
                        aL = x1
                        x1 = x2
                        r1 = r2
                        b1 = b2
                        x2 = aL + (aR - aL) * invphi
                        r2, b2 = eval_s(x2)
                        if r2 > local_best_r:
                            local_best_r = r2
                            local_best_b = b2
                    else:
                        aR = x2
                        x2 = x1
                        r2 = r1
                        b2 = b1
                        x1 = aR - (aR - aL) * invphi
                        r1, b1 = eval_s(x1)
                        if r1 > local_best_r:
                            local_best_r = r1
                            local_best_b = b1

                # Endpoints
                rL, bL = eval_s(0.0)
                if rL > local_best_r:
                    local_best_r = rL
                    local_best_b = bL
                rU, bU = eval_s(s_max)
                if rU > local_best_r:
                    local_best_r = rU
                    local_best_b = bU

                if local_best_r > best_ratio + 1e-12:
                    best_ratio = local_best_r
                    best_a = local_best_b
                    improved = True

        return best_a, best_ratio, improved

    # Central Curvature Regularization (CCR): micro-step redistribution
    def ccr_local(a_half: np.ndarray, ratio_current: float) -> Tuple[np.ndarray, float, bool]:
        if len(a_half) < 5 or a_half[0] <= 0:
            return a_half, ratio_current, False
        # small gamma based on available curvature: move a tiny mass from a0 to indices 2..4
        gamma = min(0.03 * a_half[0], max(0.0, (a_half[0] - a_half[1]) * 0.25))
        if gamma <= 1e-12:
            return a_half, ratio_current, False
        weights = np.array([0.5, 0.3, 0.2])
        weights = weights / np.sum(weights)
        b = a_half.copy()
        b[0] -= gamma
        for idx, wgt in zip([2, 3, 4], weights):
            if idx < len(b):
                b[idx] += gamma * wgt
        b = project_monotone_nonincreasing(b)
        h = build_full_from_half(b)
        h = normalize_to_sum_n(h)
        r = compute_ratio(h)
        if r > ratio_current + 1e-12:
            return b, r, True
        return a_half, ratio_current, False

    # Monotone optimization over half sequence with structured phases including STJ and CPE-Exact++
    def optimize_half(a_half_init: np.ndarray, max_cycles: int = 80) -> Tuple[np.ndarray, float]:
        a_half = project_monotone_nonincreasing(a_half_init)
        h_full = build_full_from_half(a_half)
        h_full = normalize_to_sum_n(h_full)
        best_ratio = compute_ratio(h_full)

        last_linf = float(np.max(np.convolve(h_full, h_full)))
        # Cycle through optimization phases
        for cycle in range(max_cycles):
            improved_cycle = False

            # Compute sensitivity weights for prioritization
            weights = compute_influence_weights(a_half)

            # Tri-scale mass transfers with SGP prioritization
            pair_candidates = []
            for d in [4, 3, 2, 1]:
                for j in range(0, half_len - d):
                    k = j + d
                    priority = (weights[j] - weights[k])
                    pair_candidates.append((priority, j, k, d))
            pair_candidates.sort(key=lambda t: t[0], reverse=True)

            for _, j, k, _ in pair_candidates:
                UB = float("inf")
                if j > 0:
                    UB = min(UB, a_half[j - 1] - a_half[j])
                if k + 1 < len(a_half):
                    UB = min(UB, a_half[k] - a_half[k + 1])
                else:
                    UB = min(UB, a_half[k])
                if not (UB > 1e-12):
                    continue
                d_star, r_star = line_search_pair_triscale(a_half, j, k, best_ratio)
                if r_star > best_ratio + 1e-12 and abs(d_star) > 1e-16:
                    a_half[j] += d_star
                    a_half[k] -= d_star
                    a_half = project_monotone_nonincreasing(a_half)
                    h_full = build_full_from_half(a_half)
                    h_full = normalize_to_sum_n(h_full)
                    best_ratio = compute_ratio(h_full)
                    improved_cycle = True

            # Fine-tune with adjacent pair balancing allowing both directions
            for j in range(0, half_len - 1):
                d_star, r_star = line_search_pair_adjacent(a_half, j, best_ratio)
                if r_star > best_ratio + 1e-12 and abs(d_star) > 1e-16:
                    a_half[j] += d_star
                    a_half[j + 1] -= d_star
                    a_half = project_monotone_nonincreasing(a_half)
                    h_full = build_full_from_half(a_half)
                    h_full = normalize_to_sum_n(h_full)
                    best_ratio = compute_ratio(h_full)
                    improved_cycle = True

            # Ridge flattening with extended active set
            a_half, best_ratio, rf_improved = ridge_flatten(a_half, best_ratio)
            if rf_improved:
                improved_cycle = True

            # KKT-guided signed monotone transport (inserted layer)
            def kkt_signed_transport(a_half_loc: np.ndarray, ratio_cur: float) -> Tuple[np.ndarray, float, bool]:
                # Reuse the earlier gradient mapping
                # Compute grad via conv nodes with subgradient for Linf (like baseline KKT)
                # Utilize the previously defined grad_l2sq_wrt_nodes and compose with simple Linf subgradient
                h_loc = build_full_from_half(a_half_loc)
                h_loc = normalize_to_sum_n(h_loc)
                conv = np.convolve(h_loc, h_loc)
                m = len(conv)
                grad_l2, l2_sq = grad_l2sq_wrt_nodes(conv)
                l1 = float(np.sum(conv) / (m + 1))
                c_idx = int(np.argmax(conv))
                peak_set = set(range(max(0, c_idx - 2), min(m, c_idx + 3)))
                dlinf = np.zeros_like(conv, dtype=float)
                for i in peak_set:
                    dlinf[i] = 1.0 / max(1, len(peak_set))
                linf = float(np.max(conv))
                denom = (l1 * linf)
                if denom <= 0:
                    return a_half_loc, ratio_cur, False
                grad_v = (grad_l2 * l1 * linf - l2_sq * (1.0 / (m + 1) * linf + l1 * dlinf)) / (l1 * l1 * linf * linf)
                # gradient wrt full heights
                grad_h = np.zeros_like(h_loc)
                for p in range(len(h_loc)):
                    s = 0.0
                    r_lo = max(0, p)
                    r_hi = min(m - 1, p + len(h_loc) - 1)
                    for r in range(r_lo, r_hi + 1):
                        q = r - p
                        if 0 <= q < len(h_loc):
                            s += grad_v[r] * 2.0 * h_loc[q]
                    grad_h[p] = s
                grad_half = np.zeros_like(a_half_loc)
                for i in range(len(a_half_loc)):
                    if i == 0:
                        positions = [24, 25]
                    else:
                        positions = [24 - i, 25 + i]
                    grad_half[i] = sum(grad_h[p] for p in positions)
                # sinks/sources based on grad sign
                idxs = list(range(len(a_half_loc)))
                sinks = [i for i in idxs if grad_half[i] > 0]
                sources = [i for i in idxs if grad_half[i] < 0]
                if len(sinks) == 0 or len(sources) == 0:
                    return a_half_loc, ratio_cur, False
                sinks.sort(key=lambda i: (i, -grad_half[i]))
                sources.sort(key=lambda i: (-i, grad_half[i]))
                pairs = []
                used_sources = set()
                d_list = [2, 3, 4, 5]
                for j in sinks:
                    found = False
                    for d in d_list:
                        k = j + d
                        if k in sources and k not in used_sources:
                            pairs.append((j, k))
                            used_sources.add(k)
                            found = True
                            break
                    if not found:
                        for k in sources:
                            if k not in used_sources and k > j + 1:
                                pairs.append((j, k))
                                used_sources.add(k)
                                break
                if not pairs:
                    return a_half_loc, ratio_cur, False

                def pair_ub(j: int, k: int) -> float:
                    ub = float("inf")
                    if j > 0:
                        ub = min(ub, a_half_loc[j - 1] - a_half_loc[j])
                    if k + 1 < len(a_half_loc):
                        ub = min(ub, a_half_loc[k] - a_half_loc[k + 1])
                    else:
                        ub = min(ub, a_half_loc[k])
                    return max(0.0, ub)

                tau_max = min(pair_ub(j, k) for (j, k) in pairs)
                if not (tau_max > 1e-18):
                    return a_half_loc, ratio_cur, False

                phi = (1 + math.sqrt(5)) / 2.0
                invphi = 1.0 / phi
                aL = 0.0
                aR = tau_max
                x1 = aR - (aR - aL) * invphi
                x2 = aL + (aR - aL) * invphi

                def apply_transport(tau: float) -> Tuple[float, np.ndarray]:
                    b = a_half_loc.copy()
                    for (j, k) in pairs:
                        b[j] += tau
                        b[k] -= tau
                    b = project_monotone_nonincreasing(b)
                    h = build_full_from_half(b)
                    h = normalize_to_sum_n(h)
                    return compute_ratio(h), b

                r1, b1 = apply_transport(x1)
                r2, b2 = apply_transport(x2)
                best_r = ratio_cur
                best_b = a_half_loc.copy()
                if r1 > best_r:
                    best_r = r1
                    best_b = b1
                if r2 > best_r:
                    best_r = r2
                    best_b = b2

                for _ in range(14):
                    if (aR - aL) <= 1e-12:
                        break
                    if r1 < r2:
                        aL = x1
                        x1 = x2
                        r1 = r2
                        b1 = b2
                        x2 = aL + (aR - aL) * invphi
                        r2, b2 = apply_transport(x2)
                        if r2 > best_r:
                            best_r = r2
                            best_b = b2
                    else:
                        aR = x2
                        x2 = x1
                        r2 = r1
                        b2 = b1
                        x1 = aR - (aR - aL) * invphi
                        r1, b1 = apply_transport(x1)
                        if r1 > best_r:
                            best_r = r1
                            best_b = b1

                # Endpoints
                rL, bL = apply_transport(0.0)
                if rL > best_r:
                    best_r = rL
                    best_b = bL
                rU, bU = apply_transport(tau_max)
                if rU > best_r:
                    best_r = rU
                    best_b = bU

                if best_r > ratio_cur + 1e-12:
                    return best_b, best_r, True
                return a_half_loc, ratio_cur, False

            a_half, best_ratio, ksmt_improved = kkt_signed_transport(a_half, best_ratio)
            if ksmt_improved:
                improved_cycle = True

            # Insert STJ step (exact Toeplitz Jacobian with smooth Linf surrogate)
            a_half, best_ratio, stj_improved = stj_step(a_half, best_ratio, max_steps=2)
            if stj_improved:
                improved_cycle = True

            # Convolution Plateau Equalization (CPE-Exact++) dual windows
            a_half, best_ratio, cpe_improved = cpe_equalize_dual(a_half, best_ratio)
            if cpe_improved:
                improved_cycle = True

            # Central Curvature Regularization (CCR) micro-step if needed (if Linf didn't drop)
            h_tmp = build_full_from_half(a_half)
            h_tmp = normalize_to_sum_n(h_tmp)
            linf_now = float(np.max(np.convolve(h_tmp, h_tmp)))
            if linf_now >= last_linf - 1e-12:
                a_half, best_ratio, ccr_improved = ccr_local(a_half, best_ratio)
                if ccr_improved:
                    improved_cycle = True
            last_linf = linf_now

            # Tail sparsification and redistribute
            weights = compute_influence_weights(a_half)  # refresh weights post RF/KSMT/CPE/STJ
            a_half, best_ratio, tsr_improved = tail_sparsify(a_half, weights, best_ratio)
            if tsr_improved:
                improved_cycle = True

            # Normalize and check improvement
            a_half = project_monotone_nonincreasing(a_half)
            h_full = build_full_from_half(a_half)
            h_full = normalize_to_sum_n(h_full)
            current_ratio = compute_ratio(h_full)
            if current_ratio > best_ratio + 1e-14:
                best_ratio = current_ratio
                improved_cycle = True

            if not improved_cycle:
                break

        # Final normalization and projection
        a_half = project_monotone_nonincreasing(a_half)
        h_full = build_full_from_half(a_half)
        h_full = normalize_to_sum_n(h_full)
        best_ratio = compute_ratio(h_full)
        return a_half, best_ratio

    # Run optimization on the top seeds
    best_heights = None
    best_ratio = -1.0
    for h0, _ in seed_pairs:
        # Reconstruct a_half from h0 to feed into optimizer:
        a_half0 = np.zeros(half_len, dtype=float)
        a_half0[0] = h0[24]
        for k in range(1, half_len):
            a_half0[k] = h0[24 - k]
        a_half_opt, _ = optimize_half(a_half0)
        h_opt = build_full_from_half(a_half_opt)
        h_opt = normalize_to_sum_n(h_opt)
        r_val = compute_ratio(h_opt)
        if r_val > best_ratio:
            best_ratio = r_val
            best_heights = h_opt

    # Safety: Ensure final heights satisfy symmetry, monotone, nonnegativity, and sum normalization
    if best_heights is None:
        best_heights = np.ones(n, dtype=float)
    # Enforce symmetry
    for i in range(n // 2):
        v = 0.5 * (best_heights[i] + best_heights[n - 1 - i])
        best_heights[i] = v
        best_heights[n - 1 - i] = v
    # Enforce monotone on half
    a_half_final = np.zeros(half_len, dtype=float)
    a_half_final[0] = best_heights[24]
    for k in range(1, half_len):
        a_half_final[k] = best_heights[24 - k]
    a_half_final = project_monotone_nonincreasing(a_half_final)
    best_heights = build_full_from_half(a_half_final)
    best_heights = normalize_to_sum_n(best_heights)

    # Compute final ratio
    c_lower_bound = compute_ratio(best_heights)

    return best_heights.tolist(), float(c_lower_bound)


# EVOLVE_END


if __name__ == "__main__":
    heights, c_lower_bound = optimize_lower_bound()
    print(json.dumps({"heights": heights, "c_lower_bound": c_lower_bound}))
```
