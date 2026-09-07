Key mechanisms that stabilized step selection and harvested extra decrease in the mass-transfer refinement.

- Equalize‑top‑two with third‑lag safeguard and post‑accept expansion: It sets the trial step to ε0 = 0.9 × min(eps_eq2, eps_eq3, slack) when both equalizer denominators are positive, where eps_eq2 and eps_eq3 equalize the second- and third-worst overlaps using dM_r/dε = g^{(r)}[q] − g^{(r)}[p], and otherwise falls back to ε0 = min(slack, c_scale/(|g[p]| + |g[q]|)).
- Equalize‑top‑two with third‑lag safeguard and post‑accept expansion: After a move is accepted by Armijo backtracking, it attempts one additional step along the same donor/receiver direction with ε_next = min(slack_new, α × ε_accepted) for α ≈ 1.2–1.3, guarded by the same backtracking.
- Equalize‑top‑two with third‑lag safeguard and post‑accept expansion: The third‑lag safeguard prevents the equalizing step from promoting the third lag to become the new maximum, reducing oscillations and yielding more reliable maximum reduction per move.
- Equalize‑top‑two with third‑lag safeguard and post‑accept expansion: Under the provided harness it achieved score 0.9973040336897849 with validity 1.0 and an upper bound of 0.38195674250976586 at evaluation time 4.641382556874305.

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

Enhancements in this iteration:
1) Third-lag safeguard for equalizing step:
   In addition to equalizing the top two worst overlaps (r1 and r2) under a local
   linear model to pick an initial mass-transfer step, we also compute the equalizer
   with respect to the third-worst lag r3. When both equalizer denominators are
   positive, we set the trial step to ε0 = 0.9 * min(eps_eq2, eps_eq3, slack),
   thereby preventing over-equalization that might promote the third lag to worst.

2) Single post-accept expansion:
   After accepting a mass-transfer step via Armijo backtracking, we immediately try
   one additional move in the same donor/receiver direction with an expanded step
   size (e.g., 1.25× the accepted step, capped by the new slack), guarded by the
   same backtracking logic. This captures additional local improvement safely.

