Generation 7 introduced a Top-K log-sum-exp surrogate for the smoothed max of the self-convolution with an adaptive K schedule, yielding sparse, focused gradients via FFT and maintaining runtime efficiency.

- Top-K LogSumExp Surrogate for Minimax Convolution: The method replaces the full log-sum-exp over all 2n−1 convolution entries with L_tau^K(a)=2n*τ*log Σ_{k∈TopK(b)} exp(b[k]/τ), selecting the K largest entries of b=conv(a,a) via np.argpartition and computing gradients as 2*corr(p,a) with p nonzero only on Top-K and normalized, implemented using FFT.
- Adaptive K schedule tied to temperature continuation: During the initial high-τ phases it sets K_high=max(16, int(0.10*(2n−1))) and during later low-τ phases it sets K_low=max(8, int(0.02*(2n−1))), concentrating gradient pressure on dominant peaks at moderate τ to sharpen the minimax focus while preserving smoothness for stable optimization.
- Generation 7 metrics: The run reported validity=1.0 and eval_time≈1.958s with target_ratio≈0.987 and upper_bound≈1.52506, indicating the approach maintained correctness and comparable runtime consistent with the sparse Top-K/FFT design.
- Implementation in solve.py: The change is localized to the surrogate objective and gradient, leaving the softmax parameterization, Adam/AMSGrad, TV and entropy annealing, multi-start, multiresolution upsampling, and peak-shaving intact, making the strategy easy to reuse in similar pipelines.
- Softmax-Projected Minimax Convolution Flattener (SPMCF): Parameterizes the step sequence as a = softmax(w) to enforce nonnegativity and sum(a)=1, aligning with the evaluator’s checks (nonnegative entries and sum_a >= 0.01) and reducing the objective to a constant factor times max_k of the self-convolution b = a * a.
- Softmax-Projected Minimax Convolution Flattener (SPMCF): Minimizes a smooth-max surrogate F_tau(a) = 2n * tau * log(sum_k exp(b[k]/tau)) with a continuation schedule tau = [0.08, 0.04, 0.02, 0.01], providing stable gradients early and focusing on the true maximum as tau decreases.
- Softmax-Projected Minimax Convolution Flattener (SPMCF): Blends gradients from the top-K (e.g., 3–5) largest entries of b simultaneously, which pushes down multiple peaks at once and reduces peak-swapping instability during minimax optimization.
- search_for_best_sequence: Computes b via FFT-based convolution and runs Adam over thousands of iterations at n = 600 within the time budget, enabling fast repeated evaluation and refinement of candidates.
- Softmax-Projected Minimax Convolution Flattener (SPMCF): Reported metrics include upper_bound = 1.53754412799451, target_ratio = 0.9790288113313748, validity = 1.0, eval_time = 20.57834034995176, and Error = N/A in the presented run.

