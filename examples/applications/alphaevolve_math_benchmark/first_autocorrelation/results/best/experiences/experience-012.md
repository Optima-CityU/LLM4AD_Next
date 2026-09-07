Multi-stage optimization combining smooth continuation, multi-peak active-set gradient averaging, and exact-acceptance mass transport to minimize convolution peak objectives.

- Smooth-max L-BFGS-B continuation: Warm-starting optimization across increasing sharpness parameters (1e4 to 3e6) on a log-sum-exp self-convolution surrogate rapidly positions candidate sequences into well-conditioned basins before exact polishing.
- Adaptive plateau active-set descent: Averaging exact reflected gradient contributions across all convolution peaks within a narrow band (5e-5) of the current maximum prevents minimax descent from oscillating by merely suppressing one peak while elevating tied runner-up peaks.
- Exact objective acceptance and coordinate transport: Validating every proposed simplex-projected step, multi-peak pressure-directed mass transfer, and random shake strictly against the exact maximum convolution value ensures surrogate approximations never degrade the retained global best candidate.
- L-BFGS-B smooth continuation: Warm-starting successive L-BFGS-B optimization stages over ascending LogSumExp smoothing parameters alpha (1e4 to 3e6) rapidly navigates the non-convex simplex landscape, while checking the hard convolve maximum after each stage protects against surrogate distortion.
- Adaptive plateau active-set descent: Weighting exact convolution subgradients across near-tied runner-up peaks and applying projected simplex descent prevents oscillatory stall across multi-peak convolution plateaus.
- Exact mass pressure transport: Directly shifting mass from high-pressure donor coordinates to low-pressure recipient coordinates under strict exact-score acceptance breaks peak-swapping cycles that gradient steps cannot resolve.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Find a nonnegative unit-sum vector with a small self-convolution peak.

    The search combines smooth-max L-BFGS-B continuation with exact,
    acceptance-tested minimax descent.  Since the objective is homogeneous,
    all internal candidates are normalized to have sum one.
    """
    import time

    n = 600
    rng = np.random.default_rng(20250308)
    deadline = time.monotonic() + 980.0

    def project_simplex(v):
        """Project v onto the nonnegative unit simplex."""
        v = np.asarray(v, dtype=float)
        u = np.sort(v)[::-1]
        cssv = np.cumsum(u)
        ind = np.arange(1, len(v) + 1)
        good = u - (cssv - 1.0) / ind > 0.0
        if not np.any(good):
            return np.full(len(v), 1.0 / len(v))
        rho = np.flatnonzero(good)[-1]
        theta = (cssv[rho] - 1.0) / float(rho + 1)
        return np.maximum(v - theta, 0.0)

    def convolution(a):
        return np.convolve(a, a)

    def objective(a):
        return float(np.max(convolution(a)))

    def remember(a, value):
        nonlocal best, best_peak
        if value < best_peak:
            best_peak = value
            best = np.asarray(a, dtype=float).copy()

    # A symmetric quadratic/absolute-value profile is a strong deterministic
    # starting point for this particular autocorrelation problem.
    coordinate = np.linspace(-0.25, 0.25, n)
    baseline = 1.0 + 4.0 * np.abs(coordinate) - 16.0 * coordinate**2
    baseline = 0.5 * (baseline + baseline[::-1])
    baseline = np.maximum(baseline, 0.0)
    baseline /= np.sum(baseline)

    best = baseline.copy()
    best_peak = objective(best)

    starts = [
        baseline.copy(),
        np.full(n, 1.0 / n),
    ]
    for scale in (0.02, 0.06, 0.14, 0.28):
        candidate = baseline * np.exp(rng.normal(0.0, scale, n))
        candidate /= np.sum(candidate)
        starts.append(candidate)
    starts.append(project_simplex(
        0.70 * baseline + 0.30 * rng.random(n)
    ))

    try:
        from scipy.optimize import minimize
    except Exception:
        minimize = None

    def smooth_value_gradient(x, alpha):
        """Smooth-max value and its normalization-aware gradient."""
        x = np.asarray(x, dtype=float)
        total = float(np.sum(x))
        if total <= 1e-14 or not np.all(np.isfinite(x)):
            return 1e100, np.zeros_like(x)

        a = x / total
        c = convolution(a)
        m = float(np.max(c))
        weights = np.exp(np.clip(alpha * (c - m), -745.0, 0.0))
        weights /= float(np.sum(weights))

        # Derivative of sum_k weights[k] * (a*a)[k].
        grad_a = np.zeros(n, dtype=float)
        for k, weight in enumerate(weights):
            if weight < 1e-14:
                continue
            lo = max(0, k - n + 1)
            hi = min(n - 1, k)
            idx = np.arange(lo, hi + 1)
            grad_a[idx] += 2.0 * weight * a[k - idx]

        correction = float(np.dot(grad_a, a))
        normalized_gradient = (grad_a - correction) / total
        log_mean = m + np.log(float(np.sum(
            np.exp(np.clip(alpha * (c - m), -745.0, 0.0))
        ))) / alpha

        # The small penalty fixes the otherwise irrelevant scale direction
        # for L-BFGS-B while preserving the simplex solution.
        value = 2.0 * n * log_mean + (total - 1.0) ** 2
        gradient = 2.0 * n * normalized_gradient + 2.0 * (total - 1.0)
        return float(value), gradient

    def active_gradient(a, c, iteration):
        """Average gradients from the currently relevant convolution peaks."""
        maximum = float(np.max(c))
        order = np.argsort(c)[::-1]

        # Keep one peak when it is clearly isolated, two for a moderate
        # plateau, and at most four when several peaks are nearly tied.
        gap = maximum - float(c[order[1]])
        if gap > maximum * 2.0e-4:
            count = 1
        elif gap > maximum * 5.0e-5:
            count = 2
        else:
            count = min(4, len(order))

        active = order[:count]
        heights = np.maximum(c[active], 1e-300)
        weights = heights / float(np.sum(heights))
        gradient = np.zeros(n, dtype=float)

        for k, weight in zip(active, weights):
            k = int(k)
            lo = max(0, k - n + 1)
            hi = min(n - 1, k)
            idx = np.arange(lo, hi + 1)
            gradient[idx] += weight * 2.0 * a[k - idx]
        return gradient, active

    for start_index, initial in enumerate(starts):
        if time.monotonic() >= deadline:
            break

        a = project_simplex(initial)
        current = objective(a)
        remember(a, current)

        # Smooth continuation places the candidate in a broad minimax basin.
        if minimize is not None:
            smooth_start = a.copy()
            for alpha in (1e4, 3e4, 1e5, 3e5, 1e6, 3e6):
                if time.monotonic() >= deadline:
                    break

                def smooth_fun(x, alpha=alpha):
                    return smooth_value_gradient(x, alpha)

                try:
                    result = minimize(
                        smooth_fun,
                        smooth_start,
                        method="L-BFGS-B",
                        jac=True,
                        bounds=[(0.0, None)] * n,
                        options={
                            "maxiter": 40,
                            "ftol": 1e-14,
                            "gtol": 1e-8,
                            "maxls": 18,
                        },
                    )
                    trial = np.maximum(np.asarray(result.x, dtype=float), 0.0)
                    total = float(np.sum(trial))
                    if total > 1e-14 and np.all(np.isfinite(trial)):
                        smooth_start = trial / total
                        trial_peak = objective(smooth_start)
                        if trial_peak < current:
                            a = smooth_start.copy()
                            current = trial_peak
                            remember(a, current)
                except Exception:
                    break

        # A short exact projected phase removes smooth-max approximation
        # errors before the more conservative plateau polishing.
        for iteration in range(100):
            if time.monotonic() >= deadline:
                break
            c = convolution(a)
            maximum = float(np.max(c))
            temperature = max(
                maximum * (0.18 * (1.0 - iteration / 100.0) + 0.006),
                1e-15,
            )
            weights = np.exp(np.clip((c - maximum) / temperature, -745.0, 0.0))
            weights /= float(np.sum(weights))

            gradient = np.zeros(n, dtype=float)
            for k, weight in enumerate(weights):
                if weight < 1e-12:
                    continue
                lo = max(0, k - n + 1)
                hi = min(n - 1, k)
                idx = np.arange(lo, hi + 1)
                gradient[idx] += 2.0 * weight * a[k - idx]

            step = 0.85 * (1.0 - 0.55 * iteration / 100.0)
            trial = project_simplex(a - step * gradient)
            trial_peak = objective(trial)
            if trial_peak < current:
                a, current = trial, trial_peak
                remember(a, current)

        polish_iterations = 1050 if start_index < 3 else 750
        for iteration in range(polish_iterations):
            if time.monotonic() >= deadline:
                break

            c = convolution(a)
            gradient, active = active_gradient(a, c, iteration)
            accepted = False

            trust = 0.46 / np.sqrt(1.0 + iteration / 20.0)
            if iteration % 149 == 0:
                trust *= 1.8

            for factor in (1.0, 0.65, 0.42, 0.25, 0.12, 0.06):
                trial = project_simplex(a - trust * factor * gradient)
                trial_peak = objective(trial)
                if trial_peak < current - max(1e-14, current * 1e-12):
                    a, current = trial, trial_peak
                    accepted = True
                    remember(a, current)
                    break

            # Periodically use the same active-set pressure for mass transport.
            # This is useful when projected gradient descent stalls on a plateau.
            if iteration % 19 == 0:
                pressure = np.zeros(n, dtype=float)
                for k, weight in zip(active, c[active]):
                    k = int(k)
                    lo = max(0, k - n + 1)
                    hi = min(n - 1, k)
                    idx = np.arange(lo, hi + 1)
                    pressure[idx] += weight * 2.0 * a[k - idx]

                donors = np.argsort(pressure)[-14:][::-1]
                recipients = np.argsort(pressure)[:20]
                for amount in (8e-4, 2e-4, 5e-5, 1e-5):
                    moves = 0
                    for donor in donors:
                        if moves >= 8 or a[donor] <= 1e-14:
                            continue
                        for recipient in recipients:
                            if donor == recipient:
                                continue
                            transfer = min(amount, 0.30 * a[donor])
                            if transfer <= 1e-14:
                                continue
                            trial = a.copy()
                            trial[donor] -= transfer
                            trial[recipient] += transfer
                            trial_peak = objective(trial)
                            if trial_peak < current - max(1e-14, current * 1e-12):
                                a, current = trial, trial_peak
                                remember(a, current)
                                moves += 1
                                break

            if not accepted and iteration % 67 == 0:
                perturbation = rng.normal(0.0, 1.0e-5, n)
                trial = project_simplex(a + perturbation)
                trial_peak = objective(trial)
                if trial_peak < current:
                    a, current = trial, trial_peak
                    remember(a, current)

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

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Search for a 600-element nonnegative coefficient vector.

    The objective is homogeneous, so the search is performed on the unit
    simplex. Smooth continuation is used to find useful basins, followed by
    exact acceptance-tested minimax descent and small pressure transports.
    """
    import time

    n = 600
    rng = np.random.default_rng(20250308)
    deadline = time.monotonic() + 970.0

    def project_simplex(vector):
        """Project a finite vector onto the nonnegative unit simplex."""
        v = np.asarray(vector, dtype=float)
        if v.size != n or not np.all(np.isfinite(v)):
            return np.full(n, 1.0 / n)

        order = np.sort(v)[::-1]
        cumulative = np.cumsum(order)
        indices = np.arange(1, n + 1, dtype=float)
        valid = order - (cumulative - 1.0) / indices > 0.0

        if not np.any(valid):
            return np.full(n, 1.0 / n)

        rho = int(np.flatnonzero(valid)[-1])
        threshold = (cumulative[rho] - 1.0) / float(rho + 1)
        result = np.maximum(v - threshold, 0.0)
        total = float(np.sum(result))
        if total <= 1e-15 or not np.isfinite(total):
            return np.full(n, 1.0 / n)
        return result / total

    def exact_peak(vector):
        return float(np.max(np.convolve(vector, vector)))

    def remember(vector, peak):
        nonlocal best, best_peak
        if np.isfinite(peak) and peak < best_peak:
            best = np.asarray(vector, dtype=float).copy()
            best_peak = float(peak)

    coordinate = np.linspace(-0.25, 0.25, n)
    arch = 1.0 + 4.0 * np.abs(coordinate) - 16.0 * coordinate * coordinate
    arch = np.maximum(arch, 0.0)
    arch = 0.5 * (arch + arch[::-1])
    arch /= float(np.sum(arch))

    uniform = np.full(n, 1.0 / n)
    best = arch.copy()
    best_peak = exact_peak(best)

    starts = [arch.copy(), uniform.copy()]
    for scale in (0.025, 0.07, 0.16, 0.30):
        candidate = arch * np.exp(rng.normal(0.0, scale, n))
        candidate /= float(np.sum(candidate))
        starts.append(candidate)

    for mixture in (0.25, 0.45):
        random_profile = rng.random(n)
        random_profile /= float(np.sum(random_profile))
        starts.append(project_simplex((1.0 - mixture) * arch +
                                      mixture * random_profile))

    try:
        from scipy.optimize import minimize
    except Exception:
        minimize = None

    def smooth_value_gradient(x, alpha):
        """Return a normalized smooth-max objective and its full gradient."""
        x = np.asarray(x, dtype=float)
        total = float(np.sum(x))
        if total <= 1e-14 or not np.all(np.isfinite(x)):
            return 1e100, np.zeros(n, dtype=float)

        a = x / total
        convolution = np.convolve(a, a)
        maximum = float(np.max(convolution))

        exponent = np.clip(alpha * (convolution - maximum), -745.0, 0.0)
        weights = np.exp(exponent)
        weights /= float(np.sum(weights))

        # For c_i = sum_j a_j a_(i-j), the derivative with respect to
        # a_j is 2 * sum_k weights_(j+k) a_k.  The valid convolution with
        # the reversed vector gives exactly this n-element derivative.
        gradient_a = 2.0 * np.convolve(
            weights, a[::-1], mode="valid"
        )
        correction = float(np.dot(gradient_a, a))
        normalized_gradient = (gradient_a - correction) / total

        log_sum_exp = maximum + np.log(
            float(np.sum(np.exp(exponent)))
        ) / alpha
        value = 2.0 * n * log_sum_exp + (total - 1.0) ** 2
        gradient = (
            2.0 * n * normalized_gradient +
            2.0 * (total - 1.0)
        )
        return float(value), gradient

    def active_gradient(a, convolution):
        """Build a subgradient from the currently active exact peaks."""
        maximum = float(np.max(convolution))
        order = np.argsort(convolution)[::-1]
        if len(order) < 2:
            active = order[:1]
        else:
            gap = maximum - float(convolution[order[1]])
            if gap > maximum * 2.0e-4:
                count = 1
            elif gap > maximum * 5.0e-5:
                count = 2
            else:
                count = min(4, len(order))
            active = order[:count]

        heights = np.maximum(convolution[active], 1e-300)
        weights = heights / float(np.sum(heights))
        peak_weights = np.zeros(2 * n - 1, dtype=float)
        peak_weights[active] = weights

        # As above, use the valid convolution with the reversed coefficient
        # vector so the returned gradient has the same shape as a.
        gradient = 2.0 * np.convolve(
            peak_weights, a[::-1], mode="valid"
        )
        return gradient, active

    for start_number, initial in enumerate(starts):
        if time.monotonic() >= deadline:
            break

        a = project_simplex(initial)
        current = exact_peak(a)
        remember(a, current)

        if minimize is not None:
            continuation = a.copy()
            for alpha in (1e4, 3e4, 1e5, 3e5, 1e6, 3e6):
                if time.monotonic() >= deadline:
                    break
                try:
                    result = minimize(
                        lambda x, q=alpha: smooth_value_gradient(x, q),
                        continuation,
                        method="L-BFGS-B",
                        jac=True,
                        bounds=[(0.0, None)] * n,
                        options={
                            "maxiter": 45,
                            "ftol": 1e-14,
                            "gtol": 1e-8,
                            "maxls": 20,
                        },
                    )
                    trial = np.maximum(np.asarray(result.x, dtype=float), 0.0)
                    total = float(np.sum(trial))
                    if total > 1e-14 and np.all(np.isfinite(trial)):
                        continuation = trial / total
                        score = exact_peak(continuation)
                        if score < current:
                            a = continuation.copy()
                            current = score
                            remember(a, current)
                except Exception:
                    break

        for iteration in range(110):
            if time.monotonic() >= deadline:
                break

            convolution = np.convolve(a, a)
            maximum = float(np.max(convolution))
            temperature = max(
                maximum * (0.18 * (1.0 - iteration / 110.0) + 0.006),
                1e-15,
            )
            weights = np.exp(np.clip(
                (convolution - maximum) / temperature, -745.0, 0.0
            ))
            weights /= float(np.sum(weights))
            gradient = 2.0 * np.convolve(weights, a[::-1], mode="valid")

            step = 0.86 * (1.0 - 0.55 * iteration / 110.0)
            trial = project_simplex(a - step * gradient)
            score = exact_peak(trial)
            if score < current - max(1e-14, current * 1e-12):
                a, current = trial, score
                remember(a, current)

        polish_count = 1250 if start_number < 3 else 850
        for iteration in range(polish_count):
            if time.monotonic() >= deadline:
                break

            convolution = np.convolve(a, a)
            gradient, active = active_gradient(a, convolution)
            accepted = False

            trust = 0.46 / np.sqrt(1.0 + iteration / 20.0)
            if iteration % 149 == 0:
                trust *= 1.8

            for factor in (1.0, 0.65, 0.42, 0.25, 0.12, 0.06):
                trial = project_simplex(a - trust * factor * gradient)
                score = exact_peak(trial)
                if score < current - max(1e-14, current * 1e-12):
                    a, current = trial, score
                    remember(a, current)
                    accepted = True
                    break

            if iteration % 19 == 0:
                pressure_weights = np.zeros(2 * n - 1, dtype=float)
                heights = np.maximum(convolution[active], 1e-300)
                heights /= float(np.sum(heights))
                pressure_weights[active] = heights
                pressure = 2.0 * np.convolve(
                    pressure_weights, a[::-1], mode="valid"
                )

                donors = np.argsort(pressure)[-16:][::-1]
                recipients = np.argsort(pressure)[:24]

                for amount in (8e-4, 2e-4, 5e-5, 1e-5):
                    moves = 0
                    for donor in donors:
                        if moves >= 8 or a[donor] <= 1e-14:
                            continue
                        for recipient in recipients:
                            if donor == recipient:
                                continue
                            transfer = min(amount, 0.30 * a[donor])
                            if transfer <= 1e-14:
                                continue

                            trial = a.copy()
                            trial[donor] -= transfer
                            trial[recipient] += transfer
                            score = exact_peak(trial)
                            if score < current - max(1e-14, current * 1e-12):
                                a, current = trial, score
                                remember(a, current)
                                moves += 1
                                break

            if not accepted and iteration % 67 == 0:
                perturbation = rng.normal(0.0, 1.0e-5, n)
                trial = project_simplex(a + perturbation)
                score = exact_peak(trial)
                if score < current - max(1e-14, current * 1e-12):
                    a, current = trial, score
                    remember(a, current)

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
