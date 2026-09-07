Actionable design insight from this algorithm's performance on the step-function minimax objective under nonnegativity with unit-sum parameterization and FFT-based evaluation.

- Active-Set Top-K Minimax FFT with Continuation, Peak Shaving, and Cuts: Concentrating the minimax surrogate on the Top-K self-convolution peaks while blending a small epsilon-tail over all shifts (α≈0.05→0) under a softmax parameterization and FFT-based conv/corr provided focused yet stable pressure on the true maximum, and an adaptive active-set controller adjusted K when the peak gaps or identities changed; after each τ schedule, a two-pass postprocessing (peak shaving followed by a brief cutting‑plane step on the worst peaks, then a short low‑τ polish) delivered non-smooth drops in the maximum that gradients alone miss. In this task it achieved upper_bound 1.5207944825647235 with validity 1.0 and target_ratio 0.9898115868104723 in 3.58645784802502 seconds (score 0.9898115868104723), supporting reuse of the Top‑K+epsilon surrogate with adaptive K and the shave+cuts refinement whenever the worst-convolution location can shift over time.
- Peak-Aware Weighted-Entropy with Multi-Pair Shaving: When minimizing the max autocorrelation peak, tilt the entropy term away from indices that form the current worst peak by using weights w[i] = 1 − beta·qtilde[i] derived from pair participation around k*, with beta annealed toward 0 as tau decreases, and add a small, acceptance-tested multi-pair shaving step after each tau phase that moves a total mass δ from the top-Q contributing index pairs to low-contributing locations, projecting and backtracking if the true objective does not improve; this pair-aware, annealed, and conditional transport achieved validity 1.0 with target_ratio 0.990006 and upper_bound 1.5204955621 in 4.185s in this run, and should be reused under similar max-peak suppression objectives.
- Multi-Peak Participation Tilt with Shadow Shaving: Adopt adaptive multi-peak participation weighting in both the entropy tilt and transport steps: aggregate q_mp over the top-R convolution peaks with R∈{1,2,3} chosen from the gap Δ=b_(1)−b_(2) (R=1 when Δ is large, R=3 when the spectrum is flat, otherwise R=2), then form w[i]=1−β·qtilde[i], clip to [0.5,1.5], and renormalize to mean 1, using a Huberized entropy gradient with eps_h=max(1e-16, 0.1·median(a)). In the shaving step, score unique pairs across the same peak set, remove mass from top-Q pairs and redistribute to bottom-Q pairs while biasing receivers away from peak centers by (1+γ·dist) with γ≈0.25, projecting to the simplex and accepting only if the true objective improves with backtracking. This multi-peak aggregation mitigates the see-saw where suppressing the worst peak elevates the runner-up and collapses to the original single-peak behavior when Δ is large. In this run it achieved upper_bound 1.5195111499408527 (target_ratio 0.9906475513908498, validity 1.0); reuse the adaptive R-selection and distance-biased redistribution when worst-peak spectra are flat or unstable.
- Dual-Gear Top-K LSE + Hard-Max Projected Descent with Multi-Peak Transport and Cutting-Plane: By constraining iterates to the probability simplex (a ≥ 0, sum(a) = 1) at fixed length n = 600, the algorithm reduces the objective to F(a) = 2·n·max(conv(a,a)) and uses FFT-accelerated convolution/correlation to update peaks efficiently.
- Dual-Gear Top-K LSE + Hard-Max Projected Descent with Multi-Peak Transport and Cutting-Plane: It runs a logits-based Top-K log-sum-exp phase with epsilon-tail mixing and adaptive neighbor expansion to stably suppress multiple dominant lags, then switches to a hard-max projected subgradient phase that accepts steps only when the exact F(a) decreases via backtracking and trust-region step control.
- Dual-Gear Top-K LSE + Hard-Max Projected Descent with Multi-Peak Transport and Cutting-Plane: To handle near-tied peak plateaus, it interleaves cutting-plane refinement that minimizes the average of the top-M lags and multi-peak pair-shaving transport that moves mass away from top-contributing index pairs to low-pressure indices, each accepted only if F decreases.
- DualGear Top-K epsilon-mix + Hard-Max with Inverse-Pressure Shaving and Cutting-Plane: It fixes n = 600 and constrains a to the simplex (a ≥ 0, sum(a) = 1), reducing the objective to F(a) = 2·n·max(conv(a,a)), and measures and accepts improvements strictly by decreases in the exact F(a) with strict best‑so‑far tracking and backtracking on failure.
- Top‑K log-sum-exp phase with epsilon-tail mixing: It optimizes a = softmax(w) under a decreasing temperature schedule while selecting Top‑K lags and blending an epsilon-tail into the peak weights to stabilize gradients on near‑tied peak plateaus before handing off to the hard‑max phase.
- Hard‑max projected subgradient phase: It performs projected subgradient steps on the exact hard maximum with trust‑region step control and light momentum, accepting a step only when the exact objective decreases.
- Inverse-pressure shaving and cutting‑plane refinements: It periodically shaves symmetric mass from top‑contributing pairs at the worst lag and redistributes it to low‑pressure indices, and takes cutting‑plane steps minimizing the average of the top‑M peaks, with each non‑smooth move acceptance‑tested and backtracked to flatten plateaus and mitigate peak swapping.
- Adaptive Plateau-Band Top-K with Inverse-Pressure Tail: To stabilize optimization when worst-lag peaks have near ties or swap identities, expand the Top‑K active set to the union with a plateau band P = {k : b[k] >= bmax * (1 − ε)} where ε is temperature‑scheduled from 0.01 down to 5e‑4; replace the uniform epsilon‑tail with a 50‑50 blend of inverse‑pressure u[k] ∝ 1/(b[k]+λ) (λ ≈ 1e‑12) and uniform, and scale the tail weight as α_eff = clip(α_tail * (1 + c * |P| / (2n − 1)), 0.02, 0.12) with c ≈ 1.0 to increase smoothing when plateaus are wide; apply the same union set and inverse‑pressure weights in cutting‑plane refinement to focus descent on plateau shoulders while avoiding destabilization; this configuration was observed with metrics upper_bound = 1.5261567936594844, target_ratio = 0.9863337805485418, validity = 1.0, eval_time = 1.7166099000023678.
- Adaptive Top‑K Plateau‑Minimax with Peak Transport and Cutting‑Plane (ATK‑PTCP): ATK‑PTCP exploits scale invariance by fixing sum(a)=1 and enforces a ≥ 0 via softmax on logits, using FFT‑accelerated convolution/correlation to evaluate the true max while optimizing a Top‑K+plateau log‑sum‑exp surrogate with τ annealing and ε approximately 1e‑2→5e‑4, which stabilizes updates when worst‑lag identities swap.
- Adaptive Top‑K Plateau‑Minimax with Peak Transport and Cutting‑Plane (ATK‑PTCP): The method interleaves acceptance‑tested peak‑transport (pair‑shaving) moves and cutting‑plane steps that minimize the average of the top‑M lags (M ≈ 32–40), then switches to hard‑max projected subgradients with trust‑region backtracking that are accepted only when the true objective F decreases, flattening dominant peaks that smooth surrogates alone miss.
- Adaptive Top‑K Plateau‑Minimax with Peak Transport and Cutting‑Plane (ATK‑PTCP): Time safety and robustness are ensured by continuously tracking and returning the best feasible sequence, imposing an internal deadline (≈980 s under the 1000 s cap) and falling back to a normalized uniform candidate on anomalies; in the observed run, metrics reported upper_bound 1.528481012967764, target_ratio 0.9848339542519049, validity 1.0, and eval_time 21.34212531603407.
- Adaptive Top‑K Plateau‑Minimax with Peak Transport and Cutting‑Plane (ATK‑PTCP): Future designs facing nondifferentiable max objectives over nonnegative sequences should reuse this pattern: optimize on the simplex with FFT‑based evaluation, drive a Top‑K+plateau log‑sum‑exp surrogate with τ annealing and inverse‑pressure tails to handle near ties, and punctuate with acceptance‑tested hard moves (transport and cutting‑plane) to secure true reductions in the hard objective.
- ATK-PTCP v2: Plateau-Union Top‑K Minimax with Hard‑Max Polish and Pair‑Shaving: Under a fixed n = 600 and probability-simplex parameterization (a ≥ 0, sum(a) = 1), the algorithm evaluates the exact objective 2·n·max(conv(a,a)) via FFT and uses a plateau‑union Top‑K surrogate to concentrate pressure on lags at or near the current maximum, stabilizing descent and avoiding see‑sawing between adjacent peaks.
- Hard‑max polish and pair‑shaving steps in ATK-PTCP v2: The method interleaves acceptance-tested hard‑max polish (minimizing the average of the top r peaks with backtracking) and pair‑shaving mass transport, committing changes only when the exact evaluate_sequence value strictly decreases, which prevents surrogate drift and keeps improvements aligned with the true maximum of the self‑convolution.
- ATK-PTCP v2 run metrics: In the reported run, validity was 1.0, eval_time was 322.48574998392724 seconds, and target_ratio was 0.9910036187381336, indicating the approach maintained evaluator compliance and achieved strong progress within the 1000-second budget.
- ATK-PTCP-X: Combining a plateau‑union Top‑K surrogate with an inverse‑pressure tail and interleaving strictly acceptance‑tested hard moves (hard‑max polish, cutting‑plane average‑of‑tops, and multi‑pair transport) stabilized near‑tie plateaus and yielded monotone decreases in the exact objective at fixed n=600 on the L1‑simplex; this configuration achieved upper_bound 1.5188629531436617 with validity 1.0 in 505.4008847139776 seconds, so future designs targeting 2·n·max(conv(a,a)) should reuse the plateau‑aware active set plus inverse‑pressure tail and require strict F‑decrease acceptance for non‑smooth refinements, especially when working at a fixed length to avoid 2·n inflation.
- PLUMAR-PTCP Fusion: Tri‑Gear Plateau‑Minimax with Pressure Microsteps: Alternating the plateau‑union LSE surrogate descent (G1), cheap pressure‑guided microsteps (G2), and acceptance‑tested non‑smooth refinements (G3) stabilized active‑set flips on near‑tied maxima and ensured only true reductions of the nondifferentiable objective were committed.
- Pressure microsteps (G2): Inserting low‑overhead softmax‑projected mirror updates using g_s ≈ r − ⟨r, a⟩ between LSE steps improved stability when argmax(b) changed frequently, reducing see‑saw dynamics without expensive recomputation; future designs should interleave such microsteps whenever the active lag set shifts.
- L1‑simplex parameterization and FFT evaluation: Optimizing logits on the L1‑simplex (a ≥ 0, sum(a) = 1) to exploit scale invariance and computing conv(a, a) via FFT provided fast, valid iterations (validity 1.0, eval_time ≈ 219.69 s for n = 600), so future searches should normalize to the simplex and use FFT‑based autoconvolution to keep updates cheap.

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
- Peak-shaving and a small cutting-plane-like refinement to further reduce worst peaks.

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
    return float(tau_eps * (m / tau_eps + np.log(np.sum(np.exp(z)))))


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


def _grad_surrogate(
    a: np.ndarray,
    tau: float,
    lam_tv: float,
    lam_ent: float,
    topk: int | None = None,
    alpha_tail: float = 0.0,
    neighbor_radius: int = 0,
) -> Tuple[float, np.ndarray]:
    """Compute Top-K surrogate objective and gradient w.r.t a with epsilon-tail mixing.

       L_tau^K(a) = 2n * tau * log Σ_{k ∈ S} exp(b[k]/tau), S = TopK(b) expanded by a small neighbor radius.
       If topk is None or >= len(b), this reduces to full LSE.

       Gradient uses blended softmax weights:
         p = (1 - alpha_tail) * p_S + alpha_tail * u,
       where u is uniform over all shifts (length 2n-1).

       ∂L/∂a = 2 * 2n * corr(p, a) + TV + entropy terms.

       Returns (L_total, grad_total).
    """
    n = a.size
    # Convolution and Top-K restriction on b
    b = _conv_aperiodic(a, a)  # length 2n - 1
    Lb = b.size

    use_topk = topk is not None and topk > 0 and topk < Lb

    if use_topk:
        K = int(topk)
        # Base Top-K
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
        # Gather values and compute log-sum-exp restricted to idx_top
        b_sel = b[idx_top]
        tau_eps = max(1e-300, float(tau))
        m = float(np.max(b_sel))
        z = (b_sel - m) / tau_eps
        ez = np.exp(z)
        sum_ez = float(np.sum(ez))
        if sum_ez <= 0.0 or not np.isfinite(sum_ez):
            # Fallback: uniform weights over the active set
            p_sel = np.full_like(b_sel, 1.0 / b_sel.size, dtype=np.float64)
            lse_top = m + tau_eps * np.log(max(1, b_sel.size))
        else:
            p_sel = ez / sum_ez
            lse_top = m + tau_eps * np.log(sum_ez)

        # Build p over full support with zeros elsewhere
        p = np.zeros_like(b, dtype=np.float64)
        p[idx_top] = p_sel

        # Blend epsilon tail
        if alpha_tail > 0.0:
            u = np.full_like(b, 1.0 / Lb, dtype=np.float64)
            p = (1.0 - alpha_tail) * p + alpha_tail * u

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

        if alpha_tail > 0.0:
            # when using full softmax, tail mixing is redundant; keep for consistency
            u = np.full_like(b, 1.0 / Lb, dtype=np.float64)
            p = (1.0 - alpha_tail) * p + alpha_tail * u

    # Gradient wrt a of Σ p[k] b[k] is 2 * corr(p, a); include 2n factor outside
    grad_main = 2.0 * 2.0 * n * _corr_grad_from_p(a, p)

    # TV penalty
    tv_val, tv_grad = _tv_value_and_grad(a, eps=1e-8)
    L_tv = lam_tv * tv_val
    grad_tv = lam_tv * tv_grad

    # Entropy penalty (encourage dispersion): lam_ent * sum a log a
    eps = 1e-16
    L_ent = lam_ent * float(np.sum(a * np.log(a + eps)))
    grad_ent = lam_ent * (np.log(a + eps) + 1.0)

    L_total = L_main + L_tv + L_ent
    grad_total = grad_main + grad_tv + grad_ent
    return L_total, grad_total


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
    """Optimize the Top-K log-sum-exp surrogate L_tau^K(a) with Adam over softmax logits s.
    Uses epsilon-tail mixing and an adaptive active-set controller for Top-K, and
    a peak-aware learning rate stabilizer.

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

    for t_idx, tau in enumerate(tau_schedule):
        # Target K based on phase: early -> K_high_target, later -> K_low_target
        K_target = K_high_target if t_idx < num_high_phases else K_low_target

        # Anneal TV and entropy: High tau -> stronger smoothing; low tau -> turn off
        phase_frac = t_idx / max(1.0, len(tau_schedule) - 1)
        lam_tv = tv0 * (1.0 - phase_frac)  # decays to 0
        lam_ent = ent0 * (1.0 - phase_frac)  # decays to 0

        # Epsilon-tail mixing alpha: start ~0.06 and decay to 0 towards last phase
        alpha_tail = 0.06 * (1.0 - phase_frac)
        if t_idx >= len(tau_schedule) - 2:
            alpha_tail = 0.0  # turn off near the end to sharpen

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
    best = a.copy
    # fix a copy bug: ensure proper copy
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
- Mutation additions: peak-aware weighted entropy, multi-pair peak-shaving transport, and
  epsilon-tail coupling to peak flatness for improved suppression of the worst convolution peak.

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

       Entropy is peak-aware weighted: lam_ent * Σ_i w[i] a[i] log(a[i] + eps),
       with weights w[i] depending on the worst peak's pair participation of index i.

       ∂L/∂a = 2 * 2n * corr(p, a) + TV + weighted-entropy gradients.

       Returns (L_total, grad_total).
    """
    n = a.size
    # Convolution
    b = _conv_aperiodic(a, a)  # length 2n - 1
    Lb = b.size

    # Compute dynamic epsilon-tail mixing based on top-2 peak gap
    # Larger gap -> smaller alpha_tail, flatter top -> larger alpha.
    vals2 = np.partition(b, -2)[-2:]
    b1 = float(np.max(vals2))
    b2 = float(np.min(vals2))
    delta_top = b1 - b2
    sigma = 1e-3
    alpha_dyn = float(np.clip(0.02 + 0.08 * np.exp(-delta_top / max(sigma, 1e-12)), 0.02, 0.10))
    # Use at least the dynamic alpha (ensures more mixing when flat)
    alpha_eff = max(alpha_tail, alpha_dyn)

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

    # Gradient wrt a of Σ p[k] b[k] is 2 * corr(p, a); include 2n factor outside
    grad_main = 2.0 * 2.0 * n * _corr_grad_from_p(a, p)

    # TV penalty
    tv_val, tv_grad = _tv_value_and_grad(a, eps=1e-8)
    L_tv = lam_tv * tv_val
    grad_tv = lam_tv * tv_grad

    # Peak-aware weighted-entropy penalty
    # Compute worst peak index k* and participation scores q[i] = a[i] * a[k* - i]
    kstar = int(np.argmax(b))
    i_min = max(0, kstar - (n - 1))
    i_max = min(n - 1, kstar)
    idxs = np.arange(i_min, i_max + 1, dtype=int)
    jdxs = kstar - idxs
    # Use unique pairs range to define contributions per index; but q is per index i anyway
    q = np.zeros_like(a, dtype=np.float64)
    valid_mask = (jdxs >= 0) & (jdxs < n)
    if np.any(valid_mask):
        iv = idxs[valid_mask]
        jv = jdxs[valid_mask]
        q[iv] = a[iv] * a[jv]
    sum_q = float(np.sum(q))
    if sum_q > 0.0 and np.isfinite(sum_q):
        qtilde = q / sum_q
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

    # Weighted entropy value/grad
    eps = 1e-16
    if lam_ent != 0.0:
        L_ent = lam_ent * float(np.sum(w * a * np.log(a + eps)))
        grad_ent = lam_ent * (w * (np.log(a + eps) + 1.0))
    else:
        L_ent = 0.0
        grad_ent = np.zeros_like(a)

    L_total = L_main + L_tv + L_ent
    grad_total = grad_main + grad_tv + grad_ent
    return L_total, grad_total


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
    """Multi-pair peak-shaving transport targeted at the current worst convolution peak.

    - Identify k* = argmax conv(a,a).
    - Compute pair contributions C[i] = a[i] * a[k*-i] over valid i with i <= k*-i (unique pairs).
    - Move a tiny total mass δ from the top Q pairs to the bottom Q pairs (safer locations).
      δ = 0.02 * (tau / tau0)^(0.5), backtracked if no improvement.
    - Project to simplex and accept only if the true objective decreases.

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

    for _ in range(max_tries):
        a = best.copy()
        b = _conv_aperiodic(a, a)
        kstar = int(np.argmax(b))
        i_min = max(0, kstar - (n - 1))
        i_max = min(n - 1, kstar)
        idxs = np.arange(i_min, i_max + 1, dtype=int)
        jdxs = kstar - idxs
        # Unique pairs with i <= j
        mask = idxs <= jdxs
        if not np.any(mask):
            break
        ip = idxs[mask]
        jp = jdxs[mask]
        C = a[ip] * a[jp]
        if C.size == 0 or float(np.sum(C)) <= 0.0:
            break

        # Choose top Q pairs and bottom Q pairs
        Q = int(max(8, np.floor(0.2 * ip.size)))
        Q = max(1, min(Q, ip.size))
        # Top by C
        top_idx = np.argpartition(C, -Q)[-Q:]
        # Bottom by C (safer)
        bot_idx = np.argpartition(C, Q)[:Q]
        # Ensure they are disjoint; if they overlap, adjust bottom set
        bot_idx = np.setdiff1d(bot_idx, top_idx, assume_unique=False)
        if bot_idx.size == 0:
            # If fully overlapping, pick farthest pairs by distance from center k*/2
            center = 0.5 * kstar
            dist = np.abs(ip - center) + np.abs(jp - center)
            bot_idx = np.argpartition(dist, -Q)[-Q:]

        # Mass to move per top pair proportional to C
        C_top = C[top_idx]
        sum_top = float(np.sum(C_top))
        if sum_top <= 0.0 or not np.isfinite(sum_top):
            break
        # Start with current delta; adapt by backtracking if needed
        cand = a.copy()
        mass_removed = 0.0
        for tt in top_idx:
            # Desired removal from each index of the pair
            d = float(delta * C[tt] / (sum_top + 1e-16))
            i = int(ip[tt])
            j = int(jp[tt])
            if i == j:
                # Single index pair; remove up to d (once) but keep nonnegative
                r = min(d, cand[i])
                cand[i] -= r
                mass_removed += r
            else:
                r_i = min(d, cand[i])
                r_j = min(d, cand[j])
                cand[i] -= r_i
                cand[j] -= r_j
                mass_removed += (r_i + r_j)

        # Redistribute mass equally among bottom pairs' two indices
        if mass_removed <= 0.0:
            delta *= 0.5
            if delta < 1e-6:
                break
            continue
        bot_count = int(bot_idx.size)
        # Prevent division by zero
        if bot_count <= 0:
            break
        add_per_half = mass_removed / (2.0 * bot_count)
        for bb in bot_idx:
            u = int(ip[bb])
            v = int(jp[bb])
            # Split equally to two symmetric locations
            cand[u] += add_per_half
            cand[v] += add_per_half

        cand = np.clip(cand, 0.0, None)
        cand = _project_to_simplex(cand)
        val = _evaluate_true_objective(cand)
        if val + 1e-12 < best_val:
            best = cand
            best_val = val
            # Try one more tiny pass with same delta for possible extra gain
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

        # End of tau phase: perform multi-pair peak shaving micro-transport
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
    Kinds_probe, b1_probe, b2_probe, _ = _select_top_R_peaks(b, Rmax=3)
    delta_top = b1_probe - b2_probe
    sigma = 1e-3
    alpha_dyn = float(np.clip(0.02 + 0.08 * np.exp(-delta_top / max(sigma, 1e-12)), 0.02, 0.10))
    # Use at least the dynamic alpha (ensures more mixing when flat)
    alpha_eff = max(alpha_tail, alpha_dyn)

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

```python
#!/usr/bin/env python3
"""Step-function optimizer for the first autocorrelation inequality.

This solver searches for a nonnegative sequence (interpreted as step heights)
that minimizes 2 * n * max(conv(a, a)) / (sum(a)^2). Because this functional
is scale-invariant, we keep sum(a) = 1 (i.e., a on the probability simplex).
We combine a logits-based smooth minimax phase (Top‑K log-sum-exp over peak
lags, optimized with Adam on softmax logits) with a direct projected
subgradient phase targeting the exact hard maximum. FFT-accelerated
self-convolutions, a cutting-plane top‑M averaging refinement, and occasional
targeted mass-transport ("pair shaving") and mirror-descent inverse-pressure
reweighting steps help directly suppress the worst autoconvolution peaks.
Multiple seeds and a temperature continuation schedule help escape local minima.
The search respects a strict time budget and always returns the best sequence
found so far.

This variant adds:
- Epsilon-tail mixing for the Top‑K LSE weights to stabilize near-plateaus.
- Peak-aware learning-rate stabilization in the logits phase when the worst lag
  identity swaps frequently.
- Multi-peak participation weighted entropy regularization to tilt mass away
  from indices repeatedly contributing to the worst peaks.
- Multi-peak pair shaving (transport) across the top few worst lags with
  acceptance by the exact objective.
