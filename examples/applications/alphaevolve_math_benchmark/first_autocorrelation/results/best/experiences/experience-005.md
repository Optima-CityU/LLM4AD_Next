Proof-anchored baseline plus targeted multiplicative-weights updates under simplex projection, validated by recorded metrics.

- Uniform Simplex Optimizer with Proof-Based Initialization: The method anchored the search with a theoretically motivated uniform initialization on the probability simplex and kept it as a fallback candidate, ensuring nonnegativity, fixed sum, and immediate compliance with evaluator security checks.
- search_for_best_sequence() (solve.py): The function applied multiplicative-weights subgradient updates projected onto the simplex and explicitly targeted the top autoconvolution peaks, running multiple short restarts from random and perturbed baselines while always retaining the best-scoring sequence found.
- Uniform Simplex Optimizer with Proof-Based Initialization: In the recorded run, the approach produced a valid sequence with upper_bound 1.6363383124465145, target_ratio 0.9199197919832378, Score 0.9199197919832378, and eval_time 0.4134191040066071 seconds.
- Strategy and structure of Uniform Simplex Optimizer with Proof-Based Initialization: For future designs facing max-of-convolution objectives under simplex constraints, reuse the pattern of combining a proof-backed uniform baseline with multiplicative-weights updates that suppress the largest convolution coefficients and employ short multi-restart search for robustness and quick convergence.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json
import time
from typing import Tuple

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """
    Search for a nonnegative step sequence (probability vector) that minimizes
    the evaluation 2 * n * max(convolve(a, a)) / (sum(a)^2), where n is length.
    The search uses a safe baseline and a lightweight multiplicative-weights
    subgradient descent that targets the top peaks of the autoconvolution.

    Strategy:
    - Start from a strong baseline (the inherited parent construction) to ensure
      we do not regress.
    - Run multiple short optimization restarts (random and perturbed baselines).
      Each uses multiplicative-weights updates guided by the subgradient of
      the max autoconvolution coefficient (and a few of the top peaks) while
      projecting implicitly onto the simplex (sum = 1, nonnegative).
    - Keep the best sequence found (with the smallest score).

    Notes:
    - We also include the uniform vector as a candidate (for safety), but the
      search will not select it unless it is genuinely best (which it should
      not be for this objective).
    - The optimization is time-bounded (a few seconds) to remain efficient
      and robust in evaluation environments.
    """

    rng = np.random.default_rng(123456789)
    dimension = 600
    # Build baseline (parent implementation), normalized to sum to 1.
    baseline = _baseline_parent_profile(dimension)
    baseline_score = _fast_eval(baseline)

    # Uniform candidate (for completeness/safety)
    uniform = np.full(dimension, 1.0 / dimension, dtype=np.float64)
    uniform_score = _fast_eval(uniform)

    # Best-so-far tracker
    best_seq = baseline.copy()
    best_score = baseline_score

    # Keep the best between baseline and uniform to start
    if uniform_score < best_score:
        best_seq, best_score = uniform.copy(), uniform_score

    # Optimization parameters
    time_budget_sec = 3.0  # keep tight to be robust yet effective
    start_time = time.time()

    # Multiplicative-weights (entropic mirror descent) parameters
    # Step size tuned experimentally; small-to-moderate to ensure stability.
    eta = 3.0
    # Number of restarts and iterations per restart
    num_restarts = 8
    iters_per_restart = 160

    # Consider top K peaks in autoconvolution to reduce simultaneously
    top_k_peaks = 3

    # Try a mix of restarts: random and perturbed baselines
    for r in range(num_restarts):
        if time.time() - start_time > time_budget_sec:
            break

        if r % 3 == 0:
            # Random positive vector, smoothed and normalized
            a = rng.random(dimension) + 1e-9
            # Apply a light smoothing to avoid spikes: average with neighbors
            a = 0.5 * a + 0.25 * np.roll(a, 1) + 0.25 * np.roll(a, -1)
            a = np.maximum(a, 0)
            a /= np.sum(a)
        elif r % 3 == 1:
            # Baseline plus small noise
            noise = 0.02 * (rng.random(dimension) - 0.5)
            a = baseline + noise
            a = np.maximum(a, 0)
            s = np.sum(a)
            a = a / s if s > 0 else baseline.copy()
        else:
            # Start near baseline but slightly skewed to break symmetry
            skew = np.linspace(0.98, 1.02, dimension)
            a = baseline * skew
            a = np.maximum(a, 0)
            a /= np.sum(a)

        # Track best within this restart as well
        local_best = a.copy()
        local_best_score = _fast_eval(a)

        # Iterative multiplicative-weights updates
        for t in range(iters_per_restart):
            if time.time() - start_time > time_budget_sec:
                break

            # Compute autoconvolution b = a * a
            b = np.convolve(a, a)
            # Indices of the top peaks
            # Note: We desire the largest values; argpartition is efficient.
            if top_k_peaks >= len(b):
                peak_indices = np.argsort(-b)
            else:
                peak_indices = np.argpartition(b, -top_k_peaks)[-top_k_peaks:]
                # sort these top indices by their values descending
                peak_indices = peak_indices[np.argsort(-b[peak_indices])]

            # Build subgradient signal: g[i] accumulates contributions from top peaks
            g = np.zeros_like(a)
            max_b = b[peak_indices[0]]
            # Weight peaks relative to the top to prioritize the worst offenders
            for kidx in peak_indices:
                # weight emphasizing peaks near the max, discouraging smaller peaks
                # ensures numeric stability and focuses on the dominating terms
                w = (b[kidx] / (max_b + 1e-16)) ** 1.0

                # For each kidx, g[i] += 2 * w * a[kidx - i] on valid ranges
                # Valid i are those with 0 <= i <= n-1 and 0 <= kidx - i < n
                # i in [max(0, kidx-(n-1)), min(n-1, kidx)]
                i_start = max(0, kidx - (dimension - 1))
                i_end = min(dimension - 1, kidx)
                if i_end >= i_start:
                    # Corresponding j index runs descending: j = kidx - i
                    j_start = kidx - i_start
                    j_end = kidx - i_end
                    # j_start >= j_end; pick the slice and reverse
                    # slice indices for a[j_end : j_start + 1], then reverse
                    j_slice = a[j_end : j_start + 1][::-1]
                    # Add contribution
                    g[i_start : i_end + 1] += 2.0 * w * j_slice

            # Multiplicative weights update: a <- a * exp(-eta * g)
            # Stabilize exponents to avoid under/overflow: subtract max exponent
            exp_arg = -eta * g
            exp_arg -= np.max(exp_arg)
            a *= np.exp(exp_arg)
            # Normalize to maintain sum 1
            sum_a = np.sum(a)
            if sum_a <= 0:
                # Extremely unlikely, but safeguard to keep feasible
                a = baseline.copy()
            else:
                a /= sum_a

            # Evaluate current and store best
            current_score = _fast_eval(a)
            if current_score < local_best_score:
                local_best_score = current_score
                local_best = a.copy()

        # After restart, compare local best with global best
        if local_best_score < best_score:
            best_score = local_best_score
            best_seq = local_best.copy()

    # Safety: ensure nonnegative and normalized
    best_seq = np.maximum(best_seq, 0)
    s = np.sum(best_seq)
    if s > 0:
        best_seq /= s
    else:
        # Fallback to baseline if something went wrong
        best_seq = baseline

    return best_seq


