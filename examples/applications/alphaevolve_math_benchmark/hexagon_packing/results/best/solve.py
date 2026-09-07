#!/usr/bin/env python3
"""Initial candidate for packing 11 unit hexagons in a regular hexagon."""

import json


# EVOLVE_START
def optimize_construct():
    """Construct and verify a compact packing of eleven unit hexagons."""
    import math

    try:
        from scipy.optimize import linprog
    except Exception:
        linprog = None

    n = 11
    eps = 3.0e-5
    sqrt3 = math.sqrt(3.0)
    apothem = sqrt3 / 2.0

    def vertices(cx, cy, angle):
        a = math.radians(angle)
        return [
            (
                cx + math.cos(a + k * math.pi / 3.0),
                cy + math.sin(a + k * math.pi / 3.0),
            )
            for k in range(6)
        ]

    def normals(angle):
        a = math.radians(angle) + math.pi / 6.0
        return [
            (math.cos(a + k * math.pi / 3.0),
             math.sin(a + k * math.pi / 3.0))
            for k in range(6)
        ]

    def support(angle, axis):
        a = math.radians(angle)
        return max(
            math.cos(a + k * math.pi / 3.0) * axis[0]
            + math.sin(a + k * math.pi / 3.0) * axis[1]
            for k in range(6)
        )

    def active_axis(dx, dy, ai, aj):
        choices = normals(ai) + normals(aj)
        axis = max(choices, key=lambda q: abs(q[0] * dx + q[1] * dy))
        if axis[0] * dx + axis[1] * dy < 0.0:
            return (-axis[0], -axis[1])
        return axis

    def row_seed(profile, reflected=False):
        """Create a useful staggered starting point for a row profile."""
        sx = sqrt3 + eps
        sy = 1.5 + eps
        height = (len(profile) - 1) * sy
        points = []
        for r, count in enumerate(profile):
            y = r * sy - height / 2.0
            xs = [(k - (count - 1) / 2.0) * sx for k in range(count)]
            if reflected:
                xs.reverse()
            points.extend((x, y) for x in xs)
        return points

    def expand_angles(profile, row_angles):
        result = []
        for angle, count in zip(row_angles, profile):
            result.extend([float(angle)] * count)
        return result

    def solve(seed, angles, outer_angle):
        """Solve the current active-axis linear relaxation."""
        if linprog is None:
            return None

        ox = 2 * n
        oy = ox + 1
        si = oy + 1
        dim = si + 1

        objective = [0.0] * dim
        objective[si] = 1.0
        centers = list(seed)

        pair_axes = {}
        for i in range(n):
            for j in range(i + 1, n):
                dx = centers[j][0] - centers[i][0]
                dy = centers[j][1] - centers[i][1]
                pair_axes[(i, j)] = active_axis(
                    dx, dy, angles[i], angles[j]
                )

        result = None
        for _ in range(10):
            aub = []
            bub = []

            for i in range(n):
                ix, iy = 2 * i, 2 * i + 1
                for q in normals(outer_angle):
                    row = [0.0] * dim
                    row[ix] = q[0]
                    row[iy] = q[1]
                    row[ox] = -q[0]
                    row[oy] = -q[1]
                    row[si] = -apothem
                    aub.append(row)
                    bub.append(-support(angles[i], q))

            for i in range(n):
                for j in range(i + 1, n):
                    q = pair_axes[(i, j)]
                    row = [0.0] * dim
                    row[2 * i] = q[0]
                    row[2 * i + 1] = q[1]
                    row[2 * j] = -q[0]
                    row[2 * j + 1] = -q[1]
                    required = (
                        support(angles[i], q)
                        + support(angles[j], (-q[0], -q[1]))
                        + eps
                    )
                    aub.append(row)
                    bub.append(-required)

            result = linprog(
                objective,
                A_ub=aub,
                b_ub=bub,
                bounds=[(None, None)] * (2 * n + 2) + [(0.1, None)],
                method="highs",
            )
            if not result.success:
                return None

            values = result.x
            centers = [
                (float(values[2 * i]), float(values[2 * i + 1]))
                for i in range(n)
            ]

            changed = False
            for i in range(n):
                for j in range(i + 1, n):
                    dx = centers[j][0] - centers[i][0]
                    dy = centers[j][1] - centers[i][1]
                    q = active_axis(dx, dy, angles[i], angles[j])
                    if q != pair_axes[(i, j)]:
                        pair_axes[(i, j)] = q
                        changed = True
            if not changed:
                break

        if result is None:
            return None
        return (
            centers,
            (float(result.x[ox]), float(result.x[oy])),
            float(result.x[si]),
        )

    def valid(centers, center, side, outer_angle, angles):
        """Use strict separating-axis checks before retaining a candidate."""
        cx, cy = center
        for (x, y), angle in zip(centers, angles):
            for q in normals(outer_angle):
                if (
                    q[0] * (x - cx) + q[1] * (y - cy)
                    + support(angle, q)
                    > apothem * side - 1.0e-7
                ):
                    return False

        for i in range(n):
            for j in range(i + 1, n):
                dx = centers[j][0] - centers[i][0]
                dy = centers[j][1] - centers[i][1]
                separated = False
                for q in normals(angles[i]) + normals(angles[j]):
                    required = (
                        support(angles[i], q)
                        + support(angles[j], (-q[0], -q[1]))
                        + 1.0e-7
                    )
                    if abs(q[0] * dx + q[1] * dy) > required:
                        separated = True
                        break
                if not separated:
                    return False
        return True

    def attempt(seed, angles, outer_angle):
        answer = solve(seed, angles, outer_angle)
        if answer is None:
            return None
        centers, center, side = answer
        side += 8.0e-5
        if not valid(centers, center, side, outer_angle, angles):
            return None
        return (centers, center, side, float(outer_angle), list(angles))

    # These profiles cover the principal three-row and mixed-width basins.
    profiles = [
        (4, 3, 4), (3, 4, 4), (4, 4, 3),
        (5, 3, 3), (3, 3, 5), (3, 5, 3),
    ]
    row_profiles = {
        3: (
            (0.0, 0.0, 0.0),
            (15.0, 15.0, 15.0),
            (30.0, 30.0, 30.0),
            (0.0, 30.0, 0.0),
            (30.0, 0.0, 30.0),
            (15.0, 45.0, 15.0),
            (45.0, 15.0, 45.0),
        )
    }

    beam = []
    for profile in profiles:
        for reflected in (False, True):
            seed = row_seed(profile, reflected)
            for base in (0.0, 15.0, 30.0):
                for rp in row_profiles[3]:
                    row_angles = [base + x for x in rp]
                    angles = expand_angles(profile, row_angles)
                    for outer in (0.0, 10.0, 20.0, 30.0, 40.0, 50.0):
                        candidate = attempt(seed, angles, outer)
                        if candidate is not None:
                            beam.append(candidate)

    beam.sort(key=lambda item: item[2])
    beam = beam[:4]

    # Alternating outer-angle and row-orientation coordinate descent.
    refined = []
    for candidate in beam:
        centers, center, side, outer, angles = candidate
        profile_index = 0
        for profile in profiles:
            if sum(profile) == len(angles):
                # Row boundaries are determined by the selected profile.
                profile_index = profile
                break
        if not profile_index:
            refined.append(candidate)
            continue

        row_angles = []
        pos = 0
        for count in profile_index:
            row_angles.append(float(angles[pos]))
            pos += count

        current = candidate
        for pass_no in range(3):
            best_local = current

            outer_step = 0.25 if pass_no else 0.5
            for k in range(-12, 13):
                trial_outer = outer + k * outer_step
                trial = attempt(
                    centers,
                    expand_angles(profile_index, row_angles),
                    trial_outer,
                )
                if trial is not None and trial[2] < best_local[2]:
                    best_local = trial

            centers, center, side, outer, angles = best_local
            current = best_local

            steps = (2.0, 1.0, 0.5) if pass_no == 0 else (0.25, 0.1)
            for step in steps:
                improved = True
                while improved:
                    improved = False
                    for r in range(len(row_angles)):
                        winner = current
                        for delta in (-step, step):
                            trial_rows = list(row_angles)
                            trial_rows[r] += delta
                            trial = attempt(
                                centers,
                                expand_angles(profile_index, trial_rows),
                                outer,
                            )
                            if trial is not None and trial[2] < winner[2]:
                                winner = trial
                                winning_rows = trial_rows
                        if winner is not current:
                            row_angles = winning_rows
                            centers, center, side, outer, angles = winner
                            current = winner
                            improved = True

            # A fine outer-angle sweep after every orientation pass.
            centers, center, side, outer, angles = current
            winner = current
            for k in range(-12, 13):
                trial = attempt(
                    centers,
                    expand_angles(profile_index, row_angles),
                    outer + 0.25 * k,
                )
                if trial is not None and trial[2] < winner[2]:
                    winner = trial
            current = winner
            centers, center, side, outer, angles = current

        refined.append(current)

    if refined:
        best = min(refined, key=lambda item: item[2])
    elif beam:
        best = beam[0]
    else:
        # Deterministic fallback for environments without scipy.
        profile = (4, 3, 4)
        centers = row_seed(profile)
        angles = [0.0] * n
        best = (
            centers,
            (0.0, 0.0),
            6.0,
            0.0,
            angles,
        )

    centers, outer_center, side, outer_angle, angles = best
    ocx, ocy = outer_center

    # Translate everything together so all reported coordinates are positive.
    points = []
    for (x, y), angle in zip(centers, angles):
        points.extend(vertices(x, y, angle))
    a = math.radians(outer_angle)
    points.extend(
        (
            ocx + side * math.cos(a + k * math.pi / 3.0),
            ocy + side * math.sin(a + k * math.pi / 3.0),
        )
        for k in range(6)
    )
    points.append((ocx, ocy))

    offset = max(
        0.1,
        -min(min(x for x, _ in points), min(y for _, y in points)) + 0.1,
    )

    inner = [
        [float(x + offset), float(y + offset), float(angle)]
        for (x, y), angle in zip(centers, angles)
    ]
    return (
        inner,
        [float(ocx + offset), float(ocy + offset)],
        float(side),
        float(outer_angle),
    )
# EVOLVE_END


if __name__ == "__main__":
    inner, center, side, angle = optimize_construct()
    print(json.dumps({
        "inner_hexagons": inner,
        "outer_center": center,
        "outer_side_length": side,
        "outer_angle_degrees": angle,
    }))