- Tiny adaptive TV in the hard-max phase to quell spikes only when needed.
"""

import json
import time
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Run a two-gear peak-aware optimization to minimize the worst self-convolution peak.

    Strategy summary:
    - Work on the simplex: a >= 0, sum(a) = 1 (scale invariance removed).
    - Objective under this normalization becomes f(a) = 2 * n * max(conv(a, a)).
    - Gear A (logits/soft-max): a = softmax(w). Optimize a smooth surrogate
      (Top‑K log-sum-exp over peak lags of b = conv(a,a)) with Adam on w.
      Temperature continuation and decaying TV+entropy regularizers maintain
      stability then sharpen to the hard objective. Between phases apply:
        * Multi-peak pair shaving (mass transport) away from strongest pairs
        * Cutting-plane (top‑M average) micro-steps
        * Inverse-pressure mirror-descent reweighting on a
        * Optional symmetry averaging (acceptance-tested)
      Includes epsilon-tail mixing for LSE stability and peak-switch LR control.
    - Gear B (projected subgradient on a): directly target the hard max by
      minimizing the average of the active top peaks with momentum, backtracking,
      and periodic pair shaving + cutting-plane refinement + mirror reweighting.
      Tiny adaptive TV penalty activates only when spike metrics indicate.
    - FFT-based convolution for efficiency (O(n log n)).
    - Multi-start initialization with deterministic shapes plus smoothed random seeds.
    - Strict runtime budget with best-so-far safeguard.
    """
    rng = np.random.default_rng(20260829)
    start_time = time.perf_counter()
    time_budget = 980.0  # leave margin under the 1000s requirement
    deadline = start_time + time_budget

    n = 600  # fixed length to avoid 2*n inflation across candidates

    # --- FFT utilities ---
    def next_pow_two(x: int) -> int:
        return 1 << (x - 1).bit_length()

    fft_len = next_pow_two(2 * n - 1)

    def conv_autocorr(a: np.ndarray) -> np.ndarray:
        """Compute b = conv(a, a) via real FFT. Returns length 2n-1 array."""
        fa = np.fft.rfft(a, n=fft_len)
        b_full = np.fft.irfft(fa * fa, n=fft_len)
        return b_full[: 2 * n - 1]

    # Objective (sum(a) assumed 1)
    def objective_from_b(b: np.ndarray) -> float:
        return 2.0 * n * float(np.max(b))

    def evaluate_objective(a: np.ndarray) -> Tuple[float, np.ndarray]:
        """Return (objective value, convolution b). Assumes sum(a)=1 and a>=0."""
        b = conv_autocorr(a)
        return objective_from_b(b), b

    # --- Simplex projection helpers ---
    def project_simplex_clip_renorm(v: np.ndarray) -> np.ndarray:
        v = np.maximum(v, 0.0)
        s = v.sum()
        if s <= 0:
            return np.full_like(v, 1.0 / len(v))
        return v / s

    def project_simplex_exact(v: np.ndarray) -> np.ndarray:
        # Duchi et al. (2008) projection onto simplex
        u = np.sort(v)[::-1]
        cssv = np.cumsum(u)
        rho_idx = np.nonzero(u * (np.arange(1, v.size + 1)) > (cssv - 1))[0]
        if len(rho_idx) == 0:
            return project_simplex_clip_renorm(v)
        rho = rho_idx[-1]
        theta = (cssv[rho] - 1.0) / (rho + 1)
        w = np.maximum(v - theta, 0.0)
        s = w.sum()
        if s <= 0:
            return np.full_like(v, 1.0 / len(v))
        return w / s

    # --- Gradient building blocks ---
    def shift_reverse(a: np.ndarray, k: int) -> np.ndarray:
        """Return s where s[t] = a[k - t] if in bounds else 0."""
        s = np.zeros_like(a)
        t_lo = max(0, k - (n - 1))
        t_hi = min(n - 1, k)
        if t_hi >= t_lo:
            start_idx = k - t_hi
            end_idx = k - t_lo
            s[t_lo : t_hi + 1] = a[start_idx : end_idx + 1][::-1]
        return s

    def laplacian_1d(a: np.ndarray) -> np.ndarray:
        """Discrete 1D Laplacian with Neumann boundary conditions."""
        g = np.zeros_like(a)
        g[1:-1] = 2 * a[1:-1] - a[:-2] - a[2:]
        g[0] = a[0] - a[1]
        g[-1] = a[-1] - a[-2]
        return g

    # --- Utilities ---
    def softmax_vec(z: np.ndarray) -> np.ndarray:
        zmax = np.max(z)
        e = np.exp(z - zmax)
        s = e.sum()
        if s <= 0:
            return np.full_like(z, 1.0 / len(z))
        return e / s

    def softmax_temp(z: np.ndarray, tau: float) -> np.ndarray:
        # softmax(z / tau)
        if tau <= 0:
            p = np.zeros_like(z)
            p[np.argmax(z)] = 1.0
            return p
        return softmax_vec(z / max(1e-12, tau))

    def to_logits_from_a(a: np.ndarray) -> np.ndarray:
        eps = 1e-12
        return np.log(np.maximum(a, eps))

    # --- Seeds ---
    def generate_seeds(num_random: int = 5) -> List[np.ndarray]:
        seeds: List[np.ndarray] = []

        # Uniform
        seeds.append(np.full(n, 1.0 / n))

        # Tent (triangular)
        x = np.linspace(-1.0, 1.0, n)
        tent = np.maximum(0.0, 1.0 - np.abs(x))
        tent /= tent.sum()
        seeds.append(tent)

        # Cosine-squared bump
        t = np.linspace(-np.pi / 2, np.pi / 2, n)
        cos2 = np.cos(t) ** 2
        cos2 /= cos2.sum()
        seeds.append(cos2)

        # Concave quadratic hump
        quad = np.maximum(0.0, 1.0 - x**2)
        quad /= quad.sum()
        seeds.append(quad)

        # Cosine^4 bump (sharper center but still smooth)
        cos4 = np.cos(t) ** 4
        cos4 /= cos4.sum()
        seeds.append(cos4)

        # Symmetric compact bump resembling [-1/4, 1/4]
        xi = np.linspace(-0.25, 0.25, n)
        base = 1.0 + 4.0 * np.abs(xi) - 16.0 * xi**2
        base = (base + base[::-1]) / 2
        base = np.maximum(base, 0.0)
        base = project_simplex_clip_renorm(base)
        seeds.append(base)

        # Smoothed random seeds via Gamma + convolutional smoothing
        ker = np.array([1.0, 2.0, 3.0, 2.0, 1.0], dtype=float)
        for _ in range(num_random):
            r = rng.gamma(shape=0.6, scale=1.0, size=n)
            rr = np.convolve(r, ker, mode="same")
            rr = np.maximum(rr, 0.0)
            rr /= rr.sum()
            seeds.append(rr)

        return seeds

    # --- Peak selection ---
    def topk_indices_desc(b: np.ndarray, k: int) -> np.ndarray:
        k = min(k, b.size)
        if k <= 0:
            return np.array([], dtype=int)
        idx = np.argpartition(-b, kth=k - 1)[:k]
        idx = idx[np.argsort(-b[idx])]
        return idx

    def expand_with_neighbors(b: np.ndarray, idx: np.ndarray, rel_eps: float = 0.002) -> np.ndarray:
        """Expand active peak set by including ±1 neighbors if they are close in height."""
        if idx.size == 0:
            return idx
        bmax = b[idx[0]]
        sel = set(int(k) for k in idx.tolist())
        for k in idx:
            for nb in (k - 1, k + 1):
                if 0 <= nb < b.size and b[nb] >= bmax * (1.0 - rel_eps):
                    sel.add(int(nb))
        out = np.array(sorted(sel), dtype=int)
        return out

    def argmax_plateau_set(b: np.ndarray, rel_eps: float = 5e-5) -> np.ndarray:
        """Return the set of indices near the maximum of b within a tiny relative epsilon."""
        bmax = float(np.max(b))
        thr = bmax * (1.0 - rel_eps)
        S = np.where(b >= thr)[0]
        return S

    # --- Pressure and mirror-descent style reweighting ---
    def pressure_from_peaks(a: np.ndarray, K: List[int]) -> np.ndarray:
        """Compute pressure vector p_i = sum_{k in K} shift_reverse(a,k)[i]."""
        p = np.zeros_like(a)
        for kk in K:
            p += shift_reverse(a, int(kk))
        return p

    def mirror_inverse_pressure(a: np.ndarray, b: np.ndarray, K: List[int], step: float = 0.04) -> np.ndarray:
        """Mirror-descent style multiplicative reweighting by inverse pressure."""
        if len(K) == 0:
            return a
        p = pressure_from_peaks(a, K)
        # Reweight: a_i <- a_i * exp(-step * p_i) then renormalize.
        delta = -step * p
        delta = np.clip(delta, -5.0, 5.0)
        w = np.exp(delta)
        a_new = a * w
        a_new = project_simplex_clip_renorm(a_new)
        return a_new

    # --- Multi-peak pair shaving mass-transport ---
    def pair_shave(
        a: np.ndarray,
        b: np.ndarray,
        K: List[int],
        shave_frac: float = 0.002,
        target_pool: int = 24,
        peaks_to_shave: int = 2,
        max_pairs_per_peak: int = 24,
    ) -> np.ndarray:
        """Reduce mass on top-contributing pairs for the worst few peaks; redistribute to low-pressure indices.

        Multi-peak version: iterates over the top 'peaks_to_shave' lags in K and
        shaves a fraction (shave_frac / J) at each, aggregating the removed mass
        and redistributing it to low-pressure indices (computed from all K).
        """
        if len(K) == 0 or shave_frac <= 0:
            return a

        J = max(1, min(peaks_to_shave, len(K)))
        a_new = a.copy()
        removed_total = 0.0

        # Global pressure for redistribution from all provided K
        pressure = np.zeros_like(a)
        for kk in K:
            pressure += shift_reverse(a, int(kk))

        for kk in K[:J]:
            kstar = int(kk)
            i_lo = max(0, kstar - (n - 1))
            i_hi = min(n - 1, kstar)
            if i_hi < i_lo:
                continue

            i = np.arange(i_lo, i_hi + 1)
            j = kstar - i
            pair_contrib = a_new[i] * a_new[j]

            M = min(max_pairs_per_peak, i.size)
            # Select pairs by current contribution at this stage
            sel = np.argpartition(-pair_contrib, kth=min(M - 1, pair_contrib.size - 1))[:M]
            sel = sel[np.argsort(-pair_contrib[sel])]

            # Shave a small fraction symmetrically from both indices in the pair
            local_removed = 0.0
            for pidx in sel:
                ii = i[pidx]
                jj = j[pidx]
                frac = shave_frac / J
                di = min(frac * a_new[ii], a_new[ii])
                dj = min(frac * a_new[jj], a_new[jj])
                if di > 0:
                    a_new[ii] -= di
                    local_removed += di
                if dj > 0:
                    a_new[jj] -= dj
                    local_removed += dj
            removed_total += local_removed

        if removed_total <= 0:
            return a

        # Redistribution targets: lowest pressure indices
        tp = min(target_pool, n)
        target_idx = np.argpartition(pressure, kth=min(tp - 1, pressure.size - 1))[:tp]
        target_idx = np.unique(target_idx)
        if target_idx.size == 0:
            return a

        # Weight redistribution by inverse pressure to escape current peaks
        w = 1.0 / (1e-12 + pressure[target_idx])
        w_sum = w.sum()
        if w_sum <= 0:
            w = np.full_like(w, 1.0 / target_idx.size)
        else:
            w /= w_sum
        a_new[target_idx] += removed_total * w
        a_new = project_simplex_clip_renorm(a_new)
        return a_new

    # --- Symmetrization (acceptance-based) ---
    def symmetrize_accept(a: np.ndarray, val_current: float) -> Tuple[np.ndarray, float]:
        """Try symmetric averaging a <- (a + a[::-1]) / 2 and accept if improves."""
        a_sym = 0.5 * (a + a[::-1])
        a_sym = project_simplex_clip_renorm(a_sym)
        v_sym, _ = evaluate_objective(a_sym)
        if v_sym + 1e-12 < val_current:
            return a_sym, float(v_sym)
        return a, float(val_current)

    # --- Gear A: logits softmax + Top-K LSE with Adam ---
    def gearA_logits_optimization(a_init: np.ndarray, best_val_global: float) -> Tuple[np.ndarray, float]:
        """Run several τ phases of logits/soft surrogate optimization."""
        # Initialize logits from a_init
        w = to_logits_from_a(project_simplex_clip_renorm(a_init))

        # Adam parameters
        lr_w = 0.06
        beta1, beta2 = 0.9, 0.999
        eps_adam = 1e-8
        m = np.zeros_like(w)
        v = np.zeros_like(w)
        t_adam = 0

        # τ schedule and Top-K schedule (sharpen gradually)
        tau_schedule = [0.35, 0.20, 0.10, 0.05, 0.025, 0.012]
        K_schedule = [18, 14, 10, 7, 5, 4]
        max_iters_per_tau = 120

        # Regularization (decays across phases)
        tv0 = 8e-4
        ent0 = 5e-4

        # Epsilon-tail mixing for Top‑K LSE weights
        epsmix0 = 0.12

        # Shaving cadence and small cutting-plane micro-steps per phase
        shave_every = 36
        mirror_every = 24  # inverse-pressure mirror reweighting cadence
        micro_cutting_steps = 10
        topM_cut = 6

        # Peak-switch LR stabilization state
        last_top_idx = None
        peak_switches = 0

        # Track best
        a = softmax_vec(w)
        val, b = evaluate_objective(a)
        best_a, best_val = a.copy(), float(val)
        no_improve_count = 0

        for phase, (tau, K) in enumerate(zip(tau_schedule, K_schedule)):
            if time.perf_counter() > deadline:
                break

            tv_coeff = tv0 * (0.6 ** phase)
            ent_coeff = ent0 * (0.5 ** phase)
            epsmix = epsmix0 * (0.5 ** phase)

            for it in range(max_iters_per_tau):
                if time.perf_counter() > deadline:
                    break

                # Current a/b/val
                a = softmax_vec(w)
                val, b = evaluate_objective(a)

                if val + 1e-12 < best_val:
                    best_val = float(val)
                    best_a = a.copy()
                    no_improve_count = 0
                else:
                    no_improve_count += 1

                # Build Top‑K set and expanded neighbors
                k_idx = topk_indices_desc(b, K)
                if k_idx.size == 0:
                    continue
                # Track the identity of the current worst peak for LR stabilization
                top_now = int(k_idx[0])
                if last_top_idx is not None and top_now != last_top_idx:
                    peak_switches += 1
                last_top_idx = top_now
                # Expand with neighbors when nearly tied
                k_act = expand_with_neighbors(b, k_idx, rel_eps=0.0015)

                # Softmax weights over active peaks with epsilon-tail mixing
                soft_w = softmax_temp(b[k_act], tau)
                uni_w = np.full_like(soft_w, 1.0 / soft_w.size)
                p_k = (1.0 - epsmix) * soft_w + epsmix * uni_w

                # Gradient wrt a of L(b(a)) where L is LSE over Top‑K: dL/da = 2 * sum_k p_k * shift_reverse(a,k)
                g_a = np.zeros_like(a)
                for wk, kk in zip(p_k, k_act):
                    g_a += wk * shift_reverse(a, int(kk))
                g_a *= 2.0

                # Participation-weighted entropy: derive pressure from top-R peaks by gap
                # Determine R by inspecting gaps among top peaks
                R = 1
                if k_idx.size >= 2 and b[k_idx[1]] > 0.996 * b[k_idx[0]]:
                    R = 2
                if k_idx.size >= 3 and b[k_idx[2]] > 0.994 * b[k_idx[0]]:
                    R = 3
                idx_R = k_idx[:R]
                part = pressure_from_peaks(a, [int(x) for x in idx_R]) if idx_R.size > 0 else np.zeros_like(a)
                part_norm = part / (np.max(part) + 1e-12)
                ent_weight = (0.5 + 0.5 * part_norm)  # weight in [0.5, 1] toward high-participation indices

                # Regularization in a-space
                if tv_coeff > 0:
                    g_a += tv_coeff * laplacian_1d(a)
                if ent_coeff > 0:
                    # Entropy gradient: d/da_j [sum a_j log a_j] = log a_j + 1, weighted by participation
                    g_a += ent_coeff * ent_weight * (np.log(a + 1e-12) + 1.0)

                # Chain rule to logits w: da/dw = diag(a) - a a^T, so g_w = a * (g_a - <g_a, a>)
                ga_dot_a = float(np.dot(g_a, a))
                g_w = a * (g_a - ga_dot_a)

                # Adam step on logits
                t_adam += 1
                m = beta1 * m + (1 - beta1) * g_w
                v = beta2 * v + (1 - beta2) * (g_w * g_w)
                m_hat = m / (1 - beta1**t_adam)
                v_hat = v / (1 - beta2**t_adam)
                w = w - lr_w * m_hat / (np.sqrt(v_hat) + eps_adam)

                # Peak-switch based LR stabilization check every 20 steps
                if (it + 1) % 20 == 0:
                    if peak_switches >= 6:
                        lr_w *= 0.8
                    else:
                        lr_w = min(lr_w * 1.02, 0.08)
                    peak_switches = 0  # reset counter

                # Occasionally try inverse-pressure mirror reweighting (accept only if true objective improves)
                if (it + 1) % mirror_every == 0:
                    a_curr = softmax_vec(w)
                    k_now = topk_indices_desc(b, max(4, K))
                    a_mirr = mirror_inverse_pressure(a_curr, b, list(k_now), step=0.035)
                    if not np.allclose(a_mirr, a_curr):
                        v_mirr, _ = evaluate_objective(a_mirr)
                        if v_mirr + 1e-12 < val:
                            w = to_logits_from_a(a_mirr)
                            val = v_mirr
                            if val + 1e-12 < best_val:
                                best_val = float(val)
                                best_a = a_mirr.copy()
                                no_improve_count = 0

                # Occasionally try multi-peak pair shaving (accept only if true objective improves)
                if (it + 1) % shave_every == 0:
                    k_now = topk_indices_desc(b, max(4, K))
                    k_now = expand_with_neighbors(b, k_now, rel_eps=0.0015)
                    a_curr = softmax_vec(w)
                    a_shaved = pair_shave(
                        a_curr,
                        b,
                        list(k_now),
                        shave_frac=0.002,
                        target_pool=24,
                        peaks_to_shave=2,
                        max_pairs_per_peak=24,
                    )
                    if not np.allclose(a_shaved, a_curr):
                        shaved_val, _ = evaluate_objective(a_shaved)
                        if shaved_val + 1e-12 < val:
                            # Accept shaving by resetting logits to the shaved a
                            w = to_logits_from_a(a_shaved)
                            val = shaved_val
                            if val + 1e-12 < best_val:
                                best_val = float(val)
                                best_a = a_shaved.copy()
                                no_improve_count = 0

                # Early prune unpromising seeds relative to known global best
                if best_val_global < np.inf and val > 1.012 * best_val_global and (it > 80 or phase > 1):
                    break

                # Mild stagnation handling: small noise to logits and tiny lr decay
                if no_improve_count > 60:
                    w += rng.standard_normal(n) * 1e-3
                    lr_w *= 0.95
                    no_improve_count = 0

            # After each τ phase, do a short cutting-plane refinement on logits:
            # minimize the average of the top‑M peaks (harder surrogate than LSE).
            a = softmax_vec(w)
            val, b = evaluate_objective(a)
            k_act = topk_indices_desc(b, topM_cut)
            if k_act.size > 0:
                for _ in range(micro_cutting_steps):
                    if time.perf_counter() > deadline:
                        break
                    # Average-of-topM gradient in a-space
                    g_a = np.zeros_like(a)
                    for kk in k_act:
                        g_a += shift_reverse(a, int(kk))
                    g_a *= (2.0 / k_act.size)
                    # Tiny TV to keep smoothness
                    g_a += (tv0 * 0.2) * laplacian_1d(a)
                    ga_dot_a = float(np.dot(g_a, a))
                    g_w = a * (g_a - ga_dot_a)
                    # Small step
                    w = w - 0.02 * g_w
                    a = softmax_vec(w)
                    v_try, b_try = evaluate_objective(a)
                    if v_try + 1e-12 < val:
                        val, b = v_try, b_try
                        if val + 1e-12 < best_val:
                            best_val = float(val)
                            best_a = a.copy()

            # Try symmetry averaging (acceptance-based)
            a = softmax_vec(w)
            val, _ = evaluate_objective(a)
            a_sym, v_sym = symmetrize_accept(a, val)
            if v_sym + 1e-12 < val:
                w = to_logits_from_a(a_sym)
                if v_sym + 1e-12 < best_val:
                    best_val = float(v_sym)
                    best_a = a_sym.copy()

        return best_a, best_val

    # --- Gear B: projected subgradient on a (hard max focus) ---
    def gearB_projected_subgradient(a_init: np.ndarray, best_val_global: float) -> Tuple[np.ndarray, float]:
        a = project_simplex_clip_renorm(a_init)
        val, b = evaluate_objective(a)
        best_a, best_val = a.copy(), float(val)

        # Hyperparameters
        eta = 0.6
        eta_min = 1e-4
        momentum = 0.7
        v_mom = np.zeros_like(a)
        iters = 420
        shave_every = 35
        mirror_every = 28
        tv_coeff = 2e-4  # tiny smoothing (adaptive below)
        topM = 4

        stagnation = 0

        for it in range(iters):
            if time.perf_counter() > deadline:
                break

            val, b = evaluate_objective(a)
            if val + 1e-12 < best_val:
                best_val = float(val)
                best_a = a.copy()
                stagnation = 0
            else:
                stagnation += 1

            # Active set S: combine topM and argmax plateau to prevent peak swapping
            k_idx = topk_indices_desc(b, topM)
            plateau = argmax_plateau_set(b, rel_eps=5e-5)
            k_act = np.unique(np.concatenate((k_idx, plateau)))
            k_act = expand_with_neighbors(b, k_act, rel_eps=0.0015)

            # Subgradient of average of active peaks
            g = np.zeros_like(a)
            if k_act.size > 0:
                for kk in k_act:
                    g += shift_reverse(a, int(kk))
                g *= (2.0 / k_act.size)

            # Adaptive TV: only increase when spikes appear
            if (it + 1) % 25 == 0:
                am = float(np.max(a))
                med = float(np.median(a))
                ratio = am / (med + 1e-12)
                tv_coeff = 3e-4 if ratio > 30.0 else 1e-4

            if tv_coeff > 0:
                g += tv_coeff * laplacian_1d(a)

            # Momentum and backtracking projected step
            g_step = g + momentum * v_mom
            v_mom = g_step.copy()  # simple heavy-ball style

            accepted = False
            eta_local = eta
            curr_val = val
            for _ in range(7):
                a_cand = project_simplex_exact(a - eta_local * g_step)
                new_val, _ = evaluate_objective(a_cand)
                if new_val + 1e-12 < curr_val:
                    a = a_cand
                    val = new_val
                    eta = min(eta_local * 1.05, 1.0)
                    accepted = True
                    break
                else:
                    eta_local *= 0.5
                    if eta_local < eta_min:
                        break

            if not accepted:
                eta = max(eta * 0.7, eta_min)

            # Periodic inverse-pressure mirror reweighting (acceptance-only)
            if (it + 1) % mirror_every == 0:
                k_now = topk_indices_desc(b, max(4, topM))
                a_mirr = mirror_inverse_pressure(a, b, list(k_now), step=0.03)
                if not np.allclose(a_mirr, a):
                    v_mirr, _ = evaluate_objective(a_mirr)
                    if v_mirr + 1e-12 < val:
                        a = a_mirr
                        val = v_mirr
                        eta = min(eta * 1.05, 1.0)

            # Periodic multi-peak pair shaving
            if (it + 1) % shave_every == 0:
                k_idx_now = topk_indices_desc(b, max(3, topM))
                a_shaved = pair_shave(
                    a,
                    b,
                    list(k_idx_now),
                    shave_frac=0.002,
                    target_pool=24,
                    peaks_to_shave=2,
                    max_pairs_per_peak=24,
                )
                if not np.allclose(a_shaved, a):
                    shaved_val, _ = evaluate_objective(a_shaved)
                    if shaved_val + 1e-12 < val:
                        a = a_shaved
                        val = shaved_val
                        eta = min(eta * 1.1, 1.0)

            # Cutting-plane micro refinement intermittently: a few steps on average-of-topM
            if (it + 1) % 70 == 0 and k_act.size > 0:
                for _ in range(6):
                    if time.perf_counter() > deadline:
                        break
                    g_cp = np.zeros_like(a)
                    for kk in k_act:
                        g_cp += shift_reverse(a, int(kk))
                    g_cp *= (2.0 / k_act.size)
                    a_try = project_simplex_exact(a - 0.25 * eta * g_cp)
                    v_try, _ = evaluate_objective(a_try)
                    if v_try + 1e-12 < val:
                        a = a_try
                        val = v_try
                        if val + 1e-12 < best_val:
                            best_val = float(val)
                            best_a = a.copy()

            # Symmetry averaging attempt
            if (it + 1) % 90 == 0:
                a_sym, v_sym = symmetrize_accept(a, val)
                if v_sym + 1e-12 < val:
                    a = a_sym
                    val = v_sym
                    if val + 1e-12 < best_val:
                        best_val = float(val)
                        best_a = a.copy()

            # Early prune if clearly worse than global best
            if best_val_global < np.inf and val > 1.01 * best_val_global and it > 120:
                break

            # Mild randomization if stagnating
            if stagnation > 80:
                a = project_simplex_clip_renorm(a + rng.standard_normal(n) * 5e-5)
                stagnation = 0

        return best_a, best_val

    # --- Orchestration: multi-start and two-gear pipeline ---
    seeds = generate_seeds(num_random=6)

    # Track global best
    global_best_a = None
    global_best_val = np.inf

    # Iterate seeds deterministically
    for si, a0 in enumerate(seeds):
        if time.perf_counter() > deadline:
            break
        # Tiny symmetry-breaking noise
        a0 = project_simplex_clip_renorm(a0 + rng.standard_normal(n) * 1e-5)

        # Gear A
        aA, vA = gearA_logits_optimization(a0, global_best_val)

        # Gear B starting from Gear A best
        aB, vB = gearB_projected_subgradient(aA, min(global_best_val, vA))

        cand_a, cand_val = (aB, vB) if vB + 1e-12 < vA else (aA, vA)
        if cand_val + 1e-12 < global_best_val:
            global_best_val = float(cand_val)
            global_best_a = cand_a.copy()

    # Safety fallback
    if global_best_a is None:
        global_best_a = np.full(n, 1.0 / n)

    # Final micro-polish: a few low-τ LSE steps then mirror + pair shave nudge
    if time.perf_counter() < deadline - 1.0:
        a = project_simplex_clip_renorm(global_best_a)
        for _ in range(20):
            if time.perf_counter() > deadline:
                break
            val, b = evaluate_objective(a)
            k_idx = topk_indices_desc(b, 4)
            if k_idx.size == 0:
                break
            p_k = softmax_temp(b[k_idx], tau=0.015)
            # Epsilon-tail mixing even in micro-polish
            p_k = 0.9 * p_k + 0.1 * np.full_like(p_k, 1.0 / len(p_k))
            g_a = np.zeros_like(a)
            for wk, kk in zip(p_k, k_idx):
                g_a += wk * shift_reverse(a, int(kk))
            g_a *= 2.0
            a_try = project_simplex_exact(a - 0.08 * g_a)
            v_try, _ = evaluate_objective(a_try)
            if v_try + 1e-12 < val:
                a = a_try
                if v_try + 1e-12 < global_best_val:
                    global_best_val = float(v_try)
                    global_best_a = a.copy()
        # Tiny inverse-pressure mirror step
        val, b = evaluate_objective(global_best_a)
        k_idx = topk_indices_desc(b, 4)
        a_mirr = mirror_inverse_pressure(global_best_a, b, list(k_idx), step=0.02)
        v_try, _ = evaluate_objective(a_mirr)
        if v_try + 1e-12 < global_best_val:
            global_best_val = float(v_try)
            global_best_a = a_mirr.copy()
        # Tiny pair shave
        val, b = evaluate_objective(global_best_a)
        k_idx = topk_indices_desc(b, 4)
        a_shaved = pair_shave(global_best_a, b, list(k_idx), shave_frac=0.0015, target_pool=20, peaks_to_shave=2)
        v_try, _ = evaluate_objective(a_shaved)
        if v_try + 1e-12 < global_best_val:
            global_best_val = float(v_try)
            global_best_a = a_shaved.copy()

    # Final normalization and return
    global_best_a = project_simplex_clip_renorm(global_best_a)
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
    return list(search_for_best_sequence())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```

```python
#!/usr/bin/env python3
"""Step-function optimizer for the first autocorrelation inequality.

This solver searches for a nonnegative sequence (interpreted as step heights)
that minimizes 2 * n * max(conv(a, a)) / (sum(a)^2). Because this functional
is scale-invariant, we keep sum(a) = 1 (i.e., a on the probability simplex).
We combine a logits-based smooth minimax phase (Top‑K log-sum-exp over peak
lags, optimized with Adam on softmax logits) with a direct projected
subgradient phase targeting the exact hard maximum. FFT-accelerated
self-convolutions, a cutting-plane top‑M averaging refinement, and occasional
targeted mass-transport ("pair shaving") and mirror-descent inverse-pressure
reweighting steps help directly suppress the worst autoconvolution peaks.
Multiple seeds and a temperature continuation schedule help escape local minima.
The search respects a strict time budget and always returns the best sequence
found so far.

This variant adds:
- Epsilon-tail mixing for the Top‑K LSE weights to stabilize near-plateaus.
- Peak-aware learning-rate stabilization in the logits phase when the worst lag
  identity swaps frequently.
- Multi-peak participation weighted entropy regularization to tilt mass away
  from indices repeatedly contributing to the worst peaks.
- Multi-peak pair shaving (transport) across the top few worst lags with
  acceptance by the exact objective.
- Tiny adaptive TV in the hard-max phase to quell spikes only when needed.
- Single-pass inverse-pressure pre-shaving of selected seeds before optimization.
- Optional cross-resolution shaping: brief low-cost refinement at nearby
  resolutions (e.g., 512 and 960) with resampling back to n=600.
"""

