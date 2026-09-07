A multi-stage optimization pipeline combining warm-start smooth surrogate continuation, exact plateau subgradient descent, targeted mass transport, and variable-length postprocessing achieved an upper bound of 1.51383.

- Continuation Plateau-Minimax with Exact Transport and Adaptive Trimming: The algorithm initializes diverse multi-start candidates on the nonnegative unit-sum simplex using symmetric quadratic arch profiles, uniform profiles, multiplicatively perturbed arches, and asymmetric arch-random mixtures to explore distinct basins.
- L-BFGS-B smooth-max continuation: The method executes a warm-start L-BFGS-B optimization sequence over increasing log-sum-exp smoothing parameters (alpha from 1e4 to 3e6) with full chain-rule gradients and simplex constraints, tracking the best candidate evaluated on the exact hard maximum after each stage.
- Adaptive plateau active-set descent: To resolve nondifferentiable tied peaks and surrogate drift, the algorithm calculates height-weighted averages of exact subgradients across active convolution lags and executes decreasing projected trust-region steps accepted strictly on exact objective reduction.
- Pressure transport mechanism: The optimization redistributes mass from donor coordinates with maximal pair-pressure contributions on active convolution peaks to low-pressure recipient coordinates across scheduled step sizes (8e-4 down to 1e-5), accepting updates only upon strict exact evaluation improvement.
- Validated boundary-trimming postprocessor: Following fixed-length (n=600) optimization, the algorithm iteratively tests symmetric and asymmetric boundary removals (1, 2, 4, 8, 12, and 16 entries) followed by renormalization and exact evaluation, exploiting the sequence-length factor in the objective function.
- Smooth-Max Continuation and Active-Plateau Polishing Pipeline: Warm-start L-BFGS-B continuation over increasing inverse temperatures (alpha from 1e4 to 3e6) guides asymmetric multi-start profiles into high-quality basins, while subsequent exact active-plateau minimax subgradient steps directly suppress tied convolution peaks that smooth surrogates overlook.
- Pressure-Based Mass Transport and Validated Boundary Trimming: Pressure-based coordinate transport selectively moves mass from high-contribution pair coordinates to low-pressure coordinates under strict exact evaluation acceptance, and post-processing variable-length boundary trimming safely exploits length-dependent evaluation penalties without risking incumbent regression.
- Unit simplex normalization: Normalizing candidate sequences such that sum(a) = 1 reduces the scale-invariant autocorrelation evaluation function 2 * n * max(convolve(a, a)) / (sum(a)**2) directly to 2 * n * max(convolve(a, a)) during optimization.
- Smooth-max L-BFGS-B continuation: Warm-starting L-BFGS-B across increasing log-sum-exp sharpness schedules (from 1e4 up to 3e6) with full normalization chain-rule gradients identifies promising basins while avoiding premature convergence to non-smooth local minima.
- Active plateau minimax polishing: Projected simplex subgradient descent on an adaptive active set of the worst convolution lags resolves peak-swapping dynamics that smooth surrogates miss, accepting proposals only upon strict decrease of the exact hard maximum.
- Pressure-based mass transport: When projected gradient descent stalls, computing coordinate contributions to active peak lag products a[i]*a[k-i] identifies high-pressure donors and low-pressure recipients to transfer mass and escape active-set local traps.
- Validated boundary trimming postprocessing: Testing asymmetric and symmetric boundary crops across sizes 1 to 16 and accepting improvements based on 2 * length * max(convolve(crop, crop)) directly exploits the multiplicative length factor to lower the objective bound.
- Warm-start log-sum-exp continuation screening: Performs initial basin identification by screening diverse parameterized arch and perturbation seeds using warm-start L-BFGS-B on smooth log-sum-exp approximations across increasing inverse temperatures (3e3 to 3e6), concentrating execution time on the highest-ranked candidate basins.
- Active plateau subgradient polishing: Resolves tied convolution peaks during projected simplex trust-region descent by weighting subgradients across an adaptive active lag set within a narrow tolerance of the maximum self-convolution value rather than relying on a single peak gradient.
- Pressure-guided mass transport and validated cropping: Escapes gradient plateaus by transferring mass from high-pressure donor coordinates to low-pressure recipient coordinates based on pairwise contribution products, and applies length-aware postprocessing that accepts boundary cropping only when re-evaluated scale-adjusted scores strictly improve.
- Adaptive Plateau Transport with Dual Pair-Shaving and Validated Cropping: The algorithm screens diverse geometric seeds (uniform, quadratic arches, raised cosines, and mixtures) on the unit simplex using L-BFGS-B over a smooth log-sum-exp convolution maximum across inverse temperatures from 3e3 to 3e6 with simplex projection and analytic gradients.
- Adaptive Plateau Transport with Dual Pair-Shaving and Validated Cropping: The algorithm refines the best basin via plateau-minimax polishing with weighted subgradients across active convolution lags, falling back on stagnation to hierarchical exact-tested transport moves: single-donor/recipient coordinate pressure transport, local balanced pair-shaving on dominant active-lag index pairs, and two-donor/two-recipient mass transport.
- Adaptive Plateau Transport with Dual Pair-Shaving and Validated Cropping: The algorithm postprocesses the protected incumbent by testing one-sided and symmetric boundary crops across sizes 1 to 16, accepting trimmed sequences only when the full length-adjusted objective strictly improves.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Search the nonnegative simplex for a low convolution peak."""
    import time

    n = 600
    rng = np.random.default_rng(20250308)
    deadline = time.monotonic() + 970.0
    uniform = np.full(n, 1.0 / n)

    def normalize(x):
        x = np.asarray(x, dtype=float)
        x = np.maximum(x, 0.0)
        s = float(np.sum(x))
        if not np.isfinite(s) or s <= 1e-15:
            return uniform.copy()
        return x / s

    def peak(x):
        return float(np.max(np.convolve(x, x)))

    def project_simplex(x):
        x = np.asarray(x, dtype=float)
        if x.size != n or not np.all(np.isfinite(x)):
            return uniform.copy()
        u = np.sort(x)[::-1]
        cssv = np.cumsum(u)
        ind = np.arange(1, n + 1, dtype=float)
        good = u - (cssv - 1.0) / ind > 0.0
        if not np.any(good):
            return uniform.copy()
        rho = int(np.flatnonzero(good)[-1])
        theta = (cssv[rho] - 1.0) / (rho + 1.0)
        y = np.maximum(x - theta, 0.0)
        return normalize(y)

    coordinate = np.linspace(-0.25, 0.25, n)
    arch = 1.0 + 4.0 * np.abs(coordinate) - 16.0 * coordinate**2
    arch = np.maximum(arch, 0.0)
    arch = normalize(0.5 * (arch + arch[::-1]))

    best = arch.copy()
    best_score = peak(best)

    def remember(x, score=None):
        nonlocal best, best_score
        x = np.asarray(x, dtype=float)
        if score is None:
            score = peak(x)
        if (
            x.size == n
            and np.isfinite(score)
            and np.all(np.isfinite(x))
            and np.min(x) >= -1e-11
            and abs(float(np.sum(x)) - 1.0) < 1e-7
            and score < best_score
        ):
            best = normalize(x)
            best_score = float(score)

    starts = [arch, uniform]
    for scale in (0.02, 0.05, 0.10, 0.18, 0.28):
        starts.append(normalize(arch * np.exp(rng.normal(0.0, scale, n))))

    for mixing in (0.15, 0.30, 0.45, 0.60):
        random_profile = rng.random(n) ** rng.uniform(0.55, 1.1)
        starts.append(normalize((1.0 - mixing) * arch + mixing * random_profile))

    for shift, skew in ((7, 0.10), (-9, -0.08), (15, 0.16), (-18, -0.12)):
        x = np.roll(arch, shift)
        x *= np.exp(skew * np.linspace(-1.0, 1.0, n))
        starts.append(normalize(x))

    try:
        from scipy.optimize import minimize
    except Exception:
        minimize = None

    def smooth_objective(x, alpha):
        """Log-sum-exp convolution objective and its full gradient."""
        x = np.asarray(x, dtype=float)
        total = float(np.sum(x))
        if total <= 1e-15 or not np.all(np.isfinite(x)):
            return 1e100, np.zeros(n, dtype=float)

        a = x / total
        c = np.convolve(a, a)
        m = float(np.max(c))
        shifted = np.clip(alpha * (c - m), -745.0, 0.0)
        weights = np.exp(shifted)
        weights /= float(np.sum(weights))

        raw_gradient = 2.0 * np.convolve(
            weights, a[::-1], mode="valid"
        )
        # Chain rule for a=x/sum(x).
        gradient_a = (raw_gradient - float(np.dot(raw_gradient, a))) / total
        smooth_peak = m + np.log(float(np.sum(np.exp(shifted)))) / alpha
        value = 2.0 * n * smooth_peak + (total - 1.0) ** 2
        gradient = 2.0 * n * gradient_a + 2.0 * (total - 1.0)
        return float(value), gradient

    def exact_step(current, score, gradient, scales):
        for scale in scales:
            if time.monotonic() >= deadline:
                return current, score, False
            candidate = project_simplex(current - scale * gradient)
            candidate_score = peak(candidate)
            if candidate_score < score - max(1e-14, score * 1e-12):
                return candidate, candidate_score, True
        return current, score, False

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
                        lambda z, q=alpha: smooth_objective(z, q),
                        continuation,
                        method="L-BFGS-B",
                        jac=True,
                        bounds=[(0.0, None)] * n,
                        options={
                            "maxiter": 90,
                            "ftol": 1e-14,
                            "gtol": 1e-8,
                            "maxls": 25,
                        },
                    )
                    candidate = np.asarray(result.x, dtype=float)
                    if np.all(np.isfinite(candidate)):
                        continuation = project_simplex(candidate)
                        value = peak(continuation)
                        remember(continuation, value)
                        if value < current_score:
                            current, current_score = continuation, value
                except Exception:
                    break

        failures = 0
        iterations = 1150 if start_number < 5 else 650
        for iteration in range(iterations):
            if time.monotonic() >= deadline:
                break

            convolution = np.convolve(current, current)
            maximum = float(np.max(convolution))

            # Annealed minimax phase, followed by an adaptive active plateau.
            if iteration < 220:
                temperature = max(
                    maximum * (0.18 - 0.14 * iteration / 220.0), 1e-15
                )
                weights = np.exp(
                    np.clip((convolution - maximum) / temperature, -745.0, 0.0)
                )
                weights /= float(np.sum(weights))
            else:
                relative_band = (
                    2e-4 if iteration < 600 else 7e-5
                )
                active = np.flatnonzero(
                    convolution >= maximum - relative_band * max(maximum, 1e-15)
                )
                if active.size == 0:
                    active = np.array([int(np.argmax(convolution))])
                if active.size > 4:
                    order = np.argsort(convolution[active])[::-1]
                    active = active[order[:4]]
                weights = np.zeros(2 * n - 1, dtype=float)
                weights[active] = convolution[active]
                weights /= max(float(np.sum(weights)), 1e-30)

            gradient = 2.0 * np.convolve(
                weights, current[::-1], mode="valid"
            )
            trust = 0.42 / np.sqrt(1.0 + iteration / 38.0)
            candidate, candidate_score, accepted = exact_step(
                current,
                current_score,
                gradient,
                (trust, 0.68 * trust, 0.38 * trust, 0.17 * trust, 0.07 * trust),
            )

            if accepted:
                current, current_score = candidate, candidate_score
                remember(current, current_score)
                failures = 0
                continue

            failures += 1

            # Targeted pressure transport from coordinates responsible for
            # the currently active convolution peaks.
            if iteration >= 180 and (iteration % 3 == 0 or failures > 8):
                convolution = np.convolve(current, current)
                maximum = float(np.max(convolution))
                active = np.flatnonzero(
                    convolution >= maximum - 3e-4 * max(maximum, 1e-15)
                )
                if active.size > 4:
                    active = active[np.argsort(convolution[active])[-4:]]
                pressure = np.zeros(n, dtype=float)
                for lag in active:
                    for i in range(n):
                        j = int(lag) - i
                        if 0 <= j < n:
                            pressure[i] += current[i] * current[j]
                donors = np.argsort(pressure)[-12:][::-1]
                recipients = np.argsort(pressure)[:20]
                moved = False
                for amount in (8e-4, 2e-4, 5e-5, 1e-5):
                    if moved:
                        break
                    for donor in donors[:8]:
                        if current[donor] <= amount:
                            continue
                        for recipient in recipients[:12]:
                            if donor == recipient:
                                continue
                            trial = current.copy()
                            trial[donor] -= amount
                            trial[recipient] += amount
                            trial_score = peak(trial)
                            if trial_score < current_score - 1e-14:
                                current, current_score = trial, trial_score
                                remember(current, current_score)
                                moved = True
                                break
                        if moved:
                            break
                if moved:
                    failures = 0
                elif failures >= 30 and iteration % 11 == 0:
                    trial = project_simplex(
                        current + rng.normal(0.0, 3e-6, n)
                    )
                    trial_score = peak(trial)
                    if trial_score < current_score:
                        current, current_score = trial, trial_score
                        remember(current, current_score)
                        failures = 0

    # Exact boundary trimming.  The original 600-vector is retained unless
    # a crop is strictly better under the evaluator's length penalty.
    cropped = [float(v) for v in best]
    cropped_score = float(2.0 * len(cropped) * peak(best))

    def consider_crop(left, right):
        nonlocal cropped, cropped_score
        if left < 0 or right < 0 or left + right >= len(cropped):
            return False
        end = len(cropped) - right if right else None
        values = cropped[left:end]
        total = float(sum(values))
        if not values or total <= 0.0:
            return False
        candidate = [float(v / total) for v in values]
        score = float(2.0 * len(candidate) * np.max(np.convolve(candidate, candidate)))
        if np.isfinite(score) and score < cropped_score:
            cropped, cropped_score = candidate, score
            return True
        return False

    improved = True
    while improved and time.monotonic() < deadline:
        improved = False
        length = len(cropped)
        for size in (1, 2, 4, 8, 12, 16):
            if size >= length - 1:
                continue
            choices = (
                (size, 0),
                (0, size),
                (size, size),
                (size, min(2 * size, length - size - 1)),
                (min(2 * size, length - size - 1), size),
            )
            for left, right in choices:
                if consider_crop(left, right):
                    improved = True

    return [float(v) for v in cropped]
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
    """Find a nonnegative coefficient vector with a small self-convolution peak."""
    import time

    n = 600
    rng = np.random.default_rng(20250308)
    deadline = time.monotonic() + 970.0
    uniform = np.full(n, 1.0 / n, dtype=float)

    def normalize(x):
        x = np.asarray(x, dtype=float)
        if x.size != n or not np.all(np.isfinite(x)):
            return uniform.copy()
        x = np.maximum(x, 0.0)
        total = float(np.sum(x))
        if total <= 1e-15:
            return uniform.copy()
        return x / total

    def project_simplex(x):
        """Euclidean projection onto the n-dimensional probability simplex."""
        x = np.asarray(x, dtype=float)
        if x.size != n or not np.all(np.isfinite(x)):
            return uniform.copy()

        u = np.sort(x)[::-1]
        cssv = np.cumsum(u)
        indices = np.arange(1, n + 1, dtype=float)
        valid = u - (cssv - 1.0) / indices > 0.0
        if not np.any(valid):
            return uniform.copy()
        rho = int(np.flatnonzero(valid)[-1])
        theta = (cssv[rho] - 1.0) / float(rho + 1)
        return normalize(np.maximum(x - theta, 0.0))

    def peak(x):
        return float(np.max(np.convolve(x, x)))

    coordinate = np.linspace(-0.25, 0.25, n)
    arch = 1.0 + 4.0 * np.abs(coordinate) - 16.0 * coordinate * coordinate
    arch = normalize(np.maximum(arch, 0.0))
    arch = normalize(0.5 * (arch + arch[::-1]))

    best = arch.copy()
    best_score = peak(best)

    def remember(x, score=None):
        nonlocal best, best_score
        x = normalize(x)
        if score is None:
            score = peak(x)
        if np.isfinite(score) and score < best_score:
            best = x.copy()
            best_score = float(score)

    # The starts deliberately include asymmetric profiles.  The convolution
    # objective does not require the coefficient vector itself to be symmetric.
    starts = [uniform.copy(), arch.copy()]
    for scale in (0.02, 0.05, 0.10, 0.18, 0.28):
        starts.append(normalize(arch * np.exp(rng.normal(0.0, scale, n))))

    for power in (0.55, 0.75, 1.0, 1.25):
        random_profile = rng.random(n) ** power
        starts.append(normalize(0.65 * arch + 0.35 * random_profile))

    for shift, skew in ((7, 0.10), (-9, -0.08), (15, 0.16), (-18, -0.12)):
        trial = np.roll(arch, shift)
        trial *= np.exp(skew * np.linspace(-1.0, 1.0, n))
        starts.append(normalize(trial))

    try:
        from scipy.optimize import minimize
    except Exception:
        minimize = None

    def smooth_objective(x, alpha):
        """Smooth max of the convolution and its complete gradient."""
        x = np.asarray(x, dtype=float)
        total = float(np.sum(x))
        if total <= 1e-15 or not np.all(np.isfinite(x)):
            return 1e100, np.zeros(n, dtype=float)

        a = x / total
        convolution = np.convolve(a, a)
        maximum = float(np.max(convolution))

        shifted = np.clip(alpha * (convolution - maximum), -745.0, 0.0)
        exponentials = np.exp(shifted)
        weight_sum = float(np.sum(exponentials))
        weights = exponentials / max(weight_sum, 1e-300)

        # d (a*a) / d a = 2 * convolution(weights, reverse(a)).
        raw_gradient = 2.0 * np.convolve(
            weights, a[::-1], mode="valid"
        )
        # Account for a = x / sum(x).
        gradient_a = (
            raw_gradient - float(np.dot(raw_gradient, a))
        ) / total

        smooth_peak = maximum + np.log(weight_sum) / alpha
        value = 2.0 * n * smooth_peak + (total - 1.0) ** 2
        gradient = 2.0 * n * gradient_a + 2.0 * (total - 1.0)
        return float(value), gradient

    def exact_descent_step(current, current_score, gradient):
        """Use backtracking and accept only a strictly better hard maximum."""
        scale = 0.40
        for _ in range(8):
            if time.monotonic() >= deadline:
                break
            candidate = project_simplex(current - scale * gradient)
            candidate_score = peak(candidate)
            if candidate_score < current_score - max(
                1e-14, 1e-12 * current_score
            ):
                return candidate, candidate_score, True
            scale *= 0.48
        return current, current_score, False

    def active_gradient(current):
        convolution = np.convolve(current, current)
        maximum = float(np.max(convolution))
        relative_gap = 2.0e-4 if maximum > 0 else 0.0
        active = np.flatnonzero(
            convolution >= maximum * (1.0 - relative_gap)
        )

        # Use one lag when isolated, two when nearly tied, and at most four
        # in a genuine plateau.
        if active.size == 0:
            active = np.array([int(np.argmax(convolution))])
        elif active.size > 4:
            order = np.argsort(convolution[active])[::-1]
            active = active[order[:4]]

        weights = np.zeros(2 * n - 1, dtype=float)
        weights[active] = convolution[active]
        weights /= max(float(np.sum(weights)), 1e-300)
        return 2.0 * np.convolve(weights, current[::-1], mode="valid"), convolution

    def transport(current, current_score, convolution):
        """Try moving mass from high-pressure coordinates to low-pressure ones."""
        maximum = float(np.max(convolution))
        active = np.flatnonzero(
            convolution >= maximum * (1.0 - 3.0e-4)
        )
        if active.size > 4:
            order = np.argsort(convolution[active])[::-1]
            active = active[order[-4:]]

        pressure = np.zeros(n, dtype=float)
        for lag in active:
            lo = max(0, int(lag) - (n - 1))
            hi = min(n - 1, int(lag))
            indices = np.arange(lo, hi + 1)
            partners = int(lag) - indices
            pressure[indices] += current[indices] * current[partners]

        donors = np.argsort(pressure)[-14:][::-1]
        recipients = np.argsort(pressure)[:24]

        for amount in (8e-4, 2e-4, 5e-5, 1e-5):
            for donor in donors[:10]:
                if current[donor] <= amount:
                    continue
                for recipient in recipients[:16]:
                    if donor == recipient:
                        continue
                    candidate = current.copy()
                    candidate[donor] -= amount
                    candidate[recipient] += amount
                    score = peak(candidate)
                    if score < current_score - 1e-14:
                        return candidate, score, True
        return current, current_score, False

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
                        lambda z, q=alpha: smooth_objective(z, q),
                        continuation,
                        method="L-BFGS-B",
                        jac=True,
                        bounds=[(0.0, None)] * n,
                        options={
                            "maxiter": 100,
                            "maxls": 30,
                            "ftol": 1e-14,
                            "gtol": 1e-8,
                        },
                    )
                    candidate = np.asarray(result.x, dtype=float)
                    if np.all(np.isfinite(candidate)):
                        continuation = project_simplex(candidate)
                        candidate_score = peak(continuation)
                        remember(continuation, candidate_score)
                        if candidate_score < current_score:
                            current = continuation
                            current_score = candidate_score
                except Exception:
                    break

        failures = 0
        iterations = 1300 if start_number < 6 else 750
        for iteration in range(iterations):
            if time.monotonic() >= deadline:
                break

            gradient, convolution = active_gradient(current)
            candidate, score, accepted = exact_descent_step(
                current, current_score, gradient
            )

            if accepted:
                current, current_score = candidate, score
                remember(current, current_score)
                failures = 0
                continue

            failures += 1
            if iteration >= 120 and (iteration % 3 == 0 or failures >= 8):
                candidate, score, moved = transport(
                    current, current_score, convolution
                )
                if moved:
                    current, current_score = candidate, score
                    remember(current, current_score)
                    failures = 0
                    continue

            if failures >= 28 and iteration % 9 == 0:
                candidate = project_simplex(
                    current + rng.normal(0.0, 2.5e-6, n)
                )
                score = peak(candidate)
                if score < current_score - 1e-14:
                    current, current_score = candidate, score
                    remember(current, current_score)
                    failures = 0

    # Conservative variable-length trimming.  Every crop is normalized and
    # evaluated with the length-dependent factor before it is accepted.
    cropped = [float(value) for value in best]
    cropped_score = float(2.0 * len(cropped) * peak(best))

    def consider_crop(left, right):
        nonlocal cropped, cropped_score
        length = len(cropped)
        if left < 0 or right < 0 or left + right >= length:
            return False

        end = length - right if right else None
        values = cropped[left:end]
        total = float(sum(values))
        if not values or total <= 0.0:
            return False

        candidate = [float(value / total) for value in values]
        score = float(
            2.0 * len(candidate) * np.max(np.convolve(candidate, candidate))
        )
        if np.isfinite(score) and score < cropped_score - 1e-14:
            cropped = candidate
            cropped_score = score
            return True
        return False

    improved = True
    while improved and time.monotonic() < deadline:
        improved = False
        length = len(cropped)
        for size in (1, 2, 4, 8, 12, 16):
            if size >= length - 1:
                continue
            choices = (
                (size, 0),
                (0, size),
                (size, size),
                (size, min(2 * size, length - size - 1)),
                (min(2 * size, length - size - 1), size),
            )
            for left, right in choices:
                if consider_crop(left, right):
                    improved = True

    return [float(value) for value in cropped]
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
    """Search for a nonnegative sequence with a small normalized
    self-convolution maximum.

    The optimization is performed on the unit simplex.  Smooth
    log-sum-exp continuation is used to find promising basins, followed
    by exact hard-maximum projected descent and validated boundary
    trimming.
    """
    import time

    n = 600
    rng = np.random.default_rng(20250308)
    deadline = time.monotonic() + 970.0
    uniform = np.full(n, 1.0 / n, dtype=float)

    def normalize(values):
        values = np.asarray(values, dtype=float)
        if values.size != n or not np.all(np.isfinite(values)):
            return uniform.copy()
        values = np.maximum(values, 0.0)
        total = float(values.sum())
        if total <= 1e-15:
            return uniform.copy()
        return values / total

    def peak(values):
        return float(np.max(np.convolve(values, values)))

    def score(values):
        return float(2.0 * len(values) * np.max(np.convolve(values, values)))

    def project_simplex(values):
        values = np.asarray(values, dtype=float)
        if values.size != n or not np.all(np.isfinite(values)):
            return uniform.copy()

        values = np.maximum(values, 0.0)
        ordered = np.sort(values)[::-1]
        cumulative = np.cumsum(ordered)
        indices = np.arange(1, n + 1, dtype=float)
        valid = ordered - (cumulative - 1.0) / indices > 0.0

        if not np.any(valid):
            return uniform.copy()

        rho = int(np.flatnonzero(valid)[-1])
        theta = (cumulative[rho] - 1.0) / float(rho + 1)
        result = np.maximum(values - theta, 0.0)
        total = float(result.sum())
        return uniform.copy() if total <= 1e-15 else result / total

    coordinate = np.linspace(-0.25, 0.25, n)

    # A broad arch is a particularly effective deterministic starting point.
    arch = 1.0 + 4.0 * np.abs(coordinate) - 16.0 * coordinate**2
    arch = normalize(np.maximum(arch, 0.0))

    best = arch.copy()
    best_value = peak(best)

    def remember(candidate, value=None):
        nonlocal best, best_value
        candidate = normalize(candidate)
        if value is None:
            value = peak(candidate)
        if np.isfinite(value) and value < best_value:
            best = candidate.copy()
            best_value = float(value)

    # Construct diverse, deliberately asymmetric starts.
    starts = [arch, uniform]
    for scale in (0.01, 0.025, 0.05, 0.09, 0.16, 0.28, 0.42):
        starts.append(normalize(arch * np.exp(rng.normal(0.0, scale, n))))

    for mixture in (0.10, 0.22, 0.38, 0.58, 0.78):
        random_profile = rng.random(n) ** rng.uniform(0.55, 1.25)
        starts.append(
            normalize((1.0 - mixture) * arch + mixture * random_profile)
        )

    for shift, skew in ((5, 0.08), (-8, -0.10), (13, 0.14), (-19, -0.16)):
        candidate = np.roll(arch, shift)
        candidate *= np.exp(skew * np.linspace(-1.0, 1.0, n))
        starts.append(normalize(candidate))

    try:
        from scipy.optimize import minimize
    except Exception:
        minimize = None

    def smooth_objective(values, alpha):
        """Smooth maximum and its complete normalization-chain gradient."""
        values = np.asarray(values, dtype=float)
        total = float(values.sum())

        if (
            total <= 1e-14
            or values.size != n
            or not np.all(np.isfinite(values))
        ):
            return 1e100, np.zeros(n, dtype=float)

        normalized = values / total
        convolution = np.convolve(normalized, normalized)
        maximum = float(np.max(convolution))

        shifted = np.clip(alpha * (convolution - maximum), -745.0, 0.0)
        exponential = np.exp(shifted)
        denominator = max(float(exponential.sum()), 1e-300)
        weights = exponential / denominator

        raw_gradient = 2.0 * np.convolve(
            weights, normalized[::-1], mode="valid"
        )
        gradient = (
            raw_gradient - float(np.dot(raw_gradient, normalized))
        ) / total

        smooth_maximum = maximum + np.log(denominator) / alpha
        value = 2.0 * n * smooth_maximum + (total - 1.0) ** 2
        gradient = 2.0 * n * gradient + 2.0 * (total - 1.0)
        return float(value), gradient

    def exact_step(current, current_value, gradient):
        # Several trust radii are important because active peaks can swap.
        for amount in (0.50, 0.32, 0.20, 0.12, 0.07, 0.035, 0.015):
            if time.monotonic() >= deadline:
                break
            trial = project_simplex(current - amount * gradient)
            trial_value = peak(trial)
            if trial_value < current_value - max(
                1e-14, current_value * 1e-12
            ):
                return trial, trial_value, True
        return current, current_value, False

    for start_number, start in enumerate(starts):
        if time.monotonic() >= deadline:
            break

        current = project_simplex(start)
        current_value = peak(current)
        remember(current, current_value)

        # Warm-start smooth-max continuation.
        if minimize is not None:
            continuation = current.copy()
            for alpha in (8e3, 2e4, 6e4, 1.5e5, 4e5, 1e6, 3e6):
                if time.monotonic() >= deadline:
                    break
                try:
                    result = minimize(
                        lambda x, a=alpha: smooth_objective(x, a),
                        continuation,
                        method="L-BFGS-B",
                        jac=True,
                        bounds=[(0.0, None)] * n,
                        options={
                            "maxiter": 100 if start_number < 8 else 60,
                            "ftol": 1e-14,
                            "gtol": 1e-8,
                            "maxls": 30,
                        },
                    )
                    candidate = np.asarray(result.x, dtype=float)
                    if candidate.size != n or not np.all(
                        np.isfinite(candidate)
                    ):
                        continue

                    continuation = project_simplex(candidate)
                    candidate_value = peak(continuation)
                    remember(continuation, candidate_value)

                    if candidate_value < current_value:
                        current = continuation
                        current_value = candidate_value
                except Exception:
                    break

        failures = 0
        iterations = 1350 if start_number < 8 else 850

        for iteration in range(iterations):
            if time.monotonic() >= deadline:
                break

            convolution = np.convolve(current, current)
            maximum = float(np.max(convolution))

            if iteration < 280:
                temperature = max(
                    maximum * (0.22 - 0.17 * iteration / 280.0),
                    1e-15,
                )
                weights = np.exp(
                    np.clip(
                        (convolution - maximum) / temperature,
                        -745.0,
                        0.0,
                    )
                )
                weights /= max(float(weights.sum()), 1e-300)
            else:
                relative_band = (
                    2.5e-4 if iteration < 700 else 8.0e-5
                )
                active = np.flatnonzero(
                    convolution >= maximum - relative_band * maximum
                )
                if active.size == 0:
                    active = np.array([int(np.argmax(convolution))])
                if active.size > 4:
                    active = active[
                        np.argsort(convolution[active])[-4:]
                    ]

                weights = np.zeros(2 * n - 1, dtype=float)
                weights[active] = convolution[active]
                weights /= max(float(weights.sum()), 1e-300)

            gradient = 2.0 * np.convolve(
                weights, current[::-1], mode="valid"
            )
            candidate, candidate_value, accepted = exact_step(
                current, current_value, gradient
            )

            if accepted:
                current = candidate
                current_value = candidate_value
                remember(current, current_value)
                failures = 0
                continue

            failures += 1

            # Pressure transport moves mass away from coordinates that
            # contribute heavily to the currently active worst lags.
            if iteration >= 120 and (
                iteration % 3 == 0 or failures >= 7
            ):
                convolution = np.convolve(current, current)
                maximum = float(np.max(convolution))
                active = np.flatnonzero(
                    convolution >= maximum - 3.0e-4 * maximum
                )
                if active.size == 0:
                    active = np.array([int(np.argmax(convolution))])
                if active.size > 4:
                    active = active[
                        np.argsort(convolution[active])[-4:]
                    ]

                pressure = np.zeros(n, dtype=float)
                for lag in active:
                    left = max(0, int(lag) - n + 1)
                    right = min(n - 1, int(lag))
                    indices = np.arange(left, right + 1)
                    pressure[indices] += (
                        current[indices]
                        * current[int(lag) - indices]
                    )

                donors = np.argsort(pressure)[-14:][::-1]
                recipients = np.argsort(pressure)[:24]
                moved = False

                for amount in (8e-4, 2e-4, 5e-5, 1e-5):
                    if moved:
                        break
                    for donor in donors[:10]:
                        if current[donor] <= amount:
                            continue
                        for recipient in recipients[:16]:
                            if donor == recipient:
                                continue
                            trial = current.copy()
                            trial[donor] -= amount
                            trial[recipient] += amount
                            trial_value = peak(trial)
                            if trial_value < current_value - 1e-14:
                                current = trial
                                current_value = trial_value
                                remember(current, current_value)
                                moved = True
                                break
                        if moved:
                            break

                if moved:
                    failures = 0
                elif failures >= 24 and iteration % 8 == 0:
                    # Tiny asymmetric kicks help escape tied active sets.
                    trial = project_simplex(
                        current + rng.normal(0.0, 2.0e-6, n)
                    )
                    trial_value = peak(trial)
                    if trial_value < current_value - 1e-14:
                        current = trial
                        current_value = trial_value
                        remember(current, current_value)
                        failures = 0

    # Length-aware postprocessing.  A crop is retained only after exact
    # reevaluation, so this phase can never damage the incumbent.
    cropped = [float(value) for value in best]
    cropped_value = score(cropped)

    def consider_crop(left, right):
        nonlocal cropped, cropped_value
        length = len(cropped)
        if left < 0 or right < 0 or left + right >= length:
            return False

        end = length - right if right else None
        values = cropped[left:end]
        if not values:
            return False

        total = float(sum(values))
        if total <= 0.0:
            return False

        candidate = [float(value / total) for value in values]
        candidate_value = score(candidate)

        if np.isfinite(candidate_value) and candidate_value < (
            cropped_value - 1e-14
        ):
            cropped = candidate
            cropped_value = candidate_value
            return True
        return False

    improved = True
    while improved and time.monotonic() < deadline:
        improved = False
        length = len(cropped)

        for size in (1, 2, 4, 8, 12, 16, 24):
            if size >= length - 1:
                continue

            choices = (
                (size, 0),
                (0, size),
                (size, size),
                (size, min(2 * size, length - size - 1)),
                (min(2 * size, length - size - 1), size),
            )
            for left, right in choices:
                if consider_crop(left, right):
                    improved = True

    return [float(value) for value in cropped]
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
    """Find a nonnegative sequence with a small normalized self-convolution peak."""
    import time

    n = 600
    rng = np.random.default_rng(20250308)
    deadline = time.monotonic() + 970.0

    def normalize(x):
        x = np.asarray(x, dtype=float)
        x = np.maximum(x, 0.0)
        total = float(np.sum(x))
        if x.size == 0:
            return x
        if not np.isfinite(total) or total <= 1e-15:
            return np.full(x.size, 1.0 / x.size)
        return x / total

    def score(x):
        x = normalize(x)
        return float(2.0 * x.size * np.max(np.convolve(x, x)))

    def simplex_projection(x):
        """Project a finite vector onto the unit simplex."""
        x = np.asarray(x, dtype=float)
        if x.size != n or not np.all(np.isfinite(x)):
            return np.full(n, 1.0 / n)

        ordered = np.sort(x)[::-1]
        cumulative = np.cumsum(ordered)
        indices = np.arange(1, n + 1, dtype=float)
        feasible = ordered - (cumulative - 1.0) / indices > 0.0
        if not np.any(feasible):
            return np.full(n, 1.0 / n)

        rho = int(np.flatnonzero(feasible)[-1])
        threshold = (cumulative[rho] - 1.0) / (rho + 1.0)
        return normalize(np.maximum(x - threshold, 0.0))

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
    seeds.extend(
        (
            normalize(raised_cosine),
            normalize(0.65 * base + 0.35 * uniform),
        )
    )

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
        seeds.append(normalize((1.0 - mixture) * base + mixture * random_profile))

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

    def smooth_value_gradient(x, inverse_temperature):
        """
        Smooth the convolution maximum and return its complete gradient.
        The normalization chain rule is included explicitly.
        """
        x = np.asarray(x, dtype=float)
        total = float(np.sum(x))
        if total <= 1e-15 or not np.all(np.isfinite(x)):
            return 1e100, np.zeros(n, dtype=float)

        a = x / total
        convolution = np.convolve(a, a)
        peak = float(np.max(convolution))
        shifted = np.clip(
            inverse_temperature * (convolution - peak), -745.0, 0.0
        )
        exponentials = np.exp(shifted)
        partition = max(float(np.sum(exponentials)), 1e-300)
        weights = exponentials / partition

        # This is the derivative of sum_i weights[i] * (a*a)[i].
        gradient_a = 2.0 * np.convolve(
            weights, a[::-1], mode="valid"
        )
        gradient_a -= float(np.dot(gradient_a, a))
        gradient_x = gradient_a / total

        smooth_peak = peak + np.log(partition) / inverse_temperature
        penalty = 1e-3 * (total - 1.0) ** 2
        gradient_x += 2e-3 * (total - 1.0)
        return float(smooth_peak + penalty), gradient_x

    # Use inexpensive continuation to screen every deterministic and
    # asymmetric seed, then spend the expensive stages on the best basins.
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
                        lambda z, q=temperature:
                            smooth_value_gradient(z, q),
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

        basins = sorted(
            screened + [best],
            key=score,
        )[:8]

        for seed in basins:
            if time.monotonic() >= deadline - 25.0:
                break
            candidate = simplex_projection(seed)
            for temperature in (1e5, 3e5, 1e6, 3e6):
                if time.monotonic() >= deadline - 25.0:
                    break
                try:
                    result = minimize(
                        lambda z, q=temperature:
                            smooth_value_gradient(z, q),
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

    polish_starts = [best]
    polish_starts.extend(
        sorted((normalize(x) for x in seeds), key=score)[:6]
    )

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
            if active.size > 10:
                active = active[np.argsort(convolution[active])[-10:]]

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
                        moved_amount = min(
                            amount, 0.45 * current[donor]
                        )
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
            elif failures >= 24 and iteration % 11 == 0:
                trial = simplex_projection(
                    current + rng.normal(0.0, 1.5e-6, n)
                )
                trial_value = score(trial)
                if trial_value < current_value:
                    current, current_value = trial, trial_value
                    remember(current)
                    failures = 0

    # Cropping is accepted only after recomputing the evaluator with the new
    # length factor.  This also naturally removes negligible boundary tails.
    result = [float(v) for v in best]
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

        candidate = [float(v / total) for v in values]
        candidate_score = float(
            2.0 * len(candidate) *
            np.max(np.convolve(np.asarray(candidate), candidate))
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

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Search for a normalized nonnegative sequence with a small convolution peak."""
    import time

    n = 600
    rng = np.random.default_rng(20250308)
    deadline = time.monotonic() + 970.0
    uniform = np.full(n, 1.0 / n)
    xcoord = np.linspace(-0.25, 0.25, n)

    def normalize(x):
        x = np.asarray(x, dtype=float)
        if x.size == 0:
            return x
        x = np.maximum(x, 0.0)
        s = float(np.sum(x))
        if not np.isfinite(s) or s <= 1e-15:
            return np.full(x.size, 1.0 / x.size)
        return x / s

    def score(x):
        x = normalize(x)
        return float(2.0 * x.size * np.max(np.convolve(x, x)))

    def project(x):
        """Euclidean projection onto the unit simplex."""
        x = np.asarray(x, dtype=float)
        if x.size != n or not np.all(np.isfinite(x)):
            return uniform.copy()
        u = np.sort(x)[::-1]
        cssv = np.cumsum(u)
        ind = np.arange(1, n + 1, dtype=float)
        ok = u - (cssv - 1.0) / ind > 0.0
        if not np.any(ok):
            return uniform.copy()
        rho = int(np.flatnonzero(ok)[-1])
        theta = (cssv[rho] - 1.0) / (rho + 1.0)
        return normalize(np.maximum(x - theta, 0.0))

    def arch(width):
        z = xcoord / max(width, 1e-12)
        return normalize(np.maximum(1.0 + 4.0 * np.abs(z) - 16.0 * z * z, 0.0))

    base = arch(1.0)
    seeds = [uniform.copy()]
    for width in np.arange(0.65, 1.36, 0.05):
        seeds.append(arch(float(width)))

    cosine = np.maximum(0.0, 0.5 * (1.0 + np.cos(2.0 * np.pi * xcoord)))
    seeds += [
        normalize(cosine),
        normalize(0.65 * base + 0.35 * uniform),
        normalize(0.8 * base + 0.2 * cosine),
    ]

    for scale in (0.01, 0.025, 0.05, 0.09, 0.16, 0.25):
        seeds.append(normalize(base * np.exp(rng.normal(0.0, scale, n))))

    for shift, skew in ((-30, -0.18), (-15, -0.10), (-6, -0.04),
                        (6, 0.04), (15, 0.10), (30, 0.18)):
        q = np.roll(base, shift)
        q *= np.exp(skew * np.linspace(-1.0, 1.0, n))
        seeds.append(normalize(q))

    for fraction in (0.08, 0.16, 0.28, 0.42, 0.60):
        noise = rng.random(n) ** rng.uniform(0.55, 1.3)
        seeds.append(normalize((1.0 - fraction) * base + fraction * noise))

    best = min(seeds, key=score).copy()
    best_value = score(best)

    def remember(x):
        nonlocal best, best_value
        x = normalize(x)
        v = score(x)
        if np.isfinite(v) and v < best_value:
            best = x.copy()
            best_value = v

    try:
        from scipy.optimize import minimize
    except Exception:
        minimize = None

    def smooth_objective(x, temperature):
        x = np.asarray(x, dtype=float)
        total = float(np.sum(x))
        if total <= 1e-15 or not np.all(np.isfinite(x)):
            return 1e100, np.zeros(n)

        a = x / total
        conv = np.convolve(a, a)
        peak = float(np.max(conv))
        z = np.clip(temperature * (conv - peak), -745.0, 0.0)
        ew = np.exp(z)
        weights = ew / max(float(np.sum(ew)), 1e-300)

        grad = 2.0 * np.convolve(weights, a[::-1], mode="valid")
        grad = (grad - float(np.dot(grad, a))) / total
        value = peak + np.log(max(float(np.sum(ew)), 1e-300)) / temperature

        # A very weak normalization penalty keeps L-BFGS-B well behaved.
        value += 1e-3 * (total - 1.0) ** 2
        grad += 2e-3 * (total - 1.0)
        return float(value), grad

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
            screened.append(candidate)

        basins = sorted(screened + [best], key=score)[:8]
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

    def active_lags(a, tolerance=4e-4, limit=16):
        c = np.convolve(a, a)
        m = float(np.max(c))
        ids = np.flatnonzero(c >= m * (1.0 - tolerance))
        if ids.size == 0:
            ids = np.array([int(np.argmax(c))])
        if ids.size > limit:
            ids = ids[np.argsort(c[ids])[-limit:]]
        return c, ids

    def try_trial(current, current_value, trial):
        if np.any(trial < -1e-14):
            return current, current_value, False
        trial = normalize(trial)
        value = score(trial)
        if value < current_value - max(1e-14, current_value * 2e-12):
            return trial, value, True
        return current, current_value, False

    def pair_shave(a, av, active):
        pairs = []
        for lag in active:
            lag = int(lag)
            lo, hi = max(0, lag - n + 1), min(n - 1, lag)
            ii = np.arange(lo, hi + 1)
            pp = a[ii] * a[lag - ii]
            for k in np.argsort(pp)[-14:][::-1]:
                i = int(ii[k])
                pairs.append((float(pp[k]), i, lag - i))
        pairs.sort(reverse=True)

        for _, i, j in pairs[:55]:
            for di, dj in ((1, 1), (-1, -1), (1, -1), (-1, 1)):
                u, v = i + di, j + dj
                if not (0 <= u < n and 0 <= v < n):
                    continue
                for amount in (2e-4, 5e-5, 1e-5, 3e-6, 1e-6):
                    if time.monotonic() > deadline - 15.0:
                        return a, av, False
                    cap = min(float(a[i]), float(a[j]))
                    if i == j:
                        cap *= 0.5
                    moved = min(amount, 0.45 * cap)
                    if moved <= 0.0:
                        continue
                    trial = a.copy()
                    trial[i] -= moved
                    trial[j] -= moved
                    trial[u] += moved
                    trial[v] += moved
                    a2, v2, ok = try_trial(a, av, trial)
                    if ok:
                        return a2, v2, True
        return a, av, False

    def transport(a, av, active):
        pressure = np.zeros(n, dtype=float)
        for lag in active:
            lag = int(lag)
            lo, hi = max(0, lag - n + 1), min(n - 1, lag)
            for i in range(lo, hi + 1):
                pressure[i] += a[i] * a[lag - i]

        donors = np.argsort(pressure)[-22:][::-1]
        recipients = np.argsort(pressure)[:32]

        for amount in (8e-4, 2e-4, 5e-5, 1e-5, 3e-6, 1e-6):
            for d in donors:
                if a[d] <= amount:
                    continue
                for r in recipients:
                    if d == r:
                        continue
                    moved = min(amount, 0.45 * float(a[d]))
                    trial = a.copy()
                    trial[d] -= moved
                    trial[r] += moved
                    a2, v2, ok = try_trial(a, av, trial)
                    if ok:
                        return a2, v2, True

        # Broader two-donor/two-recipient transport.  The asymmetric
        # alternatives are useful when two competing plateaus have unequal
        # pressure.
        for d1 in donors[:12]:
            for d2 in donors[:12]:
                if d1 >= d2:
                    continue
                for r1 in recipients[:16]:
                    for r2 in recipients[:16]:
                        if r1 == r2 or r1 in (d1, d2) or r2 in (d1, d2):
                            continue
                        for amount in (2e-4, 5e-5, 1e-5):
                            if time.monotonic() > deadline - 15.0:
                                return a, av, False
                            for x, y in ((0.5, 0.5), (0.5, 1.5), (1.5, 0.5)):
                                total = amount * (x + y)
                                if total > 0.8 * (a[d1] + a[d2]):
                                    continue
                                p = min(0.45 * a[d1], amount * x)
                                q = min(0.45 * a[d2], total - p)
                                if p + q <= 0.0:
                                    continue
                                trial = a.copy()
                                trial[d1] -= p
                                trial[d2] -= q
                                trial[r1] += 0.5 * (p + q)
                                trial[r2] += 0.5 * (p + q)
                                a2, v2, ok = try_trial(a, av, trial)
                                if ok:
                                    return a2, v2, True
        return a, av, False

    starts = [best] + sorted(seeds, key=score)[:5]
    for initial in starts:
        if time.monotonic() > deadline - 15.0:
            break
        current = project(initial)
        current_value = score(current)
        failures = 0

        for iteration in range(2200):
            if time.monotonic() > deadline - 15.0:
                break
            conv, active = active_lags(
                current,
                3e-4 if iteration < 600 else
                1e-4 if iteration < 1400 else 3e-5,
                18,
            )
            if iteration < 260:
                temp = max(conv.max() * (0.20 - 0.15 * iteration / 260.0), 1e-14)
                weights = np.exp(np.clip((conv - conv.max()) / temp, -745, 0))
            else:
                weights = np.zeros(2 * n - 1)
                weights[active] = conv[active] ** 2
            weights /= max(float(weights.sum()), 1e-300)
            gradient = 2.0 * np.convolve(weights, current[::-1], mode="valid")

            accepted = False
            for step in (0.75, 0.35, 0.15, 0.06, 0.02, 0.006, 0.002):
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

            current, current_value, moved = transport(
                current, current_value, active
            )
            if not moved:
                current, current_value, moved = pair_shave(
                    current, current_value, active
                )
            if moved:
                remember(current)
                failures = 0
            elif failures >= 18 and iteration % 9 == 0:
                trial = project(current + rng.normal(0.0, 1.2e-6, n))
                current, current_value, moved = try_trial(
                    current, current_value, trial
                )
                if moved:
                    remember(current)
                    failures = 0

    result = [float(x) for x in best]
    result_value = score(np.asarray(result))

    def crop(left, right):
        nonlocal result, result_value
        if left < 0 or right < 0 or left + right >= len(result):
            return False
        end = len(result) - right if right else None
        values = result[left:end]
        total = sum(values)
        if not values or total <= 1e-15:
            return False
        candidate = [float(x / total) for x in values]
        value = score(np.asarray(candidate))
        if np.isfinite(value) and value < result_value:
            result, result_value = candidate, value
            return True
        return False

    changed = True
    while changed and time.monotonic() < deadline - 3.0:
        changed = False
        length = len(result)
        for size in (1, 2, 4, 8, 12, 16):
            if size >= length - 1:
                continue
            choices = (
                (size, 0), (0, size), (size, size),
                (size, min(2 * size, length - size - 1)),
                (min(2 * size, length - size - 1), size),
            )
            for left, right in choices:
                if crop(left, right):
                    changed = True

    return [float(x) for x in result]
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
