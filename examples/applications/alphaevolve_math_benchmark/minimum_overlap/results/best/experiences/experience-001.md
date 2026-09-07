A stage-annealed, symmetric, plateau-aware equiripple micro-step blended between uniform and weighted peak equalization, applied immediately after each projected softmax-gradient update with norm-aware scaling and exact water-filling projection, was key to stabilizing corrections while suppressing the tallest correlation peaks in the mirrored sequence objective.

- Symmetric Weighted Equiripple with Uniform Blend (SWEB): After each projected softmax-gradient update, SWEB applies a sign-paired, plateau-aware equiripple micro-step that blends uniform and weighted peak equalization with a stage-increasing β and norm-aware scaling, then re-projects with water-filling while pinning b[-1]=0.5 and enforcing sum(b)=n/2, targeting all near-max correlation shifts to reduce oscillations and directional bias.
- Symmetric Weighted Equiripple with Uniform Blend (SWEB): In this run, SWEB produced a valid sequence (validity 1.0) with an upper bound of 0.3821837334379626 and score 0.9967117034870702 (eval_time 2.097075874917209) under the provided compute_upper_bound, demonstrating that the symmetric, plateau-aware blended micro-step maintained feasibility while lowering peak overlaps.

```python
#!/usr/bin/env python3
"""Minimum-overlap problem: mirror-projected minimax optimizer.

This implements a mirror-aware projected minimax (soft-max) optimization to
design a half-sequence that, after mirroring as described in the prompt, yields
a small maximum overlap (upper bound) value.

We directly optimize the exact objective used by the downstream
compute_upper_bound function on the final mirrored sequence:
  UB = max_k sum_i s_i * (1 - s_{i+k}) / (len(s)) * 2

Key elements (SWEB: Symmetric Weighted Equiripple Blend):
- Mirror assembly: final sequence s is built as concat(b[:-1], reversed(b)),
  where b is the "half" sequence we optimize.
- Softmax smoothing of the max convolution peak to get informative gradients.
- Exact gradient computation using correlation/convolution identities.
- Projected gradient descent:
  - Projection onto [0,1]^n with an exact water-filling projection onto the
    hyperplane sum(b) = n/2 and pinning the last element b[-1] = 0.5.
- Mild smoothing between iterations to avoid oscillations (decreasing strength).
- Symmetric, weighted, plateau-aware equiripple micro-step:
  after each main update and projection, detect paired top offending correlation
  peaks (+/-k, optionally k=0), blend uniform and weighted equalization, and
  apply a small corrective step with norm-aware scaling, then re-project.

Annealed stages progressively sharpen the softmax toward the true max while
decreasing the step size. The algorithm tracks the best snapshot and returns
the optimized half-sequence. The downstream script constructs the final mirrored
sequence, verifies constraints, and computes the upper bound.
"""

import json
import numpy as np


# EVOLVE_START
def generate_erdos_data() -> np.ndarray:
    """Generates a sequence of coefficients for Erdős' minimum overlap problem.

    Returns:
        np.ndarray: The half-sequence b of length n. The downstream caller will
                    build the final mirrored sequence s as:
                        s = concat(b[:-1], reversed(b)).
                    We ensure:
                      - 0 <= b[i] <= 1 for all i
                      - sum(b) = n / 2
                      - b[-1] = 0.5
    """
    np.seterr(all="ignore")

    # -------------------------
    # Helper functions
    # -------------------------

    def assemble_sequence(b: np.ndarray) -> np.ndarray:
        # s = concat(b[:-1], reversed(b))
        return np.concatenate([b[:-1], b[::-1]])

    def max_overlap_value(s: np.ndarray) -> float:
        # Same objective as compute_upper_bound in the prompt:
        # UB = max_k corr(s, 1 - s, 'full') / len(s) * 2
        conv_vals = np.correlate(s, 1 - s, mode="full")
        return np.max(conv_vals) / len(s) * 2.0

    def softmax_weights(c: np.ndarray, tau: float) -> np.ndarray:
        # Numerically stable softmax over the correlation vector "c"
        c_max = np.max(c)
        z = (c - c_max) / max(tau, 1e-12)
        z = np.clip(z, -60.0, 60.0)  # prevent overflow
        expz = np.exp(z)
        s = np.sum(expz)
        if s <= 0:
            # Fallback to uniform if numerical issues
            return np.full_like(expz, 1.0 / len(expz))
        return expz / s

    def compute_gradient_s(s: np.ndarray, w: np.ndarray) -> np.ndarray:
        """Compute gradient wrt s using softmax weights w over shifts.

        c_k = sum_i s_i * (1 - s_{i+k})
        ∂c_k/∂s_j = [1 - s_{j+k}]_valid - [s_{j-k}]_valid

        For J_tau(s) = τ log sum_k exp(c_k/τ), gradient is sum_k w_k ∂c_k/∂s.

        Efficient computation via convolution identities:
          - Let L = len(s), w_shift of length 2L-1 indexed by k ∈ [-(L-1)..(L-1)]
          - termA[j] = sum_{k: 0≤j+k<L} w_k
                      = windowed sum over w of length L starting at a = L-1-j
          - termB[j] = sum_k w_k s_{j+k}
          - termC[j] = sum_k w_k s_{j-k}
          Using linear convolution, for arrays of unequal lengths we obtain the
          exact L-length results by slicing the 'full' convolution:
            termB = (w[::-1] * s)[L-1 : 2L-1]
            termC = (w * s)[L-1 : 2L-1]
        """
        L = len(s)
        # w is length 2L-1 with zero shift at index L-1
        w_shift = w

        # termA: sliding window sums of w of width L; window start a = L-1-j
        wc = np.cumsum(w_shift)
        termA = np.empty(L, dtype=float)
        for j in range(L):
            start = (L - 1) - j
            end = (2 * L - 2) - j
            # start ∈ [0, L-1], end ∈ [L-1, 2L-2]
            if start == 0:
                sum_w = wc[end]
            else:
                sum_w = wc[end] - wc[start - 1]
            termA[j] = sum_w

        # termB and termC via 'full' convolution with proper slicing to get length L
        # See docstring for derivation of slice indices.
        w_flip = w_shift[::-1]
        conv_wflip_s_full = np.convolve(w_flip, s, mode="full")
        termB = conv_wflip_s_full[L - 1 : 2 * L - 1]

        conv_w_s_full = np.convolve(w_shift, s, mode="full")
        termC = conv_w_s_full[L - 1 : 2 * L - 1]

        g_s = termA - termB - termC
        return g_s

    def map_grad_s_to_b(g_s: np.ndarray, n: int) -> np.ndarray:
        """Map gradient from final sequence s back to half-sequence b.

        b indices: 0..n-1
        s length L = 2n-1, with
          s[i] = b[i] for i in [0..n-2]
          s[n-1] = b[n-1]
          s[L-1 - i] = b[i] for i in [0..n-2]

        Therefore:
          ∂J/∂b[i] = ∂J/∂s[i] + ∂J/∂s[L-1-i]  for i < n-1
          ∂J/∂b[n-1] = ∂J/∂s[n-1]
        """
        L = 2 * n - 1
        g_b = np.zeros(n, dtype=float)
        for i in range(n - 1):
            g_b[i] = g_s[i] + g_s[L - 1 - i]
        g_b[n - 1] = g_s[n - 1]
        return g_b

    def project_box_sum(b: np.ndarray, target_sum: float, fixed_idx: int, fixed_val: float) -> np.ndarray:
        """Project onto intersection of [0,1]^n, sum(b) = target_sum, and b[fixed_idx] = fixed_val.

        Exact L2 projection via water-filling:
          Find lambda such that x_i = clip(v_i + lambda, 0, 1) for i != fixed_idx
          and sum_i x_i + fixed_val = target_sum.

        This procedure iteratively peels off saturated indices.
        """
        n = len(b)
        v = np.array(b, dtype=float, copy=True)

        # Pin the fixed index exactly
        v[fixed_idx] = fixed_val

        # Saturated set mask (false means free)
        sat_mask = np.zeros(n, dtype=bool)
        sat_mask[fixed_idx] = True

        # Values to return
        x = v.copy()

        target_wo_fixed = target_sum - fixed_val

        # Iteratively saturate violators and solve for lambda on remaining free set
        for _ in range(5 * n):
            free_idx = np.where(~sat_mask)[0]
            if free_idx.size == 0:
                # Nothing left to adjust
                return x

            # Sum of current saturated (non-fixed) entries:
            sat_nonfixed_sum = np.sum(x[sat_mask & (np.arange(n) != fixed_idx)])

            # Solve lambda assuming no further saturation: x_i = v_i + lambda
            lambda_val = (target_wo_fixed - sat_nonfixed_sum - np.sum(v[free_idx])) / float(free_idx.size)
            y = v[free_idx] + lambda_val

            below = y < 0.0
            above = y > 1.0

            if not below.any() and not above.any():
                x[free_idx] = y
                return x

            # Saturate the violators and mark them
            if below.any():
                idxs = free_idx[below]
                x[idxs] = 0.0
                sat_mask[idxs] = True
            if above.any():
                idxs = free_idx[above]
                x[idxs] = 1.0
                sat_mask[idxs] = True

        # Fallback (should not be hit): distribute uniformly over non-fixed entries
        x = np.clip(v, 0.0, 1.0)
        x[fixed_idx] = fixed_val
        delta = target_sum - np.sum(x)
        if abs(delta) > 1e-12:
            free_mask = np.ones(n, dtype=bool)
            free_mask[fixed_idx] = False
            cnt = np.sum(free_mask)
            if cnt > 0:
                x[free_mask] = np.clip(x[free_mask] + delta / cnt, 0.0, 1.0)
        return x

    def select_symmetric_plateau_peaks(c: np.ndarray, L: int, K_pairs: int, eps: float) -> np.ndarray:
        """Select a symmetric set of shifts for equalization.

        - Build sign-paired set: for each |k|>0 include both +k and -k; include k=0 at most once.
        - Choose up to K_pairs pairs by descending c_k.
        - Plateau expansion: include any shifts with c_k >= max(c) - eps, preserving ± symmetry.

        Args:
            c: correlation array of length 2L-1 (full mode), zero shift at index L-1
            L: length of s
            K_pairs: number of sign-pairs to include (not counting k=0)
            eps: plateau envelope

        Returns:
            np.ndarray of integer shifts k.
        """
        center = L - 1
        # Indices sorted by descending c
        idx_sorted = np.argsort(c)[::-1]
        sel = set()
        pairs = 0

        # Helper to add a pair
        def add_pair(k: int):
            nonlocal pairs
            if k == 0:
                sel.add(0)
                return
            if k in sel or (-k) in sel:
                # already added as part of some pair
                return
            sel.add(k)
            sel.add(-k)
            pairs += 1

        # First pass: take top pairs
        for p in idx_sorted:
            if pairs >= K_pairs:
                break
            k = int(p - center)
            if k == 0:
                # include k=0 if very top but don't count toward pair budget
                if 0 not in sel:
                    add_pair(0)
                continue
            add_pair(k)

        # Plateau expansion: include any shifts within eps of the max
        c_max = c[idx_sorted[0]] if idx_sorted.size > 0 else -np.inf
        thresh = c_max - eps
        for p in range(c.size):
            if c[p] >= thresh:
                k = int(p - center)
                if k == 0:
                    sel.add(0)
                else:
                    sel.add(k)
                    sel.add(-k)

        # Return sorted unique shifts
        shifts = np.array(sorted(sel), dtype=int)
        return shifts

    def compute_eq_grad_s_weighted(s: np.ndarray, shifts: np.ndarray, weights: np.ndarray) -> np.ndarray:
        """Compute weighted equalization gradient on s from selected shifts.

        For each selected shift k:
          ∂c_k/∂s_j = [1 - s_{j+k}]_valid - [s_{j-k}]_valid

        We weight across selected k by weights[k].
        """
        L = len(s)
        g = np.zeros(L, dtype=float)
        if len(shifts) == 0:
            return g
        for k, w in zip(shifts, weights):
            kk = int(k)
            if kk >= 0:
                # j+k valid -> j in [0, L-1-kk]
                if L - kk > 0:
                    g[: L - kk] += w * (1.0 - s[kk:])
                    g[kk:] -= w * s[: L - kk]
            else:
                kneg = -kk
                if L - kneg > 0:
                    g[kneg:] += w * (1.0 - s[: L - kneg])
                    g[: L - kneg] -= w * s[kneg:]
        return g

    # -------------------------
    # Optimization parameters
    # -------------------------

    n = 129  # Half-length; final length L = 2n - 1
    L = 2 * n - 1

    # Initialize b as a smooth sigmoid ramp, then project to constraints.
    t = np.linspace(-4.0, 4.0, n)
    b = 1.0 / (1.0 + np.exp(-t))
    # Ensure last element exactly 0.5 to satisfy mirror sum constraint later
    b[-1] = 0.5
    # Project to sum n/2 and box constraints
    b = project_box_sum(b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)

    # Stage schedule: (tau, iterations, step_factor, K_pairs, beta, alpha_frac)
    # eta per step will be (step_factor / L)
    stages = [
        (2.0, 200, 0.80, 8, 0.25, 0.35),
        (1.0, 260, 0.60, 12, 0.50, 0.30),
        (0.5, 300, 0.45, 16, 0.75, 0.20),
        (0.25, 350, 0.33, 22, 0.90, 0.12),
    ]

    # Mild smoothing kernels per stage (weaken smoothing later)
    smooth_kernels = [
        np.array([0.06, 0.88, 0.06]),
        np.array([0.05, 0.90, 0.05]),
        np.array([0.04, 0.92, 0.04]),
        np.array([0.03, 0.94, 0.03]),
    ]

    # Track best solution found
    s = assemble_sequence(b)
    best_b = b.copy()
    best_ub = max_overlap_value(s)

    # Main optimization loop
    for stage_idx, (tau, iters, step_mul, K_pairs, beta, alpha_frac) in enumerate(stages):
        eta = (step_mul / L)
        kern = smooth_kernels[stage_idx]
        for _ in range(iters):
            # Assemble mirrored sequence s
            s = assemble_sequence(b)

            # Compute correlation and softmax weights
            c = np.correlate(s, 1 - s, mode="full")  # length 2L - 1
            w = softmax_weights(c, tau)

            # Gradient wrt s and map back to b
            g_s = compute_gradient_s(s, w)
            g_b = map_grad_s_to_b(g_s, n)

            # Scale step adaptively by gradient norm to stabilize updates
            g_scale = max(1e-10, np.linalg.norm(g_b) / np.sqrt(n))
            step = eta / g_scale

            # Update with projected gradient step
            b = b - step * g_b

            # Smooth slightly to damp oscillations (exclude last pinned element, re-pin)
            sm = b.copy()
            # interior smoothing
            sm[1:-1] = kern[0] * b[0:-2] + kern[1] * b[1:-1] + kern[2] * b[2:]
            # edges (soften a bit)
            sm[0] = (kern[1] * b[0] + kern[2] * b[1]) / (kern[1] + kern[2])
            sm[-2] = kern[0] * b[-3] + kern[1] * b[-2] + kern[2] * b[-1]
            sm[-1] = 0.5  # exact pin
            b = sm

            # Project onto box and sum-hyperplane with fixed last element
            b = np.clip(b, 0.0, 1.0)
            b[-1] = 0.5
            b = project_box_sum(b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)

            # -------------------------
            # Symmetric weighted equiripple micro-step (SWEB)
            # -------------------------
            # Select symmetric top peaks with plateau expansion using pre-update c
            eps_plateau = 0.6 * tau  # proportional to current temperature
            shifts = select_symmetric_plateau_peaks(c, L, K_pairs=K_pairs, eps=eps_plateau)

            # Compute weights for selected shifts via sharp softmax; then blend with uniform
            if shifts.size > 0:
                # Extract c values at selected shifts
                center = L - 1
                sel_idx = shifts + center
                c_sel = c[sel_idx]

                # Weighted component
                tau_eq = max(0.08, 0.5 * tau)
                w_sel = softmax_weights(c_sel, tau_eq)

                # Clip and renormalize to avoid over-concentration
                K_sel = len(w_sel)
                wmin = 0.05 / max(1, K_sel)
                wmax = 0.60
                w_sel = np.clip(w_sel, wmin, wmax)
                w_sel = w_sel / np.sum(w_sel)

                # Uniform component
                w_uni = np.full(K_sel, 1.0 / K_sel, dtype=float)

                # Blend
                w_mix = (1.0 - beta) * w_uni + beta * w_sel
                w_mix = w_mix / np.sum(w_mix)

                # Compute equalization grad on current s (after main projection)
                s_now = assemble_sequence(b)
                g_s_eq = compute_eq_grad_s_weighted(s_now, shifts, w_mix)
                g_b_eq = map_grad_s_to_b(g_s_eq, n)

                # Norm-aware corrective step
                eq_norm = max(1e-12, np.linalg.norm(g_b_eq))
                main_norm = max(1e-12, np.linalg.norm(g_b))
                scale_ratio = min(1.5, main_norm / eq_norm)
                alpha = alpha_frac * eta * scale_ratio

                # Apply micro-step and re-project
                b = b - alpha * g_b_eq
                b = np.clip(b, 0.0, 1.0)
                b[-1] = 0.5
                b = project_box_sum(b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)

                # Optional light post-correction smoothing (very light)
                if stage_idx <= 1:
                    b[1:-1] = 0.975 * b[1:-1] + 0.0125 * (b[0:-2] + b[2:])
                    b[-1] = 0.5
                    b = project_box_sum(b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)

            # Track best
            s = assemble_sequence(b)
            ub = max_overlap_value(s)
            if ub < best_ub:
                best_ub = ub
                best_b = b.copy()

    # Final polishing: plateau-aware equiripple-only steps at low temperature
    tau = 0.20
    eta = (0.25 / L)
    for _ in range(220):
        s = assemble_sequence(b)
        c = np.correlate(s, 1 - s, mode="full")
        # Select broader plateau but moderate K
        shifts = select_symmetric_plateau_peaks(c, L, K_pairs=18, eps=0.4 * tau)
        if shifts.size == 0:
            break
        center = L - 1
        c_sel = c[shifts + center]
        tau_eq = max(0.06, 0.5 * tau)
        w_sel = softmax_weights(c_sel, tau_eq)
        K_sel = len(w_sel)
        wmin = 0.04 / max(1, K_sel)
        wmax = 0.65
        w_sel = np.clip(w_sel, wmin, wmax)
        w_sel = w_sel / np.sum(w_sel)
        # Blend with strong weighted focus in polish
        beta = 0.92
        w_uni = np.full(K_sel, 1.0 / K_sel, dtype=float)
        w_mix = (1.0 - beta) * w_uni + beta * w_sel
        w_mix = w_mix / np.sum(w_mix)

        # Compute gradient and take a small step
        s_now = assemble_sequence(b)
        g_s_eq = compute_eq_grad_s_weighted(s_now, shifts, w_mix)
        g_b_eq = map_grad_s_to_b(g_s_eq, n)
        g_norm = max(1e-10, np.linalg.norm(g_b_eq) / np.sqrt(n))
        step = (eta / g_norm)
        b = b - step * g_b_eq

        # Project
        b = np.clip(b, 0.0, 1.0)
        b[-1] = 0.5
        b = project_box_sum(b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)

        # Track best
        s = assemble_sequence(b)
        ub = max_overlap_value(s)
        if ub < best_ub:
            best_ub = ub
            best_b = b.copy()

    # Return the best found half-sequence as a NumPy array
    # Ensure pin and sum constraints exactly (final polish)
    best_b[-1] = 0.5
    best_b = project_box_sum(best_b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)
    return best_b
# EVOLVE_END


if __name__ == "__main__":
    # Note: JSON cannot serialize NumPy arrays directly; convert to list for printing.
    hs = generate_erdos_data()
    try:
        serializable = hs.tolist()
    except Exception:
        serializable = list(map(float, hs))
    print(json.dumps({"half_sequence": serializable}))
```

