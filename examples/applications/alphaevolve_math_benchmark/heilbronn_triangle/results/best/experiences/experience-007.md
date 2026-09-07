Reusable design decisions for max-min geometric optimization.

- Barycentric Max-Min Annealing with Active-Triple Polishing: Barycentric coordinates represent every point with positive normalized coordinates, make strict triangle containment automatic, and reduce the normalized area objective to the absolute determinant of the three corresponding barycentric-coordinate rows, allowing the exact bottleneck quantity to be evaluated without repeatedly dividing by the large triangle area.
- Barycentric Max-Min Annealing with Active-Triple Polishing: The search combines 24 to 40 independent restarts for n=11, 8,000 to 20,000 annealing moves per restart, active-triple perturbations focused on the triples with the smallest determinants, a lexicographic score based on the minimum determinant and the average of the smallest few determinants, and 8 to 12 coordinate-polishing passes; future max-min designs should combine multi-basin exploration with targeted refinement of the currently limiting constraints.
- Barycentric Max-Min Annealing with Active-Triple Polishing: In the reported evaluation, the implementation produced min_area 0.03180044269448745, score and target_ratio 0.8712450053284234, validity 1.0, and evaluation time 26.263588814996183 seconds, with no reported error.
- Elite Barycentric Annealing with Signed-Constraint SQP Polishing: The algorithm represents each point with positive barycentric coordinates summing to one, making strict containment automatic and making every normalized triangle area equal to the absolute determinant of the three corresponding barycentric rows.
- Elite Barycentric Annealing with Signed-Constraint SQP Polishing: The algorithm combines a diverse deterministic population with max-min simulated annealing that preferentially perturbs points involved in the smallest-area triples, uses reheating after stagnation, ranks candidates by the minimum determinant and the mean of the smallest determinants, and retains a global elite pool.
- Elite Barycentric Annealing with Signed-Constraint SQP Polishing: The algorithm polishes multiple elite candidates with SLSQP by fixing each candidate's signed-area orientation pattern and maximizing an auxiliary minimum-area variable under signed-area and strict-interior constraints, then applies short barycentric coordinate polishing and full validity checks.
- Elite Barycentric Annealing with Signed-Constraint SQP Polishing: For n=11, the implementation obtained a minimum normalized area of 0.032654379687043086, a target ratio of 0.8946405393710435, validity 1.0, and an evaluation time of 35.2750468601007 seconds.
- Barycentric Active-Constraint Annealing with SQP Polishing: 该算法将每个点表示为满足 a+b+c=1 且三个坐标均为正的重心坐标，并将每个点三元组的归一化面积计算为其三行坐标组成矩阵的绝对行列式，从而避免笛卡尔几何缩放误差并自动保持点位于单纯形内部。
- Barycentric Active-Constraint Annealing with SQP Polishing: 该算法使用确定性的多起点初始化，包括不同 Dirichlet 分布、折叠低差异序列和带零和重心扰动的三角晶格点；模拟退火中的大多数变异针对当前最小面积三元组涉及的点，其余变异随机选择点，并在停滞时恢复局部最优状态、重新加热高频瓶颈点。
- Barycentric Active-Constraint Annealing with SQP Polishing: 该算法对精英候选执行带辅助变量 t 的约束 SLSQP 精修，保持每个三元组的当前有向方向，约束 signed_determinant(triple) >= t、每个点的重心坐标和为 1 且每个坐标至少为正的小 margin，并提供解析行列式 Jacobian；在 n=11 时得到 min_area=0.03516839966291153、validity=1.0、target_ratio=0.9635177989838776，评估时间为 55.2806326369755。