All other logic (projection, top-k aggregation, Armijo backtracking) remains unchanged.
"""

import json
import math
import numpy as np


# EVOLVE_START
def generate_erdos_data() -> np.ndarray:
    """Generates a half-sequence optimized to lower Erdős' minimum-overlap upper bound.

    Returns:
        np.ndarray: Half-sequence s of length m, values in [0,1], with s[-1]=0.5 and sum(s)=m/2.
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
    iters = 420
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
        # endpoints (including center; projection will fix center)
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

    # Mass-transfer refinement with top-k aggregated marginal gradients and Armijo backtracking.
    def mass_transfer_refine(s_init, max_accepts=250, top_k=9, t_grad=0.15, c_scale=0.2, backtrack=0.5):
        """Refine s by conservative mass transfers guided by aggregated worst-lag gradients.

        After computing the aggregated gradient over the top-k worst lags and selecting
        donor p (largest positive gradient) and receiver q (most negative), compute exact
        per-lag marginal gradients and set an initial step size using a safe equalizing
        heuristic that accounts for both the second- and third-worst lags. Fall back to a
        gradient-scaled step when equalizers are unusable. Use Armijo-style backtracking
        and exact projection to ensure robustness. After a successful step, try one
        additional expanded move in the same direction with the same safeguards.

        Args:
            s_init: initial half sequence (feasible).
            max_accepts: maximum number of accepted transfers.
            top_k: number of worst lags to aggregate for the gradient.
            t_grad: softmax temperature for weighting top-k overlaps (0.1-0.2 recommended).
            c_scale: scaling constant in epsilon0 = min(slack, c_scale / (|g[p]| + |g[q]|)).
            backtrack: Armijo-style backtracking factor in (0,1).

        Returns:
            (s_refined, best_upper_bound)
        """
        s = project_half(s_init)
        h = half_to_full(s)
        n = h.shape[0]
        conv = np.correlate(h, 1.0 - h, mode='full')
        best_ub = float(np.max(conv)) / n * 2.0

        accept_count = 0

        # Helper: exact gradient of a single lag r at full h, mapped to half s.
        def grad_half_for_lag(s_local, r):
            h_local = half_to_full(s_local)
            n_local = h_local.shape[0]
            g_h = np.zeros_like(h_local)
            if r >= 0:
                sft = r
                g_h[r:] += (1.0 - h_local[:n_local - sft])
                g_h[:n_local - sft] += -h_local[sft:]
            else:
                sft = -r
                g_h[:n_local - sft] += (1.0 - h_local[sft:])
                g_h[sft:] += -h_local[:n_local - sft]
            # Map to half gradient
            g_s = np.zeros_like(s_local)
            two_m2 = 2 * m - 2
            if m > 1:
                idx1 = np.arange(0, m - 1, dtype=int)
                idx2 = two_m2 - idx1
                g_s[idx1] = g_h[idx1] + g_h[idx2]
            g_s[m - 1] = 0.0  # center fixed
            return g_s

        while accept_count < max_accepts:
            # Recompute current overlaps and worst lags
            h = half_to_full(s)
            conv = np.correlate(h, 1.0 - h, mode='full')
            n = h.shape[0]

            # Identify top-k worst lags by correlation magnitude (descending)
            worst_idx_desc = np.argsort(conv)[::-1]
            topk_idx = worst_idx_desc[: min(top_k, conv.shape[0])]
            if topk_idx.size == 0:
                break
            topk_lags = [int(idx) - (n - 1) for idx in topk_idx]
            topk_vals = conv[topk_idx].astype(float)

            # Softmax weights over the top-k overlaps (temperature ~0.1-0.2)
            vmax = float(np.max(topk_vals))
            w_raw = np.exp((topk_vals - vmax) / max(t_grad, 1e-8))
            w = w_raw / float(np.sum(w_raw))

            # Compute exact per-lag gradients once
            grads_per_lag = [grad_half_for_lag(s, r) for r in topk_lags]

            # Aggregate exact marginal gradients g_s = sum_r w_r g_s^{(r)}
            g_s = np.zeros_like(s)
            for wr, g_r in zip(w, grads_per_lag):
                if wr == 0.0:
                    continue
                g_s += wr * g_r

            # Fix center gradient to zero (center value is constrained to 0.5)
            g_s[m - 1] = 0.0

            # Select donor index p (largest positive g_s) and receiver q (most negative g_s)
            if m <= 1:
                break
            mask = np.ones(m, dtype=bool)
            mask[m - 1] = False  # exclude center
            g_free = g_s.copy()
            g_free[~mask] = 0.0

            p = int(np.argmax(g_free))
            q = int(np.argmin(g_free))
            gp = float(g_s[p])
            gq = float(g_s[q])

            # If no positive/negative split or degenerate choice, terminate
            if gp <= 1e-18 or gq >= -1e-18 or p == q:
                break

            # Available slack to move epsilon from p to q without violating [0,1]
            sp = float(s[p])
            sq = float(s[q])
            slack = min(sp - 0.0, 1.0 - sq)
            if slack <= 1e-12:
                break

            # Compute initial step via equalizing with third-lag safeguard when possible.
            # Directional derivatives dM_r/dε = g^{(r)}[q] − g^{(r)}[p]
            eps0 = None
            # Attempt using r2 and r3 if both available and safe
            if len(topk_lags) >= 3:
                M1 = float(topk_vals[0])
                M2 = float(topk_vals[1])
                M3 = float(topk_vals[2])
                g1 = grads_per_lag[0]
                g2 = grads_per_lag[1]
                g3 = grads_per_lag[2]
                dM1 = float(g1[q] - g1[p])
                dM2 = float(g2[q] - g2[p])
                dM3 = float(g3[q] - g3[p])
                denom2 = dM2 - dM1
                denom3 = dM3 - dM1
                if denom2 > 1e-18 and denom3 > 1e-18:
                    eps_eq2 = (M1 - M2) / denom2
                    eps_eq3 = (M1 - M3) / denom3
                    if eps_eq2 > 0 and eps_eq3 > 0 and np.isfinite(eps_eq2) and np.isfinite(eps_eq3):
                        eps0 = 0.9 * min(eps_eq2, eps_eq3, slack)

            # If third-lag guarded equalizer not usable, optionally try 2-lag equalizer as a mild fallback
            if (eps0 is None or eps0 <= 0) and len(topk_lags) >= 2:
                M1 = float(topk_vals[0])
                M2 = float(topk_vals[1])
                g1 = grads_per_lag[0]
                g2 = grads_per_lag[1]
                dM1 = float(g1[q] - g1[p])
                dM2 = float(g2[q] - g2[p])
                denom_eq = dM2 - dM1
                if denom_eq > 1e-18:
                    eps_eq = (M1 - M2) / denom_eq
                    if eps_eq > 0 and np.isfinite(eps_eq):
                        eps0 = min(slack, 0.9 * eps_eq)

            # Fallback to gradient-scaled heuristic when equalizers are not usable
            if eps0 is None or eps0 <= 0:
                denom = abs(gp) + abs(gq)
                if denom > 0:
                    eps0 = min(slack, c_scale / denom)
                else:
                    eps0 = 0.5 * slack  # conservative fallback

            # Ensure minimal positive trial
            eps_try = max(1e-12, eps0)

            # Armijo-style backtracking until the true maximum overlap decreases
            improved = False
            accepted_eps = None
            for _ in range(20):
                s_trial = s.copy()
                s_trial[p] = max(0.0, s_trial[p] - eps_try)
                s_trial[q] = min(1.0, s_trial[q] + eps_try)
                # Exact projection to satisfy box/sum constraints and fixed center
                s_trial = project_half(s_trial)

                h_trial = half_to_full(s_trial)
                conv_trial = np.correlate(h_trial, 1.0 - h_trial, mode='full')
                ub_trial = float(np.max(conv_trial)) / len(h_trial) * 2.0

                if ub_trial + 1e-12 < best_ub:
                    # Accept the move
                    s = s_trial
                    best_ub = ub_trial
                    accept_count += 1
                    improved = True
                    accepted_eps = eps_try
                    break
                else:
                    eps_try *= backtrack  # back off and retry

            if not improved:
                # No improvement found; terminate refinement
                break

            # Single post-accept expansion: try one more move in the same direction with expanded step.
            if accepted_eps is not None:
                # Recompute slack after the accepted move
                sp_new = float(s[p])
                sq_new = float(s[q])
                slack_new = min(sp_new - 0.0, 1.0 - sq_new)
                if slack_new > 1e-12:
                    eps_next = min(slack_new, 1.25 * accepted_eps)
                    tried_second = False
                    for _ in range(15):
                        s_trial2 = s.copy()
                        s_trial2[p] = max(0.0, s_trial2[p] - eps_next)
                        s_trial2[q] = min(1.0, s_trial2[q] + eps_next)
                        s_trial2 = project_half(s_trial2)
                        h_trial2 = half_to_full(s_trial2)
                        conv_trial2 = np.correlate(h_trial2, 1.0 - h_trial2, mode='full')
                        ub_trial2 = float(np.max(conv_trial2)) / len(h_trial2) * 2.0
                        tried_second = True
                        if ub_trial2 + 1e-12 < best_ub:
                            s = s_trial2
                            best_ub = ub_trial2
                            accept_count += 1
                            break
                        else:
                            eps_next *= backtrack
                    # If we couldn't make progress with the expanded move, simply continue.

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

    # Apply mass-transfer refinement with aggregated gradients, third-lag safeguard equalizer,
    # and single post-accept expansion.
    best_s_all, best_ub_all = mass_transfer_refine(
        best_s_all, max_accepts=250, top_k=9, t_grad=0.15, c_scale=0.2, backtrack=0.5
    )

    # Final safety projection
    best_s_all = project_half(best_s_all)
    # Return as a numpy array
    return np.asarray(best_s_all, dtype=float)
