#!/usr/bin/env python3
"""Initial relaxed sequence for the minimum-overlap problem."""

import json


# EVOLVE_START
import numpy as np


def generate_erdos_data():
    """Construct a balanced reflection-compatible half-sequence.

    The returned sequence contains the non-central half followed by the
    fixed central value 0.5.  Callers can form the full reflected sequence
    with ``a[:-1] + a[::-1]``.
    """
    try:
        from scipy.optimize import minimize
    except Exception:
        return [0.5] * 50

    rng = np.random.default_rng(20250308)

    def build_grid(m):
        p = m - 1
        n = 2 * m - 1

        mapping = np.zeros((n, p), dtype=float)
        mapping[:p, :] = np.eye(p)
        mapping[m:, :] = np.fliplr(np.eye(p))

        fixed = np.zeros(n, dtype=float)
        fixed[m - 1] = 0.5

        pairs = []
        for lag in range(-(n - 1), n):
            ii = []
            jj = []
            for i in range(n):
                j = i - lag
                if 0 <= j < n:
                    ii.append(i)
                    jj.append(j)
            pairs.append((np.asarray(ii, dtype=int), np.asarray(jj, dtype=int)))

        def project_balance(x):
            """Clip to the box and impose sum(x) == p / 2."""
            x = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)
            target = p / 2.0

            for _ in range(40):
                delta = target - float(np.sum(x))
                if abs(delta) <= 1e-12:
                    break

                room = (1.0 - x) if delta > 0.0 else x
                total = float(np.sum(room))
                if total <= 1e-14:
                    break
                x = np.clip(x + delta * room / total, 0.0, 1.0)

            return x

        def correlations(x, with_jacobian=False):
            h = mapping @ x + fixed
            values = np.empty(len(pairs), dtype=float)
            jac = np.empty((len(pairs), p), dtype=float) if with_jacobian else None

            for q, (ii, jj) in enumerate(pairs):
                values[q] = np.sum(h[ii] * (1.0 - h[jj]))

                if with_jacobian:
                    gradient = np.zeros(n, dtype=float)
                    np.add.at(gradient, ii, 1.0 - h[jj])
                    np.add.at(gradient, jj, -h[ii])
                    jac[q] = gradient @ mapping

            return (values, jac) if with_jacobian else values

        def solve_with_lags(start, active=None, maxiter=450):
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

            start = project_balance(start)
            initial_correlations = correlations(start)
            initial = np.concatenate(
                (
                    start,
                    [max(float(np.max(initial_correlations[active])) + 1e-5, 0.0)],
                )
            )

            return minimize(
                objective,
                initial,
                jac=objective_jac,
                method="SLSQP",
                bounds=[(0.0, 1.0)] * p + [(0.0, float(n))],
                constraints=[
                    {"type": "eq", "fun": equality, "jac": equality_jac},
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
        """Perform epigraph optimization and exact active-lag refinement."""
        p, n, project_balance, correlations, solve_with_lags = build_grid(m)
        candidates = []
        best_x = None
        best_score = float("inf")

        def consider(x):
            nonlocal best_x, best_score
            x = project_balance(x)
            score = 2.0 * float(np.max(correlations(x))) / n
            candidates.append(x.copy())
            if score < best_score:
                best_score = score
                best_x = x.copy()

        if multistart:
            starts = [make_start(p, kind) for kind in range(7)]

        for start in starts:
            result = solve_with_lags(project_balance(start))
            if (
                result.x.shape[0] == p + 1
                and np.all(np.isfinite(result.x))
            ):
                consider(result.x[:p])
            else:
                consider(start)

        # Repeatedly add every currently violated lag.  The full correlation
        # vector is always reevaluated, including after the final solve.
        for initial_candidate in list(candidates):
            candidate = project_balance(initial_candidate)
            values = correlations(candidate)
            maximum = float(np.max(values))
            active = np.flatnonzero(values >= maximum - 1e-4)
            active = np.unique(np.concatenate((active, [int(np.argmax(values))])))

            for _ in range(8):
                result = solve_with_lags(candidate, active, maxiter=300)
                if (
                    result.x.shape[0] != p + 1
                    or not np.all(np.isfinite(result.x))
                ):
                    break

                candidate = project_balance(result.x[:p])
                trial_values = correlations(candidate)
                consider(candidate)

                violating = np.flatnonzero(
                    trial_values > float(result.x[-1]) + 1e-7
                )
                if violating.size == 0:
                    break

                new_active = np.unique(np.concatenate((active, violating)))
                if new_active.size == active.size:
                    break
                active = new_active

        return best_x, best_score, n

    def interpolate_half(source_half, target_m):
        """Interpolate a reflected source onto a target grid."""
        source_full = np.concatenate((source_half, source_half[::-1]))
        target_n = 2 * target_m - 1
        old_grid = np.linspace(0.0, 1.0, source_full.size)
        new_grid = np.linspace(0.0, 1.0, target_n)
        full = np.interp(new_grid, old_grid, source_full)
        return np.clip(full[: target_m - 1], 0.0, 1.0)

    # First stage: retain the inherited m=64 multistart solve.
    best64, score64, _ = refine_grid(64, [], multistart=True)

    # Second stage: continue from m=64 to m=96.
    start96 = interpolate_half(best64, 96)
    starts96 = [start96]
    for scale in (0.0025, 0.006, 0.012):
        starts96.append(start96 + rng.normal(0.0, scale, size=start96.size))
    best96, score96, _ = refine_grid(96, starts96)

    # Third stage: continue the best m=96 reflected sequence to m=128.
    # The central value is fixed by build_grid; only the non-central variables
    # are passed to the optimizer and projected onto their balance hyperplane.
    start128 = interpolate_half(best96, 128)
    starts128 = [start128]
    for scale in (0.0015, 0.004, 0.008):
        starts128.append(start128 + rng.normal(0.0, scale, size=start128.size))

    best128, score128, _ = refine_grid(128, starts128)

    # Compare exact normalized maxima from all resolutions.  A finer result is
    # used only when it improves the best already available candidate.
    answer = best64
    best_score = score64
    if best96 is not None and score96 < best_score:
        answer = best96
        best_score = score96
    if best128 is not None and score128 < best_score:
        answer = best128

    answer = np.clip(np.asarray(answer, dtype=float), 0.0, 1.0)

    # Enforce balance once more after selecting the winning candidate.
    target = answer.size / 2.0
    for _ in range(40):
        delta = target - float(np.sum(answer))
        if abs(delta) <= 1e-12:
            break
        room = 1.0 - answer if delta > 0.0 else answer
        total = float(np.sum(room))
        if total <= 1e-14:
            break
        answer = np.clip(answer + delta * room / total, 0.0, 1.0)

    return np.concatenate((answer, np.array([0.5], dtype=float))).tolist()
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"half_sequence": generate_erdos_data()}))
