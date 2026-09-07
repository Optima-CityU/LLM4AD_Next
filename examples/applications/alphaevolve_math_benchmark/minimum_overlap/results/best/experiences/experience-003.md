Actionable design pattern for nonconvex minimax searches with multiple promising basins.

- Elite-Preserving Higher-Resolution Continuation: At the 25-point stage, rank refined feasible starts by the exact hard maximum and retain the best three instead of keeping only one; independently interpolate and perturb all retained candidates at the next resolution, then again keep the best three by the recomputed hard maximum.
- Elite-Preserving Higher-Resolution Continuation: Increase the final half-sequence grid from 57 to 73 entries and pass every retained final-grid candidate through active-lag descent and cutting-plane epigraph polishing, because distinct phase-pattern basins can produce different candidates for a highly nonconvex max-correlation objective.
- Apply weighted water-filling projection after interpolation and perturbation to clip candidates to [0,1] while enforcing the mirror-induced mass equality, preserving feasibility throughout elite continuation.
- Elite-Preserving Higher-Resolution Continuation: The implementation achieved a score of 0.9998690941799867, an upper bound of 0.38097687208984704 against the target upper bound 0.380927, validity of 1.0, and an evaluation time of 472.3415603709873 seconds.

```python
#!/usr/bin/env python3
"""Initial relaxed sequence for the minimum-overlap problem."""

import json


# EVOLVE_START
def generate_erdos_data():
    """Construct a feasible half-sequence by deterministic minimax optimization."""
    import numpy as np

    rng = np.random.default_rng(195505)
    eps = 1.0e-12

    def project(values):
        """Project onto the box and the mirror-induced mass hyperplane."""
        y = np.asarray(values, dtype=float).copy()
        m = len(y)
        weights = np.full(m, 2.0)
        weights[-1] = 1.0
        target = (2.0 * m - 1.0) / 2.0

        def mass(lam):
            return float(np.sum(
                weights * np.clip(y - lam * weights, 0.0, 1.0)
            ))

        lo, hi = -2.0, 2.0
        while mass(lo) < target:
            lo *= 2.0
        while mass(hi) > target:
            hi *= 2.0

        for _ in range(80):
            mid = (lo + hi) * 0.5
            if mass(mid) > target:
                lo = mid
            else:
                hi = mid

        return np.clip(y - 0.5 * (lo + hi) * weights, 0.0, 1.0)

    def expand(x):
        # The endpoint is shared by the two mirrored halves.
        return np.concatenate((x[:-1], x[::-1]))

    def correlations(s):
        """Return all correlations and their derivatives."""
        n = len(s)
        values = np.correlate(s, 1.0 - s, mode="full")
        gradients = np.zeros((2 * n - 1, n), dtype=float)

        for lag in range(2 * n - 1):
            first = max(0, lag - n + 1)
            last = min(n - 1, lag)
            p = np.arange(first, last + 1)
            q = lag - p
            np.add.at(gradients[lag], p, 1.0 - s[q])
            np.add.at(gradients[lag], q, -s[p])

        return values, gradients

    def fold_gradient(gradient, m):
        result = np.zeros(m, dtype=float)
        result[:-1] += gradient[:m - 1]
        result += gradient[m - 1:][::-1]
        return result

    def value(x):
        return float(np.max(correlations(expand(x))[0]))

    def make_starts(m, count):
        t = np.linspace(0.0, 1.0, m)
        starts = []

        for k in range(count):
            if k == 0:
                y = np.full(m, 0.5)
            else:
                phase = rng.uniform(-np.pi, np.pi)
                f1 = rng.uniform(0.12, 0.48)
                f2 = rng.uniform(0.28, 1.25)

                if k == 1:
                    y = 0.5 + 0.5 * np.tanh(
                        np.sin(2.0 * np.pi * f1 * t + phase) /
                        rng.uniform(0.10, 0.28)
                    )
                elif k == 2:
                    y = 0.5 + 0.5 * np.sign(
                        np.sin(2.0 * np.pi * f1 * t + phase)
                    )
                else:
                    y = (
                        0.5
                        + rng.uniform(0.16, 0.34)
                        * np.sin(2.0 * np.pi * f1 * t + phase)
                        + rng.uniform(0.06, 0.18)
                        * np.sin(2.0 * np.pi * f2 * t - phase)
                        + rng.normal(0.0, 0.06, m)
                    )

            starts.append(project(y))

        return starts

    def smooth_descent(start, iterations, temperatures, step_size):
        """Minimize a continuation approximation to the hard maximum."""
        x = start.copy()
        best = x.copy()
        best_value = value(x)
        first_moment = np.zeros(len(x))
        second_moment = np.zeros(len(x))
        iteration = 0

        for temperature in temperatures:
            for _ in range(iterations):
                iteration += 1
                s = expand(x)
                c, full_gradients = correlations(s)

                scaled = (c - np.max(c)) / max(temperature, eps)
                soft_weights = np.exp(np.clip(scaled, -60.0, 0.0))
                soft_weights /= np.sum(soft_weights)
                gradient = fold_gradient(
                    soft_weights @ full_gradients, len(x)
                )

                first_moment = 0.90 * first_moment + 0.10 * gradient
                second_moment = (
                    0.995 * second_moment + 0.005 * gradient * gradient
                )
                direction = first_moment / (
                    np.sqrt(second_moment) + 1.0e-8
                )

                base = float(np.max(c))
                accepted = False
                for factor in (1.0, 0.45, 0.18):
                    trial = project(
                        x - factor * step_size * direction /
                        np.sqrt(float(iteration))
                    )
                    if value(trial) <= base + 1.0e-10:
                        x = trial
                        accepted = True
                        break

                if not accepted:
                    x = project(x - 0.05 * step_size * direction)

                current = value(x)
                if current < best_value:
                    best = x.copy()
                    best_value = current

        return best

    def retain_best(candidates, count=3):
        """Keep a small elite set while preserving distinct search basins."""
        ordered = sorted(candidates, key=value)
        result = []
        for candidate in ordered:
            if not any(np.max(np.abs(candidate - old)) < 1.0e-9
                       for old in result):
                result.append(candidate)
            if len(result) == count:
                break
        return result

    # Retaining three winners at every stage prevents a useful phase pattern
    # from being discarded merely because it is not the current winner.
    levels = (25, 41, 73)

    candidates = make_starts(levels[0], 10)
    candidates = [
        smooth_descent(q, 50, (4.0, 2.0, 0.8), 0.20)
        for q in candidates
    ]
    candidates = retain_best(candidates)

    for m in levels[1:]:
        old_grid = np.linspace(0.0, 1.0, len(candidates[0]))
        new_grid = np.linspace(0.0, 1.0, m)
        starts = []

        for incumbent in candidates:
            interpolated = project(
                np.interp(new_grid, old_grid, incumbent)
            )
            starts.append(interpolated)
            for scale in (0.025, 0.05, 0.08):
                starts.append(project(
                    interpolated + rng.normal(0.0, scale, m)
                ))

        refined = [
            smooth_descent(q, 45, (3.0, 1.2, 0.45, 0.16), 0.15)
            for q in starts
        ]
        candidates = retain_best(refined)

    def hard_descent(start, iterations=90):
        """Directly descend against the currently active hard lags."""
        x = start.copy()

        for _ in range(iterations):
            s = expand(x)
            c, full_gradients = correlations(s)
            peak = float(np.max(c))
            active = np.flatnonzero(
                c >= peak - max(1.0e-7, 0.002 * max(1.0, abs(peak)))
            )
            active = np.unique(np.concatenate((
                active, active - 1, active + 1
            )))
            active = active[
                (active >= 0) & (active < len(c))
            ]

            gradients = np.array([
                fold_gradient(full_gradients[i], len(x))
                for i in active
            ])
            norms = np.linalg.norm(gradients, axis=1) + 1.0e-10
            lag_weights = 1.0 / norms
            lag_weights /= np.sum(lag_weights)
            direction = np.sum(
                lag_weights[:, None] * gradients, axis=0
            )
            norm = np.linalg.norm(direction)

            if norm < eps:
                break

            accepted = False
            for scale in (0.05, 0.025, 0.012, 0.005, 0.002):
                trial = project(x - scale * direction / norm)
                if value(trial) < peak - 1.0e-10:
                    x = trial
                    accepted = True
                    break

            if not accepted:
                break

        return x

    # Every member of the final elite set receives the expensive hard-max
    # refinement and epigraph cutting-plane polish independently.
    candidates = [hard_descent(q) for q in candidates]

    try:
        from scipy.optimize import minimize

        def polish(start_x):
            m = len(start_x)
            weights = np.full(m, 2.0)
            weights[-1] = 1.0
            target = (2.0 * m - 1.0) / 2.0
            current = project(start_x)

            all_values, _ = correlations(expand(current))
            active = np.flatnonzero(
                all_values >= np.max(all_values) -
                max(1.0e-7, 0.001 * max(1.0, np.max(all_values)))
            )
            active = np.unique(np.concatenate((
                active, active - 1, active + 1
            )))
            active = active[
                (active >= 0) & (active < len(all_values))
            ]

            for _ in range(8):
                def objective(y):
                    return float(y[-1])

                def objective_jacobian(y):
                    result = np.zeros(m + 1)
                    result[-1] = 1.0
                    return result

                def constraints(y):
                    vals, _ = correlations(expand(y[:-1]))
                    return y[-1] - vals[active]

                def constraint_jacobian(y):
                    _, gradients = correlations(expand(y[:-1]))
                    result = np.zeros((len(active), m + 1))
                    for row, lag in enumerate(active):
                        result[row, :-1] = -fold_gradient(
                            gradients[lag], m
                        )
                        result[row, -1] = 1.0
                    return result

                def mass_constraint(y):
                    return float(np.dot(weights, y[:-1]) - target)

                def mass_jacobian(y):
                    result = np.zeros(m + 1)
                    result[:-1] = weights
                    return result

                current_values, _ = correlations(expand(current))
                initial = np.concatenate((
                    current,
                    np.array([float(np.max(current_values)) + 1.0e-6])
                ))

                result = minimize(
                    objective,
                    initial,
                    jac=objective_jacobian,
                    method="SLSQP",
                    bounds=[(0.0, 1.0)] * m + [(None, None)],
                    constraints=[
                        {
                            "type": "ineq",
                            "fun": constraints,
                            "jac": constraint_jacobian,
                        },
                        {
                            "type": "eq",
                            "fun": mass_constraint,
                            "jac": mass_jacobian,
                        },
                    ],
                    options={"ftol": 1.0e-12, "maxiter": 1000},
                )

                if not np.all(np.isfinite(result.x)):
                    break

                current = project(result.x[:-1])
                checked, _ = correlations(expand(current))
                checked_peak = float(np.max(checked))
                z = float(result.x[-1])

                violating = np.flatnonzero(
                    checked > z + max(2.0e-9, 2.0e-7 * abs(z))
                )
                violating = np.unique(np.concatenate((
                    violating, violating - 1, violating + 1
                )))
                violating = violating[
                    (violating >= 0) & (violating < len(checked))
                ]
                enlarged = np.unique(np.concatenate((active, violating)))

                if len(enlarged) == len(active):
                    return current, checked_peak
                active = enlarged

            return current, value(current)

        polished = []
        for candidate in candidates:
            polished_candidate, polished_value = polish(candidate)
            if np.isfinite(polished_value):
                polished.append(polished_candidate)

        if polished:
            candidates.extend(polished)

    except (ImportError, RuntimeError, ValueError):
        pass

    # A small coordinate-transfer pass preserves feasibility exactly and can
    # remove residual discretization imbalance after nonlinear polishing.
    x = min(candidates, key=value).copy()
    for delta in (0.01, 0.003, 0.001, 0.0003):
        changed = True
        while changed:
            changed = False
            current = value(x)

            for i in range(len(x) - 1):
                wi = 1.0 if i == len(x) - 1 else 2.0
                for j in range(i + 1, len(x)):
                    wj = 1.0 if j == len(x) - 1 else 2.0

                    for sign in (1.0, -1.0):
                        trial = x.copy()
                        trial[i] += sign * delta / wi
                        trial[j] -= sign * delta / wj

                        if np.min(trial) < -eps or np.max(trial) > 1.0 + eps:
                            continue

                        if value(trial) < current - 1.0e-10:
                            x = trial
                            changed = True
                            break

                    if changed:
                        break
                if changed:
                    break

    # The fixed JSON adapter requires a JSON-native return value.
    return project(x).tolist()
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"half_sequence": generate_erdos_data()}))

```

