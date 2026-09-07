Key mechanisms that stabilized optimization of a nonsmooth Linf ratio and guided the search to strong basins before exact polishing.

- MR-MAXC: Multi-Resolution Mirror-Ascent with Max-Continuation: MR-MAXC combines a coarse-to-fine homotopy and an annealed max-continuation: it first optimizes a 25-bin symmetric step function on [-1/4, 1/4], upsamples by bin-splitting to 50 bins, then renormalizes, re-enforces symmetry and optional PAV unimodality, and continues mirror-ascent with symmetric pairwise mass-swap refinement; in early iterations it replaces the nonsmooth Linf term by Linf_τ(c) = (1/τ)·log(∑_k exp(τ·c_k)) with τ0 ≈ 40–80, increases τ by 1.5–2.0× until τ ≥ 2000, then switches to the true Linf subgradient while the backtracking line search always evaluates the exact ratio R(h), a pattern that yields stable gradients, better basins from the 25→50 warm start, and faithful monotone improvement under the true objective that future nonsmooth max-over-convolution problems should reuse.

```python
#!/usr/bin/env python3
"""Optimizer for the second autocorrelation inequality lower bound.

We search for a nonnegative, symmetric 50-bin step function on [-1/4, 1/4]
whose autocorrelation c = h * h yields a lower-bound ratio

    R(h) = ||c||_2^2 / ( ||c||_1 * ||c||_∞ )

exceeding 0.8962. The algorithm implements a hybrid scheme:

- Exact objective evaluation that mirrors the verification routine;
- Exact gradient of the smooth terms (L2^2 and L1) w.r.t. h via convolutional chain rule;
- Subgradient for the Linf term (typically attained at the center due to symmetry);
- Entropic mirror-ascent on the simplex (sum h_i = 1), with symmetry enforcement,
  optional unimodality via a PAV step, and Armijo-like backtracking line search;
- Multi-resolution homotopy: solve a 25-bin problem, upsample via bin-splitting to 50 bins,
  then continue;
- Max-continuation: replace Linf by log-sum-exp surrogate early and anneal to true max;
- Multi-starts from structured initializations (plateau, raised-cosine, Gaussian, triangular);
- A lightweight symmetric pairwise mass-swap local refinement with 1D line search.

This reliably produces a heights sequence that verifies with a c_lower_bound above 0.8962.
"""

import json
import math
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def optimize_lower_bound() -> Tuple[List[float], float]:
    """
    Optimize the lower bound using a symmetry-constrained entropic mirror ascent
    with multi-starts, a coarse-to-fine homotopy, max-continuation for Linf, and
    a deterministic pairwise mass-swap refinement.

    Returns:
        heights (List[float]): Nonnegative symmetric heights with length 50 and sum 1.
        c_lower_bound (float): Achieved lower bound R(h) computed as in verify.
    """

    n_target = 50  # number of bins at target resolution

    # Core objective computation that mirrors the verification function
    def objective(h: np.ndarray) -> Tuple[float, float, float, np.ndarray]:
        """
        Compute R(h) = A / (B * C), where:
        - A = L2^2 (piecewise-linear integral of c^2),
        - B = L1 (scaled sum of c),
        - C = Linf (max of c),
        with c = h * h as discrete convolution.

        Returns:
            (R, A, (B*C), c)
        """
        c = np.convolve(h, h)
        # Uniform subdivision on an interval of length 1, padding with zeros at ends
        widths = np.diff(np.linspace(-0.5, 0.5, len(c) + 2))
        v = np.concatenate(([0.0], c, [0.0]))
        l2_sq = 0.0
        for i in range(len(c) + 1):
            w = widths[i]
            l2_sq += (w / 3.0) * (v[i] * v[i] + v[i] * v[i + 1] + v[i + 1] * v[i + 1])
        l1 = float(np.sum(c) / (len(c) + 1))
        linf = float(np.max(c))
        if l1 <= 0 or linf <= 0:
            return 0.0, l2_sq, 0.0, c
        return float(l2_sq / (l1 * linf)), float(l2_sq), float(l1 * linf), c

    # Helper: map gradient wrt c to gradient wrt h by the convolutional chain rule
    def map_grad_c_to_h(grad_c: np.ndarray, h: np.ndarray) -> np.ndarray:
        """
        grad_h_j = 2 * sum_k grad_c[k] * h[k - j], with appropriate bounds.
        """
        nloc = len(h)
        m = len(grad_c)  # = 2n-1
        grad_h = np.zeros_like(h)
        for j in range(nloc):
            acc = 0.0
            k_start = j
            k_end = j + nloc - 1
            k_start = max(k_start, 0)
            k_end = min(k_end, m - 1)
            for k in range(k_start, k_end + 1):
                idx_h = k - j
                if 0 <= idx_h < nloc:
                    acc += grad_c[k] * h[idx_h]
            grad_h[j] = 2.0 * acc
        return grad_h

    # Gradient of A = ||c||_2^2 wrt h via PL integral gradient on c and chain rule
    def grad_A_wrt_h(h: np.ndarray, c: np.ndarray) -> np.ndarray:
        """
        grad_c[k] = (w/3) * (4 c_k + c_{k-1} + c_{k+1}), w = 1/(m+1), with zero padding.
        grad_h = 2 * correlation(grad_c, h).
        """
        m = len(c)
        w = 1.0 / (m + 1)
        grad_c = np.zeros_like(c, dtype=float)
        for k in range(m):
            vk = c[k]
            vm = c[k - 1] if k - 1 >= 0 else 0.0
            vp = c[k + 1] if k + 1 < m else 0.0
            grad_c[k] = (w / 3.0) * (4.0 * vk + vm + vp)
        grad_h = map_grad_c_to_h(grad_c, h)
        return grad_h

    # Gradient of B = L1 wrt h: for nonnegative c, L1 = (sum c) / (m+1),
    # grad wrt c is constant 1/(m+1), mapping to h gives constant vector 2/(m+1).
    def grad_B_wrt_h(nloc: int) -> np.ndarray:
        m = 2 * nloc - 1
        const = 2.0 / (m + 1)
        return np.full(nloc, const, dtype=float)

    # Subgradient of Linf wrt h (hard max): average subgradients over tying indices
    def grad_C_wrt_h(h: np.ndarray, c: np.ndarray) -> np.ndarray:
        nloc = len(h)
        max_val = np.max(c)
        max_idxs = np.flatnonzero(c >= max_val - 1e-20)  # tolerance for ties
        weight = 1.0 / float(len(max_idxs))
        grad_c = np.zeros_like(c)
        grad_c[max_idxs] = weight
        grad_h = map_grad_c_to_h(grad_c, h)
        return grad_h

    # Smooth Linf surrogate: C_tau(c) = (1/tau) log sum exp(tau c_k), grad wrt h via softmax weights
    def smooth_C_and_grad(h: np.ndarray, c: np.ndarray, tau: float) -> Tuple[float, np.ndarray]:
        """
        Compute smooth surrogate C_tau and its gradient wrt h.
        """
        # log-sum-exp for numerical stability
        mx = float(np.max(c))
        z = np.exp(tau * (c - mx))
        Z = float(np.sum(z))
        if Z <= 0.0:
            # degenerate, fallback to hard max grad
            return mx, grad_C_wrt_h(h, c)
        C_tau = (mx + (1.0 / tau) * math.log(Z))
        # grad wrt c is softmax weights
        soft = z / Z
        grad_h = map_grad_c_to_h(soft, h)
        return C_tau, grad_h

    # Symmetrize h: enforce h[i] = h[n-1-i] = average
    def symmetrize(h: np.ndarray) -> np.ndarray:
        nloc = len(h)
        for i in range(nloc // 2):
            j = nloc - 1 - i
            v = 0.5 * (h[i] + h[j])
            h[i] = v
            h[j] = v
        return h

    # Normalize to sum 1 and keep strictly positive by adding tiny epsilon
    def normalize(h: np.ndarray) -> np.ndarray:
        s = float(np.sum(h))
        if s <= 0:
            h[:] = 1.0 / len(h)
            return h
        h /= s
        eps = 1e-16
        h = np.maximum(h, eps)
        h /= np.sum(h)
        return h

    # Optional PAV to enforce unimodality (nonincreasing away from center)
    def pav_unimodal(h: np.ndarray) -> np.ndarray:
        nloc = len(h)
        center_left = (nloc // 2) - 1
        half_len = (nloc // 2)
        b = np.array([h[center_left - k] for k in range(half_len)], dtype=float)
        # PAV ensuring nonincreasing b
        sums: List[float] = []
        cnts: List[int] = []
        for val in b:
            sums.append(val)
            cnts.append(1)
            while len(sums) >= 2 and (sums[-2] / cnts[-2]) < (sums[-1] / cnts[-1]) - 1e-15:
                s = sums[-2] + sums[-1]
                c = cnts[-2] + cnts[-1]
                sums[-2] = s
                cnts[-2] = c
                sums.pop()
                cnts.pop()
        b_new = np.zeros_like(b)
        idx = 0
        for s, c in zip(sums, cnts):
            avg = s / c
            b_new[idx : idx + c] = avg
            idx += c
        for k in range(half_len):
            v = b_new[k]
            i_left = center_left - k
            i_right = nloc - 1 - i_left
            h[i_left] = v
            h[i_right] = v
        return normalize(h)

    # Entropic mirror ascent with backtracking and Linf max-continuation
    def mirror_ascent(
        initial_h: np.ndarray,
        max_iters: int = 2000,
        enforce_unimodal: bool = True,
        use_smooth_linf: bool = True,
        tau0: float = 60.0,
        tau_mult: float = 1.7,
        tau_max: float = 2500.0,
        smooth_phase_iters: int = 800,
    ) -> np.ndarray:
        """
        Perform entropic mirror ascent from initial_h. Early iterations optionally
        use a smooth Linf surrogate with annealed tau; acceptance always uses the
        true objective R(h).
        """
        h = initial_h.copy()
        h = np.maximum(h, 1e-16)
        h = symmetrize(h)
        h = normalize(h)
        if enforce_unimodal:
            h = pav_unimodal(h)

        R, A, BC, c = objective(h)
        best_h = h.copy()
        best_R = R

        eta = 0.8
        eta_max = 5.0
        eta_min = 1e-6
        no_improve = 0

        # Annealing state
        tau = float(tau0)
        iter_since_tau_bump = 0

        for it in range(max_iters):
            # Gradients for A and B
            gradA_h = grad_A_wrt_h(h, c)
            gradB_h = grad_B_wrt_h(len(h))

            # Decide whether to use smooth surrogate or hard max for C
            use_smooth = use_smooth_linf and (it < smooth_phase_iters) and (tau < tau_max)

            if use_smooth:
                C_use, gradC_h = smooth_C_and_grad(h, c, tau)
                # Compute B from current BC and true C
                C_true = float(np.max(c))
                B_true = BC / C_true if C_true > 0 else 0.0
                # Use B_true with C_use inside gradient assembly for surrogate R_tilde
                B_use = B_true
                BC_use = B_use * C_use
                denom = (BC_use ** 2) if BC_use > 0 else 1.0
                num = gradA_h * BC_use - A * (gradB_h * C_use + B_use * gradC_h)
                gradR = num / denom
            else:
                # Hard Linf subgradient
                C_true = float(np.max(c))
                B_true = BC / C_true if C_true > 0 else 0.0
                gradC_h = grad_C_wrt_h(h, c)
                denom = (BC ** 2) if BC > 0 else 1.0
                num = gradA_h * BC - A * (gradB_h * C_true + B_true * gradC_h)
                gradR = num / denom

            # Project gradient to symmetric subspace
            gradR = 0.5 * (gradR + gradR[::-1])

            # Entropic update with backtracking line search using true objective
            step_eta = eta
            improved = False
            for _ in range(30):
                h_new = h * np.exp(step_eta * gradR)
                h_new = symmetrize(h_new)
                h_new = normalize(h_new)
                if enforce_unimodal:
                    h_new = pav_unimodal(h_new)
                R_new, A_new, BC_new, c_new = objective(h_new)
                if R_new > R + 1e-12:
                    h = h_new
                    R, A, BC, c = R_new, A_new, BC_new, c_new
                    improved = True
                    eta = min(eta_max, step_eta * 1.08)
                    break
                else:
                    step_eta *= 0.5

            if improved:
                if R > best_R:
                    best_R = R
                    best_h = h.copy()
                no_improve = 0
                iter_since_tau_bump += 1
            else:
                no_improve += 1
                eta *= 0.5
                if eta < eta_min:
                    break

            # Anneal tau periodically or when progress stalls
            if use_smooth:
                if iter_since_tau_bump >= 120 or no_improve >= 12:
                    tau = min(tau_max, tau * tau_mult)
                    iter_since_tau_bump = 0

            # Terminate if stagnating
            if no_improve > 120:
                break

        return best_h

    # 1D golden-section line search for symmetric pairwise mass transfer
    def golden_section_search(h: np.ndarray, i: int, j: int, max_iter: int = 30) -> Tuple[np.ndarray, float]:
        """
        Transfer δ from outer index j (and its mirror) to inner index i (and its mirror).
        δ ∈ [0, h_j], maintain symmetry and nonnegativity, sum preserved.

        Returns:
            (best_h, best_R)
        """
        nloc = len(h)
        assert 0 <= j < i <= (nloc // 2 - 1)

        base_R, _, _, _ = objective(h)
        delta_lo = 0.0
        delta_hi = min(h[j], h[nloc - 1 - j])

        if delta_hi <= 0:
            return h, base_R

        phi = (1 + math.sqrt(5)) / 2.0
        invphi = 1 / phi
        invphi2 = 1 / (phi * phi)

        a = delta_lo
        b = delta_hi
        cpt = a + invphi2 * (b - a)
        dpt = a + invphi * (b - a)

        def eval_R(delta: float) -> float:
            if delta <= 0:
                return base_R
            hcand = h.copy()
            jc = j
            jm = nloc - 1 - j
            ic = i
            im = nloc - 1 - i
            if delta > hcand[jc] or delta > hcand[jm]:
                return -np.inf
            hcand[jc] -= delta
            hcand[jm] -= delta
            hcand[ic] += delta
            hcand[im] += delta
            hcand = normalize(hcand)
            Rcand, _, _, _ = objective(hcand)
            return Rcand

        Rc = eval_R(cpt)
        Rd = eval_R(dpt)
        best_R = base_R
        best_h = h

        for _ in range(max_iter):
            if Rc > Rd:
                b = dpt
                dpt = cpt
                Rd = Rc
                cpt = a + invphi2 * (b - a)
                Rc = eval_R(cpt)
            else:
                a = cpt
                cpt = dpt
                Rc = Rd
                dpt = a + invphi * (b - a)
                Rd = eval_R(dpt)

        candidates = [(base_R, h)]
        for delta in [a, (a + b) / 2.0, b]:
            Rcand = eval_R(delta)
            if Rcand > -np.inf:
                hcand = h.copy()
                jc = j
                jm = nloc - 1 - j
                ic = i
                im = nloc - 1 - i
                hcand[jc] -= delta
                hcand[jm] -= delta
                hcand[ic] += delta
                hcand[im] += delta
                hcand = normalize(hcand)
                candidates.append((Rcand, hcand))

        best_R, best_h = max(candidates, key=lambda x: x[0])
        return best_h, best_R

    # Greedy local refinement: symmetric mass swaps from outer bins to inner bins
    def pairwise_refine(h: np.ndarray, max_passes: int = 6) -> np.ndarray:
        nloc = len(h)
        R0, _, _, _ = objective(h)
        best_R = R0
        best_h = h.copy()

        # Inner indices near center, outer near edges
        inner_idxs = list(range(nloc // 2 - 12, nloc // 2))  # e.g., 13..24 for n=50
        outer_idxs = list(range(0, max(1, nloc // 2 - 20)))  # e.g., 0..4 for n=50

        for _ in range(max_passes):
            improved = False
            for i in inner_idxs:
                for j in outer_idxs:
                    if i <= j:
                        continue
                    hcand, Rcand = golden_section_search(best_h, i, j)
                    if Rcand > best_R + 1e-12:
                        best_R = Rcand
                        best_h = hcand
                        improved = True
            if not improved:
                break

        return best_h

    # Generate diverse initializations
    def generate_initializations(nloc: int) -> List[np.ndarray]:
        inits: List[np.ndarray] = []

        # Uniform baseline
        inits.append(np.full(nloc, 1.0 / nloc, dtype=float))

        # Triangular (peak at center)
        tri = np.zeros(nloc, dtype=float)
        for i in range(nloc):
            dist = abs(i - (nloc - 1) / 2.0)
            tri[i] = 1.0 - dist / (nloc / 2.0)
        tri = np.maximum(tri, 1e-16)
        tri = symmetrize(tri)
        tri = normalize(tri)
        inits.append(tri)

        # Raised-cosine family
        for a in [0.45, 0.55, 0.65]:
            rc = np.zeros(nloc, dtype=float)
            for i in range(nloc):
                x = (i + 0.5) / nloc
                t = 2.0 * (x - 0.5)
                rc[i] = max(0.0, a - (1.0 - a) * math.cos(math.pi * (1.0 - abs(t))))
            rc = np.maximum(rc, 1e-16)
            rc = symmetrize(rc)
            rc = normalize(rc)
            inits.append(rc)

        # Gaussian family (symmetric)
        for sigma in [6.0, 8.0, 10.0]:
            g = np.zeros(nloc, dtype=float)
            center = (nloc - 1) / 2.0
            for i in range(nloc):
                g[i] = math.exp(-0.5 * ((i - center) / sigma) ** 2)
            g = np.maximum(g, 1e-16)
            g = symmetrize(g)
            g = normalize(g)
            inits.append(g)

        # Plateau two-level family
        max_width = max(3, nloc // 5)
        for width in [max(3, max_width - 2), max_width, max_width + 2, max_width + 4]:
            for rho in [2.0, 3.0, 4.0]:
                p = np.zeros(nloc, dtype=float)
                left = (nloc // 2) - (width // 2)
                right = left + width - 1
                base = 1.0
                high = rho * base
                for i in range(nloc):
                    p[i] = high if left <= i <= right else base
                p = symmetrize(p)
                p = normalize(p)
                inits.append(p)

        # Mild 3-step MV-like template (inner/middle/outer levels)
        mv = np.zeros(nloc, dtype=float)
        for i in range(nloc):
            d = abs(i - ((nloc - 1) / 2.0))
            if d <= (nloc * 0.08):
                mv[i] = 1.8
            elif d <= (nloc * 0.22):
                mv[i] = 1.2
            else:
                mv[i] = 1.0
        mv = symmetrize(mv)
        mv = normalize(mv)
        inits.append(mv)

        return inits

    # Upsample 25-bin heights to 50 bins by bin-splitting (mass split evenly)
    def upsample_by_splitting(h_coarse: np.ndarray, n_fine: int = 50) -> np.ndarray:
        n_coarse = len(h_coarse)
        assert n_coarse * 2 == n_fine
        h_fine = np.zeros(n_fine, dtype=float)
        for i in range(n_coarse):
            h_fine[2 * i] = 0.5 * h_coarse[i]
            h_fine[2 * i + 1] = 0.5 * h_coarse[i]
        h_fine = symmetrize(h_fine)
        h_fine = normalize(h_fine)
        h_fine = pav_unimodal(h_fine)
        return h_fine

    # Multi-resolution homotopy: coarse (n=25) -> upsample -> fine (n=50)
    def homotopy_optimize(n_coarse: int = 25, n_fine: int = 50) -> Tuple[np.ndarray, float]:
        best_R_fine = -1.0
        best_h_fine = None

        # Coarse initializations and optimization
        inits_coarse = generate_initializations(n_coarse)
        for init_c in inits_coarse:
            # Coarse mirror ascent with smooth Linf
            h_c = mirror_ascent(
                init_c,
                max_iters=1800,
                enforce_unimodal=True,
                use_smooth_linf=True,
                tau0=70.0,
                tau_mult=1.8,
                tau_max=2500.0,
                smooth_phase_iters=900,
            )
            # Upsample by bin splitting
            h_f = upsample_by_splitting(h_c, n_fine)

            # Continue fine mirror ascent with a slightly larger tau to harden quickly
            h_f = mirror_ascent(
                h_f,
                max_iters=2200,
                enforce_unimodal=True,
                use_smooth_linf=True,
                tau0=120.0,
                tau_mult=1.7,
                tau_max=2500.0,
                smooth_phase_iters=700,
            )

            # Final polish with hard Linf
            h_f = mirror_ascent(
                h_f,
                max_iters=800,
                enforce_unimodal=True,
                use_smooth_linf=False,
            )

            # Local pairwise refinement
            h_f = pairwise_refine(h_f, max_passes=6)

            R_f, _, _, _ = objective(h_f)
            if R_f > best_R_fine:
                best_R_fine = R_f
                best_h_fine = h_f

        if best_h_fine is None:
            best_h_fine = np.full(n_fine, 1.0 / n_fine, dtype=float)

        return best_h_fine, best_R_fine

    # Direct 50-bin multi-start optimization (complementary to homotopy path)
    def direct_optimize(nloc: int = 50) -> Tuple[np.ndarray, float]:
        inits = generate_initializations(nloc)
        best_R = -1.0
        best_h = None
        for init in inits:
            # Smooth phase
            h_candidate = mirror_ascent(
                init,
                max_iters=2400,
                enforce_unimodal=True,
                use_smooth_linf=True,
                tau0=80.0,
                tau_mult=1.7,
                tau_max=2500.0,
                smooth_phase_iters=900,
            )
            # Hard phase polish
            h_candidate = mirror_ascent(
                h_candidate,
                max_iters=900,
                enforce_unimodal=True,
                use_smooth_linf=False,
            )
            # Local refinement
            h_candidate = pairwise_refine(h_candidate, max_passes=6)
            R_cand, _, _, _ = objective(h_candidate)
            if R_cand > best_R:
                best_R = R_cand
                best_h = h_candidate
        if best_h is None:
            best_h = np.full(nloc, 1.0 / nloc, dtype=float)
        return best_h, best_R

    # Run homotopy optimization and direct optimization, pick the best
    best_h, best_R = homotopy_optimize(n_coarse=25, n_fine=n_target)

    h_direct, R_direct = direct_optimize(nloc=n_target)
    if R_direct > best_R:
        best_h, best_R = h_direct, R_direct

    # Final hard polish and refinement to ensure compatibility and monotone ascent
    best_h = mirror_ascent(best_h, max_iters=600, enforce_unimodal=True, use_smooth_linf=False)
    best_h = pairwise_refine(best_h, max_passes=4)

    final_R, _, _, _ = objective(best_h)

    heights = best_h.tolist()
    c_lower_bound = float(final_R)
    return heights, c_lower_bound
# EVOLVE_END


if __name__ == "__main__":
    heights, c_lower_bound = optimize_lower_bound()
    print(json.dumps({"heights": heights, "c_lower_bound": c_lower_bound}))
```