```python
#!/usr/bin/env python3
"""Advanced search for a step function minimizing the autocorrelation-based objective.

This script implements a direct minimization of:
  J(a) = 2 * n * max(conv(a, a)) / (sum(a)^2)

Key features:
- Softmax parameterization for nonnegativity and unit-sum constraints.
- Smooth surrogate for the max via Top-K log-sum-exp with temperature continuation.
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


def _grad_surrogate(
    a: np.ndarray,
    tau: float,
    lam_tv: float,
    lam_ent: float,
    topk: int | None = None,
) -> Tuple[float, np.ndarray]:
    """Compute Top-K surrogate objective and gradient w.r.t a:

       L_tau^K(a) = 2n * tau * log Σ_{k ∈ TopK(b)} exp(b[k]/tau)
       where b = conv(a, a) is the aperiodic self-convolution, and TopK(b) selects
       the K largest entries of b (if topk is None or >= len(b), this reduces to full LSE).

       Gradient uses softmax weights p_k ∝ exp(b[k]/tau) restricted to the Top-K set and zero elsewhere:
         ∂L/∂a = 2 * 2n * corr(p, a)
       plus TV and entropy regularizers.

       Returns (L_total, grad_total) with TV and entropy penalties included.
    """
    n = a.size
    # Convolution and prepare Top-K restriction on b
    b = _conv_aperiodic(a, a)  # length 2n - 1
    Lb = b.size

    # Determine whether to use Top-K or full set
    use_topk = topk is not None and topk > 0 and topk < Lb

    if use_topk:
        # Indices of top-K entries using argpartition (O(Lb))
        K = int(topk)
        idx_top = np.argpartition(b, Lb - K)[-K:]
        # Gather values and compute log-sum-exp restricted to idx_top
        b_top = b[idx_top]
        tau_eps = max(1e-300, float(tau))
        m = float(np.max(b_top))
        z = (b_top - m) / tau_eps
        ez = np.exp(z)
        sum_ez = float(np.sum(ez))
        if sum_ez <= 0.0 or not np.isfinite(sum_ez):
            # Fallback: uniform weights over the Top-K set
            p_top = np.full_like(b_top, 1.0 / K, dtype=np.float64)
            lse_top = m  # since log(sum)=log(K) but multiplied by tau ~ small; keep stable baseline
            # More precisely: m + tau * log(K), but tau may be tiny; still fine to include:
            lse_top = m + tau_eps * np.log(K)
        else:
            p_top = ez / sum_ez
            lse_top = m + tau_eps * np.log(sum_ez)

        # Build p over full support with zeros elsewhere
        p = np.zeros_like(b, dtype=np.float64)
        p[idx_top] = p_top

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

    # Gradient wrt a of Σ p[k] b[k] is 2 * corr(p, a)
    # Implement via convolution with reversed p
    pr = p[::-1]
    c = _conv_aperiodic(a, pr)  # length 3n - 2
    c_slice = c[n - 1 : 2 * n - 1][::-1]  # length n
    grad_main = 2.0 * 2.0 * n * c_slice  # 2 for symmetry, and 2n from outer coefficient

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
    K_high = max(16, int(0.10 * Lb))  # early phases
    K_low = max(8, int(0.02 * Lb))    # later phases
    # Number of early (high-τ) phases to use K_high
    num_high_phases = 3 if len(tau_schedule) >= 5 else 2

    for t_idx, tau in enumerate(tau_schedule):
        # Select K based on phase: early -> K_high, later -> K_low
        K = K_high if t_idx < num_high_phases else K_low

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

            # Objective and gradient wrt a using Top-K surrogate
            L_tau, grad_a = _grad_surrogate(a, tau, lam_tv=lam_tv, lam_ent=lam_ent, topk=K)
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
    """Run a multi-start, multi-resolution optimizer to minimize the objective using Top-K surrogate."""
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

```python
#!/usr/bin/env python3
"""Optimized step function search for minimizing autocorrelation-based evaluation."""