# EVOLVE_END


if __name__ == "__main__":
    # Convert to list for JSON serialization
    hs = generate_erdos_data()
    print(json.dumps({"half_sequence": [float(x) for x in hs]}))
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

Enhancements in this iteration:
1) Third-lag safeguard for equalizing step:
   In addition to equalizing the top two worst overlaps (r1 and r2) under a local
   linear model to pick an initial mass-transfer step, we also compute the equalizer
   with respect to the third-worst lag r3. When both equalizer denominators are
   positive, we set the trial step to ε0 = 0.9 * min(eps_eq2, eps_eq3, slack),
   thereby preventing over-equalization that might promote the third lag to worst.

2) Single post-accept expansion:
   After accepting a mass-transfer step via Armijo backtracking, we immediately try
   one additional move in the same donor/receiver direction with an expanded step
   size (e.g., 1.25× the accepted step, capped by the new slack), guarded by the
   same backtracking logic. This captures additional local improvement safely.

All other logic (projection, top-k aggregation, Armijo backtracking) remains unchanged.
"""

import json
import math
import numpy as np


# EVOLVE_START
def generate_erdos_data() -> np.ndarray:
    """Generates a half-sequence optimized to lower Erdős' minimum-overlap upper bound.

    Returns:
        np.ndarray: Half-sequence s of length m, values in [0,1], with s[-1]=0.5 and sum(s)=m/2.
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
    iters = 420
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
        # endpoints (including center; projection will fix center)
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

    # Mass-transfer refinement with top-k aggregated marginal gradients and Armijo backtracking.
    def mass_transfer_refine(s_init, max_accepts=250, top_k=9, t_grad=0.15, c_scale=0.2, backtrack=0.5):
        """Refine s by conservative mass transfers guided by aggregated worst-lag gradients.

        After computing the aggregated gradient over the top-k worst lags and selecting
        donor p (largest positive gradient) and receiver q (most negative), compute exact
        per-lag marginal gradients and set an initial step size using a safe equalizing
        heuristic that accounts for both the second- and third-worst lags. Fall back to a
        gradient-scaled step when equalizers are unusable. Use Armijo-style backtracking
        and exact projection to ensure robustness. After a successful step, try one
        additional expanded move in the same direction with the same safeguards.

        Args:
            s_init: initial half sequence (feasible).
            max_accepts: maximum number of accepted transfers.
            top_k: number of worst lags to aggregate for the gradient.
            t_grad: softmax temperature for weighting top-k overlaps (0.1-0.2 recommended).
            c_scale: scaling constant in epsilon0 = min(slack, c_scale / (|g[p]| + |g[q]|)).
            backtrack: Armijo-style backtracking factor in (0,1).

        Returns:
            (s_refined, best_upper_bound)
        """
        s = project_half(s_init)
        h = half_to_full(s)
        n = h.shape[0]
        conv = np.correlate(h, 1.0 - h, mode='full')
        best_ub = float(np.max(conv)) / n * 2.0

        accept_count = 0

        # Helper: exact gradient of a single lag r at full h, mapped to half s.
        def grad_half_for_lag(s_local, r):
            h_local = half_to_full(s_local)
            n_local = h_local.shape[0]
            g_h = np.zeros_like(h_local)
            if r >= 0:
                sft = r
                g_h[r:] += (1.0 - h_local[:n_local - sft])
                g_h[:n_local - sft] += -h_local[sft:]
            else:
                sft = -r
                g_h[:n_local - sft] += (1.0 - h_local[sft:])
                g_h[sft:] += -h_local[:n_local - sft]
            # Map to half gradient
            g_s = np.zeros_like(s_local)
            two_m2 = 2 * m - 2
            if m > 1:
                idx1 = np.arange(0, m - 1, dtype=int)
                idx2 = two_m2 - idx1
                g_s[idx1] = g_h[idx1] + g_h[idx2]
            g_s[m - 1] = 0.0  # center fixed
            return g_s

        while accept_count < max_accepts:
            # Recompute current overlaps and worst lags
            h = half_to_full(s)
            conv = np.correlate(h, 1.0 - h, mode='full')
            n = h.shape[0]

            # Identify top-k worst lags by correlation magnitude (descending)
            worst_idx_desc = np.argsort(conv)[::-1]
            topk_idx = worst_idx_desc[: min(top_k, conv.shape[0])]
            if topk_idx.size == 0:
                break
            topk_lags = [int(idx) - (n - 1) for idx in topk_idx]
            topk_vals = conv[topk_idx].astype(float)

            # Softmax weights over the top-k overlaps (temperature ~0.1-0.2)
            vmax = float(np.max(topk_vals))
            w_raw = np.exp((topk_vals - vmax) / max(t_grad, 1e-8))
            w = w_raw / float(np.sum(w_raw))

            # Compute exact per-lag gradients once
            grads_per_lag = [grad_half_for_lag(s, r) for r in topk_lags]

            # Aggregate exact marginal gradients g_s = sum_r w_r g_s^{(r)}
            g_s = np.zeros_like(s)
            for wr, g_r in zip(w, grads_per_lag):
                if wr == 0.0:
                    continue
                g_s += wr * g_r

            # Fix center gradient to zero (center value is constrained to 0.5)
            g_s[m - 1] = 0.0

            # Select donor index p (largest positive g_s) and receiver q (most negative g_s)
            if m <= 1:
                break
            mask = np.ones(m, dtype=bool)
            mask[m - 1] = False  # exclude center
            g_free = g_s.copy()
            g_free[~mask] = 0.0

            p = int(np.argmax(g_free))
            q = int(np.argmin(g_free))
            gp = float(g_s[p])
            gq = float(g_s[q])

            # If no positive/negative split or degenerate choice, terminate
            if gp <= 1e-18 or gq >= -1e-18 or p == q:
                break

            # Available slack to move epsilon from p to q without violating [0,1]
            sp = float(s[p])
            sq = float(s[q])
            slack = min(sp - 0.0, 1.0 - sq)
            if slack <= 1e-12:
                break

            # Compute initial step via equalizing with third-lag safeguard when possible.
            # Directional derivatives dM_r/dε = g^{(r)}[q] − g^{(r)}[p]
            eps0 = None
            # Attempt using r2 and r3 if both available and safe
            if len(topk_lags) >= 3:
                M1 = float(topk_vals[0])
                M2 = float(topk_vals[1])
                M3 = float(topk_vals[2])
                g1 = grads_per_lag[0]
                g2 = grads_per_lag[1]
                g3 = grads_per_lag[2]
                dM1 = float(g1[q] - g1[p])
                dM2 = float(g2[q] - g2[p])
                dM3 = float(g3[q] - g3[p])
                denom2 = dM2 - dM1
                denom3 = dM3 - dM1
                if denom2 > 1e-18 and denom3 > 1e-18:
                    eps_eq2 = (M1 - M2) / denom2
                    eps_eq3 = (M1 - M3) / denom3
                    if eps_eq2 > 0 and eps_eq3 > 0 and np.isfinite(eps_eq2) and np.isfinite(eps_eq3):
                        eps0 = 0.9 * min(eps_eq2, eps_eq3, slack)

            # If third-lag guarded equalizer not usable, optionally try 2-lag equalizer as a mild fallback
            if (eps0 is None or eps0 <= 0) and len(topk_lags) >= 2:
                M1 = float(topk_vals[0])
                M2 = float(topk_vals[1])
                g1 = grads_per_lag[0]
                g2 = grads_per_lag[1]
                dM1 = float(g1[q] - g1[p])
                dM2 = float(g2[q] - g2[p])
                denom_eq = dM2 - dM1
                if denom_eq > 1e-18:
                    eps_eq = (M1 - M2) / denom_eq
                    if eps_eq > 0 and np.isfinite(eps_eq):
                        eps0 = min(slack, 0.9 * eps_eq)

            # Fallback to gradient-scaled heuristic when equalizers are not usable
            if eps0 is None or eps0 <= 0:
                denom = abs(gp) + abs(gq)
                if denom > 0:
                    eps0 = min(slack, c_scale / denom)
                else:
                    eps0 = 0.5 * slack  # conservative fallback

            # Ensure minimal positive trial
            eps_try = max(1e-12, eps0)

            # Armijo-style backtracking until the true maximum overlap decreases
            improved = False
            accepted_eps = None
            for _ in range(20):
                s_trial = s.copy()
                s_trial[p] = max(0.0, s_trial[p] - eps_try)
                s_trial[q] = min(1.0, s_trial[q] + eps_try)
                # Exact projection to satisfy box/sum constraints and fixed center
                s_trial = project_half(s_trial)

                h_trial = half_to_full(s_trial)
                conv_trial = np.correlate(h_trial, 1.0 - h_trial, mode='full')
                ub_trial = float(np.max(conv_trial)) / len(h_trial) * 2.0

                if ub_trial + 1e-12 < best_ub:
                    # Accept the move
                    s = s_trial
                    best_ub = ub_trial
                    accept_count += 1
                    improved = True
                    accepted_eps = eps_try
                    break
                else:
                    eps_try *= backtrack  # back off and retry

            if not improved:
                # No improvement found; terminate refinement
                break

            # Single post-accept expansion: try one more move in the same direction with expanded step.
            if accepted_eps is not None:
                # Recompute slack after the accepted move
                sp_new = float(s[p])
                sq_new = float(s[q])
                slack_new = min(sp_new - 0.0, 1.0 - sq_new)
                if slack_new > 1e-12:
                    eps_next = min(slack_new, 1.25 * accepted_eps)
                    tried_second = False
                    for _ in range(15):
                        s_trial2 = s.copy()
                        s_trial2[p] = max(0.0, s_trial2[p] - eps_next)
                        s_trial2[q] = min(1.0, s_trial2[q] + eps_next)
                        s_trial2 = project_half(s_trial2)
                        h_trial2 = half_to_full(s_trial2)
                        conv_trial2 = np.correlate(h_trial2, 1.0 - h_trial2, mode='full')
                        ub_trial2 = float(np.max(conv_trial2)) / len(h_trial2) * 2.0
                        tried_second = True
                        if ub_trial2 + 1e-12 < best_ub:
                            s = s_trial2
                            best_ub = ub_trial2
                            accept_count += 1
                            break
                        else:
                            eps_next *= backtrack
                    # If we couldn't make progress with the expanded move, simply continue.

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

    # Apply mass-transfer refinement with aggregated gradients, third-lag safeguard equalizer,
    # and single post-accept expansion.
    best_s_all, best_ub_all = mass_transfer_refine(
        best_s_all, max_accepts=250, top_k=9, t_grad=0.15, c_scale=0.2, backtrack=0.5
    )

    # Final safety projection
    best_s_all = project_half(best_s_all)
    # Return as a numpy array
    return np.asarray(best_s_all, dtype=float)
# EVOLVE_END


if __name__ == "__main__":
    # Convert to list for JSON serialization
    hs = generate_erdos_data()
    print(json.dumps({"half_sequence": [float(x) for x in hs]}))
```

```text
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
```

```text
0.379005 \leq C_5 \leq 0.380927
```
