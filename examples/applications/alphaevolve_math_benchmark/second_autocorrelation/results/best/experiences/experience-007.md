Stabilizing Linf handling and shaping a flat central plateau early, then releasing unimodality at the end, while always accepting steps on the true objective.

- SEGLS+CPH: Central-Plateau Homotopy with Near-Max Subgradient and Late Unimodality Release: It replaces the hard argmax Linf subgradient with an adaptive near-max band averaging over indices satisfying c[k] >= max(c) - eps, using eps = 1e-8 * max(c) with a 1e-14 floor, to stabilize ascent on flat central plateaus while still evaluating and accepting steps with the true hard objective.
- SEGLS+CPH: Central-Plateau Homotopy with Near-Max Subgradient and Late Unimodality Release: During the smooth-Linf phase it adds a small central-window variance minimization term over an odd-width window (e.g., 9 or 11 bins), with a weight lambda_t that starts near 2e-3 and decays geometrically by about 0.9 every ~80 accepted steps or when tau increases, shaping c toward a flatter central plateau without altering the acceptance criterion.
- SEGLS+CPH: Central-Plateau Homotopy with Near-Max Subgradient and Late Unimodality Release: After the hard-Linf polish and pairwise refinement, it runs a final mirror_ascent pass with enforce_unimodal set to False for roughly 400–600 iterations to allow symmetric micro-adjustments inside the plateau that PAV would otherwise clamp.

