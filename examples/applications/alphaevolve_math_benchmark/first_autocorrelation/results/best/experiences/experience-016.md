Design choices that reduced the worst self-convolution peak efficiently and patterns to reuse when minimizing a scale-invariant L∞-type objective over nonnegative sequences under an L1-simplex constraint.

- MoMI-PEAK-FFT: Monotone Mirror-Descent with Peak Equalization and Adaptive Length: On L1-normalized nonnegative sequences, it replaces max(conv(a, a)) with an annealed SmoothMax surrogate Jτ and applies multiplicative mirror-descent updates with centered gradients while computing convolutions and correlations via FFT, yielding O(n log n) iterations and stable simplex-feasible steps.
- Peak equalization pressure: Each iteration identifies lags with b[k] ≥ (1−δ)·max(b), builds a mask r over these peak lags, and adds g_press = corr(r, a) (scaled by λ) to the gradient to down-weight indices that most contribute to the worst convolution peaks.
- Isotonic regression (PAV) projections: In monotone optimization tracks, it periodically projects the sequence onto nondecreasing or nonincreasing cones and renormalizes, leveraging rearrangement to anti-align with the reversed sequence and reduce autocorrelation peaks.
- Greedy mass-redistribution: Every T_g iterations it finds the current argmax lag k*, computes influence s[i]=a[k*−i], and shifts a small mass δ from high-influence donors to low-influence recipients with backtracking, accepting a move only if the true evaluator decreases, thereby directly lowering the active peak of b.
- Adaptive trimming: It trims near-zero tails and renormalizes only when the true objective does not worsen, directly reducing the 2·n factor by lowering n without increasing the peak.
- Multi-length continuation: It resamples promising shapes to nearby lengths (e.g., 512, 576, 600, 640, 672) via CDF-preserving interpolation and performs short refinements, keeping the length that yields the best true objective to avoid length-specific local minima.
- Time management schedule: It allocates roughly 20% of time to exploration with large τ and larger steps, 60% to full annealing with interleaved peak equalization, greedy shifts, and trimming on top candidates, and 20% to small-τ polishing with final greedy-then-trim passes, always tracking the best sequence under the true evaluator.
- For this run, Score equals 0.9481808031023087 and validity equals 1.0, indicating a valid optimization process under the reported setup.
- For this run, eval_time equals 99.4997118099709 seconds, consistent with FFT-accelerated iterations and the described time management.
- Softmax-Minimax FFT Optimizer with Continuation and Peak Shaving: Use a softmax parameterization to enforce nonnegativity and unit-sum while directly minimizing a log-sum-exp surrogate of max(conv(a,a)) with a temperature continuation schedule (τ decreasing from about 1e-2 to about 1e-6) to stabilize early search and sharpen toward the true maximum late.
- Softmax-Minimax FFT Optimizer with Continuation and Peak Shaving: Compute convolution and the surrogate gradient via zero-padded real FFTs for O(n log n) updates to maintain runtime efficiency, which aligned with the observed eval_time of approximately 2.07 seconds in this run.
- Softmax-Minimax FFT Optimizer with Continuation and Peak Shaving: After convergence at the lowest temperature, apply a peak-shaving step that finds the active worst shift k*, identifies top contributing index pairs a[i]·a[k*−i], and redistributes small mass to nearby indices, accepting only changes that reduce the objective; this targeted postprocessing is worth reusing alongside multi-start and coarse-to-fine refinement to escape local minima and lower the worst convolution peak.