```python
#!/usr/bin/env python3
"""Heuristic solver for the Heilbronn triangle problem.

Points are optimized in barycentric coordinates.  In these coordinates the
normalized area of a triangle is simply the absolute determinant of the three
corresponding barycentric rows.
"""

import itertools
import json
import math
import numpy as np


# EVOLVE_START
def find_best_placement(n):
    """Search for a placement maximizing the minimum normalized triangle area."""
    if n < 3:
        raise ValueError("at least three points are required")

    rng = np.random.default_rng(918273645 + 31 * n)
    triples = np.asarray(list(itertools.combinations(range(n), 3)), dtype=np.int32)
    margin = 1.0e-8
    sqrt3 = math.sqrt(3.0)

    def normalize_inside(points):
        """Keep barycentric coordinates strictly positive and normalized."""
        points = np.maximum(np.asarray(points, dtype=np.float64), margin)
        points /= points.sum(axis=1, keepdims=True)

        # The normalization above can reduce a coordinate very slightly below
        # the margin.  A second pass makes the strict-interior guarantee robust.
        points = np.maximum(points, margin)
        points /= points.sum(axis=1, keepdims=True)
        return points

    def determinant_values(points):
        """Return absolute determinants for all point triples."""
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
        """Lexicographic bottleneck score.

        The secondary term is useful during polishing: it avoids configurations
        where one constraint is good only because several others are nearly
        degenerate.
        """
        values = determinant_values(points)
        count = min(10, len(values))
        smallest = np.partition(values, count - 1)[:count]
        return float(smallest.min()), float(smallest.mean())

    def better(left, right):
        return left[0] > right[0] + 1.0e-14 or (
            abs(left[0] - right[0]) <= 1.0e-14
            and left[1] > right[1] + 1.0e-14
        )

    def make_candidate(kind):
        """Generate a mixture of random, lattice-like, and low-discrepancy starts."""
        if kind == 0:
            # Dirichlet samples provide genuinely independent random starts.
            p = rng.dirichlet(np.ones(3), size=n)
        elif kind == 1:
            # Jittered points on a triangular barycentric lattice.
            k = max(4, int(math.ceil(math.sqrt(2.0 * n))) + 1)
            lattice = []
            for i in range(1, k):
                for j in range(1, k - i):
                    lattice.append((i / k, j / k, 1.0 - (i + j) / k))
            if len(lattice) < n:
                p = rng.dirichlet(np.ones(3), size=n)
            else:
                p = np.asarray(lattice, dtype=np.float64)
                rng.shuffle(p)
                p = p[:n].copy()
                p += rng.normal(0.0, 0.025 / k, p.shape)
                p = normalize_inside(p)
        elif kind == 2:
            # A folded low-discrepancy sequence in the unit triangle.
            golden1 = (math.sqrt(5.0) - 1.0) / 2.0
            golden2 = (math.sqrt(3.0) - 1.0) / 2.0
            p = []
            offset = rng.random()
            for q in range(n):
                u = (offset + (q + 1) * golden1) % 1.0
                v = (0.37 * offset + (q + 1) * golden2) % 1.0
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                p.append((max(u, margin), max(v, margin),
                          max(1.0 - u - v, margin)))
            p = normalize_inside(np.asarray(p))
        else:
            # A deliberately uneven Dirichlet start explores a different set
            # of basins than the uniform random population.
            p = rng.dirichlet(np.full(3, 0.65), size=n)

        return normalize_inside(p)

    best_points = None
    best_score = (-1.0, -1.0)

    # The schedule is substantial enough for n=11 while remaining practical
    # for repeated calls by an evaluator.
    restarts = 26 if n == 11 else 18
    anneal_moves = 7200 if n == 11 else 5000

    for restart in range(restarts):
        points = make_candidate(restart % 4)
        current_score = score(points)
        local_best_points = points.copy()
        local_best_score = current_score
        stagnant = 0

        for iteration in range(anneal_moves):
            values = determinant_values(points)

            # Usually select a random point, but frequently attack points in
            # currently active (nearly collinear) triples.
            if iteration % 4 == 0:
                active_count = min(12, len(values))
                active = np.argpartition(values, active_count - 1)[:active_count]
                counts = np.bincount(
                    triples[active].ravel(), minlength=n
                ).astype(np.float64)
                counts += 0.15
                point_index = int(rng.choice(n, p=counts / counts.sum()))
            else:
                point_index = int(rng.integers(n))

            # Transfer mass between two barycentric coordinates.  This
            # preserves their sum exactly, so every move stays normalized.
            first, second = rng.choice(3, size=2, replace=False)
            current = points[point_index]
            available_positive = current[second] - margin
            available_negative = current[first] - margin

            # Large early moves help escape poor basins; late moves polish.
            fraction = 1.0 - iteration / anneal_moves
            step_scale = 0.18 * fraction + 0.004
            delta = rng.normal(0.0, step_scale)
            delta = float(np.clip(delta, -available_positive, available_negative))
            if abs(delta) < 1.0e-14:
                continue

            trial = points.copy()
            trial[point_index, first] += delta
            trial[point_index, second] -= delta
            if np.min(trial[point_index]) <= margin:
                continue

            trial_score = score(trial)
            improvement = trial_score[0] - current_score[0]

            # Temperature has an absolute floor because poor random starts can
            # have an extremely tiny bottleneck determinant.
            temperature = max(
                2.0e-5,
                0.0018 * (0.015 ** (iteration / anneal_moves)),
            )
            accept = better(trial_score, current_score)
            if not accept and improvement < 0.0:
                accept = rng.random() < math.exp(
                    max(-60.0, improvement / temperature)
                )
            elif not accept and abs(improvement) <= 1.0e-14:
                accept = trial_score[1] >= current_score[1] or rng.random() < 0.02

            if accept:
                points = trial
                current_score = trial_score
                stagnant = 0
                if better(current_score, local_best_score):
                    local_best_points = points.copy()
                    local_best_score = current_score
            else:
                stagnant += 1

            # Reheating is particularly helpful after all active triples have
            # become nearly equally limiting.
            if stagnant > 850:
                points = local_best_points.copy()
                current_score = local_best_score
                stagnant = 0

                # Occasionally make a larger mutation to the most frequently
                # active point before resuming annealing.
                if rng.random() < 0.7:
                    values = determinant_values(points)
                    active_count = min(8, len(values))
                    active = np.argpartition(values, active_count - 1)[:active_count]
                    counts = np.bincount(
                        triples[active].ravel(), minlength=n
                    )
                    point_index = int(np.argmax(counts))
                    noise = rng.normal(0.0, 0.035, 3)
                    noise -= noise.mean()
                    trial = points.copy()
                    trial[point_index] += noise
                    if np.min(trial[point_index]) > margin:
                        trial = normalize_inside(trial)
                        trial_score = score(trial)
                        if better(trial_score, current_score):
                            points, current_score = trial, trial_score

        # Coordinate-wise deterministic polishing.  Each direction is an
        # exact barycentric transfer and therefore needs no projection.
        points = local_best_points.copy()
        current_score = score(points)
        for polish_pass in range(11):
            step = 0.035 * (0.48 ** polish_pass)
            for point_index in range(n):
                for first, second in ((0, 1), (0, 2), (1, 2)):
                    improved = True
                    while improved:
                        improved = False
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
                                improved = True
                                break

        # Tiny final perturbation/polishing rounds can break exact numerical
        # ties among active constraints.
        for _ in range(3):
            trial = points.copy()
            index = int(rng.integers(n))
            noise = rng.normal(0.0, 2.0e-4, 3)
            noise -= noise.mean()
            trial[index] += noise
            if np.min(trial[index]) > margin:
                trial = normalize_inside(trial)
                trial_score = score(trial)
                if better(trial_score, current_score):
                    points, current_score = trial, trial_score

        if better(current_score, best_score):
            best_score = current_score
            best_points = points.copy()

    # Enforce the strict interior condition one final time and recompute the
    # objective after this harmless numerical cleanup.
    best_points = normalize_inside(best_points)
    final_values = determinant_values(best_points)
    min_area = float(final_values.min())

    # Cartesian coordinates for vertices (0,0), (1,0), (1/2,sqrt(3)/2).
    cartesian = np.empty((n, 2), dtype=np.float64)
    cartesian[:, 0] = best_points[:, 1] + 0.5 * best_points[:, 2]
    cartesian[:, 1] = 0.5 * sqrt3 * best_points[:, 2]
    return cartesian, min_area


def run_search_point(n=11):
    return find_best_placement(n)


# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points.tolist(), "min_area": min_area}))
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
            # Interior triangular-lattice points, with a small random jitter.
            k = max(5, int(math.ceil(math.sqrt(2.0 * n))) + 1)
            lattice = []
            for i in range(1, k):
                for j in range(1, k - i):
                    lattice.append(
                        (i / k, j / k, 1.0 - (i + j) / k)
                    )

            if len(lattice) < n:
                p = rng.dirichlet(np.ones(3), size=n)
            else:
                p = np.asarray(lattice, dtype=np.float64)
                rng.shuffle(p)
                p = p[:n].copy()
                p += rng.normal(0.0, 0.025 / k, p.shape)
                p = normalize_inside(p)

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

            # Use the signed determinant of the ordered triple.  No determinant
            # is expected to be exactly zero after annealing.
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

    # Final small barycentric coordinate polish around the best SQP/annealing
    # result.  This is deliberately local and uses exact lexicographic scores.
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
    # objective therefore exactly corresponds to the returned Cartesian points.
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
"""Initial 11-point construction in an equilateral triangle."""

import json
import itertools
import math
import random


# EVOLVE_START
def run_search_point(n=11):
    """Search for an interior n-point configuration with large minimum area."""
    import numpy as np

    def find_best_placement(n):
        if n < 3:
            raise ValueError("at least three points are required")

        rng = np.random.default_rng(20240517)
        margin = 2.0e-6
        triples = np.asarray(list(itertools.combinations(range(n), 3)), dtype=np.int32)
        triple_count = len(triples)

        def normalize_barycentric(points):
            """Project rows into the strict interior of the simplex."""
            points = np.maximum(np.asarray(points, dtype=float), margin)
            points /= points.sum(axis=1, keepdims=True)
            # Convexly move points inward if normalization put a coordinate
            # closer to the boundary than the requested numerical margin.
            smallest = float(points.min())
            if smallest < margin:
                weight = min(1.0, (margin - smallest) / (1.0 / 3.0 - smallest))
                points = (1.0 - weight) * points + weight / 3.0
            return points

        def signed_determinants(points):
            rows = points[triples]
            return (
                rows[:, 0, 0]
                * (rows[:, 1, 1] * rows[:, 2, 2]
                   - rows[:, 1, 2] * rows[:, 2, 1])
                - rows[:, 0, 1]
                * (rows[:, 1, 0] * rows[:, 2, 2]
                   - rows[:, 1, 2] * rows[:, 2, 0])
                + rows[:, 0, 2]
                * (rows[:, 1, 0] * rows[:, 2, 1]
                   - rows[:, 1, 1] * rows[:, 2, 0])
            )

        def quality(points):
            values = np.sort(np.abs(signed_determinants(points)))
            width = min(12, len(values))
            return float(values[0]), float(values[:width].mean())

        def better(left, right, tolerance=1.0e-13):
            if left[0] > right[0] + tolerance:
                return True
            return abs(left[0] - right[0]) <= tolerance and left[1] > right[1]

        def radical_inverse(index, base):
            result = 0.0
            scale = 1.0 / base
            while index:
                index, digit = divmod(index, base)
                result += digit * scale
                scale /= base
            return result

        def low_discrepancy_start(offset):
            result = []
            shift_u = (0.1732050807568877 * offset) % 1.0
            shift_v = (0.2718281828459045 * offset) % 1.0
            for index in range(1, n + 1):
                u = (radical_inverse(index + 17 * offset, 2) + shift_u) % 1.0
                v = (radical_inverse(index + 17 * offset, 3) + shift_v) % 1.0
                root = math.sqrt(max(u, 1.0e-15))
                result.append([1.0 - root, root * (1.0 - v), root * v])
            return normalize_barycentric(result)

        def lattice_points():
            # Strict interior lattice points have three positive integer
            # coordinates summing to k.  For n=11 this selects k=7, yielding
            # 15 possible points instead of the insufficient k=6 lattice.
            k = 3
            while (k - 1) * (k - 2) // 2 < n:
                k += 1
            result = []
            for first in range(1, k - 1):
                for second in range(1, k - first):
                    third = k - first - second
                    if third > 0:
                        result.append([first / k, second / k, third / k])
            return np.asarray(result, dtype=float)

        def jitter_lattice(points, scale):
            result = points.copy()
            for row in range(len(result)):
                # Two transfers give zero-sum jitter without projecting out of
                # barycentric space.
                for _ in range(2):
                    source, target = rng.choice(3, size=2, replace=False)
                    limit = result[row, source] - margin
                    delta = min(abs(float(rng.normal(0.0, scale))), 0.7 * limit)
                    result[row, source] -= delta
                    result[row, target] += delta
            return normalize_barycentric(result)

        starts = []

        # Uniform and both boundary- and center-biased Dirichlet starts.
        for alpha in (1.0, 1.0, 0.55, 0.75, 1.7, 3.0):
            starts.append(normalize_barycentric(rng.dirichlet([alpha] * 3, n)))

        for offset in range(4):
            starts.append(low_discrepancy_start(offset + 1))

        lattice = lattice_points()
        lattice_restarts = 7 if n == 11 else 5
        for restart in range(lattice_restarts):
            if len(lattice) == n:
                subset = lattice.copy()
            else:
                indices = rng.choice(len(lattice), size=n, replace=False)
                subset = lattice[indices]
            starts.append(jitter_lattice(subset, 0.008 + 0.003 * restart))

        # Remove accidental duplicate starts and reject numerically degenerate
        # seeds. A little additional jitter repairs lattice-derived seeds.
        accepted_starts = []
        fingerprints = set()
        for start in starts:
            candidate = start
            for attempt in range(4):
                if np.min(np.abs(signed_determinants(candidate))) > 1.0e-9:
                    break
                candidate = jitter_lattice(candidate, 0.003 * (attempt + 1))
            fingerprint = tuple(np.round(candidate.ravel(), 7))
            if fingerprint not in fingerprints:
                fingerprints.add(fingerprint)
                accepted_starts.append(candidate)

        def anneal(initial, iterations):
            current = initial.copy()
            current_quality = quality(current)
            local_best = current.copy()
            local_quality = current_quality
            stale = 0
            reheat_until = -1

            for iteration in range(iterations):
                fraction = iteration / max(1, iterations - 1)
                step = 0.115 * (1.0 - fraction) ** 1.45 + 0.0015
                temperature = 0.0017 * (1.0 - fraction) ** 2.0 + 1.0e-7
                if iteration < reheat_until:
                    step = max(step, 0.035)
                    temperature = max(temperature, 0.00045)

                determinants = np.abs(signed_determinants(current))
                active_count = min(14, triple_count)
                active = np.argpartition(determinants, active_count - 1)[:active_count]

                if rng.random() < 0.88:
                    chosen_triple = triples[int(rng.choice(active))]
                    point_index = int(rng.choice(chosen_triple))
                else:
                    point_index = int(rng.integers(n))

                source, target = rng.choice(3, size=2, replace=False)
                available = current[point_index, source] - margin
                room = 1.0 - margin - current[point_index, target]
                maximum = min(available, room)
                if maximum <= 0.0:
                    continue

                # Most steps are local, while occasional large transfers allow
                # the trajectory to leave an orientation/combinatorial basin.
                if rng.random() < 0.08:
                    amount = float(rng.random()) * maximum
                else:
                    amount = min(abs(float(rng.normal(0.0, step))), maximum)

                proposal = current.copy()
                proposal[point_index, source] -= amount
                proposal[point_index, target] += amount
                proposal_quality = quality(proposal)

                old_energy = current_quality[0] + 0.018 * current_quality[1]
                new_energy = proposal_quality[0] + 0.018 * proposal_quality[1]
                delta = new_energy - old_energy
                accept = better(proposal_quality, current_quality)
                if not accept and temperature > 0.0:
                    accept = rng.random() < math.exp(max(-60.0, delta / temperature))

                if accept:
                    current = proposal
                    current_quality = proposal_quality

                if better(current_quality, local_quality):
                    local_best = current.copy()
                    local_quality = current_quality
                    stale = 0
                else:
                    stale += 1

                if stale >= 1450:
                    current = local_best.copy()
                    # Reheat by perturbing the point most frequently occurring
                    # among the current bottleneck triples.
                    values = np.abs(signed_determinants(current))
                    bottlenecks = np.argpartition(values, active_count - 1)[:active_count]
                    counts = np.bincount(
                        triples[bottlenecks].ravel(), minlength=n
                    )
                    point_index = int(np.argmax(counts))
                    for _ in range(3):
                        source, target = rng.choice(3, size=2, replace=False)
                        maximum = current[point_index, source] - margin
                        if maximum > 0.0:
                            amount = min(
                                maximum * 0.65,
                                abs(float(rng.normal(0.0, 0.045))),
                            )
                            current[point_index, source] -= amount
                            current[point_index, target] += amount
                    current_quality = quality(current)
                    reheat_until = iteration + 300
                    stale = 0

            return local_best, local_quality

        elites = []
        anneal_iterations = 14500 if n == 11 else max(7000, 120000 // n)
        for start in accepted_starts:
            candidate, candidate_quality = anneal(start, anneal_iterations)
            elites.append((candidate_quality, candidate))

        elites.sort(key=lambda item: (item[0][0], item[0][1]), reverse=True)
        elite_count = min(8, len(elites))
        best_quality, best_points = elites[0][0], elites[0][1].copy()

        # Max-min polishing with an epigraph variable and fixed determinant
        # orientations. SciPy is optional so the annealed result remains a
        # valid deterministic fallback in minimal Python environments.
        try:
            from scipy.optimize import minimize

            equality_jacobian = np.zeros((n, 3 * n + 1), dtype=float)
            for point_index in range(n):
                equality_jacobian[
                    point_index, 3 * point_index:3 * point_index + 3
                ] = 1.0

            def polish(seed):
                seed_determinants = signed_determinants(seed)
                signs = np.where(seed_determinants >= 0.0, 1.0, -1.0)
                initial_t = float(np.min(np.abs(seed_determinants)))
                initial = np.concatenate((seed.ravel(), [initial_t]))

                def objective(vector):
                    return -float(vector[-1])

                def objective_jacobian(vector):
                    result = np.zeros_like(vector)
                    result[-1] = -1.0
                    return result

                def equalities(vector):
                    return vector[:-1].reshape(n, 3).sum(axis=1) - 1.0

                def inequality_values(vector):
                    points = vector[:-1].reshape(n, 3)
                    return signs * signed_determinants(points) - vector[-1]

                def inequality_jacobian(vector):
                    points = vector[:-1].reshape(n, 3)
                    jacobian = np.zeros((triple_count, 3 * n + 1), dtype=float)
                    for row, (first, second, third) in enumerate(triples):
                        p0, p1, p2 = (
                            points[first],
                            points[second],
                            points[third],
                        )
                        sign = signs[row]
                        jacobian[row, 3 * first:3 * first + 3] = sign * np.cross(p1, p2)
                        jacobian[row, 3 * second:3 * second + 3] = sign * np.cross(p2, p0)
                        jacobian[row, 3 * third:3 * third + 3] = sign * np.cross(p0, p1)
                        jacobian[row, -1] = -1.0
                    return jacobian

                constraints = (
                    {
                        "type": "eq",
                        "fun": equalities,
                        "jac": lambda vector: equality_jacobian,
                    },
                    {
                        "type": "ineq",
                        "fun": inequality_values,
                        "jac": inequality_jacobian,
                    },
                )
                bounds = [(margin, 1.0)] * (3 * n) + [(0.0, 1.0)]
                result = minimize(
                    objective,
                    initial,
                    method="SLSQP",
                    jac=objective_jacobian,
                    bounds=bounds,
                    constraints=constraints,
                    options={
                        "maxiter": 1200,
                        "ftol": 2.0e-12,
                        "disp": False,
                    },
                )
                candidate = normalize_barycentric(result.x[:-1].reshape(n, 3))
                return candidate, quality(candidate)

            for _, elite in elites[:elite_count]:
                polished, polished_quality = polish(elite)
                if better(polished_quality, best_quality):
                    best_points = polished
                    best_quality = polished_quality
        except (ImportError, ValueError, ArithmeticError):
            pass

        # Final deterministic coordinate-wise transfer search. This can repair
        # small SLSQP tolerances and improve secondary bottlenecks.
        step = 0.004
        for _ in range(10):
            changed = False
            for point_index in range(n):
                for source in range(3):
                    for target in range(3):
                        if source == target:
                            continue
                        maximum = best_points[point_index, source] - margin
                        if maximum <= 0.0:
                            continue
                        amount = min(step, maximum)
                        candidate = best_points.copy()
                        candidate[point_index, source] -= amount
                        candidate[point_index, target] += amount
                        candidate_quality = quality(candidate)
                        if better(candidate_quality, best_quality):
                            best_points = candidate
                            best_quality = candidate_quality
                            changed = True
            step *= 0.62 if changed else 0.45

        best_points = normalize_barycentric(best_points)
        exact_values = np.abs(signed_determinants(best_points))
        exact_minimum = float(np.min(exact_values))

        if (
            not np.all(best_points > 0.0)
            or exact_minimum <= 1.0e-12
            or any(
                np.linalg.norm(best_points[i] - best_points[j]) <= 1.0e-12
                for i, j in itertools.combinations(range(n), 2)
            )
        ):
            raise RuntimeError("search produced an invalid point configuration")

        height = math.sqrt(3.0) / 2.0
        cartesian = np.empty((n, 2), dtype=float)
        cartesian[:, 0] = best_points[:, 1] + 0.5 * best_points[:, 2]
        cartesian[:, 1] = height * best_points[:, 2]
        return cartesian, exact_minimum

    points, min_area = find_best_placement(n)
    return points.tolist(), float(min_area)
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))

```