import json
import time
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Run a two-gear peak-aware optimization to minimize the worst self-convolution peak.

    Strategy summary:
    - Work on the simplex: a >= 0, sum(a) = 1 (scale invariance removed).
    - Objective under this normalization becomes f(a) = 2 * n * max(conv(a, a)).
    - Gear A (logits/soft-max): a = softmax(w). Optimize a smooth surrogate
      (Top‑K log-sum-exp over peak lags of b = conv(a,a)) with Adam on w.
      Temperature continuation and decaying TV+entropy regularizers maintain
      stability then sharpen to the hard objective. Between phases apply:
        * Multi-peak pair shaving (mass transport) away from strongest pairs
        * Cutting-plane (top‑M average) micro-steps
        * Inverse-pressure mirror-descent reweighting on a
        * Optional symmetry averaging (acceptance-tested)
      Includes epsilon-tail mixing for LSE stability and peak-switch LR control.
    - Gear B (projected subgradient on a): directly target the hard max by
      minimizing the average of the active top peaks with momentum, backtracking,
      and periodic pair shaving + cutting-plane refinement + mirror reweighting.
      Tiny adaptive TV penalty activates only when spike metrics indicate.
    - FFT-based convolution for efficiency (O(n log n)).
    - Multi-start initialization with deterministic shapes plus smoothed random seeds.
    - Optional cross-resolution shaping at nearby resolutions, then resample to n.
    - Strict runtime budget with best-so-far safeguard.
    """
    rng = np.random.default_rng(20260829)
    start_time = time.perf_counter()
    time_budget = 980.0  # leave margin under the 1000s requirement
    deadline = start_time + time_budget

    n = 600  # fixed length to avoid 2*n inflation across candidates

    # --- FFT utilities ---
    def next_pow_two(x: int) -> int:
        return 1 << (x - 1).bit_length()

    fft_len = next_pow_two(2 * n - 1)

    def conv_autocorr(a: np.ndarray) -> np.ndarray:
        """Compute b = conv(a, a) via real FFT. Returns length 2n-1 array."""
        fa = np.fft.rfft(a, n=fft_len)  # zero-padded to fft_len
        b_full = np.fft.irfft(fa * fa, n=fft_len)  # circular conv at fft_len
        # Since a is zero-padded, the first 2n-1 entries equal linear convolution
        return b_full[: 2 * n - 1]

    # Objective (sum(a) assumed 1)
    def objective_from_b(b: np.ndarray) -> float:
        return 2.0 * n * float(np.max(b))

    def evaluate_objective(a: np.ndarray) -> Tuple[float, np.ndarray]:
        """Return (objective value, convolution b). Assumes sum(a)=1 and a>=0."""
        b = conv_autocorr(a)
        return objective_from_b(b), b

    # --- Simplex projection helpers ---
    def project_simplex_clip_renorm(v: np.ndarray) -> np.ndarray:
        v = np.maximum(v, 0.0)
        s = v.sum()
        if s <= 0:
            return np.full_like(v, 1.0 / len(v))
        return v / s

    def project_simplex_exact(v: np.ndarray) -> np.ndarray:
        # Duchi et al. (2008) projection onto simplex
        u = np.sort(v)[::-1]
        cssv = np.cumsum(u)
        rho_idx = np.nonzero(u * (np.arange(1, v.size + 1)) > (cssv - 1))[0]
        if len(rho_idx) == 0:
            return project_simplex_clip_renorm(v)
        rho = rho_idx[-1]
        theta = (cssv[rho] - 1.0) / (rho + 1)
        w = np.maximum(v - theta, 0.0)
        s = w.sum()
        if s <= 0:
            return np.full_like(v, 1.0 / len(v))
        return w / s

    # --- Gradient building blocks ---
    def shift_reverse(a: np.ndarray, k: int) -> np.ndarray:
        """Return s where s[t] = a[k - t] if in bounds else 0."""
        s = np.zeros_like(a)
        t_lo = max(0, k - (n - 1))
        t_hi = min(n - 1, k)
        if t_hi >= t_lo:
            start_idx = k - t_hi
            end_idx = k - t_lo
            s[t_lo : t_hi + 1] = a[start_idx : end_idx + 1][::-1]
        return s

    def laplacian_1d(a: np.ndarray) -> np.ndarray:
        """Discrete 1D Laplacian with Neumann boundary conditions."""
        g = np.zeros_like(a)
        g[1:-1] = 2 * a[1:-1] - a[:-2] - a[2:]
        g[0] = a[0] - a[1]
        g[-1] = a[-1] - a[-2]
        return g

    # --- Utilities ---
    def softmax_vec(z: np.ndarray) -> np.ndarray:
        zmax = np.max(z)
        e = np.exp(z - zmax)
        s = e.sum()
        if s <= 0:
            return np.full_like(z, 1.0 / len(z))
        return e / s

    def softmax_temp(z: np.ndarray, tau: float) -> np.ndarray:
        # softmax(z / tau)
        if tau <= 0:
            p = np.zeros_like(z)
            p[np.argmax(z)] = 1.0
            return p
        return softmax_vec(z / max(1e-12, tau))

    def to_logits_from_a(a: np.ndarray) -> np.ndarray:
        eps = 1e-12
        return np.log(np.maximum(a, eps))

    # --- Resampling between resolutions ---
    def resample_to_length(a_src: np.ndarray, n_dst: int) -> np.ndarray:
        """Linear resample from len(a_src) to n_dst, then project to simplex."""
        n_src = a_src.size
        if n_src == n_dst:
            return project_simplex_clip_renorm(a_src)
        # positions in [0, 1]
        xs = np.linspace(0.0, 1.0, n_src)
        xd = np.linspace(0.0, 1.0, n_dst)
        a_dst = np.interp(xd, xs, a_src)
        return project_simplex_clip_renorm(a_dst)

    # --- Seeds ---
    def generate_seeds(num_random: int = 5) -> List[np.ndarray]:
        seeds: List[np.ndarray] = []

        # Uniform
        seeds.append(np.full(n, 1.0 / n))

        # Tent (triangular)
        x = np.linspace(-1.0, 1.0, n)
        tent = np.maximum(0.0, 1.0 - np.abs(x))
        tent /= tent.sum()
        seeds.append(tent)

        # Cosine-squared bump
        t = np.linspace(-np.pi / 2, np.pi / 2, n)
        cos2 = np.cos(t) ** 2
        cos2 /= cos2.sum()
        seeds.append(cos2)

        # Concave quadratic hump
        quad = np.maximum(0.0, 1.0 - x**2)
        quad /= quad.sum()
        seeds.append(quad)

        # Cosine^4 bump (sharper center but still smooth)
        cos4 = np.cos(t) ** 4
        cos4 /= cos4.sum()
        seeds.append(cos4)

        # Symmetric compact bump resembling support [-1/4, 1/4]
        xi = np.linspace(-0.25, 0.25, n)
        base = 1.0 + 4.0 * np.abs(xi) - 16.0 * xi**2
        base = (base + base[::-1]) / 2
        base = np.maximum(base, 0.0)
        base = project_simplex_clip_renorm(base)
        seeds.append(base)

        # Smoothed random seeds via Gamma + convolutional smoothing
        ker = np.array([1.0, 2.0, 3.0, 2.0, 1.0], dtype=float)
        for _ in range(num_random):
            r = rng.gamma(shape=0.6, scale=1.0, size=n)
            rr = np.convolve(r, ker, mode="same")
            rr = np.maximum(rr, 0.0)
            rr /= rr.sum()
            seeds.append(rr)

        return seeds

    # --- Peak selection ---
    def topk_indices_desc(b: np.ndarray, k: int) -> np.ndarray:
        k = min(k, b.size)
        if k <= 0:
            return np.array([], dtype=int)
        idx = np.argpartition(-b, kth=k - 1)[:k]
        idx = idx[np.argsort(-b[idx])]
        return idx

    def expand_with_neighbors(b: np.ndarray, idx: np.ndarray, rel_eps: float = 0.002) -> np.ndarray:
        """Expand active peak set by including ±1 neighbors if they are close in height."""
        if idx.size == 0:
            return idx
        bmax = b[idx[0]]
        sel = set(int(k) for k in idx.tolist())
        for k in idx:
            for nb in (k - 1, k + 1):
                if 0 <= nb < b.size and b[nb] >= bmax * (1.0 - rel_eps):
                    sel.add(int(nb))
        out = np.array(sorted(sel), dtype=int)
        return out

    def argmax_plateau_set(b: np.ndarray, rel_eps: float = 5e-5) -> np.ndarray:
        """Return the set of indices near the maximum of b within a tiny relative epsilon."""
        bmax = float(np.max(b))
        thr = bmax * (1.0 - rel_eps)
        S = np.where(b >= thr)[0]
        return S

    # --- Pressure and mirror-descent style reweighting ---
    def pressure_from_peaks(a: np.ndarray, K: List[int]) -> np.ndarray:
        """Compute pressure vector p_i = sum_{k in K} shift_reverse(a,k)[i]."""
        p = np.zeros_like(a)
        for kk in K:
            p += shift_reverse(a, int(kk))
        return p

    def mirror_inverse_pressure(a: np.ndarray, b: np.ndarray, K: List[int], step: float = 0.04) -> np.ndarray:
        """Mirror-descent style multiplicative reweighting by inverse pressure."""
        if len(K) == 0:
            return a
        p = pressure_from_peaks(a, K)
        # Reweight: a_i <- a_i * exp(-step * p_i) then renormalize.
        delta = -step * p
        delta = np.clip(delta, -5.0, 5.0)
        w = np.exp(delta)
        a_new = a * w
        a_new = project_simplex_clip_renorm(a_new)
        return a_new

    # --- Multi-peak pair shaving mass-transport ---
    def pair_shave(
        a: np.ndarray,
        b: np.ndarray,
        K: List[int],
        shave_frac: float = 0.002,
        target_pool: int = 24,
        peaks_to_shave: int = 2,
        max_pairs_per_peak: int = 24,
    ) -> np.ndarray:
        """Reduce mass on top-contributing pairs for the worst few peaks; redistribute to low-pressure indices.

        Multi-peak version: iterates over the top 'peaks_to_shave' lags in K and
        shaves a fraction (shave_frac / J) at each, aggregating the removed mass
        and redistributing it to low-pressure indices (computed from all K).
        Redistribution further biases toward indices far from the peak centers.
        """
        if len(K) == 0 or shave_frac <= 0:
            return a

        J = max(1, min(peaks_to_shave, len(K)))
        a_new = a.copy()
        removed_total = 0.0

        # Global pressure for redistribution from all provided K
        pressure = np.zeros_like(a)
        for kk in K:
            pressure += shift_reverse(a, int(kk))

        # Distance score: indices farther from centers k/2 get larger weight
        idxs = np.arange(n, dtype=float)
        dist_acc = np.zeros_like(a)
        for kk in K:
            center = 0.5 * kk
            dist_acc += np.abs(idxs - center)
        dist_acc = dist_acc / (np.max(dist_acc) + 1e-12)

        for kk in K[:J]:
            kstar = int(kk)
            i_lo = max(0, kstar - (n - 1))
            i_hi = min(n - 1, kstar)
            if i_hi < i_lo:
                continue

            i = np.arange(i_lo, i_hi + 1)
            j = kstar - i
            pair_contrib = a_new[i] * a_new[j]

            M = min(max_pairs_per_peak, i.size)
            # Select pairs by current contribution at this stage
            sel = np.argpartition(-pair_contrib, kth=min(M - 1, pair_contrib.size - 1))[:M]
            sel = sel[np.argsort(-pair_contrib[sel])]

            # Shave a small fraction symmetrically from both indices in the pair
            local_removed = 0.0
            for pidx in sel:
                ii = int(i[pidx])
                jj = int(j[pidx])
                frac = shave_frac / J
                di = min(frac * a_new[ii], a_new[ii])
                dj = min(frac * a_new[jj], a_new[jj])
                if di > 0:
                    a_new[ii] -= di
                    local_removed += di
                if dj > 0:
                    a_new[jj] -= dj
                    local_removed += dj
            removed_total += local_removed

        if removed_total <= 0:
            return a

        # Redistribution targets: lowest pressure indices
        tp = min(target_pool, n)
        target_idx = np.argpartition(pressure, kth=min(tp - 1, pressure.size - 1))[:tp]
        target_idx = np.unique(target_idx)
        if target_idx.size == 0:
            return a

        # Weight redistribution by inverse pressure and distance from centers
        inv_press = 1.0 / (1e-12 + pressure[target_idx])
        dist_weight = 0.5 + 0.5 * dist_acc[target_idx]  # in [0.5, 1]
        w = inv_press * dist_weight
        w_sum = w.sum()
        if w_sum <= 0:
            w = np.full_like(w, 1.0 / target_idx.size)
        else:
            w /= w_sum
        a_new[target_idx] += removed_total * w
        a_new = project_simplex_clip_renorm(a_new)
        return a_new

    # --- Symmetrization (acceptance-based) ---
    def symmetrize_accept(a: np.ndarray, val_current: float) -> Tuple[np.ndarray, float]:
        """Try symmetric averaging a <- (a + a[::-1]) / 2 and accept if improves."""
        a_sym = 0.5 * (a + a[::-1])
        a_sym = project_simplex_clip_renorm(a_sym)
        v_sym, _ = evaluate_objective(a_sym)
        if v_sym + 1e-12 < val_current:
            return a_sym, float(v_sym)
        return a, float(val_current)

    # --- Gear A: logits softmax + Top-K LSE with Adam ---
    def gearA_logits_optimization(a_init: np.ndarray, best_val_global: float) -> Tuple[np.ndarray, float]:
        """Run several τ phases of logits/soft surrogate optimization."""
        # Initialize logits from a_init
        a_init = project_simplex_clip_renorm(a_init)
        w = to_logits_from_a(a_init)

        # Adam parameters
        lr_w = 0.06
        beta1, beta2 = 0.9, 0.999
        eps_adam = 1e-8
        m = np.zeros_like(w)
        v = np.zeros_like(w)
        t_adam = 0

        # τ schedule and Top-K schedule (sharpen gradually)
        tau_schedule = [0.42, 0.26, 0.16, 0.08, 0.04, 0.016]
        # Slightly larger K early to control more peaks
        K_schedule = [40, 28, 20, 14, 10, 6]
        max_iters_per_tau = 120

        # Regularization (decays across phases)
        tv0 = 8e-4
        ent0 = 5e-4

        # Epsilon-tail mixing for Top‑K LSE weights
        epsmix0 = 0.12

        # Shaving cadence and cutting-plane micro-steps per phase
        shave_every = 36
        mirror_every = 24  # inverse-pressure mirror reweighting cadence
        micro_cutting_steps = 16
        topM_cut = 16

        # Peak-switch LR stabilization state
        last_top_idx = None
        peak_switches = 0

        # Track best
        a = softmax_vec(w)
        val, b = evaluate_objective(a)
        best_a, best_val = a.copy(), float(val)
        no_improve_count = 0

        for phase, (tau, K) in enumerate(zip(tau_schedule, K_schedule)):
            if time.perf_counter() > deadline:
                break

            tv_coeff = tv0 * (0.6 ** phase)
            ent_coeff = ent0 * (0.5 ** phase)
            epsmix = epsmix0 * (0.5 ** phase)

            for it in range(max_iters_per_tau):
                if time.perf_counter() > deadline:
                    break

                # Current a/b/val
                a = softmax_vec(w)
                val, b = evaluate_objective(a)

                if val + 1e-12 < best_val:
                    best_val = float(val)
                    best_a = a.copy()
                    no_improve_count = 0
                else:
                    no_improve_count += 1

                # Build Top‑K set and expanded neighbors
                k_idx = topk_indices_desc(b, K)
                if k_idx.size == 0:
                    continue
                # Track the identity of the current worst peak for LR stabilization
                top_now = int(k_idx[0])
                if last_top_idx is not None and top_now != last_top_idx:
                    peak_switches += 1
                last_top_idx = top_now
                # Expand with neighbors when nearly tied
                k_act = expand_with_neighbors(b, k_idx, rel_eps=0.0015)

                # Softmax weights over active peaks with epsilon-tail mixing
                soft_w = softmax_temp(b[k_act], tau)
                uni_w = np.full_like(soft_w, 1.0 / soft_w.size)
                p_k = (1.0 - epsmix) * soft_w + epsmix * uni_w

                # Gradient wrt a of L(b(a)) where L is LSE over Top‑K: dL/da = 2 * sum_k p_k * shift_reverse(a,k)
                g_a = np.zeros_like(a)
                for wk, kk in zip(p_k, k_act):
                    g_a += wk * shift_reverse(a, int(kk))
                g_a *= 2.0

                # Participation-weighted entropy: derive pressure from top-R peaks by gap
                R = 1
                if k_idx.size >= 2 and b[k_idx[1]] > 0.996 * b[k_idx[0]]:
                    R = 2
                if k_idx.size >= 3 and b[k_idx[2]] > 0.994 * b[k_idx[0]]:
                    R = 3
                idx_R = k_idx[:R]
                part = pressure_from_peaks(a, [int(x) for x in idx_R]) if idx_R.size > 0 else np.zeros_like(a)
                part_norm = part / (np.max(part) + 1e-12)
                ent_weight = (0.5 + 0.5 * part_norm)  # weight in [0.5, 1] toward high-participation indices

                # Regularization in a-space
                if tv_coeff > 0:
                    g_a += tv_coeff * laplacian_1d(a)
                if ent_coeff > 0:
                    # Entropy gradient: d/da_j [sum a_j log a_j] = log a_j + 1, weighted by participation
                    g_a += ent_coeff * ent_weight * (np.log(a + 1e-12) + 1.0)

                # Chain rule to logits w: da/dw = diag(a) - a a^T, so g_w = a * (g_a - <g_a, a>)
                ga_dot_a = float(np.dot(g_a, a))
                g_w = a * (g_a - ga_dot_a)

                # Adam step on logits
                t_adam += 1
                m = beta1 * m + (1 - beta1) * g_w
                v = beta2 * v + (1 - beta2) * (g_w * g_w)
                m_hat = m / (1 - beta1**t_adam)
                v_hat = v / (1 - beta2**t_adam)
                w = w - lr_w * m_hat / (np.sqrt(v_hat) + eps_adam)

                # Peak-switch based LR stabilization check every 20 steps
                if (it + 1) % 20 == 0:
                    if peak_switches >= 6:
                        lr_w *= 0.8
                    else:
                        lr_w = min(lr_w * 1.02, 0.08)
                    peak_switches = 0  # reset counter

                # Occasionally try inverse-pressure mirror reweighting (accept only if true objective improves)
                if (it + 1) % mirror_every == 0:
                    a_curr = softmax_vec(w)
                    k_now = topk_indices_desc(b, max(4, K))
                    a_mirr = mirror_inverse_pressure(a_curr, b, list(k_now), step=0.035)
                    if not np.allclose(a_mirr, a_curr):
                        v_mirr, _ = evaluate_objective(a_mirr)
                        if v_mirr + 1e-12 < val:
                            w = to_logits_from_a(a_mirr)
                            val = v_mirr
                            if val + 1e-12 < best_val:
                                best_val = float(val)
                                best_a = a_mirr.copy()
                                no_improve_count = 0

                # Occasionally try multi-peak pair shaving (accept only if true objective improves)
                if (it + 1) % shave_every == 0:
                    k_now = topk_indices_desc(b, max(4, K))
                    k_now = expand_with_neighbors(b, k_now, rel_eps=0.0015)
                    a_curr = softmax_vec(w)
                    a_shaved = pair_shave(
                        a_curr,
                        b,
                        list(k_now),
                        shave_frac=0.002,
                        target_pool=24,
                        peaks_to_shave=2,
                        max_pairs_per_peak=24,
                    )
                    if not np.allclose(a_shaved, a_curr):
                        shaved_val, _ = evaluate_objective(a_shaved)
                        if shaved_val + 1e-12 < val:
                            # Accept shaving by resetting logits to the shaved a
                            w = to_logits_from_a(a_shaved)
                            val = shaved_val
                            if val + 1e-12 < best_val:
                                best_val = float(val)
                                best_a = a_shaved.copy()
                                no_improve_count = 0

                # Early prune unpromising seeds relative to known global best
                if best_val_global < np.inf and val > 1.012 * best_val_global and (it > 80 or phase > 1):
                    break

                # Mild stagnation handling: small noise to logits and tiny lr decay
                if no_improve_count > 60:
                    w += rng.standard_normal(n) * 1e-3
                    lr_w *= 0.95
                    no_improve_count = 0

            # After each τ phase, do a short cutting-plane refinement on logits:
            # minimize the average of the top‑M peaks (harder surrogate than LSE).
            a = softmax_vec(w)
            val, b = evaluate_objective(a)
            k_act = topk_indices_desc(b, topM_cut)
            if k_act.size > 0:
                for _ in range(micro_cutting_steps):
                    if time.perf_counter() > deadline:
                        break
                    # Average-of-topM gradient in a-space
                    g_a = np.zeros_like(a)
                    for kk in k_act:
                        g_a += shift_reverse(a, int(kk))
                    g_a *= (2.0 / k_act.size)
                    # Tiny TV to keep smoothness
                    g_a += (tv0 * 0.2) * laplacian_1d(a)
                    ga_dot_a = float(np.dot(g_a, a))
                    g_w = a * (g_a - ga_dot_a)
                    # Small step
                    w = w - 0.02 * g_w
                    a = softmax_vec(w)
                    v_try, b_try = evaluate_objective(a)
                    if v_try + 1e-12 < val:
                        val, b = v_try, b_try
                        if val + 1e-12 < best_val:
                            best_val = float(val)
                            best_a = a.copy()

            # Try symmetry averaging (acceptance-based)
            a = softmax_vec(w)
            val, _ = evaluate_objective(a)
            a_sym, v_sym = symmetrize_accept(a, val)
            if v_sym + 1e-12 < val:
                w = to_logits_from_a(a_sym)
                if v_sym + 1e-12 < best_val:
                    best_val = float(v_sym)
                    best_a = a_sym.copy()

        return best_a, best_val

    # --- Gear B: projected subgradient on a (hard max focus) ---
    def gearB_projected_subgradient(a_init: np.ndarray, best_val_global: float) -> Tuple[np.ndarray, float]:
        a = project_simplex_clip_renorm(a_init)
        val, b = evaluate_objective(a)
        best_a, best_val = a.copy(), float(val)

        # Hyperparameters
        eta = 0.6
        eta_min = 1e-4
        momentum = 0.7
        v_mom = np.zeros_like(a)
        iters = 420
        shave_every = 35
        mirror_every = 28
        tv_coeff = 2e-4  # tiny smoothing (adaptive below)
        topM = 8  # slightly larger set to control plateau

        stagnation = 0

        for it in range(iters):
            if time.perf_counter() > deadline:
                break

            val, b = evaluate_objective(a)
            if val + 1e-12 < best_val:
                best_val = float(val)
                best_a = a.copy()
                stagnation = 0
            else:
                stagnation += 1

            # Active set S: combine topM and argmax plateau to prevent peak swapping
            k_idx = topk_indices_desc(b, topM)
            plateau = argmax_plateau_set(b, rel_eps=5e-5)
            k_act = np.unique(np.concatenate((k_idx, plateau)))
            k_act = expand_with_neighbors(b, k_act, rel_eps=0.0015)

            # Subgradient of average of active peaks
            g = np.zeros_like(a)
            if k_act.size > 0:
                for kk in k_act:
                    g += shift_reverse(a, int(kk))
                g *= (2.0 / k_act.size)

            # Adaptive TV: only increase when spikes appear
            if (it + 1) % 25 == 0:
                am = float(np.max(a))
                med = float(np.median(a))
                ratio = am / (med + 1e-12)
                tv_coeff = 3e-4 if ratio > 30.0 else 1e-4

            if tv_coeff > 0:
                g += tv_coeff * laplacian_1d(a)

            # Momentum and backtracking projected step
            g_step = g + momentum * v_mom
            v_mom = g_step.copy()  # simple heavy-ball style

            accepted = False
            eta_local = eta
            curr_val = val
            for _ in range(7):
                a_cand = project_simplex_exact(a - eta_local * g_step)
                new_val, _ = evaluate_objective(a_cand)
                if new_val + 1e-12 < curr_val:
                    a = a_cand
                    val = new_val
                    eta = min(eta_local * 1.05, 1.0)
                    accepted = True
                    break
                else:
                    eta_local *= 0.5
                    if eta_local < eta_min:
                        break

            if not accepted:
                eta = max(eta * 0.7, eta_min)

            # Periodic inverse-pressure mirror reweighting (acceptance-only)
            if (it + 1) % mirror_every == 0:
                k_now = topk_indices_desc(b, max(4, topM))
                a_mirr = mirror_inverse_pressure(a, b, list(k_now), step=0.03)
                if not np.allclose(a_mirr, a):
                    v_mirr, _ = evaluate_objective(a_mirr)
                    if v_mirr + 1e-12 < val:
                        a = a_mirr
                        val = v_mirr
                        eta = min(eta * 1.05, 1.0)

            # Periodic multi-peak pair shaving
            if (it + 1) % shave_every == 0:
                k_idx_now = topk_indices_desc(b, max(3, topM))
                a_shaved = pair_shave(
                    a,
                    b,
                    list(k_idx_now),
                    shave_frac=0.002,
                    target_pool=24,
                    peaks_to_shave=2,
                    max_pairs_per_peak=24,
                )
                if not np.allclose(a_shaved, a):
                    shaved_val, _ = evaluate_objective(a_shaved)
                    if shaved_val + 1e-12 < val:
                        a = a_shaved
                        val = shaved_val
                        eta = min(eta * 1.1, 1.0)

            # Cutting-plane micro refinement intermittently: a few steps on average-of-topM
            if (it + 1) % 70 == 0 and k_act.size > 0:
                for _ in range(6):
                    if time.perf_counter() > deadline:
                        break
                    g_cp = np.zeros_like(a)
                    for kk in k_act:
                        g_cp += shift_reverse(a, int(kk))
                    g_cp *= (2.0 / k_act.size)
                    a_try = project_simplex_exact(a - 0.25 * eta * g_cp)
                    v_try, _ = evaluate_objective(a_try)
                    if v_try + 1e-12 < val:
                        a = a_try
                        val = v_try
                        if val + 1e-12 < best_val:
                            best_val = float(val)
                            best_a = a.copy()

            # Symmetry averaging attempt
            if (it + 1) % 90 == 0:
                a_sym, v_sym = symmetrize_accept(a, val)
                if v_sym + 1e-12 < val:
                    a = a_sym
                    val = v_sym
                    if val + 1e-12 < best_val:
                        best_val = float(val)
                        best_a = a.copy()

            # Early prune if clearly worse than global best
            if best_val_global < np.inf and val > 1.01 * best_val_global and it > 120:
                break

            # Mild randomization if stagnating
            if stagnation > 80:
                a = project_simplex_clip_renorm(a + rng.standard_normal(n) * 5e-5)
                stagnation = 0

        return best_a, best_val

    # --- Optional: seed preconditioning with single-pass inverse-pressure shaving ---
    def pre_shave_seed(a_seed: np.ndarray) -> np.ndarray:
        a0 = project_simplex_clip_renorm(a_seed)
        val0, b0 = evaluate_objective(a0)
        # Build peak set: top few lags with neighbors
        k0 = topk_indices_desc(b0, 6)
        k0 = expand_with_neighbors(b0, k0, rel_eps=0.0015)
        if k0.size == 0:
            return a0
        # Try two shave strengths with backtracking acceptance
        for frac in (0.01, 0.005):
            a_try = pair_shave(a0, b0, list(k0), shave_frac=frac, target_pool=28, peaks_to_shave=2, max_pairs_per_peak=28)
            v_try, _ = evaluate_objective(a_try)
            if v_try + 1e-12 < val0:
                return a_try
        return a0

    # --- Cross-resolution shaping (short, acceptance-based, time-limited) ---
    def cross_resolution_refine(a_curr: np.ndarray, best_val_now: float) -> Tuple[np.ndarray, float]:
        """Briefly refine at resolutions in {512, 960}, resample back to n=600. Acceptance-tested."""
        if time.perf_counter() > deadline - 4.0:
            return a_curr, best_val_now

        def short_logits_pass(a_init_small: np.ndarray) -> np.ndarray:
            """A very short logits Top-K LSE pass at the small resolution."""
            m = a_init_small.size
            fft_len_m = next_pow_two(2 * m - 1)

            def conv_m(a: np.ndarray) -> np.ndarray:
                fa = np.fft.rfft(a, n=fft_len_m)
                c = np.fft.irfft(fa * fa, n=fft_len_m)
                return c[: 2 * m - 1]

            def eval_m(a: np.ndarray) -> Tuple[float, np.ndarray]:
                b = conv_m(a)
                return float(np.max(b)), b  # scale irrelevant internally; we minimize max(b)

            def shift_rev_m(a: np.ndarray, k: int) -> np.ndarray:
                s = np.zeros_like(a)
                lo = max(0, k - (m - 1))
                hi = min(m - 1, k)
                if hi >= lo:
                    s[lo : hi + 1] = a[(k - hi) : (k - lo + 1)][::-1]
                return s

            w = to_logits_from_a(project_simplex_clip_renorm(a_init_small))
            # tiny schedule
            tau_sched = [0.30, 0.10]
            K_sched = [max(4, m // 64), max(4, m // 96)]
            lr = 0.05
            m1 = np.zeros_like(w)
            m2 = np.zeros_like(w)
            tstep = 0
            for tau, K in zip(tau_sched, K_sched):
                for _ in range(40):
                    if time.perf_counter() > deadline - 1.0:
                        break
                    a = softmax_vec(w)
                    vmax, b = eval_m(a)
                    idx = topk_indices_desc(b, int(K))
                    if idx.size == 0:
                        break
                    idx = expand_with_neighbors(b, idx, rel_eps=0.002)
                    pk = softmax_temp(b[idx], tau)
                    pk = 0.9 * pk + 0.1 * np.full_like(pk, 1.0 / len(pk))
                    g_a = np.zeros_like(a)
                    for wk, kk in zip(pk, idx):
                        g_a += wk * shift_rev_m(a, int(kk))
                    g_a *= 2.0
                    # tiny smoothing
                    g_a += 4e-4 * laplacian_1d(a)
                    dot = float(np.dot(g_a, a))
                    g_w = a * (g_a - dot)
                    tstep += 1
                    m1 = 0.9 * m1 + 0.1 * g_w
                    m2 = 0.999 * m2 + 0.001 * (g_w * g_w)
                    w = w - lr * m1 / (np.sqrt(m2) + 1e-8)
            return softmax_vec(w)

        a_best = a_curr.copy()
        v_best = float(best_val_now)
        for m in (512, 960):
            if time.perf_counter() > deadline - 2.0:
                break
            # Down/up sample
            a_small = resample_to_length(a_curr, m)
            a_small = short_logits_pass(a_small)
            # back to n
            a_res = resample_to_length(a_small, n)
            a_res = project_simplex_clip_renorm(a_res)
            v_res, _ = evaluate_objective(a_res)
            if v_res + 1e-12 < v_best:
                a_best = a_res
                v_best = float(v_res)
        return a_best, v_best

    # --- Orchestration: multi-start and two-gear pipeline ---
    seeds = generate_seeds(num_random=6)

    # Precondition selected seeds via single-pass inverse-pressure shaving
    pre_idx = [0, 1, 2, 5]  # indices of seeds to pre-shave: uniform, tent, cos2, compact bump
    for idx in pre_idx:
        if 0 <= idx < len(seeds):
            seeds[idx] = pre_shave_seed(seeds[idx])

    # Track global best
    global_best_a = None
    global_best_val = np.inf

    # Iterate seeds deterministically
    for si, a0 in enumerate(seeds):
        if time.perf_counter() > deadline:
            break
        # Tiny symmetry-breaking noise
        a0 = project_simplex_clip_renorm(a0 + rng.standard_normal(n) * 1e-5)

        # Gear A
        aA, vA = gearA_logits_optimization(a0, global_best_val)

        # Gear B starting from Gear A best
        aB, vB = gearB_projected_subgradient(aA, min(global_best_val, vA))

        cand_a, cand_val = (aB, vB) if vB + 1e-12 < vA else (aA, vA)

        # Optional cross-resolution shaping on the better candidate (cheap and short)
        cand_a, cand_val = cross_resolution_refine(cand_a, cand_val)

        if cand_val + 1e-12 < global_best_val:
            global_best_val = float(cand_val)
            global_best_a = cand_a.copy()

    # Safety fallback
    if global_best_a is None:
        global_best_a = np.full(n, 1.0 / n)

    # Final micro-polish: a few low-τ LSE steps then mirror + pair shave nudge, plus tiny uniform mixing trial
    if time.perf_counter() < deadline - 1.0:
        a = project_simplex_clip_renorm(global_best_a)
        for _ in range(20):
            if time.perf_counter() > deadline:
                break
            val, b = evaluate_objective(a)
            k_idx = topk_indices_desc(b, 6)
            if k_idx.size == 0:
                break
            p_k = softmax_temp(b[k_idx], tau=0.015)
            # Epsilon-tail mixing even in micro-polish
            p_k = 0.9 * p_k + 0.1 * np.full_like(p_k, 1.0 / len(p_k))
            g_a = np.zeros_like(a)
            for wk, kk in zip(p_k, k_idx):
                g_a += wk * shift_reverse(a, int(kk))
            g_a *= 2.0
            # a-step with exact simplex projection
            a_try = project_simplex_exact(a - 0.08 * g_a)
            v_try, _ = evaluate_objective(a_try)
            if v_try + 1e-12 < val:
                a = a_try
                if v_try + 1e-12 < global_best_val:
                    global_best_val = float(v_try)
                    global_best_a = a.copy()
        # Tiny inverse-pressure mirror step
        val, b = evaluate_objective(global_best_a)
        k_idx = topk_indices_desc(b, 6)
        a_mirr = mirror_inverse_pressure(global_best_a, b, list(k_idx), step=0.02)
        v_try, _ = evaluate_objective(a_mirr)
        if v_try + 1e-12 < global_best_val:
            global_best_val = float(v_try)
            global_best_a = a_mirr.copy()
        # Tiny pair shave
        val, b = evaluate_objective(global_best_a)
        k_idx = topk_indices_desc(b, 6)
        a_shaved = pair_shave(global_best_a, b, list(k_idx), shave_frac=0.0015, target_pool=20, peaks_to_shave=2)
        v_try, _ = evaluate_objective(a_shaved)
        if v_try + 1e-12 < global_best_val:
            global_best_val = float(v_try)
            global_best_a = a_shaved.copy()
        # Tiny uniform mixing to flatten residual plateau
        a_uni = np.full(n, 1.0 / n)
        for mix in (0.005, 0.01):
            a_mix = project_simplex_clip_renorm((1.0 - mix) * global_best_a + mix * a_uni)
            v_mix, _ = evaluate_objective(a_mix)
            if v_mix + 1e-12 < global_best_val:
                global_best_val = float(v_mix)
                global_best_a = a_mix.copy()

    # Final normalization and return
    global_best_a = project_simplex_clip_renorm(global_best_a)
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
    return list(search_for_best_sequence())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```

```python
#!/usr/bin/env python3
"""Step-function optimizer for the first autocorrelation inequality.