```python
#!/usr/bin/env python3
"""Optimized step function search for minimizing the L∞ peak of self-convolution
under L1-normalized nonnegative constraints, using annealed mirror descent with FFTs,
plus isotonic projections, greedy mass redistribution, adaptive trimming, and
multi-length continuation.
"""

import json
import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """
    Generate optimized step function using annealed mirror descent on a smooth
    softmax surrogate of the objective max(conv(a, a)), accelerated with FFTs.

    Augmentations beyond the parent:
    - Optional isotonic (monotone) projection branches (increasing/decreasing).
    - Targeted greedy mass redistribution to reduce the active convolution peak.
    - Adaptive trimming of near-zero tails to reduce n if it does not worsen the true objective.
    - Optional multi-length continuation: resample promising shapes to nearby lengths and refine.
    - Careful time management to stay within the budget.

    Returns:
        np.ndarray of shape (n,): nonnegative, L1-normalized sequence (sum=1),
        then re-normalized again by the caller's evaluation function.
    """
    import time

    rng = np.random.default_rng(123456)

    # Default problem dimension (consistent with provided baseline).
    DEFAULT_N = 600

    # Helper: evaluation proxy using provided evaluate_sequence (later in file).
    def eval_bound(a):
        # Takes a numpy array a, returns float
        return evaluate_sequence(list(map(float, a)))

    # Helper: next power of two for FFT size
    def next_pow_two(m):
        p = 1
        while p < m:
            p <<= 1
        return p

    # Helper: real linear convolution via FFT, returns length len(x)+len(y)-1
    def linconv_fft(x, y, nfft=None):
        lx, ly = len(x), len(y)
        L = lx + ly - 1
        if nfft is None:
            nfft = next_pow_two(L)
        X = np.fft.rfft(x, nfft)
        Y = np.fft.rfft(y, nfft)
        c = np.fft.irfft(X * Y, nfft)
        return c[:L]

    # Helper: cross-correlation corr(x, y) = conv(x, reverse(y))
    # Returns length len(x)+len(y)-1
    def lincorr_fft(x, y, nfft=None):
        y_rev = y[::-1]
        return linconv_fft(x, y_rev, nfft=nfft)

    # Helper: softmax weights of b/tau in a numerically stable way
    def softmax_weights(b, tau):
        m = float(np.max(b))
        z = np.exp((b - m) / max(tau, 1e-12))
        s = float(z.sum())
        if not np.isfinite(s) or s <= 0:
            # Fallback: uniform weights
            return np.full_like(b, 1.0 / len(b))
        return z / s

    # Mild 1D smoothing to avoid spikes
    def smooth1d(a, strength):
        # Tri-diagonal kernel [s, 1-2s, s], reflecting boundaries
        if strength <= 0:
            return a
        s = float(min(max(strength, 0.0), 0.25))
        b = a.copy()
        if len(a) >= 3:
            # interior
            b[1:-1] = s * a[:-2] + (1 - 2 * s) * a[1:-1] + s * a[2:]
            # boundaries (reflecting)
            b[0] = s * a[1] + (1 - 2 * s) * a[0] + s * a[1]
            b[-1] = s * a[-2] + (1 - 2 * s) * a[-1] + s * a[-2]
        elif len(a) == 2:
            # simple averaging
            m = (a[0] + a[1]) * 0.5
            b[0] = (1 - s) * a[0] + s * m
            b[1] = (1 - s) * a[1] + s * m
        else:
            # length 1, nothing to do
            pass
        return np.maximum(b, 0.0)

    # Isotonic regression projection (PAV) onto nondecreasing/decreasing sequences (L2)
    def isotonic_project(y, mode="increasing"):
        if mode not in ("increasing", "decreasing"):
            return y
        if mode == "decreasing":
            y_rev = y[::-1]
            proj_rev = isotonic_project(y_rev, "increasing")
            return proj_rev[::-1]
        # increasing via PAV
        n = len(y)
        means = []
        weights = []
        for i in range(n):
            means.append(float(y[i]))
            weights.append(1)
            # merge while violating monotonicity
            while len(means) >= 2 and means[-2] > means[-1] + 1e-18:
                m = (means[-2] * weights[-2] + means[-1] * weights[-1]) / (weights[-2] + weights[-1])
                w = weights[-2] + weights[-1]
                means[-2] = float(m)
                weights[-2] = w
                means.pop()
                weights.pop()
        # expand
        out = np.empty(n, dtype=float)
        idx = 0
        for m, w in zip(means, weights):
            out[idx:idx + w] = m
            idx += w
        # nonnegativity preserved if y >= 0, still clip
        return np.maximum(out, 0.0)

    # Resample a to new length new_n via linear interpolation on index space
    def resample_linear(a, new_n):
        if new_n <= 2:
            # trivial resampling to avoid degeneracy
            out = np.array([a.sum() / new_n] * new_n, dtype=float)
            return out / max(out.sum(), 1e-18)
        n = len(a)
        if n == new_n:
            b = a.copy()
            return b / max(b.sum(), 1e-18)
        x_old = np.linspace(0.0, 1.0, n)
        x_new = np.linspace(0.0, 1.0, new_n)
        b = np.interp(x_new, x_old, a)
        b = np.maximum(b, 0.0)
        s = b.sum()
        if not np.isfinite(s) or s <= 0:
            b = np.ones(new_n, dtype=float) / new_n
        else:
            b /= s
        return b

    # Initializers (multi-start)
    def init_uniform(n):
        a = np.ones(n, dtype=float)
        return a / a.sum()

    def init_beta_edge(n, alpha):
        # U-shaped profile emphasizing edges; alpha in (0,1) strengthens edges
        i = np.arange(n, dtype=float)
        left = (i + 1.0) ** (alpha - 1.0)
        right = (n - i) ** (alpha - 1.0)
        a = left + right
        a = np.maximum(a, 0.0)
        return a / max(a.sum(), 1e-18)

    def init_edge_gaussians(n, sigma_ratio=0.05):
        # Two Gaussians near edges with small sigma
        x = np.linspace(0.0, 1.0, n)
        sigma = max(float(sigma_ratio), 1e-3)
        g_left = np.exp(-0.5 * ((x - 0.03) / sigma) ** 2)
        g_right = np.exp(-0.5 * ((x - 0.97) / sigma) ** 2)
        a = g_left + g_right
        return a / max(a.sum(), 1e-18)

    def init_smoothed_random(n, strength=0.02):
        a = rng.random(n).astype(float)
        for _ in range(4):
            a = smooth1d(a, strength)
        a = np.maximum(a, 1e-12)
        return a / max(a.sum(), 1e-18)

    def init_skew_edge(n, alpha=0.6, skew=0.15):
        # Edge-emphasized but slightly skewed to spread peaks
        i = np.arange(n, dtype=float)
        left = (i + 1.0) ** (alpha - 1.0)
        right = (n - i) ** (alpha - 1.0)
        a = (1 + skew) * left + (1 - skew) * right
        a = np.maximum(a, 0.0)
        return a / max(a.sum(), 1e-18)

    def init_ramp(n, increasing=True, power=1.0):
        i = np.arange(n, dtype=float) + 1.0
        if increasing:
            a = i ** power
        else:
            a = (n + 1.0 - i) ** power
        a = np.maximum(a, 0.0)
        return a / max(a.sum(), 1e-18)

    # Greedy mass redistribution towards lowering the current max convolution peak
    def greedy_redistribute(a, k_star, max_moves=8, initial_delta=1e-3):
        # a: numpy array, sum=1; k_star: index of argmax of b (0..2n-2)
        n = len(a)
        if n <= 3:
            return a, False, None  # nothing useful
        # Influence s[i] = a[k_star - i] if in range, else 0
        s = np.zeros(n, dtype=float)
        j_idx = k_star - np.arange(n)
        mask = (j_idx >= 0) & (j_idx < n)
        s[mask] = a[j_idx[mask]]
        # donors: high s and with some mass; recipients: low s
        donors = np.argsort(-s)  # descending by influence
        recips = np.argsort(s)   # ascending
        # avoid picking the same index
        # Try a few pairs
        total_improved = False
        best_val = None
        improved_a = a
        delta_base = float(initial_delta)
        # Build small shortlist
        donor_list = [idx for idx in donors if a[idx] > 1e-6][:min(12, n)]
        recip_list = [idx for idx in recips][:min(12, n)]
        moves_done = 0
        for di in donor_list:
            if moves_done >= max_moves:
                break
            for rj in recip_list:
                if moves_done >= max_moves:
                    break
                if rj == di:
                    continue
                # Try moving mass
                delta = min(delta_base, a[di] * 0.5)
                if delta <= 1e-9:
                    continue
                tried = 0
                while tried < 3 and delta > 1e-9:
                    a_trial = improved_a.copy()
                    a_trial[di] -= delta
                    a_trial[rj] += delta
                    # enforce nonnegativity
                    if a_trial[di] < 0:
                        a_trial[di] = 0.0
                    ssum = a_trial.sum()
                    if ssum <= 0 or not np.isfinite(ssum):
                        break
                    a_trial /= ssum
                    val_trial = eval_bound(a_trial)
                    # Initialize best_val lazily
                    if best_val is None:
                        best_val = eval_bound(improved_a)
                    if val_trial + 1e-12 < best_val:
                        improved_a = a_trial
                        best_val = val_trial
                        total_improved = True
                        moves_done += 1
                        break
                    else:
                        tried += 1
                        delta *= 0.5
        return improved_a, total_improved, best_val

    # Trim small-mass tails; accept only if true objective does not worsen
    def try_trim_tails(a, max_tail_mass=0.004, min_keep=32):
        n = len(a)
        if n <= min_keep:
            return a, False, None
        # compute prefix and suffix cumulative masses
        cumsum = np.cumsum(a)
        total = cumsum[-1]
        if total <= 0:
            return a, False, None
        # allow trimming as long as we keep at least min_keep entries
        # find leftmost index such that mass on left <= max_tail_mass
        left_cut = int(np.searchsorted(cumsum, max_tail_mass, side='right'))
        # find rightmost index where suffix mass <= max_tail_mass
        suffix_cumsum = np.cumsum(a[::-1])
        right_cut = int(np.searchsorted(suffix_cumsum, max_tail_mass, side='right'))
        i0 = min(left_cut, n - min_keep)
        i1 = max(n - right_cut, min_keep)
        i0 = max(0, i0)
        i1 = min(n, i1)
        if i1 - i0 < n and (i1 - i0) >= min_keep:
            a_new = a[i0:i1].copy()
            s = a_new.sum()
            if s > 0 and np.isfinite(s):
                a_new /= s
                val_old = eval_bound(a)
                val_new = eval_bound(a_new)
                if val_new + 1e-12 <= val_old:
                    return a_new, True, val_new
        return a, False, None

    # Optimize one start with annealed mirror descent and targeted equalization
    def optimize_start(a0, time_deadline, monotone=None, short=False):
        # If short=True, use fewer iterations for quick refinements.
        # Annealing schedule for softness parameter tau
        if short:
            tau_schedule = [1.0e-3, 5e-4, 2.5e-4, 1.25e-4, 8.0e-5]
            max_iters_per_tau = 50
        else:
            tau_schedule = [2e-3, 1e-3, 5e-4, 2.5e-4, 1.25e-4, 8e-5, 5e-5, 2.5e-5]
            max_iters_per_tau = 80

        a = np.maximum(a0.astype(float), 1e-18)
        a /= max(a.sum(), 1e-18)

        n = len(a)
        Lb = 2 * n - 1  # length of conv(a, a)
        nfft_b = next_pow_two(Lb)
        Lcorr = (2 * n - 1) + n - 1  # len(w)+len(a)-1 = (2n-1) + n - 1 = 3n-2
        nfft_corr = next_pow_two(Lcorr)

        best_a = a.copy()
        best_val = eval_bound(a)

        # Track recent improvements to early-break per temperature
        for ti, tau in enumerate(tau_schedule):
            if time.time() > time_deadline:
                break

            # Learning rates and smoothing depend on tau
            eta = 0.9 * np.sqrt(tau_schedule[0] / max(tau, 1e-12))  # larger steps when smoother
            eta = float(np.clip(eta, 0.25, 1.2))
            eta_press = 0.25 * eta
            smooth_strength = 0.02 * (tau / tau_schedule[0]) ** 0.5

            # Band for defining the "peak set" of b
            peak_band_frac = 0.004 + 0.002 * (ti / max(1, len(tau_schedule) - 1))

            no_improve_count = 0
            # Triggers
            greedy_every = 25
            trim_every = 30
            iso_every = 6 if monotone in ("increasing", "decreasing") else None

            for it in range(max_iters_per_tau):
                if time.time() > time_deadline:
                    break

                # Compute b = conv(a, a)
                b = linconv_fft(a, a, nfft=nfft_b)
                max_b = float(np.max(b))
                argmax_k = int(np.argmax(b))

                # Softmax weights for gradient
                w = softmax_weights(b, tau)

                # Gradient via correlation: g = 2 * corr(w, a) evaluated at lags m=0..n-1
                # corr(w, a) length: 3n - 2, slice indices m -> idx = m + (n - 1)
                corr_full = lincorr_fft(w, a, nfft=nfft_corr)
                g_soft = 2.0 * corr_full[(n - 1):(2 * n - 1)]

                # Peak-equalization pressure: pick indices near the current max in b
                thr = (1.0 - peak_band_frac) * max_b
                peak_mask = (b >= thr)
                if not np.any(peak_mask):
                    peak_idx = int(np.argmax(b))
                    peak_mask = np.zeros_like(b, dtype=bool)
                    peak_mask[peak_idx] = True
                r = peak_mask.astype(float)
                if r.sum() > 1:
                    r = smooth1d(r, 0.05)
                corr_press = lincorr_fft(r, a, nfft=nfft_corr)
                g_press = corr_press[(n - 1):(2 * n - 1)]

                # Combine gradients (soft + targeted)
                g_total = g_soft + eta_press * g_press

                # Optional isotonic projection guidance: push in monotone cone (via projection after update)
                # but here just mirror descent step first.

                # Center gradient to preserve L1 mass under multiplicative update
                g_center = float(np.dot(a, g_total))
                g_total_centered = g_total - g_center

                # Multiplicative update with safeguard clipping
                delta = -eta * g_total_centered
                delta = np.clip(delta, -20.0, 20.0)
                a *= np.exp(delta)

                # Mild smoothing against spikiness
                a = smooth1d(a, smooth_strength)

                # Isotonic projection if required
                if iso_every is not None and (it % iso_every == iso_every - 1):
                    a = isotonic_project(a, mode=monotone)

                # Nonnegativity and renormalize
                a = np.maximum(a, 1e-18)
                s = a.sum()
                if not np.isfinite(s) or s <= 0:
                    # Reset to uniform if degeneracy occurs
                    a = init_uniform(n)
                else:
                    a /= s

                # Occasionally attempt greedy mass redistribution around current k*
                if (it % greedy_every == greedy_every - 1) and (time.time() <= time_deadline - 0.02):
                    a_new, improved, maybe_val = greedy_redistribute(a, argmax_k, max_moves=6, initial_delta=7e-4)
                    if improved:
                        a = a_new
                        val_after = maybe_val if maybe_val is not None else eval_bound(a)
                        if val_after + 1e-12 < best_val:
                            best_val = val_after
                            best_a = a.copy()
                            no_improve_count = 0

                # Occasionally attempt tail trimming to reduce n if harmless
                if (it % trim_every == trim_every - 1) and (time.time() <= time_deadline - 0.02):
                    a_trim, trimmed, val_trim = try_trim_tails(a, max_tail_mass=0.004, min_keep=64)
                    if trimmed:
                        a = a_trim
                        n = len(a)
                        # recompute FFT sizes for new n
                        Lb = 2 * n - 1
                        nfft_b = next_pow_two(Lb)
                        Lcorr = (2 * n - 1) + n - 1
                        nfft_corr = next_pow_two(Lcorr)
                        # update best
                        if val_trim is None:
                            val_trim = eval_bound(a)
                        if val_trim + 1e-12 < best_val:
                            best_val = val_trim
                            best_a = a.copy()

                # Track best by the true evaluation (infrequent check accelerates)
                # Check every 2 iterations to reduce overhead
                if (it % 2 == 1) or (ti == len(tau_schedule) - 1 and it == max_iters_per_tau - 1):
                    current_val = eval_bound(a)
                    if current_val + 1e-9 < best_val:
                        best_val = current_val
                        best_a = a.copy()
                        no_improve_count = 0
                    else:
                        no_improve_count += 1

                # Early stopping if no improvement for a while
                if no_improve_count >= 120:
                    break

        # Return the best found in this run
        # Ensure normalization and nonnegativity
        best_a = np.maximum(best_a, 0.0)
        s = best_a.sum()
        if s <= 0 or not np.isfinite(s):
            best_a = init_uniform(len(best_a))
        else:
            best_a /= s
        return best_a, best_val

    # Build a pool of initializations
    n0 = DEFAULT_N
    inits = [
        (init_uniform(n0), None),
        (init_beta_edge(n0, 0.5), None),
        (init_beta_edge(n0, 0.35), None),
        (init_edge_gaussians(n0, 0.06), None),
        (init_smoothed_random(n0, 0.03), None),
        (init_skew_edge(n0, alpha=0.6, skew=0.12), None),
        (init_ramp(n0, increasing=True, power=1.0), "increasing"),
        (init_ramp(n0, increasing=False, power=1.0), "decreasing"),
    ]

    # Add a few additional random smooth starts
    for _ in range(3):
        inits.append((init_smoothed_random(n0, 0.05), None))

    # Baseline from previous implementation (for fallback)
    def init_baseline_quadratic(n):
        x = np.linspace(-0.25, 0.25, n)
        a = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
        a = (a + a[::-1]) / 2.0
        a = np.maximum(a, 0.0)
        s = a.sum()
        if s > 0:
            a /= s
        else:
            a = init_uniform(n)
        return a

    inits.append((init_baseline_quadratic(n0), None))

    # Time budget (keep under 1000 sec)
    total_budget_sec = 980.0
    import time as _time
    t_start = _time.time()
    t_deadline = t_start + total_budget_sec

    # Track the global best
    global_best_a = None
    global_best_val = np.inf

    # Evaluate and set a safe fallback as current best immediately
    baseline = init_baseline_quadratic(n0)
    baseline_val = eval_bound(baseline)
    global_best_a = baseline.copy()
    global_best_val = float(baseline_val)

    # Optimize each start until time budget is exhausted
    for idx, (a0, mono_flag) in enumerate(inits):
        if _time.time() > t_deadline:
            break
        # Allocate a fair slice of remaining time
        remaining = t_deadline - _time.time()
        starts_left = len(inits) - idx
        per_start = max(remaining / max(starts_left, 1), 18.0)
        per_deadline = min(_time.time() + per_start, t_deadline)

        a_opt, val_opt = optimize_start(a0, per_deadline, monotone=mono_flag, short=False)
        if val_opt + 1e-12 < global_best_val:
            global_best_val = val_opt
            global_best_a = a_opt.copy()

    # Multi-length continuation around the best
    if global_best_a is not None and _time.time() + 25.0 < t_deadline:
        n_best = len(global_best_a)
        # Candidate nearby lengths (unique, reasonable)
        cand_lengths = sorted(set([
            max(64, int(round(n_best * 0.90))),
            max(64, int(round(n_best * 0.96))),
            n_best,
            int(round(n_best * 1.06)),
            int(round(n_best * 1.12)),
        ]))
        # Clamp to avoid excessive sizes
        cand_lengths = [int(nc) for nc in cand_lengths if 64 <= nc <= 900]
        # Dedicate time per candidate
        remaining = t_deadline - _time.time()
        per_cand = max(min(remaining / max(len(cand_lengths), 1), 40.0), 12.0)
        for n_cand in cand_lengths:
            if _time.time() > t_deadline:
                break
            a_seed = resample_linear(global_best_a, n_cand)
            short_deadline = min(_time.time() + per_cand, t_deadline)
            # Try both free and a monotone projection quick pass
            a1, v1 = optimize_start(a_seed, short_deadline, monotone=None, short=True)
            if v1 + 1e-12 < global_best_val:
                global_best_val = v1
                global_best_a = a1.copy()
            if _time.time() > t_deadline:
                break
            # A very quick monotone decreasing pass (often beneficial)
            short_deadline2 = min(_time.time() + max(per_cand * 0.5, 8.0), t_deadline)
            a2, v2 = optimize_start(a1, short_deadline2, monotone="decreasing", short=True)
            if v2 + 1e-12 < global_best_val:
                global_best_val = v2
                global_best_a = a2.copy()

    # Final small-τ polishing if time remains
    if _time.time() + 8.0 < t_deadline and global_best_a is not None:
        a_polish = global_best_a.copy()

        def quick_polish(a0, time_deadline):
            tau_list = [5e-5, 2.5e-5]
            n = len(a0)
            nfft_b = next_pow_two(2 * n - 1)
            nfft_corr = next_pow_two(3 * n - 2)
            a = a0.copy()
            best_a = a.copy()
            best_val = eval_bound(a)
            for tau in tau_list:
                if _time.time() > time_deadline:
                    break
                eta = 0.6
                for _ in range(40):
                    if _time.time() > time_deadline:
                        break
                    b = linconv_fft(a, a, nfft=nfft_b)
                    w = softmax_weights(b, tau)
                    corr_full = lincorr_fft(w, a, nfft=nfft_corr)
                    g_soft = 2.0 * corr_full[(n - 1):(2 * n - 1)]
                    g_center = float(np.dot(a, g_soft))
                    delta = -eta * (g_soft - g_center)
                    delta = np.clip(delta, -20.0, 20.0)
                    a *= np.exp(delta)
                    a = smooth1d(a, 0.01)
                    a = np.maximum(a, 1e-18)
                    a /= a.sum()
                    val = eval_bound(a)
                    if val + 1e-12 < best_val:
                        best_val = val
                        best_a = a.copy()
            return best_a, best_val

        a_final, v_final = quick_polish(a_polish, t_deadline)
        if v_final + 1e-12 < global_best_val:
            global_best_val = v_final
            global_best_a = a_final.copy()

    # Return the best sequence found (nonnegative, L1-normalized)
    out = global_best_a if global_best_a is not None else init_uniform(DEFAULT_N)
    out = np.maximum(out, 0.0)
    s = out.sum()
    if s <= 0 or not np.isfinite(s):
        out = init_uniform(len(out))
    else:
        out = out / s

    return out
# EVOLVE_END


def evaluate_sequence(sequence: list[float]) -> float:
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

```python
#!/usr/bin/env python3
"""Advanced search for a step function minimizing the autocorrelation-based objective.

This script implements a direct minimization of:
  J(a) = 2 * n * max(conv(a, a)) / (sum(a)^2)

Key features:
- Softmax parameterization for nonnegativity and unit-sum constraints.
- Smooth surrogate for the max via log-sum-exp with temperature continuation.
- Efficient FFT-based computation of convolution and its gradient.
- Adam optimizer with AMSGrad, TV smoothing early on, small entropy bonus early.
- Multi-start and multi-resolution (coarse-to-fine) continuation.
- Optional peak-shaving post-processing to reduce the active worst convolution peak.

All sequences are real, nonnegative, and normalized to sum 1.
"""