import json
import time
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def search_for_best_sequence(time_budget: float = 990.0, random_seed: int = 42) -> np.ndarray:
    """
    Optimize a nonnegative step sequence on the simplex to minimize the evaluation function.
    Uses a softmax parameterization with Adam optimizer and a smooth-max surrogate of the
    discrete self-convolution's maximum. Includes multi-peak blending and small regularizers.

    Args:
        time_budget: Allowed wall-clock time in seconds before returning the best found.
        random_seed: Seed for reproducibility in initialization perturbations.

    Returns:
        Best found sequence as a NumPy array of length n, nonnegative and summing to 1.
    """
    rng = np.random.default_rng(random_seed)
    start_time = time.time()

    # Problem dimension: based on current best report with 600 intervals
    n = 600

    # Hyperparameters
    # Smooth-max temperatures for continuation (higher -> smoother)
    tau_schedule = [0.08, 0.04, 0.02, 0.01]
    # Number of iterations per stage; tuned to fit within time budget
    iters_per_stage = [700, 700, 700, 700]
    # Blend factor for top-K largest convolution peaks (0 -> pure smooth-max, 1 -> pure top-K)
    alpha_top_schedule = [0.2, 0.3, 0.4, 0.5]
    # Number of peaks to include in top-K blending
    top_k = 5
    # Learning rate for Adam
    lr = 0.05
    beta1 = 0.9
    beta2 = 0.999
    eps = 1e-8
    # Tiny regularization to stabilize and promote flatness
    lambda_tv = 5e-6  # total variation (squared-diff) regularizer on a
    lambda_var = 2e-5  # variance of b regularizer

    # Utility functions

    def softmax(w: np.ndarray) -> np.ndarray:
        """Stable softmax producing nonnegative sequence that sums to 1."""
        m = np.max(w)
        z = np.exp(w - m)
        return z / np.sum(z)

    def next_pow_two(m: int) -> int:
        """Return the smallest power-of-two >= m."""
        return 1 << ((m - 1).bit_length())

    def self_convolution(a: np.ndarray) -> np.ndarray:
        """
        Compute b = a * a (linear convolution) using real FFT.
        Returns length 2n-1 convolution.
        """
        L = 2 * n - 1
        fft_len = next_pow_two(L)
        A = np.fft.rfft(a, fft_len)
        b_full = np.fft.irfft(A * A, fft_len)
        return b_full[:L]

    def conv_weights_with_arev(weights: np.ndarray, a: np.ndarray) -> np.ndarray:
        """
        Compute c[j] = sum_k weights[k] * a[k - j] with index validity handled by convolution.

        Equivalent to: c[j] = (weights convolve a_rev)[j + (n - 1)],
        where a_rev is reversed a. This uses FFT for speed.

        Args:
            weights: Array of length 2n-1.
            a: Array of length n.

        Returns:
            c: Array of length n.
        """
        Lw = 2 * n - 1
        La = n
        Lc = Lw + La - 1  # = 3n - 2
        fft_len = next_pow_two(Lc)
        a_rev = a[::-1]
        Wr = np.fft.rfft(weights, fft_len)
        Ar = np.fft.rfft(a_rev, fft_len)
        d = np.fft.irfft(Wr * Ar, fft_len)[:Lc]
        # Extract the segment corresponding to j + (n - 1)
        return d[(n - 1):(n - 1 + n)]

    def evaluate_sequence(sequence: List[float]) -> float:
        """
        Secure evaluation function per the task specification.
        Returns np.inf if invalid.
        """
        # Verify that the input is a list
        if not isinstance(sequence, list):
            return np.inf

        # Reject empty lists
        if not sequence:
            return np.inf

        # Check each element in the list for validity
        for x in sequence:
            # Reject boolean types and any other non-integer/non-float types
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                return np.inf
            # Reject NaN or infinity
            if np.isnan(x) or np.isinf(x):
                return np.inf

        # Convert to floats
        seq = [float(x) for x in sequence]
        # Clamp negative and too-large values
        seq = [max(0.0, x) for x in seq]
        seq = [min(1000.0, x) for x in seq]

        total = np.sum(seq)
        if total < 0.01:
            return np.inf

        b = np.convolve(seq, seq)
        return float(2 * len(seq) * np.max(b) / (total**2))

    # Seed initial sequences (nonnegative, sum 1)
    def seed_sequences() -> List[np.ndarray]:
        seeds = []

        # Uniform
        a_uniform = np.ones(n, dtype=float)
        a_uniform /= np.sum(a_uniform)
        seeds.append(a_uniform)

        # Hann window
        i = np.arange(n, dtype=float)
        a_hann = 0.5 - 0.5 * np.cos(2.0 * np.pi * i / (n - 1))
        a_hann = np.maximum(a_hann, 0.0)
        a_hann /= np.sum(a_hann)
        seeds.append(a_hann)

        # Cosine squared centered
        x = (i - (n - 1) / 2.0) / (n - 1)
        a_cos2 = np.cos(np.pi * x) ** 2
        a_cos2 = np.maximum(a_cos2, 0.0)
        a_cos2 /= np.sum(a_cos2)
        seeds.append(a_cos2)

        # Beta(1.5, 1.5) shape on [0,1] mapped to indices
        t = i / (n - 1)
        a_beta = np.power(t + 1e-12, 0.5) * np.power(1.0 - t + 1e-12, 0.5)
        a_beta = np.maximum(a_beta, 0.0)
        a_beta /= np.sum(a_beta)
        seeds.append(a_beta)

        # Trapezoid: ramp up, plateau, ramp down
        plateau_frac = 0.4
        ramp_frac = (1.0 - plateau_frac) / 2.0
        ramp_len = int(np.floor(ramp_frac * n))
        plateau_len = n - 2 * ramp_len
        a_trap = np.zeros(n, dtype=float)
        # Rising ramp
        if ramp_len > 0:
            a_trap[:ramp_len] = np.linspace(0.0, 1.0, ramp_len, endpoint=False)
        # Plateau
        a_trap[ramp_len:ramp_len + plateau_len] = 1.0
        # Falling ramp
        if ramp_len > 0:
            a_trap[ramp_len + plateau_len:] = np.linspace(1.0, 0.0, ramp_len, endpoint=False)
        a_trap = np.maximum(a_trap, 0.0)
        a_trap /= np.sum(a_trap)
        seeds.append(a_trap)

        # Slightly perturbed symmetric seed to break symmetry traps
        a_pert = a_hann.copy()
        a_pert += 0.01 * rng.normal(size=n)
        a_pert = np.maximum(a_pert, 0.0)
        a_pert /= np.sum(a_pert)
        seeds.append(a_pert)

        # Baseline polynomial-like seed derived from project baseline
        interval_start = -1 / 4
        interval_end = 1 / 4
        xgrid = np.linspace(interval_start, interval_end, n)
        a_poly = 1.0 + 4.0 * np.abs(xgrid) - 16.0 * xgrid**2
        a_poly = (a_poly + a_poly[::-1]) / 2.0
        a_poly = np.maximum(a_poly, 0.0)
        a_poly /= np.sum(a_poly)
        seeds.append(a_poly)

        return seeds

    # Track global best
    best_a = None
    best_val = np.inf

    # Evaluate a sequence through the evaluation function
    def eval_a(a: np.ndarray) -> float:
        return evaluate_sequence(list(a.tolist()))

    # Optimization loop over seeds
    for seed in seed_sequences():
        # Initialize w from seed via log
        w = np.log(seed + 1e-12)
        m = np.zeros_like(w)
        v = np.zeros_like(w)
        t_adam = 0

        # Evaluate initial
        a = softmax(w)
        val = eval_a(a)
        if val < best_val:
            best_val = val
            best_a = a.copy()

        # Run continuation stages
        for stage_idx, (tau, iters, alpha_top) in enumerate(
            zip(tau_schedule, iters_per_stage, alpha_top_schedule)
        ):
            for it in range(iters):
                # Time check
                if time.time() - start_time > time_budget:
                    # Return best found so far
                    return best_a if best_a is not None else a

                # Current sequence
                a = softmax(w)

                # Compute convolution b = a * a
                b = self_convolution(a)
                Lb = b.shape[0]

                # Smooth-max weights over b
                b_shift = b - np.max(b)
                w_soft = np.exp(b_shift / max(tau, 1e-6))
                w_soft_sum = np.sum(w_soft)
                if w_soft_sum <= 0.0 or not np.isfinite(w_soft_sum):
                    # Reset weights to uniform if numerical issues
                    w_soft = np.ones(Lb) / Lb
                else:
                    w_soft /= w_soft_sum

                # Top-K peak blending
                idx_sorted = np.argsort(-b)  # descending
                w_top = np.zeros_like(b)
                w_top[idx_sorted[:top_k]] = 1.0 / top_k

                # Combined weights
                weights = (1.0 - alpha_top) * w_soft + alpha_top * w_top

                # Gradient wrt a from smooth-max component:
                # grad_j = 2*n * sum_k weights[k] * ∂b[k]/∂a[j] = 2*n * sum_k weights[k] * 2*a[k-j]
                # The inner sum computed via conv(weights, a_rev) with appropriate slice.
                c = conv_weights_with_arev(weights, a)  # length n
                g_a_smax = (2.0 * n) * 2.0 * c  # factor from objective

                # Variance regularizer on b to flatten peaks: Var(b) = mean((b - m)^2)
                m_b = np.mean(b)
                v_b = 2.0 * (b - m_b) / Lb  # gradient of Var(b) w.r.t. b
                c_var = conv_weights_with_arev(v_b, a)
                g_a_var = 2.0 * lambda_var * c_var

                # Total variation (squared difference) regularizer on a
                d = a[1:] - a[:-1]
                g_tv = np.zeros_like(a)
                g_tv[:-1] -= d
                g_tv[1:] += d
                g_a_tv = lambda_tv * g_tv

                # Combine gradients on a
                g_a = g_a_smax + g_a_var + g_a_tv

                # Convert gradient on a to gradient on w via softmax jacobian
                # g_w[j] = a[j] * (g_a[j] - <g_a, a>)
                ga_dot_a = float(np.dot(g_a, a))
                g_w = a * (g_a - ga_dot_a)

                # Adam update
                t_adam += 1
                m = beta1 * m + (1.0 - beta1) * g_w
                v = beta2 * v + (1.0 - beta2) * (g_w * g_w)
                m_hat = m / (1.0 - beta1**t_adam)
                v_hat = v / (1.0 - beta2**t_adam)
                w -= lr * (m_hat / (np.sqrt(v_hat) + eps))

                # Occasional mild smoothing on w to reduce tiny oscillations
                if (it + 1) % 200 == 0:
                    # 3-point moving average: w_i <- 0.25*w_{i-1} + 0.5*w_i + 0.25*w_{i+1}
                    w = smooth_w(w)

                # Track best solution
                a_curr = softmax(w)
                val_curr = eval_a(a_curr)
                if val_curr < best_val:
                    best_val = val_curr
                    best_a = a_curr.copy()

            # End of stage loop

        # End of seed loop stage

    # If nothing improved (unlikely), return last a; else return best
    return best_a if best_a is not None else a


