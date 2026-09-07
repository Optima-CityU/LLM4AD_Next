For Heilbronn triangle optimization with n=11, preserve structured triangular-lattice starts by ensuring the lattice contains enough strict interior points, then diversify and validate jittered subsets before the existing optimization pipeline.

- Triangular-lattice initialization: The candidate generator should choose the smallest integer k satisfying (k-1)(k-2)/2 >= n; for n=11, k=7 provides 15 strict interior lattice points, whereas k=6 provides only 10 and causes the lattice construction to be discarded.
- Jittered lattice candidates: The initialization should generate the full interior lattice, randomly shuffle it, evaluate several subsets, and perturb selected points in barycentric coordinates with zero-sum Gaussian noise at scales 0.01/k, 0.025/k, and 0.05/k before projecting them back into the strict simplex.
- Candidate validation and downstream optimization: The initialization should reject duplicate-point candidates and candidates with an excessively small initial minimum determinant, retain the best lexicographic bottleneck score, and leave subsequent annealing, elite selection, SQP polishing, and final validation unchanged.
- Repair and Strengthen the Triangular-Lattice Initialization: This design achieved a min_area of 0.03383624423469345, validity 1.0, target_ratio 0.9270203899916013, and evaluation time 68.79933765716851 seconds for the n=11 benchmark.

