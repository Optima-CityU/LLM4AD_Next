Targeted pairwise mass transfer around active convolution peaks improves upper bounds when gradient descent and single-coordinate transport stagnate.

- Exact Pairwise Peak-Shaving Transport: Augmenting pressure-guided search with targeted balanced pair transfers from dominant product indices (i, j) at active convolution peaks to adjacent coordinate pairs ((i±1, j±1)) under a descending transfer schedule (2e-4 down to 1e-6) directly reduces peak contributions while maintaining non-negativity.
- Exact Score Acceptance Criterion: Validating every pairwise mass transfer directly against the exact scale-invariant score 2*n*max(convolve(a,a))/(sum(a)**2) and accepting only strict improvements prevents candidate degradation while enabling search to escape plateaus where projected gradient and single-donor transport fail.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Search for a nonnegative sequence with a small normalized convolution peak."""
    import time

    n = 600
    rng = np.random.default_rng(20250308)
    deadline = time.monotonic() + 970.0

    def normalize(values):
        values = np.asarray(values, dtype=float)
        values = np.maximum(values, 0.0)
        total = float(np.sum(values))
        if values.size == 0:
            return values
        if not np.isfinite(total) or total <= 1e-15:
            return np.full(values.size, 1.0 / values.size)
        return values / total

    def score(values):
        values = normalize(values)
        return float(2.0 * values.size * np.max(np.convolve(values, values)))

    def simplex_projection(values):
        values = np.asarray(values, dtype=float)
        if values.size != n or not np.all(np.isfinite(values)):
            return np.full(n, 1.0 / n)

        ordered = np.sort(values)[::-1]
        cumulative = np.cumsum(ordered)
        indices = np.arange(1, n + 1, dtype=float)
        feasible = ordered - (cumulative - 1.0) / indices > 0.0
        if not np.any(feasible):
            return np.full(n, 1.0 / n)

        rho = int(np.flatnonzero(feasible)[-1])
        threshold = (cumulative[rho] - 1.0) / (rho + 1.0)
        return normalize(np.maximum(values - threshold, 0.0))

    coordinates = np.linspace(-0.25, 0.25, n)
    uniform = np.full(n, 1.0 / n)

    def arch(width):
        z = coordinates / max(float(width), 1e-12)
        values = 1.0 + 4.0 * np.abs(z) - 16.0 * z * z
        return normalize(np.maximum(values, 0.0))

    base = arch(1.0)
    seeds = [uniform]
    for width in np.arange(0.70, 1.301, 0.05):
        seeds.append(arch(width))

    raised_cosine = np.maximum(
        0.0, 0.5 * (1.0 + np.cos(2.0 * np.pi * coordinates))
    )
    seeds.extend((
        normalize(raised_cosine),
        normalize(0.65 * base + 0.35 * uniform),
    ))

    for scale in (0.012, 0.025, 0.05, 0.09, 0.16, 0.24):
        seeds.append(normalize(base * np.exp(rng.normal(0.0, scale, n))))

    for shift, skew in (
        (-24, -0.18), (-12, -0.10), (-5, -0.05),
        (5, 0.05), (12, 0.10), (24, 0.18),
    ):
        candidate = np.roll(base, shift)
        candidate *= np.exp(skew * np.linspace(-1.0, 1.0, n))
        seeds.append(normalize(candidate))

    for mixture in (0.08, 0.16, 0.28, 0.42, 0.60):
        random_profile = rng.random(n) ** rng.uniform(0.55, 1.25)
        seeds.append(normalize((1.0 - mixture) * base +
                               mixture * random_profile))

    best = min(seeds, key=score).copy()
    best_score = score(best)

    def remember(candidate):
        nonlocal best, best_score
        candidate = normalize(candidate)
        value = score(candidate)
        if np.isfinite(value) and value < best_score:
            best = candidate.copy()
            best_score = value

    try:
        from scipy.optimize import minimize
    except Exception:
        minimize = None

    def smooth_value_gradient(values, inverse_temperature):
        values = np.asarray(values, dtype=float)
        total = float(np.sum(values))
        if total <= 1e-15 or not np.all(np.isfinite(values)):
            return 1e100, np.zeros(n, dtype=float)

        current = values / total
        convolution = np.convolve(current, current)
        peak = float(np.max(convolution))
        shifted = np.clip(
            inverse_temperature * (convolution - peak), -745.0, 0.0
        )
        exponentials = np.exp(shifted)
        partition = max(float(np.sum(exponentials)), 1e-300)
        weights = exponentials / partition

        gradient = 2.0 * np.convolve(
            weights, current[::-1], mode="valid"
        )
        gradient -= float(np.dot(gradient, current))
        gradient /= total

        smooth_peak = peak + np.log(partition) / inverse_temperature
        penalty = 1e-3 * (total - 1.0) ** 2
        gradient += 2e-3 * (total - 1.0)
        return float(smooth_peak + penalty), gradient

    if minimize is not None:
        screened = []
        for seed in seeds:
            if time.monotonic() >= deadline - 25.0:
                break

            candidate = simplex_projection(seed)
            for temperature in (3e3, 1e4, 3e4):
                if time.monotonic() >= deadline - 25.0:
                    break
                try:
                    result = minimize(
                        lambda values, q=temperature:
                            smooth_value_gradient(values, q),
                        candidate,
                        method="L-BFGS-B",
                        jac=True,
                        bounds=[(0.0, None)] * n,
                        options={
                            "maxiter": 45,
                            "ftol": 1e-12,
                            "gtol": 1e-8,
                            "maxls": 18,
                        },
                    )
                    if np.all(np.isfinite(result.x)):
                        candidate = simplex_projection(result.x)
                        remember(candidate)
                except Exception:
                    break
            screened.append(candidate)

        basins = sorted(screened + [best], key=score)[:8]
        for seed in basins:
            if time.monotonic() >= deadline - 25.0:
                break

            candidate = simplex_projection(seed)
            for temperature in (1e5, 3e5, 1e6, 3e6):
                if time.monotonic() >= deadline - 25.0:
                    break
                try:
                    result = minimize(
                        lambda values, q=temperature:
                            smooth_value_gradient(values, q),
                        candidate,
                        method="L-BFGS-B",
                        jac=True,
                        bounds=[(0.0, None)] * n,
                        options={
                            "maxiter": 70,
                            "ftol": 1e-13,
                            "gtol": 5e-9,
                            "maxls": 20,
                        },
                    )
                    if np.all(np.isfinite(result.x)):
                        candidate = simplex_projection(result.x)
                        remember(candidate)
                except Exception:
                    break

    def exact_step(current, current_value, gradient):
        for step_size in (
            0.75, 0.45, 0.25, 0.13, 0.07, 0.035,
            0.017, 0.008, 0.0035, 0.0015, 0.0006,
        ):
            if time.monotonic() >= deadline - 12.0:
                return current, current_value, False

            trial = simplex_projection(current - step_size * gradient)
            trial_value = score(trial)
            if trial_value < current_value - max(
                1e-14, current_value * 2e-12
            ):
                return trial, trial_value, True

        return current, current_value, False

    def balanced_pair_shave(current, current_value, convolution, active):
        """Try local balanced moves targeting dominant active-lag products."""
        pairs = []
        for lag in active:
            lag = int(lag)
            lo = max(0, lag - n + 1)
            hi = min(n - 1, lag)
            if hi < lo:
                continue

            indices = np.arange(lo, hi + 1)
            products = current[indices] * current[lag - indices]
            if products.size:
                order = np.argsort(products)[-12:][::-1]
                for position in order:
                    i = int(indices[position])
                    j = lag - i
                    pairs.append((float(products[position]), i, j))

        pairs.sort(reverse=True)
        proposals = 0

        for _, i, j in pairs[:48]:
            if time.monotonic() >= deadline - 12.0:
                return current, current_value, False

            for di, dj in (
                (1, 1), (-1, -1), (1, -1), (-1, 1)
            ):
                u, v = i + di, j + dj
                if not (0 <= u < n and 0 <= v < n):
                    continue

                for amount in (2e-4, 5e-5, 1e-5, 3e-6, 1e-6):
                    if time.monotonic() >= deadline - 12.0:
                        return current, current_value, False

                    # If the two source coordinates coincide, two equal
                    # amounts are removed from that coordinate.
                    source_limit = min(current[i], current[j])
                    if i == j:
                        source_limit = current[i] * 0.5
                    moved = min(amount, 0.45 * source_limit)
                    if moved <= 0.0:
                        continue

                    trial = current.copy()
                    trial[i] -= moved
                    trial[j] -= moved
                    trial[u] += moved
                    trial[v] += moved

                    if np.any(trial < -1e-15):
                        continue

                    proposals += 1
                    trial_value = score(trial)
                    if trial_value < current_value - 1e-14:
                        return trial, trial_value, True

                    if proposals >= 900:
                        break
                if proposals >= 900:
                    break
            if proposals >= 900:
                break

        return current, current_value, False

    polish_starts = [best]
    polish_starts.extend(sorted(
        (normalize(seed) for seed in seeds), key=score
    )[:6])

    for initial in polish_starts:
        if time.monotonic() >= deadline - 12.0:
            break

        current = simplex_projection(initial)
        current_value = score(current)
        remember(current)
        failures = 0

        for iteration in range(1800):
            if time.monotonic() >= deadline - 12.0:
                break

            convolution = np.convolve(current, current)
            peak = float(np.max(convolution))

            if iteration < 220:
                relative_temperature = 0.20 - (
                    0.15 * iteration / 220.0
                )
                temperature = max(relative_temperature * peak, 1e-14)
                weights = np.exp(np.clip(
                    (convolution - peak) / temperature, -745.0, 0.0
                ))
                weights /= max(float(np.sum(weights)), 1e-300)
            else:
                tolerance = (
                    3e-4 if iteration < 650 else
                    1e-4 if iteration < 1150 else 3e-5
                )
                active = np.flatnonzero(
                    convolution >= peak * (1.0 - tolerance)
                )
                if active.size == 0:
                    active = np.array([int(np.argmax(convolution))])
                if active.size > 16:
                    active = active[
                        np.argsort(convolution[active])[-16:]
                    ]

                weights = np.zeros(2 * n - 1, dtype=float)
                weights[active] = convolution[active]
                weights /= max(float(np.sum(weights)), 1e-300)

            gradient = 2.0 * np.convolve(
                weights, current[::-1], mode="valid"
            )
            current, current_value, accepted = exact_step(
                current, current_value, gradient
            )

            if accepted:
                remember(current)
                failures = 0
                continue

            failures += 1
            if failures < 3 or (iteration % 3 and failures < 12):
                continue

            active = np.flatnonzero(
                convolution >= peak * (1.0 - 5e-4)
            )
            if active.size == 0:
                active = np.array([int(np.argmax(convolution))])
            if active.size > 12:
                active = active[np.argsort(convolution[active])[-12:]]

            pressure = np.zeros(n, dtype=float)
            for lag in active:
                lo = max(0, int(lag) - n + 1)
                hi = min(n - 1, int(lag))
                for i in range(lo, hi + 1):
                    pressure[i] += current[i] * current[int(lag) - i]

            donors = np.argsort(pressure)[-18:][::-1]
            recipients = np.argsort(pressure)[:28]
            moved = False

            for amount in (8e-4, 2e-4, 5e-5, 1e-5, 3e-6, 1e-6):
                if moved or time.monotonic() >= deadline - 12.0:
                    break
                for donor in donors:
                    if current[donor] <= amount:
                        continue
                    for recipient in recipients:
                        if donor == recipient:
                            continue

                        trial = current.copy()
                        moved_amount = min(amount, 0.45 * current[donor])
                        trial[donor] -= moved_amount
                        trial[recipient] += moved_amount
                        trial_value = score(trial)

                        if trial_value < current_value - 1e-14:
                            current = trial
                            current_value = trial_value
                            remember(current)
                            moved = True
                            break
                    if moved:
                        break

            if moved:
                failures = 0
                continue

            # Targeted balanced pair-shaving is used after ordinary transport
            # fails, and is always checked with the exact objective.
            if failures >= 3:
                current, current_value, moved = balanced_pair_shave(
                    current, current_value, convolution, active
                )
                if moved:
                    remember(current)
                    failures = 0
                    continue

            if failures >= 24 and iteration % 11 == 0:
                trial = simplex_projection(
                    current + rng.normal(0.0, 1.5e-6, n)
                )
                trial_value = score(trial)
                if trial_value < current_value:
                    current, current_value = trial, trial_value
                    remember(current)
                    failures = 0

    result = [float(value) for value in best]
    result_score = score(np.asarray(result))

    def try_crop(left, right):
        nonlocal result, result_score
        length = len(result)
        if left < 0 or right < 0 or left + right >= length:
            return False

        end = length - right if right else None
        values = result[left:end]
        total = float(sum(values))
        if not values or total <= 1e-15:
            return False

        candidate = [float(value / total) for value in values]
        candidate_array = np.asarray(candidate)
        candidate_score = float(
            2.0 * len(candidate) *
            np.max(np.convolve(candidate_array, candidate_array))
        )
        if np.isfinite(candidate_score) and candidate_score < result_score:
            result = candidate
            result_score = candidate_score
            return True
        return False

    changed = True
    while changed and time.monotonic() < deadline - 3.0:
        changed = False
        length = len(result)
        for size in (1, 2, 4, 8, 12, 16):
            if size >= length - 1:
                continue

            options = (
                (size, 0),
                (0, size),
                (size, size),
                (size, min(2 * size, length - size - 1)),
                (min(2 * size, length - size - 1), size),
            )
            for left, right in options:
                if try_crop(left, right):
                    changed = True

    return [float(value) for value in result]
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