def smooth_w(w: np.ndarray) -> np.ndarray:
    """
    Apply a small 3-point moving average smoothing to parameter vector w.
    Keeps endpoints smoothed with adjacent values.
    """
    n = w.shape[0]
    if n < 3:
        return w.copy()
    w_sm = w.copy()
    w_sm[1:-1] = 0.25 * w[:-2] + 0.5 * w[1:-1] + 0.25 * w[2:]
    # Endpoints: simple average with neighbor
    w_sm[0] = 0.6 * w[0] + 0.4 * w[1]
    w_sm[-1] = 0.6 * w[-1] + 0.4 * w[-2]
    return w_sm
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
    sequence = [max(0.0, x) for x in sequence]

    # Protect against numbers that are too large
    sequence = [min(1000.0, x) for x in sequence]

    n = len(sequence)
    b_sequence = np.convolve(sequence, sequence)
    max_b = float(np.max(b_sequence))
    sum_a = float(np.sum(sequence))

    # Protect against the case where the sum is too close to zero
    if sum_a < 0.01:
        return np.inf

    return float(2.0 * n * max_b / (sum_a**2))


def run_search_for_best_sequence() -> list[float]:
    """
    Run the optimizer with a 1000-second budget and return the best sequence found.
    """
    # Leave a small margin below 1000 seconds to ensure timely return
    best_sequence = search_for_best_sequence(time_budget=990.0)
    return list(best_sequence.tolist())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```
