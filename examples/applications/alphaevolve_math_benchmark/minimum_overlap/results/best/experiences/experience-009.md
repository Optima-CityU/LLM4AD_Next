Deterministic worst-lag-directed mass-transfer step after Adam + projection, using exact derivatives and mirror-consistent transfers with accept-if-decrease on the true max-over-lags objective.

- Mirror-consistent worst-lag mass-transfer polish: The method runs after Adam plus projection and targets the worst lag identified from the full correlation of h with 1 − h, applying a deterministic, mirror-consistent mass-transfer refinement to directly reduce the true maximum overlap.
- Mirror-consistent worst-lag mass-transfer polish: For the worst lag r*, it computes the exact marginal derivative dF_{r*}/dh_j = (1 − h_{j+r*}) − h_{j−r*} with out-of-range indices contributing 0, maps this gradient to the half-parameterization by combining mirror pairs, and transfers ε mass from the index with largest positive gradient to the most negative while respecting [0,1] bounds and preserving the total sum via mirroring.
- Mirror-consistent worst-lag mass-transfer polish: Each transfer is followed by a water-filling projection and is accepted only if the true global maximum over all lags decreases; in the reported run, the evaluation recorded validity 1.0, an upper bound of 0.3820813126788658, eval_time 3.9923696238547564, and a target_upper_bound of 0.380927.

