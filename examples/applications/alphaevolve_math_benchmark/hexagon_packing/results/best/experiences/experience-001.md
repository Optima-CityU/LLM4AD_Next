Reusable design pattern for tightly constrained convex-packing problems where feasibility must be preserved during optimization.

- Iterative SAT-Separating LP with Topology and Orientation Multistart: The method initializes multiple staggered packing topologies, including 4-3-4, 3-4-4, 4-4-3, 5-3-3, and 3-5-3, and tests inner and outer orientations from 0 to 60 degrees before optimizing, which broadens structural coverage while exploiting hexagonal symmetry.
- Iterative SAT-Separating LP with Topology and Orientation Multistart: With orientations fixed, the method expresses outer containment through supporting half-planes and pairwise non-overlap through separating-axis constraints, then repeatedly recomputes the selected axis for every pair and resolves the linear program for approximately 6 to 10 rounds so centers can deform away from rigid lattice seeds.
- Iterative SAT-Separating LP with Topology and Orientation Multistart: The method uses a clearance of roughly 2e-5 to 5e-5, adds a small final side-length safety margin, and directly validates retained candidates with the supplied geometric verifier because contact within EPSILON is treated as intersection.
- Iterative SAT-Separating LP with Topology and Orientation Multistart: The recorded implementation achieved an outer hexagon side length of 3.9438433665420427, validity 1.0, evaluation time 3.557796749053523 seconds, and target ratio 0.9967434389887285, with no reported error.