```python
#!/usr/bin/env python3
"""Heuristic solver for the Heilbronn triangle problem.

Points are optimized in barycentric coordinates.  In these coordinates the
normalized area of a triangle is simply the absolute determinant of the three
corresponding barycentric rows.
"""

import itertools
import json
import math
import numpy as np


# EVOLVE_START
def find_best_placement(n):
    """Search for a placement maximizing the minimum normalized triangle area."""
    if n < 3:
        raise ValueError("at least three points are required")

    rng = np.random.default_rng(918273645 + 31 * n)
    triples = np.asarray(list(itertools.combinations(range(n), 3)), dtype=np.int32)
    margin = 1.0e-8
    sqrt3 = math.sqrt(3.0)

    def normalize_inside(points):
        """Keep barycentric coordinates strictly positive and normalized."""
        points = np.maximum(np.asarray(points, dtype=np.float64), margin)
        points /= points.sum(axis=1, keepdims=True)

        # The normalization above can reduce a coordinate very slightly below
        # the margin.  A second pass makes the strict-interior guarantee robust.
        points = np.maximum(points, margin)
        points /= points.sum(axis=1, keepdims=True)
        return points

    def determinant_values(points):
        """Return absolute determinants for all point triples."""
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
        """Lexicographic bottleneck score.

        The secondary term is useful during polishing: it avoids configurations
        where one constraint is good only because several others are nearly
        degenerate.
        """
        values = determinant_values(points)
        count = min(10, len(values))
        smallest = np.partition(values, count - 1)[:count]
        return float(smallest.min()), float(smallest.mean())

    def better(left, right):
        return left[0] > right[0] + 1.0e-14 or (
            abs(left[0] - right[0]) <= 1.0e-14
            and left[1] > right[1] + 1.0e-14
        )

    def make_candidate(kind):
        """Generate a mixture of random, lattice-like, and low-discrepancy starts."""
        if kind == 0:
            # Dirichlet samples provide genuinely independent random starts.
            p = rng.dirichlet(np.ones(3), size=n)
        elif kind == 1:
            # Jittered points on a triangular barycentric lattice.
            k = max(4, int(math.ceil(math.sqrt(2.0 * n))) + 1)
            lattice = []
            for i in range(1, k):
                for j in range(1, k - i):
                    lattice.append((i / k, j / k, 1.0 - (i + j) / k))
            if len(lattice) < n:
                p = rng.dirichlet(np.ones(3), size=n)
            else:
                p = np.asarray(lattice, dtype=np.float64)
                rng.shuffle(p)
                p = p[:n].copy()
                p += rng.normal(0.0, 0.025 / k, p.shape)
                p = normalize_inside(p)
        elif kind == 2:
            # A folded low-discrepancy sequence in the unit triangle.
            golden1 = (math.sqrt(5.0) - 1.0) / 2.0
            golden2 = (math.sqrt(3.0) - 1.0) / 2.0
            p = []
            offset = rng.random()
            for q in range(n):
                u = (offset + (q + 1) * golden1) % 1.0
                v = (0.37 * offset + (q + 1) * golden2) % 1.0
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                p.append((max(u, margin), max(v, margin),
                          max(1.0 - u - v, margin)))
            p = normalize_inside(np.asarray(p))
        else:
            # A deliberately uneven Dirichlet start explores a different set
            # of basins than the uniform random population.
            p = rng.dirichlet(np.full(3, 0.65), size=n)

        return normalize_inside(p)

    best_points = None
    best_score = (-1.0, -1.0)

    # The schedule is substantial enough for n=11 while remaining practical
    # for repeated calls by an evaluator.
    restarts = 26 if n == 11 else 18
    anneal_moves = 7200 if n == 11 else 5000

    for restart in range(restarts):
        points = make_candidate(restart % 4)
        current_score = score(points)
        local_best_points = points.copy()
        local_best_score = current_score
        stagnant = 0

        for iteration in range(anneal_moves):
            values = determinant_values(points)

            # Usually select a random point, but frequently attack points in
            # currently active (nearly collinear) triples.
            if iteration % 4 == 0:
                active_count = min(12, len(values))
                active = np.argpartition(values, active_count - 1)[:active_count]
                counts = np.bincount(
                    triples[active].ravel(), minlength=n
                ).astype(np.float64)
                counts += 0.15
                point_index = int(rng.choice(n, p=counts / counts.sum()))
            else:
                point_index = int(rng.integers(n))

            # Transfer mass between two barycentric coordinates.  This
            # preserves their sum exactly, so every move stays normalized.
            first, second = rng.choice(3, size=2, replace=False)
            current = points[point_index]
            available_positive = current[second] - margin
            available_negative = current[first] - margin

            # Large early moves help escape poor basins; late moves polish.
            fraction = 1.0 - iteration / anneal_moves
            step_scale = 0.18 * fraction + 0.004
            delta = rng.normal(0.0, step_scale)
            delta = float(np.clip(delta, -available_positive, available_negative))
            if abs(delta) < 1.0e-14:
                continue

            trial = points.copy()
            trial[point_index, first] += delta
            trial[point_index, second] -= delta
            if np.min(trial[point_index]) <= margin:
                continue

            trial_score = score(trial)
            improvement = trial_score[0] - current_score[0]

            # Temperature has an absolute floor because poor random starts can
            # have an extremely tiny bottleneck determinant.
            temperature = max(
                2.0e-5,
                0.0018 * (0.015 ** (iteration / anneal_moves)),
            )
            accept = better(trial_score, current_score)
            if not accept and improvement < 0.0:
                accept = rng.random() < math.exp(
                    max(-60.0, improvement / temperature)
                )
            elif not accept and abs(improvement) <= 1.0e-14:
                accept = trial_score[1] >= current_score[1] or rng.random() < 0.02

            if accept:
                points = trial
                current_score = trial_score
                stagnant = 0
                if better(current_score, local_best_score):
                    local_best_points = points.copy()
                    local_best_score = current_score
            else:
                stagnant += 1

            # Reheating is particularly helpful after all active triples have
            # become nearly equally limiting.
            if stagnant > 850:
                points = local_best_points.copy()
                current_score = local_best_score
                stagnant = 0

                # Occasionally make a larger mutation to the most frequently
                # active point before resuming annealing.
                if rng.random() < 0.7:
                    values = determinant_values(points)
                    active_count = min(8, len(values))
                    active = np.argpartition(values, active_count - 1)[:active_count]
                    counts = np.bincount(
                        triples[active].ravel(), minlength=n
                    )
                    point_index = int(np.argmax(counts))
                    noise = rng.normal(0.0, 0.035, 3)
                    noise -= noise.mean()
                    trial = points.copy()
                    trial[point_index] += noise
                    if np.min(trial[point_index]) > margin:
                        trial = normalize_inside(trial)
                        trial_score = score(trial)
                        if better(trial_score, current_score):
                            points, current_score = trial, trial_score

        # Coordinate-wise deterministic polishing.  Each direction is an
        # exact barycentric transfer and therefore needs no projection.
        points = local_best_points.copy()
        current_score = score(points)
        for polish_pass in range(11):
            step = 0.035 * (0.48 ** polish_pass)
            for point_index in range(n):
                for first, second in ((0, 1), (0, 2), (1, 2)):
                    improved = True
                    while improved:
                        improved = False
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
                                improved = True
                                break

        # Tiny final perturbation/polishing rounds can break exact numerical
        # ties among active constraints.
        for _ in range(3):
            trial = points.copy()
            index = int(rng.integers(n))
            noise = rng.normal(0.0, 2.0e-4, 3)
            noise -= noise.mean()
            trial[index] += noise
            if np.min(trial[index]) > margin:
                trial = normalize_inside(trial)
                trial_score = score(trial)
                if better(trial_score, current_score):
                    points, current_score = trial, trial_score

        if better(current_score, best_score):
            best_score = current_score
            best_points = points.copy()

    # Enforce the strict interior condition one final time and recompute the
    # objective after this harmless numerical cleanup.
    best_points = normalize_inside(best_points)
    final_values = determinant_values(best_points)
    min_area = float(final_values.min())

    # Cartesian coordinates for vertices (0,0), (1,0), (1/2,sqrt(3)/2).
    cartesian = np.empty((n, 2), dtype=np.float64)
    cartesian[:, 0] = best_points[:, 1] + 0.5 * best_points[:, 2]
    cartesian[:, 1] = 0.5 * sqrt3 * best_points[:, 2]
    return cartesian, min_area


def run_search_point(n=11):
    return find_best_placement(n)


# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points.tolist(), "min_area": min_area}))
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
            # Interior triangular-lattice points, with a small random jitter.
            k = max(5, int(math.ceil(math.sqrt(2.0 * n))) + 1)
            lattice = []
            for i in range(1, k):
                for j in range(1, k - i):
                    lattice.append(
                        (i / k, j / k, 1.0 - (i + j) / k)
                    )

            if len(lattice) < n:
                p = rng.dirichlet(np.ones(3), size=n)
            else:
                p = np.asarray(lattice, dtype=np.float64)
                rng.shuffle(p)
                p = p[:n].copy()
                p += rng.normal(0.0, 0.025 / k, p.shape)
                p = normalize_inside(p)

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

            # Use the signed determinant of the ordered triple.  No determinant
            # is expected to be exactly zero after annealing.
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

    # Final small barycentric coordinate polish around the best SQP/annealing
    # result.  This is deliberately local and uses exact lexicographic scores.
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
    # objective therefore exactly corresponds to the returned Cartesian points.
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
"""Initial 11-point construction in an equilateral triangle."""

import json
import itertools
import math
import random


# EVOLVE_START
def run_search_point(n=11):
    """Search for an interior n-point configuration with large minimum area."""
    import numpy as np

    def find_best_placement(n):
        if n < 3:
            raise ValueError("at least three points are required")

        rng = np.random.default_rng(20240517)
        margin = 2.0e-6
        triples = np.asarray(list(itertools.combinations(range(n), 3)), dtype=np.int32)
        triple_count = len(triples)

        def normalize_barycentric(points):
            """Project rows into the strict interior of the simplex."""
            points = np.maximum(np.asarray(points, dtype=float), margin)
            points /= points.sum(axis=1, keepdims=True)
            # Convexly move points inward if normalization put a coordinate
            # closer to the boundary than the requested numerical margin.
            smallest = float(points.min())
            if smallest < margin:
                weight = min(1.0, (margin - smallest) / (1.0 / 3.0 - smallest))
                points = (1.0 - weight) * points + weight / 3.0
            return points

        def signed_determinants(points):
            rows = points[triples]
            return (
                rows[:, 0, 0]
                * (rows[:, 1, 1] * rows[:, 2, 2]
                   - rows[:, 1, 2] * rows[:, 2, 1])
                - rows[:, 0, 1]
                * (rows[:, 1, 0] * rows[:, 2, 2]
                   - rows[:, 1, 2] * rows[:, 2, 0])
                + rows[:, 0, 2]
                * (rows[:, 1, 0] * rows[:, 2, 1]
                   - rows[:, 1, 1] * rows[:, 2, 0])
            )

        def quality(points):
            values = np.sort(np.abs(signed_determinants(points)))
            width = min(12, len(values))
            return float(values[0]), float(values[:width].mean())

        def better(left, right, tolerance=1.0e-13):
            if left[0] > right[0] + tolerance:
                return True
            return abs(left[0] - right[0]) <= tolerance and left[1] > right[1]

        def radical_inverse(index, base):
            result = 0.0
            scale = 1.0 / base
            while index:
                index, digit = divmod(index, base)
                result += digit * scale
                scale /= base
            return result

        def low_discrepancy_start(offset):
            result = []
            shift_u = (0.1732050807568877 * offset) % 1.0
            shift_v = (0.2718281828459045 * offset) % 1.0
            for index in range(1, n + 1):
                u = (radical_inverse(index + 17 * offset, 2) + shift_u) % 1.0
                v = (radical_inverse(index + 17 * offset, 3) + shift_v) % 1.0
                root = math.sqrt(max(u, 1.0e-15))
                result.append([1.0 - root, root * (1.0 - v), root * v])
            return normalize_barycentric(result)

        def lattice_points():
            # Strict interior lattice points have three positive integer
            # coordinates summing to k.  For n=11 this selects k=7, yielding
            # 15 possible points instead of the insufficient k=6 lattice.
            k = 3
            while (k - 1) * (k - 2) // 2 < n:
                k += 1
            result = []
            for first in range(1, k - 1):
                for second in range(1, k - first):
                    third = k - first - second
                    if third > 0:
                        result.append([first / k, second / k, third / k])
            return np.asarray(result, dtype=float)

        def jitter_lattice(points, scale):
            result = points.copy()
            for row in range(len(result)):
                # Two transfers give zero-sum jitter without projecting out of
                # barycentric space.
                for _ in range(2):
                    source, target = rng.choice(3, size=2, replace=False)
                    limit = result[row, source] - margin
                    delta = min(abs(float(rng.normal(0.0, scale))), 0.7 * limit)
                    result[row, source] -= delta
                    result[row, target] += delta
            return normalize_barycentric(result)

        starts = []

        # Uniform and both boundary- and center-biased Dirichlet starts.
        for alpha in (1.0, 1.0, 0.55, 0.75, 1.7, 3.0):
            starts.append(normalize_barycentric(rng.dirichlet([alpha] * 3, n)))

        for offset in range(4):
            starts.append(low_discrepancy_start(offset + 1))

        lattice = lattice_points()
        lattice_restarts = 7 if n == 11 else 5
        for restart in range(lattice_restarts):
            if len(lattice) == n:
                subset = lattice.copy()
            else:
                indices = rng.choice(len(lattice), size=n, replace=False)
                subset = lattice[indices]
            starts.append(jitter_lattice(subset, 0.008 + 0.003 * restart))

        # Remove accidental duplicate starts and reject numerically degenerate
        # seeds. A little additional jitter repairs lattice-derived seeds.
        accepted_starts = []
        fingerprints = set()
        for start in starts:
            candidate = start
            for attempt in range(4):
                if np.min(np.abs(signed_determinants(candidate))) > 1.0e-9:
                    break
                candidate = jitter_lattice(candidate, 0.003 * (attempt + 1))
            fingerprint = tuple(np.round(candidate.ravel(), 7))
            if fingerprint not in fingerprints:
                fingerprints.add(fingerprint)
                accepted_starts.append(candidate)

        def anneal(initial, iterations):
            current = initial.copy()
            current_quality = quality(current)
            local_best = current.copy()
            local_quality = current_quality
            stale = 0
            reheat_until = -1

            for iteration in range(iterations):
                fraction = iteration / max(1, iterations - 1)
                step = 0.115 * (1.0 - fraction) ** 1.45 + 0.0015
                temperature = 0.0017 * (1.0 - fraction) ** 2.0 + 1.0e-7
                if iteration < reheat_until:
                    step = max(step, 0.035)
                    temperature = max(temperature, 0.00045)

                determinants = np.abs(signed_determinants(current))
                active_count = min(14, triple_count)
                active = np.argpartition(determinants, active_count - 1)[:active_count]

                if rng.random() < 0.88:
                    chosen_triple = triples[int(rng.choice(active))]
                    point_index = int(rng.choice(chosen_triple))
                else:
                    point_index = int(rng.integers(n))

                source, target = rng.choice(3, size=2, replace=False)
                available = current[point_index, source] - margin
                room = 1.0 - margin - current[point_index, target]
                maximum = min(available, room)
                if maximum <= 0.0:
                    continue

                # Most steps are local, while occasional large transfers allow
                # the trajectory to leave an orientation/combinatorial basin.
                if rng.random() < 0.08:
                    amount = float(rng.random()) * maximum
                else:
                    amount = min(abs(float(rng.normal(0.0, step))), maximum)

                proposal = current.copy()
                proposal[point_index, source] -= amount
                proposal[point_index, target] += amount
                proposal_quality = quality(proposal)

                old_energy = current_quality[0] + 0.018 * current_quality[1]
                new_energy = proposal_quality[0] + 0.018 * proposal_quality[1]
                delta = new_energy - old_energy
                accept = better(proposal_quality, current_quality)
                if not accept and temperature > 0.0:
                    accept = rng.random() < math.exp(max(-60.0, delta / temperature))

                if accept:
                    current = proposal
                    current_quality = proposal_quality

                if better(current_quality, local_quality):
                    local_best = current.copy()
                    local_quality = current_quality
                    stale = 0
                else:
                    stale += 1

                if stale >= 1450:
                    current = local_best.copy()
                    # Reheat by perturbing the point most frequently occurring
                    # among the current bottleneck triples.
                    values = np.abs(signed_determinants(current))
                    bottlenecks = np.argpartition(values, active_count - 1)[:active_count]
                    counts = np.bincount(
                        triples[bottlenecks].ravel(), minlength=n
                    )
                    point_index = int(np.argmax(counts))
                    for _ in range(3):
                        source, target = rng.choice(3, size=2, replace=False)
                        maximum = current[point_index, source] - margin
                        if maximum > 0.0:
                            amount = min(
                                maximum * 0.65,
                                abs(float(rng.normal(0.0, 0.045))),
                            )
                            current[point_index, source] -= amount
                            current[point_index, target] += amount
                    current_quality = quality(current)
                    reheat_until = iteration + 300
                    stale = 0

            return local_best, local_quality

        elites = []
        anneal_iterations = 14500 if n == 11 else max(7000, 120000 // n)
        for start in accepted_starts:
            candidate, candidate_quality = anneal(start, anneal_iterations)
            elites.append((candidate_quality, candidate))

        elites.sort(key=lambda item: (item[0][0], item[0][1]), reverse=True)
        elite_count = min(8, len(elites))
        best_quality, best_points = elites[0][0], elites[0][1].copy()

        # Max-min polishing with an epigraph variable and fixed determinant
        # orientations. SciPy is optional so the annealed result remains a
        # valid deterministic fallback in minimal Python environments.
        try:
            from scipy.optimize import minimize

            equality_jacobian = np.zeros((n, 3 * n + 1), dtype=float)
            for point_index in range(n):
                equality_jacobian[
                    point_index, 3 * point_index:3 * point_index + 3
                ] = 1.0

            def polish(seed):
                seed_determinants = signed_determinants(seed)
                signs = np.where(seed_determinants >= 0.0, 1.0, -1.0)
                initial_t = float(np.min(np.abs(seed_determinants)))
                initial = np.concatenate((seed.ravel(), [initial_t]))

                def objective(vector):
                    return -float(vector[-1])

                def objective_jacobian(vector):
                    result = np.zeros_like(vector)
                    result[-1] = -1.0
                    return result

                def equalities(vector):
                    return vector[:-1].reshape(n, 3).sum(axis=1) - 1.0

                def inequality_values(vector):
                    points = vector[:-1].reshape(n, 3)
                    return signs * signed_determinants(points) - vector[-1]

                def inequality_jacobian(vector):
                    points = vector[:-1].reshape(n, 3)
                    jacobian = np.zeros((triple_count, 3 * n + 1), dtype=float)
                    for row, (first, second, third) in enumerate(triples):
                        p0, p1, p2 = (
                            points[first],
                            points[second],
                            points[third],
                        )
                        sign = signs[row]
                        jacobian[row, 3 * first:3 * first + 3] = sign * np.cross(p1, p2)
                        jacobian[row, 3 * second:3 * second + 3] = sign * np.cross(p2, p0)
                        jacobian[row, 3 * third:3 * third + 3] = sign * np.cross(p0, p1)
                        jacobian[row, -1] = -1.0
                    return jacobian

                constraints = (
                    {
                        "type": "eq",
                        "fun": equalities,
                        "jac": lambda vector: equality_jacobian,
                    },
                    {
                        "type": "ineq",
                        "fun": inequality_values,
                        "jac": inequality_jacobian,
                    },
                )
                bounds = [(margin, 1.0)] * (3 * n) + [(0.0, 1.0)]
                result = minimize(
                    objective,
                    initial,
                    method="SLSQP",
                    jac=objective_jacobian,
                    bounds=bounds,
                    constraints=constraints,
                    options={
                        "maxiter": 1200,
                        "ftol": 2.0e-12,
                        "disp": False,
                    },
                )
                candidate = normalize_barycentric(result.x[:-1].reshape(n, 3))
                return candidate, quality(candidate)

            for _, elite in elites[:elite_count]:
                polished, polished_quality = polish(elite)
                if better(polished_quality, best_quality):
                    best_points = polished
                    best_quality = polished_quality
        except (ImportError, ValueError, ArithmeticError):
            pass

        # Final deterministic coordinate-wise transfer search. This can repair
        # small SLSQP tolerances and improve secondary bottlenecks.
        step = 0.004
        for _ in range(10):
            changed = False
            for point_index in range(n):
                for source in range(3):
                    for target in range(3):
                        if source == target:
                            continue
                        maximum = best_points[point_index, source] - margin
                        if maximum <= 0.0:
                            continue
                        amount = min(step, maximum)
                        candidate = best_points.copy()
                        candidate[point_index, source] -= amount
                        candidate[point_index, target] += amount
                        candidate_quality = quality(candidate)
                        if better(candidate_quality, best_quality):
                            best_points = candidate
                            best_quality = candidate_quality
                            changed = True
            step *= 0.62 if changed else 0.45

        best_points = normalize_barycentric(best_points)
        exact_values = np.abs(signed_determinants(best_points))
        exact_minimum = float(np.min(exact_values))

        if (
            not np.all(best_points > 0.0)
            or exact_minimum <= 1.0e-12
            or any(
                np.linalg.norm(best_points[i] - best_points[j]) <= 1.0e-12
                for i, j in itertools.combinations(range(n), 2)
            )
        ):
            raise RuntimeError("search produced an invalid point configuration")

        height = math.sqrt(3.0) / 2.0
        cartesian = np.empty((n, 2), dtype=float)
        cartesian[:, 0] = best_points[:, 1] + 0.5 * best_points[:, 2]
        cartesian[:, 1] = height * best_points[:, 2]
        return cartesian, exact_minimum

    points, min_area = find_best_placement(n)
    return points.tolist(), float(min_area)
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```