```python
#!/usr/bin/env python3
"""Optimization-based generator for Erdős' minimum overlap step function.

This script produces a half-sequence (the "left half" including the center)
which, when mirrored as described by the evaluation harness, yields a full
sequence that defines a step function h on [0,2]. The sequence aims to reduce
the worst-case overlap, i.e., the maximum correlation h * (1 - h) over all
integer lags, thereby improving the known upper bound.

Key implementation points:
- We optimize a half-sequence s in [0,1]^m with sum(s) = m/2 and s[-1] = 0.5.
- The full sequence h is formed as: h = concat(s[:-1], s[::-1]).
- Objective surrogate: softmax of the lagged overlaps, annealed to approximate
  the true max. Gradients are computed analytically and mapped back to s.
- Constraints are enforced via an exact projection onto the [0,1] box with
  fixed sum for the free coordinates (excluding s[-1]), solved by a bisection
  on the Lagrange multiplier (water-filling).
- Use Adam optimizer with a mild quadratic smoothness penalty early on.

The function returns the optimized half-sequence as a plain Python list so
that it can be JSON-serialized. The evaluation harness will mirror it to form
the final sequence and compute the upper bound.

Enhancement in this iteration:
- A deterministic, symmetry-preserving mass-transfer refinement step is added
  after Adam optimization. It targets the true worst lag of the correlation
  objective using exact marginal derivatives and performs conservative, feasible
  transfers of mass between two half-indices to strictly decrease the maximum
  overlap. This post-processing step tends to reduce the computed upper bound
  beyond what the smooth surrogate optimization attains.
"""

import json
import math
import numpy as np


# EVOLVE_START
def generate_erdos_data():
    """Generates a half-sequence optimized to lower Erdős' minimum-overlap upper bound.

    Returns:
        list[float]: Half-sequence s of length m, values in [0,1], with s[-1]=0.5 and sum(s)=m/2.
                     When mirrored as per the harness (concatenate s[:-1] with reversed s),
                     the resulting full sequence satisfies the constraints and yields a low upper bound.
    """
    rng = np.random.default_rng(seed=123456)

    # Problem size: choose half-length m (final length n = 2m - 1).
    # Larger m allows finer control but increases compute. m=121 => n=241 is a good balance.
    m = 121

    # Constraint: s in [0,1]^m, s[-1] = 0.5, sum(s) = m/2.
    target_sum = m / 2.0
    center_val = 0.5

    # Optimization hyperparameters.
    num_starts = 3
    iters = 380
    tau_start = 0.20
    tau_end = 0.02
    lr = 0.08  # base learning rate for Adam
    beta1 = 0.9
    beta2 = 0.999
    eps_adam = 1e-8
    lam_smooth_start = 1e-2  # quadratic smoothness weight, will decay to ~0

    # Helper: Build full sequence h from half-sequence s.
    def half_to_full(s):
        # s: shape (m,)
        return np.concatenate((s[:-1], s[::-1]))

    # Helper: Compute the evaluation upper bound on the full sequence as per the harness code.
    def compute_upper_bound_full(h):
        conv_vals = np.correlate(h, 1.0 - h, mode='full')
        return np.max(conv_vals) / len(h) * 2.0

    # Helper: Project vector v (length m-1, the free coords excluding center) onto [0,1] with sum T via bisection.
    def project_box_sum(v, T):
        # Returns x = argmin ||x - v||^2 s.t. 0 <= x <= 1 and sum(x) = T.
        # Solves x_i = clip(v_i - lambda, 0, 1) with lambda chosen so sum(x) = T.
        n = v.shape[0]
        # Clamp trivial cases
        T = float(np.clip(T, 0.0, n))
        if n == 0:
            return v.copy()
        # Establish bisection bounds on lambda.
        lo = np.min(v) - 1.0  # ensures v - lo >= 1 -> sum(x)=n
        hi = np.max(v) + 1.0  # ensures v - hi <= 0 -> sum(x)=0
        # If already feasible and sum close, return clip.
        x = np.clip(v, 0.0, 1.0)
        s = float(np.sum(x))
        if abs(s - T) < 1e-12:
            return x
        # Bisection on lambda
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            x = np.clip(v - mid, 0.0, 1.0)
            s = float(np.sum(x))
            if s > T:
                lo = mid
            else:
                hi = mid
        return np.clip(v - 0.5 * (lo + hi), 0.0, 1.0)

    # Helper: Project full s (length m) to constraints: s[-1]=0.5, sum(s)=m/2, and 0<=s<=1.
    def project_half(s):
        s = np.clip(s, 0.0, 1.0)
        s[-1] = center_val
        T = target_sum - center_val
        v = s[:-1]
        v_proj = project_box_sum(v, T)
        s_proj = np.concatenate([v_proj, np.array([center_val])])
        return s_proj

    # Helper: Smoothness (quadratic) gradient on s to discourage spiky patterns early.
    # Penalizes sum (s[i+1] - s[i])^2.
    def smoothness_grad_s(s):
        g = np.zeros_like(s)
        # interior points
        g[1:-1] = 2 * (2 * s[1:-1] - s[0:-2] - s[2:])
        # endpoints (excluding center, but we still compute; projection will fix center)
        g[0] = 2 * (s[0] - s[1])
        g[-1] = 2 * (s[-1] - s[-2])
        return g

    # Compute softmax objective and gradient w.r.t. H (full sequence), then map to s.
    def softmax_obj_and_grad_s(s, tau, lam_smooth=0.0):
        h = half_to_full(s)
        n = h.shape[0]
        # Compute full cross-correlation (matches evaluation)
        M = np.correlate(h, 1.0 - h, mode='full')  # length 2n - 1
        # Softmax weights (stabilized)
        M_max = float(np.max(M))
        exps = np.exp((M - M_max) / tau)
        Z = float(np.sum(exps))
        w = exps / Z  # weights sum to 1

        # Objective (smoothed)
        soft_obj = tau * (math.log(Z) + M_max / tau)

        # Gradient dJ/dh via sum_r w_r * dM_r/dh
        g_h = np.zeros_like(h)
        # Iterate over lags r in -(n-1)..(n-1), vectorized slice updates for efficiency.
        for r in range(-(n - 1), n):
            wr = w[r + (n - 1)]
            if wr == 0.0:
                continue
            if r >= 0:
                sft = r
                g_h[r:] += wr * (1.0 - h[:n - sft])
                g_h[:n - sft] += -wr * h[sft:]
            else:
                sft = -r
                g_h[:n - sft] += wr * (1.0 - h[sft:])
                g_h[sft:] += -wr * h[:n - sft]

        # Map g_h to g_s
        g_s = np.zeros_like(s)
        # Indices mapping: for j in [0, m-2], H[j] and H[2m-2-j]; for j=m-1, H[m-1].
        two_m_minus_2 = 2 * m - 2
        if m > 1:
            idx1 = np.arange(0, m - 1, dtype=int)
            idx2 = two_m_minus_2 - idx1
            g_s[idx1] = g_h[idx1] + g_h[idx2]
        # center
        g_s[m - 1] = g_h[m - 1]

        # Add smoothness gradient on s (quadratic); projection fixes center, but we null its grad downstream.
        if lam_smooth > 0.0:
            g_s += lam_smooth * smoothness_grad_s(s)

        return soft_obj, g_s

    # Optimize from an initialization.
    def optimize_from_init(s0):
        s = project_half(s0)
        # Adam state
        m_t = np.zeros_like(s)
        v_t = np.zeros_like(s)
        best_s = s.copy()
        best_ub = compute_upper_bound_full(half_to_full(s))
        # Anneal tau and lam_smooth
        for t in range(1, iters + 1):
            frac = t / iters
            tau = tau_start * (1 - frac) + tau_end * frac
            lam_smooth = lam_smooth_start * (1 - frac)  # taper to 0

            # Compute gradient
            _, grad_s = softmax_obj_and_grad_s(s, tau, lam_smooth)

            # Center is fixed: null its gradient to avoid attempting to move it.
            grad_s[-1] = 0.0

            # Adam update
            m_t = beta1 * m_t + (1 - beta1) * grad_s
            v_t = beta2 * v_t + (1 - beta2) * (grad_s * grad_s)
            m_hat = m_t / (1 - beta1 ** t)
            v_hat = v_t / (1 - beta2 ** t)

            # Compute adaptive step
            step = lr * m_hat / (np.sqrt(v_hat) + eps_adam)
            # Take step and project
            s = s - step
            s = project_half(s)

            # Track best true upper bound occasionally
            if t % 10 == 0 or t == iters:
                ub = compute_upper_bound_full(half_to_full(s))
                if ub < best_ub:
                    best_ub = ub
                    best_s = s.copy()

        return best_s, best_ub

    # Deterministic mass-transfer refinement as described in the algorithm notes.
    def mass_transfer_refine(s_init, max_accepts=200, top_k=3):
        s = project_half(s_init)
        h = half_to_full(s)
        n = h.shape[0]
        conv = np.correlate(h, 1.0 - h, mode='full')
        best_ub = float(np.max(conv)) / n * 2.0

        accept_count = 0

        # Helper: exact gradient of M_r at full h, mapped to half s.
        def grad_half_for_lag(s, r):
            h_local = half_to_full(s)
            n_local = h_local.shape[0]
            # Gradient on full h using exact marginal derivative for lag r
            g_h = np.zeros_like(h_local)
            if r >= 0:
                sft = r
                g_h[r:] += (1.0 - h_local[:n_local - sft])
                g_h[:n_local - sft] += -h_local[sft:]
            else:
                sft = -r
                g_h[:n_local - sft] += (1.0 - h_local[sft:])
                g_h[sft:] += -h_local[:n_local - sft]

            # Map full gradient to half gradient
            g_s = np.zeros_like(s)
            two_m2 = 2 * m - 2
            if m > 1:
                idx1 = np.arange(0, m - 1, dtype=int)
                idx2 = two_m2 - idx1
                g_s[idx1] = g_h[idx1] + g_h[idx2]
            g_s[m - 1] = g_h[m - 1]
            return g_s

        # Main refinement loop
        while accept_count < max_accepts:
            # Recompute worst lags
            h = half_to_full(s)
            conv = np.correlate(h, 1.0 - h, mode='full')
            n = h.shape[0]
            # Indices of top-k worst lags (descending)
            worst_idx_desc = np.argsort(conv)[::-1]
            # Generate candidate lags (convert from idx to signed r)
            r_candidates = []
            for idx in worst_idx_desc[: min(top_k, conv.shape[0])]:
                r = int(idx) - (n - 1)
                r_candidates.append(r)

            improved = False
            # Try the worst few lags
            for r in r_candidates:
                g_s = grad_half_for_lag(s, r)
                # Center is fixed
                g_s[m - 1] = 0.0
                # Choose p with largest positive gradient (decrease s[p] reduces M_r),
                # and q with most negative gradient (increase s[q] reduces M_r)
                # Exclude center index.
                if m <= 1:
                    continue
                # Mask out center for selection
                mask = np.ones(m, dtype=bool)
                mask[m - 1] = False
                g_free = g_s.copy()
                g_free[~mask] = 0.0

                p = int(np.argmax(g_free))
                q = int(np.argmin(g_free))
                if p == m - 1 or q == m - 1:
                    # Should not happen due to masking, but guard anyway.
                    continue

                gp = g_s[p]
                gq = g_s[q]
                # If no positive/negative split, skip this lag
                if gp <= 0.0 or gq >= 0.0 or p == q:
                    continue

                # Available slack to move epsilon from p to q without violating [0,1]
                sp = float(s[p])
                sq = float(s[q])
                slack = min(sp - 0.0, 1.0 - sq)
                if slack <= 1e-12:
                    continue

                # Line search on epsilon
                eps_try = 0.6 * slack  # conservative initial step
                accepted_local = False
                for _ in range(12):
                    s_trial = s.copy()
                    s_trial[p] = max(0.0, s_trial[p] - eps_try)
                    s_trial[q] = min(1.0, s_trial[q] + eps_try)
                    # Project to maintain exact constraints and mirror consistency
                    s_trial = project_half(s_trial)

                    h_trial = half_to_full(s_trial)
                    conv_trial = np.correlate(h_trial, 1.0 - h_trial, mode='full')
                    ub_trial = float(np.max(conv_trial)) / len(h_trial) * 2.0

                    # Accept only if the true maximum decreases
                    if ub_trial + 1e-12 < best_ub:
                        s = s_trial
                        best_ub = ub_trial
                        accept_count += 1
                        improved = True
                        accepted_local = True
                        break
                    else:
                        eps_try *= 0.5  # back off

                if accepted_local:
                    # After acceptance, restart with fresh worst-lag computation
                    break

            if not improved:
                # No improvement across current top-k lags -> terminate
                break

        return s, best_ub

    # Create several deterministic initializations
    inits = []

    # 1) Uniform
    s0 = np.full(m, 0.5, dtype=float)
    inits.append(s0)

    # 2) Cosine bump towards center
    x = np.linspace(-1.0, 1.0, m)
    s0 = 0.5 + 0.12 * np.cos(np.pi * x)
    s0 = np.clip(s0, 0.0, 1.0)
    inits.append(s0)

    # 3) Slight random perturbation around 0.5
    s0 = 0.5 + 0.08 * rng.standard_normal(m)
    s0 = np.clip(s0, 0.0, 1.0)
    inits.append(s0)

    # Run optimizations and select the best
    best_s_all = None
    best_ub_all = float('inf')
    for s0 in inits[:num_starts]:
        s_opt, ub = optimize_from_init(s0)
        if ub < best_ub_all:
            best_ub_all = ub
            best_s_all = s_opt

    # Apply deterministic mass-transfer refinement to further reduce the true maximum overlap
    best_s_all, best_ub_all = mass_transfer_refine(best_s_all, max_accepts=200, top_k=3)

    # Final safety projection and rounding small numerical noise
    best_s_all = project_half(best_s_all)
    # Return as a Python list (JSON-serializable)
    return [float(x) for x in best_s_all]
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"half_sequence": generate_erdos_data()}))
```