```python
#!/usr/bin/env python3
"""Initial candidate for packing 11 unit hexagons in a regular hexagon."""

import json


# EVOLVE_START
def optimize_construct():
    import math

    clearance = 5.0e-5
    root3 = math.sqrt(3.0)
    apothem_factor = math.cos(math.pi / 6.0)

    def vertices(cx, cy, angle):
        a = math.radians(angle)
        return [
            (cx + math.cos(a + k * math.pi / 3.0),
             cy + math.sin(a + k * math.pi / 3.0))
            for k in range(6)
        ]

    def support(angle, nx, ny):
        a = math.radians(angle)
        return max(
            math.cos(a + k * math.pi / 3.0) * nx
            + math.sin(a + k * math.pi / 3.0) * ny
            for k in range(6)
        )

    def polygon_disjoint(p, q):
        axes = []
        for poly in (p, q):
            for i in range(6):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % 6]
                dx, dy = x2 - x1, y2 - y1
                length = math.hypot(dx, dy)
                axes.append((-dy / length, dx / length))

        for ax, ay in axes:
            pmin = min(x * ax + y * ay for x, y in p)
            pmax = max(x * ax + y * ay for x, y in p)
            qmin = min(x * ax + y * ay for x, y in q)
            qmax = max(x * ax + y * ay for x, y in q)
            if pmax < qmin - 1.0e-8 or qmax < pmin - 1.0e-8:
                return True
        return False

    def contained(poly, center, side, angle):
        a = math.radians(angle)
        for k in range(6):
            nx = math.cos(a + math.pi / 6.0 + k * math.pi / 3.0)
            ny = math.sin(a + math.pi / 6.0 + k * math.pi / 3.0)
            limit = side * apothem_factor
            for x, y in poly:
                if (x - center[0]) * nx + (y - center[1]) * ny > limit + 1.0e-8:
                    return False
        return True

    def valid(centers, orientations, outer_center, side, outer_angle):
        polys = [
            vertices(x, y, angle)
            for (x, y), angle in zip(centers, orientations)
        ]
        for i in range(len(polys)):
            if not contained(polys[i], outer_center, side, outer_angle):
                return False
            for j in range(i):
                if not polygon_disjoint(polys[i], polys[j]):
                    return False
        return True

    def seed_positions(pattern):
        positions = []
        row_count = len(pattern)
        for row, count in enumerate(pattern):
            y = (row - (row_count - 1) / 2.0) * root3 * 1.015
            offset = 1.0 if row % 2 else 0.0
            for col in range(count):
                x = (col - (count - 1) / 2.0) * 2.015 + offset
                positions.append((x, y))
        return positions

    def fallback():
        positions = seed_positions((4, 3, 4))
        # This deliberately spacious fallback is valid even without scipy.
        side = 5.25
        shift = 8.0
        return (
            [[x + shift, y + shift, 0.0] for x, y in positions],
            [shift, shift],
            side,
            0.0,
        )

    try:
        from scipy.optimize import linprog
    except Exception:
        return fallback()

    patterns = ((4, 3, 4), (3, 4, 4), (4, 4, 3), (5, 3, 3), (3, 5, 3))
    best = None
    n = 11
    side_angles = (0.0, 15.0, 30.0, 45.0)

    for pattern in patterns:
        base = seed_positions(pattern)
        for mode in range(4):
            if mode == 0:
                orientations = [0.0] * n
            elif mode == 1:
                orientations = [
                    30.0 if (r % 2) else 0.0
                    for r, count in enumerate(pattern)
                    for _ in range(count)
                ]
            elif mode == 2:
                orientations = [
                    30.0 if ((r + c) % 2) else 0.0
                    for r, count in enumerate(pattern)
                    for c in range(count)
                ]
            else:
                orientations = [
                    15.0 if (r % 2) else 0.0
                    for r, count in enumerate(pattern)
                    for _ in range(count)
                ]

            for outer_angle in side_angles:
                centers = list(base)
                result = None

                for _ in range(9):
                    # Variables are x/y for each inner center, outer x/y, and S.
                    ox = 2 * n
                    oy = ox + 1
                    sv = oy + 1
                    dimension = sv + 1
                    objective = [0.0] * dimension
                    objective[sv] = 1.0
                    aub = []
                    bub = []

                    normals = [
                        (
                            math.cos(math.radians(outer_angle + 30.0 + 60.0 * k)),
                            math.sin(math.radians(outer_angle + 30.0 + 60.0 * k)),
                        )
                        for k in range(6)
                    ]

                    # Six supporting half-planes of the enclosing hexagon.
                    for i in range(n):
                        for nx, ny in normals:
                            row = [0.0] * dimension
                            row[2 * i] = nx
                            row[2 * i + 1] = ny
                            row[ox] = -nx
                            row[oy] = -ny
                            row[sv] = -apothem_factor
                            aub.append(row)
                            bub.append(-support(orientations[i], nx, ny))

                    # Select one separating side-normal for every pair.
                    for i in range(n):
                        for j in range(i + 1, n):
                            dx = centers[j][0] - centers[i][0]
                            dy = centers[j][1] - centers[i][1]
                            candidate_axes = []
                            for angle in (orientations[i], orientations[j]):
                                a = math.radians(angle)
                                for k in range(6):
                                    t = a + math.pi / 6.0 + k * math.pi / 3.0
                                    candidate_axes.append((math.cos(t), math.sin(t)))

                            nx, ny = max(
                                candidate_axes,
                                key=lambda u: abs(u[0] * dx + u[1] * dy)
                            )
                            if nx * dx + ny * dy < 0.0:
                                nx, ny = -nx, -ny

                            rhs = (
                                support(orientations[i], nx, ny)
                                + support(orientations[j], nx, ny)
                                + clearance
                            )
                            row = [0.0] * dimension
                            row[2 * i] = nx
                            row[2 * i + 1] = ny
                            row[2 * j] = -nx
                            row[2 * j + 1] = -ny
                            aub.append(row)
                            bub.append(-rhs)

                    bounds = [(None, None)] * dimension
                    bounds[sv] = (0.0, None)
                    lp = linprog(
                        objective,
                        A_ub=aub,
                        b_ub=bub,
                        bounds=bounds,
                        method="highs",
                    )
                    if not lp.success:
                        result = None
                        break

                    result = lp.x
                    centers = [
                        (float(result[2 * i]), float(result[2 * i + 1]))
                        for i in range(n)
                    ]

                if result is None:
                    continue

                outer_center = (float(result[ox]), float(result[oy]))
                side = float(result[sv]) + 2.0e-5
                if not valid(centers, orientations, outer_center, side, outer_angle):
                    continue

                if best is None or side < best[0]:
                    best = (side, centers, orientations, outer_center, outer_angle)

    if best is None:
        return fallback()

    side, centers, orientations, outer_center, outer_angle = best

    # Apply one common positive translation, preserving all geometry.
    minimum = min(
        [outer_center[0], outer_center[1]]
        + [v for c in centers for v in c]
    )
    shift = max(1.0, 1.0 - minimum)
    translated_centers = [
        [float(x + shift), float(y + shift), float(a)]
        for (x, y), a in zip(centers, orientations)
    ]

    return (
        translated_centers,
        [float(outer_center[0] + shift), float(outer_center[1] + shift)],
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

```