import json
import time
from typing import Tuple, List

import numpy as np


# EVOLVE_START
def _softmax(x: np.ndarray) -> np.ndarray:
    """Stable softmax returning a probability vector that sums to 1."""
    x = x - np.max(x)
    ex = np.exp(x)
    s = np.sum(ex)
    if s == 0.0 or not np.isfinite(s):
        # Fallback to uniform to avoid degenerate cases
        return np.full_like(x, 1.0 / x.size)
    return ex / s


def _next_pow2(n: int) -> int:
    """Return the next power-of-two >= n."""
    return 1 << int(np.ceil(np.log2(max(1, n))))


def _conv_aperiodic(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Aperiodic linear convolution via real FFT. Returns length len(a) + len(b) - 1."""
    la = a.size
    lb = b.size
    L = _next_pow2(la + lb - 1)
    fa = np.fft.rfft(a, L)
    fb = np.fft.rfft(b, L)
    c = np.fft.irfft(fa * fb, L)
    return c[: la + lb - 1]


def _evaluate_true_objective(a: np.ndarray) -> float:
    """Compute the true target objective J(a) consistent with evaluate_sequence."""
    # Ensure nonnegative and normalize for sum=1
    a = np.clip(a, 0.0, None)
    s = np.sum(a)
    if not np.isfinite(s) or s <= 0.0:
        return np.inf
    a = a / s
    b = _conv_aperiodic(a, a)
    max_b = float(np.max(b))
    n = a.size
    return 2.0 * n * max_b


def _logsumexp(x: np.ndarray, tau: float) -> float:
    """Compute tau * logsumexp(x / tau) in a stable way."""
    m = float(np.max(x))
    z = (x - m) / max(1e-300, tau)
    return float(tau * (m / max(1e-300, tau) + np.log(np.sum(np.exp(z)))))


def _tv_value_and_grad(a: np.ndarray, eps: float = 1e-8) -> Tuple[float, np.ndarray]:
    """Compute smooth TV penalty value and gradient w.r.t. a using sqrt(dx^2 + eps)."""
    n = a.size
    if n <= 1:
        return 0.0, np.zeros_like(a)
    d = a[1:] - a[:-1]
    den = np.sqrt(d * d + eps)
    val = float(np.sum(den))
    # Gradient: interior points gather contributions from left and right diffs
    g = np.zeros_like(a)
    # For i in [1..n-1], contribution from left diff
    g[:-1] += d / den
    # For i in [0..n-2], contribution from right diff with negative sign
    g[1:] -= d / den
    return val, g


class Adam:
    """Simple Adam/AMSGrad optimizer for vectors."""

    def __init__(self, shape: Tuple[int, ...], lr: float = 1e-2, beta1: float = 0.9, beta2: float = 0.999, eps: float = 1e-8, amsgrad: bool = True):
        self.lr = lr
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.m = np.zeros(shape, dtype=np.float64)
        self.v = np.zeros(shape, dtype=np.float64)
        self.t = 0
        self.amsgrad = amsgrad
        self.vhat = np.zeros(shape, dtype=np.float64) if amsgrad else None

    def step(self, params: np.ndarray, grad: np.ndarray):
        self.t += 1
        b1, b2 = self.beta1, self.beta2
        self.m = b1 * self.m + (1 - b1) * grad
        self.v = b2 * self.v + (1 - b2) * (grad * grad)
        mhat = self.m / (1 - b1**self.t)
        vhat = self.v / (1 - b2**self.t)
        if self.amsgrad:
            self.vhat = np.maximum(self.vhat, vhat)
            denom = np.sqrt(self.vhat) + self.eps
        else:
            denom = np.sqrt(vhat) + self.eps
        params -= self.lr * (mhat / denom)
        return params


def _grad_surrogate(a: np.ndarray, tau: float, lam_tv: float, lam_ent: float) -> Tuple[float, np.ndarray]:
    """Compute surrogate objective and gradient w.r.t a:
       L_tau(a) = 2n * tau * logsumexp(b / tau) + lam_tv * TV(a) + lam_ent * sum a log a
       Returns (L_tau, grad_a)
    """
    n = a.size
    # Convolution and smooth weights p = softmax(b / tau)
    b = _conv_aperiodic(a, a)  # length 2n - 1
    # Smooth surrogate objective: 2n * tau * logsumexp(b / tau) (constant shift irrelevant)
    # We'll compute the logsumexp stably
    lse = _logsumexp(b, tau)
    L_main = 2.0 * n * lse
    # softmax(b/tau):
    m = float(np.max(b))
    z = (b - m) / max(1e-300, tau)
    ez = np.exp(z)
    sum_ez = float(np.sum(ez))
    if sum_ez <= 0 or not np.isfinite(sum_ez):
        # Fallback to uniform if numerical issues
        p = np.full_like(b, 1.0 / b.size)
    else:
        p = ez / sum_ez

    # Gradient wrt a of sum_k p[k] b[k] is 2 * sum_j a[j] p[m + j], for each m
    # Compute c = conv(a, reverse(p)), and extract slice reversed as derived:
    pr = p[::-1]
    c = _conv_aperiodic(a, pr)  # length 3n - 2
    c_slice = c[n - 1 : 2 * n - 1][::-1]  # length n
    grad_main = 2.0 * 2.0 * n * c_slice  # factor 2 for symmetry, and 2n from outer coefficient

    # TV penalty
    tv_val, tv_grad = _tv_value_and_grad(a, eps=1e-8)
    L_tv = lam_tv * tv_val
    grad_tv = lam_tv * tv_grad

    # Entropy penalty (encourage dispersion): lam_ent * sum a log a
    # For a_i very small, add epsilon to avoid -inf; gradient: log(a_i + eps) + 1
    eps = 1e-16
    L_ent = lam_ent * float(np.sum(a * np.log(a + eps)))
    grad_ent = lam_ent * (np.log(a + eps) + 1.0)

    L_total = L_main + L_tv + L_ent
    grad_total = grad_main + grad_tv + grad_ent
    return L_total, grad_total


def _optimize_softmax(
    a0: np.ndarray,
    time_deadline: float,
    tau_schedule: List[float],
    steps_per_tau: int = 80,
    lr0: float = 5e-2,
    tv0: float = 1e-2,
    ent0: float = 1e-3,
    verbose: bool = False,
) -> Tuple[np.ndarray, float]:
    """Optimize the surrogate objective L_tau(a) with Adam over softmax logits s.
    Returns best a and its true objective.
    """
    n = a0.size
    # Initialize logits from a0 (ensure positivity)
    a0 = np.clip(a0, 1e-12, None)
    a0 = a0 / np.sum(a0)
    s = np.log(a0)  # since softmax(s) ∝ exp(s)

    # Adam optimizer over logits
    opt = Adam(shape=s.shape, lr=lr0, amsgrad=True)
    best_a = _softmax(s)
    best_score = _evaluate_true_objective(best_a)
    last_improve_it = 0
    it = 0

    for t_idx, tau in enumerate(tau_schedule):
        # Anneal TV and entropy
        # High tau -> stronger smoothing; low tau -> turn off
        phase = t_idx / max(1.0, len(tau_schedule) - 1)
        lam_tv = tv0 * (1.0 - phase)  # decays to 0
        lam_ent = ent0 * (1.0 - phase)  # decays to 0
        # Anneal learning rate slightly
        opt.lr = lr0 * (0.8 ** t_idx)

        for _ in range(steps_per_tau):
            if time.time() > time_deadline:
                # Return best so far
                return best_a, best_score

            it += 1
            # Current a
            a = _softmax(s)

            # Objective and gradient wrt a
            L_tau, grad_a = _grad_surrogate(a, tau, lam_tv=lam_tv, lam_ent=lam_ent)
            # Chain rule through softmax: dL/ds_j = a_j * (grad_a_j - <grad_a, a>)
            ga_dot_a = float(np.dot(grad_a, a))
            grad_s = a * (grad_a - ga_dot_a)

            # Step
            s = opt.step(s, grad_s)

            # Track best by true objective
            if it % 10 == 0:
                score = _evaluate_true_objective(a)
                if score < best_score - 1e-6:
                    best_score = score
                    best_a = a.copy()
                    last_improve_it = it

            # Early stop if no improvement for too long in this phase
            if it - last_improve_it > max(200, 5 * steps_per_tau) and t_idx > 0:
                break

    return best_a, best_score


def _upsample_linear(a: np.ndarray, new_n: int) -> np.ndarray:
    """Linearly upsample a to length new_n and renormalize."""
    n = a.size
    if new_n == n:
        return a.copy()
    x_old = np.linspace(0.0, 1.0, n, endpoint=True)
    x_new = np.linspace(0.0, 1.0, new_n, endpoint=True)
    a_new = np.interp(x_new, x_old, a)
    a_new = np.clip(a_new, 0.0, None)
    s = np.sum(a_new)
    if s <= 0:
        a_new = np.full(new_n, 1.0 / new_n)
    else:
        a_new = a_new / s
    return a_new


def _peak_shaving(a: np.ndarray, max_trials: int = 40, delta_frac: float = 0.25) -> np.ndarray:
    """Heuristic: reduce the current maximum convolution peak by moving mass away from top contributing pair.
    - Iteratively identify k* and top contributing pair (i, k*-i).
    - Move a small delta mass from both indices to adjacent ones to alter the sum away from k*.
    - Accept only if the true objective decreases.
    """
    n = a.size
    a = np.clip(a, 0.0, None)
    a = a / max(1e-300, np.sum(a))
    best = a.copy()
    best_val = _evaluate_true_objective(best)

    for _ in range(max_trials):
        b = _conv_aperiodic(best, best)
        kstar = int(np.argmax(b))
        # Find top contributing pair to b[kstar]
        i_min = max(0, kstar - (n - 1))
        i_max = min(n - 1, kstar)
        idxs = np.arange(i_min, i_max + 1)
        jdxs = kstar - idxs
        contrib = best[idxs] * best[jdxs]
        top_idx = int(idxs[np.argmax(contrib)])
        top_jdx = int(kstar - top_idx)

        # If either has too little mass, stop
        if best[top_idx] <= 1e-9 or best[top_jdx] <= 1e-9:
            break

        # Delta to move from each location
        base_delta = min(best[top_idx], best[top_jdx]) * (delta_frac * 0.5)
        base_delta = float(max(base_delta, 1e-8))

        # Generate a few candidate redistribution patterns
        candidates = []
        moves = [
            (-1, -1),
            (+1, +1),
            (-1, +1),
            (+1, -1),
            (-2, +2),
            (+2, -2),
        ]
        for di, dj in moves:
            i_to = np.clip(top_idx + di, 0, n - 1)
            j_to = np.clip(top_jdx + dj, 0, n - 1)
            cand = best.copy()
            # Remove from top pair
            d = base_delta
            d = min(d, cand[top_idx])
            d = min(d, cand[top_jdx])
            if d <= 0:
                continue
            cand[top_idx] -= d
            cand[top_jdx] -= d
            # Redistribute equally to i_to, j_to
            cand[i_to] += d
            cand[j_to] += d
            # Renormalize (tiny drift)
            cand = np.clip(cand, 0.0, None)
            cand /= max(1e-300, np.sum(cand))
            candidates.append(cand)

        # Evaluate candidates and accept best improvement
        improved = False
        for cand in candidates:
            val = _evaluate_true_objective(cand)
            if val + 1e-12 < best_val:
                best_val = val
                best = cand
                improved = True
                break
        if not improved:
            # No improvement; reduce delta for next attempt
            delta_frac *= 0.5
            if delta_frac < 1e-3:
                break

    return best


def search_for_best_sequence():
    """Run a multi-start, multi-resolution optimizer to minimize the objective."""
    rng = np.random.default_rng(123456789)

    # Global time budget (seconds)
    time_budget = 980.0
    t0 = time.time()
    deadline = t0 + time_budget

    # Multi-resolution ladder
    lengths = [384, 512, 600, 768, 960]  # modest sizes to keep runtime reasonable

    # Tau continuation schedule (from smooth to sharp)
    # Typical conv max ~ 1/n, so initial tau around 2/n, down to ~1e-6
    def make_tau_schedule(n: int) -> List[float]:
        base = max(5e-4, 2.5 / max(10.0, n))  # ~2.5/n
        taus = [base, base * 0.3, base * 0.1, base * 0.03, base * 0.01, 1e-5, 1e-6]
        # Ensure strictly decreasing positive
        return [float(max(1e-8, t)) for t in taus]

    # Initialize seeds at the coarsest resolution
    n0 = lengths[0]
    uniform = np.full(n0, 1.0 / n0, dtype=np.float64)
    ramp_up = np.linspace(1.0, 2.0, n0)
    ramp_up /= np.sum(ramp_up)
    ramp_down = np.linspace(2.0, 1.0, n0)
    ramp_down /= np.sum(ramp_down)
    triangle = np.bartlett(n0)
    if np.sum(triangle) > 0:
        triangle /= np.sum(triangle)
    else:
        triangle = uniform.copy()
    dirichlet_bal = rng.dirichlet(alpha=np.ones(n0, dtype=np.float64) * 1.0)
    dirichlet_sparse = rng.dirichlet(alpha=np.ones(n0, dtype=np.float64) * 0.5)

    seeds = [uniform, ramp_up, ramp_down, triangle, dirichlet_bal, dirichlet_sparse]

    global_best_a = uniform.copy()
    global_best_score = _evaluate_true_objective(global_best_a)

    # Optimize at coarse resolution for each seed
    for seed in seeds:
        if time.time() > deadline:
            break
        tau_sched = make_tau_schedule(n0)
        a_best_seed, score_seed = _optimize_softmax(
            seed,
            time_deadline=deadline,
            tau_schedule=tau_sched,
            steps_per_tau=80,
            lr0=8e-2,
            tv0=5e-3,
            ent0=1e-3,
            verbose=False,
        )
        if score_seed < global_best_score:
            global_best_score = score_seed
            global_best_a = a_best_seed

    # Progressively upsample and refine
    current = global_best_a.copy()
    for n in lengths[1:]:
        if time.time() > deadline:
            break
        # Upsample current best to new length
        current = _upsample_linear(current, n)
        # Create a few local perturbation starts
        starts = [current]
        # Mild noise around current
        for _ in range(2):
            noise = rng.normal(0.0, 0.01, size=n)
            cand = np.clip(current + noise, 1e-12, None)
            cand /= np.sum(cand)
            starts.append(cand)
        # A fresh random seed at this resolution
        starts.append(rng.dirichlet(alpha=np.ones(n) * 0.8))

        # Refine each start
        for start in starts:
            if time.time() > deadline:
                break
            tau_sched = make_tau_schedule(n)
            a_ref, score_ref = _optimize_softmax(
                start,
                time_deadline=deadline,
                tau_schedule=tau_sched,
                steps_per_tau=70,
                lr0=6e-2,
                tv0=3e-3,
                ent0=5e-4,
                verbose=False,
            )
            # Peak shaving small refinement
            a_ref2 = _peak_shaving(a_ref, max_trials=25, delta_frac=0.25)
            score_ref2 = _evaluate_true_objective(a_ref2)
            if score_ref2 < score_ref:
                a_ref, score_ref = a_ref2, score_ref2

            if score_ref < global_best_score:
                global_best_score = score_ref
                global_best_a = a_ref
                current = a_ref

    # Final tiny sharpening at the best length with very small tau
    if time.time() < deadline:
        n = global_best_a.size
        tau_sched = [max(1e-7, 1.0 / (n * 1e6)), 1e-7, 1e-8]
        a_final, score_final = _optimize_softmax(
            global_best_a,
            time_deadline=deadline,
            tau_schedule=tau_sched,
            steps_per_tau=30,
            lr0=3e-2,
            tv0=1e-3,
            ent0=0.0,
            verbose=False,
        )
        # One more quick peak shave
        a_final2 = _peak_shaving(a_final, max_trials=20, delta_frac=0.2)
        score_final2 = _evaluate_true_objective(a_final2)
        if score_final2 < score_final:
            global_best_a = a_final2
            global_best_score = score_final2
        else:
            global_best_a = a_final
            global_best_score = score_final

    # Return the best found sequence (already normalized)
    return global_best_a
# EVOLVE_END


def evaluate_sequence(sequence: list[float]) -> float:
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
    best = search_for_best_sequence()
    # Ensure it is a list of floats and normalized
    best = np.asarray(best, dtype=np.float64)
    best = np.clip(best, 0.0, None)
    s = float(np.sum(best))
    if not np.isfinite(s) or s <= 0.0:
        # Fallback to uniform of length 600
        n = 600
        best = np.full(n, 1.0 / n, dtype=np.float64)
    else:
        best /= s
    return list(map(float, best))


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```
