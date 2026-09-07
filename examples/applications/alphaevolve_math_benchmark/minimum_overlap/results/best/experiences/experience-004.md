Use coarse-grid active-set optimization to obtain a strong candidate, then continue it onto a finer grid while preserving balance and validate every lag before accepting the result.

- Multiresolution Active-Set Refinement: After m=64 multistart and active-lag optimization, the method interpolates the best reflected step function onto an m=96 or m=128 grid, clips values to [0,1], applies the balance projection, rebuilds the finer-grid correlation and epigraph machinery, and refines the interpolated candidate together with small random perturbations.
- Active-set refinement: The optimization initially focuses on lags with the largest correlations, then evaluates every lag after each solve and accepts the candidate only according to its exact maximum correlation, preventing inactive lags from becoming worse unnoticed.
- Multiresolution Active-Set Refinement: The recorded run produced a validity score of 1.0 and an upper bound of 0.3809319355619135 in 697.7987152021378 seconds, compared with the target upper bound 0.380927 and an overall score of 0.9999870434546103.

```python
#!/usr/bin/env python3
"""Initial relaxed sequence for the minimum-overlap problem."""

import json


# EVOLVE_START
import numpy as np


def generate_erdos_data():
    """Construct a balanced reflection-compatible sequence.

    The returned value is the half-sequence ``a``.  The caller forms the
    reflected sequence ``a[:-1] + a[::-1]``.
    """
    try:
        from scipy.optimize import minimize
    except Exception:
        return [0.5] * 50

    rng = np.random.default_rng(20250308)

    def build_grid(m):
        """Build the affine representation and correlation machinery."""
        p = m - 1
        n = 2 * m - 1

        mapping = np.zeros((n, p), dtype=float)
        mapping[:p, :] = np.eye(p)
        mapping[m:, :] = np.fliplr(np.eye(p))

        fixed = np.zeros(n, dtype=float)
        fixed[m - 1] = 0.5

        lags = np.arange(-(n - 1), n)
        pairs = []
        for lag in lags:
            ii = []
            jj = []
            for i in range(n):
                j = i - int(lag)
                if 0 <= j < n:
                    ii.append(i)
                    jj.append(j)
            pairs.append(
                (
                    np.asarray(ii, dtype=int),
                    np.asarray(jj, dtype=int),
                )
            )

        def project_balance(x):
            """Project onto the box while enforcing the required balance."""
            target = p / 2.0
            x = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)

            for _ in range(30):
                delta = target - float(np.sum(x))
                if abs(delta) <= 1e-12:
                    break

                room = 1.0 - x if delta > 0.0 else x
                total = float(np.sum(room))
                if total <= 1e-14:
                    break

                x = np.clip(x + delta * room / total, 0.0, 1.0)

            return x

        def correlations(x, with_jacobian=False):
            h = mapping @ x + fixed
            values = np.empty(len(pairs), dtype=float)
            jac = (
                np.empty((len(pairs), p), dtype=float)
                if with_jacobian
                else None
            )

            for q, (ii, jj) in enumerate(pairs):
                values[q] = np.sum(h[ii] * (1.0 - h[jj]))

                if with_jacobian:
                    gradient = np.zeros(n, dtype=float)
                    np.add.at(gradient, ii, 1.0 - h[jj])
                    np.add.at(gradient, jj, -h[ii])
                    jac[q] = gradient @ mapping

            return (values, jac) if with_jacobian else values

        def solve_with_lags(start, active=None, maxiter=450):
            """Solve the epigraph problem using the selected lag rows."""
            if active is None:
                active = np.arange(len(pairs), dtype=int)
            else:
                active = np.asarray(active, dtype=int)

            def objective(v):
                return float(v[-1])

            def objective_jac(v):
                result = np.zeros(p + 1, dtype=float)
                result[-1] = 1.0
                return result

            def equality(v):
                return float(np.sum(v[:p]) - p / 2.0)

            def equality_jac(v):
                result = np.zeros(p + 1, dtype=float)
                result[:p] = 1.0
                return result

            def inequalities(v):
                return v[-1] - correlations(v[:p])[active]

            def inequalities_jac(v):
                _, derivative = correlations(v[:p], with_jacobian=True)
                result = np.zeros((len(active), p + 1), dtype=float)
                result[:, :p] = -derivative[active]
                result[:, -1] = 1.0
                return result

            initial_correlations = correlations(start)
            initial = np.concatenate(
                [
                    project_balance(start),
                    [
                        max(
                            float(np.max(initial_correlations[active]))
                            + 1e-5,
                            0.0,
                        )
                    ],
                ]
            )

            return minimize(
                objective,
                initial,
                jac=objective_jac,
                method="SLSQP",
                bounds=[(0.0, 1.0)] * p + [(0.0, float(n))],
                constraints=[
                    {
                        "type": "eq",
                        "fun": equality,
                        "jac": equality_jac,
                    },
                    {
                        "type": "ineq",
                        "fun": inequalities,
                        "jac": inequalities_jac,
                    },
                ],
                options={"maxiter": maxiter, "ftol": 2e-10, "disp": False},
            )

        return p, n, project_balance, correlations, solve_with_lags

    def make_start(p, kind):
        if kind == 0:
            block = np.repeat([0.03, 0.97], p // 8 + 1)[:p]
            x = np.tile(block, 4)[:p]
        elif kind == 1:
            values = []
            for k in range(1, p + 1):
                value = 0.0
                t = k
                place = 0.5
                while t:
                    value += (t & 1) * place
                    t >>= 1
                    place *= 0.5
                values.append(0.08 if value < 0.5 else 0.92)
            x = np.asarray(values, dtype=float)
        elif kind == 2:
            raw = rng.random(p)
            x = 0.12 + 0.76 * (raw > np.median(raw))
            x = 0.65 * x + 0.35 * np.roll(x, 1)
        else:
            x = rng.uniform(0.02, 0.98, p)

        return x

    def refine_grid(m, starts, multistart=False):
        """Run full epigraph solves followed by active-lag refinement."""
        p, n, project_balance, correlations, solve_with_lags = build_grid(m)
        candidates = []
        best_x = None
        best_score = float("inf")

        def consider(x):
            nonlocal best_x, best_score
            x = project_balance(x)
            value = float(np.max(correlations(x)))
            score = 2.0 * value / n
            candidates.append(x)
            if score < best_score:
                best_score = score
                best_x = x.copy()

        if multistart:
            starts = [make_start(p, kind) for kind in range(7)]

        for start in starts:
            result = solve_with_lags(project_balance(start))
            if result.x.shape[0] == p + 1 and np.all(np.isfinite(result.x)):
                consider(result.x[:p])
            else:
                consider(start)

        # Active-set refinement uses the exact full correlation vector after
        # every solve, so inactive lags can never be silently overlooked.
        initial_candidates = list(candidates)
        for initial_candidate in initial_candidates:
            candidate = project_balance(initial_candidate)
            all_values = correlations(candidate)
            maximum = float(np.max(all_values))
            active = np.flatnonzero(all_values >= maximum - 1e-4)
            active = np.unique(
                np.concatenate([active, [int(np.argmax(all_values))]])
            )

            for _ in range(8):
                result = solve_with_lags(candidate, active, maxiter=300)
                if result.x.shape[0] != p + 1 or not np.all(
                    np.isfinite(result.x)
                ):
                    break

                trial = project_balance(result.x[:p])
                trial_values = correlations(trial)
                consider(trial)

                epigraph_value = float(result.x[-1])
                violating = np.flatnonzero(
                    trial_values > epigraph_value + 1e-7
                )

                candidate = trial
                all_values = trial_values
                if violating.size == 0:
                    break

                new_active = np.unique(
                    np.concatenate([active, violating])
                )
                if new_active.size == active.size:
                    break
                active = new_active

        return best_x, best_score, n

    # Preserve the inherited m=64 multistart and active-lag optimization.
    old_best, old_score, old_n = refine_grid(64, [], multistart=True)

    # Continue on a finer grid by interpolating the optimized full reflected
    # step function.  This provides a high-quality basin for the finer solve.
    old_full = np.concatenate([old_best, old_best[::-1]])
    fine_m = 96
    fine_n = 2 * fine_m - 1
    old_coordinates = np.linspace(0.0, 1.0, old_full.size)
    fine_coordinates = np.linspace(0.0, 1.0, fine_n)
    interpolated_full = np.interp(
        fine_coordinates, old_coordinates, old_full
    )
    interpolated_full = np.clip(interpolated_full, 0.0, 1.0)

    fine_p = fine_m - 1
    interpolated_half = interpolated_full[:fine_p]
    fine_starts = [interpolated_half]

    # Small perturbations help continuation escape a basin inherited from
    # the coarser discretization without discarding its strong initial point.
    for scale in (0.0025, 0.006, 0.012):
        perturbed = interpolated_half + rng.normal(
            0.0, scale, size=fine_p
        )
        fine_starts.append(perturbed)

    fine_best, fine_score, _ = refine_grid(fine_m, fine_starts)

    if fine_best is not None and fine_score <= old_score:
        answer = fine_best
    else:
        answer = old_best

    answer = np.asarray(answer, dtype=float)
    answer = np.clip(answer, 0.0, 1.0)

    # The final half-sequence includes the fixed central value.  Balance is
    # imposed only on the non-central entries, as required by reflection.
    expected_p = answer.size
    target = expected_p / 2.0
    for _ in range(30):
        delta = target - float(np.sum(answer))
        if abs(delta) <= 1e-12:
            break
        room = 1.0 - answer if delta > 0.0 else answer
        total = float(np.sum(room))
        if total <= 1e-14:
            break
        answer = np.clip(answer + delta * room / total, 0.0, 1.0)

    return np.concatenate([answer, [0.5]]).tolist()
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
import numpy as np


def generate_erdos_data():
    """Construct a balanced reflection-compatible sequence.

    The returned value is the half-sequence ``a``.  The caller forms the
    reflected sequence ``a[:-1] + a[::-1]``.
    """
    try:
        from scipy.optimize import minimize
    except Exception:
        return [0.5] * 50

    rng = np.random.default_rng(20250308)

    def build_grid(m):
        """Build the affine representation and correlation machinery."""
        p = m - 1
        n = 2 * m - 1

        mapping = np.zeros((n, p), dtype=float)
        mapping[:p, :] = np.eye(p)
        mapping[m:, :] = np.fliplr(np.eye(p))

        fixed = np.zeros(n, dtype=float)
        fixed[m - 1] = 0.5

        lags = np.arange(-(n - 1), n)
        pairs = []
        for lag in lags:
            ii = []
            jj = []
            for i in range(n):
                j = i - int(lag)
                if 0 <= j < n:
                    ii.append(i)
                    jj.append(j)
            pairs.append(
                (
                    np.asarray(ii, dtype=int),
                    np.asarray(jj, dtype=int),
                )
            )

        def project_balance(x):
            """Project onto the box while enforcing the required balance."""
            target = p / 2.0
            x = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)

            for _ in range(30):
                delta = target - float(np.sum(x))
                if abs(delta) <= 1e-12:
                    break

                room = 1.0 - x if delta > 0.0 else x
                total = float(np.sum(room))
                if total <= 1e-14:
                    break

                x = np.clip(x + delta * room / total, 0.0, 1.0)

            return x

        def correlations(x, with_jacobian=False):
            h = mapping @ x + fixed
            values = np.empty(len(pairs), dtype=float)
            jac = (
                np.empty((len(pairs), p), dtype=float)
                if with_jacobian
                else None
            )

            for q, (ii, jj) in enumerate(pairs):
                values[q] = np.sum(h[ii] * (1.0 - h[jj]))

                if with_jacobian:
                    gradient = np.zeros(n, dtype=float)
                    np.add.at(gradient, ii, 1.0 - h[jj])
                    np.add.at(gradient, jj, -h[ii])
                    jac[q] = gradient @ mapping

            return (values, jac) if with_jacobian else values

        def solve_with_lags(start, active=None, maxiter=450):
            """Solve the epigraph problem using the selected lag rows."""
            if active is None:
                active = np.arange(len(pairs), dtype=int)
            else:
                active = np.asarray(active, dtype=int)

            def objective(v):
                return float(v[-1])

            def objective_jac(v):
                result = np.zeros(p + 1, dtype=float)
                result[-1] = 1.0
                return result

            def equality(v):
                return float(np.sum(v[:p]) - p / 2.0)

            def equality_jac(v):
                result = np.zeros(p + 1, dtype=float)
                result[:p] = 1.0
                return result

            def inequalities(v):
                return v[-1] - correlations(v[:p])[active]

            def inequalities_jac(v):
                _, derivative = correlations(v[:p], with_jacobian=True)
                result = np.zeros((len(active), p + 1), dtype=float)
                result[:, :p] = -derivative[active]
                result[:, -1] = 1.0
                return result

            initial_correlations = correlations(start)
            initial = np.concatenate(
                [
                    project_balance(start),
                    [
                        max(
                            float(np.max(initial_correlations[active]))
                            + 1e-5,
                            0.0,
                        )
                    ],
                ]
            )

            return minimize(
                objective,
                initial,
                jac=objective_jac,
                method="SLSQP",
                bounds=[(0.0, 1.0)] * p + [(0.0, float(n))],
                constraints=[
                    {
                        "type": "eq",
                        "fun": equality,
                        "jac": equality_jac,
                    },
                    {
                        "type": "ineq",
                        "fun": inequalities,
                        "jac": inequalities_jac,
                    },
                ],
                options={"maxiter": maxiter, "ftol": 2e-10, "disp": False},
            )

        return p, n, project_balance, correlations, solve_with_lags

    def make_start(p, kind):
        if kind == 0:
            block = np.repeat([0.03, 0.97], p // 8 + 1)[:p]
            x = np.tile(block, 4)[:p]
        elif kind == 1:
            values = []
            for k in range(1, p + 1):
                value = 0.0
                t = k
                place = 0.5
                while t:
                    value += (t & 1) * place
                    t >>= 1
                    place *= 0.5
                values.append(0.08 if value < 0.5 else 0.92)
            x = np.asarray(values, dtype=float)
        elif kind == 2:
            raw = rng.random(p)
            x = 0.12 + 0.76 * (raw > np.median(raw))
            x = 0.65 * x + 0.35 * np.roll(x, 1)
        else:
            x = rng.uniform(0.02, 0.98, p)

        return x

    def refine_grid(m, starts, multistart=False):
        """Run full epigraph solves followed by active-lag refinement."""
        p, n, project_balance, correlations, solve_with_lags = build_grid(m)
        candidates = []
        best_x = None
        best_score = float("inf")

        def consider(x):
            nonlocal best_x, best_score
            x = project_balance(x)
            value = float(np.max(correlations(x)))
            score = 2.0 * value / n
            candidates.append(x)
            if score < best_score:
                best_score = score
                best_x = x.copy()

        if multistart:
            starts = [make_start(p, kind) for kind in range(7)]

        for start in starts:
            result = solve_with_lags(project_balance(start))
            if result.x.shape[0] == p + 1 and np.all(np.isfinite(result.x)):
                consider(result.x[:p])
            else:
                consider(start)

        # Active-set refinement uses the exact full correlation vector after
        # every solve, so inactive lags can never be silently overlooked.
        initial_candidates = list(candidates)
        for initial_candidate in initial_candidates:
            candidate = project_balance(initial_candidate)
            all_values = correlations(candidate)
            maximum = float(np.max(all_values))
            active = np.flatnonzero(all_values >= maximum - 1e-4)
            active = np.unique(
                np.concatenate([active, [int(np.argmax(all_values))]])
            )

            for _ in range(8):
                result = solve_with_lags(candidate, active, maxiter=300)
                if result.x.shape[0] != p + 1 or not np.all(
                    np.isfinite(result.x)
                ):
                    break

                trial = project_balance(result.x[:p])
                trial_values = correlations(trial)
                consider(trial)

                epigraph_value = float(result.x[-1])
                violating = np.flatnonzero(
                    trial_values > epigraph_value + 1e-7
                )

                candidate = trial
                all_values = trial_values
                if violating.size == 0:
                    break

                new_active = np.unique(
                    np.concatenate([active, violating])
                )
                if new_active.size == active.size:
                    break
                active = new_active

        return best_x, best_score, n

    # Preserve the inherited m=64 multistart and active-lag optimization.
    old_best, old_score, old_n = refine_grid(64, [], multistart=True)

    # Continue on a finer grid by interpolating the optimized full reflected
    # step function.  This provides a high-quality basin for the finer solve.
    old_full = np.concatenate([old_best, old_best[::-1]])
    fine_m = 96
    fine_n = 2 * fine_m - 1
    old_coordinates = np.linspace(0.0, 1.0, old_full.size)
    fine_coordinates = np.linspace(0.0, 1.0, fine_n)
    interpolated_full = np.interp(
        fine_coordinates, old_coordinates, old_full
    )
    interpolated_full = np.clip(interpolated_full, 0.0, 1.0)

    fine_p = fine_m - 1
    interpolated_half = interpolated_full[:fine_p]
    fine_starts = [interpolated_half]

    # Small perturbations help continuation escape a basin inherited from
    # the coarser discretization without discarding its strong initial point.
    for scale in (0.0025, 0.006, 0.012):
        perturbed = interpolated_half + rng.normal(
            0.0, scale, size=fine_p
        )
        fine_starts.append(perturbed)

    fine_best, fine_score, _ = refine_grid(fine_m, fine_starts)

    if fine_best is not None and fine_score <= old_score:
        answer = fine_best
    else:
        answer = old_best

    answer = np.asarray(answer, dtype=float)
    answer = np.clip(answer, 0.0, 1.0)

    # The final half-sequence includes the fixed central value.  Balance is
    # imposed only on the non-central entries, as required by reflection.
    expected_p = answer.size
    target = expected_p / 2.0
    for _ in range(30):
        delta = target - float(np.sum(answer))
        if abs(delta) <= 1e-12:
            break
        room = 1.0 - answer if delta > 0.0 else answer
        total = float(np.sum(room))
        if total <= 1e-14:
            break
        answer = np.clip(answer + delta * room / total, 0.0, 1.0)

    return np.concatenate([answer, [0.5]]).tolist()
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