This solver searches for a nonnegative sequence (interpreted as step heights)
that minimizes 2 * n * max(conv(a, a)) / (sum(a)^2). Because this functional
is scale-invariant, we keep sum(a) = 1 (i.e., a on the probability simplex).
We combine a logits-based smooth minimax phase (Top‑K log-sum-exp over peak
lags, optimized with Adam on softmax logits) with a direct projected
subgradient phase targeting the exact hard maximum. FFT-accelerated
self-convolutions, a cutting-plane top‑M averaging refinement, and occasional
targeted mass-transport ("pair shaving") and mirror-descent inverse-pressure
reweighting steps help directly suppress the worst autoconvolution peaks.
Multiple seeds and a temperature continuation schedule help escape local minima.
The search respects a strict time budget and always returns the best sequence
found so far.

This variant adds:
- Epsilon-tail mixing for the Top‑K LSE weights to stabilize near-plateaus.
- Peak-aware learning-rate stabilization in the logits phase when the worst lag
  identity swaps frequently.
- Multi-peak participation weighted entropy regularization to tilt mass away
  from indices repeatedly contributing to the worst peaks.
- Multi-peak pair shaving (transport) across the top few worst lags with
  acceptance by the exact objective.
- Tiny adaptive TV in the hard-max phase to quell spikes only when needed.
- Single-pass inverse-pressure pre-shaving of selected seeds before optimization.
- Optional cross-resolution shaping: brief low-cost refinement at nearby
  resolutions (e.g., 512 and 960) with resampling back to n=600.

Mutation applied:
- Plateau-band union active set S for the Top‑K surrogate: S = Top‑K ∪ P where
  P = {k : b[k] >= bmax * (1 − ε)} with ε scheduled from 0.01 to 5e‑4 across τ.
- Inverse‑pressure epsilon‑tail mixing: replace the uniform epsilon tail with a
  50‑50 blend of inverse‑pressure u_inv[k] ∝ 1/(b[k]+λ) and uniform distribution
  over all lags. Tail weight α_eff scales with plateau width:
  α_eff = clip(α_tail_base * (1 + |P|/(2n−1)), 0.02, 0.12).
- Cutting‑plane micro‑refinement over the same union set uses inverse‑pressure
  weights over selected lags instead of uniform averaging.
