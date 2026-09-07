Design decisions and reusable strategies that target the evaluation function directly, control time reliably, and sustain valid progress on large n via efficient convolution and projection steps.

- Projected Subgradient + Mass-Transport Minimizer for Autoconvolution Peak: The optimizer fixes sum(a)=1 to exploit the evaluation function’s scale invariance and repeatedly shifts mass from indices that maximize the active autoconvolution peak to indices that minimize it, projecting to non-negativity after each move to directly reduce max_k (a⋆a)[k].
- Projected Subgradient + Mass-Transport Minimizer for Autoconvolution Peak: Autoconvolution is computed with FFT to evaluate b and b_max efficiently, enabling many iterations for candidate dimensions in the 300–700 range and supporting multi-start exploration across n.
- Projected Subgradient + Mass-Transport Minimizer for Autoconvolution Peak: The search combines multi-start initialization, simulated annealing acceptance, and adaptive step-size backtracking on the active maxima to escape poor local minima while enforcing progress when b_max fails to decrease.
- search_for_best_sequence: The routine maintains a global best sequence throughout and enforces a time safety policy by using a 995.0-second time budget within the 1000-second hard cutoff to guarantee returning a valid result.

```python
#!/usr/bin/env python3
"""Optimized search for a step function minimizing the autoconvolution peak ratio."""

import json
import time
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def search_for_best_sequence() -> np.ndarray:
    """
    Global search for a non-negative sequence (step function) that minimizes:
        score(a) = 2 * n * max_k (a ⋆ a)[k] / (sum(a)^2)
    subject to a_i >= 0. The score is scale-invariant, so we maintain sum(a) = 1.

    Strategy:
    - Multi-start over different dimensions and initial shapes.
    - Inner loop performs targeted "mass transport" steps:
        Move a small amount of mass from indices that most contribute to the current
        autoconvolution peak(s) to indices that contribute least.
    - Uses FFT for fast autoconvolution.
    - Simulated annealing acceptance with adaptive step sizes.
    - Always keeps and returns the global best-so-far sequence.
    - Time-bounded with a safety margin to ensure returning within the budget.
    """
    rng = np.random.default_rng(42)
    t_start = time.time()
    # Safety margin to ensure we return before a 1000-second hard cutoff
    time_budget = 995.0

    # Helper functions (nested to keep them local and avoid polluting global namespace)
    def next_pow2(x: int) -> int:
        return 1 << (x - 1).bit_length()

    def autoconv(a: np.ndarray) -> np.ndarray:
        """Compute a ⋆ a using FFT, returning a vector of length 2n-1."""
        n = a.size
        L = next_pow2(2 * n - 1)
        fa = np.fft.rfft(a, n=L)
        conv = np.fft.irfft(fa * fa, n=L)
        return conv[: 2 * n - 1].copy()

    def score_from_bmax(n: int, bmax: float, sum_a: float) -> float:
        if sum_a <= 1e-12:
            return np.inf
        return float(2.0 * n * bmax / (sum_a * sum_a))

    def compute_score(a: np.ndarray) -> Tuple[float, float, np.ndarray]:
        """Return (score, bmax, b) with sum(a)=1 assumption (but handles general sum)."""
        aa = np.maximum(a, 0.0).astype(np.float64)
        s = float(np.sum(aa))
        if s == 0.0:
            return np.inf, np.inf, np.zeros(1, dtype=np.float64)
        b = autoconv(aa)
        bmax = float(np.max(b))
        return score_from_bmax(aa.size, bmax, s), bmax, b

    def normalize_nonneg(a: np.ndarray) -> np.ndarray:
        a = np.maximum(a, 0.0)
        s = float(np.sum(a))
        if s <= 0.0:
            # fallback: small uniform if degenerate
            a = np.ones_like(a) / a.size
        else:
            a = a / s
        return a

    def s_vector_for_k(a: np.ndarray, k: int) -> np.ndarray:
        """
        Construct s(k) where s_i(k) = a_{k - i} for indices within [0, n-1], else 0.
        This is the key 'overlap' vector for peak k.
        """
        n = a.size
        s = np.zeros_like(a)
        i0 = max(0, k - (n - 1))
        i1 = min(n - 1, k)
        if i1 >= i0:
            idx = np.arange(i0, i1 + 1)
            s[idx] = a[k - idx]
        return s

    def choose_initial_profiles(n: int) -> List[np.ndarray]:
        """Generate a diverse set of initial feasible profiles of length n (sum=1)."""
        profiles: List[np.ndarray] = []

        # Uniform
        profiles.append(np.ones(n, dtype=np.float64) / n)

        # Triangular (center peaked)
        tri = 1.0 - np.abs(np.linspace(-1.0, 1.0, n))
        profiles.append(normalize_nonneg(tri))

        # Raised cosine (Hann-like, zero at ends)
        x = np.linspace(0, np.pi, n)
        rc = 0.5 * (1.0 - np.cos(2.0 * x / 2.0))
        profiles.append(normalize_nonneg(rc))

        # U-shaped (edge-concentrated)
        idx = np.arange(n, dtype=np.float64)
        mid = 0.5 * (n - 1)
        ushape = np.abs(idx - mid) + 0.1
        profiles.append(normalize_nonneg(ushape))

        # Two-hump (peaks at quarter and three-quarter positions)
        pos = np.linspace(0.0, 1.0, n)
        g1 = np.exp(-0.5 * ((pos - 0.25) / 0.12) ** 2)
        g2 = np.exp(-0.5 * ((pos - 0.75) / 0.12) ** 2)
        twoh = g1 + g2 + 1e-3
        profiles.append(normalize_nonneg(twoh))

        # Polynomial baseline similar to the given template (mapped to indices)
        # 1 + 4|x| - 16 x^2 on x in [-1/4, 1/4], clipped at 0, symmetrized
        x1 = np.linspace(-0.25, 0.25, n)
        poly = 1.0 + 4.0 * np.abs(x1) - 16.0 * x1 * x1
        poly = 0.5 * (poly + poly[::-1])
        poly = np.maximum(poly, 0.0)
        profiles.append(normalize_nonneg(poly))

        # Slight random perturbations around good shapes to diversify
        base_choices = [profiles[1], profiles[2], profiles[5]]
        for base in base_choices:
            for _ in range(2):
                noise = rng.normal(0.0, 0.02, size=n)
                cand = normalize_nonneg(np.maximum(0.0, base + noise))
                profiles.append(cand)

        return profiles

    def mass_transport_step(
        a: np.ndarray,
        g: np.ndarray,
        step_mass: float,
        donor_frac: float = 0.20,
        recv_frac: float = 0.20,
        min_positive: float = 1e-16,
    ) -> np.ndarray:
        """
        Perform one L1-preserving non-negative mass transport step guided by g:
        - Identify donors: indices with largest g (most contributing to peak).
        - Identify receivers: indices with smallest g (least contributing).
        - Move total 'step_mass' from donors to receivers, respecting a_i >= 0.
        """
        n = a.size
        # Sort indices by g
        order = np.argsort(g)  # ascending
        recv_count = max(4, int(recv_frac * n))
        donor_count = max(4, int(donor_frac * n))

        recv_idx = order[:recv_count]
        donor_idx = order[-donor_count:]

        # Ensure donors have positive available mass
        donor_idx = donor_idx[a[donor_idx] > min_positive]
        if donor_idx.size == 0:
            return a  # no move possible

        # Compute donor weights proportional to their g (the higher g, the more we remove)
        d_weights = g[donor_idx] - np.min(g[donor_idx]) + 1e-12
        if not np.all(np.isfinite(d_weights)) or np.sum(d_weights) <= 0:
            d_weights = np.ones_like(d_weights)
        d_weights = d_weights / np.sum(d_weights)

        # Proposed outflows from donors
        proposed_out = step_mass * d_weights
        avail = a[donor_idx].copy()
        out = np.minimum(proposed_out, avail)

        # Redistribute remainder if we clipped some donors
        remainder = float(step_mass - np.sum(out))
        if remainder > 1e-14:
            # Try to allocate remainder among donors not saturated
            for _ in range(3):  # a few passes should be enough
                mask = (avail - out) > 1e-16
                if not np.any(mask):
                    break
                w = d_weights[mask]
                w_sum = np.sum(w)
                if w_sum <= 0:
                    break
                add = remainder * w / w_sum
                add = np.minimum(add, (avail - out)[mask])
                out[mask] += add
                remainder = float(step_mass - np.sum(out))
                if remainder <= 1e-14:
                    break

        actual_moved = float(np.sum(out))
        if actual_moved <= 0.0:
            return a

        # Receivers: weights favor smaller g (larger displacement)
        r_weights = (np.max(g[recv_idx]) - g[recv_idx]) + 1e-12
        if not np.all(np.isfinite(r_weights)) or np.sum(r_weights) <= 0:
            r_weights = np.ones_like(r_weights)
        r_weights = r_weights / np.sum(r_weights)
        inflow = actual_moved * r_weights

        # Apply update
        a_new = a.copy()
        a_new[donor_idx] -= out
        a_new[recv_idx] += inflow

        # Projection back to non-negative and unit sum
        a_new = normalize_nonneg(a_new)
        return a_new

    def optimize_one(a0: np.ndarray, end_time: float) -> Tuple[np.ndarray, float]:
        """
        Optimize starting from a0 within the time budget:
        Returns best (a, score). Maintains sum(a)=1 and a>=0.
        """
        a = normalize_nonneg(a0.copy())
        best_a = a.copy()
        best_score, bmax, b = compute_score(a)
        # Prevent degenerate sequences
        if not np.isfinite(best_score):
            a = np.ones_like(a) / a.size
            best_a = a.copy()
            best_score, bmax, b = compute_score(a)

        # Trackers and hyperparameters
        step_mass = 0.01  # total L1 mass to move per step
        min_step_mass = 1e-5
        max_step_mass = 0.05
        cooldown = 0
        patience = 150
        iter_since_improve = 0

        # Simulated annealing temperature based on current bmax
        T = 0.05 * bmax
        T_min = 1e-9
        accept_cool = 0.995

        n = a.size

        # small smoothing kernel (zero-padded) to avoid spikes
        smooth_kernel = np.array([0.25, 0.5, 0.25], dtype=np.float64)

        it = 0
        while time.time() < end_time:
            it += 1
            # Evaluate convolution
            _, bmax, b = compute_score(a)

            # Find peak indices (top few peaks)
            # Take all within a tolerance of the max and then cap to top M
            M = 4
            tol = 1e-12
            if np.isfinite(bmax):
                peak_idx = np.flatnonzero(b >= (bmax - tol))
                if peak_idx.size > M:
                    # Keep only top M by value (descending)
                    top_order = np.argsort(-b)[:M]
                    peak_idx = top_order
            else:
                peak_idx = np.array([np.argmax(b)], dtype=int)

            # Construct averaged s-vector across active peaks
            g = np.zeros(n, dtype=np.float64)
            for k in peak_idx:
                g += s_vector_for_k(a, int(k))
            g /= max(1, peak_idx.size)

            # Mass transport step
            a_candidate = mass_transport_step(a, g, step_mass=step_mass)

            # Optional gentle smoothing every few steps (with renormalization)
            if it % 50 == 0:
                a_candidate = np.convolve(a_candidate, smooth_kernel, mode="same")
                a_candidate = normalize_nonneg(a_candidate)

            cand_score, cand_bmax, _ = compute_score(a_candidate)

            improved = cand_bmax < bmax - 1e-14
            accept = False
            if improved:
                accept = True
            else:
                # Simulated annealing acceptance
                delta = float(cand_bmax - bmax)
                if delta < 0.0:
                    accept = True
                else:
                    if T > T_min and np.isfinite(delta):
                        p = np.exp(-delta / max(T, 1e-18))
                        if rng.random() < p:
                            accept = True

            if accept:
                a = a_candidate
                bmax = cand_bmax
                # Adapt step size
                if improved:
                    iter_since_improve = 0
                    step_mass = min(max_step_mass, step_mass * 1.05)
                    # Update best-so-far
                    if cand_score < best_score - 1e-12:
                        best_score = cand_score
                        best_a = a.copy()
                else:
                    iter_since_improve += 1
                    step_mass = max(min_step_mass, step_mass * 0.99)
                # Cool temperature
                T = max(T_min, T * accept_cool)
            else:
                # Backtrack: shrink step and increase patience counter
                iter_since_improve += 1
                step_mass = max(min_step_mass, 0.7 * step_mass)

            # Restart if no improvement for a while
            if iter_since_improve >= patience:
                # small random shuffle to escape local minima
                noise = rng.normal(0.0, 0.01, size=n)
                a = normalize_nonneg(np.maximum(0.0, best_a + noise))
                _, bmax, _ = compute_score(a)
                iter_since_improve = 0
                # Slightly reset step size and temperature
                step_mass = min(max_step_mass, max(0.005, step_mass * 1.2))
                T = max(T_min, 0.05 * bmax)

            # Exit early if we have very small step or negligible change and close to time limit
            if step_mass <= min_step_mass and time.time() > end_time - 0.02:
                break

        # Final return of the best found for this start
        return best_a, best_score

    # Global multi-start across dimensions and initial profiles
    # Include the legacy baseline as a fallback best
    def legacy_baseline(n: int = 600) -> np.ndarray:
        xs = np.linspace(-0.25, 0.25, n)
        f = 1.0 + 4.0 * np.abs(xs) - 16.0 * xs * xs
        f = 0.5 * (f + f[::-1])
        f = np.maximum(f, 0.0)
        return normalize_nonneg(f)

    global_best_seq = legacy_baseline(600)
    # Use the outer evaluate_sequence to exactly match the scoring routine
    # We'll import it later; for now, a quick internal score:
    g_score, _, _ = compute_score(global_best_seq)
    global_best_score = g_score

    # Candidate dimensions to explore (balanced between n and max-convolution trade-off)
    candidate_ns = [450, 500, 550, 600, 650, 700]
    # Exact time slicing is dynamic: iterate until time budget exhausted
    for n in candidate_ns:
        if time.time() - t_start > time_budget:
            break
        profiles = choose_initial_profiles(n)
        # Limit number of starts per dimension to keep within budget
        for a0 in profiles:
            if time.time() - t_start > time_budget:
                break
            # Assign a small per-run time slice dynamically depending on remaining time
            time_left = time_budget - (time.time() - t_start)
            # Allocate around 4 to 10 seconds per start depending on remaining budget
            per_run_time = max(1.0, min(8.0, time_left / max(1, len(profiles))))
            end_time = time.time() + per_run_time

            a_best, s_best = optimize_one(a0, end_time)
            if s_best + 1e-12 < global_best_score:
                global_best_score = s_best
                global_best_seq = a_best.copy()

    # Ensure the output is valid: non-negative and normalized
    global_best_seq = normalize_nonneg(global_best_seq)
    return global_best_seq
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
    # Run the search and convert the numpy array to a plain Python list of floats
    seq = search_for_best_sequence()
    # Final normalization and safety clamping (defensive)
    seq = np.maximum(0.0, np.asarray(seq, dtype=np.float64))
    s = float(np.sum(seq))
    if s <= 0.0:
        seq = np.ones_like(seq) / seq.size
    else:
        seq = seq / s
    # Ensure within bounds
    seq = np.minimum(1000.0, np.maximum(0.0, seq))
    return list(map(float, seq))


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```
