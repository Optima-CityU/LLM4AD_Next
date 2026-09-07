Actionable design pattern that improved the step-sequence search under a max autoconvolution objective.

- Multi-peak mass transport with acceptance and adaptive peak set: Replacing a single-peak transport with a multi-peak transport that aggregates over the near-maximum autoconvolution set K = {k : b[k] >= (1 − delta_peak) * max(b)}, shaves high-contributing index pairs (top_pair_frac ≈ 0.04) with multiplicative factor (1 − alpha) per hit (alpha ≈ 0.035), and redistributes removed mass to low-coupling indices outside a small neighborhood (low_couple_frac ≈ 0.30, neighborhood_radius ≈ 2) reduced oscillations between adjacent peaks. An acceptance filter that applies the transport only when the true objective strictly decreases, together with adapting delta_peak based on peak-set breadth (shrink focus if |K| > 0.08*(2n − 1), broaden if |K| < 0.02*(2n − 1), with 0.01 <= delta_peak <= 0.06), provided reliable safeguards against regressions. In this run, this design yielded upper_bound = 1.5671095742502876, target_ratio = 0.9605582307287878, validity = 1.0, and eval_time = 11.466579170024488; future designs facing multiple comparable autoconvolution peaks should reuse the multi-peak aggregation, acceptance gating on evaluate_sequence, and adaptive near-peak tolerance at similar parameter scales.

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

    This implementation augments the inherited multiplicative-weights (entropic
    mirror descent) with a multi-peak mass-transport step that simultaneously
    accounts for all near-maximum autoconvolution peaks:

      - Dynamic peak set: K = {k : b[k] >= (1 - delta_peak) * max(b)}.
      - Per-index coupling score s[i] = sum_{k in K, valid} a[k - i].
      - Top-pair contribution score p[i] = max_{k in K, valid} a[i] * a[k - i].
      - Shaving: select top_pair_frac fraction of indices by p[i] as the shaved
        core; for each selected i and for every k in K (with j = k - i valid),
        increment shave_counts[i] and shave_counts[j] by 1 (if i == j, once).
        Apply multiplicative shaving with factor (1 - alpha)^{shave_counts[idx]}.
      - Redistribution: compute removed_mass = sum(a) - sum(a_shaved). Choose
        recipients as indices in the bottom low_couple_frac quantile of s[i],
        excluding a small neighborhood around the shaved core and their partners.
        Fallback to outside-neighborhood, else all indices if empty. Redistribute
        removed_mass uniformly to recipients and renormalize to sum 1 to get a
        candidate vector.
      - Acceptance filter: evaluate the objective before and after the transport;
        keep the candidate only if it strictly improves the true objective.
      - Adaptive near-peak tolerance delta_peak: based on the breadth of K,
        adapt within 0.01 <= delta_peak <= 0.06 as:
          * if |K| > 0.08 * (2n - 1): delta_peak <- min(1.5 * delta_peak, 0.06)
          * if |K| < 0.02 * (2n - 1): delta_peak <- max(0.8 * delta_peak, 0.01)

    The vector a is maintained on the simplex (sum = 1, nonnegative).
    The objective is evaluated via _fast_eval, which assumes sum(a) ~ 1.
    """

    rng = np.random.default_rng(123456789)
    dimension = 600

    # Baseline (parent profile), normalized to sum to 1.
    baseline = _baseline_parent_profile(dimension)
    baseline_score = _fast_eval(baseline)

    # Uniform candidate (safety)
    uniform = np.full(dimension, 1.0 / dimension, dtype=np.float64)
    uniform_score = _fast_eval(uniform)

    # Best-so-far tracker
    best_seq = baseline.copy()
    best_score = baseline_score

    if uniform_score < best_score:
        best_seq, best_score = uniform.copy(), uniform_score

    # Global time budget: modestly increased but still well below the hard cap
    time_budget_sec = 12.0
    start_time = time.time()

    # Multiplicative-weights (entropic mirror descent) parameters
    eta0 = 3.0  # initial stepsize; decays with 1/sqrt(t+1)

    # Optimization schedule: restarts/iterations, guarded by time
    num_restarts = 16
    iters_per_restart = 320

    # Near-peak tolerance delta_peak with adaptive bounds
    delta_peak = 0.02  # initial tolerance (2%)
    delta_min, delta_max = 0.01, 0.06

    # Mass-transport parameters (defaults per spec)
    top_pair_frac = 0.04       # shave top 4% by p[i]
    low_couple_frac = 0.30     # recipients from bottom 30% by s[i]
    shave_alpha = 0.035        # multiplicative shaving factor per hit
    neighborhood_radius = 2    # exclude small neighborhood for redistribution

    # Try a mix of restarts: random, noisy baseline, skewed baseline
    for r in range(num_restarts):
        if time.time() - start_time > time_budget_sec:
            break

        if r % 3 == 0:
            # Random positive vector, lightly smoothed and normalized
            a = rng.random(dimension) + 1e-12
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
            # Slightly skewed baseline to break symmetry
            skew = np.linspace(0.98, 1.02, dimension)
            a = baseline * skew
            a = np.maximum(a, 0)
            a /= np.sum(a)

        # Track best within this restart
        local_best = a.copy()
        local_best_score = _fast_eval(a)

        for t in range(iters_per_restart):
            if time.time() - start_time > time_budget_sec:
                break

            # Compute autoconvolution b = a * a
            b = np.convolve(a, a)
            max_b = float(np.max(b))

            # Dynamic peak set: all k with value within (1 - delta_peak) of max
            peak_indices = np.where(b >= (1.0 - delta_peak) * max_b)[0]

            # Build subgradient signal g for entropic mirror descent
            g = np.zeros_like(a)
            # Accumulate contributions from each near-maximum peak
            for kidx in peak_indices:
                w = (b[kidx] / (max_b + 1e-16)) ** 1.0  # emphasize worst peaks
                # Valid i satisfy: 0 <= i <= n-1 and 0 <= kidx - i < n
                i_start = max(0, kidx - (dimension - 1))
                i_end = min(dimension - 1, kidx)
                if i_end >= i_start:
                    # j = kidx - i
                    j_start = kidx - i_start
                    j_end = kidx - i_end
                    j_slice = a[j_end : j_start + 1][::-1]
                    g[i_start : i_end + 1] += 2.0 * w * j_slice

            # Adaptive step size
            eta_t = eta0 / np.sqrt(t + 1.0)

            # Multiplicative-weights update: a <- a * exp(-eta_t * g), then renormalize
            exp_arg = -eta_t * g
            exp_arg -= np.max(exp_arg)  # stabilize
            a *= np.exp(exp_arg)
            sum_a = np.sum(a)
            if sum_a <= 0:
                a = baseline.copy()
            else:
                a /= sum_a

            # Multi-peak mass-transport candidate (uses current K = peak_indices)
            pre_score = _fast_eval(a)
            a_candidate = _multi_peak_mass_transport(
                a=a,
                K=peak_indices,
                alpha=shave_alpha,
                top_pair_frac=top_pair_frac,
                low_couple_frac=low_couple_frac,
                neighborhood_radius=neighborhood_radius,
            )
            cand_score = _fast_eval(a_candidate)

            # Acceptance filter: keep only if strictly improves the objective
            if cand_score < pre_score - 1e-12:
                a = a_candidate
                current_score = cand_score
            else:
                current_score = pre_score

            # Adaptive delta_peak based on breadth of K (clamped to [0.01, 0.06])
            total_b_len = 2 * dimension - 1
            breadth = len(peak_indices)
            if breadth > 0.08 * total_b_len:
                delta_peak = min(1.5 * delta_peak, delta_max)
            elif breadth < 0.02 * total_b_len:
                delta_peak = max(0.8 * delta_peak, delta_min)

            # Keep local best
            if current_score < local_best_score:
                local_best_score = current_score
                local_best = a.copy()

        # Update global best
        if local_best_score < best_score:
            best_score = local_best_score
            best_seq = local_best.copy()

    # Safety: ensure nonnegative and normalized
    best_seq = np.maximum(best_seq, 0)
    s = np.sum(best_seq)
    if s > 0:
        best_seq /= s
    else:
        best_seq = baseline

    return best_seq


def _multi_peak_mass_transport(
    a: np.ndarray,
    K: np.ndarray,
    alpha: float = 0.035,
    top_pair_frac: float = 0.04,
    low_couple_frac: float = 0.30,
    neighborhood_radius: int = 2,
) -> np.ndarray:
    """
    Multi-peak mass-transport step that simultaneously targets all near-maximum peaks.

    Inputs:
      - a: current probability vector (sum ~ 1, nonnegative).
      - K: array of near-maximum peak indices in [0, 2n - 2].
      - alpha: per-hit multiplicative shaving factor (1 - alpha).
      - top_pair_frac: fraction of indices selected by p[i] as the shaved core.
      - low_couple_frac: low-quantile cutoff for recipient selection by s[i].
      - neighborhood_radius: integer radius to exclude around the shaved core and partners.

    Procedure:
      (1) Compute, for each i in [0, n-1]:
          s[i] = sum_{k in K with 0 <= k - i < n} a[k - i]
          p[i] = max_{k in K with 0 <= k - i < n} a[i] * a[k - i]
      (2) Select top_pair_frac fraction of indices by p[i] as the shaved core.
      (3) For each selected i and every k in K with j = k - i valid,
          increment shave_counts[i] and shave_counts[j] by 1 (if i == j, increment once).
      (4) Apply multiplicative shaving: a_shaved = a * (1 - alpha)^{shave_counts}.
      (5) Redistribute the removed mass uniformly to the recipient set:
          indices in the bottom low_couple_frac quantile of s[i],
          excluding a neighborhood of the shaved core and partners.
          Fallbacks: (i) all indices outside neighborhood; (ii) all indices.
      (6) Renormalize to sum 1 and return candidate.
    """
    n = len(a)
    if K is None or len(K) == 0 or alpha <= 0.0:
        return a

    # Step (1): compute s[i] (sum of partner masses across K) and p[i] (max pair contributions)
    s_vec = np.zeros(n, dtype=np.float64)
    p_vec = np.zeros(n, dtype=np.float64)
    for k in K:
        i_start = max(0, k - (n - 1))
        i_end = min(n - 1, k)
        if i_end < i_start:
            continue
        j_start = k - i_start
        j_end = k - i_end
        j_slice = a[j_end : j_start + 1][::-1]  # partner masses for valid i
        i_slice = slice(i_start, i_end + 1)
        s_vec[i_slice] += j_slice
        # Candidate pair contributions for p[i]
        p_candidate = a[i_start : i_end + 1] * j_slice
        # Take max across K
        p_vec[i_slice] = np.maximum(p_vec[i_slice], p_candidate)

    # Step (2): select shaved core indices by top_pair_frac fraction of p[i]
    m = max(1, int(np.ceil(top_pair_frac * n)))
    if m >= n:
        top_i_idx = np.arange(n, dtype=np.int64)
    else:
        # Efficient top-m selection on p_vec
        part_idx = np.argpartition(p_vec, -m)[-m:]
        order = np.argsort(-p_vec[part_idx])
        top_i_idx = part_idx[order].astype(np.int64)

    # Step (3): build shave_counts by iterating over selected i and K
    shave_counts = np.zeros(n, dtype=np.int32)
    partners = []  # collect partners for neighborhood banning
    for i in top_i_idx:
        for k in K:
            j = k - i
            if 0 <= j < n:
                if i == j:
                    shave_counts[i] += 1
                else:
                    shave_counts[i] += 1
                    shave_counts[j] += 1
                partners.append(j)

    # Step (4): apply multiplicative shaving
    factor = np.power(1.0 - alpha, shave_counts, dtype=np.float64)
    a_shaved = a * factor

    # Removed mass
    removed_mass = float(np.sum(a) - np.sum(a_shaved))
    if removed_mass <= 0:
        # Numerical edge: nothing removed; normalize and return
        s = np.sum(a_shaved)
        return a_shaved / s if s > 0 else a_shaved

    # Step (5): select recipients by low quantile of s[i], excluding neighborhood
    # Low-couple threshold on s_vec
    thresh = np.quantile(s_vec, low_couple_frac)
    low_mask = s_vec <= thresh
    low_indices = np.nonzero(low_mask)[0]

    # Build banned neighborhood around shaved core and their partners
    shaved_core = np.unique(np.concatenate([top_i_idx, np.array(partners, dtype=np.int64)]) if partners else top_i_idx)
    banned = _expand_indices(shaved_core, n, neighborhood_radius)
    banned_set = set(int(x) for x in banned.tolist())

    # Exclude banned from low_indices
    recipients = np.array([idx for idx in low_indices if idx not in banned_set], dtype=np.int64)

    # Fallbacks
    if recipients.size == 0:
        all_idx = np.setdiff1d(np.arange(n, dtype=np.int64), banned, assume_unique=False)
        recipients = all_idx
    if recipients.size == 0:
        recipients = np.arange(n, dtype=np.int64)

    # Redistribute uniformly
    a_new = a_shaved.copy()
    add_val = removed_mass / float(recipients.size)
    np.add.at(a_new, recipients, add_val)

    # Step (6): renormalize and ensure nonnegativity
    a_new = np.maximum(a_new, 0)
    s = np.sum(a_new)
    if s > 0:
        a_new /= s

    return a_new


def _peak_mass_transport(
    a: np.ndarray,
    k: int,
    alpha: float = 0.035,
    top_pair_frac: float = 0.04,
    low_couple_frac: float = 0.30,
    neighborhood_radius: int = 2,
) -> np.ndarray:
    """
    Legacy single-peak mass transport (retained for reference/backward compatibility).
    Not used by the current search, which employs _multi_peak_mass_transport().
    """
    n = len(a)
    # Valid i range s.t. 0 <= i <= n-1 and 0 <= k - i < n
    i_start = max(0, k - (n - 1))
    i_end = min(n - 1, k)
    if i_end < i_start:
        # No valid pairs for this k; nothing to do
        return a

    i_idx = np.arange(i_start, i_end + 1)
    j_idx = k - i_idx
    # Pair contributions to b[k]
    p = a[i_idx] * a[j_idx]

    # Number of top pairs to shave (at least 1)
    m = max(1, int(np.ceil(top_pair_frac * p.size)))
    if m >= p.size:
        top_i_idx = i_idx  # shave all pairs (extreme/degenerate)
    else:
        # Efficient top-m selection
        part_idx = np.argpartition(p, -m)[-m:]
        # Order these top indices by contribution descending
        order = np.argsort(-p[part_idx])
        top_i_idx = i_idx[part_idx[order]]

    # Build shaving multiplicity per coordinate; use exponentiated factor later
    shave_counts = np.zeros(n, dtype=np.int32)
    top_j_idx = k - top_i_idx
    # Increment shave count symmetrically
    np.add.at(shave_counts, top_i_idx, 1)
    np.add.at(shave_counts, top_j_idx, 1)
    # For pairs where i == j (center), decrement one to apply only once total
    center_mask = (top_i_idx == top_j_idx)
    if np.any(center_mask):
        np.add.at(shave_counts, top_i_idx[center_mask], -1)

    # Apply multiplicative shaving
    if alpha <= 0:
        return a  # nothing to do
    factor = np.power(1.0 - alpha, shave_counts, dtype=np.float64)
    a_shaved = a * factor

    # Total removed mass to redistribute
    removed_mass = float(np.sum(a) - np.sum(a_shaved))
    if removed_mass <= 0:
        s = np.sum(a_shaved)
        return a_shaved / s if s > 0 else a_shaved

    # Identify low-coupling recipient indices (bottom quantile)
    c = a[i_idx] * a[j_idx]  # coupling strengths for indices i_idx
    # Quantile threshold for low coupling
    thresh = np.quantile(c, low_couple_frac)
    low_mask = c <= thresh
    recipients = i_idx[low_mask]

    # Exclude shaved indices and a small neighborhood to avoid immediate recoupling
    shaved_core = np.unique(np.concatenate([top_i_idx, top_j_idx]))
    banned = _expand_indices(shaved_core, n, neighborhood_radius)
    recipients = np.array([idx for idx in recipients if idx not in banned], dtype=np.int64)

    # Fallbacks if too few recipients remain
    if recipients.size == 0:
        all_idx = np.setdiff1d(np.arange(n), banned, assume_unique=False)
        recipients = all_idx
    if recipients.size == 0:
        recipients = np.arange(n)

    # Redistribute removed mass uniformly over recipients
    a_new = a_shaved.copy()
    add_val = removed_mass / float(recipients.size)
    np.add.at(a_new, recipients, add_val)

    # Renormalize to maintain sum(a) = 1 and ensure nonnegativity
    a_new = np.maximum(a_new, 0)
    s = np.sum(a_new)
    if s > 0:
        a_new /= s

    return a_new


def _expand_indices(indices: np.ndarray, n: int, radius: int) -> np.ndarray:
    """
    Expand a set of indices by including a neighborhood of given radius around each.
    Returned array contains unique indices in [0, n-1].
    """
    if radius <= 0 or indices.size == 0:
        return np.unique(indices)
    expanded = []
    for idx in np.unique(indices):
        lo = max(0, idx - radius)
        hi = min(n - 1, idx + radius)
        expanded.append(np.arange(lo, hi + 1))
    if not expanded:
        return np.array([], dtype=np.int64)
    return np.unique(np.concatenate(expanded))


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