"""

import json
import time
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Run a two-gear peak-aware optimization to minimize the worst self-convolution peak.

    Strategy summary:
    - Work on the simplex: a >= 0, sum(a) = 1 (scale invariance removed).
    - Objective under this normalization becomes f(a) = 2 * n * max(conv(a, a)).
    - Gear A (logits/soft-max): a = softmax(w). Optimize a smooth surrogate
      (Top‑K log-sum-exp over peak lags of b = conv(a,a)) with Adam on w.
      Temperature continuation and decaying TV+entropy regularizers maintain
      stability then sharpen to the hard objective. Between phases apply:
        * Multi-peak pair shaving (mass transport) away from strongest pairs
        * Cutting-plane (top‑M average) micro-steps
        * Inverse-pressure mirror-descent reweighting on a
        * Optional symmetry averaging (acceptance-tested)
      Includes inverse-pressure epsilon-tail mixing and union with an
      argmax plateau band to stabilize near-plateaus.
    - Gear B (projected subgradient on a): directly target the hard max by
      minimizing the average of the active top peaks with momentum, backtracking,
      and periodic pair shaving + cutting-plane refinement + mirror reweighting.
      Tiny adaptive TV penalty activates only when spike metrics indicate.
    - FFT-based convolution for efficiency (O(n log n)).
    - Multi-start initialization with deterministic shapes plus smoothed random seeds.
    - Optional cross-resolution shaping at nearby resolutions, then resample to n.
    - Strict runtime budget with best-so-far safeguard.
    """
    rng = np.random.default_rng(20260829)
    start_time = time.perf_counter()
    time_budget = 980.0  # leave margin under the 1000s requirement
    deadline = start_time + time_budget

    n = 600  # fixed length to avoid 2*n inflation across candidates
    num_lags = 2 * n - 1

    # --- FFT utilities ---
    def next_pow_two(x: int) -> int:
        return 1 << (x - 1).bit_length()

    fft_len = next_pow_two(2 * n - 1)

    def conv_autocorr(a: np.ndarray) -> np.ndarray:
        """Compute b = conv(a, a) via real FFT. Returns length 2n-1 array."""
        fa = np.fft.rfft(a, n=fft_len)  # zero-padded to fft_len
        b_full = np.fft.irfft(fa * fa, n=fft_len)  # circular conv at fft_len
        # Since a is zero-padded, the first 2n-1 entries equal linear convolution
        return b_full[: 2 * n - 1]

    # Objective (sum(a) assumed 1)
    def objective_from_b(b: np.ndarray) -> float:
        return 2.0 * n * float(np.max(b))

    def evaluate_objective(a: np.ndarray) -> Tuple[float, np.ndarray]:
        """Return (objective value, convolution b). Assumes sum(a)=1 and a>=0."""
        b = conv_autocorr(a)
        return objective_from_b(b), b

    # --- Simplex projection helpers ---
    def project_simplex_clip_renorm(v: np.ndarray) -> np.ndarray:
        v = np.maximum(v, 0.0)
        s = v.sum()
        if s <= 0:
            return np.full_like(v, 1.0 / len(v))
        return v / s

    def project_simplex_exact(v: np.ndarray) -> np.ndarray:
        # Duchi et al. (2008) projection onto simplex
        u = np.sort(v)[::-1]
        cssv = np.cumsum(u)
        rho_idx = np.nonzero(u * (np.arange(1, v.size + 1)) > (cssv - 1))[0]
        if len(rho_idx) == 0:
            return project_simplex_clip_renorm(v)
        rho = rho_idx[-1]
        theta = (cssv[rho] - 1.0) / (rho + 1)
        w = np.maximum(v - theta, 0.0)
        s = w.sum()
        if s <= 0:
            return np.full_like(v, 1.0 / len(v))
        return w / s

    # --- Gradient building blocks ---
    def shift_reverse(a: np.ndarray, k: int) -> np.ndarray:
        """Return s where s[t] = a[k - t] if in bounds else 0."""
        s = np.zeros_like(a)
        t_lo = max(0, k - (n - 1))
        t_hi = min(n - 1, k)
        if t_hi >= t_lo:
            start_idx = k - t_hi
            end_idx = k - t_lo
            s[t_lo : t_hi + 1] = a[start_idx : end_idx + 1][::-1]
        return s

    def laplacian_1d(a: np.ndarray) -> np.ndarray:
        """Discrete 1D Laplacian with Neumann boundary conditions."""
        g = np.zeros_like(a)
        g[1:-1] = 2 * a[1:-1] - a[:-2] - a[2:]
        g[0] = a[0] - a[1]
        g[-1] = a[-1] - a[-2]
        return g

    # --- Utilities ---
    def softmax_vec(z: np.ndarray) -> np.ndarray:
        zmax = np.max(z)
        e = np.exp(z - zmax)
        s = e.sum()
        if s <= 0:
            return np.full_like(z, 1.0 / len(z))
        return e / s

    def softmax_temp(z: np.ndarray, tau: float) -> np.ndarray:
        # softmax(z / tau)
        if tau <= 0:
            p = np.zeros_like(z)
            p[np.argmax(z)] = 1.0
            return p
        return softmax_vec(z / max(1e-12, tau))

    def to_logits_from_a(a: np.ndarray) -> np.ndarray:
        eps = 1e-12
        return np.log(np.maximum(a, eps))

    # --- Resampling between resolutions ---
    def resample_to_length(a_src: np.ndarray, n_dst: int) -> np.ndarray:
        """Linear resample from len(a_src) to n_dst, then project to simplex."""
        n_src = a_src.size
        if n_src == n_dst:
            return project_simplex_clip_renorm(a_src)
        # positions in [0, 1]
        xs = np.linspace(0.0, 1.0, n_src)
        xd = np.linspace(0.0, 1.0, n_dst)
        a_dst = np.interp(xd, xs, a_src)
        return project_simplex_clip_renorm(a_dst)

    # --- Seeds ---
    def generate_seeds(num_random: int = 5) -> List[np.ndarray]:
        seeds: List[np.ndarray] = []

        # Uniform
        seeds.append(np.full(n, 1.0 / n))

        # Tent (triangular)
        x = np.linspace(-1.0, 1.0, n)
        tent = np.maximum(0.0, 1.0 - np.abs(x))
        tent /= tent.sum()
        seeds.append(tent)

        # Cosine-squared bump
        t = np.linspace(-np.pi / 2, np.pi / 2, n)
        cos2 = np.cos(t) ** 2
        cos2 /= cos2.sum()
        seeds.append(cos2)

        # Concave quadratic hump
        quad = np.maximum(0.0, 1.0 - x**2)
        quad /= quad.sum()
        seeds.append(quad)

        # Cosine^4 bump (sharper center but still smooth)
        cos4 = np.cos(t) ** 4
        cos4 /= cos4.sum()
        seeds.append(cos4)

        # Symmetric compact bump resembling support [-1/4, 1/4]
        xi = np.linspace(-0.25, 0.25, n)
        base = 1.0 + 4.0 * np.abs(xi) - 16.0 * xi**2
        base = (base + base[::-1]) / 2
        base = np.maximum(base, 0.0)
        base = project_simplex_clip_renorm(base)
        seeds.append(base)

        # Smoothed random seeds via Gamma + convolutional smoothing
        ker = np.array([1.0, 2.0, 3.0, 2.0, 1.0], dtype=float)
        for _ in range(num_random):
            r = rng.gamma(shape=0.6, scale=1.0, size=n)
            rr = np.convolve(r, ker, mode="same")
            rr = np.maximum(rr, 0.0)
            rr /= rr.sum()
            seeds.append(rr)

        return seeds

    # --- Peak selection ---
    def topk_indices_desc(b: np.ndarray, k: int) -> np.ndarray:
        k = min(k, b.size)
        if k <= 0:
            return np.array([], dtype=int)
        idx = np.argpartition(-b, kth=k - 1)[:k]
        idx = idx[np.argsort(-b[idx])]
        return idx

    def expand_with_neighbors(b: np.ndarray, idx: np.ndarray, rel_eps: float = 0.002) -> np.ndarray:
        """Expand active peak set by including ±1 neighbors if they are close in height."""
        if idx.size == 0:
            return idx
        bmax = b[idx[0]]
        sel = set(int(k) for k in idx.tolist())
        for k in idx:
            for nb in (k - 1, k + 1):
                if 0 <= nb < b.size and b[nb] >= bmax * (1.0 - rel_eps):
                    sel.add(int(nb))
        out = np.array(sorted(sel), dtype=int)
        return out

    def argmax_plateau_set(b: np.ndarray, rel_eps: float = 5e-5) -> np.ndarray:
        """Return the set of indices near the maximum of b within a tiny relative epsilon."""
        bmax = float(np.max(b))
        thr = bmax * (1.0 - rel_eps)
        S = np.where(b >= thr)[0]
        return S

    # --- Pressure and mirror-descent style reweighting ---
    def pressure_from_peaks(a: np.ndarray, K: List[int]) -> np.ndarray:
        """Compute pressure vector p_i = sum_{k in K} shift_reverse(a,k)[i]."""
        p = np.zeros_like(a)
        for kk in K:
            p += shift_reverse(a, int(kk))
        return p

    def mirror_inverse_pressure(a: np.ndarray, b: np.ndarray, K: List[int], step: float = 0.04) -> np.ndarray:
        """Mirror-descent style multiplicative reweighting by inverse pressure."""
        if len(K) == 0:
            return a
        p = pressure_from_peaks(a, K)
        # Reweight: a_i <- a_i * exp(-step * p_i) then renormalize.
        delta = -step * p
        delta = np.clip(delta, -5.0, 5.0)
        w = np.exp(delta)
        a_new = a * w
        a_new = project_simplex_clip_renorm(a_new)
        return a_new

    # --- Multi-peak pair shaving mass-transport ---
    def pair_shave(
        a: np.ndarray,
        b: np.ndarray,
        K: List[int],
        shave_frac: float = 0.002,
        target_pool: int = 24,
        peaks_to_shave: int = 2,
        max_pairs_per_peak: int = 24,
    ) -> np.ndarray:
        """Reduce mass on top-contributing pairs for the worst few peaks; redistribute to low-pressure indices.

        Multi-peak version: iterates over the top 'peaks_to_shave' lags in K and
        shaves a fraction (shave_frac / J) at each, aggregating the removed mass
        and redistributing it to low-pressure indices (computed from all K).
        Redistribution further biases toward indices far from the peak centers.
        """
        if len(K) == 0 or shave_frac <= 0:
            return a

        J = max(1, min(peaks_to_shave, len(K)))
        a_new = a.copy()
        removed_total = 0.0

        # Global pressure for redistribution from all provided K
        pressure = np.zeros_like(a)
        for kk in K:
            pressure += shift_reverse(a, int(kk))

        # Distance score: indices farther from centers k/2 get larger weight
        idxs = np.arange(n, dtype=float)
        dist_acc = np.zeros_like(a)
        for kk in K:
            center = 0.5 * kk
            dist_acc += np.abs(idxs - center)
        dist_acc = dist_acc / (np.max(dist_acc) + 1e-12)

        for kk in K[:J]:
            kstar = int(kk)
            i_lo = max(0, kstar - (n - 1))
            i_hi = min(n - 1, kstar)
            if i_hi < i_lo:
                continue

            i = np.arange(i_lo, i_hi + 1)
            j = kstar - i
            pair_contrib = a_new[i] * a_new[j]

            M = min(max_pairs_per_peak, i.size)
            # Select pairs by current contribution at this stage
            sel = np.argpartition(-pair_contrib, kth=min(M - 1, pair_contrib.size - 1))[:M]
            sel = sel[np.argsort(-pair_contrib[sel])]

            # Shave a small fraction symmetrically from both indices in the pair
            local_removed = 0.0
            for pidx in sel:
                ii = int(i[pidx])
                jj = int(j[pidx])
                frac = shave_frac / J
                di = min(frac * a_new[ii], a_new[ii])
                dj = min(frac * a_new[jj], a_new[jj])
                if di > 0:
                    a_new[ii] -= di
                    local_removed += di
                if dj > 0:
                    a_new[jj] -= dj
                    local_removed += dj
            removed_total += local_removed

        if removed_total <= 0:
            return a

        # Redistribution targets: lowest pressure indices
        tp = min(target_pool, n)
        target_idx = np.argpartition(pressure, kth=min(tp - 1, pressure.size - 1))[:tp]
        target_idx = np.unique(target_idx)
        if target_idx.size == 0:
            return a

        # Weight redistribution by inverse pressure and distance from centers
        inv_press = 1.0 / (1e-12 + pressure[target_idx])
        dist_weight = 0.5 + 0.5 * dist_acc[target_idx]  # in [0.5, 1]
        w = inv_press * dist_weight
        w_sum = w.sum()
        if w_sum <= 0:
            w = np.full_like(w, 1.0 / target_idx.size)
        else:
            w /= w_sum
        a_new[target_idx] += removed_total * w
        a_new = project_simplex_clip_renorm(a_new)
        return a_new

    # --- Symmetrization (acceptance-based) ---
    def symmetrize_accept(a: np.ndarray, val_current: float) -> Tuple[np.ndarray, float]:
        """Try symmetric averaging a <- (a + a[::-1]) / 2 and accept if improves."""
        a_sym = 0.5 * (a + a[::-1])
        a_sym = project_simplex_clip_renorm(a_sym)
        v_sym, _ = evaluate_objective(a_sym)
        if v_sym + 1e-12 < val_current:
            return a_sym, float(v_sym)
        return a, float(val_current)

    # --- Gear A: logits softmax + Top-K LSE with Adam ---
    def gearA_logits_optimization(a_init: np.ndarray, best_val_global: float) -> Tuple[np.ndarray, float]:
        """Run several τ phases of logits/soft surrogate optimization with plateau-band union and inverse-pressure tail."""
        # Initialize logits from a_init
        a_init = project_simplex_clip_renorm(a_init)
        w = to_logits_from_a(a_init)

        # Adam parameters
        lr_w = 0.06
        beta1, beta2 = 0.9, 0.999
        eps_adam = 1e-8
        m = np.zeros_like(w)
        v = np.zeros_like(w)
        t_adam = 0

        # τ schedule and Top-K schedule (sharpen gradually)
        tau_schedule = [0.42, 0.26, 0.16, 0.08, 0.04, 0.016]
        # Slightly larger K early to control more peaks
        K_schedule = [40, 28, 20, 14, 10, 6]
        max_iters_per_tau = 120

        # Regularization (decays across phases)
        tv0 = 8e-4
        ent0 = 5e-4

        # Base epsilon-tail weight (to be scaled per-plateau) and plateau epsilon schedule
        alpha_tail0 = 0.12  # base tail at highest τ; will decay by 0.5 per phase before plateau scaling
        plateau_eps_schedule = np.linspace(0.01, 5e-4, num=len(tau_schedule))

        # Shaving cadence and cutting-plane micro-steps per phase
        shave_every = 36
        mirror_every = 24  # inverse-pressure mirror reweighting cadence
        micro_cutting_steps = 16
        topM_cut = 16

        # Peak-switch LR stabilization state
        last_top_idx = None
        peak_switches = 0

        # Track best
        a = softmax_vec(w)
        val, b = evaluate_objective(a)
        best_a, best_val = a.copy(), float(val)
        no_improve_count = 0

        for phase, (tau, K) in enumerate(zip(tau_schedule, K_schedule)):
            if time.perf_counter() > deadline:
                break

            tv_coeff = tv0 * (0.6 ** phase)
            ent_coeff = ent0 * (0.5 ** phase)
            alpha_tail_base = alpha_tail0 * (0.5 ** phase)
            eps_plateau = float(plateau_eps_schedule[phase])

            for it in range(max_iters_per_tau):
                if time.perf_counter() > deadline:
                    break

                # Current a/b/val
                a = softmax_vec(w)
                val, b = evaluate_objective(a)

                if val + 1e-12 < best_val:
                    best_val = float(val)
                    best_a = a.copy()
                    no_improve_count = 0
                else:
                    no_improve_count += 1

                # Build active set S as union of Top‑K and plateau band P
                k_idx = topk_indices_desc(b, K)
                if k_idx.size == 0:
                    continue
                bmax = float(np.max(b))
                P = argmax_plateau_set(b, rel_eps=eps_plateau)
                S = np.unique(np.concatenate((k_idx, P)))

                # Track the identity of the current worst peak for LR stabilization
                top_now = int(k_idx[0])
                if last_top_idx is not None and top_now != last_top_idx:
                    peak_switches += 1
                last_top_idx = top_now

                # Softmax weights over active peaks S with temperature τ
                soft_w = softmax_temp(b[S], tau)

                # Inverse‑pressure epsilon‑tail over all lags, blended with uniform (50‑50)
                lam = 1e-12
                u_inv_all = 1.0 / (b + lam)
                u_inv_all = u_inv_all / np.sum(u_inv_all)
                u_uni_all = np.full_like(b, 1.0 / b.size)
                u_mix_all = 0.5 * u_inv_all + 0.5 * u_uni_all
                # Restrict tail to active set S and renormalize
                u_tail_S = u_mix_all[S]
                u_tail_S = u_tail_S / (np.sum(u_tail_S) + 1e-18)

                # Plateau-width scaled effective tail weight α_eff
                alpha_eff = alpha_tail_base * (1.0 + float(P.size) / float(b.size))
                alpha_eff = float(np.clip(alpha_eff, 0.02, 0.12))

                # Final surrogate weights over S
                p_k = (1.0 - alpha_eff) * soft_w + alpha_eff * u_tail_S

                # Gradient wrt a of surrogate: dL/da = 2 * sum_{k in S} p_k * shift_reverse(a,k)
                g_a = np.zeros_like(a)
                for wk, kk in zip(p_k, S):
                    g_a += wk * shift_reverse(a, int(kk))
                g_a *= 2.0

                # Participation-weighted entropy: derive pressure from top-R peaks by gap
                R = 1
                if k_idx.size >= 2 and b[k_idx[1]] > 0.996 * b[k_idx[0]]:
                    R = 2
                if k_idx.size >= 3 and b[k_idx[2]] > 0.994 * b[k_idx[0]]:
                    R = 3
                idx_R = k_idx[:R]
                part = pressure_from_peaks(a, [int(x) for x in idx_R]) if idx_R.size > 0 else np.zeros_like(a)
                part_norm = part / (np.max(part) + 1e-12)
                ent_weight = (0.5 + 0.5 * part_norm)  # weight in [0.5, 1] toward high-participation indices

                # Regularization in a-space
                if tv_coeff > 0:
                    g_a += tv_coeff * laplacian_1d(a)
                if ent_coeff > 0:
                    # Entropy gradient: d/da_j [sum a_j log a_j] = log a_j + 1, weighted by participation
                    g_a += ent_coeff * ent_weight * (np.log(a + 1e-12) + 1.0)

                # Chain rule to logits w: da/dw = diag(a) - a a^T, so g_w = a * (g_a - <g_a, a>)
                ga_dot_a = float(np.dot(g_a, a))
                g_w = a * (g_a - ga_dot_a)

                # Adam step on logits
                t_adam += 1
                m = beta1 * m + (1 - beta1) * g_w
                v = beta2 * v + (1 - beta2) * (g_w * g_w)
                m_hat = m / (1 - beta1**t_adam)
                v_hat = v / (1 - beta2**t_adam)
                w = w - lr_w * m_hat / (np.sqrt(v_hat) + eps_adam)

                # Peak-switch based LR stabilization check every 20 steps
                if (it + 1) % 20 == 0:
                    if peak_switches >= 6:
                        lr_w *= 0.8
                    else:
                        lr_w = min(lr_w * 1.02, 0.08)
                    peak_switches = 0  # reset counter

                # Occasionally try inverse-pressure mirror reweighting (accept only if true objective improves)
                if (it + 1) % mirror_every == 0:
                    a_curr = softmax_vec(w)
                    k_now = topk_indices_desc(b, max(4, K))
                    a_mirr = mirror_inverse_pressure(a_curr, b, list(k_now), step=0.035)
                    if not np.allclose(a_mirr, a_curr):
                        v_mirr, _ = evaluate_objective(a_mirr)
                        if v_mirr + 1e-12 < val:
                            w = to_logits_from_a(a_mirr)
                            val = v_mirr
                            if val + 1e-12 < best_val:
                                best_val = float(val)
                                best_a = a_mirr.copy()
                                no_improve_count = 0

                # Occasionally try multi-peak pair shaving (accept only if true objective improves)
                if (it + 1) % shave_every == 0:
                    k_now = topk_indices_desc(b, max(4, K))
                    # Keep neighbor expansion here for transport targeting robustness
                    k_now = expand_with_neighbors(b, k_now, rel_eps=0.0015)
                    a_curr = softmax_vec(w)
                    a_shaved = pair_shave(
                        a_curr,
                        b,
                        list(k_now),
                        shave_frac=0.002,
                        target_pool=24,
                        peaks_to_shave=2,
                        max_pairs_per_peak=24,
                    )
                    if not np.allclose(a_shaved, a_curr):
                        shaved_val, _ = evaluate_objective(a_shaved)
                        if shaved_val + 1e-12 < val:
                            # Accept shaving by resetting logits to the shaved a
                            w = to_logits_from_a(a_shaved)
                            val = shaved_val
                            if val + 1e-12 < best_val:
                                best_val = float(val)
                                best_a = a_shaved.copy()
                                no_improve_count = 0

                # Early prune unpromising seeds relative to known global best
                if best_val_global < np.inf and val > 1.012 * best_val_global and (it > 80 or phase > 1):
                    break

                # Mild stagnation handling: small noise to logits and tiny lr decay
                if no_improve_count > 60:
                    w += rng.standard_normal(n) * 1e-3
                    lr_w *= 0.95
                    no_improve_count = 0

            # After each τ phase, do a short cutting-plane refinement on logits:
            # Use union of Top‑M and plateau band, with inverse-pressure weights over that set.
            a = softmax_vec(w)
            val, b = evaluate_objective(a)
            k_act_top = topk_indices_desc(b, topM_cut)
            P = argmax_plateau_set(b, rel_eps=eps_plateau)
            S_cp = np.unique(np.concatenate((k_act_top, P)))
            if S_cp.size > 0:
                lam = 1e-12
                w_inv_all = 1.0 / (b + lam)
                w_inv_all = w_inv_all / np.sum(w_inv_all)
                w_inv_S = w_inv_all[S_cp]
                w_inv_S = w_inv_S / (np.sum(w_inv_S) + 1e-18)
                for _ in range(micro_cutting_steps):
                    if time.perf_counter() > deadline:
                        break
                    # Weighted average-of-selected-peaks gradient in a-space
                    g_a = np.zeros_like(a)
                    for wk, kk in zip(w_inv_S, S_cp):
                        g_a += wk * shift_reverse(a, int(kk))
                    g_a *= 2.0
                    # Tiny TV to keep smoothness
                    g_a += (tv0 * 0.2) * laplacian_1d(a)
                    ga_dot_a = float(np.dot(g_a, a))
                    g_w = a * (g_a - ga_dot_a)
                    # Small step
                    w = w - 0.02 * g_w
                    a = softmax_vec(w)
                    v_try, b_try = evaluate_objective(a)
                    if v_try + 1e-12 < val:
                        val, b = v_try, b_try
                        if val + 1e-12 < best_val:
                            best_val = float(val)
                            best_a = a.copy()

            # Try symmetry averaging (acceptance-based)
            a = softmax_vec(w)
            val, _ = evaluate_objective(a)
            a_sym, v_sym = symmetrize_accept(a, val)
            if v_sym + 1e-12 < val:
                w = to_logits_from_a(a_sym)
                if v_sym + 1e-12 < best_val:
                    best_val = float(v_sym)
                    best_a = a_sym.copy()

        return best_a, best_val

    # --- Gear B: projected subgradient on a (hard max focus) ---
    def gearB_projected_subgradient(a_init: np.ndarray, best_val_global: float) -> Tuple[np.ndarray, float]:
        a = project_simplex_clip_renorm(a_init)
        val, b = evaluate_objective(a)
        best_a, best_val = a.copy(), float(val)

        # Hyperparameters
        eta = 0.6
        eta_min = 1e-4
        momentum = 0.7
        v_mom = np.zeros_like(a)
        iters = 420
        shave_every = 35
        mirror_every = 28
        tv_coeff = 2e-4  # tiny smoothing (adaptive below)
        topM = 8  # slightly larger set to control plateau

        stagnation = 0

        for it in range(iters):
            if time.perf_counter() > deadline:
                break

            val, b = evaluate_objective(a)
            if val + 1e-12 < best_val:
                best_val = float(val)
                best_a = a.copy()
                stagnation = 0
            else:
                stagnation += 1

            # Active set S: combine topM and argmax plateau to prevent peak swapping
            k_idx = topk_indices_desc(b, topM)
            plateau = argmax_plateau_set(b, rel_eps=5e-5)
            k_act = np.unique(np.concatenate((k_idx, plateau)))
            k_act = expand_with_neighbors(b, k_act, rel_eps=0.0015)

            # Subgradient of average of active peaks (uniform over this union set)
            g = np.zeros_like(a)
            if k_act.size > 0:
                for kk in k_act:
                    g += shift_reverse(a, int(kk))
                g *= (2.0 / k_act.size)

            # Adaptive TV: only increase when spikes appear
            if (it + 1) % 25 == 0:
                am = float(np.max(a))
                med = float(np.median(a))
                ratio = am / (med + 1e-12)
                tv_coeff = 3e-4 if ratio > 30.0 else 1e-4

            if tv_coeff > 0:
                g += tv_coeff * laplacian_1d(a)

            # Momentum and backtracking projected step
            g_step = g + momentum * v_mom
            v_mom = g_step.copy()  # simple heavy-ball style

            accepted = False
            eta_local = eta
            curr_val = val
            for _ in range(7):
                a_cand = project_simplex_exact(a - eta_local * g_step)
                new_val, _ = evaluate_objective(a_cand)
                if new_val + 1e-12 < curr_val:
                    a = a_cand
                    val = new_val
                    eta = min(eta_local * 1.05, 1.0)
                    accepted = True
                    break
                else:
                    eta_local *= 0.5
                    if eta_local < eta_min:
                        break

            if not accepted:
                eta = max(eta * 0.7, eta_min)

            # Periodic inverse-pressure mirror reweighting (acceptance-only)
            if (it + 1) % mirror_every == 0:
                k_now = topk_indices_desc(b, max(4, topM))
                a_mirr = mirror_inverse_pressure(a, b, list(k_now), step=0.03)
                if not np.allclose(a_mirr, a):
                    v_mirr, _ = evaluate_objective(a_mirr)
                    if v_mirr + 1e-12 < val:
                        a = a_mirr
                        val = v_mirr
                        eta = min(eta * 1.05, 1.0)

            # Periodic multi-peak pair shaving
            if (it + 1) % shave_every == 0:
                k_idx_now = topk_indices_desc(b, max(3, topM))
                a_shaved = pair_shave(
                    a,
                    b,
                    list(k_idx_now),
                    shave_frac=0.002,
                    target_pool=24,
                    peaks_to_shave=2,
                    max_pairs_per_peak=24,
                )
                if not np.allclose(a_shaved, a):
                    shaved_val, _ = evaluate_objective(a_shaved)
                    if shaved_val + 1e-12 < val:
                        a = a_shaved
                        val = shaved_val
                        eta = min(eta * 1.1, 1.0)

            # Cutting-plane micro refinement intermittently: weighted by inverse-pressure over union set
            if (it + 1) % 70 == 0:
                # Build union set of topM and a tiny plateau band
                k_top = topk_indices_desc(b, topM)
                P = argmax_plateau_set(b, rel_eps=5e-5)
                S_cp = np.unique(np.concatenate((k_top, P)))
                if S_cp.size > 0:
                    lam = 1e-12
                    w_inv_all = 1.0 / (b + lam)
                    w_inv_all = w_inv_all / np.sum(w_inv_all)
                    w_inv_S = w_inv_all[S_cp]
                    w_inv_S = w_inv_S / (np.sum(w_inv_S) + 1e-18)
                    for _ in range(6):
                        if time.perf_counter() > deadline:
                            break
                        g_cp = np.zeros_like(a)
                        for wk, kk in zip(w_inv_S, S_cp):
                            g_cp += wk * shift_reverse(a, int(kk))
                        g_cp *= 2.0
                        a_try = project_simplex_exact(a - 0.25 * eta * g_cp)
                        v_try, _ = evaluate_objective(a_try)
                        if v_try + 1e-12 < val:
                            a = a_try
                            val = v_try
                            if val + 1e-12 < best_val:
                                best_val = float(val)
                                best_a = a.copy()

            # Symmetry averaging attempt
            if (it + 1) % 90 == 0:
                a_sym, v_sym = symmetrize_accept(a, val)
                if v_sym + 1e-12 < val:
                    a = a_sym
                    val = v_sym
                    if val + 1e-12 < best_val:
                        best_val = float(val)
                        best_a = a.copy()

            # Early prune if clearly worse than global best
            if best_val_global < np.inf and val > 1.01 * best_val_global and it > 120:
                break

            # Mild randomization if stagnating
            if stagnation > 80:
                a = project_simplex_clip_renorm(a + rng.standard_normal(n) * 5e-5)
                stagnation = 0

        return best_a, best_val

    # --- Optional: seed preconditioning with single-pass inverse-pressure shaving ---
    def pre_shave_seed(a_seed: np.ndarray) -> np.ndarray:
        a0 = project_simplex_clip_renorm(a_seed)
        val0, b0 = evaluate_objective(a0)
        # Build peak set: top few lags with neighbors
        k0 = topk_indices_desc(b0, 6)
        k0 = expand_with_neighbors(b0, k0, rel_eps=0.0015)
        if k0.size == 0:
            return a0
        # Try two shave strengths with backtracking acceptance
        for frac in (0.01, 0.005):
            a_try = pair_shave(a0, b0, list(k0), shave_frac=frac, target_pool=28, peaks_to_shave=2, max_pairs_per_peak=28)
            v_try, _ = evaluate_objective(a_try)
            if v_try + 1e-12 < val0:
                return a_try
        return a0

    # --- Cross-resolution shaping (short, acceptance-based, time-limited) ---
    def cross_resolution_refine(a_curr: np.ndarray, best_val_now: float) -> Tuple[np.ndarray, float]:
        """Briefly refine at resolutions in {512, 960}, resample back to n=600. Acceptance-tested."""
        if time.perf_counter() > deadline - 4.0:
            return a_curr, best_val_now

        def short_logits_pass(a_init_small: np.ndarray) -> np.ndarray:
            """A very short logits Top-K LSE pass at the small resolution."""
            m = a_init_small.size
            fft_len_m = next_pow_two(2 * m - 1)

            def conv_m(a: np.ndarray) -> np.ndarray:
                fa = np.fft.rfft(a, n=fft_len_m)
                c = np.fft.irfft(fa * fa, n=fft_len_m)
                return c[: 2 * m - 1]

            def eval_m(a: np.ndarray) -> Tuple[float, np.ndarray]:
                b = conv_m(a)
                return float(np.max(b)), b  # scale irrelevant internally; we minimize max(b)

            def shift_rev_m(a: np.ndarray, k: int) -> np.ndarray:
                s = np.zeros_like(a)
                lo = max(0, k - (m - 1))
                hi = min(m - 1, k)
                if hi >= lo:
                    s[lo : hi + 1] = a[(k - hi) : (k - lo + 1)][::-1]
                return s

            w = to_logits_from_a(project_simplex_clip_renorm(a_init_small))
            # tiny schedule
            tau_sched = [0.30, 0.10]
            K_sched = [max(4, m // 64), max(4, m // 96)]
            lr = 0.05
            m1 = np.zeros_like(w)
            m2 = np.zeros_like(w)
            tstep = 0
            for tau, K in zip(tau_sched, K_sched):
                for _ in range(40):
                    if time.perf_counter() > deadline - 1.0:
                        break
                    a = softmax_vec(w)
                    vmax, b = eval_m(a)
                    idx = topk_indices_desc(b, int(K))
                    if idx.size == 0:
                        break
                    idx = expand_with_neighbors(b, idx, rel_eps=0.002)
                    pk = softmax_temp(b[idx], tau)
                    pk = 0.9 * pk + 0.1 * np.full_like(pk, 1.0 / len(pk))
                    g_a = np.zeros_like(a)
                    for wk, kk in zip(pk, idx):
                        g_a += wk * shift_rev_m(a, int(kk))
                    g_a *= 2.0
                    # tiny smoothing
                    g_a += 4e-4 * laplacian_1d(a)
                    dot = float(np.dot(g_a, a))
                    g_w = a * (g_a - dot)
                    tstep += 1
                    m1 = 0.9 * m1 + 0.1 * g_w
                    m2 = 0.999 * m2 + 0.001 * (g_w * g_w)
                    w = w - lr * m1 / (np.sqrt(m2) + 1e-8)
            return softmax_vec(w)

        a_best = a_curr.copy()
        v_best = float(best_val_now)
        for m in (512, 960):
            if time.perf_counter() > deadline - 2.0:
                break
            # Down/up sample
            a_small = resample_to_length(a_curr, m)
            a_small = short_logits_pass(a_small)
            # back to n
            a_res = resample_to_length(a_small, n)
            a_res = project_simplex_clip_renorm(a_res)
            v_res, _ = evaluate_objective(a_res)
            if v_res + 1e-12 < v_best:
                a_best = a_res
                v_best = float(v_res)
        return a_best, v_best

    # --- Orchestration: multi-start and two-gear pipeline ---
    seeds = generate_seeds(num_random=6)

    # Precondition selected seeds via single-pass inverse-pressure shaving
    pre_idx = [0, 1, 2, 5]  # indices of seeds to pre-shave: uniform, tent, cos2, compact bump
    for idx in pre_idx:
        if 0 <= idx < len(seeds):
            seeds[idx] = pre_shave_seed(seeds[idx])

    # Track global best
    global_best_a = None
    global_best_val = np.inf

    # Iterate seeds deterministically
    for si, a0 in enumerate(seeds):
        if time.perf_counter() > deadline:
            break
        # Tiny symmetry-breaking noise
        a0 = project_simplex_clip_renorm(a0 + rng.standard_normal(n) * 1e-5)

        # Gear A
        aA, vA = gearA_logits_optimization(a0, global_best_val)

        # Gear B starting from Gear A best
        aB, vB = gearB_projected_subgradient(aA, min(global_best_val, vA))

        cand_a, cand_val = (aB, vB) if vB + 1e-12 < vA else (aA, vA)

        # Optional cross-resolution shaping on the better candidate (cheap and short)
        cand_a, cand_val = cross_resolution_refine(cand_a, cand_val)

        if cand_val + 1e-12 < global_best_val:
            global_best_val = float(cand_val)
            global_best_a = cand_a.copy()

    # Safety fallback
    if global_best_a is None:
        global_best_a = np.full(n, 1.0 / n)

    # Final micro-polish: a few low-τ LSE steps then mirror + pair shave nudge, plus tiny uniform mixing trial
    if time.perf_counter() < deadline - 1.0:
        a = project_simplex_clip_renorm(global_best_a)
        for _ in range(20):
            if time.perf_counter() > deadline:
                break
            val, b = evaluate_objective(a)
            k_idx = topk_indices_desc(b, 6)
            if k_idx.size == 0:
                break
            p_k = softmax_temp(b[k_idx], tau=0.015)
            # Use a small epsilon-tail in micro-polish as uniform for stability
            p_k = 0.9 * p_k + 0.1 * np.full_like(p_k, 1.0 / len(p_k))
            g_a = np.zeros_like(a)
            for wk, kk in zip(p_k, k_idx):
                g_a += wk * shift_reverse(a, int(kk))
            g_a *= 2.0
            # a-step with exact simplex projection
            a_try = project_simplex_exact(a - 0.08 * g_a)
            v_try, _ = evaluate_objective(a_try)
            if v_try + 1e-12 < val:
                a = a_try
                if v_try + 1e-12 < global_best_val:
                    global_best_val = float(v_try)
                    global_best_a = a.copy()
        # Tiny inverse-pressure mirror step
        val, b = evaluate_objective(global_best_a)
        k_idx = topk_indices_desc(b, 6)
        a_mirr = mirror_inverse_pressure(global_best_a, b, list(k_idx), step=0.02)
        v_try, _ = evaluate_objective(a_mirr)
        if v_try + 1e-12 < global_best_val:
            global_best_val = float(v_try)
            global_best_a = a_mirr.copy()
        # Tiny pair shave
        val, b = evaluate_objective(global_best_a)
        k_idx = topk_indices_desc(b, 6)
        a_shaved = pair_shave(global_best_a, b, list(k_idx), shave_frac=0.0015, target_pool=20, peaks_to_shave=2)
        v_try, _ = evaluate_objective(a_shaved)
        if v_try + 1e-12 < global_best_val:
            global_best_val = float(v_try)
            global_best_a = a_shaved.copy()
        # Tiny uniform mixing to flatten residual plateau
        a_uni = np.full(n, 1.0 / n)
        for mix in (0.005, 0.01):
            a_mix = project_simplex_clip_renorm((1.0 - mix) * global_best_a + mix * a_uni)
            v_mix, _ = evaluate_objective(a_mix)
            if v_mix + 1e-12 < global_best_val:
                global_best_val = float(v_mix)
                global_best_a = a_mix.copy()

    # Final normalization and return
    global_best_a = project_simplex_clip_renorm(global_best_a)
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
    return list(search_for_best_sequence())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```

```python
#!/usr/bin/env python3
"""Optimized step-function search for minimizing 2 n max(conv(a,a)) / (sum(a)^2).

Implements an active-top-K, plateau-focused smooth minimax with FFT acceleration,
interleaved with hard-max polishing and transport updates.

The search maintains feasibility by working on the simplex via softmax logits.
It continuously keeps the current best feasible sequence and returns it in time.
"""

import json
import math
import os
import time
from typing import Tuple

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """
    Run an adaptive search on the simplex a >= 0, sum(a) = 1 to minimize:
        F(a) = 2 * n * max(conv(a, a)) / (sum(a)^2)
    With simplex enforcement, sum(a) = 1, so F(a) = 2 * n * max(conv(a, a)).

    Returns:
        numpy.ndarray: best found nonnegative sequence summing to 1 (length n=600).
    """
    # Problem dimension and time budget
    n = 600
    # Use environment override if provided; otherwise default to a safe budget.
    # Cap to 980 seconds to leave a safety margin below the 1000-second hard limit.
    max_seconds_env = float(os.environ.get("SEARCH_SECONDS", "30"))
    max_seconds = float(min(980.0, max_seconds_env))
    t_start = time.time()
    deadline = t_start + max_seconds

    # FFT helpers -------------------------------------------------------------
    class FFTCache:
        def __init__(self):
            self.last_nfft = None
            self.last_len = None

    fft_cache = FFTCache()

    def next_pow2(x: int) -> int:
        return 1 << (x - 1).bit_length()

    def conv_full(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """
        Full linear convolution using real FFT.
        Length = len(a) + len(b) - 1.
        """
        la, lb = len(a), len(b)
        L = la + lb - 1
        nfft = next_pow2(L)
        fa = np.fft.rfft(a, nfft)
        fb = np.fft.rfft(b, nfft)
        c = np.fft.irfft(fa * fb, nfft)[:L]
        return c

    def autocorr_full(a: np.ndarray) -> np.ndarray:
        """
        Autoconvolution b = conv(a, a), length 2n-1.
        """
        return conv_full(a, a)

    def corr_w_a(w: np.ndarray, a: np.ndarray) -> np.ndarray:
        """
        Compute c[j] = sum_m w[m + j] * a[m] for j = 0..n-1,
        i.e., correlation between w (length Lw) and a (length n).
        Implemented via convolution: conv(rev(w), a) and an index mapping.
        """
        Lw = len(w)
        wr = w[::-1]
        d = conv_full(wr, a)  # length = Lw + n - 1
        # c[j] = d[Lw - 1 - j]
        idx = (Lw - 1) - np.arange(len(a))
        return d[idx]

    # Objective, evaluation, and guards --------------------------------------
    def true_objective(a: np.ndarray) -> float:
        # On simplex sum(a)=1, objective reduces to 2 * n * max(b)
        b = autocorr_full(a)
        return float(2.0 * n * float(np.max(b)))

    def ensure_simplex_from_logits(s: np.ndarray) -> np.ndarray:
        # Stable softmax
        s_max = np.max(s)
        exps = np.exp(s - s_max)
        a = exps / np.sum(exps)
        return a

    # Seeds -------------------------------------------------------------------
    def seeds_collection(n: int) -> list[np.ndarray]:
        center = (n - 1) / 2.0
        x = np.linspace(-1.0, 1.0, n)
        seeds = []

        # 1) Baseline quadratic-ish symmetric shape (from provided baseline)
        x_bl = np.linspace(-0.25, 0.25, n)
        a_bl = 1.0 + 4.0 * np.abs(x_bl) - 16.0 * (x_bl**2)
        a_bl = (a_bl + a_bl[::-1]) / 2.0
        a_bl = np.maximum(a_bl, 0)
        if a_bl.sum() > 0:
            a_bl = a_bl / a_bl.sum()
            seeds.append(a_bl)

        # 2) Uniform
        a_u = np.ones(n) / n
        seeds.append(a_u)

        # 3) Raised cosine
        a_rc = 0.5 * (1.0 + np.cos(np.pi * x))
        a_rc = a_rc / a_rc.sum()
        seeds.append(a_rc)

        # 4) Gaussian with moderate width
        sigma = 0.35 * n
        g = np.exp(-((np.arange(n) - center) ** 2) / (2 * (sigma ** 2)))
        g = g / g.sum()
        seeds.append(g)

        # 5) Triangular (tent) centered
        tri = 1.0 - np.abs(np.linspace(-1, 1, n))
        tri = tri / tri.sum()
        seeds.append(tri)

        # 6) Edge-tilted (to explore non-unimodal)
        ramp = np.linspace(0.1, 1.0, n)
        ramp = (ramp + ramp[::-1]) / 2.0  # keep symmetry
        ramp /= ramp.sum()
        seeds.append(ramp)

        return seeds

    # Smooth surrogate loss/grad ---------------------------------------------
    def surrogate_weights(b: np.ndarray,
                          frac_tau: float,
                          eps_plateau: float,
                          top_k: int,
                          alpha_tail: float) -> np.ndarray:
        """
        Build weights over lags (length 2n-1) focusing on active top-K and plateau,
        with a small tail mixing uniform and inverse-pressure.
        Returns a probability vector w (sum to 1).
        """
        L = len(b)
        bmax = float(b.max())
        # Get top-K indices
        if top_k >= L:
            idx_top = np.arange(L)
        else:
            # argpartition is O(L)
            idx_top = np.argpartition(b, -top_k)[-top_k:]
        # Plateau indices where b >= bmax*(1 - eps)
        plateau_mask = b >= (bmax * (1.0 - eps_plateau))
        idx_plateau = np.nonzero(plateau_mask)[0]
        # Active set = union
        S = np.unique(np.concatenate([idx_top, idx_plateau]))
        # Softmax on active set with temperature τ
        tau = max(1e-12, frac_tau * max(1e-12, bmax))
        # stabilize by subtracting bmax
        logits = (b[S] - bmax) / tau
        logits = logits - logits.max()
        exp_logits = np.exp(logits)
        wS = exp_logits / max(1e-16, np.sum(exp_logits))

        # Tail: mixture of uniform and inverse-pressure
        lam = 1e-6 * bmax + 1e-12
        invp = 1.0 / (b + lam)
        invp /= invp.sum()
        unif = np.full(L, 1.0 / L)

        tail = 0.5 * unif + 0.5 * invp

        w = np.zeros(L)
        # Combine
        w[S] += (1.0 - alpha_tail) * wS
        w += alpha_tail * tail
        # Normalize to sum 1
        s = w.sum()
        if not np.isfinite(s) or s <= 0:
            # Fallback to uniform if degenerate
            w = np.full(L, 1.0 / L)
        else:
            w /= s
        return w

    def lse_grad(a: np.ndarray,
                 iter_frac: float,
                 stage_params: dict) -> Tuple[float, np.ndarray]:
        """
        Compute surrogate objective value (for monitoring only) and gradient wrt a.
        Returns (loss_value, grad_a).
        """
        b = autocorr_full(a)
        bmax = float(b.max())
        # Stage parameters
        frac_tau = stage_params['frac_tau_start'] * (stage_params['frac_tau_decay'] ** stage_params['iter'])
        frac_tau = max(stage_params['frac_tau_min'], frac_tau)

        eps_plateau = stage_params['eps_plateau_start'] * (stage_params['eps_plateau_decay'] ** stage_params['iter'])
        eps_plateau = max(stage_params['eps_plateau_min'], eps_plateau)

        top_k = stage_params['top_k']

        # increase tail when many lags nearly tie (early); reduce later
        alpha_tail = stage_params['alpha_tail_start'] * (stage_params['alpha_tail_decay'] ** stage_params['iter'])
        alpha_tail = max(stage_params['alpha_tail_min'], alpha_tail)

        w = surrogate_weights(b, frac_tau, eps_plateau, top_k, alpha_tail)
        # Effective loss ~ 2n * sum w[k] * b[k]
        # (not the exact LSE loss; we only need a smooth surrogate to guide updates)
        loss = float(2.0 * n * float(np.dot(w, b)))

        # Gradient wrt a: d/d a = 2 * corr(w, a) scaled by 2n
        grad_corr = 2.0 * corr_w_a(w, a)
        grad_a = 2.0 * n * grad_corr

        # Regularization (early phase): TV and entropy tilt to avoid spikiness
        beta_tv = stage_params['beta_tv']
        beta_ent = stage_params['beta_ent']
        if beta_tv > 0.0:
            # TV^2: sum (a[i] - a[i-1])^2
            da = np.zeros_like(a)
            # internal points
            da[1:-1] += 2.0 * (2.0 * a[1:-1] - a[:-2] - a[2:])
            # boundaries
            da[0] += 2.0 * (a[0] - a[1])
            da[-1] += 2.0 * (a[-1] - a[-2])
            grad_a += beta_tv * da
            loss += float(beta_tv * np.sum((np.diff(a)) ** 2))
        if beta_ent > 0.0:
            eps = 1e-12
            grad_a += -beta_ent / (a + eps)
            loss += float(-beta_ent * np.sum(np.log(a + eps)))

        return loss, grad_a

    # Hard-max polishing step -------------------------------------------------
    def hardmax_polish(a: np.ndarray, steps: int = 3, top_r: int = 2, init_lr: float = 0.5) -> Tuple[np.ndarray, float]:
        """
        Use subgradient of the average of top-r max lags to take small projected steps.
        Accept only if the true objective improves.
        """
        best_a = a.copy()
        best_F = true_objective(best_a)
        lr = init_lr
        for _ in range(steps):
            b = autocorr_full(best_a)
            idx_sorted = np.argpartition(b, -top_r)[-top_r:]
            w = np.zeros_like(b)
            w[idx_sorted] = 1.0 / top_r
            grad_a = 2.0 * corr_w_a(w, best_a) * 2.0 * n  # scale consistent with objective
            # Convert to logits gradient via softmax Jacobian
            s = np.log(best_a + 1e-12)
            a_now = ensure_simplex_from_logits(s)  # keep normalized
            g = grad_a
            ag = float(np.dot(a_now, g))
            grad_s = a_now * (g - ag)
            # Line-search on logits
            improved = False
            inner_lr = lr
            for _bt in range(12):
                s_new = s - inner_lr * grad_s
                a_new = ensure_simplex_from_logits(s_new)
                F_new = true_objective(a_new)
                if F_new + 1e-14 < best_F:
                    best_a = a_new
                    best_F = F_new
                    improved = True
                    break
                inner_lr *= 0.5
            if not improved:
                lr *= 0.5  # shrink trust region for next outer step
        return best_a, best_F

    # Transport step ----------------------------------------------------------
    def transport_shave(a: np.ndarray,
                        b: np.ndarray,
                        total_delta: float,
                        top_pairs_q: int = 16,
                        top_r_lags: int = 1) -> Tuple[np.ndarray, bool]:
        """
        Heuristic mass transport: identify top contributing index-pairs to worst lags and
        move a small total mass from those indices to low-influence indices.
        Returns (new_a, accepted)
        """
        L = len(b)
        # Worst lags
        idx_sorted = np.argpartition(b, -top_r_lags)[-top_r_lags:]
        # Compute pair contributions for each i (summed across worst lags)
        n_local = len(a)
        contrib = np.zeros(n_local)
        for k in idx_sorted:
            # For full conv indices: b[k] = sum_i a[i] a[k - i]
            # Valid i such that 0 <= i < n and 0 <= k - i < n:
            i_min = max(0, k - (n_local - 1))
            i_max = min(n_local - 1, k)
            # Sum contributions
            i_idx = np.arange(i_min, i_max + 1)
            j_idx = k - i_idx
            contrib[i_idx] += a[i_idx] * a[j_idx]

        # Sources: indices with highest contrib (limited by available mass)
        if top_pairs_q <= 0:
            return a.copy(), False
        src_idx = np.argpartition(contrib, -top_pairs_q)[-top_pairs_q:]
        # Targets: indices with lowest contrib (and possibly low a to avoid immediate spike)
        target_metric = contrib + 0.1 * a  # prefer low contrib and moderately low a
        dst_idx = np.argpartition(target_metric, top_pairs_q)[:top_pairs_q]

        # Compute per-source removable mass (fraction of a[i])
        a_new = a.copy()
        total_removed = 0.0
        removable = np.minimum(a_new[src_idx], total_delta / top_pairs_q)
        # Remove from sources
        for i, rm in zip(src_idx, removable):
            a_new[i] = max(0.0, a_new[i] - rm)
            total_removed += (a[i] - a_new[i])

        if total_removed <= 0:
            return a.copy(), False

        # Distribute to targets proportionally to (1 - a) to avoid saturation
        cap = (1.0 - a_new[dst_idx])
        weights = cap + 1e-12
        weights /= weights.sum()
        add = total_removed * weights
        a_new[dst_idx] += add

        # Renormalize due to numerical rounding (should still be near 1)
        s = a_new.sum()
        if s > 0:
            a_new /= s
        else:
            return a.copy(), False

        return a_new, True

    # Optimizer (Adam on logits) ---------------------------------------------
    class Adam:
        def __init__(self, lr=0.1, b1=0.9, b2=0.999, eps=1e-8):
            self.lr = lr
            self.b1 = b1
            self.b2 = b2
            self.eps = eps
            self.m = None
            self.v = None
            self.t = 0

        def step(self, params: np.ndarray, grad: np.ndarray) -> np.ndarray:
            if self.m is None:
                self.m = np.zeros_like(grad)
                self.v = np.zeros_like(grad)
                self.t = 0
            self.t += 1
            self.m = self.b1 * self.m + (1 - self.b1) * grad
            self.v = self.b2 * self.v + (1 - self.b2) * (grad * grad)
            m_hat = self.m / (1 - self.b1 ** self.t)
            v_hat = self.v / (1 - self.b2 ** self.t)
            update = self.lr * m_hat / (np.sqrt(v_hat) + self.eps)
            return params - update

        def set_lr(self, lr: float):
            self.lr = lr

    # Core single-seed optimization ------------------------------------------
    def optimize_seed(a0: np.ndarray, seed_time_budget: float) -> Tuple[np.ndarray, float]:
        nonlocal fft_cache
        # Initialize logits
        s = np.log(a0 + 1e-12)
        a = ensure_simplex_from_logits(s)
        best_a = a.copy()
        best_F = true_objective(a)

        # Optimizer & schedules
        adam = Adam(lr=0.15, b1=0.9, b2=0.999, eps=1e-8)
        max_iters = 20000  # iteration cap; time budget will stop earlier
        check_every = 20
        polish_every = 200
        transport_every = 250
        symmetry_mix_every = 50

        # Schedules and parameters for surrogate and regularization
        stage_params = {
            'iter': 0,
            'frac_tau_start': 0.02,
            'frac_tau_decay': 0.9975,
            'frac_tau_min': 1e-4,
            'eps_plateau_start': 5e-3,
            'eps_plateau_decay': 0.998,
            'eps_plateau_min': 5e-4,
            'alpha_tail_start': 0.10,
            'alpha_tail_decay': 0.9985,
            'alpha_tail_min': 0.01,
            'top_k': 48,
            'beta_tv': 2e-3,      # will decay during optimization
            'beta_ent': 5e-4,     # will decay during optimization
        }

        no_improve_count = 0
        last_improve_iter = 0
        last_eval_F = best_F

        t0 = time.time()
        for it in range(1, max_iters + 1):
            now = time.time()
            if now >= deadline or now - t0 >= seed_time_budget:
                break

            stage_params['iter'] = it
            # Decay regularizers
            stage_params['beta_tv'] *= 0.9995
            stage_params['beta_ent'] *= 0.9995

            # Surrogate gradient step
            iter_frac = it / max_iters
            loss, grad_a = lse_grad(a, iter_frac, stage_params)

            # Convert to logits gradient: grad_s = a * (grad_a - <grad_a, a>)
            ag = float(np.dot(a, grad_a))
            grad_s = a * (grad_a - ag)

            # Gradient clipping (to stabilize)
            gnorm = float(np.linalg.norm(grad_s))
            if not np.isfinite(gnorm) or gnorm > 100.0:
                scale = 100.0 / (gnorm + 1e-12)
                grad_s *= scale

            # Adam step
            s = adam.step(s, grad_s)
            a = ensure_simplex_from_logits(s)

            # Small symmetry mixing early on
            if it % symmetry_mix_every == 0:
                gamma = max(0.0, 0.1 * (1.0 - it / 2000.0))
                if gamma > 0:
                    a = (1.0 - gamma) * a + gamma * a[::-1]
                    a /= a.sum()
                    s = np.log(a + 1e-12)

            # Periodic evaluation and bookkeeping
            if it % check_every == 0:
                F = true_objective(a)
                if F + 1e-12 < best_F:
                    best_F = F
                    best_a = a.copy()
                    last_improve_iter = it
                    no_improve_count = 0
                else:
                    no_improve_count += 1
                last_eval_F = F

            # Hard-max polishing
            if it % polish_every == 0:
                a_polish, F_polish = hardmax_polish(a, steps=2, top_r=2, init_lr=0.2)
                if F_polish + 1e-12 < last_eval_F:
                    a = a_polish
                    s = np.log(a + 1e-12)
                    last_eval_F = F_polish
                    if F_polish + 1e-12 < best_F:
                        best_F = F_polish
                        best_a = a.copy()
                        last_improve_iter = it
                        no_improve_count = 0

            # Transport mass if stagnating
            if it % transport_every == 0 and no_improve_count >= (transport_every // check_every):
                b = autocorr_full(a)
                a_try = a.copy()
                delta = 5e-3  # total mass to move
                accepted_any = False
                # Backtrack on delta if no improvement
                for _ in range(5):
                    a_new, ok = transport_shave(a_try, b, total_delta=delta, top_pairs_q=24, top_r_lags=2)
                    if not ok:
                        break
                    F_new = true_objective(a_new)
                    if F_new + 1e-12 < last_eval_F:
                        a = a_new
                        s = np.log(a + 1e-12)
                        last_eval_F = F_new
                        accepted_any = True
                        if F_new + 1e-12 < best_F:
                            best_F = F_new
                            best_a = a.copy()
                            last_improve_iter = it
                            no_improve_count = 0
                        break
                    delta *= 0.5
                if not accepted_any:
                    # Reduce learning rate to stabilize
                    adam.set_lr(max(0.02, adam.lr * 0.7))

            # Occasional learning rate decay if long stagnation
            if no_improve_count > 25:
                adam.set_lr(max(0.02, adam.lr * 0.9))
                no_improve_count = 0

        return best_a, best_F

    # Multi-start orchestration ----------------------------------------------
    all_seeds = seeds_collection(n)
    # Also include slight perturbations of baseline to escape symmetry traps
    if len(all_seeds) > 0:
        base = all_seeds[0]
        rng = np.random.default_rng(123)
        for _ in range(2):
            noise = rng.normal(0, 1e-3, size=n)
            pert = np.clip(base + noise, 0, None)
            pert = pert / pert.sum()
            all_seeds.append(pert)

    # Time allocation per seed
    n_seeds = len(all_seeds)
    # Reserve 5% time margin
    time_left = max(0.0, deadline - time.time())
    if n_seeds <= 0 or time_left <= 0.1:
        # Fallback to uniform
        return np.ones(n) / n

    # Bias more time to first few seeds (baseline-like) as they tend to do better
    seed_weights = np.array([3.0, 2.0, 1.5] + [1.0] * (n_seeds - 3))
    seed_weights = seed_weights[:n_seeds]
    seed_weights = np.maximum(seed_weights, 0.1)
    seed_weights /= seed_weights.sum()
    # Use 95% of available time for optimization, keep 5% as buffer
    global_budget = 0.95 * time_left
    per_seed_budgets = global_budget * seed_weights

    global_best_a = None
    global_best_F = float('inf')

    for i, seed in enumerate(all_seeds):
        if time.time() >= deadline:
            break
        a_best_seed, F_best_seed = optimize_seed(seed, seed_time_budget=float(per_seed_budgets[i]))
        if F_best_seed + 1e-12 < global_best_F:
            global_best_F = F_best_seed
            global_best_a = a_best_seed

    # Final safeguard: if nothing improved, return a reasonable candidate
    if global_best_a is None:
        # Return baseline seed as safe fallback
        x_bl = np.linspace(-0.25, 0.25, n)
        a_bl = 1.0 + 4.0 * np.abs(x_bl) - 16.0 * (x_bl ** 2)
        a_bl = (a_bl + a_bl[::-1]) / 2.0
        a_bl = np.maximum(a_bl, 0)
        if a_bl.sum() > 0:
            a_bl = a_bl / a_bl.sum()
            global_best_a = a_bl
        else:
            global_best_a = np.ones(n) / n

    # Ensure nonnegativity and normalization
    global_best_a = np.clip(global_best_a, 0.0, None)
    ssum = global_best_a.sum()
    if ssum <= 0:
        global_best_a = np.ones(n) / n
    else:
        global_best_a /= ssum

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
    return list(search_for_best_sequence())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```

```python
#!/usr/bin/env python3
"""Search for a 600-step nonnegative sequence that minimizes the autocorrelation-based upper bound.

Implements a plateau-aware smooth surrogate descent with FFT-accelerated exact objective evaluation,
interleaved with acceptance-tested non-smooth moves (hard-max polish and pair-shaving mass transport).
"""

import json
import math
import time
from typing import Tuple, List

import numpy as np


# EVOLVE_START
def softmax(x: np.ndarray) -> np.ndarray:
    """Numerically stable softmax."""
    m = np.max(x)
    ex = np.exp(x - m)
    s = ex.sum()
    if s == 0.0 or not np.isfinite(s):
        # Safe fallback in extreme cases
        ex = np.exp(np.clip(x - m, -50.0, 50.0))
        s = ex.sum()
        if s == 0.0:
            return np.full_like(x, 1.0 / len(x))
    return ex / s


def next_pow2(n: int) -> int:
    """Next power of two >= n."""
    return 1 << (n - 1).bit_length()


def conv1d_fft(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Linear convolution of real 1D arrays using FFT (length len(x)+len(y)-1)."""
    n = len(x)
    m = len(y)
    L = n + m - 1
    nfft = next_pow2(L)
    X = np.fft.rfft(x, nfft)
    Y = np.fft.rfft(y, nfft)
    Z = X * Y
    z = np.fft.irfft(Z, nfft)
    return z[:L]


def self_convolution(a: np.ndarray) -> np.ndarray:
    """Return b = conv(a, a) as linear convolution length 2n-1."""
    return conv1d_fft(a, a)


def objective_from_b(b: np.ndarray, n: int) -> float:
    """F(a) = 2 * n * max(conv(a, a)), valid when sum(a) = 1."""
    return float(2.0 * n * float(np.max(b)))


def exact_objective(a: np.ndarray) -> Tuple[float, np.ndarray]:
    """Compute exact objective and self-convolution."""
    b = self_convolution(a)
    F = objective_from_b(b, len(a))
    return F, b


def grad_corr_wrt_a(w: np.ndarray, a: np.ndarray) -> np.ndarray:
    """Compute gradient wrt a of ⟨w, conv(a, a)⟩ which equals 2 * corr(w, a).
    Implement via convolution with reversed a:
      corr(w, a)[j] = sum_k w[k] * a[k - j] = (w * reverse(a))[j + (n - 1)]
    """
    n = len(a)
    ar = a[::-1]
    t = conv1d_fft(w, ar)  # length (2n-1) + n - 1 = 3n - 2
    # Extract indices j = 0..n-1 at positions l = (n-1) + j
    start = n - 1
    corr_vals = t[start : start + n]
    return 2.0 * corr_vals


def tv2_and_grad(a: np.ndarray) -> Tuple[float, np.ndarray]:
    """Second-difference squared (TV^2-like) penalty and its gradient.
    Endpoints: sdd[0] = a0 - a1, sdd[n-1] = a_{n-1} - a_{n-2}
    Interior: sdd[i] = a[i-1] - 2 a[i] + a[i+1] for i = 1..n-2
    grad = 2 * L^T sdd
    """
    n = len(a)
    sdd = np.zeros_like(a)
    if n >= 2:
        sdd[0] = a[0] - a[1]
        sdd[-1] = a[-1] - a[-2]
    if n >= 3:
        sdd[1:-1] = a[:-2] - 2.0 * a[1:-1] + a[2:]

    # Apply L^T
    g = np.zeros_like(a)
    if n >= 2:
        g[0] += sdd[0]
        g[1] += -sdd[0]
        g[-1] += sdd[-1]
        g[-2] += -sdd[-1]
    if n >= 3:
        # Interior contributions
        g[:-2] += sdd[1:-1]
        g[1:-1] += -2.0 * sdd[1:-1]
        g[2:] += sdd[1:-1]

    loss = float(np.dot(sdd, sdd))
    grad = 2.0 * g
    return loss, grad


class Adam:
    def __init__(self, dim: int, lr: float = 0.03, beta1: float = 0.9, beta2: float = 0.999, eps: float = 1e-8):
        self.m = np.zeros(dim, dtype=np.float64)
        self.v = np.zeros(dim, dtype=np.float64)
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.lr = lr
        self.t = 0

    def step(self, grad: np.ndarray) -> np.ndarray:
        self.t += 1
        self.m = self.beta1 * self.m + (1.0 - self.beta1) * grad
        self.v = self.beta2 * self.v + (1.0 - self.beta2) * (grad * grad)
        m_hat = self.m / (1.0 - self.beta1 ** self.t)
        v_hat = self.v / (1.0 - self.beta2 ** self.t)
        return -self.lr * m_hat / (np.sqrt(v_hat) + self.eps)

    def set_lr(self, lr: float):
        self.lr = lr


def build_seeds(n: int, rng: np.random.Generator) -> List[np.ndarray]:
    """Generate a set of initial seeds on the simplex."""
    x = np.linspace(-0.25, 0.25, n)
    seeds = []

    # Arch family with width scaling
    width_scales = [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3]
    for s in width_scales:
        xs = x / s
        arch = 1.0 + 4.0 * np.abs(xs) - 16.0 * xs * xs
        arch = np.maximum(arch, 0.0)
        arch = 0.5 * (arch + arch[::-1])  # symmetrize
        if arch.sum() > 0:
            arch /= arch.sum()
            seeds.append(arch)

    # Uniform
    seeds.append(np.full(n, 1.0 / n))

    # Raised cosine (Hann window shape normalized)
    t = np.linspace(-1.0, 1.0, n)
    rc = 0.5 * (1.0 + np.cos(np.pi * t))
    rc /= rc.sum()
    seeds.append(rc)

    # Gaussian
    sigma = 0.35  # relative width in [-1,1]
    ga = np.exp(-0.5 * (t / sigma) ** 2)
    ga /= ga.sum()
    seeds.append(ga)

    # Triangular
    tri = 1.0 - np.abs(np.linspace(-1.0, 1.0, n))
    tri = np.maximum(tri, 0.0)
    tri /= tri.sum()
    seeds.append(tri)

    # Slightly perturbed variants of best current arch (use s=1.0)
    base = seeds[3] if len(seeds) > 3 else seeds[0]
    for _ in range(4):
        noise = rng.normal(0.0, 0.015, size=n)
        pert = base * (1.0 + noise)
        # Mild asymmetry exploration
        w = 0.7
        pert = w * pert + (1.0 - w) * pert[::-1]
        pert = np.maximum(pert, 0.0)
        if pert.sum() == 0:
            continue
        pert /= pert.sum()
        seeds.append(pert)

    # Deduplicate coarse (by rounding) and cap number
    uniq = []
    seen = set()
    for s in seeds:
        key = tuple(np.round(s, 6))
        if key not in seen:
            seen.add(key)
            uniq.append(s)
        if len(uniq) >= 16:
            break
    return uniq


def build_surrogate_weights(b: np.ndarray, tau_rel: float = 0.02, eps_plateau: float = 0.01, K: int = 12) -> np.ndarray:
    """Construct plateau-aware weights over lags."""
    L = len(b)
    bmax = float(np.max(b))
    bmin = float(np.min(b))
    tau = tau_rel * (bmax - bmin + 1e-12)

    # Top-K indices
    order = np.argsort(-b)
    topk = order[: min(K, L)]
    # Plateau set within (1 - eps)*bmax
    plateau_mask = b >= (1.0 - eps_plateau) * bmax
    S = np.where(plateau_mask)[0].tolist()
    # Expand the active set by small neighbor radius depending on plateau size
    plateau_size = int(np.sum(plateau_mask))
    radius = 1 + max(0, plateau_size // 40)
    active = set(S)
    for idx in list(S) + list(topk):
        for d in range(-radius, radius + 1):
            j = idx + d
            if 0 <= j < L:
                active.add(j)

    active = sorted(active)
    ws = np.zeros(L, dtype=np.float64)
    if active:
        logits = (b[active] - bmax) / max(1e-12, tau)
        # Softmax over active set
        ex = np.exp(np.clip(logits, -60.0, 0.0))
        sum_ex = ex.sum()
        if sum_ex > 0:
            ws_active = ex / sum_ex
        else:
            ws_active = np.full(len(active), 1.0 / len(active))
        ws[active] = ws_active
    else:
        ws[:] = 1.0 / L

    # Tail distribution: 50% uniform + 50% inverse pressure
    uniform = np.full(L, 1.0 / L)
    inv = 1.0 / (b - bmin + 1e-9)
    inv /= inv.sum()
    tail = 0.5 * uniform + 0.5 * inv

    # Gap-adaptive mixing
    # Estimate gap between top two peaks
    if len(order) >= 2:
        gap = (b[order[0]] - b[order[1]]) / (b[order[0]] + 1e-12)
    else:
        gap = 0.0
    # More tail when gap small
    alpha = 0.1 + 0.6 * (1.0 - min(1.0, gap / 0.02))
    alpha = float(np.clip(alpha, 0.1, 0.7))

    w = (1.0 - alpha) * ws + alpha * tail
    # Normalize safety
    w = np.maximum(w, 0.0)
    sw = w.sum()
    if sw <= 0:
        w[:] = 1.0 / L
    else:
        w /= sw
    return w


def entropy_grad(a: np.ndarray, participation: np.ndarray, base_weight: float, beta: float) -> Tuple[float, np.ndarray]:
    """Weighted entropy penalty: sum wj * a_j * log a_j with weights wj = base_weight * (1 + beta * participation_norm).
    Returns loss and gradient w.r.t a.
    """
    n = len(a)
    p = participation
    p = p - p.min()
    if p.max() > 0:
        p = p / p.max()
    w = base_weight * (1.0 + beta * p)
    # Avoid a_j == 0 causing -inf
    a_safe = np.maximum(a, 1e-15)
    loss = float(np.dot(w, a_safe * np.log(a_safe)))
    grad = w * (1.0 + np.log(a_safe))
    return loss, grad


def compute_participation(a: np.ndarray, top_lags: List[int]) -> np.ndarray:
    """Compute index participation in the selected lags:
    participation[j] = sum_{k in top_lags} a[k - j] if in range.
    """
    n = len(a)
    part = np.zeros(n, dtype=np.float64)
    for k in top_lags:
        j_min = max(0, k - (n - 1))
        j_max = min(n - 1, k)
        # For j in [j_min, j_max], contribution is a[k - j]
        # This is reverse segment of a
        # We'll accumulate efficiently
        if j_min <= j_max:
            idx_j = np.arange(j_min, j_max + 1)
            idx_a = k - idx_j
            part[idx_j] += a[idx_a]
    return part


def project_simplex(v: np.ndarray) -> np.ndarray:
    """Project onto the probability simplex: nonnegative, sum=1 (Euclidean projection)."""
    n = len(v)
    # Based on sorting trick
    u = np.sort(v)[::-1]
    cssv = np.cumsum(u)
    rho = np.nonzero(u * np.arange(1, n + 1) > (cssv - 1))[0]
    if len(rho) == 0:
        # fallback
        return np.full(n, 1.0 / n)
    rho = rho[-1]
    theta = (cssv[rho] - 1.0) / (rho + 1)
    w = np.maximum(v - theta, 0.0)
    s = w.sum()
    if s <= 0:
        return np.full(n, 1.0 / n)
    return w / s


def hard_max_polish(a: np.ndarray, r: int = 3, steps: int = 6, init_lr: float = 0.2) -> Tuple[np.ndarray, float]:
    """A few projected steps to minimize the average of the top-r peaks. Accept only improving moves.
    Returns (possibly improved a, best objective).
    """
    n = len(a)
    best_a = a.copy()
    best_F, b = exact_objective(a)
    order = np.argsort(-b)
    sel = order[: min(r, len(b))]
    w = np.zeros_like(b)
    w[sel] = 1.0 / len(sel)
    # Gradient wrt a of 2*n * <w, b> is 2*n*2*corr(w,a)
    for t in range(steps):
        g = 2.0 * n * grad_corr_wrt_a(w, a)
        # Take step in negative gradient direction on a and project to simplex
        lr = init_lr / (1.0 + 0.5 * t)
        a_trial = a - lr * g
        # Project to simplex and ensure positivity
        a_trial = project_simplex(a_trial)
        F_trial, _ = exact_objective(a_trial)
        if F_trial < best_F:
            best_F = F_trial
            best_a = a_trial.copy()
            a = a_trial
        else:
            # backtrack step
            init_lr *= 0.5
            if init_lr < 1e-6:
                break
    return best_a, best_F


def pair_shaving_transport(a: np.ndarray, b: np.ndarray, trials: int = 10, Q: int = 10) -> Tuple[np.ndarray, float]:
    """Remove a tiny mass from top-Q contributors and redistribute to Q low-pressure indices, with backtracking.
    Returns potentially improved (a, objective).
    """
    n = len(a)
    F_curr = objective_from_b(b, n)
    order = np.argsort(-b)
    worst_lags = order[:2].tolist()

    participation = compute_participation(a, worst_lags)
    # Contribution score: participation * a to reflect actual mass
    score = participation * (a + 1e-12)
    # top and bottom indices
    top_idx = np.argsort(-score)[:Q]
    bottom_idx = np.argsort(score)[:Q]

    total_mass = float(np.minimum(0.02, 0.5 * a[top_idx].sum()))
    if total_mass <= 0:
        return a, F_curr

    # Start with small epsilon and backtrack
    eps = total_mass
    best_a = a.copy()
    best_F = F_curr
    for _ in range(trials):
        # Remove mass from top proportionally to a and score
        remove_weights = a[top_idx]
        if remove_weights.sum() <= 0:
            break
        remove = eps * (remove_weights / remove_weights.sum())
        a_prop = a.copy()
        a_prop[top_idx] -= remove
        # Redistribute to bottom, favor indices with low a and low participation
        add_weights = (1.0 - participation[bottom_idx]) + (1e-6 - a[bottom_idx])
        add_weights = np.maximum(add_weights, 1e-9)
        add = eps * (add_weights / add_weights.sum())
        a_prop[bottom_idx] += add
        # Project to simplex in case of minor drifts
        a_prop = np.maximum(a_prop, 0.0)
        a_prop = a_prop / a_prop.sum()
        F_prop, _ = exact_objective(a_prop)
        if F_prop < best_F - 1e-12:
            best_F = F_prop
            best_a = a_prop
            # Try a slightly larger move next time
            eps *= 1.2
        else:
            eps *= 0.5
        if eps < 1e-8:
            break
    return best_a, best_F


def surrogate_descent(seed: np.ndarray, deadline: float, rng: np.random.Generator) -> Tuple[np.ndarray, float]:
    """Run the main optimization loop starting from a seed. Returns (best_a, best_score)."""
    n = len(seed)
    # Initialize logits so that softmax(log(a)) = a
    a = seed.copy()
    s = np.log(np.maximum(a, 1e-16))

    # Optimizer
    adam = Adam(dim=n, lr=0.04)
    grad_clip_norm = 5.0

    # Annealing and regularizer schedules
    tau_rel = 0.02
    eps_plateau = 0.012
    tv2_strength = 1e-3
    ent_base = 4e-4
    ent_beta = 0.8

    # Trackers
    best_a = a.copy()
    best_F, b = exact_objective(a)
    last_improv_iter = 0
    max_iters = 20000  # will be cut by time
    check_every = 20

    for it in range(max_iters):
        if time.time() > deadline:
            break

        # Current a and exact convolution
        a = softmax(s)
        F, b = exact_objective(a)

        # Update best
        if F < best_F - 1e-12:
            best_F = F
            best_a = a.copy()
            last_improv_iter = it

        # Build surrogate weights
        w = build_surrogate_weights(b, tau_rel=tau_rel, eps_plateau=eps_plateau, K=12)
        # Surrogate grad wrt a: g_sur = d/d a [2n * <w, b(a)>] = 2n * 2 * corr(w, a)
        g_sur_a = 2.0 * n * grad_corr_wrt_a(w, a)

        # Regularizers
        tv2_loss, tv2_grad_a = tv2_and_grad(a)
        ent_loss, ent_grad_a = entropy_grad(a, participation=compute_participation(a, [int(np.argmax(b))]), base_weight=ent_base, beta=ent_beta)
        # Total gradient wrt a
        g_a = g_sur_a + tv2_strength * tv2_grad_a + ent_grad_a

        # Map to logits space
        # g_s_j = a_j * (g_a_j - <g_a, a>)
        avg = float(np.dot(g_a, a))
        g_s = a * (g_a - avg)

        # Gradient clipping
        gnorm = np.linalg.norm(g_s)
        if not np.isfinite(gnorm) or gnorm > grad_clip_norm:
            if not np.isfinite(gnorm):
                g_s = np.nan_to_num(g_s, nan=0.0, posinf=0.0, neginf=0.0)
            else:
                g_s *= grad_clip_norm / (gnorm + 1e-12)

        # Adam step
        step = adam.step(g_s)
        s = s + step

        # Mild symmetry mixing early on to avoid pathological asymmetry (without enforcing perfect symmetry)
        if it % 50 == 0 and it < 600:
            a_tmp = softmax(s)
            mix = 0.1
            a_tmp = (1.0 - mix) * a_tmp + mix * a_tmp[::-1]
            a_tmp = a_tmp / a_tmp.sum()
            s = np.log(np.maximum(a_tmp, 1e-16))

        # Every few iterations, attempt polish or transport if no recent improvement
        if it % 200 == 150:
            # Hard-max polish on average of top r peaks
            a_tmp = softmax(s)
            a_polish, F_polish = hard_max_polish(a_tmp, r=3, steps=6, init_lr=0.15)
            if F_polish < best_F - 1e-12:
                best_F = F_polish
                best_a = a_polish
                s = np.log(np.maximum(best_a, 1e-16))

        if it % 260 == 210:
            # Pair-shaving based on worst two lags
            a_tmp = softmax(s)
            F_tmp, b_tmp = exact_objective(a_tmp)
            a_ps, F_ps = pair_shaving_transport(a_tmp, b_tmp, trials=8, Q=12)
            if F_ps < best_F - 1e-12:
                best_F = F_ps
                best_a = a_ps
                s = np.log(np.maximum(best_a, 1e-16))

        # Check annealing and learning rate adjustments
        if it % check_every == 0 and it > 0:
            # Anneal temperature and regularizers slowly
            tau_rel = max(0.005, tau_rel * 0.995)
            eps_plateau = max(0.004, eps_plateau * 0.997)
            tv2_strength = max(1e-6, tv2_strength * 0.997)
            ent_base = max(5e-5, ent_base * 0.995)

            # If no improvement for a while, reduce lr
            if it - last_improv_iter > 800:
                adam.set_lr(max(0.005, adam.lr * 0.7))
                last_improv_iter = it  # avoid repeated reductions too frequently

    return best_a, best_F


def search_for_best_sequence():
    """Run the multi-seed optimization with time management and return the best sequence found."""
    n = 600
    rng = np.random.default_rng(42)
    # Set internal deadline (leave a small safety margin)
    total_time_budget = 1000.0
    safety_margin = 4.0
    deadline = time.time() + total_time_budget - safety_margin

    # Build seeds
    seeds = build_seeds(n, rng)
    # Evaluate seeds quickly and prioritize
    seed_scores = []
    for a0 in seeds:
        F0, _ = exact_objective(a0)
        seed_scores.append(F0)
    order = np.argsort(seed_scores)
    seeds = [seeds[i] for i in order]

    # Keep best-so-far
    best_a = seeds[0].copy()
    best_F, _ = exact_objective(best_a)

    # Allocate per-seed time adaptively: more time for better seeds
    base_slice = max(5.0, (total_time_budget - safety_margin) / (2.0 * max(1, len(seeds))))
    # Start optimization over seeds until time runs out
    for idx, seed in enumerate(seeds):
        if time.time() > deadline:
            break
        # Bias time allocation: earlier seeds get more passes
        per_seed_deadline = min(deadline, time.time() + base_slice * (1.5 if idx < 3 else 1.0))

        a_opt, F_opt = surrogate_descent(seed, per_seed_deadline, rng)
        if F_opt < best_F - 1e-12:
            best_F = F_opt
            best_a = a_opt

        # Brief final polish if time allows
        if time.time() < deadline - 1.0:
            a_polish, F_polish = hard_max_polish(best_a, r=3, steps=6, init_lr=0.12)
            if F_polish < best_F - 1e-12:
                best_F = F_polish
                best_a = a_polish

    # Ensure nonnegativity and normalization
    best_a = np.maximum(best_a, 0.0)
    s = best_a.sum()
    if not np.isfinite(s) or s <= 0:
        # Fallback to a safe arch if something went wrong
        x = np.linspace(-0.25, 0.25, n)
        step_function = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
        step_function = (step_function + step_function[::-1]) / 2.0
        step_function = np.maximum(step_function, 0.0)
        step_function /= np.sum(step_function)
        return step_function
    best_a /= s
    return best_a
# EVOLVE_END


def evaluate_sequence(sequence: list[float]) -> float:
    """
    Evaluates a sequence of coefficients with enhanced security checks.
    Returns np.inf if the input is invalid.
    """
    # --- Security Checks ---

    # Verify that the input is a list
    if not isinstance(sequence, list):
        return np.inf

    # Reject empty lists
    if not sequence:
        return np.inf

    # Check each element in the list for validity
    for x in sequence:
        # Reject boolean types (as they are a subclass of int) and
        # any other non-integer/non-float types (like strings or complex numbers).
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return np.inf

        # Reject Not-a-Number (NaN) and infinity values.
        if np.isnan(x) or np.isinf(x):
            return np.inf

    # Convert all elements to float for consistency
    sequence = [float(x) for x in sequence]

    # Protect against negative numbers
    sequence = [max(0, x) for x in sequence]

    # Protect against numbers that are too large
    sequence = [min(1000.0, x) for x in sequence]

    n = len(sequence)
    b_sequence = np.convolve(sequence, sequence)
    max_b = max(b_sequence)
    sum_a = np.sum(sequence)

    # Protect against the case where the sum is too close to zero
    if sum_a < 0.01:
        return np.inf

    return float(2 * n * max_b / (sum_a**2))


def run_search_for_best_sequence():
    # Run the optimizer and return the best sequence found as a Python list
    best = search_for_best_sequence()
    # Final safety normalization and type conversion
    best = np.maximum(best, 0.0)
    s = best.sum()
    if not np.isfinite(s) or s <= 0:
        # Fallback to simple uniform sequence if something went wrong
        best = np.full(600, 1.0 / 600.0, dtype=np.float64)
    else:
        best = best / s
    return [float(x) for x in best.tolist()]


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```

```python
#!/usr/bin/env python3
"""Search for a 600-step nonnegative sequence that minimizes the autocorrelation-based upper bound.

Implements ATK‑PTCP‑X: a plateau-aware minimax optimizer on the simplex with FFT-accelerated exact
objective evaluation, annealed surrogate descent, and acceptance-tested non-smooth moves
(hard-max polish, cutting-plane average-of-tops, and multi-pair transport).
"""

import json
import math
import time
from typing import Tuple, List

import numpy as np


# EVOLVE_START
def softmax(x: np.ndarray) -> np.ndarray:
    """Numerically stable softmax."""
    m = np.max(x)
    z = x - m
    # Clip extreme negative values for stability
    ex = np.exp(np.clip(z, -60.0, 60.0))
    s = ex.sum()
    if s == 0.0 or not np.isfinite(s):
        # Safe fallback in extreme cases
        ex = np.exp(np.clip(z, -50.0, 50.0))
        s = ex.sum()
        if s == 0.0 or not np.isfinite(s):
            return np.full_like(x, 1.0 / len(x))
    return ex / s


def next_pow2(n: int) -> int:
    """Next power of two >= n."""
    return 1 << (n - 1).bit_length()


def conv1d_fft(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Linear convolution of real 1D arrays using FFT (length len(x)+len(y)-1)."""
    n = len(x)
    m = len(y)
    L = n + m - 1
    nfft = next_pow2(L)
    X = np.fft.rfft(x, nfft)
    Y = np.fft.rfft(y, nfft)
    Z = X * Y
    z = np.fft.irfft(Z, nfft)
    return z[:L]


def self_convolution(a: np.ndarray) -> np.ndarray:
    """Return b = conv(a, a) as linear convolution length 2n-1."""
    return conv1d_fft(a, a)


def objective_from_b(b: np.ndarray, n: int) -> float:
    """F(a) = 2 * n * max(conv(a, a)), valid when sum(a) = 1."""
    return float(2.0 * n * float(np.max(b)))


def exact_objective(a: np.ndarray) -> Tuple[float, np.ndarray]:
    """Compute exact objective and self-convolution."""
    b = self_convolution(a)
    F = objective_from_b(b, len(a))
    return F, b


def grad_corr_wrt_a(w: np.ndarray, a: np.ndarray) -> np.ndarray:
    """Compute gradient wrt a of ⟨w, conv(a, a)⟩ which equals 2 * corr(w, a).
    Implement via convolution with reversed a:
      corr(w, a)[j] = sum_k w[k] * a[k - j] = (w * reverse(a))[j + (n - 1)]
    """
    n = len(a)
    ar = a[::-1]
    t = conv1d_fft(w, ar)  # length (len(w)+len(ar)-1)
    # Extract indices j = 0..n-1 at positions l = (n-1) + j
    start = n - 1
    corr_vals = t[start : start + n]
    return 2.0 * corr_vals


def tv2_and_grad(a: np.ndarray) -> Tuple[float, np.ndarray]:
    """Second-difference squared (TV^2-like) penalty and its gradient.
    Endpoints: sdd[0] = a0 - a1, sdd[n-1] = a_{n-1} - a_{n-2}
    Interior: sdd[i] = a[i-1] - 2 a[i] + a[i+1] for i = 1..n-2
    grad = 2 * L^T sdd
    """
    n = len(a)
    sdd = np.zeros_like(a)
    if n >= 2:
        sdd[0] = a[0] - a[1]
        sdd[-1] = a[-1] - a[-2]
    if n >= 3:
        sdd[1:-1] = a[:-2] - 2.0 * a[1:-1] + a[2:]

    # Apply L^T
    g = np.zeros_like(a)
    if n >= 2:
        g[0] += sdd[0]
        g[1] += -sdd[0]
        g[-1] += sdd[-1]
        g[-2] += -sdd[-1]
    if n >= 3:
        # Interior contributions
        g[:-2] += sdd[1:-1]
        g[1:-1] += -2.0 * sdd[1:-1]
        g[2:] += sdd[1:-1]

    loss = float(np.dot(sdd, sdd))
    grad = 2.0 * g
    return loss, grad


class AdamAMSGrad:
    """Adam optimizer with AMSGrad variant for stability."""
    def __init__(self, dim: int, lr: float = 0.03, beta1: float = 0.9, beta2: float = 0.999, eps: float = 1e-8):
        self.m = np.zeros(dim, dtype=np.float64)
        self.v = np.zeros(dim, dtype=np.float64)
        self.vhat = np.zeros(dim, dtype=np.float64)
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.lr = lr
        self.t = 0

    def step(self, grad: np.ndarray) -> np.ndarray:
        self.t += 1
        self.m = self.beta1 * self.m + (1.0 - self.beta1) * grad
        self.v = self.beta2 * self.v + (1.0 - self.beta2) * (grad * grad)
        self.vhat = np.maximum(self.vhat, self.v)
        m_hat = self.m / (1.0 - self.beta1 ** self.t)
        v_hat = self.vhat / (1.0 - self.beta2 ** self.t)
        return -self.lr * m_hat / (np.sqrt(v_hat) + self.eps)

    def set_lr(self, lr: float):
        self.lr = lr


def build_seeds(n: int, rng: np.random.Generator) -> List[np.ndarray]:
    """Generate a set of initial seeds on the simplex."""
    x = np.linspace(-0.25, 0.25, n)
    seeds = []

    # Arch family with width scaling
    width_scales = [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3]
    for s in width_scales:
        xs = x / s
        arch = 1.0 + 4.0 * np.abs(xs) - 16.0 * xs * xs
        arch = np.maximum(arch, 0.0)
        arch = 0.5 * (arch + arch[::-1])  # symmetrize
        if arch.sum() > 0:
            arch /= arch.sum()
            seeds.append(arch)

    # Uniform
    seeds.append(np.full(n, 1.0 / n))

    # Raised cosine (Hann window shape normalized)
    t = np.linspace(-1.0, 1.0, n)
    rc = 0.5 * (1.0 + np.cos(np.pi * t))
    rc /= rc.sum()
    seeds.append(rc)

    # Gaussian
    sigma = 0.35  # relative width in [-1,1]
    ga = np.exp(-0.5 * (t / sigma) ** 2)
    ga /= ga.sum()
    seeds.append(ga)

    # Triangular
    tri = 1.0 - np.abs(np.linspace(-1.0, 1.0, n))
    tri = np.maximum(tri, 0.0)
    tri /= tri.sum()
    seeds.append(tri)

    # Slightly perturbed variants of a good arch
    base = seeds[3] if len(seeds) > 3 else seeds[0]
    for _ in range(6):
        noise = rng.normal(0.0, 0.02, size=n)
        pert = base * (1.0 + noise)
        # Mild asymmetry exploration
        w = rng.uniform(0.6, 0.8)
        pert = w * pert + (1.0 - w) * pert[::-1]
        pert = np.maximum(pert, 0.0)
        if pert.sum() == 0:
            continue
        pert /= pert.sum()
        seeds.append(pert)

    # Deduplicate coarse (by rounding) and cap number
    uniq = []
    seen = set()
    for s in seeds:
        key = tuple(np.round(s, 6))
        if key not in seen:
            seen.add(key)
            uniq.append(s)
        if len(uniq) >= 20:
            break
    return uniq


def build_surrogate_weights(b: np.ndarray, tau_rel: float, eps_plateau: float, K: int, neighbor_radius: int) -> np.ndarray:
    """Construct plateau-aware Top-K union weights over lags, with inverse-pressure tail."""
    L = len(b)
    bmax = float(np.max(b))
    bmin = float(np.min(b))
    # Scale tau relative to current dynamic range
    tau = tau_rel * (bmax - bmin + 1e-12)

    # Top-K indices
    order = np.argsort(-b)
    topk = order[: min(K, L)]
    # Plateau set within (1 - eps_plateau) * bmax
    plateau_mask = b >= (1.0 - eps_plateau) * bmax
    S = np.where(plateau_mask)[0].tolist()

    # Active set is the union of plateau and Top-K, expanded by neighbor_radius
    active = set(S)
    for idx in list(S) + list(topk):
        for d in range(-neighbor_radius, neighbor_radius + 1):
            j = idx + d
            if 0 <= j < L:
                active.add(j)
    active = sorted(active)

    # Low-temperature softmax over the active set centered at bmax
    ws = np.zeros(L, dtype=np.float64)
    if active:
        logits = (b[active] - bmax) / max(1e-12, tau)
        ex = np.exp(np.clip(logits, -60.0, 0.0))
        sum_ex = ex.sum()
        ws_active = ex / sum_ex if sum_ex > 0 else np.full(len(active), 1.0 / len(active))
        ws[active] = ws_active
    else:
        ws[:] = 1.0 / L

    # Tail distribution: 50% uniform + 50% inverse pressure
    uniform = np.full(L, 1.0 / L)
    inv = 1.0 / (b - bmin + 1e-9)
    inv_sum = inv.sum()
    if inv_sum <= 0 or not np.isfinite(inv_sum):
        inv = uniform.copy()
    else:
        inv /= inv_sum
    tail = 0.5 * uniform + 0.5 * inv

    # Gap-adaptive mixing
    # Estimate gap between top two peaks
    if len(order) >= 2:
        gap = (b[order[0]] - b[order[1]]) / (b[order[0]] + 1e-12)
    else:
        gap = 0.0
    plateau_size = int(np.sum(plateau_mask))
    # More tail when gap is small or plateau is wide
    alpha0 = 0.08
    alpha = alpha0 + 0.06 * (1.0 - min(1.0, gap / 0.025)) + 0.02 * min(1.0, plateau_size / (0.25 * L))
    alpha = float(np.clip(alpha, 0.06, 0.22))

    w = (1.0 - alpha) * ws + alpha * tail
    # Normalize safety
    w = np.maximum(w, 0.0)
    sw = w.sum()
    if sw <= 0:
        w[:] = 1.0 / L
    else:
        w /= sw
    return w


def entropy_grad(a: np.ndarray, participation: np.ndarray, base_weight: float, beta: float) -> Tuple[float, np.ndarray]:
    """Weighted entropy penalty: sum wj * a_j * log a_j with weights wj = base_weight * (1 + beta * participation_norm)."""
    p = participation.copy()
    p = p - p.min()
    if p.max() > 0:
        p = p / p.max()
    w = base_weight * (1.0 + beta * p)
    a_safe = np.maximum(a, 1e-15)
    loss = float(np.dot(w, a_safe * np.log(a_safe)))
    grad = w * (1.0 + np.log(a_safe))
    return loss, grad


def compute_participation(a: np.ndarray, top_lags: List[int]) -> np.ndarray:
    """Compute index participation in the selected lags:
    participation[j] = sum_{k in top_lags} a[k - j] if in range.
    """
    n = len(a)
    part = np.zeros(n, dtype=np.float64)
    for k in top_lags:
        j_min = max(0, k - (n - 1))
        j_max = min(n - 1, k)
        if j_min <= j_max:
            idx_j = np.arange(j_min, j_max + 1)
            idx_a = k - idx_j
            part[idx_j] += a[idx_a]
    return part


def project_simplex(v: np.ndarray) -> np.ndarray:
    """Project onto the probability simplex: nonnegative, sum=1 (Euclidean projection)."""
    n = len(v)
    u = np.sort(v)[::-1]
    cssv = np.cumsum(u)
    rho = np.nonzero(u * np.arange(1, n + 1) > (cssv - 1))[0]
    if len(rho) == 0:
        # fallback
        w = np.maximum(v, 0.0)
        s = w.sum()
        return w / s if s > 0 else np.full(n, 1.0 / n)
    rho = rho[-1]
    theta = (cssv[rho] - 1.0) / (rho + 1)
    w = np.maximum(v - theta, 0.0)
    s = w.sum()
    if s <= 0:
        return np.full(n, 1.0 / n)
    return w / s


def hard_max_polish(a: np.ndarray, r: int = 3, steps: int = 6, init_lr: float = 0.2) -> Tuple[np.ndarray, float]:
    """A few projected steps to minimize the average of the top-r peaks. Accept only improving moves."""
    n = len(a)
    best_a = a.copy()
    best_F, b = exact_objective(a)
    order = np.argsort(-b)
    sel = order[: min(r, len(b))]
    w = np.zeros_like(b)
    w[sel] = 1.0 / len(sel)
    for t in range(steps):
        g = 2.0 * n * grad_corr_wrt_a(w, a)
        lr = init_lr / (1.0 + 0.5 * t)
        a_trial = a - lr * g
        a_trial = project_simplex(a_trial)
        F_trial, _ = exact_objective(a_trial)
        if F_trial < best_F - 1e-12:
            best_F = F_trial
            best_a = a_trial.copy()
            a = a_trial
        else:
            init_lr *= 0.5
            if init_lr < 1e-6:
                break
    return best_a, best_F


def cutting_plane_refinement(a: np.ndarray, M: int = 36, steps: int = 50, init_lr: float = 0.12) -> Tuple[np.ndarray, float]:
    """Projected gradient refinement targeting the average of top-M lags (cutting-plane style).
    Accept only improving moves under the exact metric."""
    n = len(a)
    best_a = a.copy()
    best_F, b = exact_objective(a)
    order = np.argsort(-b)
    sel = order[: min(M, len(b))]
    w = np.zeros_like(b)
    w[sel] = 1.0 / len(sel)

    lr = init_lr
    a_curr = a.copy()
    for t in range(steps):
        g = 2.0 * n * grad_corr_wrt_a(w, a_curr)
        a_trial = a_curr - lr * g
        a_trial = project_simplex(a_trial)
        F_trial, b_trial = exact_objective(a_trial)
        if F_trial < best_F - 1e-12:
            best_F = F_trial
            best_a = a_trial.copy()
            a_curr = a_trial
            # mild increase in lr on success
            lr *= 1.05
        else:
            # backtrack
            lr *= 0.6
            if lr < 1e-7:
                break
    return best_a, best_F


def pair_shaving_transport(a: np.ndarray, b: np.ndarray, trials: int = 10, Q: int = 12, R: int = 3) -> Tuple[np.ndarray, float]:
    """Multi-pair transport: remove a tiny mass from top-Q contributors and redistribute to Q low-pressure indices.
    Participation computed using top-R worst lags. Accept only if F decreases."""
    n = len(a)
    F_curr = objective_from_b(b, n)
    order = np.argsort(-b)
    worst_lags = order[: min(R, len(b))].tolist()

    participation = compute_participation(a, worst_lags)
    # Contribution score: participation * a to reflect actual mass
    score = participation * (a + 1e-12)
    # top and bottom indices
    top_idx = np.argsort(-score)[:Q]
    bottom_idx = np.argsort(score)[:Q]

    total_mass = float(np.minimum(0.02, 0.4 * a[top_idx].sum()))
    if total_mass <= 0:
        return a, F_curr

    eps = total_mass
    best_a = a.copy()
    best_F = F_curr
    for _ in range(trials):
        remove_weights = a[top_idx] * (1.0 + score[top_idx] / (np.max(score[top_idx]) + 1e-12))
        rsum = remove_weights.sum()
        if rsum <= 0:
            break
        remove = eps * (remove_weights / rsum)
        a_prop = a.copy()
        a_prop[top_idx] -= remove

        add_weights = (1.0 - participation[bottom_idx]) + (1e-6 - a[bottom_idx])
        add_weights = np.maximum(add_weights, 1e-9)
        add = eps * (add_weights / add_weights.sum())
        a_prop[bottom_idx] += add

        a_prop = np.maximum(a_prop, 0.0)
        a_prop = a_prop / a_prop.sum()

        F_prop, _ = exact_objective(a_prop)
        if F_prop < best_F - 1e-12:
            best_F = F_prop
            best_a = a_prop
            # Try a slightly larger move next time
            eps *= 1.25
        else:
            eps *= 0.5
        if eps < 1e-8:
            break
    return best_a, best_F


def surrogate_descent(seed: np.ndarray, deadline: float, rng: np.random.Generator) -> Tuple[np.ndarray, float]:
    """Run the main optimization loop starting from a seed. Returns (best_a, best_score)."""
    n = len(seed)
    # Initialize logits so that softmax(log(a)) = a
    a = seed.copy()
    s = np.log(np.maximum(a, 1e-16))

    # Optimizer: AMSGrad with a cautious initial LR
    adam = AdamAMSGrad(dim=n, lr=0.035)
    grad_clip_norm = 5.0

    # Annealing and regularizer schedules
    tau_rel = 0.02
    eps_plateau = 0.012
    tv2_strength = 1.2e-3
    ent_base = 5e-4
    ent_beta = 0.8

    # Active-set controller parameters
    K = 12
    neighbor_radius = 1
    worst_idx_prev = None
    worst_change_count = 0

    # Trackers
    best_a = a.copy()
    best_F, b = exact_objective(a)
    last_improv_iter = 0
    max_iters = 24000  # limited by time
    check_every = 20

    # Phase scheduling
    phase_len = 300
    it = 0
    while it < max_iters:
        if time.time() > deadline:
            break

        # Current a and exact convolution
        a = softmax(s)
        F, b = exact_objective(a)

        # Update best
        if F < best_F - 1e-12:
            best_F = F
            best_a = a.copy()
            last_improv_iter = it

        # Update worst index change tracker
        worst_idx = int(np.argmax(b))
        if worst_idx_prev is None:
            worst_idx_prev = worst_idx
        else:
            if worst_idx != worst_idx_prev:
                worst_change_count += 1
            worst_idx_prev = worst_idx

        # Adaptive active-set control depending on plateau and gap
        order = np.argsort(-b)
        gap = (b[order[0]] - b[order[1]]) / (b[order[0]] + 1e-12) if len(order) >= 2 else 0.0
        plateau_mask = b >= (1.0 - eps_plateau) * b.max()
        plateau_size = int(np.sum(plateau_mask))
        if plateau_size > 0.12 * (2 * n - 1) or gap < 0.015:
            K = min(40, K + 2)
            neighbor_radius = min(2, 1 + plateau_size // 60)
        else:
            K = max(10, K - 1)
            neighbor_radius = max(0, neighbor_radius - 1)

        # Build surrogate weights
        w = build_surrogate_weights(b, tau_rel=tau_rel, eps_plateau=eps_plateau, K=K, neighbor_radius=neighbor_radius)
        # Surrogate gradient wrt a: g_sur = d/d a [2n * <w, b(a)>] = 2n * 2 * corr(w, a)
        g_sur_a = 2.0 * n * grad_corr_wrt_a(w, a)

        # Regularizers
        # Participation uses top-R lags; R increases if gap is very small
        R = 3 if gap < 0.012 else 2
        top_lags = order[: min(R, len(b))].tolist()
        part = compute_participation(a, top_lags)
        tv2_loss, tv2_grad_a = tv2_and_grad(a)
        ent_loss, ent_grad_a = entropy_grad(a, participation=part, base_weight=ent_base, beta=ent_beta)

        # Total gradient wrt a
        g_a = g_sur_a + tv2_strength * tv2_grad_a + ent_grad_a

        # Map to logits space: g_s_j = a_j * (g_a_j - <g_a, a>)
        avg = float(np.dot(g_a, a))
        g_s = a * (g_a - avg)

        # Gradient clipping
        gnorm = np.linalg.norm(g_s)
        if not np.isfinite(gnorm) or gnorm > grad_clip_norm:
            if not np.isfinite(gnorm):
                g_s = np.nan_to_num(g_s, nan=0.0, posinf=0.0, neginf=0.0)
            else:
                g_s *= grad_clip_norm / (gnorm + 1e-12)

        # Adam step
        step_vec = adam.step(g_s)
        s = s + step_vec

        # Mild symmetry mixing early to avoid pathological asymmetry
        if it % 50 == 0 and it < 800:
            a_tmp = softmax(s)
            mix = 0.08
            a_tmp = (1.0 - mix) * a_tmp + mix * a_tmp[::-1]
            a_tmp = a_tmp / a_tmp.sum()
            s = np.log(np.maximum(a_tmp, 1e-16))

        # Hard moves interleaving: polish, cutting-plane, and transport
        if it % phase_len == phase_len - 150 and time.time() < deadline - 0.5:
            a_tmp = softmax(s)
            a_polish, F_polish = hard_max_polish(a_tmp, r=3, steps=6, init_lr=0.15)
            if F_polish < best_F - 1e-12:
                best_F = F_polish
                best_a = a_polish
                s = np.log(np.maximum(best_a, 1e-16))

        if it % phase_len == phase_len - 100 and time.time() < deadline - 0.5:
            a_tmp = softmax(s)
            a_cp, F_cp = cutting_plane_refinement(a_tmp, M=36, steps=40, init_lr=0.1)
            if F_cp < best_F - 1e-12:
                best_F = F_cp
                best_a = a_cp
                s = np.log(np.maximum(best_a, 1e-16))

        if it % phase_len == phase_len - 50 and time.time() < deadline - 0.5:
            a_tmp = softmax(s)
            F_tmp, b_tmp = exact_objective(a_tmp)
            a_ps, F_ps = pair_shaving_transport(a_tmp, b_tmp, trials=8, Q=12, R=3)
            if F_ps < best_F - 1e-12:
                best_F = F_ps
                best_a = a_ps
                s = np.log(np.maximum(best_a, 1e-16))

        # Schedules and stability checks
        if it % check_every == 0 and it > 0:
            # Anneal temperature and regularizers slowly
            tau_rel = max(0.0045, tau_rel * 0.996)
            eps_plateau = max(0.0035, eps_plateau * 0.997)
            tv2_strength = max(8e-7, tv2_strength * 0.997)
            ent_base = max(4e-5, ent_base * 0.995)
            ent_beta = max(0.25, ent_beta * 0.997)

            # If no improvement for a while or worst peak keeps swapping, reduce lr
            if it - last_improv_iter > 900 or worst_change_count > 7:
                adam.set_lr(max(0.006, adam.lr * 0.7))
                worst_change_count = 0
                last_improv_iter = it  # avoid repeated reductions too frequently

        it += 1

    # Final tiny-τ sharpening and brief hard moves
    a_final = softmax(s)
    F_final, _ = exact_objective(a_final)
    if F_final < best_F - 1e-12:
        best_F = F_final
        best_a = a_final

    # Final acceptance-tested polish and cutting-plane
    a_polish, F_polish = hard_max_polish(best_a, r=3, steps=6, init_lr=0.12)
    if F_polish < best_F - 1e-12:
        best_F = F_polish
        best_a = a_polish
    a_cp, F_cp = cutting_plane_refinement(best_a, M=40, steps=35, init_lr=0.08)
    if F_cp < best_F - 1e-12:
        best_F = F_cp
        best_a = a_cp

    # Ensure nonnegativity and normalization
    best_a = np.maximum(best_a, 0.0)
    ssum = best_a.sum()
    if not np.isfinite(ssum) or ssum <= 0:
        # Fallback to a safe arch if something went wrong
        x = np.linspace(-0.25, 0.25, n)
        step_function = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
        step_function = (step_function + step_function[::-1]) / 2.0
        step_function = np.maximum(step_function, 0.0)
        step_function /= np.sum(step_function)
        return step_function, objective_from_b(self_convolution(step_function), n)
    best_a /= ssum
    return best_a, best_F


def search_for_best_sequence():
    """Run the multi-seed optimization with time management and return the best sequence found."""
    n = 600
    rng = np.random.default_rng(42)
    # Set internal deadline (leave a small safety margin)
    total_time_budget = 1000.0
    safety_margin = 4.0
    deadline = time.time() + total_time_budget - safety_margin

    # Build seeds
    seeds = build_seeds(n, rng)
    # Evaluate seeds quickly and prioritize
    seed_scores = []
    for a0 in seeds:
        F0, _ = exact_objective(a0)
        seed_scores.append(F0)
    order = np.argsort(seed_scores)
    seeds = [seeds[i] for i in order]

    # Keep best-so-far
    best_a = seeds[0].copy()
    best_F, _ = exact_objective(best_a)

    # Allocate per-seed time adaptively: more time for better seeds
    num_seeds = max(1, len(seeds))
    base_slice = max(6.0, (total_time_budget - safety_margin) / (2.0 * num_seeds))

    # Start optimization over seeds until time runs out
    for idx, seed in enumerate(seeds):
        if time.time() > deadline:
            break
        # Bias time allocation: earlier seeds get more passes
        factor = 1.7 if idx < 3 else (1.2 if idx < 7 else 1.0)
        per_seed_deadline = min(deadline, time.time() + base_slice * factor)

        a_opt, F_opt = surrogate_descent(seed, per_seed_deadline, rng)
        if F_opt < best_F - 1e-12:
            best_F = F_opt
            best_a = a_opt

        # Brief final polish if time allows
        if time.time() < deadline - 1.0:
            a_polish, F_polish = hard_max_polish(best_a, r=3, steps=6, init_lr=0.12)
            if F_polish < best_F - 1e-12:
                best_F = F_polish
                best_a = a_polish
            a_cp, F_cp = cutting_plane_refinement(best_a, M=40, steps=30, init_lr=0.07)
            if F_cp < best_F - 1e-12:
                best_F = F_cp
                best_a = a_cp

    # Ensure nonnegativity and normalization
    best_a = np.maximum(best_a, 0.0)
    s = best_a.sum()
    if not np.isfinite(s) or s <= 0:
        # Fallback to a safe arch if something went wrong
        x = np.linspace(-0.25, 0.25, n)
        step_function = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
        step_function = (step_function + step_function[::-1]) / 2.0
        step_function = np.maximum(step_function, 0.0)
        step_function /= np.sum(step_function)
        return step_function
    best_a /= s
    return best_a
# EVOLVE_END


def evaluate_sequence(sequence: list[float]) -> float:
    """
    Evaluates a sequence of coefficients with enhanced security checks.
    Returns np.inf if the input is invalid.
    """
    # --- Security Checks ---

    # Verify that the input is a list
    if not isinstance(sequence, list):
        return np.inf

    # Reject empty lists
    if not sequence:
        return np.inf

    # Check each element in the list for validity
    for x in sequence:
        # Reject boolean types (as they are a subclass of int) and
        # any other non-integer/non-float types (like strings or complex numbers).
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return np.inf

        # Reject Not-a-Number (NaN) and infinity values.
        if np.isnan(x) or np.isinf(x):
            return np.inf

    # Convert all elements to float for consistency
    sequence = [float(x) for x in sequence]

    # Protect against negative numbers
    sequence = [max(0, x) for x in sequence]

    # Protect against numbers that are too large
    sequence = [min(1000.0, x) for x in sequence]

    n = len(sequence)
    b_sequence = np.convolve(sequence, sequence)
    max_b = max(b_sequence)
    sum_a = np.sum(sequence)

    # Protect against the case where the sum is too close to zero
    if sum_a < 0.01:
        return np.inf

    return float(2 * n * max_b / (sum_a**2))


def run_search_for_best_sequence():
    # Run the optimizer and return the best sequence found as a Python list
    best = search_for_best_sequence()
    # Final safety normalization and type conversion
    best = np.maximum(best, 0.0)
    s = best.sum()
    if not np.isfinite(s) or s <= 0:
        # Fallback to simple uniform sequence if something went wrong
        best = np.full(600, 1.0 / 600.0, dtype=np.float64)
    else:
        best = best / s
    return [float(x) for x in best.tolist()]


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```

```python
#!/usr/bin/env python3
"""Optimized step function search for minimizing the autocorrelation upper bound.

This module implements a search to find a nonnegative sequence (step heights)
that minimizes the evaluation function

  F(a) = 2 * n * max(conv(a, a)) / (sum(a)^2)

under nonnegativity constraints. We parameterize a on the probability simplex
(sum(a) = 1, a_i >= 0) using a softmax of unconstrained logits and optimize a
smooth surrogate of the hard maximum of the autoconvolution via Adam with FFT-
accelerated convolutions. We incorporate:
- Plateau-union Top-K log-sum-exp (LSE) surrogate to track the active maxima,
- inverse-pressure tail blend to stabilize near ties,
- cheap pressure-guided mirror microsteps to stabilize plateaus,
- accepted-only cutting-plane, hard-max polish, and multi-pair transport moves,
- symmetry nudging and light smoothing regularization,
- multi-start diverse seeding and jittered variants.

The code tracks the best true objective encountered and returns the best
nonnegative sequence (list of floats). It respects a time budget provided by
the environment variable MAX_SEARCH_TIME (seconds) and returns before the
deadline with a small safety buffer.
"""

import json
import os
import time
from typing import Tuple

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Generate optimized step function using a tri-gear optimizer with FFT-based evaluation.

    Returns:
        np.ndarray: Optimized nonnegative sequence summing to 1.
    """
    # Problem dimension and grid
    n = 600
    interval_start = -1.0 / 4.0
    interval_end = 1.0 / 4.0
    x = np.linspace(interval_start, interval_end, n)

    # Time budget management
    # Honour environment variable; default to a solid budget but not excessive by default.
    default_time_budget = 300.0
    max_time = float(os.environ.get("MAX_SEARCH_TIME", default_time_budget))
    t_start = time.time()
    t_deadline = t_start + max_time * 0.98  # leave buffer

    rng = np.random.default_rng(20260831)

    # Utility: normalize and make nonnegative
    def normalize(a: np.ndarray) -> np.ndarray:
        a = np.maximum(a, 0.0)
        s = np.sum(a)
        if s <= 0:
            # fallback to uniform if degenerate
            return np.ones_like(a) / len(a)
        return a / s

    # Seed generators (diverse shapes)
    def seed_uniform():
        return np.ones(n) / n

    def seed_triangular(width_scale=1.0):
        # Triangular centered, peak at center, taper to ends
        t = np.linspace(-1.0, 1.0, n)
        a = np.maximum(0.0, 1.0 - width_scale * np.abs(t))
        return normalize(a)

    def seed_raised_cosine(width_scale=1.0):
        # Raised cosine with adjustable width (Kaiser/Tukey-like broad hump)
        t = np.linspace(-1.0, 1.0, n)
        eff = np.clip(1.0 - width_scale * np.abs(t), 0.0, 1.0)
        a = 0.5 * (1.0 + np.cos(np.pi * (1.0 - eff)))
        a *= (eff > 0).astype(float)
        return normalize(a)

    def seed_gaussian(sigma_scale=1.0):
        # Gaussian centered at 0; sigma relative to range length
        t = np.linspace(-1.0, 1.0, n)
        sigma = 0.22 * sigma_scale
        a = np.exp(-0.5 * (t / sigma) ** 2)
        return normalize(a)

    def seed_tukey(alpha=0.5):
        # Tukey window (tapered cosine)
        N = n
        a = np.zeros(N, dtype=float)
        if alpha <= 0:
            a[:] = 1.0
        elif alpha >= 1:
            a = 0.5 * (1.0 + np.cos(np.linspace(-np.pi, np.pi, N)))
        else:
            # standard symmetric Tukey
            for i in range(N):
                k = i / (N - 1.0)
                if k < alpha / 2:
                    a[i] = 0.5 * (1 + np.cos(np.pi * (2 * k / alpha - 1)))
                elif k <= 1 - alpha / 2:
                    a[i] = 1.0
                else:
                    a[i] = 0.5 * (1 + np.cos(np.pi * (2 * k / alpha - 2 / alpha + 1)))
        return normalize(a)

    def seed_blackman():
        # Blackman window
        N = n
        m = np.arange(N)
        a = 0.42 - 0.5 * np.cos(2 * np.pi * m / (N - 1)) + 0.08 * np.cos(4 * np.pi * m / (N - 1))
        return normalize(a)

    def seed_kaiser(beta=8.0):
        # Kaiser window
        m = np.arange(n)
        return normalize(np.kaiser(n, beta))

    def seed_arch(scale=1.0):
        # Arch-like polynomial; scale modifies curvature width
        s = float(scale)
        # The base profile ensures nonnegativity within the interval; replicate symmetrically
        step = 1.0 + 4.0 * np.abs(x) - 16.0 * (s * x) ** 2
        step = (step + step[::-1]) / 2.0
        step = np.maximum(step, 0.0)
        return normalize(step)

    # FFT convolution utilities: precompute FFT size for efficiency
    def next_pow2(m: int) -> int:
        k = 1
        while k < m:
            k <<= 1
        return k

    conv_len = 2 * n - 1
    L_fft = next_pow2(conv_len)

    def conv_full_rfft(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        A = np.fft.rfft(a, L_fft)
        B = np.fft.rfft(b, L_fft)
        C = A * B
        c = np.fft.irfft(C, L_fft)
        return c[:conv_len]

    def self_convolution(a: np.ndarray) -> np.ndarray:
        # full convolution conv(a, a) length 2n-1
        return conv_full_rfft(a, a)

    # Objective function on simplex (sum(a)=1): F(a) = 2 * n * max(conv(a,a))
    def objective_and_b(a: np.ndarray) -> Tuple[float, np.ndarray]:
        b = self_convolution(a)
        # Numerical safety: clip tiny negatives
        if np.min(b) < 0:
            b = np.maximum(b, 0.0)
        return 2.0 * n * float(np.max(b)), b

    # Gradient of surrogate J(a) = 2*n * sum_k w_k b_k, where b = conv(a, a)
    # dJ/da = 4*n * f, with f[p] = sum_{j} a[j] * w[p + j]
    # f obtained via convolution conv(a_rev, w) and slicing.
    def surrogate_grad(a: np.ndarray, w: np.ndarray) -> np.ndarray:
        a_rev = a[::-1]
        conv_aw = conv_full_rfft(a_rev, w)
        # select indices p in [0..n-1]: position offset by (n-1)
        f = conv_aw[(n - 1):(n - 1 + n)]
        return 4.0 * n * f

    # Softmax mapping and helper
    def softmax(z: np.ndarray) -> np.ndarray:
        z_shift = z - np.max(z)
        e = np.exp(z_shift)
        s = np.sum(e)
        if not np.isfinite(s) or s <= 0:
            # Fallback to uniform
            return np.ones_like(z) / len(z)
        return e / s

    # Build active set and surrogate weights focusing on top peaks with smoothing tail
    def build_weights(b: np.ndarray,
                      tau_scale: float,
                      alpha_tail: float,
                      top_k: int,
                      plateau_eps: float) -> Tuple[np.ndarray, np.ndarray]:
        m = b.shape[0]
        w = np.zeros(m, dtype=float)
        bmax = float(np.max(b))
        bmin = float(np.min(b))
        if bmax <= 0.0:
            return np.ones(m) / m, np.arange(m)

        idx_sorted = np.argsort(-b)
        top_idx = idx_sorted[:max(1, min(top_k, m))]

        plateau_mask = b >= max(0.0, (1.0 - plateau_eps) * bmax)
        S_mask = plateau_mask.copy()
        S_mask[top_idx] = True

        # Expand neighbors by radius depending on plateau width
        plateau_count = int(np.sum(plateau_mask))
        radius = 1 if plateau_count < 10 else 2
        S_indices = np.where(S_mask)[0]
        if S_indices.size > 0:
            S_indices = np.unique(
                np.concatenate(
                    [S_indices,
                     np.clip(S_indices - radius, 0, m - 1),
                     np.clip(S_indices + radius, 0, m - 1)]
                )
            )
        else:
            S_indices = top_idx

        # Soft weights on S using temperature
        tau = max(1e-6, tau_scale * bmax)
        logits = (b[S_indices] - bmax) / tau
        logits -= np.max(logits)
        ws = np.exp(logits)
        sum_ws = np.sum(ws)
        if not np.isfinite(sum_ws) or sum_ws <= 0:
            ws = np.ones_like(ws) / len(ws)
        else:
            ws = ws / sum_ws

        # Tail blend: uniform and inverse-pressure tail
        w_uniform = np.ones(m, dtype=float) / m
        inv_press = 1.0 / (b - bmin + 1e-12)
        inv_press = inv_press / np.sum(inv_press)
        tail = 0.5 * w_uniform + 0.5 * inv_press

        w[S_indices] = ws * (1.0 - alpha_tail)
        w += alpha_tail * tail

        # Normalize to sum 1
        w = w / np.sum(w)
        return w, S_indices

    # Cutting-plane weights: uniform on top M lags
    def build_cutting_plane_weights(b: np.ndarray, M: int = 36) -> np.ndarray:
        m = b.shape[0]
        w = np.zeros(m, dtype=float)
        idx_sorted = np.argsort(-b)
        sel = idx_sorted[:max(1, min(M, m))]
        w[sel] = 1.0
        w /= np.sum(w)
        return w

    # Pressure vector from weighted active lags S: approximate per-index participation
    # Using corr(w, a); gradient relates to 4*n*corr(w, a). We use f = corr(w, a) as "pressure".
    def pressure_vector(a: np.ndarray, w: np.ndarray) -> np.ndarray:
        a_rev = a[::-1]
        conv_aw = conv_full_rfft(a_rev, w)
        f = conv_aw[(n - 1):(n - 1 + n)]
        return 2.0 * f  # scale to reflect both sides; overall scale is not critical

    # Multi-pair mass transport: shift small mass from high-pressure to low-pressure indices with distance bias.
    def multi_pair_transport(a: np.ndarray,
                             pressure: np.ndarray,
                             delta: float = 5e-3,
                             Q: int = 10) -> np.ndarray:
        # donors: highest pressure; receivers: lowest pressure with bias away from center
        idx_desc = np.argsort(-pressure)
        idx_asc = np.argsort(pressure)
        donors = idx_desc[:Q]
        # Distance bias away from center for receivers
        c = 0.5 * (n - 1)
        dist = np.abs(np.arange(n) - c)
        bias = dist / (c + 1e-12)
        # among low-pressure, choose those with highest bias
        cand_recv = idx_asc[:max(2 * Q, Q)]
        cand_bias = bias[cand_recv]
        order_recv = cand_recv[np.argsort(-cand_bias)]
        receivers = order_recv[:Q]

        a_new = a.copy()
        donor_total = float(np.sum(a_new[donors]))
        if donor_total <= 0:
            return a

        # Limit total moved mass
        total_delta = min(delta, donor_total * 0.25)
        if total_delta <= 0:
            return a

        # Remove proportional to donor mass
        share = a_new[donors] / (np.sum(a_new[donors]) + 1e-18)
        rem = total_delta * share
        a_new[donors] -= rem
        a_new[donors] = np.maximum(a_new[donors], 0.0)

        # Distribute to receivers proportional to bias
        recv_bias = bias[receivers] + 1e-6
        recv_share = recv_bias / np.sum(recv_bias)
        a_new[receivers] += total_delta * recv_share

        # Renormalize safeguard
        a_new = normalize(a_new)
        return a_new

    # Simple micro mass transport using the single worst lag
    def local_peak_shave(a: np.ndarray, b: np.ndarray, delta: float = 1e-3) -> np.ndarray:
        k_star = int(np.argmax(b))
        w_star = np.zeros_like(b)
        w_star[k_star] = 1.0
        p = pressure_vector(a, w_star)
        return multi_pair_transport(a, p, delta=delta, Q=8)

    # Optimization from a seed using tri-gear orchestration
    def optimize_from_seed(a0: np.ndarray, time_budget: float) -> np.ndarray:
        # Initialize logits from seed
        z = np.log(np.maximum(a0, 1e-12))
        z -= np.mean(z)

        # Adam parameters
        beta1 = 0.9
        beta2 = 0.999
        eps_adam = 1e-8
        lr0 = 0.06  # initial learning rate on logits
        lr_min = 1e-3
        lr_decay = 0.997  # per iteration

        # Regularization strengths
        lam_entropy0 = 1e-3
        lam_tv0 = 8e-4

        # Periods for auxiliary moves
        cp_period = 30
        hardmax_period = 60
        transport_period = 45
        symmetry_period = 50
        microsteps_per_iter = 2

        # Annealing schedule (as a function of iteration)
        def anneal_factor(iteration: int, total_hint: float = 5000.0) -> float:
            # Smooth decay ~ 1/sqrt(1 + t/T)
            return 1.0 / np.sqrt(1.0 + iteration / total_hint)

        # Tracking
        m = np.zeros_like(z)
        v = np.zeros_like(z)
        t_adam = 0

        a = softmax(z)
        best_a = a.copy()
        best_F, best_b = objective_and_b(a)
        best_time = time.time()

        # Argmax flapping detection
        last_k = None
        flap_count = 0
        history_len = 12
        recent_ks = []

        iter_count = 0
        local_deadline = min(time.time() + time_budget, t_deadline)

        while time.time() < local_deadline:
            iter_count += 1
            t_adam += 1

            # Current a and b
            a = softmax(z)
            F, b = objective_and_b(a)
            bmax = float(np.max(b))

            # Track best
            if F < best_F - 1e-12:
                best_F = F
                best_b = b.copy()
                best_a = a.copy()
                best_time = time.time()

            # Active lag argmax and flapping control
            k_star = int(np.argmax(b))
            recent_ks.append(k_star)
            if len(recent_ks) > history_len:
                recent_ks.pop(0)
            if last_k is not None and k_star != last_k:
                flap_count += 1
            last_k = k_star
            flap_rate = flap_count / max(1, iter_count)

            # Build surrogate weights (G1)
            # Adaptive alpha_tail based on plateau width and top-two gap
            idx_sorted = np.argsort(-b)
            b1 = b[idx_sorted[0]]
            b2 = b[idx_sorted[1]] if len(idx_sorted) > 1 else b1
            gap = (b1 - b2) / (b1 + 1e-18)
            plateau_eps = max(5e-4, 1e-3 * (0.7 + 0.6 * (1.0 - gap)))
            plateau_count = int(np.sum(b >= (1.0 - plateau_eps) * bmax))
            alpha_tail_base = 0.25 + 0.20 * (1.0 - gap) + 0.10 * np.tanh(max(0, plateau_count - 6) / 8.0)
            alpha_tail = float(np.clip(alpha_tail_base, 0.2, 0.6))

            # Temperature scale annealing
            af = anneal_factor(iter_count, 6000.0)
            tau_scale = max(1e-4, 0.03 * af)

            w, S_indices = build_weights(b, tau_scale=tau_scale, alpha_tail=alpha_tail,
                                         top_k=18, plateau_eps=plateau_eps)

            # Surrogate gradient wrt a
            grad_a = surrogate_grad(a, w)

            # Regularization: entropy encourages spread, second-difference smoothness
            lam_entropy = lam_entropy0 * af
            lam_tv = lam_tv0 * af
            grad_a += lam_entropy * (-(np.log(np.maximum(a, 1e-18)) + 1.0))
            lap = np.zeros_like(a)
            lap[1:-1] = 2.0 * a[1:-1] - a[:-2] - a[2:]
            lap[0] = a[0] - a[1]
            lap[-1] = a[-1] - a[-2]
            grad_a += lam_tv * lap

            # Map to logits gradient via softmax Jacobian
            sdot = float(np.dot(a, grad_a))
            grad_z = a * (grad_a - sdot)

            # Gradient clipping
            gnorm = np.linalg.norm(grad_z)
            if np.isfinite(gnorm) and gnorm > 5.0:
                grad_z *= 5.0 / gnorm

            # Adam update on logits (G1)
            m = beta1 * m + (1.0 - beta1) * grad_z
            v = beta2 * v + (1.0 - beta2) * (grad_z * grad_z)
            m_hat = m / (1.0 - beta1 ** t_adam)
            v_hat = v / (1.0 - beta2 ** t_adam)

            # Learning rate with decay and flapping control
            lr = max(lr_min, lr0 * (lr_decay ** iter_count))
            if flap_rate > 0.05 or (len(recent_ks) >= 6 and len(set(recent_ks[-6:])) >= 3):
                lr *= 0.7  # dampen when maxima identity flips frequently
            step = -lr * m_hat / (np.sqrt(v_hat) + eps_adam)

            # Trust region: clip step norm
            step_norm = np.linalg.norm(step)
            if step_norm > 0.6:
                step *= (0.6 / step_norm)
            z += step

            # Cheap pressure-guided mirror microsteps (G2)
            # Use active set weights restricted to S with focus temperature
            # Build a sharper w_active over S only
            tau_s = max(1e-5, 0.015 * af) * bmax
            logits_s = (b[S_indices] - bmax) / max(tau_s, 1e-12)
            logits_s -= np.max(logits_s)
            ws_s = np.exp(logits_s)
            ws_s /= np.sum(ws_s) if np.sum(ws_s) > 0 else 1.0
            w_active = np.zeros_like(b)
            w_active[S_indices] = ws_s
            # Microstep loop
            for _ in range(microsteps_per_iter):
                r = pressure_vector(a, w_active)  # approximate participation/pressure
                rs = r - float(np.dot(r, a))  # center
                g_micro = a * rs
                # Move against pressure (descent)
                lr_micro = 0.2 * lr  # smaller than main step
                z -= lr_micro * g_micro

            # Accepted-only non-smooth moves (G3)
            # Cutting-plane: average of top-M lags
            if iter_count % cp_period == 0:
                a_cp = softmax(z)
                F_cp, b_cp = objective_and_b(a_cp)
                w_cp = build_cutting_plane_weights(b_cp, M=40 if plateau_count > 8 else 32)
                # 1-2 backtracked projected steps
                grad_cp = surrogate_grad(a_cp, w_cp)
                sdot_cp = float(np.dot(a_cp, grad_cp))
                gradz_cp = a_cp * (grad_cp - sdot_cp)
                # Try a couple of step sizes
                accepted = False
                for gamma in [0.03, 0.015]:
                    z_trial = z - gamma * gradz_cp
                    a_trial = softmax(z_trial)
                    F_trial, _ = objective_and_b(a_trial)
                    if F_trial <= F_cp - 1e-12:
                        z = z_trial
                        accepted = True
                        break
                if not accepted:
                    # no change
                    pass

            # Hard-max polish on top-2 or top-3 lags
            if iter_count % hardmax_period == 0 and (time.time() - best_time) > 1.0:
                a_pol = softmax(z)
                F_pol, b_pol = objective_and_b(a_pol)
                idx_sorted = np.argsort(-b_pol)
                r = 3 if gap < 0.03 else 2
                top_r = idx_sorted[:r]
                w_pol = np.zeros_like(b_pol)
                w_pol[top_r] = 1.0 / r
                grad_pol = surrogate_grad(a_pol, w_pol)
                sdot_pol = float(np.dot(a_pol, grad_pol))
                gradz_pol = a_pol * (grad_pol - sdot_pol)
                z_pol = z - 0.02 * gradz_pol
                a_pol2 = softmax(z_pol)
                F_pol2, _ = objective_and_b(a_pol2)
                if F_pol2 <= F_pol:
                    z = z_pol  # accept

            # Multi-pair transport (peak shaving) with acceptance
            if iter_count % transport_period == 0:
                a_now = softmax(z)
                F_now, b_now = objective_and_b(a_now)
                # Use worst-lag based pressure
                w_star = np.zeros_like(b_now)
                w_star[int(np.argmax(b_now))] = 1.0
                p = pressure_vector(a_now, w_star)
                # escalate δ if long stagnation
                dt_since_best = time.time() - best_time
                delta = 4e-3 if dt_since_best > 5.0 else 2e-3
                a_mt = multi_pair_transport(a_now, p, delta=delta, Q=12 if gap < 0.05 else 8)
                F_mt, _ = objective_and_b(a_mt)
                if F_mt < F_now - 1e-12:
                    z = np.log(np.maximum(a_mt, 1e-18))
                    z -= np.mean(z)
                else:
                    # Backtrack smaller
                    a_mt2 = multi_pair_transport(a_now, p, delta=delta * 0.5, Q=8)
                    F_mt2, _ = objective_and_b(a_mt2)
                    if F_mt2 < F_now - 1e-12:
                        z = np.log(np.maximum(a_mt2, 1e-18))
                        z -= np.mean(z)

            # Symmetry nudging with acceptance
            if iter_count % symmetry_period == 0:
                a_sym = 0.9 * a + 0.1 * a[::-1]
                a_sym = normalize(a_sym)
                F_sym, _ = objective_and_b(a_sym)
                if F_sym <= F + 1e-12:
                    z = np.log(np.maximum(a_sym, 1e-18))
                    z -= np.mean(z)

            # Occasional immediate local peak shave
            if iter_count % 70 == 0 and (time.time() - best_time) > 1.5:
                a_now = softmax(z)
                F_now, b_now = objective_and_b(a_now)
                a_shave = local_peak_shave(a_now, b_now, delta=1e-3)
                F_shave, _ = objective_and_b(a_shave)
                if F_shave < F_now - 1e-12:
                    z = np.log(np.maximum(a_shave, 1e-18))
                    z -= np.mean(z)

            # Early exit if close to deadline
            if time.time() >= local_deadline:
                break

        return best_a

    # Build seeds
    seeds = []

    # Width-swept arch family
    for s in np.linspace(0.65, 1.35, 11):
        seeds.append(seed_arch(scale=float(s)))

    # Other shapes
    seeds.append(seed_uniform())
    for wscale in [0.85, 1.0, 1.15]:
        seeds.append(seed_triangular(width_scale=wscale))
    for sgs in [0.8, 1.0, 1.2]:
        seeds.append(seed_gaussian(sigma_scale=sgs))
    seeds.append(seed_raised_cosine(width_scale=1.0))
    seeds.append(seed_tukey(alpha=0.5))
    seeds.append(seed_tukey(alpha=0.8))
    seeds.append(seed_blackman())
    seeds.append(seed_kaiser(beta=8.0))

    # Quick peak-shave on best arch seed (immediate accepted move)
    def quick_shave_seed(a_seed: np.ndarray) -> np.ndarray:
        F0, b0 = objective_and_b(a_seed)
        a1 = local_peak_shave(a_seed, b0, delta=1e-3)
        F1, _ = objective_and_b(a1)
        return a1 if F1 < F0 else a_seed

    # Evaluate seeds and pick top candidates
    seed_scores = []
    for a_seed in seeds:
        seed_scores.append(objective_and_b(a_seed)[0])
    order = np.argsort(seed_scores)
    seeds_sorted = [seeds[i] for i in order]

    # Improve the single best seed using quick shave and add jittered variants
    best_seed = quick_shave_seed(seeds_sorted[0])
    # Jitter variants
    jittered = []
    z0 = np.log(np.maximum(best_seed, 1e-18))
    z0 -= np.mean(z0)
    for sig in [0.02, 0.04, 0.08]:
        noise = rng.normal(0.0, sig, size=n)
        a_jit = softmax(z0 + noise)
        jittered.append(a_jit)
    seeds_sorted = [best_seed] + jittered + seeds_sorted[1:]

    # Allocate time: bias towards best seeds
    remaining_time = t_deadline - time.time()
    if remaining_time <= 0.02:
        # Return best seed if no time
        return normalize(seeds_sorted[0])

    # We'll optimize top K seeds with split time
    num_to_optimize = min(5, len(seeds_sorted))
    # Distribution: 40%, 25%, 15%, 12%, 8% (normalized)
    alloc_fracs = [0.40, 0.25, 0.15, 0.12, 0.08][:num_to_optimize]
    total_frac = sum(alloc_fracs)
    alloc_fracs = [f / total_frac for f in alloc_fracs]

    # Track best overall
    best_overall_a = seeds_sorted[0]
    best_overall_F, _ = objective_and_b(best_overall_a)

    for idx in range(num_to_optimize):
        if time.time() >= t_deadline:
            break
        # Dynamically compute budget as a fraction of remaining time
        budget = max(0.0, (t_deadline - time.time()) * alloc_fracs[idx])
        if budget <= 0.01:
            continue
        candidate = optimize_from_seed(seeds_sorted[idx], time_budget=budget)
        F_cand, _ = objective_and_b(candidate)
        if F_cand < best_overall_F - 1e-12:
            best_overall_F = F_cand
            best_overall_a = candidate

        # After best seed pass, run a short extra polish on a perturbed version if time remains
        if time.time() < t_deadline and idx == 0:
            budget2 = max(0.0, (t_deadline - time.time()) * 0.18)
            if budget2 > 0.01:
                zbest = np.log(np.maximum(best_overall_a, 1e-18))
                zbest -= np.mean(zbest)
                noise = rng.normal(0.0, 0.03, size=n)
                a_pert = softmax(zbest + noise)
                candidate2 = optimize_from_seed(a_pert, time_budget=budget2)
                F_cand2, _ = objective_and_b(candidate2)
                if F_cand2 < best_overall_F - 1e-12:
                    best_overall_F = F_cand2
                    best_overall_a = candidate2

    # Final safeguard normalization and nonnegativity
    best_seq = np.maximum(best_overall_a, 0.0)
    best_seq = best_seq / np.sum(best_seq)
    return best_seq
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
    return list(search_for_best_sequence())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```