```python
#!/usr/bin/env python3
"""Hybrid heuristic solver for the Heilbronn triangle problem.

The search is performed in barycentric coordinates.  A point is represented
by a positive row (a, b, c), with a + b + c = 1.  In these coordinates the
normalized area of a triangle is the absolute determinant of its three
barycentric rows.
"""

import itertools
import json
import math

import numpy as np


# EVOLVE_START
def find_best_placement(n):
    """Search for n interior points maximizing the minimum triangle area."""
    if n < 3:
        raise ValueError("at least three points are required")

    rng = np.random.default_rng(918273645 + 31 * n)
    triples = np.asarray(
        list(itertools.combinations(range(n), 3)), dtype=np.int32
    )
    margin = 1.0e-8
    sqrt3 = math.sqrt(3.0)

    def normalize_inside(points):
        """Project rows into the strict positive simplex."""
        p = np.asarray(points, dtype=np.float64).copy()
        p = np.maximum(p, margin)
        p /= p.sum(axis=1, keepdims=True)

        # Repeat once so roundoff cannot leave a coordinate outside the
        # strict-interior region.
        p = np.maximum(p, margin)
        p /= p.sum(axis=1, keepdims=True)
        return p

    def determinant_values(points):
        """Return absolute normalized areas for every point triple."""
        a = points[triples[:, 0]]
        b = points[triples[:, 1]]
        c = points[triples[:, 2]]

        values = (
            a[:, 0] * (b[:, 1] * c[:, 2] - b[:, 2] * c[:, 1])
            - a[:, 1] * (b[:, 0] * c[:, 2] - b[:, 2] * c[:, 0])
            + a[:, 2] * (b[:, 0] * c[:, 1] - b[:, 1] * c[:, 0])
        )
        return np.abs(values)

    def score(points):
        """Lexicographic bottleneck score."""
        values = determinant_values(points)
        count = min(12, values.size)
        smallest = np.partition(values, count - 1)[:count]
        return float(smallest.min()), float(smallest.mean())

    def better(left, right):
        return (
            left[0] > right[0] + 1.0e-14
            or (
                abs(left[0] - right[0]) <= 1.0e-14
                and left[1] > right[1] + 1.0e-14
            )
        )

    def make_candidate(kind):
        """Create a diverse deterministic starting population."""
        if kind == 0:
            p = rng.dirichlet(np.ones(3), size=n)

        elif kind == 1:
            # Use the smallest triangular lattice containing at least n
            # strict-interior points.  The previous sqrt-based choice gave
            # only ten points for n=11 and therefore silently discarded this
            # useful structured initialization.
            k = 3
            while (k - 1) * (k - 2) // 2 < n:
                k += 1

            lattice = []
            for i in range(1, k):
                for j in range(1, k - i):
                    lattice.append(
                        (i / k, j / k, 1.0 - (i + j) / k)
                    )
            lattice = np.asarray(lattice, dtype=np.float64)

            # Exact lattice points contain many collinear triples.  Try
            # several independent shuffled subsets and several zero-sum
            # barycentric jitter scales, retaining the best valid start.
            best_lattice = None
            best_lattice_score = (-1.0, -1.0)
            jitter_scales = (0.01 / k, 0.025 / k, 0.05 / k)

            for scale in jitter_scales:
                # A few more subset trials are useful when the lattice has
                # substantially more points than are required.
                trial_count = 8 if len(lattice) > n else 4
                for _ in range(trial_count):
                    order = rng.permutation(len(lattice))
                    selected = lattice[order[:n]].copy()

                    noise = rng.normal(0.0, scale, selected.shape)
                    # Keep every perturbed row on its affine barycentric
                    # plane, so projection does not need to repair its sum.
                    noise -= noise.mean(axis=1, keepdims=True)
                    candidate = normalize_inside(selected + noise)

                    # Reject accidental duplicates before scoring.  The
                    # tolerance is intentionally small relative to lattice
                    # spacing, but catches numerically indistinguishable rows.
                    differences = (
                        candidate[:, None, :] - candidate[None, :, :]
                    )
                    squared_distances = np.sum(
                        differences * differences, axis=2
                    )
                    np.fill_diagonal(squared_distances, np.inf)
                    if np.min(squared_distances) <= 1.0e-18:
                        continue

                    values = determinant_values(candidate)
                    if np.min(values) <= 1.0e-9:
                        continue

                    candidate_score = score(candidate)
                    if better(candidate_score, best_lattice_score):
                        best_lattice = candidate
                        best_lattice_score = candidate_score

            if best_lattice is not None:
                p = best_lattice
            else:
                # This is only a defensive fallback for unusual small n or
                # pathological random draws; the lattice construction itself
                # is always available for n=11.
                p = rng.dirichlet(np.ones(3), size=n)

        elif kind == 2:
            # A folded two-dimensional low-discrepancy sequence.
            g1 = (math.sqrt(5.0) - 1.0) / 2.0
            g2 = (math.sqrt(3.0) - 1.0) / 2.0
            offset = rng.random()
            rows = []
            for q in range(n):
                u = (offset + (q + 1) * g1) % 1.0
                v = (0.37 * offset + (q + 1) * g2) % 1.0
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                rows.append(
                    (
                        max(u, margin),
                        max(v, margin),
                        max(1.0 - u - v, margin),
                    )
                )
            p = normalize_inside(np.asarray(rows))

        elif kind == 3:
            # Uneven Dirichlet distributions tend to discover different
            # orientation patterns and boundary-adjacent configurations.
            p = rng.dirichlet(np.full(3, 0.65), size=n)

        else:
            alpha = rng.choice(
                [
                    np.array([0.35, 0.8, 1.7]),
                    np.array([1.7, 0.35, 0.8]),
                    np.array([0.8, 1.7, 0.35]),
                ]
            )
            p = rng.dirichlet(alpha, size=n)

        return normalize_inside(p)

    # Keep several structurally different candidates for the deterministic
    # constrained optimizer, rather than only the best annealing trajectory.
    elite = []
    best_points = None
    best_score = (-1.0, -1.0)

    restarts = 24 if n == 11 else 16
    anneal_moves = 6200 if n == 11 else 4400

    for restart in range(restarts):
        points = make_candidate(restart % 5)
        current_score = score(points)
        local_best_points = points.copy()
        local_best_score = current_score
        stagnant = 0

        for iteration in range(anneal_moves):
            values = determinant_values(points)

            # Attack points involved in the active bottleneck constraints,
            # while retaining frequent fully random choices.
            if iteration % 4 == 0:
                active_count = min(14, values.size)
                active = np.argpartition(values, active_count - 1)[
                    :active_count
                ]
                counts = np.bincount(
                    triples[active].ravel(), minlength=n
                ).astype(np.float64)
                counts += 0.12
                point_index = int(
                    rng.choice(n, p=counts / counts.sum())
                )
            else:
                point_index = int(rng.integers(n))

            first, second = rng.choice(3, size=2, replace=False)
            current = points[point_index]

            # Transfer mass between two coordinates.  This keeps the row sum
            # exactly one and avoids repeated projection artifacts.
            available_positive = current[second] - margin
            available_negative = current[first] - margin

            fraction = 1.0 - iteration / anneal_moves
            step_scale = 0.19 * fraction + 0.0035
            delta = float(np.clip(
                rng.normal(0.0, step_scale),
                -available_positive,
                available_negative,
            ))
            if abs(delta) < 1.0e-14:
                continue

            trial = points.copy()
            trial[point_index, first] += delta
            trial[point_index, second] -= delta
            if np.min(trial[point_index]) <= margin:
                continue

            trial_score = score(trial)
            improvement = trial_score[0] - current_score[0]

            temperature = max(
                1.8e-5,
                0.0019 * (0.012 ** (iteration / anneal_moves)),
            )
            accept = better(trial_score, current_score)

            if not accept and improvement < 0.0:
                accept = rng.random() < math.exp(
                    max(-60.0, improvement / temperature)
                )
            elif not accept and abs(improvement) <= 1.0e-14:
                accept = (
                    trial_score[1] >= current_score[1]
                    or rng.random() < 0.025
                )

            if accept:
                points = trial
                current_score = trial_score
                stagnant = 0

                if better(current_score, local_best_score):
                    local_best_points = points.copy()
                    local_best_score = current_score
            else:
                stagnant += 1

            if stagnant > 760:
                points = local_best_points.copy()
                current_score = local_best_score
                stagnant = 0

                # Reheat by mutating the point most frequently present in
                # currently limiting triples.
                if rng.random() < 0.75:
                    values = determinant_values(points)
                    active_count = min(9, values.size)
                    active = np.argpartition(values, active_count - 1)[
                        :active_count
                    ]
                    counts = np.bincount(
                        triples[active].ravel(), minlength=n
                    )
                    point_index = int(np.argmax(counts))

                    noise = rng.normal(0.0, 0.04, 3)
                    noise -= noise.mean()
                    trial = points.copy()
                    trial[point_index] += noise
                    if np.min(trial[point_index]) > margin:
                        trial = normalize_inside(trial)
                        trial_score = score(trial)
                        if better(trial_score, current_score):
                            points = trial
                            current_score = trial_score

        # Coordinate-wise deterministic polish before adding this candidate
        # to the global elite pool.
        points = local_best_points.copy()
        current_score = score(points)

        for polish_pass in range(10):
            step = 0.038 * (0.47 ** polish_pass)
            for point_index in range(n):
                for first, second in ((0, 1), (0, 2), (1, 2)):
                    changed = True
                    while changed:
                        changed = False
                        for sign in (1.0, -1.0):
                            delta = sign * step
                            trial = points.copy()
                            trial[point_index, first] += delta
                            trial[point_index, second] -= delta

                            if np.min(trial[point_index]) <= margin:
                                continue

                            trial_score = score(trial)
                            if better(trial_score, current_score):
                                points = trial
                                current_score = trial_score
                                changed = True
                                break

        elite.append((current_score, points.copy()))
        elite.sort(key=lambda item: (item[0][0], item[0][1]), reverse=True)
        elite = elite[:8]

        if better(current_score, best_score):
            best_score = current_score
            best_points = points.copy()

    # ------------------------------------------------------------------
    # Constrained SQP polish
    #
    # For each elite candidate, retain its signed orientation for every
    # triple.  We then maximize t subject to sign(det) * det >= t.  Since
    # t starts positive, a successful run cannot cross an orientation
    # boundary.
    # ------------------------------------------------------------------
    try:
        from scipy.optimize import minimize

        def signed_determinants_and_jacobian(x, signs):
            p = x[: 3 * n].reshape(n, 3)
            m = len(triples)
            vals = np.empty(m, dtype=np.float64)
            jac = np.zeros((m, 3 * n + 1), dtype=np.float64)

            for row, (ia, ib, ic) in enumerate(triples):
                a = p[ia]
                b = p[ib]
                c = p[ic]

                da = np.array(
                    [
                        b[1] * c[2] - b[2] * c[1],
                        b[2] * c[0] - b[0] * c[2],
                        b[0] * c[1] - b[1] * c[0],
                    ]
                )
                db = np.array(
                    [
                        a[2] * c[1] - a[1] * c[2],
                        a[0] * c[2] - a[2] * c[0],
                        a[1] * c[0] - a[0] * c[1],
                    ]
                )
                dc = np.array(
                    [
                        a[1] * b[2] - a[2] * b[1],
                        a[2] * b[0] - a[0] * b[2],
                        a[0] * b[1] - a[1] * b[0],
                    ]
                )

                det = float(np.dot(a, da))
                s = signs[row]
                vals[row] = s * det
                jac[row, 3 * ia:3 * ia + 3] = s * da
                jac[row, 3 * ib:3 * ib + 3] = s * db
                jac[row, 3 * ic:3 * ic + 3] = s * dc
                jac[row, -1] = -1.0

            return vals, jac

        def sqp_polish(start):
            p = normalize_inside(start)
            raw = determinant_values(p)

            # Use the signed determinant of the ordered triple.  No
            # determinant is expected to be exactly zero after annealing.
            signed_raw = np.empty_like(raw)
            for q, (ia, ib, ic) in enumerate(triples):
                a, b, c = p[ia], p[ib], p[ic]
                signed_raw[q] = np.linalg.det(
                    np.asarray((a, b, c), dtype=np.float64)
                )
            signs = np.where(signed_raw >= 0.0, 1.0, -1.0)

            initial_t = max(1.0e-10, float(np.min(signs * signed_raw)))
            z0 = np.concatenate((p.ravel(), [initial_t * 0.985]))

            def objective(z):
                return -z[-1]

            def objective_jac(z):
                g = np.zeros(3 * n + 1, dtype=np.float64)
                g[-1] = -1.0
                return g

            def area_constraint(z):
                vals, _ = signed_determinants_and_jacobian(z, signs)
                return vals

            def area_jacobian(z):
                _, jac = signed_determinants_and_jacobian(z, signs)
                return jac

            def sum_constraint(z):
                return z[: 3 * n].reshape(n, 3).sum(axis=1) - 1.0

            sum_jac = np.zeros((n, 3 * n + 1), dtype=np.float64)
            for i in range(n):
                sum_jac[i, 3 * i:3 * i + 3] = 1.0

            constraints = [
                {
                    "type": "ineq",
                    "fun": area_constraint,
                    "jac": area_jacobian,
                },
                {
                    "type": "eq",
                    "fun": sum_constraint,
                    "jac": lambda z: sum_jac,
                },
            ]

            lower = np.full(3 * n + 1, margin, dtype=np.float64)
            upper = np.ones(3 * n + 1, dtype=np.float64)
            lower[-1] = 0.0
            upper[-1] = 0.25

            result = minimize(
                objective,
                z0,
                jac=objective_jac,
                constraints=constraints,
                bounds=list(zip(lower, upper)),
                method="SLSQP",
                options={
                    "maxiter": 650,
                    "ftol": 2.0e-12,
                    "disp": False,
                },
            )

            candidate = result.x[: 3 * n].reshape(n, 3)
            candidate = normalize_inside(candidate)

            if (
                not np.all(np.isfinite(candidate))
                or np.min(candidate) <= margin * 0.5
            ):
                return p

            return candidate

        # Multiple elite starts are important because the fixed orientation
        # pattern defines a separate local optimization basin.
        for _, candidate in elite:
            polished = sqp_polish(candidate)
            polished_score = score(polished)
            if better(polished_score, best_score):
                best_points = polished.copy()
                best_score = polished_score

    except (ImportError, ValueError, RuntimeError):
        # SciPy is optional.  The annealer remains a complete solver when it
        # is unavailable or an individual constrained solve fails.
        pass

    # Final small barycentric coordinate polish around the best
    # SQP/annealing result.  This is deliberately local and uses exact
    # lexicographic scores.
    best_points = normalize_inside(best_points)
    current_score = score(best_points)

    for pass_index in range(7):
        step = 0.0018 * (0.43 ** pass_index)
        for i in range(n):
            for first, second in ((0, 1), (0, 2), (1, 2)):
                for sign in (1.0, -1.0):
                    trial = best_points.copy()
                    trial[i, first] += sign * step
                    trial[i, second] -= sign * step

                    if np.min(trial[i]) <= margin:
                        continue

                    trial_score = score(trial)
                    if better(trial_score, current_score):
                        best_points = trial
                        current_score = trial_score

    # Explicitly recompute after the final normalization.  The returned
    # objective therefore exactly corresponds to the returned Cartesian
    # points.
    best_points = normalize_inside(best_points)
    final_values = determinant_values(best_points)
    min_area = float(np.min(final_values))

    # Barycentric-to-Cartesian conversion for vertices
    # (0, 0), (1, 0), and (1/2, sqrt(3)/2).
    cartesian = np.empty((n, 2), dtype=np.float64)
    cartesian[:, 0] = best_points[:, 1] + 0.5 * best_points[:, 2]
    cartesian[:, 1] = 0.5 * sqrt3 * best_points[:, 2]

    return cartesian, min_area


def run_search_point(n=11):
    return find_best_placement(n)


# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({
        "points": points.tolist(),
        "min_area": min_area,
    }))
```