$$
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
$$

$$
0.379005 \leq C_5 \leq 0.380927
$$

```python
#!/usr/bin/env python3
"""Initial relaxed sequence for the minimum-overlap problem."""

import json


# EVOLVE_START
def generate_erdos_data():
    """Construct a feasible half-sequence by deterministic minimax optimization."""
    import numpy as np

    rng = np.random.default_rng(195505)
    eps = 1.0e-12

    def project(values):
        """Project onto the box and the mirror-induced mass hyperplane."""
        y = np.asarray(values, dtype=float).copy()
        m = len(y)
        weights = np.full(m, 2.0)
        weights[-1] = 1.0
        target = (2.0 * m - 1.0) / 2.0

        def mass(lam):
            return float(np.sum(
                weights * np.clip(y - lam * weights, 0.0, 1.0)
            ))

        lo, hi = -2.0, 2.0
        while mass(lo) < target:
            lo *= 2.0
        while mass(hi) > target:
            hi *= 2.0

        for _ in range(80):
            mid = (lo + hi) * 0.5
            if mass(mid) > target:
                lo = mid
            else:
                hi = mid

        return np.clip(y - 0.5 * (lo + hi) * weights, 0.0, 1.0)

    def expand(x):
        # The endpoint is shared by the two mirrored halves.
        return np.concatenate((x[:-1], x[::-1]))

    def correlations(s):
        """Return all correlations and their derivatives."""
        n = len(s)
        values = np.correlate(s, 1.0 - s, mode="full")
        gradients = np.zeros((2 * n - 1, n), dtype=float)

        for lag in range(2 * n - 1):
            first = max(0, lag - n + 1)
            last = min(n - 1, lag)
            p = np.arange(first, last + 1)
            q = lag - p
            np.add.at(gradients[lag], p, 1.0 - s[q])
            np.add.at(gradients[lag], q, -s[p])

        return values, gradients

    def fold_gradient(gradient, m):
        result = np.zeros(m, dtype=float)
        result[:-1] += gradient[:m - 1]
        result += gradient[m - 1:][::-1]
        return result

    def value(x):
        return float(np.max(correlations(expand(x))[0]))

    def make_starts(m, count):
        t = np.linspace(0.0, 1.0, m)
        starts = []

        for k in range(count):
            if k == 0:
                y = np.full(m, 0.5)
            else:
                phase = rng.uniform(-np.pi, np.pi)
                f1 = rng.uniform(0.12, 0.48)
                f2 = rng.uniform(0.28, 1.25)

                if k == 1:
                    y = 0.5 + 0.5 * np.tanh(
                        np.sin(2.0 * np.pi * f1 * t + phase) /
                        rng.uniform(0.10, 0.28)
                    )
                elif k == 2:
                    y = 0.5 + 0.5 * np.sign(
                        np.sin(2.0 * np.pi * f1 * t + phase)
                    )
                else:
                    y = (
                        0.5
                        + rng.uniform(0.16, 0.34)
                        * np.sin(2.0 * np.pi * f1 * t + phase)
                        + rng.uniform(0.06, 0.18)
                        * np.sin(2.0 * np.pi * f2 * t - phase)
                        + rng.normal(0.0, 0.06, m)
                    )

            starts.append(project(y))

        return starts

    def smooth_descent(start, iterations, temperatures, step_size):
        """Minimize a continuation approximation to the hard maximum."""
        x = start.copy()
        best = x.copy()
        best_value = value(x)
        first_moment = np.zeros(len(x))
        second_moment = np.zeros(len(x))
        iteration = 0

        for temperature in temperatures:
            for _ in range(iterations):
                iteration += 1
                s = expand(x)
                c, full_gradients = correlations(s)

                scaled = (c - np.max(c)) / max(temperature, eps)
                soft_weights = np.exp(np.clip(scaled, -60.0, 0.0))
                soft_weights /= np.sum(soft_weights)
                gradient = fold_gradient(
                    soft_weights @ full_gradients, len(x)
                )

                first_moment = 0.90 * first_moment + 0.10 * gradient
                second_moment = (
                    0.995 * second_moment + 0.005 * gradient * gradient
                )
                direction = first_moment / (
                    np.sqrt(second_moment) + 1.0e-8
                )

                base = float(np.max(c))
                accepted = False
                for factor in (1.0, 0.45, 0.18):
                    trial = project(
                        x - factor * step_size * direction /
                        np.sqrt(float(iteration))
                    )
                    if value(trial) <= base + 1.0e-10:
                        x = trial
                        accepted = True
                        break

                if not accepted:
                    x = project(x - 0.05 * step_size * direction)

                current = value(x)
                if current < best_value:
                    best = x.copy()
                    best_value = current

        return best

    def retain_best(candidates, count=3):
        """Keep a small elite set while preserving distinct search basins."""
        ordered = sorted(candidates, key=value)
        result = []
        for candidate in ordered:
            if not any(np.max(np.abs(candidate - old)) < 1.0e-9
                       for old in result):
                result.append(candidate)
            if len(result) == count:
                break
        return result

    # Retaining three winners at every stage prevents a useful phase pattern
    # from being discarded merely because it is not the current winner.
    levels = (25, 41, 73)

    candidates = make_starts(levels[0], 10)
    candidates = [
        smooth_descent(q, 50, (4.0, 2.0, 0.8), 0.20)
        for q in candidates
    ]
    candidates = retain_best(candidates)

    for m in levels[1:]:
        old_grid = np.linspace(0.0, 1.0, len(candidates[0]))
        new_grid = np.linspace(0.0, 1.0, m)
        starts = []

        for incumbent in candidates:
            interpolated = project(
                np.interp(new_grid, old_grid, incumbent)
            )
            starts.append(interpolated)
            for scale in (0.025, 0.05, 0.08):
                starts.append(project(
                    interpolated + rng.normal(0.0, scale, m)
                ))

        refined = [
            smooth_descent(q, 45, (3.0, 1.2, 0.45, 0.16), 0.15)
            for q in starts
        ]
        candidates = retain_best(refined)

    def hard_descent(start, iterations=90):
        """Directly descend against the currently active hard lags."""
        x = start.copy()

        for _ in range(iterations):
            s = expand(x)
            c, full_gradients = correlations(s)
            peak = float(np.max(c))
            active = np.flatnonzero(
                c >= peak - max(1.0e-7, 0.002 * max(1.0, abs(peak)))
            )
            active = np.unique(np.concatenate((
                active, active - 1, active + 1
            )))
            active = active[
                (active >= 0) & (active < len(c))
            ]

            gradients = np.array([
                fold_gradient(full_gradients[i], len(x))
                for i in active
            ])
            norms = np.linalg.norm(gradients, axis=1) + 1.0e-10
            lag_weights = 1.0 / norms
            lag_weights /= np.sum(lag_weights)
            direction = np.sum(
                lag_weights[:, None] * gradients, axis=0
            )
            norm = np.linalg.norm(direction)

            if norm < eps:
                break

            accepted = False
            for scale in (0.05, 0.025, 0.012, 0.005, 0.002):
                trial = project(x - scale * direction / norm)
                if value(trial) < peak - 1.0e-10:
                    x = trial
                    accepted = True
                    break

            if not accepted:
                break

        return x

    # Every member of the final elite set receives the expensive hard-max
    # refinement and epigraph cutting-plane polish independently.
    candidates = [hard_descent(q) for q in candidates]

    try:
        from scipy.optimize import minimize

        def polish(start_x):
            m = len(start_x)
            weights = np.full(m, 2.0)
            weights[-1] = 1.0
            target = (2.0 * m - 1.0) / 2.0
            current = project(start_x)

            all_values, _ = correlations(expand(current))
            active = np.flatnonzero(
                all_values >= np.max(all_values) -
                max(1.0e-7, 0.001 * max(1.0, np.max(all_values)))
            )
            active = np.unique(np.concatenate((
                active, active - 1, active + 1
            )))
            active = active[
                (active >= 0) & (active < len(all_values))
            ]

            for _ in range(8):
                def objective(y):
                    return float(y[-1])

                def objective_jacobian(y):
                    result = np.zeros(m + 1)
                    result[-1] = 1.0
                    return result

                def constraints(y):
                    vals, _ = correlations(expand(y[:-1]))
                    return y[-1] - vals[active]

                def constraint_jacobian(y):
                    _, gradients = correlations(expand(y[:-1]))
                    result = np.zeros((len(active), m + 1))
                    for row, lag in enumerate(active):
                        result[row, :-1] = -fold_gradient(
                            gradients[lag], m
                        )
                        result[row, -1] = 1.0
                    return result

                def mass_constraint(y):
                    return float(np.dot(weights, y[:-1]) - target)

                def mass_jacobian(y):
                    result = np.zeros(m + 1)
                    result[:-1] = weights
                    return result

                current_values, _ = correlations(expand(current))
                initial = np.concatenate((
                    current,
                    np.array([float(np.max(current_values)) + 1.0e-6])
                ))

                result = minimize(
                    objective,
                    initial,
                    jac=objective_jacobian,
                    method="SLSQP",
                    bounds=[(0.0, 1.0)] * m + [(None, None)],
                    constraints=[
                        {
                            "type": "ineq",
                            "fun": constraints,
                            "jac": constraint_jacobian,
                        },
                        {
                            "type": "eq",
                            "fun": mass_constraint,
                            "jac": mass_jacobian,
                        },
                    ],
                    options={"ftol": 1.0e-12, "maxiter": 1000},
                )

                if not np.all(np.isfinite(result.x)):
                    break

                current = project(result.x[:-1])
                checked, _ = correlations(expand(current))
                checked_peak = float(np.max(checked))
                z = float(result.x[-1])

                violating = np.flatnonzero(
                    checked > z + max(2.0e-9, 2.0e-7 * abs(z))
                )
                violating = np.unique(np.concatenate((
                    violating, violating - 1, violating + 1
                )))
                violating = violating[
                    (violating >= 0) & (violating < len(checked))
                ]
                enlarged = np.unique(np.concatenate((active, violating)))

                if len(enlarged) == len(active):
                    return current, checked_peak
                active = enlarged

            return current, value(current)

        polished = []
        for candidate in candidates:
            polished_candidate, polished_value = polish(candidate)
            if np.isfinite(polished_value):
                polished.append(polished_candidate)

        if polished:
            candidates.extend(polished)

    except (ImportError, RuntimeError, ValueError):
        pass

    # A small coordinate-transfer pass preserves feasibility exactly and can
    # remove residual discretization imbalance after nonlinear polishing.
    x = min(candidates, key=value).copy()
    for delta in (0.01, 0.003, 0.001, 0.0003):
        changed = True
        while changed:
            changed = False
            current = value(x)

            for i in range(len(x) - 1):
                wi = 1.0 if i == len(x) - 1 else 2.0
                for j in range(i + 1, len(x)):
                    wj = 1.0 if j == len(x) - 1 else 2.0

                    for sign in (1.0, -1.0):
                        trial = x.copy()
                        trial[i] += sign * delta / wi
                        trial[j] -= sign * delta / wj

                        if np.min(trial) < -eps or np.max(trial) > 1.0 + eps:
                            continue

                        if value(trial) < current - 1.0e-10:
                            x = trial
                            changed = True
                            break

                    if changed:
                        break
                if changed:
                    break

    # The fixed JSON adapter requires a JSON-native return value.
    return project(x).tolist()
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"half_sequence": generate_erdos_data()}))
```

```text
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
```

```text
0.379005 \leq C_5 \leq 0.380927
```