$$
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
$$

$$
0.379005 \leq C_5 \leq 0.380927
$$

```python
#!/usr/bin/env python3
"""Minimum-overlap problem: mirror-projected minimax optimizer.

This implements a mirror-aware projected minimax (soft-max) optimization to
design a half-sequence that, after mirroring as described in the prompt, yields
a small maximum overlap (upper bound) value.

We directly optimize the exact objective used by the downstream
compute_upper_bound function on the final mirrored sequence:
  UB = max_k sum_i s_i * (1 - s_{i+k}) / (len(s)) * 2

Key elements (SWEB: Symmetric Weighted Equiripple Blend):
- Mirror assembly: final sequence s is built as concat(b[:-1], reversed(b)),
  where b is the "half" sequence we optimize.
- Softmax smoothing of the max convolution peak to get informative gradients.
- Exact gradient computation using correlation/convolution identities.
- Projected gradient descent:
  - Projection onto [0,1]^n with an exact water-filling projection onto the
    hyperplane sum(b) = n/2 and pinning the last element b[-1] = 0.5.
- Mild smoothing between iterations to avoid oscillations (decreasing strength).
- Symmetric, weighted, plateau-aware equiripple micro-step:
  after each main update and projection, detect paired top offending correlation
  peaks (+/-k, optionally k=0), blend uniform and weighted equalization, and
  apply a small corrective step with norm-aware scaling, then re-project.

Annealed stages progressively sharpen the softmax toward the true max while
decreasing the step size. The algorithm tracks the best snapshot and returns
the optimized half-sequence. The downstream script constructs the final mirrored
sequence, verifies constraints, and computes the upper bound.
"""

import json
import numpy as np


# EVOLVE_START
def generate_erdos_data() -> np.ndarray:
    """Generates a sequence of coefficients for Erdős' minimum overlap problem.

    Returns:
        np.ndarray: The half-sequence b of length n. The downstream caller will
                    build the final mirrored sequence s as:
                        s = concat(b[:-1], reversed(b)).
                    We ensure:
                      - 0 <= b[i] <= 1 for all i
                      - sum(b) = n / 2
                      - b[-1] = 0.5
    """
    np.seterr(all="ignore")

    # -------------------------
    # Helper functions
    # -------------------------

    def assemble_sequence(b: np.ndarray) -> np.ndarray:
        # s = concat(b[:-1], reversed(b))
        return np.concatenate([b[:-1], b[::-1]])

    def max_overlap_value(s: np.ndarray) -> float:
        # Same objective as compute_upper_bound in the prompt:
        # UB = max_k corr(s, 1 - s, 'full') / len(s) * 2
        conv_vals = np.correlate(s, 1 - s, mode="full")
        return np.max(conv_vals) / len(s) * 2.0

    def softmax_weights(c: np.ndarray, tau: float) -> np.ndarray:
        # Numerically stable softmax over the correlation vector "c"
        c_max = np.max(c)
        z = (c - c_max) / max(tau, 1e-12)
        z = np.clip(z, -60.0, 60.0)  # prevent overflow
        expz = np.exp(z)
        s = np.sum(expz)
        if s <= 0:
            # Fallback to uniform if numerical issues
            return np.full_like(expz, 1.0 / len(expz))
        return expz / s

    def compute_gradient_s(s: np.ndarray, w: np.ndarray) -> np.ndarray:
        """Compute gradient wrt s using softmax weights w over shifts.

        c_k = sum_i s_i * (1 - s_{i+k})
        ∂c_k/∂s_j = [1 - s_{j+k}]_valid - [s_{j-k}]_valid

        For J_tau(s) = τ log sum_k exp(c_k/τ), gradient is sum_k w_k ∂c_k/∂s.

        Efficient computation via convolution identities:
          - Let L = len(s), w_shift of length 2L-1 indexed by k ∈ [-(L-1)..(L-1)]
          - termA[j] = sum_{k: 0≤j+k<L} w_k
                      = windowed sum over w of length L starting at a = L-1-j
          - termB[j] = sum_k w_k s_{j+k}
          - termC[j] = sum_k w_k s_{j-k}
          Using linear convolution, for arrays of unequal lengths we obtain the
          exact L-length results by slicing the 'full' convolution:
            termB = (w[::-1] * s)[L-1 : 2L-1]
            termC = (w * s)[L-1 : 2L-1]
        """
        L = len(s)
        # w is length 2L-1 with zero shift at index L-1
        w_shift = w

        # termA: sliding window sums of w of width L; window start a = L-1-j
        wc = np.cumsum(w_shift)
        termA = np.empty(L, dtype=float)
        for j in range(L):
            start = (L - 1) - j
            end = (2 * L - 2) - j
            # start ∈ [0, L-1], end ∈ [L-1, 2L-2]
            if start == 0:
                sum_w = wc[end]
            else:
                sum_w = wc[end] - wc[start - 1]
            termA[j] = sum_w

        # termB and termC via 'full' convolution with proper slicing to get length L
        # See docstring for derivation of slice indices.
        w_flip = w_shift[::-1]
        conv_wflip_s_full = np.convolve(w_flip, s, mode="full")
        termB = conv_wflip_s_full[L - 1 : 2 * L - 1]

        conv_w_s_full = np.convolve(w_shift, s, mode="full")
        termC = conv_w_s_full[L - 1 : 2 * L - 1]

        g_s = termA - termB - termC
        return g_s

    def map_grad_s_to_b(g_s: np.ndarray, n: int) -> np.ndarray:
        """Map gradient from final sequence s back to half-sequence b.

        b indices: 0..n-1
        s length L = 2n-1, with
          s[i] = b[i] for i in [0..n-2]
          s[n-1] = b[n-1]
          s[L-1 - i] = b[i] for i in [0..n-2]

        Therefore:
          ∂J/∂b[i] = ∂J/∂s[i] + ∂J/∂s[L-1-i]  for i < n-1
          ∂J/∂b[n-1] = ∂J/∂s[n-1]
        """
        L = 2 * n - 1
        g_b = np.zeros(n, dtype=float)
        for i in range(n - 1):
            g_b[i] = g_s[i] + g_s[L - 1 - i]
        g_b[n - 1] = g_s[n - 1]
        return g_b

    def project_box_sum(b: np.ndarray, target_sum: float, fixed_idx: int, fixed_val: float) -> np.ndarray:
        """Project onto intersection of [0,1]^n, sum(b) = target_sum, and b[fixed_idx] = fixed_val.

        Exact L2 projection via water-filling:
          Find lambda such that x_i = clip(v_i + lambda, 0, 1) for i != fixed_idx
          and sum_i x_i + fixed_val = target_sum.

        This procedure iteratively peels off saturated indices.
        """
        n = len(b)
        v = np.array(b, dtype=float, copy=True)

        # Pin the fixed index exactly
        v[fixed_idx] = fixed_val

        # Saturated set mask (false means free)
        sat_mask = np.zeros(n, dtype=bool)
        sat_mask[fixed_idx] = True

        # Values to return
        x = v.copy()

        target_wo_fixed = target_sum - fixed_val

        # Iteratively saturate violators and solve for lambda on remaining free set
        for _ in range(5 * n):
            free_idx = np.where(~sat_mask)[0]
            if free_idx.size == 0:
                # Nothing left to adjust
                return x

            # Sum of current saturated (non-fixed) entries:
            sat_nonfixed_sum = np.sum(x[sat_mask & (np.arange(n) != fixed_idx)])

            # Solve lambda assuming no further saturation: x_i = v_i + lambda
            lambda_val = (target_wo_fixed - sat_nonfixed_sum - np.sum(v[free_idx])) / float(free_idx.size)
            y = v[free_idx] + lambda_val

            below = y < 0.0
            above = y > 1.0

            if not below.any() and not above.any():
                x[free_idx] = y
                return x

            # Saturate the violators and mark them
            if below.any():
                idxs = free_idx[below]
                x[idxs] = 0.0
                sat_mask[idxs] = True
            if above.any():
                idxs = free_idx[above]
                x[idxs] = 1.0
                sat_mask[idxs] = True

        # Fallback (should not be hit): distribute uniformly over non-fixed entries
        x = np.clip(v, 0.0, 1.0)
        x[fixed_idx] = fixed_val
        delta = target_sum - np.sum(x)
        if abs(delta) > 1e-12:
            free_mask = np.ones(n, dtype=bool)
            free_mask[fixed_idx] = False
            cnt = np.sum(free_mask)
            if cnt > 0:
                x[free_mask] = np.clip(x[free_mask] + delta / cnt, 0.0, 1.0)
        return x

    def select_symmetric_plateau_peaks(c: np.ndarray, L: int, K_pairs: int, eps: float) -> np.ndarray:
        """Select a symmetric set of shifts for equalization.

        - Build sign-paired set: for each |k|>0 include both +k and -k; include k=0 at most once.
        - Choose up to K_pairs pairs by descending c_k.
        - Plateau expansion: include any shifts with c_k >= max(c) - eps, preserving ± symmetry.

        Args:
            c: correlation array of length 2L-1 (full mode), zero shift at index L-1
            L: length of s
            K_pairs: number of sign-pairs to include (not counting k=0)
            eps: plateau envelope

        Returns:
            np.ndarray of integer shifts k.
        """
        center = L - 1
        # Indices sorted by descending c
        idx_sorted = np.argsort(c)[::-1]
        sel = set()
        pairs = 0

        # Helper to add a pair
        def add_pair(k: int):
            nonlocal pairs
            if k == 0:
                sel.add(0)
                return
            if k in sel or (-k) in sel:
                # already added as part of some pair
                return
            sel.add(k)
            sel.add(-k)
            pairs += 1

        # First pass: take top pairs
        for p in idx_sorted:
            if pairs >= K_pairs:
                break
            k = int(p - center)
            if k == 0:
                # include k=0 if very top but don't count toward pair budget
                if 0 not in sel:
                    add_pair(0)
                continue
            add_pair(k)

        # Plateau expansion: include any shifts within eps of the max
        c_max = c[idx_sorted[0]] if idx_sorted.size > 0 else -np.inf
        thresh = c_max - eps
        for p in range(c.size):
            if c[p] >= thresh:
                k = int(p - center)
                if k == 0:
                    sel.add(0)
                else:
                    sel.add(k)
                    sel.add(-k)

        # Return sorted unique shifts
        shifts = np.array(sorted(sel), dtype=int)
        return shifts

    def compute_eq_grad_s_weighted(s: np.ndarray, shifts: np.ndarray, weights: np.ndarray) -> np.ndarray:
        """Compute weighted equalization gradient on s from selected shifts.

        For each selected shift k:
          ∂c_k/∂s_j = [1 - s_{j+k}]_valid - [s_{j-k}]_valid

        We weight across selected k by weights[k].
        """
        L = len(s)
        g = np.zeros(L, dtype=float)
        if len(shifts) == 0:
            return g
        for k, w in zip(shifts, weights):
            kk = int(k)
            if kk >= 0:
                # j+k valid -> j in [0, L-1-kk]
                if L - kk > 0:
                    g[: L - kk] += w * (1.0 - s[kk:])
                    g[kk:] -= w * s[: L - kk]
            else:
                kneg = -kk
                if L - kneg > 0:
                    g[kneg:] += w * (1.0 - s[: L - kneg])
                    g[: L - kneg] -= w * s[kneg:]
        return g

    # -------------------------
    # Optimization parameters
    # -------------------------

    n = 129  # Half-length; final length L = 2n - 1
    L = 2 * n - 1

    # Initialize b as a smooth sigmoid ramp, then project to constraints.
    t = np.linspace(-4.0, 4.0, n)
    b = 1.0 / (1.0 + np.exp(-t))
    # Ensure last element exactly 0.5 to satisfy mirror sum constraint later
    b[-1] = 0.5
    # Project to sum n/2 and box constraints
    b = project_box_sum(b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)

    # Stage schedule: (tau, iterations, step_factor, K_pairs, beta, alpha_frac)
    # eta per step will be (step_factor / L)
    stages = [
        (2.0, 200, 0.80, 8, 0.25, 0.35),
        (1.0, 260, 0.60, 12, 0.50, 0.30),
        (0.5, 300, 0.45, 16, 0.75, 0.20),
        (0.25, 350, 0.33, 22, 0.90, 0.12),
    ]

    # Mild smoothing kernels per stage (weaken smoothing later)
    smooth_kernels = [
        np.array([0.06, 0.88, 0.06]),
        np.array([0.05, 0.90, 0.05]),
        np.array([0.04, 0.92, 0.04]),
        np.array([0.03, 0.94, 0.03]),
    ]

    # Track best solution found
    s = assemble_sequence(b)
    best_b = b.copy()
    best_ub = max_overlap_value(s)

    # Main optimization loop
    for stage_idx, (tau, iters, step_mul, K_pairs, beta, alpha_frac) in enumerate(stages):
        eta = (step_mul / L)
        kern = smooth_kernels[stage_idx]
        for _ in range(iters):
            # Assemble mirrored sequence s
            s = assemble_sequence(b)

            # Compute correlation and softmax weights
            c = np.correlate(s, 1 - s, mode="full")  # length 2L - 1
            w = softmax_weights(c, tau)

            # Gradient wrt s and map back to b
            g_s = compute_gradient_s(s, w)
            g_b = map_grad_s_to_b(g_s, n)

            # Scale step adaptively by gradient norm to stabilize updates
            g_scale = max(1e-10, np.linalg.norm(g_b) / np.sqrt(n))
            step = eta / g_scale

            # Update with projected gradient step
            b = b - step * g_b

            # Smooth slightly to damp oscillations (exclude last pinned element, re-pin)
            sm = b.copy()
            # interior smoothing
            sm[1:-1] = kern[0] * b[0:-2] + kern[1] * b[1:-1] + kern[2] * b[2:]
            # edges (soften a bit)
            sm[0] = (kern[1] * b[0] + kern[2] * b[1]) / (kern[1] + kern[2])
            sm[-2] = kern[0] * b[-3] + kern[1] * b[-2] + kern[2] * b[-1]
            sm[-1] = 0.5  # exact pin
            b = sm

            # Project onto box and sum-hyperplane with fixed last element
            b = np.clip(b, 0.0, 1.0)
            b[-1] = 0.5
            b = project_box_sum(b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)

            # -------------------------
            # Symmetric weighted equiripple micro-step (SWEB)
            # -------------------------
            # Select symmetric top peaks with plateau expansion using pre-update c
            eps_plateau = 0.6 * tau  # proportional to current temperature
            shifts = select_symmetric_plateau_peaks(c, L, K_pairs=K_pairs, eps=eps_plateau)

            # Compute weights for selected shifts via sharp softmax; then blend with uniform
            if shifts.size > 0:
                # Extract c values at selected shifts
                center = L - 1
                sel_idx = shifts + center
                c_sel = c[sel_idx]

                # Weighted component
                tau_eq = max(0.08, 0.5 * tau)
                w_sel = softmax_weights(c_sel, tau_eq)

                # Clip and renormalize to avoid over-concentration
                K_sel = len(w_sel)
                wmin = 0.05 / max(1, K_sel)
                wmax = 0.60
                w_sel = np.clip(w_sel, wmin, wmax)
                w_sel = w_sel / np.sum(w_sel)

                # Uniform component
                w_uni = np.full(K_sel, 1.0 / K_sel, dtype=float)

                # Blend
                w_mix = (1.0 - beta) * w_uni + beta * w_sel
                w_mix = w_mix / np.sum(w_mix)

                # Compute equalization grad on current s (after main projection)
                s_now = assemble_sequence(b)
                g_s_eq = compute_eq_grad_s_weighted(s_now, shifts, w_mix)
                g_b_eq = map_grad_s_to_b(g_s_eq, n)

                # Norm-aware corrective step
                eq_norm = max(1e-12, np.linalg.norm(g_b_eq))
                main_norm = max(1e-12, np.linalg.norm(g_b))
                scale_ratio = min(1.5, main_norm / eq_norm)
                alpha = alpha_frac * eta * scale_ratio

                # Apply micro-step and re-project
                b = b - alpha * g_b_eq
                b = np.clip(b, 0.0, 1.0)
                b[-1] = 0.5
                b = project_box_sum(b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)

                # Optional light post-correction smoothing (very light)
                if stage_idx <= 1:
                    b[1:-1] = 0.975 * b[1:-1] + 0.0125 * (b[0:-2] + b[2:])
                    b[-1] = 0.5
                    b = project_box_sum(b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)

            # Track best
            s = assemble_sequence(b)
            ub = max_overlap_value(s)
            if ub < best_ub:
                best_ub = ub
                best_b = b.copy()

    # Final polishing: plateau-aware equiripple-only steps at low temperature
    tau = 0.20
    eta = (0.25 / L)
    for _ in range(220):
        s = assemble_sequence(b)
        c = np.correlate(s, 1 - s, mode="full")
        # Select broader plateau but moderate K
        shifts = select_symmetric_plateau_peaks(c, L, K_pairs=18, eps=0.4 * tau)
        if shifts.size == 0:
            break
        center = L - 1
        c_sel = c[shifts + center]
        tau_eq = max(0.06, 0.5 * tau)
        w_sel = softmax_weights(c_sel, tau_eq)
        K_sel = len(w_sel)
        wmin = 0.04 / max(1, K_sel)
        wmax = 0.65
        w_sel = np.clip(w_sel, wmin, wmax)
        w_sel = w_sel / np.sum(w_sel)
        # Blend with strong weighted focus in polish
        beta = 0.92
        w_uni = np.full(K_sel, 1.0 / K_sel, dtype=float)
        w_mix = (1.0 - beta) * w_uni + beta * w_sel
        w_mix = w_mix / np.sum(w_mix)

        # Compute gradient and take a small step
        s_now = assemble_sequence(b)
        g_s_eq = compute_eq_grad_s_weighted(s_now, shifts, w_mix)
        g_b_eq = map_grad_s_to_b(g_s_eq, n)
        g_norm = max(1e-10, np.linalg.norm(g_b_eq) / np.sqrt(n))
        step = (eta / g_norm)
        b = b - step * g_b_eq

        # Project
        b = np.clip(b, 0.0, 1.0)
        b[-1] = 0.5
        b = project_box_sum(b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)

        # Track best
        s = assemble_sequence(b)
        ub = max_overlap_value(s)
        if ub < best_ub:
            best_ub = ub
            best_b = b.copy()

    # Return the best found half-sequence as a NumPy array
    # Ensure pin and sum constraints exactly (final polish)
    best_b[-1] = 0.5
    best_b = project_box_sum(best_b, target_sum=n / 2.0, fixed_idx=n - 1, fixed_val=0.5)
    return best_b
# EVOLVE_END


if __name__ == "__main__":
    # Note: JSON cannot serialize NumPy arrays directly; convert to list for printing.
    hs = generate_erdos_data()
    try:
        serializable = hs.tolist()
    except Exception:
        serializable = list(map(float, hs))
    print(json.dumps({"half_sequence": serializable}))
```

```text
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
```

```text
0.379005 \leq C_5 \leq 0.380927
```
