Insight from a mutation that added shoulder-enriched active sets, flattened-top weighting (p=1.5), and targeted preconditioned rounding on a finer grid, evaluated by the np.correlate-based upper bound.

- Epsilon-active set with shoulder enrichment: In both the softmax and polishing phases, include the immediate neighbor lags (±1 indices) of each epsilon-active lag, then cap by the existing score-based prioritization, to capture the ridge around the maximizer and suppress oscillations when adjacent lags alternate as the worst case.
- Flattened-top weighting inside the active set: Replace uniform/softmax weights with centered excess weights w_L ∝ max(conv[L] − mean_A, 0)^p using p = 1.5, normalize them, and use the same rule to form the Clarke-subdifferential proxy so that near-tied max constraints are equalized and the Chebyshev bottleneck is pushed down more effectively.
- Targeted, preconditioned rounding: Apply indexwise rounding u_i ← u_i + γ0 · P[i] · tanh(3(u_i − 0.5)) so that rounding pressure concentrates on ambiguous entries near 0.5 while stability is preserved in heavily constrained regions via the preconditioner P[i].
- Discretization grid: Increase the fine grid from N = 401 (M = 201) to N = 501 (M = 251) to reduce discretization error without changing the overall structure of the procedure.
- FAST-R: Flatten-and-Shoulder Active-Set with Targeted Rounding: Under these tweaks the evaluator reported score 0.998755145689182 with upper bound 0.3814017896620145, validity 1.0, and eval_time 177.06651521800086, with the target upper bound set at 0.380927.

