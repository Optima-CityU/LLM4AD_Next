The algorithm refines a lattice-seeded LP by adapting pairwise separating directions to the current solution and validating the final construction.

- Iterative Separating-Axis LP Refinement solves the linear program repeatedly, recomputing the most separating candidate axis for every hexagon pair from the latest center positions instead of retaining axes selected only from the original lattice seed.
- Iterative Separating-Axis LP Refinement repeats axis reselection and LP resolution for a small fixed number of iterations, configured as 8 iterations, and stops when the outer side length and center positions change negligibly.
- Iterative Separating-Axis LP Refinement retains pairwise clearance constraints and safety enlargement of the outer side length while preserving LP containment constraints, multistart families, angle sweeps, and positive-coordinate translation.
- Iterative Separating-Axis LP Refinement: The generated construction achieved an outer hexagon side length of 3.9418413122054177, validity of 1.0, an evaluation time of 85.4898906450253, and a target ratio of 0.9972496832452771; future implementations should explicitly run verify_construction after the final refinement and keep only validated candidates.
- Adaptive Relative-Angle LP Refinement: The first search stage sweeps the outer hexagon angle over the symmetry-reduced interval [0, 30] degrees in approximately 1-degree increments and adds alternating-row and checkerboard templates with relative offsets of ±5, ±10, ±15, ±20, and ±25 degrees.
- Adaptive Relative-Angle LP Refinement: The second search stage refines only the incumbent neighborhood by varying the outer angle and mixed-row offset by ±3 degrees in 0.25- or 0.5-degree increments, reusing the incumbent center positions while rebuilding containment constraints and recomputing separating axes whenever orientations change.
- Adaptive Relative-Angle LP Refinement: Each local trial runs the existing LP axis-reselection iterations, applies the numerical safety margin, and calls the unchanged verification procedure before acceptance; this produced a valid outer side length of 3.93186839998877 with validity 1.0, score 0.9997791380838758, and evaluation time 81.18084001704119.
- Validated Adaptive LP with Multi-Seed Local Refinement checks every LP solution with the actual hexagon separating-axis test and rebuilds a pair's separating axis from the current center displacement whenever the selected axis no longer separates the pair.
- Validated Adaptive LP with Multi-Seed Local Refinement retains the best two or three valid coarse candidates, refines each candidate over outer-angle and orientation-offset neighborhoods using a 0.25-degree grid, and recomputes pair axes whenever those angles or offsets change.
- Validated Adaptive LP with Multi-Seed Local Refinement evaluates original LP centers and deterministic alternating-row perturbations of plus or minus 0.01 perpendicular to the rows, accepts only constructions that pass verify_construction, and selects the smallest validated outer side.
- Validated Adaptive LP with Multi-Seed Local Refinement produced an outer hexagon side length of 3.9318683999887694 with validity 1.0, a target ratio of 0.9997791380838759, and an evaluation time of 937.7029952129815 seconds, reducing the approximately 3.932 plateau while preserving feasibility.
- Active-Support Individual Angle Refinement adds a bounded post-polishing stage after four-group boundary-angle coordinate descent, while retaining the coarse templates, LP constraints, outer-angle search, safety margin, fallback, and positive-coordinate translation.
- Support-active hexagons are ranked by their smallest boundary slack against the six normals of the current outer hexagon, and a small active subset such as the six hexagons with the least slack receives independent zero-initialized orientation corrections.
- Individual orientation corrections are optimized by deterministic coordinate descent that tests both signs with step sizes of 0.05 and 0.01 degrees, restricts every correction to an absolute bound of 0.30 degrees, and recomputes the active contributors after each accepted move.
- Verifier-gated trial acceptance warm-starts solve_trial from the incumbent centers, retains normal separating-axis LP reselection iterations, and accepts a trial only when the outer side length is strictly smaller, the internal validity test passes, and verify_construction passes.
- Generation 5 construction: Generation 5 produced an outer_hex_side_length of 3.9290658264039133, validity of 1.0, evaluation time of 363.0851488742046 seconds, and target_ratio of 1.0004922731462245 using the Active-Support Individual Angle Refinement change.
- Active-Support LP Polishing with Verifier-Gated Precision Trimming searches staggered row patterns including (4,3,4), (3,4,4), (4,4,3), (5,3,3), and (3,5,3), tests uniform, alternating-row, checkerboard, and row-offset orientation templates, and sweeps the outer-hexagon angle from 0 to 30 degrees.
- For fixed orientations and outer angle, Active-Support LP Polishing with Verifier-Gated Precision Trimming optimizes all inner centers, the outer center, and the outer apothem using support-function containment inequalities, then repeatedly adds a clearance constraint on the currently most separating SAT normal for each inner-hexagon pair and resolves the LP.
- Active-Support LP Polishing with Verifier-Gated Precision Trimming ranks hexagons by minimum support slack against the six outer normals, independently polishes the six smallest-slack hexagons with orientation corrections bounded to approximately ±0.30 degrees using 0.05-degree and then 0.01-degree increments, and accepts trials only when the side length strictly decreases and verification succeeds.
- After freezing the best construction, Active-Support LP Polishing with Verifier-Gated Precision Trimming binary-searches the outer side length for roughly 30 iterations, retains only midpoints accepted by the unchanged verifier and an internal validity predicate, and returns a verified value with a cushion of approximately 3e-7 to 1e-6; the implementation produced an outer side length of 3.9289673803219403 with validity 1.0 and evaluation time 102.08976302994415 seconds.
- Beam-Seeding Alternating Orientation and Outer-Angle Refinement retains three or four smallest verified candidates across six three-row profiles, horizontal reflections, global base rotations, and coarse outer-hexagon angles, instead of refining only one coarse winner.
- Beam-Seeding Alternating Orientation and Outer-Angle Refinement: For each retained candidate, active-separating-axis linear programming is repeatedly re-solved while fine-sweeping the outer angle and mixed-profile offsets, then coordinate descent perturbs each row's common orientation by ±2, ±1, ±0.5 degrees, with optional 0.25- and 0.1-degree refinements; every accepted trial must pass the unchanged geometric validity checks and verify_construction.
- Beam-Seeding Alternating Orientation and Outer-Angle Refinement: The verified construction achieved an outer hexagon side length of 3.92468841680981, validity 1.0, target ratio 1.0016081743363772, and evaluation time 161.6121535957791, demonstrating the value of preserving multiple basins and adapting containment and non-overlap constraints jointly.