def _baseline_parent_profile(dimension: int) -> np.ndarray:
    """
    Reproduce the inherited parent profile:
    step_function = 1.0 + 4.0 * |x| - 16.0 * x^2 on x in [-1/4, 1/4], symmetrized,
    clipped at 0, and normalized.
    """
    interval_start = -1 / 4
    interval_end = 1 / 4
    x = np.linspace(interval_start, interval_end, dimension)
    step_function = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
    # Symmetrize explicitly
    step_function = (step_function + step_function[::-1]) / 2
    step_function = np.maximum(step_function, 0)
    total = np.sum(step_function)
    if total > 0:
        step_function /= total
    else:
        # degenerate safeguard: use uniform
        step_function = np.full(dimension, 1.0 / dimension, dtype=np.float64)
    return step_function


def _fast_eval(a: np.ndarray) -> float:
    """
    Fast evaluation of the objective for sequences with sum 1:
    evaluates 2 * n * max(convolve(a, a)) since sum(a) ≈ 1.

    Assumes a is nonnegative and sums to ~1; safe for search comparisons.
    """
    n = len(a)
    # Protect against near-zero sums
    s = float(np.sum(a))
    if s < 1e-12:
        return float("inf")
    b = np.convolve(a, a)
    max_b = float(np.max(b))
    return 2.0 * n * max_b / (s * s + 1e-32)
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