$$
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
$$

$$
0.379005 \leq C_5 \leq 0.380927
$$

```python
#!/usr/bin/env python3
"""Optimization-based generator for Erdős' minimum overlap step function.

This script produces a half-sequence (the "left half" including the center)
which, when mirrored as described by the evaluation harness, yields a full
sequence that defines a step function h on [0,2]. The sequence aims to reduce
the worst-case overlap, i.e., the maximum correlation h * (1 - h) over all
integer lags, thereby improving the known upper bound.

Key implementation points:
- We optimize a half-sequence s in [0,1]^m with sum(s) = m/2 and s[-1] = 0.5.
- The full sequence h is formed as: h = concat(s[:-1], s[::-1]).
- Objective surrogate: softmax of the lagged overlaps, annealed to approximate
  the true max. Gradients are computed analytically and mapped back to s.
- Constraints are enforced via an exact projection onto the [0,1] box with
  fixed sum for the free coordinates (excluding s[-1]), solved by a bisection
  on the Lagrange multiplier (water-filling).
- Use Adam optimizer with a mild quadratic smoothness penalty early on.

The function returns the optimized half-sequence as a plain Python list so
that it can be JSON-serialized. The evaluation harness will mirror it to form
the final sequence and compute the upper bound.

Enhancement in this iteration:
- A deterministic, symmetry-preserving mass-transfer refinement step is added
  after Adam optimization. It targets the true worst lag of the correlation
  objective using exact marginal derivatives and performs conservative, feasible
  transfers of mass between two half-indices to strictly decrease the maximum
  overlap. This post-processing step tends to reduce the computed upper bound
  beyond what the smooth surrogate optimization attains.
"""

import json
import math
import numpy as np


# EVOLVE_START
def generate_erdos_data():
    """Generates a half-sequence optimized to lower Erdős' minimum-overlap upper bound.

    Returns:
        list[float]: Half-sequence s of length m, values in [0,1], with s[-1]=0.5 and sum(s)=m/2.
                     When mirrored as per the harness (concatenate s[:-1] with reversed s),
                     the resulting full sequence satisfies the constraints and yields a low upper bound.
    """
    rng = np.random.default_rng(seed=123456)

    # Problem size: choose half-length m (final length n = 2m - 1).
    # Larger m allows finer control but increases compute. m=121 => n=241 is a good balance.
    m = 121

    # Constraint: s in [0,1]^m, s[-1] = 0.5, sum(s) = m/2.
    target_sum = m / 2.0
    center_val = 0.5

    # Optimization hyperparameters.
    num_starts = 3
    iters = 380
    tau_start = 0.20
    tau_end = 0.02
    lr = 0.08  # base learning rate for Adam
    beta1 = 0.9
    beta2 = 0.999
    eps_adam = 1e-8
    lam_smooth_start = 1e-2  # quadratic smoothness weight, will decay to ~0

    # Helper: Build full sequence h from half-sequence s.
    def half_to_full(s):
        # s: shape (m,)
        return np.concatenate((s[:-1], s[::-1]))

    # Helper: Compute the evaluation upper bound on the full sequence as per the harness code.
    def compute_upper_bound_full(h):
        conv_vals = np.correlate(h, 1.0 - h, mode='full')
        return np.max(conv_vals) / len(h) * 2.0

    # Helper: Project vector v (length m-1, the free coords excluding center) onto [0,1] with sum T via bisection.
    def project_box_sum(v, T):
        # Returns x = argmin ||x - v||^2 s.t. 0 <= x <= 1 and sum(x) = T.
        # Solves x_i = clip(v_i - lambda, 0, 1) with lambda chosen so sum(x) = T.
        n = v.shape[0]
        # Clamp trivial cases
        T = float(np.clip(T, 0.0, n))
        if n == 0:
            return v.copy()
        # Establish bisection bounds on lambda.
        lo = np.min(v) - 1.0  # ensures v - lo >= 1 -> sum(x)=n
        hi = np.max(v) + 1.0  # ensures v - hi <= 0 -> sum(x)=0
        # If already feasible and sum close, return clip.
        x = np.clip(v, 0.0, 1.0)
        s = float(np.sum(x))
        if abs(s - T) < 1e-12:
            return x
        # Bisection on lambda
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            x = np.clip(v - mid, 0.0, 1.0)
            s = float(np.sum(x))
            if s > T:
                lo = mid
            else:
                hi = mid
        return np.clip(v - 0.5 * (lo + hi), 0.0, 1.0)

    # Helper: Project full s (length m) to constraints: s[-1]=0.5, sum(s)=m/2, and 0<=s<=1.
    def project_half(s):
        s = np.clip(s, 0.0, 1.0)
        s[-1] = center_val
        T = target_sum - center_val
        v = s[:-1]
        v_proj = project_box_sum(v, T)
        s_proj = np.concatenate([v_proj, np.array([center_val])])
        return s_proj

    # Helper: Smoothness (quadratic) gradient on s to discourage spiky patterns early.
    # Penalizes sum (s[i+1] - s[i])^2.
    def smoothness_grad_s(s):
        g = np.zeros_like(s)
        # interior points
        g[1:-1] = 2 * (2 * s[1:-1] - s[0:-2] - s[2:])
        # endpoints (excluding center, but we still compute; projection will fix center)
        g[0] = 2 * (s[0] - s[1])
        g[-1] = 2 * (s[-1] - s[-2])
        return g

    # Compute softmax objective and gradient w.r.t. H (full sequence), then map to s.
    def softmax_obj_and_grad_s(s, tau, lam_smooth=0.0):
        h = half_to_full(s)
        n = h.shape[0]
        # Compute full cross-correlation (matches evaluation)
        M = np.correlate(h, 1.0 - h, mode='full')  # length 2n - 1
        # Softmax weights (stabilized)
        M_max = float(np.max(M))
        exps = np.exp((M - M_max) / tau)
        Z = float(np.sum(exps))
        w = exps / Z  # weights sum to 1

        # Objective (smoothed)
        soft_obj = tau * (math.log(Z) + M_max / tau)

        # Gradient dJ/dh via sum_r w_r * dM_r/dh
        g_h = np.zeros_like(h)
        # Iterate over lags r in -(n-1)..(n-1), vectorized slice updates for efficiency.
        for r in range(-(n - 1), n):
            wr = w[r + (n - 1)]
            if wr == 0.0:
                continue
            if r >= 0:
                sft = r
                g_h[r:] += wr * (1.0 - h[:n - sft])
                g_h[:n - sft] += -wr * h[sft:]
            else:
                sft = -r
                g_h[:n - sft] += wr * (1.0 - h[sft:])
                g_h[sft:] += -wr * h[:n - sft]

        # Map g_h to g_s
        g_s = np.zeros_like(s)
        # Indices mapping: for j in [0, m-2], H[j] and H[2m-2-j]; for j=m-1, H[m-1].
        two_m_minus_2 = 2 * m - 2
        if m > 1:
            idx1 = np.arange(0, m - 1, dtype=int)
            idx2 = two_m_minus_2 - idx1
            g_s[idx1] = g_h[idx1] + g_h[idx2]
        # center
        g_s[m - 1] = g_h[m - 1]

        # Add smoothness gradient on s (quadratic); projection fixes center, but we null its grad downstream.
        if lam_smooth > 0.0:
            g_s += lam_smooth * smoothness_grad_s(s)

        return soft_obj, g_s

    # Optimize from an initialization.
    def optimize_from_init(s0):
        s = project_half(s0)
        # Adam state
        m_t = np.zeros_like(s)
        v_t = np.zeros_like(s)
        best_s = s.copy()
        best_ub = compute_upper_bound_full(half_to_full(s))
        # Anneal tau and lam_smooth
        for t in range(1, iters + 1):
            frac = t / iters
            tau = tau_start * (1 - frac) + tau_end * frac
            lam_smooth = lam_smooth_start * (1 - frac)  # taper to 0

            # Compute gradient
            _, grad_s = softmax_obj_and_grad_s(s, tau, lam_smooth)

            # Center is fixed: null its gradient to avoid attempting to move it.
            grad_s[-1] = 0.0

            # Adam update
            m_t = beta1 * m_t + (1 - beta1) * grad_s
            v_t = beta2 * v_t + (1 - beta2) * (grad_s * grad_s)
            m_hat = m_t / (1 - beta1 ** t)
            v_hat = v_t / (1 - beta2 ** t)

            # Compute adaptive step
            step = lr * m_hat / (np.sqrt(v_hat) + eps_adam)
            # Take step and project
            s = s - step
            s = project_half(s)

            # Track best true upper bound occasionally
            if t % 10 == 0 or t == iters:
                ub = compute_upper_bound_full(half_to_full(s))
                if ub < best_ub:
                    best_ub = ub
                    best_s = s.copy()

        return best_s, best_ub

    # Deterministic mass-transfer refinement as described in the algorithm notes.
    def mass_transfer_refine(s_init, max_accepts=200, top_k=3):
        s = project_half(s_init)
        h = half_to_full(s)
        n = h.shape[0]
        conv = np.correlate(h, 1.0 - h, mode='full')
        best_ub = float(np.max(conv)) / n * 2.0

        accept_count = 0

        # Helper: exact gradient of M_r at full h, mapped to half s.
        def grad_half_for_lag(s, r):
            h_local = half_to_full(s)
            n_local = h_local.shape[0]
            # Gradient on full h using exact marginal derivative for lag r
            g_h = np.zeros_like(h_local)
            if r >= 0:
                sft = r
                g_h[r:] += (1.0 - h_local[:n_local - sft])
                g_h[:n_local - sft] += -h_local[sft:]
            else:
                sft = -r
                g_h[:n_local - sft] += (1.0 - h_local[sft:])
                g_h[sft:] += -h_local[:n_local - sft]

            # Map full gradient to half gradient
            g_s = np.zeros_like(s)
            two_m2 = 2 * m - 2
            if m > 1:
                idx1 = np.arange(0, m - 1, dtype=int)
                idx2 = two_m2 - idx1
                g_s[idx1] = g_h[idx1] + g_h[idx2]
            g_s[m - 1] = g_h[m - 1]
            return g_s

        # Main refinement loop
        while accept_count < max_accepts:
            # Recompute worst lags
            h = half_to_full(s)
            conv = np.correlate(h, 1.0 - h, mode='full')
            n = h.shape[0]
            # Indices of top-k worst lags (descending)
            worst_idx_desc = np.argsort(conv)[::-1]
            # Generate candidate lags (convert from idx to signed r)
            r_candidates = []
            for idx in worst_idx_desc[: min(top_k, conv.shape[0])]:
                r = int(idx) - (n - 1)
                r_candidates.append(r)

            improved = False
            # Try the worst few lags
            for r in r_candidates:
                g_s = grad_half_for_lag(s, r)
                # Center is fixed
                g_s[m - 1] = 0.0
                # Choose p with largest positive gradient (decrease s[p] reduces M_r),
                # and q with most negative gradient (increase s[q] reduces M_r)
                # Exclude center index.
                if m <= 1:
                    continue
                # Mask out center for selection
                mask = np.ones(m, dtype=bool)
                mask[m - 1] = False
                g_free = g_s.copy()
                g_free[~mask] = 0.0

                p = int(np.argmax(g_free))
                q = int(np.argmin(g_free))
                if p == m - 1 or q == m - 1:
                    # Should not happen due to masking, but guard anyway.
                    continue

                gp = g_s[p]
                gq = g_s[q]
                # If no positive/negative split, skip this lag
                if gp <= 0.0 or gq >= 0.0 or p == q:
                    continue

                # Available slack to move epsilon from p to q without violating [0,1]
                sp = float(s[p])
                sq = float(s[q])
                slack = min(sp - 0.0, 1.0 - sq)
                if slack <= 1e-12:
                    continue

                # Line search on epsilon
                eps_try = 0.6 * slack  # conservative initial step
                accepted_local = False
                for _ in range(12):
                    s_trial = s.copy()
                    s_trial[p] = max(0.0, s_trial[p] - eps_try)
                    s_trial[q] = min(1.0, s_trial[q] + eps_try)
                    # Project to maintain exact constraints and mirror consistency
                    s_trial = project_half(s_trial)

                    h_trial = half_to_full(s_trial)
                    conv_trial = np.correlate(h_trial, 1.0 - h_trial, mode='full')
                    ub_trial = float(np.max(conv_trial)) / len(h_trial) * 2.0

                    # Accept only if the true maximum decreases
                    if ub_trial + 1e-12 < best_ub:
                        s = s_trial
                        best_ub = ub_trial
                        accept_count += 1
                        improved = True
                        accepted_local = True
                        break
                    else:
                        eps_try *= 0.5  # back off

                if accepted_local:
                    # After acceptance, restart with fresh worst-lag computation
                    break

            if not improved:
                # No improvement across current top-k lags -> terminate
                break

        return s, best_ub

    # Create several deterministic initializations
    inits = []

    # 1) Uniform
    s0 = np.full(m, 0.5, dtype=float)
    inits.append(s0)

    # 2) Cosine bump towards center
    x = np.linspace(-1.0, 1.0, m)
    s0 = 0.5 + 0.12 * np.cos(np.pi * x)
    s0 = np.clip(s0, 0.0, 1.0)
    inits.append(s0)

    # 3) Slight random perturbation around 0.5
    s0 = 0.5 + 0.08 * rng.standard_normal(m)
    s0 = np.clip(s0, 0.0, 1.0)
    inits.append(s0)

    # Run optimizations and select the best
    best_s_all = None
    best_ub_all = float('inf')
    for s0 in inits[:num_starts]:
        s_opt, ub = optimize_from_init(s0)
        if ub < best_ub_all:
            best_ub_all = ub
            best_s_all = s_opt

    # Apply deterministic mass-transfer refinement to further reduce the true maximum overlap
    best_s_all, best_ub_all = mass_transfer_refine(best_s_all, max_accepts=200, top_k=3)

    # Final safety projection and rounding small numerical noise
    best_s_all = project_half(best_s_all)
    # Return as a Python list (JSON-serializable)
    return [float(x) for x in best_s_all]
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"half_sequence": generate_erdos_data()}))
```

```text
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
```

```text
0.379005 \leq C_5 \leq 0.380927
```