```python
#!/usr/bin/env python3
"""Hybrid heuristic solver for the Heilbronn triangle problem.

The search is performed in barycentric coordinates.  A point is represented
by a positive row (a, b, c), with a + b + c = 1.  In these coordinates the
normalized area of a triangle is the absolute determinant of its three
barycentric rows.
"""

import itertools
import json
import math

import numpy as np


# EVOLVE_START
def find_best_placement(n):
    """Search for n interior points maximizing the minimum triangle area."""
    if n < 3:
        raise ValueError("at least three points are required")

    rng = np.random.default_rng(918273645 + 31 * n)
    triples = np.asarray(
        list(itertools.combinations(range(n), 3)), dtype=np.int32
    )
    margin = 1.0e-8
    sqrt3 = math.sqrt(3.0)

    def normalize_inside(points):
        """Project rows into the strict positive simplex."""
        p = np.asarray(points, dtype=np.float64).copy()
        p = np.maximum(p, margin)
        p /= p.sum(axis=1, keepdims=True)

        # Repeat once so roundoff cannot leave a coordinate outside the
        # strict-interior region.
        p = np.maximum(p, margin)
        p /= p.sum(axis=1, keepdims=True)
        return p

    def determinant_values(points):
        """Return absolute normalized areas for every point triple."""
        a = points[triples[:, 0]]
        b = points[triples[:, 1]]
        c = points[triples[:, 2]]

        values = (
            a[:, 0] * (b[:, 1] * c[:, 2] - b[:, 2] * c[:, 1])
            - a[:, 1] * (b[:, 0] * c[:, 2] - b[:, 2] * c[:, 0])
            + a[:, 2] * (b[:, 0] * c[:, 1] - b[:, 1] * c[:, 0])
        )
        return np.abs(values)

    def score(points):
        """Lexicographic bottleneck score."""
        values = determinant_values(points)
        count = min(12, values.size)
        smallest = np.partition(values, count - 1)[:count]
        return float(smallest.min()), float(smallest.mean())

    def better(left, right):
        return (
            left[0] > right[0] + 1.0e-14
            or (
                abs(left[0] - right[0]) <= 1.0e-14
                and left[1] > right[1] + 1.0e-14
            )
        )

    def make_candidate(kind):
        """Create a diverse deterministic starting population."""
        if kind == 0:
            p = rng.dirichlet(np.ones(3), size=n)

        elif kind == 1:
            # Use the smallest triangular lattice containing at least n
            # strict-interior points.  The previous sqrt-based choice gave
            # only ten points for n=11 and therefore silently discarded this
            # useful structured initialization.
            k = 3
            while (k - 1) * (k - 2) // 2 < n:
                k += 1

            lattice = []
            for i in range(1, k):
                for j in range(1, k - i):
                    lattice.append(
                        (i / k, j / k, 1.0 - (i + j) / k)
                    )
            lattice = np.asarray(lattice, dtype=np.float64)

            # Exact lattice points contain many collinear triples.  Try
            # several independent shuffled subsets and several zero-sum
            # barycentric jitter scales, retaining the best valid start.
            best_lattice = None
            best_lattice_score = (-1.0, -1.0)
            jitter_scales = (0.01 / k, 0.025 / k, 0.05 / k)

            for scale in jitter_scales:
                # A few more subset trials are useful when the lattice has
                # substantially more points than are required.
                trial_count = 8 if len(lattice) > n else 4
                for _ in range(trial_count):
                    order = rng.permutation(len(lattice))
                    selected = lattice[order[:n]].copy()

                    noise = rng.normal(0.0, scale, selected.shape)
                    # Keep every perturbed row on its affine barycentric
                    # plane, so projection does not need to repair its sum.
                    noise -= noise.mean(axis=1, keepdims=True)
                    candidate = normalize_inside(selected + noise)

                    # Reject accidental duplicates before scoring.  The
                    # tolerance is intentionally small relative to lattice
                    # spacing, but catches numerically indistinguishable rows.
                    differences = (
                        candidate[:, None, :] - candidate[None, :, :]
                    )
                    squared_distances = np.sum(
                        differences * differences, axis=2
                    )
                    np.fill_diagonal(squared_distances, np.inf)
                    if np.min(squared_distances) <= 1.0e-18:
                        continue

                    values = determinant_values(candidate)
                    if np.min(values) <= 1.0e-9:
                        continue

                    candidate_score = score(candidate)
                    if better(candidate_score, best_lattice_score):
                        best_lattice = candidate
                        best_lattice_score = candidate_score

            if best_lattice is not None:
                p = best_lattice
            else:
                # This is only a defensive fallback for unusual small n or
                # pathological random draws; the lattice construction itself
                # is always available for n=11.
                p = rng.dirichlet(np.ones(3), size=n)

        elif kind == 2:
            # A folded two-dimensional low-discrepancy sequence.
            g1 = (math.sqrt(5.0) - 1.0) / 2.0
            g2 = (math.sqrt(3.0) - 1.0) / 2.0
            offset = rng.random()
            rows = []
            for q in range(n):
                u = (offset + (q + 1) * g1) % 1.0
                v = (0.37 * offset + (q + 1) * g2) % 1.0
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                rows.append(
                    (
                        max(u, margin),
                        max(v, margin),
                        max(1.0 - u - v, margin),
                    )
                )
            p = normalize_inside(np.asarray(rows))

        elif kind == 3:
            # Uneven Dirichlet distributions tend to discover different
            # orientation patterns and boundary-adjacent configurations.
            p = rng.dirichlet(np.full(3, 0.65), size=n)

        else:
            alpha = rng.choice(
                [
                    np.array([0.35, 0.8, 1.7]),
                    np.array([1.7, 0.35, 0.8]),
                    np.array([0.8, 1.7, 0.35]),
                ]
            )
            p = rng.dirichlet(alpha, size=n)

        return normalize_inside(p)

    # Keep several structurally different candidates for the deterministic
    # constrained optimizer, rather than only the best annealing trajectory.
    elite = []
    best_points = None
    best_score = (-1.0, -1.0)

    restarts = 24 if n == 11 else 16
    anneal_moves = 6200 if n == 11 else 4400

    for restart in range(restarts):
        points = make_candidate(restart % 5)
        current_score = score(points)
        local_best_points = points.copy()
        local_best_score = current_score
        stagnant = 0

        for iteration in range(anneal_moves):
            values = determinant_values(points)

            # Attack points involved in the active bottleneck constraints,
            # while retaining frequent fully random choices.
            if iteration % 4 == 0:
                active_count = min(14, values.size)
                active = np.argpartition(values, active_count - 1)[
                    :active_count
                ]
                counts = np.bincount(
                    triples[active].ravel(), minlength=n
                ).astype(np.float64)
                counts += 0.12
                point_index = int(
                    rng.choice(n, p=counts / counts.sum())
                )
            else:
                point_index = int(rng.integers(n))

            first, second = rng.choice(3, size=2, replace=False)
            current = points[point_index]

            # Transfer mass between two coordinates.  This keeps the row sum
            # exactly one and avoids repeated projection artifacts.
            available_positive = current[second] - margin
            available_negative = current[first] - margin

            fraction = 1.0 - iteration / anneal_moves
            step_scale = 0.19 * fraction + 0.0035
            delta = float(np.clip(
                rng.normal(0.0, step_scale),
                -available_positive,
                available_negative,
            ))
            if abs(delta) < 1.0e-14:
                continue

            trial = points.copy()
            trial[point_index, first] += delta
            trial[point_index, second] -= delta
            if np.min(trial[point_index]) <= margin:
                continue

            trial_score = score(trial)
            improvement = trial_score[0] - current_score[0]

            temperature = max(
                1.8e-5,
                0.0019 * (0.012 ** (iteration / anneal_moves)),
            )
            accept = better(trial_score, current_score)

            if not accept and improvement < 0.0:
                accept = rng.random() < math.exp(
                    max(-60.0, improvement / temperature)
                )
            elif not accept and abs(improvement) <= 1.0e-14:
                accept = (
                    trial_score[1] >= current_score[1]
                    or rng.random() < 0.025
                )

            if accept:
                points = trial
                current_score = trial_score
                stagnant = 0

                if better(current_score, local_best_score):
                    local_best_points = points.copy()
                    local_best_score = current_score
            else:
                stagnant += 1

            if stagnant > 760:
                points = local_best_points.copy()
                current_score = local_best_score
                stagnant = 0

                # Reheat by mutating the point most frequently present in
                # currently limiting triples.
                if rng.random() < 0.75:
                    values = determinant_values(points)
                    active_count = min(9, values.size)
                    active = np.argpartition(values, active_count - 1)[
                        :active_count
                    ]
                    counts = np.bincount(
                        triples[active].ravel(), minlength=n
                    )
                    point_index = int(np.argmax(counts))

                    noise = rng.normal(0.0, 0.04, 3)
                    noise -= noise.mean()
                    trial = points.copy()
                    trial[point_index] += noise
                    if np.min(trial[point_index]) > margin:
                        trial = normalize_inside(trial)
                        trial_score = score(trial)
                        if better(trial_score, current_score):
                            points = trial
                            current_score = trial_score

        # Coordinate-wise deterministic polish before adding this candidate
        # to the global elite pool.
        points = local_best_points.copy()
        current_score = score(points)

        for polish_pass in range(10):
            step = 0.038 * (0.47 ** polish_pass)
            for point_index in range(n):
                for first, second in ((0, 1), (0, 2), (1, 2)):
                    changed = True
                    while changed:
                        changed = False
                        for sign in (1.0, -1.0):
                            delta = sign * step
                            trial = points.copy()
                            trial[point_index, first] += delta
                            trial[point_index, second] -= delta

                            if np.min(trial[point_index]) <= margin:
                                continue

                            trial_score = score(trial)
                            if better(trial_score, current_score):
                                points = trial
                                current_score = trial_score
                                changed = True
                                break

        elite.append((current_score, points.copy()))
        elite.sort(key=lambda item: (item[0][0], item[0][1]), reverse=True)
        elite = elite[:8]

        if better(current_score, best_score):
            best_score = current_score
            best_points = points.copy()

    # ------------------------------------------------------------------
    # Constrained SQP polish
    #
    # For each elite candidate, retain its signed orientation for every
    # triple.  We then maximize t subject to sign(det) * det >= t.  Since
    # t starts positive, a successful run cannot cross an orientation
    # boundary.
    # ------------------------------------------------------------------
    try:
        from scipy.optimize import minimize

        def signed_determinants_and_jacobian(x, signs):
            p = x[: 3 * n].reshape(n, 3)
            m = len(triples)
            vals = np.empty(m, dtype=np.float64)
            jac = np.zeros((m, 3 * n + 1), dtype=np.float64)

            for row, (ia, ib, ic) in enumerate(triples):
                a = p[ia]
                b = p[ib]
                c = p[ic]

                da = np.array(
                    [
                        b[1] * c[2] - b[2] * c[1],
                        b[2] * c[0] - b[0] * c[2],
                        b[0] * c[1] - b[1] * c[0],
                    ]
                )
                db = np.array(
                    [
                        a[2] * c[1] - a[1] * c[2],
                        a[0] * c[2] - a[2] * c[0],
                        a[1] * c[0] - a[0] * c[1],
                    ]
                )
                dc = np.array(
                    [
                        a[1] * b[2] - a[2] * b[1],
                        a[2] * b[0] - a[0] * b[2],
                        a[0] * b[1] - a[1] * b[0],
                    ]
                )

                det = float(np.dot(a, da))
                s = signs[row]
                vals[row] = s * det
                jac[row, 3 * ia:3 * ia + 3] = s * da
                jac[row, 3 * ib:3 * ib + 3] = s * db
                jac[row, 3 * ic:3 * ic + 3] = s * dc
                jac[row, -1] = -1.0

            return vals, jac

        def sqp_polish(start):
            p = normalize_inside(start)
            raw = determinant_values(p)

            # Use the signed determinant of the ordered triple.  No
            # determinant is expected to be exactly zero after annealing.
            signed_raw = np.empty_like(raw)
            for q, (ia, ib, ic) in enumerate(triples):
                a, b, c = p[ia], p[ib], p[ic]
                signed_raw[q] = np.linalg.det(
                    np.asarray((a, b, c), dtype=np.float64)
                )
            signs = np.where(signed_raw >= 0.0, 1.0, -1.0)

            initial_t = max(1.0e-10, float(np.min(signs * signed_raw)))
            z0 = np.concatenate((p.ravel(), [initial_t * 0.985]))

            def objective(z):
                return -z[-1]

            def objective_jac(z):
                g = np.zeros(3 * n + 1, dtype=np.float64)
                g[-1] = -1.0
                return g

            def area_constraint(z):
                vals, _ = signed_determinants_and_jacobian(z, signs)
                return vals

            def area_jacobian(z):
                _, jac = signed_determinants_and_jacobian(z, signs)
                return jac

            def sum_constraint(z):
                return z[: 3 * n].reshape(n, 3).sum(axis=1) - 1.0

            sum_jac = np.zeros((n, 3 * n + 1), dtype=np.float64)
            for i in range(n):
                sum_jac[i, 3 * i:3 * i + 3] = 1.0

            constraints = [
                {
                    "type": "ineq",
                    "fun": area_constraint,
                    "jac": area_jacobian,
                },
                {
                    "type": "eq",
                    "fun": sum_constraint,
                    "jac": lambda z: sum_jac,
                },
            ]

            lower = np.full(3 * n + 1, margin, dtype=np.float64)
            upper = np.ones(3 * n + 1, dtype=np.float64)
            lower[-1] = 0.0
            upper[-1] = 0.25

            result = minimize(
                objective,
                z0,
                jac=objective_jac,
                constraints=constraints,
                bounds=list(zip(lower, upper)),
                method="SLSQP",
                options={
                    "maxiter": 650,
                    "ftol": 2.0e-12,
                    "disp": False,
                },
            )

            candidate = result.x[: 3 * n].reshape(n, 3)
            candidate = normalize_inside(candidate)

            if (
                not np.all(np.isfinite(candidate))
                or np.min(candidate) <= margin * 0.5
            ):
                return p

            return candidate

        # Multiple elite starts are important because the fixed orientation
        # pattern defines a separate local optimization basin.
        for _, candidate in elite:
            polished = sqp_polish(candidate)
            polished_score = score(polished)
            if better(polished_score, best_score):
                best_points = polished.copy()
                best_score = polished_score

    except (ImportError, ValueError, RuntimeError):
        # SciPy is optional.  The annealer remains a complete solver when it
        # is unavailable or an individual constrained solve fails.
        pass

    # Final small barycentric coordinate polish around the best
    # SQP/annealing result.  This is deliberately local and uses exact
    # lexicographic scores.
    best_points = normalize_inside(best_points)
    current_score = score(best_points)

    for pass_index in range(7):
        step = 0.0018 * (0.43 ** pass_index)
        for i in range(n):
            for first, second in ((0, 1), (0, 2), (1, 2)):
                for sign in (1.0, -1.0):
                    trial = best_points.copy()
                    trial[i, first] += sign * step
                    trial[i, second] -= sign * step

                    if np.min(trial[i]) <= margin:
                        continue

                    trial_score = score(trial)
                    if better(trial_score, current_score):
                        best_points = trial
                        current_score = trial_score

    # Explicitly recompute after the final normalization.  The returned
    # objective therefore exactly corresponds to the returned Cartesian
    # points.
    best_points = normalize_inside(best_points)
    final_values = determinant_values(best_points)
    min_area = float(np.min(final_values))

    # Barycentric-to-Cartesian conversion for vertices
    # (0, 0), (1, 0), and (1/2, sqrt(3)/2).
    cartesian = np.empty((n, 2), dtype=np.float64)
    cartesian[:, 0] = best_points[:, 1] + 0.5 * best_points[:, 2]
    cartesian[:, 1] = 0.5 * sqrt3 * best_points[:, 2]

    return cartesian, min_area


def run_search_point(n=11):
    return find_best_placement(n)


# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({
        "points": points.tolist(),
        "min_area": min_area,
    }))
```