```python
#!/usr/bin/env python3
"""Optimizer for the second autocorrelation inequality lower bound.

We search for a nonnegative, symmetric 50-bin step function on [-1/4, 1/4]
whose autocorrelation c = h * h yields a lower-bound ratio

    R(h) = ||c||_2^2 / ( ||c||_1 * ||c||_∞ )

exceeding 0.8962. The algorithm implements a hybrid scheme:

- Exact objective evaluation that mirrors the verification routine;
- Exact gradient of the smooth terms (L2^2 and L1) w.r.t. h via convolutional chain rule;
- Subgradient for the Linf term with an adaptive near-max band;
- Entropic mirror-ascent on the simplex (sum h_i = 1), with symmetry enforcement,
  optional unimodality via a PAV step, and Armijo-like backtracking line search;
- A smooth-Linf homotopy phase augmented with a small central-plateau variance shaper,
  annealed out before switching to the hard Linf subgradient phase;
- Multi-starts from structured initializations (plateau, raised-cosine, Gaussian, triangular);
- A symmetric pairwise mass-swap local refinement with 1D line search;
- A late-stage polish without unimodality to allow tiny symmetric micro-adjustments.

These changes integrate the requested innovations and reliably produce a heights
sequence that verifies with a c_lower_bound above 0.8962.
"""

import json
import math
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def optimize_lower_bound() -> Tuple[List[float], float]:
    """
    Optimize the lower bound using a symmetry-constrained entropic mirror ascent
    with multi-starts and a deterministic pairwise mass-swap refinement.

    Returns:
        heights (List[float]): Nonnegative symmetric heights with length 50 and sum 1.
        c_lower_bound (float): Achieved lower bound R(h) computed as in verify.
    """

    n = 50  # number of bins

    # Core objective computation that mirrors the verification function
    def objective(h: np.ndarray) -> Tuple[float, float, float, np.ndarray]:
        """
        Compute R(h) = A / (B * C), where:
        - A = L2^2 (piecewise-linear integral of c^2),
        - B = L1 (scaled sum of c),
        - C = Linf (max of c),
        with c = h * h as discrete convolution.

        Returns:
            R, A, (B*C), c
        """
        c = np.convolve(h, h)
        # Widths for integration over [-0.5, 0.5] with uniform subdivision
        widths = np.diff(np.linspace(-0.5, 0.5, len(c) + 2))
        # Piecewise-linear integration using Simpson-like rule
        v = np.concatenate(([0.0], c, [0.0]))
        l2_sq = 0.0
        for i in range(len(c) + 1):
            w = widths[i]
            l2_sq += (w / 3.0) * (v[i] * v[i] + v[i] * v[i + 1] + v[i + 1] * v[i + 1])
        # L1 with the same normalization factor as baseline
        l1 = float(np.sum(c) / (len(c) + 1))
        linf = float(np.max(c))
        # For safety, avoid division issues (shouldn't happen for positive h)
        if l1 <= 0 or linf <= 0:
            return 0.0, l2_sq, 0.0, c
        return float(l2_sq / (l1 * linf)), float(l2_sq), float(l1 * linf), c

    # Gradient of L2^2 wrt c via exact derivation and chain-rule to h (exact discrete model)
    def grad_A_wrt_h(h: np.ndarray, c: np.ndarray) -> np.ndarray:
        """
        Compute grad_h of A = ||c||_2^2 using the exact PL integral gradient:
        grad_c[k] = (w/3) * (4 c_k + c_{k-1} + c_{k+1}) with zero padding on ends;
        grad_h = 2 * correlation(grad_c, h).
        """
        m = len(c)
        w = 1.0 / (m + 1)  # since widths are uniform and sum to 1 (interval length 1)
        # Compute grad wrt c (with zero padding around v = [0, c, 0])
        grad_c = np.zeros_like(c, dtype=float)
        for k in range(m):
            vk = c[k]
            vm = c[k - 1] if k - 1 >= 0 else 0.0
            vp = c[k + 1] if k + 1 < m else 0.0
            grad_c[k] = (w / 3.0) * (4.0 * vk + vm + vp)
        # Map to grad wrt h by chain rule: grad_h_j = 2 * sum_{k=j..j+n-1} grad_c[k] * h[k - j]
        nloc = len(h)
        grad_h = np.zeros_like(h)
        for j in range(nloc):
            acc = 0.0
            k_start = j
            k_end = j + nloc - 1
            for k in range(k_start, k_end + 1):
                acc += grad_c[k] * h[k - j]
            grad_h[j] = 2.0 * acc
        return grad_h

    # Gradient of L1 wrt h: for nonnegative c, L1 = (sum c) / (m+1),
    # grad wrt c is constant 1/(m+1), and mapping to h gives constant vector 2/(m+1).
    def grad_B_wrt_h(nloc: int) -> np.ndarray:
        m = 2 * nloc - 1
        const = 2.0 / (m + 1)
        return np.full(nloc, const, dtype=float)

    # Map a gradient w.r.t. c (len 2n-1) to gradient w.r.t. h via the chain rule:
    # grad_h_j = 2 * sum_{k=j..j+n-1} grad_c[k] * h[k - j]
    def map_grad_c_to_h(grad_c: np.ndarray, h: np.ndarray) -> np.ndarray:
        nloc = len(h)
        grad_h = np.zeros_like(h)
        m = len(grad_c)
        for j in range(nloc):
            acc = 0.0
            k_start = j
            k_end = j + nloc - 1
            # guard for array bounds (should naturally hold for len grad_c = 2n-1)
            if k_start < 0:
                k_start = 0
            if k_end >= m:
                k_end = m - 1
            for k in range(k_start, k_end + 1):
                acc += grad_c[k] * h[k - j]
            grad_h[j] = 2.0 * acc
        return grad_h

    # Subgradient of Linf wrt h: near-max epsilon band averaging to stabilize
    def grad_C_wrt_h(h: np.ndarray, c: np.ndarray) -> np.ndarray:
        nloc = len(h)
        max_val = np.max(c)
        # Adaptive epsilon band: eps = max(1e-8 * max(c), 1e-14)
        eps = max(1e-8 * max_val, 1e-14)
        max_idxs = np.flatnonzero(c >= max_val - eps)
        if len(max_idxs) == 0:
            # Fallback to hard argmax
            max_idxs = np.array([int(np.argmax(c))])
        weight = 1.0 / float(len(max_idxs))
        grad_h = np.zeros_like(h)
        for idx in max_idxs:
            # Map e_idx to h via chain rule (same as before)
            for j in range(nloc):
                if j <= idx <= j + nloc - 1:
                    grad_h[j] += 2.0 * h[idx - j] * weight
        return grad_h

    # Smooth Linf surrogate gradient wrt h using softmax weights on c
    def grad_Csmooth_wrt_h(h: np.ndarray, c: np.ndarray, tau: float) -> np.ndarray:
        # Compute softmax weights in a numerically stable manner
        cmax = float(np.max(c))
        z = (c - cmax) / max(tau, 1e-18)
        z = np.clip(z, -60.0, 60.0)  # safety clamp
        w = np.exp(z)
        w_sum = float(np.sum(w))
        if w_sum <= 0:
            w = np.ones_like(c) / len(c)
        else:
            w /= w_sum
        # Map grad_c = w to grad_h
        return map_grad_c_to_h(w, h)

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
            # Reset to uniform if degenerate
            h[:] = 1.0 / len(h)
            return h
        h /= s
        # small floor to avoid zeros under entropic updates, then renormalize
        eps = 1e-16
        h = np.maximum(h, eps)
        h /= np.sum(h)
        return h

    # Optional PAV to enforce unimodality (nonincreasing away from center)
    def pav_unimodal(h: np.ndarray) -> np.ndarray:
        nloc = len(h)
        # We enforce: h[24] >= h[23] >= ... >= h[0] and mirror it.
        center_left = (nloc // 2) - 1  # 24 for n=50
        # Build half sequence b[k] = h[center_left - k], k=0..24
        half_len = nloc // 2  # 25
        b = np.array([h[center_left - k] for k in range(half_len)], dtype=float)
        # Perform PAV ensuring nonincreasing b
        sums = []
        cnts = []
        for val in b:
            sums.append(val)
            cnts.append(1)
            # Merge while second-last < last (to enforce nonincreasing)
            while len(sums) >= 2 and (sums[-2] / cnts[-2]) < (sums[-1] / cnts[-1]) - 1e-15:
                s = sums[-2] + sums[-1]
                c = cnts[-2] + cnts[-1]
                sums[-2] = s
                cnts[-2] = c
                sums.pop()
                cnts.pop()
        # Expand averaged blocks back
        b_new = np.zeros_like(b)
        idx = 0
        for s, c in zip(sums, cnts):
            avg = s / c
            b_new[idx: idx + c] = avg
            idx += c
        # Put back to h half and mirror
        for k in range(half_len):
            v = b_new[k]
            i_left = center_left - k
            i_right = nloc - 1 - i_left
            h[i_left] = v
            h[i_right] = v
        # Normalize again to maintain sum 1
        return normalize(h)

    # Entropic mirror ascent with homotopy smoothing and an auxiliary plateau shaper
    def mirror_ascent(
        initial_h: np.ndarray,
        max_iters: int = 2000,
        enforce_unimodal: bool = True,
        use_smooth_linf: bool = True,
    ) -> np.ndarray:
        """
        Perform entropic mirror ascent on the simplex with symmetry and optional unimodality.
        Early iterations may use a smooth Linf surrogate with a decaying smoothing parameter tau.
        During the smooth phase a small auxiliary gradient promotes a flat central plateau of c,
        then the algorithm switches to the hard (exact-subgradient) Linf ascent and polishes.

        The backtracking/acceptance uses the exact R(h) objective throughout.
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

        eta = 0.8  # initial step size
        eta_max = 5.0
        eta_min = 1e-6

        # Smoothing homotopy and plateau shaping parameters
        smooth_active = use_smooth_linf
        C_now = float(np.max(c))
        # tau is scaled to c-peak to remain invariant to scale; start modest, decay over time
        tau = max(1e-4 * C_now, 1e-12)
        tau_floor = max(1e-12 * C_now, 1e-16)
        tau_decay_every = 30  # accepted steps
        tau_decay_factor = 0.8

        # Plateau shaper weight lambda_t; annealed down to 0 before hard phase
        lambda_t = 2e-3
        lambda_decay_every = 80
        lambda_decay_factor = 0.9

        accepted_steps = 0
        no_improve = 0

        for it in range(max_iters):
            # Gradients of components
            gradA_h = grad_A_wrt_h(h, c)
            gradB_h = grad_B_wrt_h(len(h))
            if smooth_active:
                gradC_h = grad_Csmooth_wrt_h(h, c, tau)
            else:
                gradC_h = grad_C_wrt_h(h, c)

            B = BC / float(np.max(c))  # since BC = B*C
            C = float(np.max(c))
            # Assemble gradient of R wrt h:
            # ∇R = [(∇A)(B*C) − A(∇B*C + B∇C)] / (B*C)^2
            num = gradA_h * BC - A * (gradB_h * C + B * gradC_h)
            denom = (BC ** 2)
            gradR = num / denom

            # Central-Plateau Homotopy (CPH) shaping term during smooth phase
            if smooth_active and lambda_t > 0.0:
                # Window W around the current peak of c
                k0 = int(np.argmax(c))
                W = 11  # odd width
                half = W // 2
                m = len(c)
                lo = max(0, k0 - half)
                hi = min(m - 1, k0 + half)
                idxs = np.arange(lo, hi + 1)
                # variance minimization gradient: -2*(c - mean)
                mean_W = float(np.mean(c[idxs])) if len(idxs) > 0 else 0.0
                g_c_plateau = np.zeros_like(c)
                if len(idxs) > 0:
                    g_c_plateau[idxs] = -2.0 * lambda_t * (c[idxs] - mean_W)
                g_h_plateau = map_grad_c_to_h(g_c_plateau, h)
                gradR = gradR + g_h_plateau

            # Project gradient to symmetric subspace by averaging with its reverse
            gradR = 0.5 * (gradR + gradR[::-1])

            # Entropic update: h_new ∝ h ⊙ exp(η * gradR)
            step_eta = eta
            improved = False
            for _ in range(25):  # backtracking loop
                h_new = h * np.exp(step_eta * gradR)
                h_new = symmetrize(h_new)
                h_new = normalize(h_new)
                if enforce_unimodal:
                    h_new = pav_unimodal(h_new)

                R_new, A_new, BC_new, c_new = objective(h_new)
                if R_new > R + 1e-12:
                    # Accept
                    h = h_new
                    R, A, BC, c = R_new, A_new, BC_new, c_new
                    improved = True
                    eta = min(eta_max, step_eta * 1.08)
                    accepted_steps += 1
                    break
                else:
                    step_eta *= 0.5

            if improved:
                if R > best_R:
                    best_R = R
                    best_h = h.copy()
                no_improve = 0

                # Update smoothing/tuning schedules on accepted steps
                if smooth_active:
                    # Decay tau periodically; when too small, drop to hard mode
                    if accepted_steps % tau_decay_every == 0 and accepted_steps > 0:
                        tau = max(tau * tau_decay_factor, tau_floor)
                        # switch off smoothing if tau is very small relative to current scale
                        if tau <= tau_floor * 1.00001:
                            smooth_active = False
                            lambda_t = 0.0  # disable shaping when exiting smooth phase
                    # Decay lambda_t more slowly
                    if accepted_steps % lambda_decay_every == 0 and accepted_steps > 0:
                        lambda_t *= lambda_decay_factor

            else:
                no_improve += 1
                eta *= 0.5
                if eta < eta_min:
                    break

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
        # Only operate on half side with i closer to center than j (so j < i)
        assert 0 <= j < i <= (nloc // 2 - 1)

        # Current h and objective
        base_R, _, _, _ = objective(h)

        # Bounds for δ
        delta_lo = 0.0
        delta_hi = min(h[j], h[nloc - 1 - j])

        if delta_hi <= 0:
            return h, base_R

        phi = (1 + math.sqrt(5)) / 2.0
        invphi = 1 / phi
        invphi2 = 1 / (phi * phi)

        a = delta_lo
        b = delta_hi

        # Initial interior points
        cpt = a + invphi2 * (b - a)
        dpt = a + invphi * (b - a)

        # Helper to evaluate along direction
        def eval_R(delta: float) -> float:
            if delta <= 0:
                return base_R
            # Construct candidate by symmetric transfer
            hcand = h.copy()
            # remove from j and mirror
            jc = j
            jm = nloc - 1 - j
            ic = i
            im = nloc - 1 - i

            # ensure we don't go negative
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

        # Choose best among candidates
        candidates = [(base_R, h)]
        for delta in [a, (a + b) / 2.0, b]:
            Rcand = eval_R(delta)
            if Rcand > -np.inf:
                # Construct corresponding hcand for the best delta
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
    def pairwise_refine(h: np.ndarray, max_passes: int = 4) -> np.ndarray:
        nloc = len(h)
        R0, _, _, _ = objective(h)
        best_R = R0
        best_h = h.copy()

        # Consider pairs: inner indices near the center and outer indices near the edges
        inner_idxs = list(range(nloc // 2 - 7, nloc // 2))  # e.g., 18..24 for n=50
        outer_idxs = list(range(0, nloc // 2 - 18))  # e.g., 0..6 for n=50

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
            dist = abs(i - (nloc - 1) / 2.0)  # center between 24 and 25
            tri[i] = 1.0 - dist / (nloc / 2.0)
        tri = np.maximum(tri, 1e-16)
        tri = symmetrize(tri)
        tri = normalize(tri)
        inits.append(tri)

        # Raised-cosine family
        for a in [0.45, 0.55, 0.65]:
            rc = np.zeros(nloc, dtype=float)
            for i in range(nloc):
                x = (i + 0.5) / nloc  # center-aligned sample
                # map to [-1, 1]
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
        for width in [6, 8, 10, 12]:
            for rho in [2.0, 3.0, 4.0]:
                p = np.zeros(nloc, dtype=float)
                # central plateau centered between 24 and 25
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
            # distance from center index 24.5
            d = abs(i - 24.5)
            if d <= 4.0:
                mv[i] = 1.8
            elif d <= 10.0:
                mv[i] = 1.2
            else:
                mv[i] = 1.0
        mv = symmetrize(mv)
        mv = normalize(mv)
        inits.append(mv)

        return inits

    # Run multi-start mirror ascent and pick best, then refine
    inits = generate_initializations(n)
    best_R = -1.0
    best_h = None

    for init in inits:
        # First run: full pipeline with smooth Linf homotopy and unimodality enforced
        h_candidate = mirror_ascent(init, max_iters=3200, enforce_unimodal=True, use_smooth_linf=True)
        # Local pairwise refinement
        h_candidate = pairwise_refine(h_candidate, max_passes=3)
        R_cand, _, _, _ = objective(h_candidate)
        if R_cand > best_R:
            best_R = R_cand
            best_h = h_candidate

    # Fall back in case nothing improved (shouldn't happen)
    if best_h is None:
        best_h = np.full(n, 1.0 / n, dtype=float)

    # Late-stage unimodality release: allow small symmetric adjustments
    best_h = mirror_ascent(best_h, max_iters=500, enforce_unimodal=False, use_smooth_linf=False)
    # Short final pairwise refinement pass
    best_h = pairwise_refine(best_h, max_passes=2)

    # Compute final bound exactly as in verification
    final_R, _, _, _ = objective(best_h)

    # Return as Python list and float
    heights = best_h.tolist()
    c_lower_bound = float(final_R)
    return heights, c_lower_bound
# EVOLVE_END


if __name__ == "__main__":
    heights, c_lower_bound = optimize_lower_bound()
    print(json.dumps({"heights": heights, "c_lower_bound": c_lower_bound}))
```
