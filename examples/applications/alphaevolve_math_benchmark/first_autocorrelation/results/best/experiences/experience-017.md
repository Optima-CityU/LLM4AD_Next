Adding exact boundary-trimming postprocessing after fixed-dimension continuous optimization improves objective values sensitive to sequence length.

- Exact Boundary-Trimming Postprocessor: Applies a variable-length postprocessing stage to the best normalized 600-entry candidate sequence by iteratively testing symmetric and asymmetric crops (e.g., removing 1, 2, 4, 8, 12, or 16 boundary entries from either or both ends), renormalizing the cropped vector on the unit simplex, and evaluating it under the exact hard-maximum objective.
- Exact Boundary-Trimming Postprocessor: Exploits the explicit linear factor of n in the evaluation objective (2 * n * max(convolve(sequence, sequence)) / (sum(sequence)^2)), lowering the bound whenever mass is concentrated away from near-zero boundary entries.
- Exact Boundary-Trimming Postprocessor: Preserves optimizer efficiency by retaining fixed-dimensional optimization during the main continuation, active-set polishing, and transport phases while safely accepting length reductions only when the exact evaluation score strictly improves over the 600-entry baseline.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Search for a low-peak nonnegative sequence and conservatively trim it."""
    import time

    n = 600
    rng = np.random.default_rng(20250308)
    deadline = time.monotonic() + 970.0

    def normalize(x):
        x = np.asarray(x, dtype=float)
        x = np.maximum(x, 0.0)
        total = float(np.sum(x))
        if not np.isfinite(total) or total <= 1e-14:
            return np.full(n, 1.0 / n)
        return x / total

    def peak(x):
        return float(np.max(np.convolve(x, x)))

    def project_simplex(x):
        """Project a vector onto the unit simplex."""
        x = np.asarray(x, dtype=float)
        if x.size != n or not np.all(np.isfinite(x)):
            return np.full(n, 1.0 / n)

        u = np.sort(x)[::-1]
        cssv = np.cumsum(u)
        indices = np.arange(1, n + 1, dtype=float)
        valid = u - (cssv - 1.0) / indices > 0.0
        if not np.any(valid):
            return np.full(n, 1.0 / n)

        rho = int(np.flatnonzero(valid)[-1])
        theta = (cssv[rho] - 1.0) / float(rho + 1)
        result = np.maximum(x - theta, 0.0)
        total = float(np.sum(result))
        if total <= 1e-14 or not np.isfinite(total):
            return np.full(n, 1.0 / n)
        return result / total

    coordinate = np.linspace(-0.25, 0.25, n)
    arch = 1.0 + 4.0 * np.abs(coordinate) - 16.0 * coordinate * coordinate
    arch = np.maximum(arch, 0.0)
    arch = 0.5 * (arch + arch[::-1])
    arch /= float(np.sum(arch))

    uniform = np.full(n, 1.0 / n)
    best = arch.copy()
    best_score = peak(best)

    def remember(x, score=None):
        nonlocal best, best_score
        x = np.asarray(x, dtype=float)
        if score is None:
            score = peak(x)
        if (
            np.isfinite(score)
            and x.size == n
            and np.all(np.isfinite(x))
            and np.min(x) >= -1e-12
            and abs(float(np.sum(x)) - 1.0) < 1e-8
            and score < best_score
        ):
            best = np.maximum(x, 0.0)
            best /= float(np.sum(best))
            best_score = float(score)

    starts = [arch, uniform]
    for scale in (0.02, 0.05, 0.10, 0.18, 0.28):
        x = arch * np.exp(rng.normal(0.0, scale, n))
        starts.append(normalize(x))

    for mixing in (0.15, 0.30, 0.45, 0.60):
        random_profile = rng.random(n) ** rng.uniform(0.55, 1.1)
        random_profile /= float(np.sum(random_profile))
        starts.append(
            project_simplex((1.0 - mixing) * arch + mixing * random_profile)
        )

    for shift, skew in ((7, 0.10), (-9, -0.08), (15, 0.16), (-18, -0.12)):
        x = np.roll(arch, shift)
        x *= np.exp(skew * np.linspace(-1.0, 1.0, n))
        starts.append(normalize(x))

    for start in starts:
        remember(project_simplex(start))

    try:
        from scipy.optimize import minimize
    except Exception:
        minimize = None

    def smooth_objective(x, alpha):
        """Smooth approximation to the maximum convolution coefficient."""
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

        gradient_a = 2.0 * np.convolve(
            weights, a[::-1], mode="valid"
        )
        gradient_a -= float(np.dot(gradient_a, a))
        gradient_x = gradient_a / total

        smooth_peak = maximum + np.log(float(np.sum(np.exp(exponent)))) / alpha
        value = 2.0 * n * smooth_peak + (total - 1.0) ** 2
        gradient = 2.0 * n * gradient_x + 2.0 * (total - 1.0)
        return float(value), gradient

    for start_number, initial in enumerate(starts):
        if time.monotonic() >= deadline:
            break

        current = project_simplex(initial)
        current_score = peak(current)
        remember(current, current_score)

        if minimize is not None:
            continuation = current.copy()
            for alpha in (1e4, 3e4, 1e5, 3e5, 1e6, 3e6):
                if time.monotonic() >= deadline:
                    break
                try:
                    result = minimize(
                        lambda x, q=alpha: smooth_objective(x, q),
                        continuation,
                        method="L-BFGS-B",
                        jac=True,
                        bounds=[(0.0, None)] * n,
                        options={
                            "maxiter": 70,
                            "ftol": 1e-14,
                            "gtol": 1e-8,
                            "maxls": 25,
                        },
                    )
                    candidate = np.asarray(result.x, dtype=float)
                    if np.all(np.isfinite(candidate)):
                        continuation = project_simplex(candidate)
                        candidate_score = peak(continuation)
                        remember(continuation, candidate_score)
                        if candidate_score < current_score:
                            current = continuation.copy()
                            current_score = candidate_score
                except Exception:
                    break

        # Finish with projected nonsmooth descent.
        for iteration in range(220):
            if time.monotonic() >= deadline:
                break

            convolution = np.convolve(current, current)
            maximum = float(np.max(convolution))
            temperature = max(
                maximum * (0.18 - 0.14 * iteration / 220.0),
                1e-15,
            )
            weights = np.exp(
                np.clip((convolution - maximum) / temperature, -745.0, 0.0)
            )
            weights /= float(np.sum(weights))
            gradient = 2.0 * np.convolve(
                weights, current[::-1], mode="valid"
            )

            step = 0.65 * (1.0 - 0.35 * iteration / 220.0)
            candidate = project_simplex(current - step * gradient)
            candidate_score = peak(candidate)
            if candidate_score < current_score - max(
                1e-14, current_score * 1e-12
            ):
                current = candidate
                current_score = candidate_score
                remember(current, current_score)

        # A short exact active-set polishing phase.
        failures = 0
        for iteration in range(1000 if start_number < 4 else 600):
            if time.monotonic() >= deadline:
                break

            convolution = np.convolve(current, current)
            maximum = float(np.max(convolution))
            active = np.flatnonzero(
                convolution >= maximum - 2e-4 * max(maximum, 1e-15)
            )
            if active.size == 0:
                active = np.array([int(np.argmax(convolution))])

            weights = np.zeros(2 * n - 1, dtype=float)
            weights[active] = convolution[active]
            weights /= float(np.sum(weights))
            gradient = 2.0 * np.convolve(
                weights, current[::-1], mode="valid"
            )

            trust = 0.38 / np.sqrt(1.0 + iteration / 35.0)
            accepted = False
            for factor in (1.0, 0.65, 0.35, 0.16, 0.07):
                candidate = project_simplex(
                    current - trust * factor * gradient
                )
                candidate_score = peak(candidate)
                if candidate_score < current_score - max(
                    1e-14, current_score * 1e-12
                ):
                    current = candidate
                    current_score = candidate_score
                    remember(current, current_score)
                    accepted = True
                    failures = 0
                    break

            if not accepted:
                failures += 1

            if failures >= 35 and iteration % 17 == 0:
                candidate = project_simplex(
                    current + rng.normal(0.0, 5e-6, n)
                )
                candidate_score = peak(candidate)
                if candidate_score < current_score:
                    current = candidate
                    current_score = candidate_score
                    remember(current, current_score)
                    failures = 0

    # Keep the complete vector as a fallback, then search progressively
    # larger boundary crops using the exact evaluator objective.
    original = [float(v) for v in best]
    cropped_best = original
    cropped_score = float(2 * len(cropped_best) * peak(best))

    def consider_crop(left, right):
        nonlocal cropped_best, cropped_score
        if left < 0 or right < 0 or left + right >= len(original):
            return False
        values = original[left:len(original) - right if right else None]
        if not values:
            return False
        total = float(sum(values))
        if total <= 0.0:
            return False
        candidate = [float(v / total) for v in values]
        convolution = np.convolve(candidate, candidate)
        score = float(2 * len(candidate) * np.max(convolution))
        if np.isfinite(score) and score < cropped_score:
            cropped_best = candidate
            cropped_score = score
            return True
        return False

    crop_sizes = (1, 2, 4, 8, 12, 16)
    improved = True
    while improved and time.monotonic() < deadline:
        improved = False
        for size in crop_sizes:
            candidates = (
                (size, 0),
                (0, size),
                (size, size),
                (size, min(2 * size, len(original) - size - 1)),
                (min(2 * size, len(original) - size - 1), size),
            )
            for left, right in candidates:
                if consider_crop(left, right):
                    improved = True

    return [float(value) for value in cropped_best]
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
