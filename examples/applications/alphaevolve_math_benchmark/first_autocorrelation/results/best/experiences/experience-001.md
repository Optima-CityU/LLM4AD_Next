Multi-start minimax optimization combining continuation on a log-sum-exp surrogate with active-set hard-max polishing and exact evaluation gating.

- Log-sum-exp continuation optimizer: Optimizes 600 nonnegative coefficients using L-BFGS-B with nonnegativity bounds under a continuation schedule of increasing inverse temperatures on the log-sum-exp autoconvolution surrogate, computing analytic gradients via the correlation of softmax weights with the sequence.
- Active-set peak polishing: Identifies near-maximal convolution entries, averages their exact gradients, applies projected descent and coordinate mass transfers, and accepts candidate updates only when the true hard-max objective improves.
- Multi-start candidate management: Seeds optimization from diverse profiles—including boundary-focused, uniform, and tapered polynomials—and maintains the best exact evaluate_sequence candidate throughout runtime as a guaranteed fallback.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Search for a low-peak nonnegative sequence on the 600-point grid."""
    import time

    n = 600
    started = time.monotonic()
    # Leave a substantial margin for serialization and process shutdown.
    deadline = started + 35.0
    rng = np.random.default_rng(731947)

    def normalized(x):
        x = np.asarray(x, dtype=float)
        x = np.maximum(x, 0.0)
        total = float(np.sum(x))
        if not np.isfinite(total) or total <= 1e-14:
            return np.full(n, 1.0 / n)
        return x / total

    def hard_peak(x):
        return float(np.max(np.convolve(x, x)))

    # The inherited analytic construction is retained as a guaranteed fallback.
    grid = np.linspace(-0.25, 0.25, n)
    boundary = 1.0 + 4.0 * np.abs(grid) - 16.0 * grid * grid
    boundary = np.maximum((boundary + boundary[::-1]) * 0.5, 0.0)
    boundary = normalized(boundary)

    candidates = [
        boundary,
        np.full(n, 1.0 / n),
        normalized(1.0 - 0.85 * (2.0 * np.abs(grid)) ** 2),
        normalized(0.25 + 0.75 * (1.0 - 2.0 * np.abs(grid)) ** 4),
        normalized(0.5 + 0.5 * np.cos(np.pi * 2.0 * grid)),
    ]

    # Include small perturbations of the strongest initial profile.
    current_best = min(candidates, key=hard_peak).copy()
    for scale in (0.015, 0.04, 0.09):
        noisy = current_best * np.exp(scale * rng.standard_normal(n))
        candidates.append(normalized(noisy))

    best = current_best.copy()
    best_value = hard_peak(best)

    try:
        from scipy.optimize import minimize
    except Exception:
        minimize = None

    if minimize is not None:
        class SearchFinished(Exception):
            pass

        def soft_objective(x, beta):
            """Scaled log-sum-exp objective and its simplex-aware gradient."""
            a = normalized(x)
            convolution = np.convolve(a, a)
            z = beta * convolution
            shift = float(np.max(z))
            weights = np.exp(z - shift)
            weights /= float(np.sum(weights))
            soft = (shift + np.log(float(np.sum(np.exp(z - shift))))) / beta

            # d conv(a,a)/d a_i is twice a valid correlation with a.
            correlation = np.convolve(weights, a[::-1])
            gradient = 2.0 * correlation[n - 1:2 * n - 1]

            # Account for the normalization performed by normalized(x).
            gradient -= float(np.dot(gradient, a))
            # Scaling improves the numerical stopping criterion without
            # changing the minimizer.
            return n * soft, n * gradient

        # Use several starts and continuation from a smooth approximation
        # towards the actual maximum.
        for initial in candidates:
            if time.monotonic() >= deadline:
                break
            x = normalized(initial)
            for beta in (500.0, 1500.0, 5000.0, 18000.0):
                if time.monotonic() >= deadline:
                    break

                def objective(v, b=beta):
                    if time.monotonic() >= deadline:
                        raise SearchFinished()
                    return soft_objective(v, b)

                def callback(_):
                    if time.monotonic() >= deadline:
                        raise SearchFinished()

                try:
                    result = minimize(
                        objective,
                        x,
                        jac=True,
                        method="L-BFGS-B",
                        bounds=[(0.0, None)] * n,
                        callback=callback,
                        options={
                            "maxiter": 110,
                            "ftol": 1e-13,
                            "gtol": 2e-8,
                            "maxls": 25,
                        },
                    )
                    x = normalized(result.x)
                except SearchFinished:
                    # The last accepted iterate is still a valid candidate.
                    break
                except Exception:
                    break

                value = hard_peak(x)
                if value < best_value:
                    best, best_value = x.copy(), value

        # Polish the nonsmooth objective directly.  At a minimax point several
        # convolution entries often tie, so average their exact gradients.
        x = best.copy()
        for _ in range(90):
            if time.monotonic() >= deadline:
                break
            convolution = np.convolve(x, x)
            peak = float(np.max(convolution))
            active = np.flatnonzero(convolution >= peak - max(1e-10, peak * 2e-6))
            gradient = np.zeros(n)
            for index in active:
                left = max(0, index - n + 1)
                right = min(n - 1, index)
                for i in range(left, right + 1):
                    gradient[i] += 2.0 * x[index - i]
            gradient /= max(1, len(active))

            # Project the descent direction onto the sum-preserving tangent
            # space, then use backtracking against the exact hard objective.
            direction = gradient - float(np.dot(gradient, x))
            step = 0.12
            accepted = False
            while step > 1e-7:
                trial = normalized(np.maximum(x - step * direction, 0.0))
                trial_peak = hard_peak(trial)
                if trial_peak < peak - 1e-12:
                    x = trial
                    accepted = True
                    if trial_peak < best_value:
                        best, best_value = trial.copy(), trial_peak
                    break
                step *= 0.5
            if not accepted:
                break

        # A few short multiplicative restarts can escape a flat active set.
        for _ in range(4):
            if time.monotonic() >= deadline:
                break
            trial = normalized(best * np.exp(0.025 * rng.standard_normal(n)))
            for beta in (3000.0, 12000.0):
                if time.monotonic() >= deadline:
                    break
                try:
                    result = minimize(
                        lambda v, b=beta: soft_objective(v, b),
                        trial,
                        jac=True,
                        method="L-BFGS-B",
                        bounds=[(0.0, None)] * n,
                        options={"maxiter": 65, "ftol": 1e-12, "gtol": 2e-8},
                    )
                    trial = normalized(result.x)
                except Exception:
                    break
            trial_peak = hard_peak(trial)
            if trial_peak < best_value:
                best, best_value = trial.copy(), trial_peak

    return [float(v) for v in normalized(best)]
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
