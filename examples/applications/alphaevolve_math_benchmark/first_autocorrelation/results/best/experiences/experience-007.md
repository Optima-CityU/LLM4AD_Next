Using a smooth LogSumExp continuation schedule with L-BFGS-B prior to exact active-set minimax descent provides curvature information in broad basins without premature simplex trapping.

- L-BFGS-B continuation phase: Optimizing a scale-penalized LogSumExp smooth surrogate objective J_alpha(x) = 2 * n * alpha^{-1} * log(sum(exp(alpha * convolve(a, a)))) + (sum(x) - 1)^2 using L-BFGS-B with bounds x[i] >= 0 over an increasing smoothing schedule (alpha from 1e4 to 3e6) delivers quasi-Newton descent steps that escape broad basins where fixed-size subgradient steps get trapped.
- Two-stage continuation and active-set architecture: Warm-starting each continuation stage from the previous optimization result and feeding the final smoothed L-BFGS-B output into exact active-set minimax descent and shake steps improved the score from 0.94895 to 0.98561 (upper bound 1.52728).

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Search for a low peak-convolution profile on the simplex.

    The objective is scale invariant, so all working profiles have unit sum.
    A projected minimax descent is used after a deterministic L-BFGS-B
    continuation phase, which supplies better curvature information in the
    broad basin of the minimax problem.
    """
    import time

    dimension = 600
    rng = np.random.default_rng(20250308)
    deadline = time.monotonic() + 980.0

    def simplex_projection(vector):
        """Euclidean projection onto {x >= 0, sum(x) = 1}."""
        vector = np.asarray(vector, dtype=float)
        ordered = np.sort(vector)[::-1]
        cumulative = np.cumsum(ordered)
        indices = np.arange(1, len(vector) + 1)
        feasible = ordered - (cumulative - 1.0) / indices > 0.0
        if not np.any(feasible):
            return np.full(len(vector), 1.0 / len(vector))
        last = np.nonzero(feasible)[0][-1]
        threshold = (cumulative[last] - 1.0) / float(last + 1)
        return np.maximum(vector - threshold, 0.0)

    def peak(profile):
        return float(np.max(np.convolve(profile, profile)))

    def full_score(profile):
        return 2.0 * len(profile) * peak(profile)

    x = np.linspace(-0.25, 0.25, dimension)
    baseline = 1.0 + 4.0 * np.abs(x) - 16.0 * x * x
    baseline = (baseline + baseline[::-1]) * 0.5
    baseline = np.maximum(baseline, 0.0)
    baseline /= np.sum(baseline)

    best = baseline.copy()
    best_value = full_score(best)

    starts = [baseline, np.full(dimension, 1.0 / dimension)]
    for scale in (0.015, 0.04, 0.09, 0.18, 0.32):
        noise = rng.normal(0.0, scale, dimension)
        candidate = baseline * np.exp(noise)
        candidate /= np.sum(candidate)
        starts.append(candidate)
    starts.append(simplex_projection(
        0.65 * baseline + 0.35 * rng.random(dimension)
    ))

    # Import lazily so the search remains usable in environments without
    # SciPy; the inherited projected method is still a complete fallback.
    try:
        from scipy.optimize import minimize
    except Exception:
        minimize = None

    def smooth_value_and_gradient(vector, alpha):
        """Return J_alpha and its exact gradient with respect to x.

        The convolution is formed from a=x/s, so the chain rule includes the
        normalization correction (g - <g,a>)/s.
        """
        vector = np.asarray(vector, dtype=float)
        total = float(np.sum(vector))
        if total <= 1e-14:
            return 1e100, np.zeros_like(vector)

        a = vector / total
        convolution = np.convolve(a, a)
        shifted = alpha * (convolution - float(np.max(convolution)))
        exponentials = np.exp(np.clip(shifted, -745.0, 0.0))
        partition = float(np.sum(exponentials))
        weights = exponentials / partition

        log_sum_exp = (
            float(np.max(convolution))
            + np.log(partition) / alpha
        )
        value = 2.0 * dimension * log_sum_exp + (total - 1.0) ** 2

        # d(convolve(a,a))/da is twice a correlation with a.
        correlation = np.convolve(weights, a[::-1])
        grad_a = 2.0 * correlation[dimension - 1:2 * dimension - 1]
        normalization_correction = float(np.dot(grad_a, a))
        gradient = (
            (grad_a - normalization_correction) / total
            + 2.0 * (total - 1.0)
        )
        return float(value), np.asarray(gradient, dtype=float)

    for start_number, initial in enumerate(starts):
        if time.monotonic() >= deadline:
            break

        a = np.asarray(initial, dtype=float).copy()
        current_peak = peak(a)

        # Deterministic smooth continuation.  Each stage starts at the
        # previous L-BFGS-B solution and is checked against the true maximum.
        if minimize is not None:
            smooth_start = a.copy()
            for alpha in (1e4, 3e4, 1e5, 3e5, 1e6, 3e6):
                if time.monotonic() >= deadline:
                    break

                def objective(vector, alpha=alpha):
                    return smooth_value_and_gradient(vector, alpha)

                try:
                    result = minimize(
                        objective,
                        smooth_start,
                        method="L-BFGS-B",
                        jac=True,
                        bounds=[(0.0, None)] * dimension,
                        options={
                            "maxiter": 45,
                            "ftol": 1e-15,
                            "gtol": 1e-9,
                            "maxls": 20,
                        },
                    )
                    if np.all(np.isfinite(result.x)):
                        smooth_start = np.maximum(
                            np.asarray(result.x, dtype=float), 0.0
                        )
                        total = float(np.sum(smooth_start))
                        if total > 1e-14:
                            smooth_start /= total
                            smooth_peak = peak(smooth_start)
                            if smooth_peak < current_peak:
                                a = smooth_start.copy()
                                current_peak = smooth_peak
                                score = full_score(a)
                                if score < best_value:
                                    best_value = score
                                    best = a.copy()
                except Exception:
                    # Preserve the inherited optimizer if a numerical or
                    # optional-dependency issue occurs in this stage.
                    break

        # Existing projected soft-max continuation.
        for iteration in range(90):
            if time.monotonic() >= deadline:
                break
            convolution = np.convolve(a, a)
            maximum = float(np.max(convolution))
            temperature = max(maximum * (0.20 * (1.0 - iteration / 90.0)
                                          + 0.008), 1e-15)
            weights = np.exp((convolution - maximum) / temperature)
            weights /= np.sum(weights)

            gradient = np.zeros(dimension, dtype=float)
            for k, weight in enumerate(weights):
                if weight < 1e-12:
                    continue
                lo = max(0, k - (dimension - 1))
                hi = min(dimension - 1, k)
                gradient[lo:hi + 1] += 2.0 * weight * a[k - np.arange(lo, hi + 1)]

            step = 0.9 * (1.0 - 0.55 * iteration / 90.0)
            trial = simplex_projection(a - step * gradient)
            trial_peak = peak(trial)
            if trial_peak <= current_peak * 1.0000005:
                a, current_peak = trial, trial_peak
            else:
                step *= 0.25
                trial = simplex_projection(a - step * gradient)
                trial_peak = peak(trial)
                if trial_peak < current_peak:
                    a, current_peak = trial, trial_peak

        # Existing exact active-set minimax descent.
        iterations = 1150 if start_number < 3 else 850
        for iteration in range(iterations):
            if time.monotonic() >= deadline:
                break
            convolution = np.convolve(a, a)
            maximum = float(np.max(convolution))
            tolerance = max(1e-11, maximum * (2e-7 if iteration < 250 else 2e-9))
            active = np.flatnonzero(convolution >= maximum - tolerance)

            gradient = np.zeros(dimension, dtype=float)
            for k in active:
                lo = max(0, int(k) - dimension + 1)
                hi = min(dimension - 1, int(k))
                gradient[lo:hi + 1] += (
                    2.0 * a[int(k) - np.arange(lo, hi + 1)]
                )
            gradient /= float(max(1, len(active)))

            step = 0.42 / np.sqrt(1.0 + iteration / 18.0)
            if iteration % 137 == 0:
                step *= 2.0

            accepted = False
            for backtrack in range(8):
                trial = simplex_projection(a - step * (0.72 ** backtrack) * gradient)
                trial_peak = peak(trial)
                if trial_peak < current_peak - max(1e-14, current_peak * 1e-12):
                    a, current_peak = trial, trial_peak
                    accepted = True
                    break

            if iteration % 23 == 0:
                convolution = np.convolve(a, a)
                active_k = int(np.argmax(convolution))
                lo = max(0, active_k - dimension + 1)
                hi = min(dimension - 1, active_k)
                local_gradient = np.zeros(dimension)
                local_gradient[lo:hi + 1] = (
                    2.0 * a[active_k - np.arange(lo, hi + 1)]
                )
                donors = np.argsort(local_gradient)[-min(10, dimension):]
                recipients = np.argsort(local_gradient)[:min(16, dimension)]
                transfer = min(0.0008, 0.12 / (iteration + 20.0))
                for recipient in recipients:
                    for donor in donors:
                        if donor == recipient or a[donor] <= 0.0:
                            continue
                        amount = min(transfer, 0.35 * a[donor])
                        trial = a.copy()
                        trial[donor] -= amount
                        trial[recipient] += amount
                        trial_peak = peak(trial)
                        if trial_peak < current_peak:
                            a, current_peak = trial, trial_peak
                            accepted = True

            if not accepted and iteration % 71 == 0:
                shake = rng.normal(0.0, 1e-5, dimension)
                trial = simplex_projection(a + shake)
                trial_peak = peak(trial)
                if trial_peak < current_peak:
                    a, current_peak = trial, trial_peak

            score = full_score(a)
            if score < best_value:
                best_value = score
                best = a.copy()

    return [float(value) for value in best]
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