```python
#!/usr/bin/env python3
"""Initial relaxed sequence for the minimum-overlap problem.

This implementation constructs a palindromic step sequence h over [0, 2]
discretized to an odd grid length N = 2M-1. The caller mirrors the returned
half sequence to reconstruct the full sequence, which preserves the integral
constraint and minimizes the maximum discrete convolution
max_k sum_i h[i] * (1 - h[i+k]) as closely as possible.

We combine a smooth maximization surrogate (log-sum-exp over all shifts) with
projected gradient steps, followed by a sharper subgradient polishing that
directly reduces the discrete max overlap used by the evaluator. We enforce:
- palindromic structure via half-parameterization,
- box constraints 0 <= h <= 1,
- exact integral constraint (sum(h) = N / 2),
with an exact projection onto the affine hyperplane under box constraints using
a monotone bisection on the Lagrange multiplier (works because coefficients a_i
are strictly positive). To stabilize, we adopt an epsilon-active set of lags to
approximate the Clarke subdifferential at the nondifferentiable max and we
precondition the subgradient by a correlation-degree per index to balance
updates across heavily/lightly constrained indices. We also add a structure-
aware five-plateau seed that mirrors Haugland-like constructions to reach good
basins.

We perform a coarse-to-fine continuation by resampling a palindromic solution
from a coarser grid to a finer grid and refining it.

Mutation tweaks integrated:
1) Epsilon-active set with shoulder enrichment: after selecting epsilon-active
   lags, we include their immediate neighbors (±1 in lag index), capturing local
   ridges and reducing oscillatory maximizer switching.
2) Flattened-top weighting for the active set: weights within the active set are
   w_L ∝ max(conv[L] − mean_A, 0)^p (p=1.5), both in the soft surrogate and in
   the polishing phase, to flatten the plateau of near-max lags more effectively.
3) Targeted, preconditioned rounding and finer grid: replace uniform rounding by
   u_i ← u_i + γ0 · P[i] · tanh(3(u_i − 0.5)) using the same correlation-degree
   preconditioner P, and increase fine grid to N=501 (M=251).

The output "half_sequence" is the first half (length M) whose mirror reconstructs
the length-N sequence:
  full = concat(half[:-1], reversed(half))
"""

import json
import numpy as np


# EVOLVE_START
def generate_erdos_data() -> np.ndarray:
    """Generates a sequence of coefficients for Erdős' minimum overlap problem.

    Implements the following improvements:
    - Epsilon-active set subgradient polishing with a shrinking activity band,
      enriched with immediate-neighbor lags (shoulders).
    - Per-index correlation-degree preconditioning of the subgradient.
    - Flattened-top weighting on epsilon-active lags for both softmax surrogate
      and polishing phases (centered power weights with p=1.5).
    - Targeted, preconditioned rounding using tanh pressure near 0.5.
    - Adds a palindromic five-plateau (Haugland-like) seed to the init pool.
    - Finer final grid N=501 (M=251) to reduce discretization error.

    Returns:
      np.ndarray: First half of a palindromic sequence h of length M, in [0,1],
                  such that when mirrored as in the pipeline:
                  final_sequence = np.concatenate((u[:-1], u[::-1])),
                  it satisfies sum(final_sequence) = len(final_sequence) / 2.
    """
    rng = np.random.default_rng(2026)

    # Grid sizes: coarse for exploration; fine for final polishing.
    M_coarse = 161  # N_coarse = 321
    M_fine = 251    # N_fine   = 501

    # Build weights for the affine sum constraint of palindromic half u:
    # 2 * sum(u[:-1]) + u[-1] = N/2, where N = 2M - 1
    def affine_weights(M: int) -> tuple[np.ndarray, float]:
        N = 2 * M - 1
        a = np.ones(M) * 2.0
        a[-1] = 1.0
        b = N / 2.0
        return a, b

    # Exact projection onto {u in [0,1]^M : a^T u = b} via Lagrange multiplier bisection.
    # Because all a_i > 0, u*(mu) = clip(v - mu * a, 0, 1) yields a monotone mapping S(mu) = a^T u.
    def project_box_and_affine(v: np.ndarray, a: np.ndarray, b: float) -> np.ndarray:
        v = np.asarray(v, dtype=float)
        a = np.asarray(a, dtype=float)
        v = np.clip(v, 0.0, 1.0)
        S = float(np.dot(a, v))
        if abs(S - b) <= 1e-12:
            return v

        with np.errstate(divide="ignore", invalid="ignore"):
            mu_to_zero = v / a           # mu at which entry hits 0
            mu_to_one = (v - 1.0) / a    # mu at which entry hits 1

        finite_zero = mu_to_zero[~np.isnan(mu_to_zero)]
        finite_one = mu_to_one[~np.isnan(mu_to_one)]
        mu_lo = (np.min(finite_one) - 1.0) if finite_one.size else -1.0
        mu_hi = (np.max(finite_zero) + 1.0) if finite_zero.size else 1.0

        def S_of_mu(mu: float) -> float:
            u = v - mu * a
            u = np.clip(u, 0.0, 1.0)
            return float(np.dot(a, u))

        slo = S_of_mu(mu_lo)
        shi = S_of_mu(mu_hi)
        expand = 0
        while (slo < b or shi > b) and expand < 60:
            if slo < b:
                mu_lo -= 2.0
                slo = S_of_mu(mu_lo)
            if shi > b:
                mu_hi += 2.0
                shi = S_of_mu(mu_hi)
            expand += 1

        for _ in range(80):
            mu_mid = 0.5 * (mu_lo + mu_hi)
            s_mid = S_of_mu(mu_mid)
            if s_mid > b:
                mu_lo = mu_mid
            else:
                mu_hi = mu_mid
            if abs(mu_hi - mu_lo) <= 1e-12:
                break
        mu = 0.5 * (mu_lo + mu_hi)
        u = v - mu * a
        u = np.clip(u, 0.0, 1.0)
        return u

    # Build full palindromic sequence from half u
    def build_full(u: np.ndarray) -> np.ndarray:
        return np.concatenate([u[:-1], u[::-1]])

    # FFT-based correlation: corr_full(x, y)[k] = sum_i x[i] * y[i+k] (0 out-of-bounds)
    # Using convolution with reversed y to implement correlation efficiently.
    def corr_full_fft(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        n = len(x) + len(y) - 1
        nfft = 1 << ((n - 1).bit_length())
        X = np.fft.rfft(x, nfft)
        Y = np.fft.rfft(y[::-1], nfft)
        conv = np.fft.irfft(X * Y, nfft)[:n]
        return conv

    # Compute correlation values and the maximum (raw, without normalization)
    def compute_conv_full(h: np.ndarray) -> np.ndarray:
        return corr_full_fft(h, 1.0 - h)

    def compute_conv_max(h: np.ndarray) -> tuple[float, np.ndarray]:
        conv = compute_conv_full(h)
        return float(np.max(conv)), conv

    # Gradient on h for a given integer lag L (matching conv[L] = sum_i h[i]*(1-h[i+L]))
    def grad_h_for_lag(h: np.ndarray, L: int) -> np.ndarray:
        Nloc = h.shape[0]
        g = np.zeros_like(h)
        idx = np.arange(Nloc)
        if L == 0:
            # c0 = sum h[i]*(1-h[i]); grad = d/dh[i] = 1 - 2h[i]
            g = 1.0 - 2.0 * h
            return g
        left = idx - L
        right = idx + L
        mask_left = (left >= 0) & (left < Nloc)   # j-L valid
        mask_right = (right >= 0) & (right < Nloc)  # j+L valid
        # Contribution when j is the "left" index i=j: +1 - h[j+L]
        g[mask_right] += (1.0 - h[right[mask_right]])
        # Contribution when j is the "right" index i=j-L: -h[j-L]
        g[mask_left] -= h[left[mask_left]]
        return g

    # Map gradient on full h to gradient on u (half variables)
    def grad_u_from_grad_h(gh: np.ndarray, M: int) -> np.ndarray:
        N = 2 * M - 1
        gu = np.zeros(M, dtype=float)
        for m in range(M):
            i1 = m
            i2 = N - 1 - m
            if i1 == i2:
                gu[m] = gh[i1]
            else:
                gu[m] = gh[i1] + gh[i2]
        return gu

    # Objective (normalized upper bound) used to rank candidates
    def objective_from_u(u: np.ndarray) -> float:
        h = build_full(u)
        conv_max, _ = compute_conv_max(h)
        return conv_max / (len(h) * 2.0)

    # Helper: epsilon-active set of lags based on current conv and a band fraction.
    # Returns a sorted list of integer lags (by descending conv value) capped by max_cap.
    # Shoulder enrichment: include ±1 neighbors (in conv-index space).
    def epsilon_active_lags(conv: np.ndarray, band_frac: float, max_cap: int) -> list[int]:
        m = float(np.max(conv))
        thresh = m - band_frac * m
        idx = np.nonzero(conv >= thresh)[0]
        if idx.size == 0:
            idx = np.array([int(np.argmax(conv))], dtype=int)
        # Sort by descending conv values
        idx_sorted = idx[np.argsort(conv[idx])[::-1]]
        # Shoulder enrichment: include neighbors ±1
        full_len = conv.shape[0]
        enriched = set()
        for k in idx_sorted:
            enriched.add(int(k))
            km = k - 1
            kp = k + 1
            if km >= 0:
                enriched.add(int(km))
            if kp < full_len:
                enriched.add(int(kp))
        # Sort enriched indices by conv value descending
        enriched_list = sorted(list(enriched), key=lambda x: conv[x], reverse=True)
        # Cap by max_cap
        if len(enriched_list) > max_cap:
            enriched_list = enriched_list[:max_cap]
        # Map to lags
        N = (len(conv) + 1) // 2
        lags = [int(k - (N - 1)) for k in enriched_list]
        return lags

    # Helper: degree-preconditioner P[i] = 1/sqrt(1 + d[i]), where d[i] counts active contributions.
    def degree_preconditioner(N: int, active_lags: list[int]) -> np.ndarray:
        d = np.zeros(N, dtype=float)
        idx = np.arange(N)
        for L in active_lags:
            left = idx - L
            right = idx + L
            mask_left = (left >= 0) & (left < N)
            mask_right = (right >= 0) & (right < N)
            d[mask_left] += 1.0
            d[mask_right] += 1.0
        P = 1.0 / np.sqrt(1.0 + d)
        return P

    # Enrich a given set of lags by adding their shoulder neighbors ±1 (lag space),
    # mapped to valid conv indices.
    def shoulder_enrich_lags(lag_list: list[int], N_full: int, conv_len: int) -> list[int]:
        # Map lags to k-indices
        base_k = [L + (N_full - 1) for L in lag_list]
        # Add neighbors
        kset = set()
        for k in base_k:
            if 0 <= k < conv_len:
                kset.add(int(k))
            km = k - 1
            kp = k + 1
            if 0 <= km < conv_len:
                kset.add(int(km))
            if 0 <= kp < conv_len:
                kset.add(int(kp))
        # Convert back to lags and sort
        lags = sorted([int(k - (N_full - 1)) for k in kset])
        return lags

    # Build preconditioning weights for u (half) based on P over h.
    def precond_u_from_active_lags(M: int, active_lags: list[int]) -> np.ndarray:
        N = 2 * M - 1
        P_h = degree_preconditioner(N, active_lags)
        Pu = np.zeros(M, dtype=float)
        for m in range(M):
            i1 = m
            i2 = N - 1 - m
            if i1 == i2:
                Pu[m] = P_h[i1]
            else:
                Pu[m] = 0.5 * (P_h[i1] + P_h[i2])
        return Pu

    # Apply targeted, preconditioned rounding pressure around 0.5 using tanh.
    def apply_preconditioned_rounding(u: np.ndarray, Pu: np.ndarray, a: np.ndarray, b: float, gamma: float) -> np.ndarray:
        nudged = u + gamma * Pu * np.tanh(3.0 * (u - 0.5))
        nudged = np.clip(nudged, 0.0, 1.0)
        nudged = project_box_and_affine(nudged, a, b)
        return nudged

    # Smooth surrogate optimization with log-sum-exp softmax over shifts,
    # aligned to an epsilon-active set to better track the nondifferentiable max.
    def run_softmax_optimization(
        u0: np.ndarray,
        iters: int,
        tau_start: float,
        tau_end: float,
        alpha_start: float,
        alpha_end: float,
    ) -> np.ndarray:
        u = u0.copy()
        M = len(u)
        a, b = affine_weights(M)
        u = project_box_and_affine(np.clip(u, 0.0, 1.0), a, b)

        best_u = u.copy()
        h = build_full(u)
        best_conv_max, _ = compute_conv_max(h)

        # Cap for the adaptive active-set size in the softmax phase
        cap_soft = 64
        p_flat = 1.5  # power for flattened-top weighting

        for t in range(iters):
            # Temperature and step schedule
            frac = t / max(1, iters - 1)
            tau = tau_start * (1 - frac) + tau_end * frac
            alpha = alpha_start * (1 - frac) + alpha_end * frac

            h = build_full(u)
            conv = compute_conv_full(h)
            m = float(np.max(conv))

            # Softmax weights across all lags (for selection ranking)
            z = np.exp((conv - m) / max(1e-8, tau))
            w = z / max(1e-16, np.sum(z))

            # Build epsilon-active set aligned with the current max (band shrinks slightly)
            band_frac = 0.004 * (1 - frac) + 0.0015 * frac
            A_lags = epsilon_active_lags(conv, band_frac=band_frac, max_cap=cap_soft)

            # Also include the top-weighted lags to ensure coverage
            q_top = min(24, 2 * len(h) - 1)
            idx_sorted = np.argsort(w)[::-1][:q_top]
            N = len(h)
            top_lags = [int(k - (N - 1)) for k in idx_sorted]

            # Union sets
            lag_set = list(dict.fromkeys(A_lags + top_lags))  # preserve order

            # Shoulder enrichment on the union (ensure ±1 neighbors)
            lag_set = shoulder_enrich_lags(lag_set, N_full=N, conv_len=len(conv))

            # Cap by conv score prioritization
            if len(lag_set) > cap_soft:
                k_from_L = [L + (N - 1) for L in lag_set]
                scores = [conv[k] for k in k_from_L]
                order = np.argsort(scores)[::-1]
                lag_set = [lag_set[i] for i in order[:cap_soft]]

            # Compute gradient on h with flattened-top centered power weights over active subset
            gh = np.zeros_like(h)
            if not lag_set:
                # Safety: fallback to top few by softmax
                idx_sorted = np.argsort(w)[::-1][:min(32, 2 * N - 1)]
                lag_set = [int(k - (N - 1)) for k in idx_sorted]

            k_subset = [L + (N - 1) for L in lag_set]
            conv_subset = conv[k_subset]
            mu = float(np.mean(conv_subset))
            svals = np.maximum(conv_subset - mu, 0.0) ** p_flat
            if np.all(svals <= 1e-20):
                w_sub = np.ones_like(svals) / len(svals)
            else:
                w_sub = svals / np.sum(svals)

            for w_k, L in zip(w_sub, lag_set):
                gh += w_k * grad_h_for_lag(h, L)

            gu = grad_u_from_grad_h(gh, M)

            # Backtracking projected step
            success = False
            step = alpha
            curr_max = m
            for _ in range(6):
                uc = u - step * gu
                uc = np.clip(uc, 0.0, 1.0)
                uc = project_box_and_affine(uc, a, b)
                hc = build_full(uc)
                cand_max, _ = compute_conv_max(hc)
                if cand_max <= curr_max + 1e-9:
                    u = uc
                    success = True
                    if cand_max < best_conv_max - 1e-9:
                        best_conv_max = cand_max
                        best_u = uc.copy()
                    break
                step *= 0.5
            if not success:
                u = project_box_and_affine(np.clip(u - alpha_end * gu, 0.0, 1.0), a, b)

            # Periodic preconditioned rounding to push away from 0.5 while preserving affine constraint
            if (t + 1) % max(20, iters // 6) == 0:
                # Use current active lags to build Pu
                Pu = precond_u_from_active_lags(M, lag_set if lag_set else [0])
                gamma = 0.02
                u = apply_preconditioned_rounding(u, Pu, a, b, gamma)

        return best_u

    # Sharper subgradient polishing directly minimizing the max correlation using
    # an epsilon-active set of lags and degree preconditioning.
    def run_subgradient_polish(u0: np.ndarray, iters: int = 1800) -> np.ndarray:
        u = u0.copy()
        M = len(u)
        a, b = affine_weights(M)
        u = project_box_and_affine(np.clip(u, 0.0, 1.0), a, b)

        h = build_full(u)
        best_conv_max, _ = compute_conv_max(h)
        best_u = u.copy()

        # Initial step size
        alpha = 0.15
        # Active-set cap
        cap_active = 80
        p_flat = 1.5

        for t in range(iters):
            h = build_full(u)
            conv_max, conv = compute_conv_max(h)

            # Epsilon-active set with a shrinking band and shoulder enrichment
            frac = t / max(1, iters - 1)
            band_frac = 0.005 * (1 - frac) + 0.001 * frac
            active_lags = epsilon_active_lags(conv, band_frac=band_frac, max_cap=cap_active)
            if not active_lags:
                N = len(h)
                active_lags = [int(np.argmax(conv) - (N - 1))]
            # Enrich with shoulders
            active_lags = shoulder_enrich_lags(active_lags, N_full=len(h), conv_len=len(conv))
            # Cap by conv score prioritization if needed
            if len(active_lags) > cap_active:
                N = len(h)
                k_from_L = [L + (N - 1) for L in active_lags]
                scores = [conv[k] for k in k_from_L]
                order = np.argsort(scores)[::-1]
                active_lags = [active_lags[i] for i in order[:cap_active]]

            # Flattened-top weighting within active set: centered power weights
            N = len(h)
            k_subset = [L + (N - 1) for L in active_lags]
            conv_subset = conv[k_subset]
            mu = float(np.mean(conv_subset))
            svals = np.maximum(conv_subset - mu, 0.0) ** p_flat
            if np.all(svals <= 1e-20):
                w_sub = np.ones_like(svals) / len(svals)
            else:
                w_sub = svals / np.sum(svals)

            # Form the subgradient on h using weighted combination over active lags
            gh = np.zeros_like(h)
            for w_k, L in zip(w_sub, active_lags):
                gh += w_k * grad_h_for_lag(h, L)

            # Correlation-degree preconditioning: P[i] = 1/sqrt(1 + d[i])
            P = degree_preconditioner(len(h), active_lags)
            gh *= P

            gu = grad_u_from_grad_h(gh, M)

            # Backtracking line search
            success = False
            local_alpha = alpha
            for _ in range(6):
                u_candidate = np.clip(u - local_alpha * gu, 0.0, 1.0)
                u_candidate = project_box_and_affine(u_candidate, a, b)
                h_candidate = build_full(u_candidate)
                cand_max, _ = compute_conv_max(h_candidate)
                if cand_max <= conv_max + 1e-9:
                    u = u_candidate
                    if cand_max < best_conv_max - 1e-9:
                        best_conv_max = cand_max
                        best_u = u_candidate.copy()
                    success = True
                    break
                local_alpha *= 0.5
            if not success:
                u = project_box_and_affine(np.clip(u - local_alpha * gu, 0.0, 1.0), a, b)

            # Step size anneal
            if (t + 1) % 200 == 0:
                alpha = max(0.02, alpha * 0.9)

            # Occasional jitter and preconditioned rounding to escape shallow kinks and promote binarity
            if (t + 1) % 450 == 0:
                jitter = rng.normal(0.0, 5e-4, size=M)
                u = project_box_and_affine(np.clip(u + jitter, 0.0, 1.0), a, b)
                Pu = precond_u_from_active_lags(M, active_lags)
                u = apply_preconditioned_rounding(u, Pu, a, b, gamma=0.03)

        return best_u

    # Upsample a palindromic half-sequence u_old (length M_old) to M_new by linear interpolation on full sequence
    def upsample_half(u_old: np.ndarray, M_new: int) -> np.ndarray:
        full_old = build_full(u_old)
        N_old = len(full_old)
        N_new = 2 * M_new - 1
        x_old = np.linspace(0.0, 1.0, N_old)
        x_new = np.linspace(0.0, 1.0, N_new)
        full_new = np.interp(x_new, x_old, full_old)
        u_new = full_new[:M_new].copy()
        a_new, b_new = affine_weights(M_new)
        u_new = project_box_and_affine(np.clip(u_new, 0.0, 1.0), a_new, b_new)
        return u_new

    # Haugland-like five-plateau palindromic seed (half side uses three blocks).
    def five_plateau_seed_half(M: int) -> np.ndarray:
        # Heights chosen to approximate Haugland's five-plateau style
        h1, h2, h3 = 0.98, 0.78, 0.50  # half-side heights; mirrored yields [0.98,0.78,0.50,0.22,0.02]
        # Block fractions over the half interval [0,1]; gentle central stretch helps
        f1, f2 = 0.24, 0.20
        f3 = 1.0 - f1 - f2
        n1 = max(2, int(round(f1 * (M - 1))))
        n2 = max(2, int(round(f2 * (M - 1))))
        n3 = (M - 1) - n1 - n2
        n3 = max(2, n3)
        # Adjust if off by rounding
        while n1 + n2 + n3 != (M - 1):
            if n1 + n2 + n3 < (M - 1):
                n3 += 1
            else:
                n3 -= 1
        # Gentle linear ramps near the transitions (2% of each block, at least 1)
        r1 = max(1, int(0.02 * n1))
        r2 = max(1, int(0.02 * n2))
        # Construct half sequence
        u = np.zeros(M, dtype=float)
        # Block 1: near h1
        for i in range(n1):
            if i < r1:
                t = i / max(1, r1)
                u[i] = h1 * (1 - 0.1 * t) + (h1 - 0.04) * 0.1 * t
            else:
                u[i] = h1
        # Transition to block 2
        start = n1
        for j in range(n2):
            if j < r2:
                t = j / max(1, r2)
                u[start + j] = h1 * (1 - t) + h2 * t
            else:
                u[start + j] = h2
        # Block 3 to center
        start2 = n1 + n2
        n3_eff = M - 1 - start2
        if n3_eff > 0:
            # Linear ramp from h2 to h3 (0.5) toward the center
            for k in range(n3_eff):
                t = k / max(1, n3_eff)
                u[start2 + k] = h2 * (1 - t) + h3 * t
        # Center point exactly at h3
        u[-1] = h3
        return u

    # One optimization run from a specific initialization
    def optimize_from_init(u0: np.ndarray) -> np.ndarray:
        # Coarse softmax smoothing phase (active-set aligned)
        u_c = run_softmax_optimization(
            u0, iters=1000, tau_start=1.5, tau_end=0.4, alpha_start=0.25, alpha_end=0.06
        )
        # Coarse subgradient polish (epsilon-active + preconditioning)
        u_c = run_subgradient_polish(u_c, iters=950)

        # Upsample to finer grid and refine
        u_f = upsample_half(u_c, M_fine)
        # Mild annealed rounding preconditioning
        a_f, b_f = affine_weights(M_fine)
        Pu_f = precond_u_from_active_lags(M_fine, [0])  # neutral preconditioner
        for gamma in (0.03, 0.02):
            u_f = apply_preconditioned_rounding(u_f, Pu_f, a_f, b_f, gamma)

        # Fine softmax phase (short, aligned)
        u_f = run_softmax_optimization(
            u_f, iters=720, tau_start=0.9, tau_end=0.3, alpha_start=0.18, alpha_end=0.04
        )
        # Fine subgradient polish (epsilon-active + preconditioning)
        u_f = run_subgradient_polish(u_f, iters=1200)
        return u_f

    # Construct several initializations on the coarse grid and optimize
    a_c, b_c = affine_weights(M_coarse)
    inits = []

    # 1) Random uniform, projected (a few)
    for _ in range(3):
        u0 = rng.random(M_coarse)
        u0 = project_box_and_affine(u0, a_c, b_c)
        inits.append(u0)

    # 2) Cosine bell (tapered)
    x = np.linspace(-1.0, 1.0, M_coarse)
    bell = 0.5 * (1.0 + np.cos(np.pi * x))
    bell = (bell - bell.min()) / max(1e-12, (bell.max() - bell.min()))
    u0 = project_box_and_affine(bell, a_c, b_c)
    inits.append(u0)

    # 3) Central step-like block with linear shoulders
    step_profile = np.zeros(M_coarse)
    radius = M_coarse // 2 - 8
    step_profile[:radius] = 1.0
    shoulder_len = 18
    for i in range(radius, min(M_coarse - 1, radius + shoulder_len)):
        step_profile[i] = max(0.0, min(1.0, 1.0 - (i - radius) / shoulder_len))
    step_profile[-1] = 0.5
    step_profile = project_box_and_affine(step_profile, a_c, b_c)
    inits.append(step_profile)

    # 4) Slightly asymmetric perturbation projected back (to escape symmetry traps)
    u0 = bell.copy()
    u0 += 0.05 * np.sin(4 * np.linspace(0, np.pi, M_coarse))
    u0 = np.clip(u0, 0.0, 1.0)
    u0 = project_box_and_affine(u0, a_c, b_c)
    inits.append(u0)

    # 5) Haugland-like five-plateau palindromic seed (projected)
    u0 = five_plateau_seed_half(M_coarse)
    u0 = np.clip(u0, 0.0, 1.0)
    u0 = project_box_and_affine(u0, a_c, b_c)
    inits.append(u0)

    # Run optimization from each initialization and keep the best by objective
    best_u_fine = None
    best_obj = float("inf")
    for u0 in inits:
        u_candidate = optimize_from_init(u0)
        obj = objective_from_u(u_candidate)
        if obj < best_obj:
            best_obj = obj
            best_u_fine = u_candidate

    # Final short polish on the best candidate
    best_u_fine = run_subgradient_polish(best_u_fine, iters=700)

    # Ensure exact feasibility and bounds
    a_f, b_f = affine_weights(M_fine)
    best_u_fine = np.clip(best_u_fine, 0.0, 1.0)
    best_u_fine = project_box_and_affine(best_u_fine, a_f, b_f)

    return best_u_fine
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"half_sequence": generate_erdos_data().tolist()}))
```

