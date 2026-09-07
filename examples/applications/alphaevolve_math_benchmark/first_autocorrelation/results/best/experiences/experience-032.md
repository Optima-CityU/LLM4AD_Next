Key optimization mechanisms and strategies enabling improved upper bounds on the autocorrelation inequality objective.

- Multi-stage smooth log-sum-exp continuation: Screening diverse seed profiles across increasing inverse temperatures (from 3e3 to 3e6) via L-BFGS-B on an analytic log-sum-exp surrogate explores broad basins while filtering moves through exact hard-max evaluation.
- Hard-max plateau polishing and active-lag subgradient descent: Constructing simplex-projected subgradient descent steps from all convolution lags within an adaptive relative threshold of the maximum handles peak switching and eliminates isolated spikes left by smooth surrogates.
- Three-level coordinate transport cascade and local pair-shaving: Resolving stagnation in projected descent by computing coordinate pressure over active lags and applying single-donor transfers, local pair-shaving on dominant index pairs, and balanced two-donor/two-recipient mass shifts directly lowers tied convolution peaks.
- Validated variable-length boundary cropping: Evaluating one-sided and two-sided boundary block removals across sizes 1 to 16 against the length-dependent objective score safely exploits the 2*n penalty factor on normalized vectors.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Search for a nonnegative sequence with a small exact convolution peak."""
    import time

    n = 600
    rng = np.random.default_rng(20250308)
    deadline = time.monotonic() + 970.0
    uniform = np.full(n, 1.0 / n)
    xcoord = np.linspace(-0.25, 0.25, n)

    def normalize(x):
        """Normalize a finite vector onto the nonnegative unit simplex."""
        x = np.asarray(x, dtype=float)
        if x.size == 0:
            return x
        x = np.maximum(x, 0.0)
        total = float(np.sum(x))
        if not np.isfinite(total) or total <= 1e-15:
            return np.full(x.size, 1.0 / x.size)
        return x / total

    def score(x):
        """Exact scale-invariant objective used by the external evaluator."""
        x = normalize(x)
        return float(2.0 * x.size * np.max(np.convolve(x, x)))

    def project(x):
        """Euclidean projection of a 600-vector onto the unit simplex."""
        x = np.asarray(x, dtype=float)
        if x.size != n or not np.all(np.isfinite(x)):
            return uniform.copy()

        ordered = np.sort(x)[::-1]
        cumulative = np.cumsum(ordered)
        indices = np.arange(1, n + 1, dtype=float)
        positive = ordered - (cumulative - 1.0) / indices > 0.0
        if not np.any(positive):
            return uniform.copy()

        rho = int(np.flatnonzero(positive)[-1])
        theta = (cumulative[rho] - 1.0) / (rho + 1.0)
        return normalize(np.maximum(x - theta, 0.0))

    def arch(width):
        z = xcoord / max(float(width), 1e-12)
        return normalize(
            np.maximum(1.0 + 4.0 * np.abs(z) - 16.0 * z * z, 0.0)
        )

    # Build a diverse deterministic basin set.
    base = arch(1.0)
    seeds = [uniform.copy()]
    for width in np.arange(0.65, 1.36, 0.05):
        seeds.append(arch(float(width)))

    cosine = np.maximum(
        0.0, 0.5 * (1.0 + np.cos(2.0 * np.pi * xcoord))
    )
    seeds.extend(
        [
            normalize(cosine),
            normalize(0.65 * base + 0.35 * uniform),
            normalize(0.80 * base + 0.20 * cosine),
            normalize(0.50 * base + 0.25 * cosine + 0.25 * uniform),
        ]
    )

    for scale in (0.01, 0.025, 0.05, 0.09, 0.16, 0.25):
        seeds.append(
            normalize(base * np.exp(rng.normal(0.0, scale, n)))
        )

    for shift, skew in (
        (-30, -0.18),
        (-15, -0.10),
        (-6, -0.04),
        (6, 0.04),
        (15, 0.10),
        (30, 0.18),
    ):
        shifted = np.roll(base, shift)
        shifted *= np.exp(skew * np.linspace(-1.0, 1.0, n))
        seeds.append(normalize(shifted))

    for fraction in (0.08, 0.16, 0.28, 0.42, 0.60):
        noise = rng.random(n) ** rng.uniform(0.55, 1.30)
        seeds.append(
            normalize((1.0 - fraction) * base + fraction * noise)
        )

    best = min(seeds, key=score).copy()
    best_value = score(best)

    def remember(x):
        """Update the protected global incumbent using the exact objective."""
        nonlocal best, best_value
        candidate = normalize(x)
        value = score(candidate)
        if np.isfinite(value) and value < best_value:
            best = candidate.copy()
            best_value = value

    try:
        from scipy.optimize import minimize
    except Exception:
        minimize = None

    def smooth_objective(x, temperature):
        """Log-sum-exp convolution maximum and its analytic gradient."""
        x = np.asarray(x, dtype=float)
        total = float(np.sum(x))
        if (
            x.size != n
            or total <= 1e-15
            or not np.all(np.isfinite(x))
        ):
            return 1e100, np.zeros(n)

        a = x / total
        convolution = np.convolve(a, a)
        peak = float(np.max(convolution))
        exponent = np.clip(
            temperature * (convolution - peak), -745.0, 0.0
        )
        exponential = np.exp(exponent)
        exponential_sum = max(float(np.sum(exponential)), 1e-300)
        weights = exponential / exponential_sum

        gradient = 2.0 * np.convolve(
            weights, a[::-1], mode="valid"
        )
        gradient = (
            gradient - float(np.dot(gradient, a))
        ) / total

        value = peak + np.log(exponential_sum) / temperature

        # This weak term prevents the scale-invariant problem from allowing
        # the optimizer's raw variables to drift to inconvenient magnitudes.
        value += 1e-3 * (total - 1.0) ** 2
        gradient += 2e-3 * (total - 1.0)
        return float(value), gradient

    continuation_candidates = []

    if minimize is not None:
        screened = []
        for seed in seeds:
            if time.monotonic() > deadline - 35.0:
                break

            candidate = project(seed)
            for temperature in (3e3, 1e4, 3e4):
                if time.monotonic() > deadline - 35.0:
                    break
                try:
                    result = minimize(
                        lambda y, t=temperature: smooth_objective(y, t),
                        candidate,
                        jac=True,
                        method="L-BFGS-B",
                        bounds=[(0.0, None)] * n,
                        options={
                            "maxiter": 55,
                            "ftol": 1e-12,
                            "gtol": 1e-8,
                            "maxls": 18,
                        },
                    )
                    if np.all(np.isfinite(result.x)):
                        candidate = project(result.x)
                        remember(candidate)
                except Exception:
                    break

            screened.append(candidate.copy())

        basins = sorted(screened + [best.copy()], key=score)[:8]
        continuation_candidates.extend(screened)

        for seed in basins:
            if time.monotonic() > deadline - 35.0:
                break

            candidate = project(seed)
            for temperature in (1e5, 3e5, 1e6, 3e6):
                if time.monotonic() > deadline - 35.0:
                    break
                try:
                    result = minimize(
                        lambda y, t=temperature: smooth_objective(y, t),
                        candidate,
                        jac=True,
                        method="L-BFGS-B",
                        bounds=[(0.0, None)] * n,
                        options={
                            "maxiter": 80,
                            "ftol": 1e-13,
                            "gtol": 5e-9,
                            "maxls": 20,
                        },
                    )
                    if np.all(np.isfinite(result.x)):
                        candidate = project(result.x)
                        remember(candidate)
                except Exception:
                    break

            continuation_candidates.append(candidate.copy())

    def active_lags(a, tolerance=4e-4, limit=18):
        """Return the exact convolution and its capped near-maximum lag set."""
        convolution = np.convolve(a, a)
        peak = float(np.max(convolution))
        active = np.flatnonzero(
            convolution >= peak * (1.0 - tolerance)
        )
        if active.size == 0:
            active = np.array([int(np.argmax(convolution))])
        if active.size > limit:
            active = active[
                np.argsort(convolution[active])[-limit:]
            ]
        return convolution, active

    def try_trial(current, current_value, trial):
        """Accept a move only when it strictly improves the exact score."""
        trial = np.asarray(trial, dtype=float)
        if (
            trial.size != current.size
            or not np.all(np.isfinite(trial))
            or np.any(trial < -1e-14)
        ):
            return current, current_value, False

        trial = normalize(trial)
        value = score(trial)
        threshold = max(1e-14, current_value * 2e-12)
        if np.isfinite(value) and value < current_value - threshold:
            return trial, value, True
        return current, current_value, False

    def coordinate_pressure(a, convolution, active):
        """Compute an active-plateau subgradient for transport ranking."""
        pressure = np.zeros(n, dtype=float)
        active_weights = convolution[active].astype(float)
        active_weights /= max(float(np.sum(active_weights)), 1e-300)

        for lag, weight in zip(active, active_weights):
            lag = int(lag)
            lo = max(0, lag - n + 1)
            hi = min(n - 1, lag)
            indices = np.arange(lo, hi + 1)
            pressure[indices] += (
                2.0 * weight * a[lag - indices]
            )
        return pressure

    def single_transport(a, av, pressure):
        """Try exact single-donor/single-recipient simplex transports."""
        donors = np.argsort(pressure)[-22:][::-1]
        recipients = np.argsort(pressure)[:32]

        for amount in (8e-4, 2e-4, 5e-5, 1e-5, 3e-6, 1e-6):
            for donor in donors:
                moved = min(float(amount), 0.45 * float(a[donor]))
                if moved <= 0.0:
                    continue
                for recipient in recipients:
                    if donor == recipient:
                        continue
                    if time.monotonic() > deadline - 15.0:
                        return a, av, False

                    trial = a.copy()
                    trial[donor] -= moved
                    trial[recipient] += moved
                    candidate, value, accepted = try_trial(
                        a, av, trial
                    )
                    if accepted:
                        return candidate, value, True
        return a, av, False

    def pair_shave(a, av, active):
        """Move mass away from dominant products to nearby balanced pairs."""
        pairs = []
        for lag in active:
            lag = int(lag)
            lo = max(0, lag - n + 1)
            hi = min(n - 1, lag)
            indices = np.arange(lo, hi + 1)
            products = a[indices] * a[lag - indices]

            count = min(14, products.size)
            if count == 0:
                continue
            largest = np.argpartition(products, -count)[-count:]
            largest = largest[np.argsort(products[largest])[::-1]]
            for offset in largest:
                i = int(indices[offset])
                pairs.append(
                    (float(products[offset]), i, lag - i)
                )

        pairs.sort(reverse=True)

        for _, i, j in pairs[:55]:
            for di, dj in (
                (1, 1),
                (-1, -1),
                (1, -1),
                (-1, 1),
            ):
                u, v = i + di, j + dj
                if not (0 <= u < n and 0 <= v < n):
                    continue

                for amount in (2e-4, 5e-5, 1e-5, 3e-6, 1e-6):
                    if time.monotonic() > deadline - 15.0:
                        return a, av, False

                    # The diagonal pair removes twice from the same entry,
                    # so its per-copy removal must be capped accordingly.
                    if i == j:
                        moved = min(
                            amount, 0.225 * float(a[i])
                        )
                    else:
                        moved = min(
                            amount,
                            0.45 * float(a[i]),
                            0.45 * float(a[j]),
                        )
                    if moved <= 0.0:
                        continue

                    trial = a.copy()
                    trial[i] -= moved
                    trial[j] -= moved
                    trial[u] += moved
                    trial[v] += moved

                    candidate, value, accepted = try_trial(
                        a, av, trial
                    )
                    if accepted:
                        return candidate, value, True

        return a, av, False

    def balanced_transport(a, av, pressure):
        """Try mass-conserving two-donor/two-recipient transports."""
        donors = np.argsort(pressure)[-12:][::-1]
        recipients = np.argsort(pressure)[:16]
        donor_ratios = (
            (0.5, 0.5),
            (0.5, 1.5),
            (1.5, 0.5),
        )

        for amount in (2e-4, 5e-5, 1e-5, 3e-6, 1e-6):
            for donor_pos, donor1 in enumerate(donors):
                for donor2 in donors[donor_pos + 1:]:
                    if donor1 == donor2:
                        continue

                    for recipient_pos, recipient1 in enumerate(recipients):
                        for recipient2 in recipients[recipient_pos + 1:]:
                            if (
                                recipient1 == recipient2
                                or recipient1 in (donor1, donor2)
                                or recipient2 in (donor1, donor2)
                            ):
                                continue
                            if time.monotonic() > deadline - 15.0:
                                return a, av, False

                            for ratio1, ratio2 in donor_ratios:
                                removed1 = amount * ratio1
                                removed2 = amount * ratio2
                                if (
                                    removed1 > 0.45 * a[donor1]
                                    or removed2 > 0.45 * a[donor2]
                                ):
                                    continue

                                total_removed = removed1 + removed2
                                trial = a.copy()
                                trial[donor1] -= removed1
                                trial[donor2] -= removed2
                                trial[recipient1] += 0.5 * total_removed
                                trial[recipient2] += 0.5 * total_removed

                                candidate, value, accepted = try_trial(
                                    a, av, trial
                                )
                                if accepted:
                                    return candidate, value, True

        return a, av, False

    # Polish both continuation results and strong untouched seeds. Deduplicate
    # only by score to avoid spending most of the deadline on identical basins.
    start_pool = [best.copy()]
    start_pool.extend(continuation_candidates)
    start_pool.extend(sorted(seeds, key=score)[:8])
    start_pool.sort(key=score)

    starts = []
    start_scores = []
    for candidate in start_pool:
        value = score(candidate)
        if all(abs(value - old) > 1e-11 for old in start_scores):
            starts.append(candidate.copy())
            start_scores.append(value)
        if len(starts) >= 8:
            break

    for initial in starts:
        if time.monotonic() > deadline - 15.0:
            break

        current = project(initial)
        current_value = score(current)
        remember(current)
        failures = 0

        for iteration in range(2200):
            if time.monotonic() > deadline - 15.0:
                break

            tolerance = (
                3e-4
                if iteration < 600
                else 1e-4
                if iteration < 1400
                else 3e-5
            )
            convolution, active = active_lags(
                current, tolerance, 18
            )

            # Begin with a soft active set, then optimize the exact plateau.
            if iteration < 260:
                relative_temperature = (
                    0.20 - 0.15 * iteration / 260.0
                )
                soft_scale = max(
                    float(np.max(convolution)) * relative_temperature,
                    1e-14,
                )
                weights = np.exp(
                    np.clip(
                        (convolution - np.max(convolution)) / soft_scale,
                        -745.0,
                        0.0,
                    )
                )
            else:
                weights = np.zeros(2 * n - 1)
                weights[active] = convolution[active] ** 2

            weights /= max(float(np.sum(weights)), 1e-300)
            gradient = 2.0 * np.convolve(
                weights, current[::-1], mode="valid"
            )

            accepted = False
            for step in (
                0.75,
                0.35,
                0.15,
                0.06,
                0.02,
                0.006,
                0.002,
                0.0006,
            ):
                trial = project(current - step * gradient)
                current, current_value, accepted = try_trial(
                    current, current_value, trial
                )
                if accepted:
                    remember(current)
                    failures = 0
                    break

            if accepted:
                continue

            failures += 1
            if failures < 3:
                continue

            pressure = coordinate_pressure(
                current, convolution, active
            )

            # Exact transport cascade: simple transport, local product
            # shaving, and finally coordinated two-donor transport.
            current, current_value, moved = single_transport(
                current, current_value, pressure
            )
            if not moved:
                current, current_value, moved = pair_shave(
                    current, current_value, active
                )
            if not moved:
                current, current_value, moved = balanced_transport(
                    current, current_value, pressure
                )

            if moved:
                remember(current)
                failures = 0
            elif failures >= 18 and iteration % 9 == 0:
                # Very small projected perturbations can escape a tied
                # plateau without risking the protected incumbent.
                trial = project(
                    current + rng.normal(0.0, 1.2e-6, n)
                )
                current, current_value, moved = try_trial(
                    current, current_value, trial
                )
                if moved:
                    remember(current)
                    failures = 0

    # The evaluator includes the current vector length in its objective.
    # Therefore validate boundary cropping with the full variable-length
    # score, rather than assuming the 600-dimensional result is optimal.
    result = [float(value) for value in best]
    result_value = score(np.asarray(result, dtype=float))

    def crop(left, right):
        nonlocal result, result_value
        if left < 0 or right < 0 or left + right >= len(result):
            return False

        end = len(result) - right if right else None
        values = result[left:end]
        total = float(sum(values))
        if not values or total <= 1e-15:
            return False

        candidate = [float(value / total) for value in values]
        value = score(np.asarray(candidate, dtype=float))
        if np.isfinite(value) and value < result_value:
            result = candidate
            result_value = value
            return True
        return False

    changed = True
    while changed and time.monotonic() < deadline - 3.0:
        changed = False
        length = len(result)

        for size in (1, 2, 4, 8, 12, 16):
            if size >= length - 1:
                continue

            second = min(2 * size, length - size - 1)
            choices = (
                (size, 0),
                (0, size),
                (size, size),
                (size, second),
                (second, size),
            )
            for left, right in choices:
                if crop(left, right):
                    changed = True
                    break
            if changed:
                break

    # Return only ordinary JSON-native, finite, nonnegative Python floats.
    return [float(max(0.0, value)) for value in result]
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