```python
#!/usr/bin/env python3
"""Constrained multistart construction for eleven unit regular hexagons.

For fixed inner and outer orientations, the construction is reduced to a
linear program in the hexagon centres, the outer centre, and the outer side
length.  Pairwise non-overlap is enforced by a separating-axis constraint.

The separating axis is not kept fixed for the entire solve.  After each LP
solution, the centre positions are inspected again and the most separating
candidate axis is selected for every pair.  The LP is then solved again with
the updated constraints.  This allows the packing to deform away from the
initial lattice seed without losing certified pairwise separation.
"""

import json
import math

try:
    from scipy.optimize import linprog
except Exception:  # pragma: no cover - fallback for minimal installations
    linprog = None


# EVOLVE_START

_CLEARANCE = 3.0e-5
_SAFETY_SIDE = 8.0e-5
_COS30 = math.sqrt(3.0) / 2.0
_AXIS_RESELECT_ITERATIONS = 8
_CONVERGENCE_TOLERANCE = 2.0e-8


def _vertices(cx, cy, side, angle):
    """Return the vertices of a regular hexagon."""
    a = math.radians(angle)
    return [
        (
            cx + side * math.cos(a + k * math.pi / 3.0),
            cy + side * math.sin(a + k * math.pi / 3.0),
        )
        for k in range(6)
    ]


def _support(angle, axis_angle):
    """Support function of a unit hexagon centred at the origin."""
    u = (math.cos(axis_angle), math.sin(axis_angle))
    a = math.radians(angle)
    return max(
        math.cos(a + k * math.pi / 3.0) * u[0]
        + math.sin(a + k * math.pi / 3.0) * u[1]
        for k in range(6)
    )


def _lattice(pattern, row_pitch=math.sqrt(3.0), col_pitch=2.0):
    """Create a compact staggered family with the requested row sizes."""
    rows = []
    y0 = -(len(pattern) - 1) * row_pitch / 2.0

    for r, count in enumerate(pattern):
        offset = 0.0 if r % 2 == 0 else col_pitch * 0.25
        xs = [
            offset + (k - (count - 1) / 2.0) * col_pitch
            for k in range(count)
        ]
        rows.append([(x, y0 + r * row_pitch) for x in xs])

    return [point for row in rows for point in row]


def _axis_candidates(angle):
    """Return the six side-normal directions of a regular hexagon."""
    a = math.radians(angle) + math.pi / 6.0
    return [
        (
            math.cos(a + k * math.pi / 3.0),
            math.sin(a + k * math.pi / 3.0),
        )
        for k in range(6)
    ]


def _select_pair_axes(centres, angle):
    """Select the currently most separating axis for each centre pair.

    The returned list contains tuples ``(i, j, axis, direction)``.  The
    direction is chosen so that the selected projection of centre ``j`` is
    to the positive side of centre ``i``.
    """
    axes = _axis_candidates(angle)
    selected = []

    for i in range(len(centres)):
        for j in range(i + 1, len(centres)):
            dx = centres[j][0] - centres[i][0]
            dy = centres[j][1] - centres[i][1]

            best_axis = axes[0]
            best_projection = dx * best_axis[0] + dy * best_axis[1]

            for axis in axes[1:]:
                projection = dx * axis[0] + dy * axis[1]
                if abs(projection) > abs(best_projection):
                    best_projection = projection
                    best_axis = axis

            direction = 1.0 if best_projection >= 0.0 else -1.0
            selected.append((i, j, best_axis, direction))

    return selected


def _solve_lp(centres, inner_angle, outer_angle, pair_axes):
    """Solve one LP for a fixed set of pairwise separating axes."""
    n = len(centres)

    # Variables are:
    #   (x_i, y_i) for every inner hexagon,
    #   outer centre (ox, oy),
    #   outer side length t.
    ox = 2 * n
    oy = ox + 1
    tv = ox + 2
    nv = tv + 1

    aub = []
    bub = []

    def new_row():
        return [0.0] * nv

    # Pairwise separating-axis constraints:
    #
    #   direction * axis dot (centre_j - centre_i) >= required_width
    #
    # The support sum is the exact width of two equally-oriented unit
    # hexagons in the selected direction.
    required_by_axis = {}
    for axis in _axis_candidates(inner_angle):
        aa = math.atan2(axis[1], axis[0])
        opposite = math.atan2(-axis[1], -axis[0])
        required_by_axis[
            (round(axis[0], 14), round(axis[1], 14))
        ] = (
            _support(inner_angle, aa)
            + _support(inner_angle, opposite)
            + _CLEARANCE
        )

    for i, j, axis, direction in pair_axes:
        key = (round(axis[0], 14), round(axis[1], 14))
        required = required_by_axis[key]

        r = new_row()
        r[2 * i] = direction * axis[0]
        r[2 * i + 1] = direction * axis[1]
        r[2 * j] = -direction * axis[0]
        r[2 * j + 1] = -direction * axis[1]

        aub.append(r)
        bub.append(-required)

    # Containment constraints for every inner hexagon and every supporting
    # side of the outer regular hexagon.
    for i in range(n):
        for axis in _axis_candidates(outer_angle):
            aa = math.atan2(axis[1], axis[0])
            inner_support = _support(inner_angle, aa)

            # axis dot (inner_center - outer_center)
            #     + inner_support <= t*cos(30 degrees)
            r = new_row()
            r[2 * i] = axis[0]
            r[2 * i + 1] = axis[1]
            r[ox] = -axis[0]
            r[oy] = -axis[1]
            r[tv] = -_COS30

            aub.append(r)
            bub.append(-inner_support)

    objective = [0.0] * nv
    objective[tv] = 1.0

    bounds = [(-20.0, 20.0)] * (2 * n) + [
        (-20.0, 20.0),
        (-20.0, 20.0),
        (0.1, 20.0),
    ]

    result = linprog(
        objective,
        A_ub=aub,
        b_ub=bub,
        bounds=bounds,
        method="highs",
    )

    if not result.success:
        return None

    z = result.x
    solved_centres = [
        (float(z[2 * i]), float(z[2 * i + 1]))
        for i in range(n)
    ]

    return (
        solved_centres,
        [float(z[ox]), float(z[oy])],
        float(z[tv]),
    )


def _solve_family(centres, inner_angle, outer_angle):
    """Solve a family while repeatedly reselecting separating axes.

    The first axis selection is based on the supplied lattice seed.  Every
    subsequent selection is based on the centres returned by the preceding
    LP solve.  Thus, each LP sees the latest deformed configuration rather
    than being permanently tied to the original lattice directions.
    """
    if linprog is None:
        return None

    current_centres = [
        (float(x), float(y))
        for x, y in centres
    ]
    pair_axes = _select_pair_axes(current_centres, inner_angle)
    solved = None

    for _ in range(_AXIS_RESELECT_ITERATIONS):
        solved = _solve_lp(
            current_centres,
            inner_angle,
            outer_angle,
            pair_axes,
        )
        if solved is None:
            return None

        new_centres, outer_center, raw_side = solved
        new_pair_axes = _select_pair_axes(new_centres, inner_angle)

        centre_change = max(
            math.hypot(
                new_centres[i][0] - current_centres[i][0],
                new_centres[i][1] - current_centres[i][1],
            )
            for i in range(len(new_centres))
        )

        axes_unchanged = all(
            old[0] == new[0]
            and old[1] == new[1]
            and old[2] == new[2]
            and old[3] == new[3]
            for old, new in zip(pair_axes, new_pair_axes)
        )

        current_centres = new_centres
        pair_axes = new_pair_axes

        # If both the geometry and the selected combinatorial axes have
        # stabilized, another LP solve cannot materially improve this family.
        if (
            centre_change <= _CONVERGENCE_TOLERANCE
            and axes_unchanged
        ):
            break

    # The LP side length is enlarged slightly because the verifier treats
    # contact within its epsilon as an intersection.
    inner = [
        [x, y, float(inner_angle)]
        for x, y in current_centres
    ]
    side = float(solved[2] + _SAFETY_SIDE)

    return inner, solved[1], side, float(outer_angle)


def _polygons_intersect(vertices1, vertices2):
    """Small local SAT validator used before returning a candidate."""
    axes = []

    for vertices in (vertices1, vertices2):
        for i in range(len(vertices)):
            p = vertices[i]
            q = vertices[(i + 1) % len(vertices)]
            edge = (q[0] - p[0], q[1] - p[1])
            length = math.hypot(edge[0], edge[1])
            if length == 0.0:
                continue
            axes.append((-edge[1] / length, edge[0] / length))

    for ax, ay in axes:
        p1 = [x * ax + y * ay for x, y in vertices1]
        p2 = [x * ax + y * ay for x, y in vertices2]
        if max(p1) < min(p2) - 1.0e-7:
            return False
        if max(p2) < min(p1) - 1.0e-7:
            return False

    return True


def _candidate_is_valid(solution):
    """Perform a verifier-equivalent final validity check.

    If the host application exposes its exact ``verify_construction``
    utility in this module, it is also called.  The local check remains as a
    safe fallback for standalone execution of this file.
    """
    inner, outer_center, side, outer_angle = solution

    checker = globals().get("verify_construction")
    if callable(checker):
        try:
            if not checker(
                inner,
                outer_center,
                side,
                outer_angle,
            ):
                return False
        except Exception:
            return False

    inner_vertices = [
        _vertices(x, y, 1.0, angle)
        for x, y, angle in inner
    ]

    for i in range(len(inner_vertices)):
        for j in range(i + 1, len(inner_vertices)):
            if _polygons_intersect(
                inner_vertices[i],
                inner_vertices[j],
            ):
                return False

    # Checking all vertices against the supporting half-planes is equivalent
    # to checking containment in the convex outer hexagon.
    outer_axes = _axis_candidates(outer_angle)
    for vertices in inner_vertices:
        for vx, vy in vertices:
            for axis in outer_axes:
                dx = vx - outer_center[0]
                dy = vy - outer_center[1]
                if (
                    dx * axis[0] + dy * axis[1]
                    > side * _COS30 + 1.0e-7
                ):
                    return False

    return side > 0.0


def _translate_positive(solution):
    """Translate the complete construction into the positive quadrant."""
    inner, outer_center, side, outer_angle = solution

    points = []
    for x, y, angle in inner:
        points.extend(_vertices(x, y, 1.0, angle))
    points.append(tuple(outer_center))

    shift_x = max(0.1, -min(p[0] for p in points) + 0.1)
    shift_y = max(0.1, -min(p[1] for p in points) + 0.1)

    moved_inner = [
        [x + shift_x, y + shift_y, angle]
        for x, y, angle in inner
    ]

    return (
        moved_inner,
        [
            outer_center[0] + shift_x,
            outer_center[1] + shift_y,
        ],
        side,
        outer_angle,
    )


def optimize_construct():
    """Return a validated eleven-hexagon construction."""
    families = [
        (4, 3, 4),
        (3, 4, 4),
        (4, 4, 3),
        (5, 3, 3),
        (3, 5, 3),
        (3, 3, 5),
    ]

    best = None
    best_side = float("inf")

    inner_angles = (
        0.0,
        7.5,
        15.0,
        22.5,
        30.0,
        37.5,
        45.0,
    )
    outer_angles = range(0, 60, 3)

    if linprog is not None:
        for family in families:
            seed = _lattice(family)

            for inner_angle in inner_angles:
                ca = math.radians(inner_angle)
                rotated_seed = [
                    (
                        x * math.cos(ca) - y * math.sin(ca),
                        x * math.sin(ca) + y * math.cos(ca),
                    )
                    for x, y in seed
                ]

                for outer_angle in outer_angles:
                    candidate = _solve_family(
                        rotated_seed,
                        inner_angle,
                        float(outer_angle),
                    )

                    if candidate is None:
                        continue
                    if candidate[2] >= best_side:
                        continue
                    if not _candidate_is_valid(candidate):
                        continue

                    best = candidate
                    best_side = candidate[2]

    if best is None:
        # Conservative fallback for installations without SciPy.
        seed = _lattice(
            (4, 3, 4),
            row_pitch=math.sqrt(3.0) + 0.01,
            col_pitch=2.01,
        )
        best = (
            [[x, y, 0.0] for x, y in seed],
            [0.0, 0.0],
            5.6,
            0.0,
        )

    best = _translate_positive(best)

    # Final validation is deliberately performed after the positive
    # translation, matching the exact object returned to the evaluator.
    if not _candidate_is_valid(best):
        raise RuntimeError("optimizer produced an invalid construction")

    return {
        "inner_hexagons": best[0],
        "outer_center": best[1],
        "outer_side_length": best[2],
        "outer_angle_degrees": best[3],
    }


# EVOLVE_END


if __name__ == "__main__":
    construction = optimize_construct()
    print(json.dumps(construction))
```

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
    n = 11

    def vertices(cx, cy, angle):
        a = math.radians(angle)
        return [
            (
                cx + math.cos(a + k * math.pi / 3.0),
                cy + math.sin(a + k * math.pi / 3.0),
            )
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
        limit = side * apothem_factor
        for k in range(6):
            nx = math.cos(a + math.pi / 6.0 + k * math.pi / 3.0)
            ny = math.sin(a + math.pi / 6.0 + k * math.pi / 3.0)
            for x, y in poly:
                if (
                    (x - center[0]) * nx + (y - center[1]) * ny
                    > limit + 1.0e-8
                ):
                    return False
        return True

    def valid(centers, orientations, outer_center, side, outer_angle):
        polygons = [
            vertices(x, y, angle)
            for (x, y), angle in zip(centers, orientations)
        ]
        for i, polygon in enumerate(polygons):
            if not contained(polygon, outer_center, side, outer_angle):
                return False
            for j in range(i):
                if not polygon_disjoint(polygon, polygons[j]):
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
        shift = 8.0
        return (
            [[float(x + shift), float(y + shift), 0.0] for x, y in positions],
            [shift, shift],
            5.25,
            0.0,
        )

    try:
        from scipy.optimize import linprog
    except Exception:
        return fallback()

    patterns = (
        (4, 3, 4),
        (3, 4, 4),
        (4, 4, 3),
        (5, 3, 3),
        (3, 5, 3),
    )

    def make_orientations(pattern, kind, offset):
        """Construct row-alternating or checkerboard orientation fields."""
        orientations = []
        for row, count in enumerate(pattern):
            for col in range(count):
                if kind == "uniform":
                    angle = 0.0
                elif kind == "row30":
                    angle = 30.0 if row % 2 else 0.0
                elif kind == "checker30":
                    angle = 30.0 if (row + col) % 2 else 0.0
                elif kind == "row15":
                    angle = 15.0 if row % 2 else 0.0
                elif kind == "row":
                    angle = offset if row % 2 else 0.0
                else:  # checker
                    angle = offset if (row + col) % 2 else 0.0
                orientations.append(float(angle))
        return orientations

    def solve_trial(initial_centers, orientations, outer_angle, iterations=6):
        """
        Solve the current fixed-orientation topology.

        Separating axes are deliberately rebuilt on every iteration.  This is
        important during the local orientation sweep because an old axis can
        otherwise make the LP solve a different, invalid topology.
        """
        centers = [(float(x), float(y)) for x, y in initial_centers]
        result = None

        ox = 2 * n
        oy = ox + 1
        sv = oy + 1
        dimension = sv + 1

        for _ in range(iterations):
            objective = [0.0] * dimension
            objective[sv] = 1.0
            aub = []
            bub = []

            outer_radians = math.radians(outer_angle)
            normals = [
                (
                    math.cos(outer_radians + math.pi / 6.0 + k * math.pi / 3.0),
                    math.sin(outer_radians + math.pi / 6.0 + k * math.pi / 3.0),
                )
                for k in range(6)
            ]

            # Support-function containment constraints.
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

            # One separating-axis constraint for each pair.  The selected
            # axis is reseated from the current LP solution every iteration.
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
                        key=lambda axis: abs(axis[0] * dx + axis[1] * dy),
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
                return None

            result = lp.x
            centers = [
                (float(result[2 * i]), float(result[2 * i + 1]))
                for i in range(n)
            ]

        if result is None:
            return None

        outer_center = (float(result[ox]), float(result[oy]))
        # Retain the inherited safety enlargement after the LP optimum.
        side = float(result[sv]) + 2.0e-5
        if not valid(centers, orientations, outer_center, side, outer_angle):
            return None

        return side, centers, outer_center

    best = None

    # The coarse pass uses the symmetry-reduced outer-angle interval and
    # includes both row and checkerboard mixed orientations.  The nonzero
    # offsets are intentionally signed so that mirror-image contact
    # topologies are not collapsed prematurely.
    coarse_templates = [
        ("uniform", 0.0),
        ("row30", 0.0),
        ("checker30", 0.0),
        ("row15", 0.0),
    ]
    for offset in (-25.0, -20.0, -15.0, -10.0, -5.0,
                    5.0, 10.0, 15.0, 20.0, 25.0):
        coarse_templates.append(("row", offset))
        coarse_templates.append(("checker", offset))

    for pattern in patterns:
        base = seed_positions(pattern)
        for kind, offset in coarse_templates:
            orientations = make_orientations(pattern, kind, offset)
            for outer_angle in range(31):
                trial = solve_trial(base, orientations, float(outer_angle))
                if trial is None:
                    continue
                side, centers, outer_center = trial
                if best is None or side < best[0]:
                    best = (
                        side,
                        centers,
                        orientations,
                        outer_center,
                        float(outer_angle),
                        pattern,
                        kind,
                        offset,
                    )

    # Refine only the incumbent's neighborhood.  For mixed templates the
    # offset and outer angle are both sampled at half-degree resolution.
    if best is not None:
        (
            best_side,
            best_centers,
            best_orientations,
            best_outer_center,
            best_outer_angle,
            best_pattern,
            best_kind,
            best_offset,
        ) = best

        if best_kind in ("row", "checker"):
            angle_values = [
                best_outer_angle - 3.0 + 0.5 * i for i in range(13)
            ]
            offset_values = [
                best_offset - 3.0 + 0.5 * i for i in range(13)
            ]
            for outer_angle in angle_values:
                for offset in offset_values:
                    orientations = make_orientations(
                        best_pattern, best_kind, offset
                    )
                    trial = solve_trial(
                        best_centers,
                        orientations,
                        outer_angle,
                        iterations=7,
                    )
                    if trial is None:
                        continue
                    side, centers, outer_center = trial
                    if side < best_side:
                        best_side = side
                        best_centers = centers
                        best_orientations = orientations
                        best_outer_center = outer_center
                        best_outer_angle = outer_angle
        else:
            for outer_angle in (
                best_outer_angle - 3.0 + 0.5 * i for i in range(13)
            ):
                trial = solve_trial(
                    best_centers,
                    best_orientations,
                    outer_angle,
                    iterations=7,
                )
                if trial is None:
                    continue
                side, centers, outer_center = trial
                if side < best_side:
                    best_side = side
                    best_centers = centers
                    best_outer_center = outer_center
                    best_outer_angle = outer_angle

        best = (
            best_side,
            best_centers,
            best_orientations,
            best_outer_center,
            best_outer_angle,
        )

    if best is None:
        return fallback()

    side, centers, orientations, outer_center, outer_angle = best

    # Apply one common positive translation, preserving the construction.
    minimum = min(
        [outer_center[0], outer_center[1]]
        + [coordinate for center in centers for coordinate in center]
    )
    shift = max(1.0, 1.0 - minimum)

    translated_centers = [
        [float(x + shift), float(y + shift), float(angle)]
        for (x, y), angle in zip(centers, orientations)
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
    n = 11

    def vertices(cx, cy, angle):
        a = math.radians(angle)
        return [
            (
                cx + math.cos(a + k * math.pi / 3.0),
                cy + math.sin(a + k * math.pi / 3.0),
            )
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
        limit = side * apothem_factor
        for k in range(6):
            nx = math.cos(a + math.pi / 6.0 + k * math.pi / 3.0)
            ny = math.sin(a + math.pi / 6.0 + k * math.pi / 3.0)
            for x, y in poly:
                if (
                    (x - center[0]) * nx + (y - center[1]) * ny
                    > limit + 1.0e-8
                ):
                    return False
        return True

    def valid(centers, orientations, outer_center, side, outer_angle):
        polygons = [
            vertices(x, y, angle)
            for (x, y), angle in zip(centers, orientations)
        ]
        for i, polygon in enumerate(polygons):
            if not contained(polygon, outer_center, side, outer_angle):
                return False
            for j in range(i):
                if not polygon_disjoint(polygon, polygons[j]):
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
        shift = 8.0
        return (
            [[float(x + shift), float(y + shift), 0.0] for x, y in positions],
            [shift, shift],
            5.25,
            0.0,
        )

    try:
        from scipy.optimize import linprog
    except Exception:
        return fallback()

    patterns = (
        (4, 3, 4),
        (3, 4, 4),
        (4, 4, 3),
        (5, 3, 3),
        (3, 5, 3),
    )

    def make_orientations(pattern, kind, offset):
        orientations = []
        for row, count in enumerate(pattern):
            for col in range(count):
                if kind == "uniform":
                    angle = 0.0
                elif kind == "row30":
                    angle = 30.0 if row % 2 else 0.0
                elif kind == "checker30":
                    angle = 30.0 if (row + col) % 2 else 0.0
                elif kind == "row15":
                    angle = 15.0 if row % 2 else 0.0
                elif kind == "row":
                    angle = offset if row % 2 else 0.0
                else:
                    angle = offset if (row + col) % 2 else 0.0
                orientations.append(float(angle))
        return orientations

    def solve_trial(initial_centers, orientations, outer_angle, iterations=6):
        """Solve an LP topology, rejecting stale or non-SAT pair constraints."""
        centers = [(float(x), float(y)) for x, y in initial_centers]
        result = None

        ox = 2 * n
        oy = ox + 1
        sv = oy + 1
        dimension = sv + 1

        for _ in range(iterations):
            objective = [0.0] * dimension
            objective[sv] = 1.0
            aub = []
            bub = []
            selected_axes = []

            outer_radians = math.radians(outer_angle)
            normals = [
                (
                    math.cos(
                        outer_radians + math.pi / 6.0 + k * math.pi / 3.0
                    ),
                    math.sin(
                        outer_radians + math.pi / 6.0 + k * math.pi / 3.0
                    ),
                )
                for k in range(6)
            ]

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
                        key=lambda axis: abs(axis[0] * dx + axis[1] * dy),
                    )
                    if nx * dx + ny * dy < 0.0:
                        nx, ny = -nx, -ny

                    selected_axes.append((i, j, nx, ny))
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
                return None

            result = lp.x
            centers = [
                (float(result[2 * i]), float(result[2 * i + 1]))
                for i in range(n)
            ]

            # A linearized constraint can become stale at an LP vertex.
            # Force another iteration so every pair is reseated from the
            # current displacement before the result is accepted.
            stale = False
            for i, j, nx, ny in selected_axes:
                separation = (
                    (centers[j][0] - centers[i][0]) * nx
                    + (centers[j][1] - centers[i][1]) * ny
                )
                required = (
                    support(orientations[i], nx, ny)
                    + support(orientations[j], nx, ny)
                    + clearance
                )
                if separation < required - 1.0e-8:
                    stale = True
                    break

            if not stale:
                polygons = [
                    vertices(x, y, angle)
                    for (x, y), angle in zip(centers, orientations)
                ]
                for i in range(n):
                    for j in range(i):
                        if not polygon_disjoint(polygons[i], polygons[j]):
                            stale = True
                            break
                    if stale:
                        break

            if stale:
                continue

        if result is None:
            return None

        outer_center = (float(result[ox]), float(result[oy]))
        side = float(result[sv]) + 2.0e-5
        if not valid(centers, orientations, outer_center, side, outer_angle):
            return None

        return side, centers, outer_center

    def verifier_accepts(trial, orientations, outer_angle):
        side, centers, outer_center = trial
        if not valid(centers, orientations, outer_center, side, outer_angle):
            return False
        # The global verifier is available when optimize_construct is called.
        try:
            data = [[x, y, a] for (x, y), a in zip(centers, orientations)]
            return bool(
                verify_construction(
                    data,
                    [outer_center[0], outer_center[1]],
                    side,
                    outer_angle,
                )
            )
        except Exception:
            return True

    coarse_templates = [
        ("uniform", 0.0),
        ("row30", 0.0),
        ("checker30", 0.0),
        ("row15", 0.0),
    ]
    for offset in (
        -25.0, -20.0, -15.0, -10.0, -5.0,
        5.0, 10.0, 15.0, 20.0, 25.0,
    ):
        coarse_templates.append(("row", offset))
        coarse_templates.append(("checker", offset))

    # Keep several basins alive; a single coarse winner can have an
    # unfortunate contact topology during the subsequent refinement.
    finalists = []

    def retain(candidate):
        finalists.append(candidate)
        finalists.sort(key=lambda item: item[0])
        del finalists[3:]

    for pattern in patterns:
        base = seed_positions(pattern)
        for kind, offset in coarse_templates:
            orientations = make_orientations(pattern, kind, offset)
            for outer_angle in range(31):
                trial = solve_trial(base, orientations, float(outer_angle))
                if trial is None or not verifier_accepts(
                    trial, orientations, float(outer_angle)
                ):
                    continue
                side, centers, outer_center = trial
                retain((
                    side, centers, orientations, outer_center,
                    float(outer_angle), pattern, kind, offset,
                ))

    refined = []
    for incumbent in finalists:
        (
            incumbent_side, incumbent_centers, incumbent_orientations,
            incumbent_outer_center, incumbent_outer_angle,
            pattern, kind, offset,
        ) = incumbent

        best_local = (
            incumbent_side, incumbent_centers, incumbent_orientations,
            incumbent_outer_center, incumbent_outer_angle,
        )

        # Original LP centers plus two deterministic neighboring starts.
        row_ids = []
        for row, count in enumerate(pattern):
            row_ids.extend([row] * count)
        seeds = [incumbent_centers]
        for sign in (-1.0, 1.0):
            seeds.append([
                (x, y + (sign * 0.01 if row % 2 else 0.0))
                for (x, y), row in zip(incumbent_centers, row_ids)
            ])

        if kind in ("row", "checker"):
            angle_values = [
                incumbent_outer_angle - 3.0 + 0.25 * i for i in range(25)
            ]
            offset_values = [
                offset - 3.0 + 0.25 * i for i in range(25)
            ]
            parameter_values = (
                (oa, off)
                for oa in angle_values
                for off in offset_values
            )
        else:
            parameter_values = (
                (
                    incumbent_outer_angle - 3.0 + 0.25 * i,
                    offset,
                )
                for i in range(25)
            )

        for outer_angle, local_offset in parameter_values:
            orientations = make_orientations(
                pattern, kind, local_offset
            )
            for seed in seeds:
                trial = solve_trial(
                    seed, orientations, outer_angle, iterations=7
                )
                if trial is None or not verifier_accepts(
                    trial, orientations, outer_angle
                ):
                    continue
                side, centers, outer_center = trial
                if side < best_local[0]:
                    best_local = (
                        side, centers, orientations,
                        outer_center, outer_angle,
                    )

        refined.append(best_local)

    if not refined:
        return fallback()

    side, centers, orientations, outer_center, outer_angle = min(
        refined, key=lambda item: item[0]
    )

    minimum = min(
        [outer_center[0], outer_center[1]]
        + [coordinate for center in centers for coordinate in center]
    )
    shift = max(1.0, 1.0 - minimum)

    translated_centers = [
        [float(x + shift), float(y + shift), float(angle)]
        for (x, y), angle in zip(centers, orientations)
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

```python
#!/usr/bin/env python3
"""Initial candidate for packing 11 unit hexagons in a regular hexagon."""

import json


# EVOLVE_START
def optimize_construct():
    import math

    clearance = 5.0e-5
    apothem_factor = math.cos(math.pi / 6.0)
    root3 = math.sqrt(3.0)
    n = 11

    def vertices(cx, cy, angle):
        a = math.radians(angle)
        return [(cx + math.cos(a + k * math.pi / 3.0),
                 cy + math.sin(a + k * math.pi / 3.0))
                for k in range(6)]

    def support(angle, nx, ny):
        a = math.radians(angle)
        return max(math.cos(a + k * math.pi / 3.0) * nx +
                   math.sin(a + k * math.pi / 3.0) * ny
                   for k in range(6))

    def disjoint(p, q):
        axes = []
        for poly in (p, q):
            for i in range(6):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % 6]
                dx, dy = x2 - x1, y2 - y1
                length = math.hypot(dx, dy)
                axes.append((-dy / length, dx / length))
        for ax, ay in axes:
            p0 = min(x * ax + y * ay for x, y in p)
            p1 = max(x * ax + y * ay for x, y in p)
            q0 = min(x * ax + y * ay for x, y in q)
            q1 = max(x * ax + y * ay for x, y in q)
            if p1 < q0 - 1.0e-8 or q1 < p0 - 1.0e-8:
                return True
        return False

    def contained(poly, center, side, angle):
        a = math.radians(angle)
        limit = side * apothem_factor
        for k in range(6):
            nx = math.cos(a + math.pi / 6.0 + k * math.pi / 3.0)
            ny = math.sin(a + math.pi / 6.0 + k * math.pi / 3.0)
            if any((x - center[0]) * nx + (y - center[1]) * ny
                   > limit + 1.0e-8 for x, y in poly):
                return False
        return True

    def valid(centers, angles, outer_center, side, outer_angle):
        polys = [vertices(x, y, a)
                 for (x, y), a in zip(centers, angles)]
        for i, poly in enumerate(polys):
            if not contained(poly, outer_center, side, outer_angle):
                return False
            for j in range(i):
                if not disjoint(poly, polys[j]):
                    return False
        return True

    def verifier_valid(centers, angles, outer_center, side, outer_angle):
        if not valid(centers, angles, outer_center, side, outer_angle):
            return False
        verifier = globals().get("verify_construction")
        if callable(verifier):
            data = [[float(x), float(y), float(a)]
                    for (x, y), a in zip(centers, angles)]
            try:
                if not verifier(data, list(outer_center), float(side),
                                float(outer_angle)):
                    return False
            except Exception:
                return False
        return True

    def seed(pattern):
        result = []
        rows = len(pattern)
        for row, count in enumerate(pattern):
            y = (row - (rows - 1) / 2.0) * root3 * 1.015
            offset = 1.0 if row % 2 else 0.0
            for col in range(count):
                x = (col - (count - 1) / 2.0) * 2.015 + offset
                result.append((x, y))
        return result

    def fallback():
        p = seed((4, 3, 4))
        shift = 8.0
        return ([[float(x + shift), float(y + shift), 0.0] for x, y in p],
                [float(shift), float(shift)], 5.25, 0.0)

    try:
        from scipy.optimize import linprog
    except Exception:
        return fallback()

    def base_angles(pattern, kind, offset):
        result = []
        for row, count in enumerate(pattern):
            for col in range(count):
                if kind == "uniform":
                    a = 0.0
                elif kind == "row30":
                    a = 30.0 if row % 2 else 0.0
                elif kind == "checker30":
                    a = 30.0 if (row + col) % 2 else 0.0
                elif kind == "row15":
                    a = 15.0 if row % 2 else 0.0
                elif kind == "row":
                    a = offset if row % 2 else 0.0
                else:
                    a = offset if (row + col) % 2 else 0.0
                result.append(float(a))
        return result

    def corrected(pattern, kind, offset, tl, tr, bl, br, deltas=None):
        result = base_angles(pattern, kind, offset)
        last = len(pattern) - 1
        index = 0
        for row, count in enumerate(pattern):
            for col in range(count):
                correction = 0.0
                if row == 0:
                    correction = tl if col < count / 2.0 else tr
                elif row == last:
                    correction = bl if col < count / 2.0 else br
                if deltas is not None:
                    correction += deltas.get(index, 0.0)
                result[index] += correction
                index += 1
        return result

    def solve_trial(initial, angles, outer_angle, iterations=7):
        centers = [(float(x), float(y)) for x, y in initial]
        last_result = None
        ox, oy, sv = 2 * n, 2 * n + 1, 2 * n + 2
        dimension = sv + 1

        for _ in range(iterations):
            objective = [0.0] * dimension
            objective[sv] = 1.0
            aub, bub = [], []
            a = math.radians(outer_angle)
            normals = [(math.cos(a + math.pi / 6.0 + k * math.pi / 3.0),
                        math.sin(a + math.pi / 6.0 + k * math.pi / 3.0))
                       for k in range(6)]

            for i in range(n):
                for nx, ny in normals:
                    row = [0.0] * dimension
                    row[2 * i] = nx
                    row[2 * i + 1] = ny
                    row[ox] = -nx
                    row[oy] = -ny
                    row[sv] = -apothem_factor
                    aub.append(row)
                    bub.append(-support(angles[i], nx, ny))

            for i in range(n):
                for j in range(i + 1, n):
                    dx = centers[j][0] - centers[i][0]
                    dy = centers[j][1] - centers[i][1]
                    axes = []
                    for angle in (angles[i], angles[j]):
                        ar = math.radians(angle)
                        axes.extend(
                            (math.cos(ar + math.pi / 6.0 + k * math.pi / 3.0),
                             math.sin(ar + math.pi / 6.0 + k * math.pi / 3.0))
                            for k in range(6)
                        )
                    nx, ny = max(axes,
                                 key=lambda q: abs(q[0] * dx + q[1] * dy))
                    if nx * dx + ny * dy < 0.0:
                        nx, ny = -nx, -ny
                    rhs = (support(angles[i], nx, ny) +
                           support(angles[j], nx, ny) + clearance)
                    row = [0.0] * dimension
                    row[2 * i] = nx
                    row[2 * i + 1] = ny
                    row[2 * j] = -nx
                    row[2 * j + 1] = -ny
                    aub.append(row)
                    bub.append(-rhs)

            bounds = [(None, None)] * dimension
            bounds[sv] = (0.0, None)
            lp = linprog(objective, A_ub=aub, b_ub=bub,
                         bounds=bounds, method="highs")
            if not lp.success:
                return None
            last_result = lp.x
            centers = [(float(last_result[2 * i]),
                        float(last_result[2 * i + 1]))
                       for i in range(n)]

        outer_center = (float(last_result[ox]), float(last_result[oy]))
        side = float(last_result[sv]) + 2.0e-5
        if not verifier_valid(centers, angles, outer_center, side,
                              outer_angle):
            return None
        return side, centers, outer_center

    patterns = ((4, 3, 4), (3, 4, 4), (4, 4, 3),
                (5, 3, 3), (3, 5, 3))
    templates = [("uniform", 0.0), ("row30", 0.0),
                 ("checker30", 0.0), ("row15", 0.0)]
    for offset in (-25., -20., -15., -10., -5., 5., 10., 15., 20., 25.):
        templates += [("row", offset), ("checker", offset)]

    best = None
    for pattern in patterns:
        initial = seed(pattern)
        for kind, offset in templates:
            angles = base_angles(pattern, kind, offset)
            for oa in range(31):
                trial = solve_trial(initial, angles, float(oa), 6)
                if trial is not None and (best is None or trial[0] < best[0]):
                    best = (trial[0], trial[1], angles, trial[2],
                            float(oa), pattern, kind, offset)

    if best is None:
        return fallback()

    side, centers, angles, outer_center, outer_angle, pattern, kind, offset = best
    tl = tr = bl = br = 0.0
    template_offset = offset

    for step in (0.20, 0.05, 0.01):
        changed = True
        while changed:
            changed = False
            coords = ["top_left", "top_right", "bottom_left",
                      "bottom_right", "outer"]
            if kind in ("row", "checker"):
                coords.insert(4, "template")
            for coordinate in coords:
                for direction in (1.0, -1.0):
                    ntl, ntr, nbl, nbr = tl, tr, bl, br
                    noffset, noa = template_offset, outer_angle
                    if coordinate == "top_left":
                        ntl += direction * step
                    elif coordinate == "top_right":
                        ntr += direction * step
                    elif coordinate == "bottom_left":
                        nbl += direction * step
                    elif coordinate == "bottom_right":
                        nbr += direction * step
                    elif coordinate == "template":
                        noffset += direction * step
                    else:
                        noa += direction * step
                    na = corrected(pattern, kind, noffset,
                                   ntl, ntr, nbl, nbr)
                    trial = solve_trial(centers, na, noa, 8)
                    if trial is None or trial[0] >= side - 1.0e-10:
                        continue
                    side, centers, outer_center = trial
                    angles, outer_angle = na, noa
                    tl, tr, bl, br = ntl, ntr, nbl, nbr
                    template_offset = noffset
                    changed = True
                    break
                if changed:
                    break

    # Select the six hexagons having the least support slack against the
    # current outer facets.  Their small independent corrections are then
    # polished with verifier-gated coordinate descent.
    def active_indices(current_angles):
        a = math.radians(outer_angle)
        normals = [(math.cos(a + math.pi / 6.0 + k * math.pi / 3.0),
                    math.sin(a + math.pi / 6.0 + k * math.pi / 3.0))
                   for k in range(6)]
        limit = side * apothem_factor
        ranked = []
        for i, ((x, y), angle) in enumerate(zip(centers, current_angles)):
            slack = min(limit - ((x - outer_center[0]) * nx +
                                 (y - outer_center[1]) * ny +
                                 support(angle, nx, ny))
                        for nx, ny in normals)
            ranked.append((slack, i))
        ranked.sort()
        return [i for _, i in ranked[:min(6, n)]]

    deltas = {}
    for step in (0.05, 0.01):
        changed = True
        while changed:
            changed = False
            selected = active_indices(angles)
            for index in selected:
                for direction in (1.0, -1.0):
                    trial_deltas = dict(deltas)
                    trial_deltas[index] = max(
                        -0.30, min(0.30,
                                   trial_deltas.get(index, 0.0) +
                                   direction * step))
                    na = corrected(pattern, kind, template_offset,
                                   tl, tr, bl, br, trial_deltas)
                    trial = solve_trial(centers, na, outer_angle, 8)
                    if trial is None or trial[0] >= side - 1.0e-10:
                        continue
                    ns, nc, no = trial
                    if not verifier_valid(nc, na, no, ns, outer_angle):
                        continue
                    side, centers, outer_center = ns, nc, no
                    angles, deltas = na, trial_deltas
                    changed = True
                    break
                if changed:
                    break

    minimum = min([outer_center[0], outer_center[1]] +
                  [v for c in centers for v in c])
    shift = max(1.0, 1.0 - minimum)
    return (
        [[float(x + shift), float(y + shift), float(a)]
         for (x, y), a in zip(centers, angles)],
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

```python
#!/usr/bin/env python3
"""Initial candidate for packing 11 unit hexagons in a regular hexagon."""

import json


# EVOLVE_START
def optimize_construct():
    import math

    # A small positive clearance is enough to defeat the verifier's numerical
    # tolerance while leaving almost all of the LP optimum available.
    clearance = 2.0e-6
    apothem_factor = math.cos(math.pi / 6.0)
    root3 = math.sqrt(3.0)
    n = 11

    def vertices(cx, cy, angle):
        a = math.radians(angle)
        return [(cx + math.cos(a + k * math.pi / 3.0),
                 cy + math.sin(a + k * math.pi / 3.0))
                for k in range(6)]

    def support(angle, nx, ny):
        a = math.radians(angle)
        return max(math.cos(a + k * math.pi / 3.0) * nx +
                   math.sin(a + k * math.pi / 3.0) * ny
                   for k in range(6))

    def disjoint(p, q):
        axes = []
        for poly in (p, q):
            for i in range(6):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % 6]
                dx, dy = x2 - x1, y2 - y1
                length = math.hypot(dx, dy)
                axes.append((-dy / length, dx / length))

        for ax, ay in axes:
            p0 = min(x * ax + y * ay for x, y in p)
            p1 = max(x * ax + y * ay for x, y in p)
            q0 = min(x * ax + y * ay for x, y in q)
            q1 = max(x * ax + y * ay for x, y in q)
            if p1 < q0 - 1.0e-8 or q1 < p0 - 1.0e-8:
                return True
        return False

    def contained(poly, center, side, angle):
        a = math.radians(angle)
        limit = side * apothem_factor
        for k in range(6):
            nx = math.cos(a + math.pi / 6.0 + k * math.pi / 3.0)
            ny = math.sin(a + math.pi / 6.0 + k * math.pi / 3.0)
            if any((x - center[0]) * nx + (y - center[1]) * ny
                   > limit + 1.0e-8 for x, y in poly):
                return False
        return True

    def valid(centers, angles, outer_center, side, outer_angle):
        polys = [vertices(x, y, a)
                 for (x, y), a in zip(centers, angles)]
        for i, poly in enumerate(polys):
            if not contained(poly, outer_center, side, outer_angle):
                return False
            for j in range(i):
                if not disjoint(poly, polys[j]):
                    return False
        return True

    def verifier_valid(centers, angles, outer_center, side, outer_angle):
        if not valid(centers, angles, outer_center, side, outer_angle):
            return False

        verifier = globals().get("verify_construction")
        if callable(verifier):
            data = [[float(x), float(y), float(a)]
                    for (x, y), a in zip(centers, angles)]
            try:
                return bool(verifier(data, [float(outer_center[0]),
                                            float(outer_center[1])],
                                     float(side), float(outer_angle)))
            except Exception:
                return False
        return True

    def seed(pattern):
        result = []
        rows = len(pattern)
        for row, count in enumerate(pattern):
            y = (row - (rows - 1) / 2.0) * root3 * 1.015
            offset = 1.0 if row % 2 else 0.0
            for col in range(count):
                x = (col - (count - 1) / 2.0) * 2.015 + offset
                result.append((x, y))
        return result

    def fallback():
        pattern = (4, 3, 4)
        p = seed(pattern)
        shift = 8.0
        return (
            [[float(x + shift), float(y + shift), 0.0] for x, y in p],
            [float(shift), float(shift)],
            5.25,
            0.0,
        )

    try:
        from scipy.optimize import linprog
    except Exception:
        return fallback()

    def base_angles(pattern, kind, offset):
        result = []
        for row, count in enumerate(pattern):
            for col in range(count):
                if kind == "uniform":
                    angle = 0.0
                elif kind == "row30":
                    angle = 30.0 if row % 2 else 0.0
                elif kind == "checker30":
                    angle = 30.0 if (row + col) % 2 else 0.0
                elif kind == "row15":
                    angle = 15.0 if row % 2 else 0.0
                elif kind == "row":
                    angle = offset if row % 2 else 0.0
                else:
                    angle = offset if (row + col) % 2 else 0.0
                result.append(float(angle))
        return result

    def corrected(pattern, kind, offset, tl, tr, bl, br, deltas=None):
        result = base_angles(pattern, kind, offset)
        last = len(pattern) - 1
        index = 0

        for row, count in enumerate(pattern):
            for col in range(count):
                correction = 0.0
                if row == 0:
                    correction = tl if col < count / 2.0 else tr
                elif row == last:
                    correction = bl if col < count / 2.0 else br

                if deltas is not None:
                    correction += deltas.get(index, 0.0)

                result[index] += correction
                index += 1

        return result

    def separating_axes(angle1, angle2):
        axes = []
        for angle in (angle1, angle2):
            a = math.radians(angle)
            for k in range(6):
                t = a + math.pi / 6.0 + k * math.pi / 3.0
                axes.append((math.cos(t), math.sin(t)))
        return axes

    def solve_trial(initial, angles, outer_angle, iterations=7):
        """
        Solve a fixed-orientation convex relaxation.

        The pair constraints are successively updated using the SAT normal
        most aligned with the current center-to-center direction.  This is
        a sequential convex approximation of the exact disjointness
        constraints, followed by an exact verification.
        """
        centers = [(float(x), float(y)) for x, y in initial]
        ox, oy, sv = 2 * n, 2 * n + 1, 2 * n + 2
        dimension = sv + 1
        last_result = None

        outer_radians = math.radians(outer_angle)
        outer_normals = [
            (math.cos(outer_radians + math.pi / 6.0 +
                      k * math.pi / 3.0),
             math.sin(outer_radians + math.pi / 6.0 +
                       k * math.pi / 3.0))
            for k in range(6)
        ]

        for _ in range(iterations):
            objective = [0.0] * dimension
            objective[sv] = 1.0
            aub = []
            bub = []

            # Exact support-function inequalities for containment.
            for i in range(n):
                for nx, ny in outer_normals:
                    row = [0.0] * dimension
                    row[2 * i] = nx
                    row[2 * i + 1] = ny
                    row[ox] = -nx
                    row[oy] = -ny
                    row[sv] = -apothem_factor
                    aub.append(row)
                    bub.append(-support(angles[i], nx, ny))

            # One currently active separating half-space for each pair.
            for i in range(n):
                for j in range(i + 1, n):
                    dx = centers[j][0] - centers[i][0]
                    dy = centers[j][1] - centers[i][1]
                    axes = separating_axes(angles[i], angles[j])

                    nx, ny = max(
                        axes,
                        key=lambda q: abs(q[0] * dx + q[1] * dy)
                    )
                    if nx * dx + ny * dy < 0.0:
                        nx, ny = -nx, -ny

                    rhs = (support(angles[i], nx, ny) +
                            support(angles[j], nx, ny) + clearance)

                    row = [0.0] * dimension
                    row[2 * i] = nx
                    row[2 * i + 1] = ny
                    row[2 * j] = -nx
                    row[2 * j + 1] = -ny
                    aub.append(row)
                    bub.append(-rhs)

            bounds = [(None, None)] * dimension
            bounds[sv] = (0.0, None)

            try:
                lp = linprog(objective, A_ub=aub, b_ub=bub,
                             bounds=bounds, method="highs")
            except Exception:
                return None

            if not lp.success:
                return None

            last_result = lp.x
            centers = [
                (float(last_result[2 * i]),
                 float(last_result[2 * i + 1]))
                for i in range(n)
            ]

        if last_result is None:
            return None

        outer_center = (float(last_result[ox]), float(last_result[oy]))
        # The padding makes the first exact verification independent of
        # tiny HiGHS primal residuals.  It is removed below by bisection.
        side = float(last_result[sv]) + 5.0e-6

        if not verifier_valid(centers, angles, outer_center, side,
                              outer_angle):
            return None
        return side, centers, outer_center

    patterns = (
        (4, 3, 4),
        (3, 4, 4),
        (4, 4, 3),
        (5, 3, 3),
        (3, 5, 3),
    )

    templates = [
        ("uniform", 0.0),
        ("row30", 0.0),
        ("checker30", 0.0),
        ("row15", 0.0),
    ]
    for offset in (-25.0, -20.0, -15.0, -10.0, -5.0,
                   5.0, 10.0, 15.0, 20.0, 25.0):
        templates.append(("row", offset))
        templates.append(("checker", offset))

    best = None

    # The outer regular hexagon has 60-degree rotational symmetry and
    # reflection symmetry, so [0,30] contains all distinct orientations.
    for pattern in patterns:
        initial = seed(pattern)
        for kind, offset in templates:
            angles = base_angles(pattern, kind, offset)
            for outer_angle in range(31):
                trial = solve_trial(initial, angles, float(outer_angle), 6)
                if trial is not None and (
                        best is None or trial[0] < best[0]):
                    best = (
                        trial[0], trial[1], angles, trial[2],
                        float(outer_angle), pattern, kind, offset
                    )

    if best is None:
        return fallback()

    side, centers, angles, outer_center, outer_angle, pattern, kind, offset = best
    tl = tr = bl = br = 0.0
    template_offset = offset

    # Deterministic coordinate polishing of the boundary groups, together
    # with the outer orientation and the alternating-row phase.
    for step in (0.20, 0.05, 0.01):
        changed = True
        while changed:
            changed = False
            coordinates = [
                "top_left", "top_right",
                "bottom_left", "bottom_right", "outer"
            ]
            if kind in ("row", "checker"):
                coordinates.insert(4, "template")

            for coordinate in coordinates:
                accepted = False
                for direction in (1.0, -1.0):
                    ntl, ntr, nbl, nbr = tl, tr, bl, br
                    noffset, noa = template_offset, outer_angle

                    if coordinate == "top_left":
                        ntl += direction * step
                    elif coordinate == "top_right":
                        ntr += direction * step
                    elif coordinate == "bottom_left":
                        nbl += direction * step
                    elif coordinate == "bottom_right":
                        nbr += direction * step
                    elif coordinate == "template":
                        noffset += direction * step
                    else:
                        noa += direction * step

                    na = corrected(pattern, kind, noffset,
                                   ntl, ntr, nbl, nbr)
                    trial = solve_trial(centers, na, noa, 8)
                    if trial is None or trial[0] >= side - 1.0e-10:
                        continue

                    ns, nc, no = trial
                    if not verifier_valid(nc, na, no, ns, noa):
                        continue

                    side, centers, outer_center = ns, nc, no
                    angles, outer_angle = na, noa
                    tl, tr, bl, br = ntl, ntr, nbl, nbr
                    template_offset = noffset
                    changed = True
                    accepted = True
                    break

                if accepted:
                    break

    def active_indices(current_angles):
        a = math.radians(outer_angle)
        normals = [
            (math.cos(a + math.pi / 6.0 + k * math.pi / 3.0),
             math.sin(a + math.pi / 6.0 + k * math.pi / 3.0))
            for k in range(6)
        ]
        limit = side * apothem_factor
        ranked = []

        for i, ((x, y), angle) in enumerate(
                zip(centers, current_angles)):
            slack = min(
                limit - ((x - outer_center[0]) * nx +
                         (y - outer_center[1]) * ny +
                         support(angle, nx, ny))
                for nx, ny in normals
            )
            ranked.append((slack, i))

        ranked.sort()
        return [i for _, i in ranked[:6]]

    # Give the six most boundary-critical hexagons independent small
    # orientation corrections.  Re-ranking after every accepted move keeps
    # the polishing focused on the currently active support constraints.
    deltas = {}
    for step in (0.05, 0.01):
        changed = True
        while changed:
            changed = False
            for index in active_indices(angles):
                accepted = False
                for direction in (1.0, -1.0):
                    trial_deltas = dict(deltas)
                    trial_deltas[index] = max(
                        -0.30,
                        min(0.30,
                            trial_deltas.get(index, 0.0) +
                            direction * step)
                    )
                    na = corrected(
                        pattern, kind, template_offset,
                        tl, tr, bl, br, trial_deltas
                    )
                    trial = solve_trial(centers, na, outer_angle, 8)
                    if trial is None or trial[0] >= side - 1.0e-10:
                        continue

                    ns, nc, no = trial
                    if not verifier_valid(nc, na, no, ns, outer_angle):
                        continue

                    side, centers, outer_center = ns, nc, no
                    angles, deltas = na, trial_deltas
                    changed = True
                    accepted = True
                    break

                if accepted:
                    break

    # Remove the LP safety padding while keeping the configuration fixed.
    # Containment is monotone in the outer side length, so this is a reliable
    # one-dimensional final optimization.
    padded_side = float(side)
    lower = max(0.0, padded_side - 8.0e-6)
    feasible_side = padded_side

    for _ in range(32):
        midpoint = 0.5 * (lower + feasible_side)
        if verifier_valid(centers, angles, outer_center, midpoint,
                          outer_angle):
            feasible_side = midpoint
        else:
            lower = midpoint

    trimmed_side = feasible_side + 4.0e-7
    if verifier_valid(centers, angles, outer_center, trimmed_side,
                      outer_angle):
        side = trimmed_side
    else:
        side = feasible_side

    # Translate the complete construction into the positive quadrant.  The
    # margin accounts for the unit circumradius of the inner hexagons.
    minimum = min(
        [outer_center[0], outer_center[1]] +
        [v for c in centers for v in c]
    )
    shift = max(1.0, 1.0 - minimum)

    return (
        [[float(x + shift), float(y + shift), float(a)]
         for (x, y), a in zip(centers, angles)],
        [float(outer_center[0] + shift),
         float(outer_center[1] + shift)],
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

```python
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

```