$$
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
$$

$$
0.379005 \leq C_5 \leq 0.380927
$$

```python
#!/usr/bin/env python3
"""Initial relaxed sequence for the minimum-overlap problem.

This implementation constructs a palindromic step sequence h over [0, 2]
discretized to an odd grid length N = 2M-1. The caller mirrors the returned
half sequence to reconstruct the full sequence, which preserves the integral
constraint and minimizes the maximum discrete convolution
max_k sum_i h[i] * (1 - h[i+k]) as closely as possible.

We combine a smooth maximization surrogate (log-sum-exp over all shifts) with
projected gradient steps, followed by a sharper subgradient polishing that
directly reduces the discrete max overlap used by the evaluator. We enforce:
- palindromic structure via half-parameterization,
- box constraints 0 <= h <= 1,
- exact integral constraint (sum(h) = N / 2),
with an exact projection onto the affine hyperplane under box constraints using
a monotone bisection on the Lagrange multiplier (works because coefficients a_i
are strictly positive). To stabilize, we adopt an epsilon-active set of lags to
approximate the Clarke subdifferential at the nondifferentiable max and we
precondition the subgradient by a correlation-degree per index to balance
updates across heavily/lightly constrained indices. We also add a structure-
aware five-plateau seed that mirrors Haugland-like constructions to reach good
basins.

We perform a coarse-to-fine continuation by resampling a palindromic solution
from a coarser grid to a finer grid and refining it.

Mutation tweaks integrated:
1) Epsilon-active set with shoulder enrichment: after selecting epsilon-active
   lags, we include their immediate neighbors (±1 in lag index), capturing local
   ridges and reducing oscillatory maximizer switching.
2) Flattened-top weighting for the active set: weights within the active set are
   w_L ∝ max(conv[L] − mean_A, 0)^p (p=1.5), both in the soft surrogate and in
   the polishing phase, to flatten the plateau of near-max lags more effectively.
3) Targeted, preconditioned rounding and finer grid: replace uniform rounding by
   u_i ← u_i + γ0 · P[i] · tanh(3(u_i − 0.5)) using the same correlation-degree
   preconditioner P, and increase fine grid to N=501 (M=251).

The output "half_sequence" is the first half (length M) whose mirror reconstructs
the length-N sequence:
  full = concat(half[:-1], reversed(half))
"""

import json
import numpy as np


# EVOLVE_START
def generate_erdos_data() -> np.ndarray:
    """Generates a sequence of coefficients for Erdős' minimum overlap problem.

    Implements the following improvements:
    - Epsilon-active set subgradient polishing with a shrinking activity band,
      enriched with immediate-neighbor lags (shoulders).
    - Per-index correlation-degree preconditioning of the subgradient.
    - Flattened-top weighting on epsilon-active lags for both softmax surrogate
      and polishing phases (centered power weights with p=1.5).
    - Targeted, preconditioned rounding using tanh pressure near 0.5.
    - Adds a palindromic five-plateau (Haugland-like) seed to the init pool.
    - Finer final grid N=501 (M=251) to reduce discretization error.

    Returns:
      np.ndarray: First half of a palindromic sequence h of length M, in [0,1],
                  such that when mirrored as in the pipeline:
                  final_sequence = np.concatenate((u[:-1], u[::-1])),
                  it satisfies sum(final_sequence) = len(final_sequence) / 2.
    """
    rng = np.random.default_rng(2026)

    # Grid sizes: coarse for exploration; fine for final polishing.
    M_coarse = 161  # N_coarse = 321
    M_fine = 251    # N_fine   = 501

    # Build weights for the affine sum constraint of palindromic half u:
    # 2 * sum(u[:-1]) + u[-1] = N/2, where N = 2M - 1
    def affine_weights(M: int) -> tuple[np.ndarray, float]:
        N = 2 * M - 1
        a = np.ones(M) * 2.0
        a[-1] = 1.0
        b = N / 2.0
        return a, b

    # Exact projection onto {u in [0,1]^M : a^T u = b} via Lagrange multiplier bisection.
    # Because all a_i > 0, u*(mu) = clip(v - mu * a, 0, 1) yields a monotone mapping S(mu) = a^T u.
    def project_box_and_affine(v: np.ndarray, a: np.ndarray, b: float) -> np.ndarray:
        v = np.asarray(v, dtype=float)
        a = np.asarray(a, dtype=float)
        v = np.clip(v, 0.0, 1.0)
        S = float(np.dot(a, v))
        if abs(S - b) <= 1e-12:
            return v

        with np.errstate(divide="ignore", invalid="ignore"):
            mu_to_zero = v / a           # mu at which entry hits 0
            mu_to_one = (v - 1.0) / a    # mu at which entry hits 1

        finite_zero = mu_to_zero[~np.isnan(mu_to_zero)]
        finite_one = mu_to_one[~np.isnan(mu_to_one)]
        mu_lo = (np.min(finite_one) - 1.0) if finite_one.size else -1.0
        mu_hi = (np.max(finite_zero) + 1.0) if finite_zero.size else 1.0

        def S_of_mu(mu: float) -> float:
            u = v - mu * a
            u = np.clip(u, 0.0, 1.0)
            return float(np.dot(a, u))

        slo = S_of_mu(mu_lo)
        shi = S_of_mu(mu_hi)
        expand = 0
        while (slo < b or shi > b) and expand < 60:
            if slo < b:
                mu_lo -= 2.0
                slo = S_of_mu(mu_lo)
            if shi > b:
                mu_hi += 2.0
                shi = S_of_mu(mu_hi)
            expand += 1

        for _ in range(80):
            mu_mid = 0.5 * (mu_lo + mu_hi)
            s_mid = S_of_mu(mu_mid)
            if s_mid > b:
                mu_lo = mu_mid
            else:
                mu_hi = mu_mid
            if abs(mu_hi - mu_lo) <= 1e-12:
                break
        mu = 0.5 * (mu_lo + mu_hi)
        u = v - mu * a
        u = np.clip(u, 0.0, 1.0)
        return u

    # Build full palindromic sequence from half u
    def build_full(u: np.ndarray) -> np.ndarray:
        return np.concatenate([u[:-1], u[::-1]])

    # FFT-based correlation: corr_full(x, y)[k] = sum_i x[i] * y[i+k] (0 out-of-bounds)
    # Using convolution with reversed y to implement correlation efficiently.
    def corr_full_fft(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        n = len(x) + len(y) - 1
        nfft = 1 << ((n - 1).bit_length())
        X = np.fft.rfft(x, nfft)
        Y = np.fft.rfft(y[::-1], nfft)
        conv = np.fft.irfft(X * Y, nfft)[:n]
        return conv

    # Compute correlation values and the maximum (raw, without normalization)
    def compute_conv_full(h: np.ndarray) -> np.ndarray:
        return corr_full_fft(h, 1.0 - h)

    def compute_conv_max(h: np.ndarray) -> tuple[float, np.ndarray]:
        conv = compute_conv_full(h)
        return float(np.max(conv)), conv

    # Gradient on h for a given integer lag L (matching conv[L] = sum_i h[i]*(1-h[i+L]))
    def grad_h_for_lag(h: np.ndarray, L: int) -> np.ndarray:
        Nloc = h.shape[0]
        g = np.zeros_like(h)
        idx = np.arange(Nloc)
        if L == 0:
            # c0 = sum h[i]*(1-h[i]); grad = d/dh[i] = 1 - 2h[i]
            g = 1.0 - 2.0 * h
            return g
        left = idx - L
        right = idx + L
        mask_left = (left >= 0) & (left < Nloc)   # j-L valid
        mask_right = (right >= 0) & (right < Nloc)  # j+L valid
        # Contribution when j is the "left" index i=j: +1 - h[j+L]
        g[mask_right] += (1.0 - h[right[mask_right]])
        # Contribution when j is the "right" index i=j-L: -h[j-L]
        g[mask_left] -= h[left[mask_left]]
        return g

    # Map gradient on full h to gradient on u (half variables)
    def grad_u_from_grad_h(gh: np.ndarray, M: int) -> np.ndarray:
        N = 2 * M - 1
        gu = np.zeros(M, dtype=float)
        for m in range(M):
            i1 = m
            i2 = N - 1 - m
            if i1 == i2:
                gu[m] = gh[i1]
            else:
                gu[m] = gh[i1] + gh[i2]
        return gu

    # Objective (normalized upper bound) used to rank candidates
    def objective_from_u(u: np.ndarray) -> float:
        h = build_full(u)
        conv_max, _ = compute_conv_max(h)
        return conv_max / (len(h) * 2.0)

    # Helper: epsilon-active set of lags based on current conv and a band fraction.
    # Returns a sorted list of integer lags (by descending conv value) capped by max_cap.
    # Shoulder enrichment: include ±1 neighbors (in conv-index space).
    def epsilon_active_lags(conv: np.ndarray, band_frac: float, max_cap: int) -> list[int]:
        m = float(np.max(conv))
        thresh = m - band_frac * m
        idx = np.nonzero(conv >= thresh)[0]
        if idx.size == 0:
            idx = np.array([int(np.argmax(conv))], dtype=int)
        # Sort by descending conv values
        idx_sorted = idx[np.argsort(conv[idx])[::-1]]
        # Shoulder enrichment: include neighbors ±1
        full_len = conv.shape[0]
        enriched = set()
        for k in idx_sorted:
            enriched.add(int(k))
            km = k - 1
            kp = k + 1
            if km >= 0:
                enriched.add(int(km))
            if kp < full_len:
                enriched.add(int(kp))
        # Sort enriched indices by conv value descending
        enriched_list = sorted(list(enriched), key=lambda x: conv[x], reverse=True)
        # Cap by max_cap
        if len(enriched_list) > max_cap:
            enriched_list = enriched_list[:max_cap]
        # Map to lags
        N = (len(conv) + 1) // 2
        lags = [int(k - (N - 1)) for k in enriched_list]
        return lags

    # Helper: degree-preconditioner P[i] = 1/sqrt(1 + d[i]), where d[i] counts active contributions.
    def degree_preconditioner(N: int, active_lags: list[int]) -> np.ndarray:
        d = np.zeros(N, dtype=float)
        idx = np.arange(N)
        for L in active_lags:
            left = idx - L
            right = idx + L
            mask_left = (left >= 0) & (left < N)
            mask_right = (right >= 0) & (right < N)
            d[mask_left] += 1.0
            d[mask_right] += 1.0
        P = 1.0 / np.sqrt(1.0 + d)
        return P

    # Enrich a given set of lags by adding their shoulder neighbors ±1 (lag space),
    # mapped to valid conv indices.
    def shoulder_enrich_lags(lag_list: list[int], N_full: int, conv_len: int) -> list[int]:
        # Map lags to k-indices
        base_k = [L + (N_full - 1) for L in lag_list]
        # Add neighbors
        kset = set()
        for k in base_k:
            if 0 <= k < conv_len:
                kset.add(int(k))
            km = k - 1
            kp = k + 1
            if 0 <= km < conv_len:
                kset.add(int(km))
            if 0 <= kp < conv_len:
                kset.add(int(kp))
        # Convert back to lags and sort
        lags = sorted([int(k - (N_full - 1)) for k in kset])
        return lags

    # Build preconditioning weights for u (half) based on P over h.
    def precond_u_from_active_lags(M: int, active_lags: list[int]) -> np.ndarray:
        N = 2 * M - 1
        P_h = degree_preconditioner(N, active_lags)
        Pu = np.zeros(M, dtype=float)
        for m in range(M):
            i1 = m
            i2 = N - 1 - m
            if i1 == i2:
                Pu[m] = P_h[i1]
            else:
                Pu[m] = 0.5 * (P_h[i1] + P_h[i2])
        return Pu

    # Apply targeted, preconditioned rounding pressure around 0.5 using tanh.
    def apply_preconditioned_rounding(u: np.ndarray, Pu: np.ndarray, a: np.ndarray, b: float, gamma: float) -> np.ndarray:
        nudged = u + gamma * Pu * np.tanh(3.0 * (u - 0.5))
        nudged = np.clip(nudged, 0.0, 1.0)
        nudged = project_box_and_affine(nudged, a, b)
        return nudged

    # Smooth surrogate optimization with log-sum-exp softmax over shifts,
    # aligned to an epsilon-active set to better track the nondifferentiable max.
    def run_softmax_optimization(
        u0: np.ndarray,
        iters: int,
        tau_start: float,
        tau_end: float,
        alpha_start: float,
        alpha_end: float,
    ) -> np.ndarray:
        u = u0.copy()
        M = len(u)
        a, b = affine_weights(M)
        u = project_box_and_affine(np.clip(u, 0.0, 1.0), a, b)

        best_u = u.copy()
        h = build_full(u)
        best_conv_max, _ = compute_conv_max(h)

        # Cap for the adaptive active-set size in the softmax phase
        cap_soft = 64
        p_flat = 1.5  # power for flattened-top weighting

        for t in range(iters):
            # Temperature and step schedule
            frac = t / max(1, iters - 1)
            tau = tau_start * (1 - frac) + tau_end * frac
            alpha = alpha_start * (1 - frac) + alpha_end * frac

            h = build_full(u)
            conv = compute_conv_full(h)
            m = float(np.max(conv))

            # Softmax weights across all lags (for selection ranking)
            z = np.exp((conv - m) / max(1e-8, tau))
            w = z / max(1e-16, np.sum(z))

            # Build epsilon-active set aligned with the current max (band shrinks slightly)
            band_frac = 0.004 * (1 - frac) + 0.0015 * frac
            A_lags = epsilon_active_lags(conv, band_frac=band_frac, max_cap=cap_soft)

            # Also include the top-weighted lags to ensure coverage
            q_top = min(24, 2 * len(h) - 1)
            idx_sorted = np.argsort(w)[::-1][:q_top]
            N = len(h)
            top_lags = [int(k - (N - 1)) for k in idx_sorted]

            # Union sets
            lag_set = list(dict.fromkeys(A_lags + top_lags))  # preserve order

            # Shoulder enrichment on the union (ensure ±1 neighbors)
            lag_set = shoulder_enrich_lags(lag_set, N_full=N, conv_len=len(conv))

            # Cap by conv score prioritization
            if len(lag_set) > cap_soft:
                k_from_L = [L + (N - 1) for L in lag_set]
                scores = [conv[k] for k in k_from_L]
                order = np.argsort(scores)[::-1]
                lag_set = [lag_set[i] for i in order[:cap_soft]]

            # Compute gradient on h with flattened-top centered power weights over active subset
            gh = np.zeros_like(h)
            if not lag_set:
                # Safety: fallback to top few by softmax
                idx_sorted = np.argsort(w)[::-1][:min(32, 2 * N - 1)]
                lag_set = [int(k - (N - 1)) for k in idx_sorted]

            k_subset = [L + (N - 1) for L in lag_set]
            conv_subset = conv[k_subset]
            mu = float(np.mean(conv_subset))
            svals = np.maximum(conv_subset - mu, 0.0) ** p_flat
            if np.all(svals <= 1e-20):
                w_sub = np.ones_like(svals) / len(svals)
            else:
                w_sub = svals / np.sum(svals)

            for w_k, L in zip(w_sub, lag_set):
                gh += w_k * grad_h_for_lag(h, L)

            gu = grad_u_from_grad_h(gh, M)

            # Backtracking projected step
            success = False
            step = alpha
            curr_max = m
            for _ in range(6):
                uc = u - step * gu
                uc = np.clip(uc, 0.0, 1.0)
                uc = project_box_and_affine(uc, a, b)
                hc = build_full(uc)
                cand_max, _ = compute_conv_max(hc)
                if cand_max <= curr_max + 1e-9:
                    u = uc
                    success = True
                    if cand_max < best_conv_max - 1e-9:
                        best_conv_max = cand_max
                        best_u = uc.copy()
                    break
                step *= 0.5
            if not success:
                u = project_box_and_affine(np.clip(u - alpha_end * gu, 0.0, 1.0), a, b)

            # Periodic preconditioned rounding to push away from 0.5 while preserving affine constraint
            if (t + 1) % max(20, iters // 6) == 0:
                # Use current active lags to build Pu
                Pu = precond_u_from_active_lags(M, lag_set if lag_set else [0])
                gamma = 0.02
                u = apply_preconditioned_rounding(u, Pu, a, b, gamma)

        return best_u

    # Sharper subgradient polishing directly minimizing the max correlation using
    # an epsilon-active set of lags and degree preconditioning.
    def run_subgradient_polish(u0: np.ndarray, iters: int = 1800) -> np.ndarray:
        u = u0.copy()
        M = len(u)
        a, b = affine_weights(M)
        u = project_box_and_affine(np.clip(u, 0.0, 1.0), a, b)

        h = build_full(u)
        best_conv_max, _ = compute_conv_max(h)
        best_u = u.copy()

        # Initial step size
        alpha = 0.15
        # Active-set cap
        cap_active = 80
        p_flat = 1.5

        for t in range(iters):
            h = build_full(u)
            conv_max, conv = compute_conv_max(h)

            # Epsilon-active set with a shrinking band and shoulder enrichment
            frac = t / max(1, iters - 1)
            band_frac = 0.005 * (1 - frac) + 0.001 * frac
            active_lags = epsilon_active_lags(conv, band_frac=band_frac, max_cap=cap_active)
            if not active_lags:
                N = len(h)
                active_lags = [int(np.argmax(conv) - (N - 1))]
            # Enrich with shoulders
            active_lags = shoulder_enrich_lags(active_lags, N_full=len(h), conv_len=len(conv))
            # Cap by conv score prioritization if needed
            if len(active_lags) > cap_active:
                N = len(h)
                k_from_L = [L + (N - 1) for L in active_lags]
                scores = [conv[k] for k in k_from_L]
                order = np.argsort(scores)[::-1]
                active_lags = [active_lags[i] for i in order[:cap_active]]

            # Flattened-top weighting within active set: centered power weights
            N = len(h)
            k_subset = [L + (N - 1) for L in active_lags]
            conv_subset = conv[k_subset]
            mu = float(np.mean(conv_subset))
            svals = np.maximum(conv_subset - mu, 0.0) ** p_flat
            if np.all(svals <= 1e-20):
                w_sub = np.ones_like(svals) / len(svals)
            else:
                w_sub = svals / np.sum(svals)

            # Form the subgradient on h using weighted combination over active lags
            gh = np.zeros_like(h)
            for w_k, L in zip(w_sub, active_lags):
                gh += w_k * grad_h_for_lag(h, L)

            # Correlation-degree preconditioning: P[i] = 1/sqrt(1 + d[i])
            P = degree_preconditioner(len(h), active_lags)
            gh *= P

            gu = grad_u_from_grad_h(gh, M)

            # Backtracking line search
            success = False
            local_alpha = alpha
            for _ in range(6):
                u_candidate = np.clip(u - local_alpha * gu, 0.0, 1.0)
                u_candidate = project_box_and_affine(u_candidate, a, b)
                h_candidate = build_full(u_candidate)
                cand_max, _ = compute_conv_max(h_candidate)
                if cand_max <= conv_max + 1e-9:
                    u = u_candidate
                    if cand_max < best_conv_max - 1e-9:
                        best_conv_max = cand_max
                        best_u = u_candidate.copy()
                    success = True
                    break
                local_alpha *= 0.5
            if not success:
                u = project_box_and_affine(np.clip(u - local_alpha * gu, 0.0, 1.0), a, b)

            # Step size anneal
            if (t + 1) % 200 == 0:
                alpha = max(0.02, alpha * 0.9)

            # Occasional jitter and preconditioned rounding to escape shallow kinks and promote binarity
            if (t + 1) % 450 == 0:
                jitter = rng.normal(0.0, 5e-4, size=M)
                u = project_box_and_affine(np.clip(u + jitter, 0.0, 1.0), a, b)
                Pu = precond_u_from_active_lags(M, active_lags)
                u = apply_preconditioned_rounding(u, Pu, a, b, gamma=0.03)

        return best_u

    # Upsample a palindromic half-sequence u_old (length M_old) to M_new by linear interpolation on full sequence
    def upsample_half(u_old: np.ndarray, M_new: int) -> np.ndarray:
        full_old = build_full(u_old)
        N_old = len(full_old)
        N_new = 2 * M_new - 1
        x_old = np.linspace(0.0, 1.0, N_old)
        x_new = np.linspace(0.0, 1.0, N_new)
        full_new = np.interp(x_new, x_old, full_old)
        u_new = full_new[:M_new].copy()
        a_new, b_new = affine_weights(M_new)
        u_new = project_box_and_affine(np.clip(u_new, 0.0, 1.0), a_new, b_new)
        return u_new

    # Haugland-like five-plateau palindromic seed (half side uses three blocks).
    def five_plateau_seed_half(M: int) -> np.ndarray:
        # Heights chosen to approximate Haugland's five-plateau style
        h1, h2, h3 = 0.98, 0.78, 0.50  # half-side heights; mirrored yields [0.98,0.78,0.50,0.22,0.02]
        # Block fractions over the half interval [0,1]; gentle central stretch helps
        f1, f2 = 0.24, 0.20
        f3 = 1.0 - f1 - f2
        n1 = max(2, int(round(f1 * (M - 1))))
        n2 = max(2, int(round(f2 * (M - 1))))
        n3 = (M - 1) - n1 - n2
        n3 = max(2, n3)
        # Adjust if off by rounding
        while n1 + n2 + n3 != (M - 1):
            if n1 + n2 + n3 < (M - 1):
                n3 += 1
            else:
                n3 -= 1
        # Gentle linear ramps near the transitions (2% of each block, at least 1)
        r1 = max(1, int(0.02 * n1))
        r2 = max(1, int(0.02 * n2))
        # Construct half sequence
        u = np.zeros(M, dtype=float)
        # Block 1: near h1
        for i in range(n1):
            if i < r1:
                t = i / max(1, r1)
                u[i] = h1 * (1 - 0.1 * t) + (h1 - 0.04) * 0.1 * t
            else:
                u[i] = h1
        # Transition to block 2
        start = n1
        for j in range(n2):
            if j < r2:
                t = j / max(1, r2)
                u[start + j] = h1 * (1 - t) + h2 * t
            else:
                u[start + j] = h2
        # Block 3 to center
        start2 = n1 + n2
        n3_eff = M - 1 - start2
        if n3_eff > 0:
            # Linear ramp from h2 to h3 (0.5) toward the center
            for k in range(n3_eff):
                t = k / max(1, n3_eff)
                u[start2 + k] = h2 * (1 - t) + h3 * t
        # Center point exactly at h3
        u[-1] = h3
        return u

    # One optimization run from a specific initialization
    def optimize_from_init(u0: np.ndarray) -> np.ndarray:
        # Coarse softmax smoothing phase (active-set aligned)
        u_c = run_softmax_optimization(
            u0, iters=1000, tau_start=1.5, tau_end=0.4, alpha_start=0.25, alpha_end=0.06
        )
        # Coarse subgradient polish (epsilon-active + preconditioning)
        u_c = run_subgradient_polish(u_c, iters=950)

        # Upsample to finer grid and refine
        u_f = upsample_half(u_c, M_fine)
        # Mild annealed rounding preconditioning
        a_f, b_f = affine_weights(M_fine)
        Pu_f = precond_u_from_active_lags(M_fine, [0])  # neutral preconditioner
        for gamma in (0.03, 0.02):
            u_f = apply_preconditioned_rounding(u_f, Pu_f, a_f, b_f, gamma)

        # Fine softmax phase (short, aligned)
        u_f = run_softmax_optimization(
            u_f, iters=720, tau_start=0.9, tau_end=0.3, alpha_start=0.18, alpha_end=0.04
        )
        # Fine subgradient polish (epsilon-active + preconditioning)
        u_f = run_subgradient_polish(u_f, iters=1200)
        return u_f

    # Construct several initializations on the coarse grid and optimize
    a_c, b_c = affine_weights(M_coarse)
    inits = []

    # 1) Random uniform, projected (a few)
    for _ in range(3):
        u0 = rng.random(M_coarse)
        u0 = project_box_and_affine(u0, a_c, b_c)
        inits.append(u0)

    # 2) Cosine bell (tapered)
    x = np.linspace(-1.0, 1.0, M_coarse)
    bell = 0.5 * (1.0 + np.cos(np.pi * x))
    bell = (bell - bell.min()) / max(1e-12, (bell.max() - bell.min()))
    u0 = project_box_and_affine(bell, a_c, b_c)
    inits.append(u0)

    # 3) Central step-like block with linear shoulders
    step_profile = np.zeros(M_coarse)
    radius = M_coarse // 2 - 8
    step_profile[:radius] = 1.0
    shoulder_len = 18
    for i in range(radius, min(M_coarse - 1, radius + shoulder_len)):
        step_profile[i] = max(0.0, min(1.0, 1.0 - (i - radius) / shoulder_len))
    step_profile[-1] = 0.5
    step_profile = project_box_and_affine(step_profile, a_c, b_c)
    inits.append(step_profile)

    # 4) Slightly asymmetric perturbation projected back (to escape symmetry traps)
    u0 = bell.copy()
    u0 += 0.05 * np.sin(4 * np.linspace(0, np.pi, M_coarse))
    u0 = np.clip(u0, 0.0, 1.0)
    u0 = project_box_and_affine(u0, a_c, b_c)
    inits.append(u0)

    # 5) Haugland-like five-plateau palindromic seed (projected)
    u0 = five_plateau_seed_half(M_coarse)
    u0 = np.clip(u0, 0.0, 1.0)
    u0 = project_box_and_affine(u0, a_c, b_c)
    inits.append(u0)

    # Run optimization from each initialization and keep the best by objective
    best_u_fine = None
    best_obj = float("inf")
    for u0 in inits:
        u_candidate = optimize_from_init(u0)
        obj = objective_from_u(u_candidate)
        if obj < best_obj:
            best_obj = obj
            best_u_fine = u_candidate

    # Final short polish on the best candidate
    best_u_fine = run_subgradient_polish(best_u_fine, iters=700)

    # Ensure exact feasibility and bounds
    a_f, b_f = affine_weights(M_fine)
    best_u_fine = np.clip(best_u_fine, 0.0, 1.0)
    best_u_fine = project_box_and_affine(best_u_fine, a_f, b_f)

    return best_u_fine
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"half_sequence": generate_erdos_data().tolist()}))
```

```text
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
```

```text
0.379005 \leq C_5 \leq 0.380927
```
