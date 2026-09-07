Actionable insight distilled from the capped-softmax water-filling modification and its observed metrics.

- Water-Filled Capped Softmax for Peak Smoothing: Projecting the peak softmax weights onto a capped simplex with a water-filling procedure—using p_cap = 0.60 when the selected top-peak count R=1, 0.40 for R=2, and 0.33 for R=3—prevented over-concentration on a single dominant shift and spread gradient pressure across the top cluster of convolution peaks. This reduced the see-saw effect among neighboring peaks and stabilized updates in the minimax surrogate, yielding strong results (validity 1.0, target_ratio 0.9908924122180562, upper_bound 1.5191356613887796, eval_time 6.495587082987186). Reuse this pattern in future peak- or attention-weighted surrogates by enforcing an l∞-bounded simplex projection with an adaptive cap derived from a spectrum flatness indicator (e.g., R), so clear-winner spectra are permissively capped while flat spectra receive tighter caps.

```python
#!/usr/bin/env python3
"""Advanced search for a step function minimizing the autocorrelation-based objective.

This script implements a direct minimization of:
  J(a) = 2 * n * max(conv(a, a)) / (sum(a)^2)

Key features:
- Softmax parameterization for nonnegativity and unit-sum constraints.
- Top-K-focused smooth minimax surrogate with epsilon-tail mixing and active-set adaptation.
- Efficient FFT-based computation of convolution and its gradient.
- Adam optimizer with AMSGrad, TV smoothing early on, small entropy bonus early.
- Peak-aware learning-rate stabilization based on max-peak identity changes.
- Multi-start and multi-resolution (coarse-to-fine) continuation.
- Peak-shaving and cutting-plane-like refinement to further reduce worst peaks.
- Mutation additions: adaptive multi-peak weighted entropy (with Huberized entropy gradient),
  and multi-peak pair-shaving transport with distance-biased redistribution.

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
    tau_eps = max(1e-300, float(tau))
    z = (x - m) / tau_eps
    return float(m + tau_eps * np.log(np.sum(np.exp(z))))


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


def _corr_grad_from_p(a: np.ndarray, p: np.ndarray) -> np.ndarray:
    """Compute gradient 2 * corr(p, a) efficiently for aperiodic convolution lengths."""
    n = a.size
    pr = p[::-1]
    c = _conv_aperiodic(a, pr)  # length 3n - 2
    c_slice = c[n - 1 : 2 * n - 1][::-1]  # length n
    return 2.0 * c_slice


def _select_top_R_peaks(b: np.ndarray, Rmax: int = 3) -> Tuple[np.ndarray, float, float, int]:
    """Select top-R peaks adaptively based on the gap between the top two peaks.

    Returns (indices K, b1, b2, R) where:
      - K are the indices of the selected peaks (size R)
      - b1 is the largest value
      - b2 is the second largest value (or b1 if not available)
      - R is the chosen number of peaks from {1, 2, 3}
    """
    Lb = b.size
    order = np.argsort(b)[::-1]  # descending
    Rmax = int(min(Rmax, Lb))
    if Lb == 0:
        return np.array([], dtype=int), 0.0, 0.0, 0
    b1 = float(b[order[0]])
    b2 = float(b[order[1]]) if Lb >= 2 else b1
    delta = b1 - b2
    # Normalize gap to decide R
    denom = max(b1, 1e-16)
    dn = delta / denom
    # Heuristic thresholds:
    # - clear winner: dn >= 2e-2 -> R=1
    # - very flat: dn < 5e-3 -> R=3
    # - otherwise: R=2
    if dn >= 2e-2:
        R = 1
    elif dn < 5e-3:
        R = 3
    else:
        R = 2
    R = int(min(R, Rmax, Lb))
    return order[:R], b1, b2, R


def _project_to_simplex(x: np.ndarray) -> np.ndarray:
    """Project a vector x onto the probability simplex {y >= 0, sum y = 1}."""
    # Implementation of the algorithm by Duchi et al.
    v = np.sort(x)[::-1]
    cssv = np.cumsum(v)
    rho = np.nonzero(v * np.arange(1, v.size + 1) > (cssv - 1))[0]
    if rho.size == 0:
        # Fallback: uniform
        return np.full_like(x, 1.0 / x.size)
    rho = rho[-1]
    theta = (cssv[rho] - 1) / (rho + 1.0)
    w = np.maximum(x - theta, 0.0)
    s = np.sum(w)
    if s <= 0.0 or not np.isfinite(s):
        return np.full_like(x, 1.0 / x.size)
    return w / s


def _project_capped_simplex(p: np.ndarray, p_cap: float) -> np.ndarray:
    """Project onto the capped simplex {x >= 0, sum x = 1, x_i <= p_cap} via water-filling.

    Starting from a nonnegative p, we cap entries exceeding p_cap and redistribute
    the excess to remaining entries proportionally to their available capacity
    (p_cap - p_i), iterating until all entries satisfy the cap and the vector
    sums to 1. This is a simple l∞-bounded simplex projection.
    """
    L = p.size
    if L == 0:
        return p
    # Sanitize and normalize
    p = np.clip(p, 0.0, None)
    s = float(np.sum(p))
    if s <= 0.0 or not np.isfinite(s):
        return np.full(L, 1.0 / L, dtype=np.float64)
    p = p / s

    # If cap is loose, nothing to do
    if p_cap >= 1.0 - 1e-12:
        return p

    # Iterative water-filling
    for _ in range(10 * L):  # robust safeguard upper bound
        mask = p > p_cap + 1e-12
        if not np.any(mask):
            break
        overflow = float(np.sum(p[mask] - p_cap))
        p[mask] = p_cap
        free_mask = ~mask
        if not np.any(free_mask):
            # Fallback uniform (feasible for typical caps since 1/L <= p_cap)
            p[:] = 1.0 / L
            break
        free_cap = p_cap - p[free_mask]
        free_cap = np.clip(free_cap, 0.0, None)
        total_free = float(np.sum(free_cap))
        if total_free <= 1e-15 or not np.isfinite(total_free):
            # Fallback uniform if no capacity
            p[:] = 1.0 / L
            break
        increment = overflow * (free_cap / total_free)
        p[free_mask] += increment

    # Final normalization for numerical stability
    s = float(np.sum(p))
    if s <= 0.0 or not np.isfinite(s):
        return np.full(L, 1.0 / L, dtype=np.float64)
    return p / s


def _grad_surrogate(
    a: np.ndarray,
    tau: float,
    lam_tv: float,
    lam_ent: float,
    topk: int | None = None,
    alpha_tail: float = 0.0,
    neighbor_radius: int = 0,
    tau0: float | None = None,
) -> Tuple[float, np.ndarray]:
    """Compute Top-K surrogate objective and gradient w.r.t a with epsilon-tail mixing.

       L_tau^K(a) = 2n * tau * log Σ_{k ∈ S} exp(b[k]/tau), S = TopK(b) expanded by a small neighbor radius.
       If topk is None or >= len(b), this reduces to full LSE.

       Gradient uses blended softmax weights:
         p = (1 - alpha_tail) * p_S + alpha_tail * u,
       where u is uniform over all shifts (length 2n-1).
       NEW: after forming p, apply a capped-simplex projection with adaptive cap
            based on the top-peak spectrum: p_cap ∈ {0.60, 0.40, 0.33} for R ∈ {1,2,3}.

       Entropy is adaptive multi-peak weighted: lam_ent * Σ_i w[i] a[i] log(a[i] + eps_h),
       with weights w[i] depending on the top-R peaks' pair participation of index i
       and eps_h a Huberized offset tied to the median of a to stabilize gradients.

       ∂L/∂a = 2 * 2n * corr(p, a) + TV + weighted-entropy gradients.

       Returns (L_total, grad_total).
    """
    n = a.size
    # Convolution
    b = _conv_aperiodic(a, a)  # length 2n - 1
    Lb = b.size

    # Compute dynamic epsilon-tail mixing based on top-2 peak gap
    # Larger gap -> smaller alpha_tail, flatter top -> larger alpha.
    Kinds_probe, b1_probe, b2_probe, R_probe = _select_top_R_peaks(b, Rmax=3)
    delta_top = b1_probe - b2_probe
    sigma = 1e-3
    alpha_dyn = float(np.clip(0.02 + 0.08 * np.exp(-delta_top / max(sigma, 1e-12)), 0.02, 0.10))
    # Use at least the dynamic alpha (ensures more mixing when flat)
    alpha_eff = max(alpha_tail, alpha_dyn)

    # Adaptive cap based on top-peak spectrum flatness (R from _select_top_R_peaks)
    if R_probe == 1:
        p_cap = 0.60
    elif R_probe == 2:
        p_cap = 0.40
    else:
        p_cap = 0.33

    # Build Top-K restricted softmax weights over b
    use_topk = topk is not None and topk > 0 and topk < Lb

    if use_topk:
        K = int(topk)
        # Base Top-K indices
        idx_top = np.argpartition(b, Lb - K)[-K:]
        # Expand by neighbor radius
        if neighbor_radius > 0:
            extra = []
            for idx in idx_top:
                for r in range(1, neighbor_radius + 1):
                    if idx - r >= 0:
                        extra.append(idx - r)
                    if idx + r < Lb:
                        extra.append(idx + r)
            if extra:
                idx_top = np.unique(np.concatenate([idx_top, np.array(extra, dtype=int)]))
        # Restricted log-sum-exp
        b_sel = b[idx_top]
        tau_eps = max(1e-300, float(tau))
        m = float(np.max(b_sel))
        z = (b_sel - m) / tau_eps
        ez = np.exp(z)
        sum_ez = float(np.sum(ez))
        if sum_ez <= 0.0 or not np.isfinite(sum_ez):
            # Fallback: uniform weights over active set
            p_sel = np.full_like(b_sel, 1.0 / b_sel.size, dtype=np.float64)
            lse_top = m + tau_eps * np.log(max(1, b_sel.size))
        else:
            p_sel = ez / sum_ez
            lse_top = m + tau_eps * np.log(sum_ez)

        p = np.zeros_like(b, dtype=np.float64)
        p[idx_top] = p_sel

        # Epsilon-tail mixing
        if alpha_eff > 0.0:
            u = np.full_like(b, 1.0 / Lb, dtype=np.float64)
            p = (1.0 - alpha_eff) * p + alpha_eff * u

        # Project onto capped simplex to avoid over-concentration on a single peak
        p = _project_capped_simplex(p, p_cap=p_cap)

        # Main surrogate objective
        L_main = 2.0 * n * lse_top
    else:
        # Full log-sum-exp surrogate
        lse = _logsumexp(b, tau)
        L_main = 2.0 * n * lse
        # softmax weights over all entries
        m = float(np.max(b))
        tau_eps = max(1e-300, float(tau))
        z = (b - m) / tau_eps
        ez = np.exp(z)
        sum_ez = float(np.sum(ez))
        if sum_ez <= 0 or not np.isfinite(sum_ez):
            p = np.full_like(b, 1.0 / b.size)
        else:
            p = ez / sum_ez

        if alpha_eff > 0.0:
            u = np.full_like(b, 1.0 / Lb, dtype=np.float64)
            p = (1.0 - alpha_eff) * p + alpha_eff * u

        # Project onto capped simplex
        p = _project_capped_simplex(p, p_cap=p_cap)

    # Gradient wrt a of Σ p[k] b[k] is 2 * corr(p, a); include 2n factor outside
    grad_main = 2.0 * 2.0 * n * _corr_grad_from_p(a, p)

    # TV penalty
    tv_val, tv_grad = _tv_value_and_grad(a, eps=1e-8)
    L_tv = lam_tv * tv_val
    grad_tv = lam_tv * tv_grad

    # Adaptive multi-peak weighted-entropy penalty
    # Select top-R peaks K adaptively based on the gap between top two peaks
    Kinds, b1, b2, R = _select_top_R_peaks(b, Rmax=3)

    # Compute multi-peak participation scores q_mp[i] = Σ_{k∈Kinds} a[i] * a[k - i]
    q_mp = np.zeros_like(a, dtype=np.float64)
    if Kinds.size > 0:
        for k in Kinds:
            i_min = max(0, int(k) - (n - 1))
            i_max = min(n - 1, int(k))
            if i_max >= i_min:
                idxs = np.arange(i_min, i_max + 1, dtype=int)
                jdxs = int(k) - idxs
                valid_mask = (jdxs >= 0) & (jdxs < n)
                if np.any(valid_mask):
                    iv = idxs[valid_mask]
                    jv = jdxs[valid_mask]
                    q_mp[iv] += a[iv] * a[jv]
    sum_q = float(np.sum(q_mp))
    if sum_q > 0.0 and np.isfinite(sum_q):
        qtilde = q_mp / sum_q
    else:
        qtilde = np.zeros_like(a)

    # Beta anneals with tau relative to initial tau0; defaults to mild if tau0 missing
    if tau0 is None or tau0 <= 0.0 or not np.isfinite(tau0):
        tau0_eff = max(1e-8, float(tau))
    else:
        tau0_eff = float(tau0)
    beta = 0.6 * np.sqrt(max(float(tau), 1e-12) / tau0_eff)

    # Peak-aware weights: w = 1 - beta * qtilde, clipped and renormalized to mean 1
    w = 1.0 - beta * qtilde
    w = np.clip(w, 0.5, 1.5)
    mean_w = float(np.mean(w))
    if mean_w > 0 and np.isfinite(mean_w):
        w = w / mean_w  # renormalize average to 1

    # Huberized entropy: use eps_h tied to median(a) to stabilize gradients on near-zeros
    eps_h = float(max(1e-16, 0.1 * np.median(a))) if a.size > 0 else 1e-16
    if lam_ent != 0.0:
        # Value and gradient (keep the simple derivative form as in baseline, just with log(a+eps_h))
        L_ent = lam_ent * float(np.sum(w * a * np.log(a + eps_h)))
        grad_ent = lam_ent * (w * (np.log(a + eps_h) + 1.0))
    else:
        L_ent = 0.0
        grad_ent = np.zeros_like(a)

    L_total = L_main + L_tv + L_ent
    grad_total = grad_main + grad_tv + grad_ent
    return L_total, grad_total


def _cutting_plane_refine(a0: np.ndarray, max_peaks: int = 32, iters: int = 20, step0: float = 0.2) -> np.ndarray:
    """A small projected-gradient refinement targeting the worst M convolution peaks.

    - Select the Top-M shifts of b = conv(a, a).
    - Minimize S_M(a) = average of b[k] over these shifts using projected gradient on simplex.
    - Accept only improving steps on the true objective; adapt step size on failure.

    Returns the refined sequence (normalized).
    """
    n = a0.size
    a = np.clip(a0, 0.0, None)
    a = a / max(1e-300, np.sum(a))
    best = a.copy()
    best_val = _evaluate_true_objective(best)
    step = step0

    for _ in range(iters):
        b = _conv_aperiodic(a, a)
        Lb = b.size
        M = int(min(max_peaks, Lb))
        idx_top = np.argpartition(b, Lb - M)[-M:]
        # Uniform weights over selected peaks
        p = np.zeros_like(b, dtype=np.float64)
        p[idx_top] = 1.0 / M
        # Gradient of Σ p[k] b[k] w.r.t a is 2 * corr(p, a)
        grad = _corr_grad_from_p(a, p)
        # Take a step to reduce the surrogate; scale does not include 2n here
        cand = a - step * grad
        cand = np.clip(cand, 0.0, None)
        cand = _project_to_simplex(cand)
        val = _evaluate_true_objective(cand)
        if val + 1e-12 < best_val:
            best = cand
            best_val = val
            a = cand
            # Slightly increase step after success
            step *= 1.05
        else:
            # Backtrack
            step *= 0.5
            if step < 1e-4:
                break

    return best


def _multi_pair_shave(a0: np.ndarray, tau: float, tau0: float, max_tries: int = 4) -> np.ndarray:
    """Adaptive multi-peak pair-shaving transport.

    - Identify the top-R convolution peaks K adaptively based on the gap Δ = b_(1) - b_(2),
      with R ∈ {1,2,3} (R=1 if clear winner, R=3 if flat, else R=2).
    - For each k ∈ K, compute unique pair contributions C_k[i,j] = a[i] * a[j] over i <= j, i+j = k.
      Aggregate over K to get C_mp[i,j] = Σ_{k∈K} C_k[i,j].
    - Move a small total mass δ from the top-Q pairs (largest C_mp) to the bottom-Q pairs (smallest C_mp).
      Q is proportional to the number of unique pairs.
    - Redistribution is biased toward indices farther from all peak centers k/2 via weights
      (1 + γ * dist), with dist = average normalized distance from indices to all centers.
    - Project to the simplex and accept only if the true objective decreases. Backtrack δ otherwise.

    Returns the improved sequence or the original if no improvement.
    """
    a0 = np.clip(a0, 0.0, None)
    s0 = float(np.sum(a0))
    if s0 <= 0.0 or not np.isfinite(s0):
        return a0
    a0 = a0 / s0
    best = a0.copy()
    best_val = _evaluate_true_objective(best)
    n = a0.size
    tau0_eff = max(1e-8, float(tau0))
    delta = 0.02 * np.sqrt(max(float(tau), 0.0) / tau0_eff)

    # Nothing to do if delta extremely small
    if delta < 1e-6:
        return best

    gamma = 0.25  # bias strength for redistribution
    for _ in range(max_tries):
        a = best.copy()
        b = _conv_aperiodic(a, a)
        # Select top-R peaks adaptively
        Kinds, _, _, R = _select_top_R_peaks(b, Rmax=3)
        if R == 0:
            break

        # Centers for distance bias
        centers = 0.5 * Kinds.astype(np.float64)
        # Build aggregated pair scores over unique pairs across all selected peaks
        # Use dictionary keyed by (i, j) with i <= j
        pair_scores: dict[tuple[int, int], float] = {}
        for k in Kinds:
            i_min = max(0, int(k) - (n - 1))
            i_max = min(n - 1, int(k))
            if i_max < i_min:
                continue
            idxs = np.arange(i_min, i_max + 1, dtype=int)
            jdxs = int(k) - idxs
            mask = idxs <= jdxs
            if not np.any(mask):
                continue
            iv = idxs[mask]
            jv = jdxs[mask]
            contrib = a[iv] * a[jv]
            for i_val, j_val, cval in zip(iv.tolist(), jv.tolist(), contrib.tolist()):
                key = (int(i_val), int(j_val))
                pair_scores[key] = pair_scores.get(key, 0.0) + float(cval)

        if len(pair_scores) == 0:
            break

        # Convert dict to arrays
        pairs = np.array(list(pair_scores.keys()), dtype=int)
        ip = pairs[:, 0]
        jp = pairs[:, 1]
        Cmp = np.array([pair_scores[(int(i), int(j))] for i, j in pairs], dtype=np.float64)

        # Determine Q
        P = ip.size
        Q = int(max(8, np.floor(0.2 * P)))
        Q = max(1, min(Q, P))

        # Indices of top-Q and bottom-Q pairs by Cmp
        top_idx = np.argpartition(Cmp, -Q)[-Q:]
        bot_idx = np.argpartition(Cmp, Q)[:Q]
        # Ensure disjoint
        bot_idx = np.setdiff1d(bot_idx, top_idx, assume_unique=False)
        if bot_idx.size == 0:
            # Fallback: select farthest pairs by average distance from centers among non-top pairs
            non_top = np.setdiff1d(np.arange(P, dtype=int), top_idx, assume_unique=False)
            if non_top.size == 0:
                break
            # Pair distance: average of per-index distances to centers
            centers_f = centers
            denom = max(1.0, 0.5 * (n - 1))
            di = np.mean(np.abs(ip[non_top][:, None] - centers_f[None, :]) / denom, axis=1)
            dj = np.mean(np.abs(jp[non_top][:, None] - centers_f[None, :]) / denom, axis=1)
            dist_pair = di + dj
            m = min(Q, non_top.size)
            pick = np.argpartition(dist_pair, -m)[-m:]
            bot_idx = non_top[pick]

        # Mass to move per top pair proportional to Cmp
        C_top = Cmp[top_idx]
        sum_top = float(np.sum(C_top))
        if sum_top <= 0.0 or not np.isfinite(sum_top):
            break

        cand = a.copy()
        mass_removed = 0.0
        for tpos in top_idx:
            d = float(delta * Cmp[tpos] / (sum_top + 1e-16))
            i = int(ip[tpos])
            j = int(jp[tpos])
            if i == j:
                r = min(d, cand[i])
                cand[i] -= r
                mass_removed += r
            else:
                r_i = min(d, cand[i])
                r_j = min(d, cand[j])
                cand[i] -= r_i
                cand[j] -= r_j
                mass_removed += (r_i + r_j)

        if mass_removed <= 0.0:
            delta *= 0.5
            if delta < 1e-6:
                break
            continue

        # Build receiver indices set from bottom pairs (with multiplicity),
        # and bias by distance from all centers.
        recv_indices, counts = np.unique(
            np.concatenate([ip[bot_idx], jp[bot_idx]]), return_counts=True
        )
        # Average normalized distance from all centers
        denom = max(1.0, 0.5 * (n - 1))
        if recv_indices.size > 0:
            dist_avg = np.mean(np.abs(recv_indices[:, None] - centers[None, :]) / denom, axis=1)
            weights = counts.astype(np.float64) * (1.0 + gamma * dist_avg)
            wsum = float(np.sum(weights))
            if wsum <= 0.0 or not np.isfinite(wsum):
                # Fallback to equal
                weights = np.ones_like(weights, dtype=np.float64) / max(1, weights.size)
            else:
                weights = weights / wsum
            # Distribute mass_removed according to weights
            cand[recv_indices] += mass_removed * weights

        cand = np.clip(cand, 0.0, None)
        cand = _project_to_simplex(cand)
        val = _evaluate_true_objective(cand)
        if val + 1e-12 < best_val:
            best = cand
            best_val = val
            # Try with same delta again (a couple of micro steps)
            continue
        else:
            # Backtrack delta and retry
            delta *= 0.5
            if delta < 1e-6:
                break

    return best


def _optimize_softmax(
    a0: np.ndarray,
    time_deadline: float,
    tau_schedule: List[float],
    steps_per_tau: int = 80,
    lr0: float = 1e-2,
    tv0: float = 1e-2,
    ent0: float = 1e-3,
    verbose: bool = False,
) -> Tuple[np.ndarray, float]:
    """Optimize the Top-K log-sum-exp surrogate L_tau^K(a) with Adam over softmax logits s.
    Uses epsilon-tail mixing (coupled to peak flatness) and an adaptive active-set controller
    for Top-K, and a peak-aware learning rate stabilizer. Includes a tiny multi-pair
    peak-shaving transport at the end of each tau phase.

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

    # Precompute Top-K schedule parameters relative to 2n-1
    Lb = 2 * n - 1
    K_high_target = max(16, int(0.10 * Lb))  # early phases
    K_low_target = max(8, int(0.02 * Lb))    # later phases
    # Number of early (high-τ) phases to use higher K target
    num_high_phases = 3 if len(tau_schedule) >= 5 else 2

    # Peak-aware learning rate monitor
    max_idx_window: List[int] = []
    window_cap = 12
    adapt_interval = 5  # steps per check for active set and peak monitor
    neighbor_radius = 0
    K_current = K_high_target

    tau0 = float(tau_schedule[0]) if len(tau_schedule) > 0 else 1.0

    for t_idx, tau in enumerate(tau_schedule):
        # Target K based on phase: early -> K_high_target, later -> K_low_target
        K_target = K_high_target if t_idx < num_high_phases else K_low_target

        # Anneal TV and entropy: High tau -> stronger smoothing; low tau -> turn off
        phase_frac = t_idx / max(1.0, len(tau_schedule) - 1)
        lam_tv = tv0 * (1.0 - phase_frac)  # decays to 0
        lam_ent = ent0 * (1.0 - phase_frac)  # decays to 0

        # Base epsilon-tail mixing alpha from phase; will be increased dynamically inside grad if flat
        alpha_tail = 0.06 * (1.0 - phase_frac)
        if t_idx >= len(tau_schedule) - 2:
            alpha_tail = 0.0  # turn off near the end to sharpen (unless flat dynamic increases it)

        # Anneal learning rate slightly and additionally per phase
        opt.lr = lr0 * (0.8 ** t_idx)

        # Reset active-set controller at the beginning of a phase
        K_current = max(K_target, min(K_current, K_high_target))
        neighbor_radius = 0

        for inner in range(steps_per_tau):
            if time.time() > time_deadline:
                # Return best so far
                return best_a, best_score

            it += 1
            # Current a
            a = _softmax(s)

            # Objective and gradient wrt a using Top-K surrogate with epsilon-tail mixing
            L_tau, grad_a = _grad_surrogate(
                a,
                tau,
                lam_tv=lam_tv,
                lam_ent=lam_ent,
                topk=int(K_current),
                alpha_tail=alpha_tail,
                neighbor_radius=int(neighbor_radius),
                tau0=tau0,
            )
            # Chain rule through softmax: dL/ds_j = a_j * (grad_a_j - <grad_a, a>)
            ga_dot_a = float(np.dot(grad_a, a))
            grad_s = a * (grad_a - ga_dot_a)

            # Step
            s = opt.step(s, grad_s)

            # Track best by true objective periodically
            if it % 10 == 0:
                score = _evaluate_true_objective(a)
                if score < best_score - 1e-8:
                    best_score = score
                    best_a = a.copy()
                    last_improve_it = it

            # Adaptive active-set controller and peak-aware LR stabilization
            if it % adapt_interval == 0:
                # Compute b and its max index for monitoring
                b = _conv_aperiodic(a, a)
                kstar = int(np.argmax(b))
                # Update peak-change window
                if len(max_idx_window) == 0 or max_idx_window[-1] != kstar:
                    max_idx_window.append(kstar)
                    if len(max_idx_window) > window_cap:
                        max_idx_window.pop(0)
                # Peak-aware learning rate adjustment: if frequent changes, reduce lr
                if len(max_idx_window) >= 6:
                    changes = sum(1 for i in range(1, len(max_idx_window)) if max_idx_window[i] != max_idx_window[i - 1])
                    if changes >= int(0.6 * (len(max_idx_window) - 1)):
                        opt.lr *= 0.85
                        max_idx_window.clear()

                # Active-set adjustment: inspect gap around K_current boundary
                Lb = b.size
                Kc = int(min(max(2, K_current), Lb - 1))
                # Get slightly more than Kc to estimate gap reliably
                Kprobe = int(min(Lb - 1, max(Kc + 1, int(1.2 * Kc))))
                idx_top_probe = np.argpartition(b, Lb - Kprobe)[-Kprobe:]
                vals = np.sort(b[idx_top_probe])  # ascending
                # Largest Kc values are at the end
                if vals.size >= Kc + 1:
                    bK = vals[-Kc]
                    bK1 = vals[-(Kc + 1)]
                    gap = float(bK - bK1)
                else:
                    gap = 0.0
                bmax = float(np.max(b))
                # Relative gap threshold
                thr = max(1e-7, 1e-3 * bmax)
                if gap < thr:
                    # Flat top region -> increase K and widen neighbor radius
                    K_current = min(K_high_target, int(max(K_current + 4, K_current * 1.25)))
                    neighbor_radius = min(2, neighbor_radius + 1)
                else:
                    # Well separated -> move K down towards target and shrink radius
                    if K_current > K_target:
                        K_current = max(K_target, int(max(K_current - 2, K_current * 0.9)))
                    neighbor_radius = max(0, neighbor_radius - 1)

            # Early stop if no improvement for too long in this phase
            if it - last_improve_it > max(300, 6 * steps_per_tau) and t_idx > 0:
                break

        # End of tau phase: perform multi-peak pair shaving micro-transport
        if time.time() > time_deadline:
            return best_a, best_score
        a_phase = _softmax(s)
        a_shave = _multi_pair_shave(a_phase, tau=float(tau), tau0=float(tau0), max_tries=3)
        score_shave = _evaluate_true_objective(a_shave)
        if score_shave + 1e-12 < _evaluate_true_objective(a_phase):
            # Accept and continue from shaved sequence
            a_phase = a_shave
            s = np.log(np.clip(a_phase, 1e-16, None))
            if score_shave < best_score:
                best_score = score_shave
                best_a = a_phase.copy()
                last_improve_it = it

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
        if best[top_idx] <= 1e-10 or best[top_jdx] <= 1e-10:
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
    """Run a multi-start, multi-resolution optimizer to minimize the objective using Top-K surrogate with enhancements."""
    rng = np.random.default_rng(123456789)

    # Global time budget (seconds)
    time_budget = 980.0
    t0 = time.time()
    deadline = t0 + time_budget

    # Multi-resolution ladder; optionally extend to 1200 if time remains later
    lengths = [384, 512, 600, 768, 960]

    # Tau continuation schedule (from smooth to sharp)
    # Typical conv max ~ 1/n, so initial tau around 2/n, down to ~1e-7
    def make_tau_schedule(n: int) -> List[float]:
        base = max(5e-4, 2.5 / max(10.0, n))  # ~2.5/n
        taus = [base, base * 0.35, base * 0.12, base * 0.04, base * 0.012, 5e-6, 1e-6]
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
            steps_per_tau=85,
            lr0=8e-2,
            tv0=5e-3,
            ent0=1e-3,
            verbose=False,
        )
        # Cutting-plane micro-refinement
        a_cp = _cutting_plane_refine(a_best_seed, max_peaks=32, iters=16, step0=0.15)
        score_cp = _evaluate_true_objective(a_cp)
        if score_cp < score_seed:
            a_best_seed, score_seed = a_cp, score_cp

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
                steps_per_tau=75,
                lr0=6e-2,
                tv0=3e-3,
                ent0=4e-4,
                verbose=False,
            )
            # Peak shaving small refinement
            a_ref2 = _peak_shaving(a_ref, max_trials=25, delta_frac=0.25)
            score_ref2 = _evaluate_true_objective(a_ref2)
            if score_ref2 < score_ref:
                a_ref, score_ref = a_ref2, score_ref2

            # Small cutting-plane step
            a_cp = _cutting_plane_refine(a_ref, max_peaks=min(36, 2 * n - 1), iters=12, step0=0.12)
            score_cp = _evaluate_true_objective(a_cp)
            if score_cp < score_ref:
                a_ref, score_ref = a_cp, score_cp

            if score_ref < global_best_score:
                global_best_score = score_ref
                global_best_a = a_ref
                current = a_ref

    # If ample time remains, one more upscale to 1200 and quick refine
    if time.time() < deadline - 200.0:
        n = 1200
        current = _upsample_linear(global_best_a, n)
        tau_sched = make_tau_schedule(n)[1:] + [1e-7]
        a_ref, score_ref = _optimize_softmax(
            current,
            time_deadline=deadline,
            tau_schedule=tau_sched,
            steps_per_tau=60,
            lr0=5e-2,
            tv0=2e-3,
            ent0=0.0,
            verbose=False,
        )
        a_ref = _cutting_plane_refine(a_ref, max_peaks=40, iters=12, step0=0.1)
        a_ref = _peak_shaving(a_ref, max_trials=20, delta_frac=0.2)
        score_ref = _evaluate_true_objective(a_ref)
        if score_ref < global_best_score:
            global_best_score = score_ref
            global_best_a = a_ref

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
        # One more quick cutting-plane then peak shave
        a_final = _cutting_plane_refine(a_final, max_peaks=min(32, 2 * n - 1), iters=10, step0=0.1)
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
    """
    Evaluates a sequence of coefficients with enhanced security checks.
    Returns np.inf if the input is invalid.
    """
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
