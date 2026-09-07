Actionable insight grounded in the algorithm's deterministic multi-start linear-program refinement for packing 21 circles under W+H<=2.

- Multi-Start Sequential Linear Packing: By linearizing pairwise non-overlap as n_ij · (center_i − center_j) ≥ r_i + r_j with fixed directions per iteration and solving the resulting small linear program where the previous iterate remains feasible, the method achieves monotone increases in the total radii while guaranteeing feasibility of each iterate.
- Multi-Start Sequential Linear Packing: Using deterministic multi-start from several structurally different feasible seeds—including a 5-by-5 partial grid, staggered rows, transposed layouts, and jittered unequal-radius layouts—mitigates the contact-topology lock-in induced by fixed pair directions and improves the chance of finding better packings.
- Multi-Start Sequential Linear Packing: Co-optimizing the rectangle dimensions by treating W and H as variables under W+H ≤ 2 and accepting optional SLSQP polishing only after independent validation of all 210 separations and the perimeter yields robust candidates, evidenced by an observed sum_radii of 2.359691291055729 with validity 1.0 in Generation 0.

```python
#!/usr/bin/env python3
"""Deterministic optimization for packing 21 circles in a perimeter-four rectangle."""

import json

import numpy as np


# EVOLVE_START
def _validate(circles, tolerance=1.0e-8):
    """Independently validate positivity, non-overlap, and minimum bounds."""
    circles = np.asarray(circles, dtype=float)
    if circles.shape != (21, 3) or not np.all(np.isfinite(circles)):
        return False
    if np.any(circles[:, 2] <= 0.0):
        return False

    centers = circles[:, :2]
    radii = circles[:, 2]

    for i in range(21):
        differences = centers[i + 1 :] - centers[i]
        if len(differences):
            distances = np.sqrt(np.sum(differences * differences, axis=1))
            if np.any(distances + tolerance < radii[i] + radii[i + 1 :]):
                return False

    left = np.min(centers[:, 0] - radii)
    right = np.max(centers[:, 0] + radii)
    bottom = np.min(centers[:, 1] - radii)
    top = np.max(centers[:, 1] + radii)
    return (right - left) + (top - bottom) <= 2.0 + tolerance


def _make_feasible_seed(centers, width, height, weights=None):
    """Assign scaled radii to fixed centers, guaranteeing a feasible seed."""
    centers = np.asarray(centers, dtype=float)
    count = len(centers)
    if weights is None:
        weights = np.ones(count, dtype=float)
    else:
        weights = np.asarray(weights, dtype=float)

    # Determine the largest common scale for the requested relative radii.
    boundary_clearance = np.minimum.reduce(
        (
            centers[:, 0],
            width - centers[:, 0],
            centers[:, 1],
            height - centers[:, 1],
        )
    )
    scale = np.min(boundary_clearance / weights)

    for i in range(count):
        differences = centers[i + 1 :] - centers[i]
        if len(differences):
            distances = np.sqrt(np.sum(differences * differences, axis=1))
            scale = min(
                scale,
                np.min(distances / (weights[i] + weights[i + 1 :])),
            )

    # Leave a small margin so the seed remains feasible despite roundoff.
    radii = weights * scale * (1.0 - 2.0e-6)
    return np.column_stack((centers, radii)), float(width), float(height)


def _row_seed(row_counts, transpose=False, phase=0):
    """Build a staggered equal-radius row layout."""
    row_counts = list(row_counts)
    row_count = len(row_counts)
    max_columns = max(row_counts)

    # Matching width and height to nominal column/row counts gives cells with
    # approximately equal physical dimensions while using W + H = 2.
    width = 2.0 * max_columns / (max_columns + row_count)
    height = 2.0 - width
    pitch = width / max_columns
    centers = []

    for row, columns in enumerate(row_counts):
        y = (row + 0.5) * height / row_count
        used_width = columns * pitch

        # Alternate the side on which a shortened row receives its offset.
        spare = width - used_width
        if (row + phase) % 2:
            offset = 0.65 * spare
        else:
            offset = 0.35 * spare

        for column in range(columns):
            centers.append((offset + (column + 0.5) * pitch, y))

    centers = np.asarray(centers, dtype=float)
    if transpose:
        centers = centers[:, ::-1]
        width, height = height, width
    return _make_feasible_seed(centers, width, height)


def _jittered_seed(seed, seed_number):
    """Create a deterministic, feasible unequal-radius perturbation."""
    circles, width, height = seed
    centers = circles[:, :2].copy()
    indices = np.arange(len(centers), dtype=float)

    # The trigonometric perturbation is deterministic and avoids preserving
    # every symmetry and contact direction of the source row layout.
    amplitude_x = 0.018 * width
    amplitude_y = 0.018 * height
    centers[:, 0] += amplitude_x * np.sin(
        (indices + 1.0) * (1.73 + 0.11 * seed_number)
    )
    centers[:, 1] += amplitude_y * np.cos(
        (indices + 1.0) * (2.09 + 0.07 * seed_number)
    )

    margin = 1.0e-4
    centers[:, 0] = np.clip(centers[:, 0], margin, width - margin)
    centers[:, 1] = np.clip(centers[:, 1], margin, height - margin)

    weights = (
        0.78
        + 0.17 * (1.0 + np.sin((indices + 1.0) * (1.31 + seed_number)))
        + 0.06 * ((indices.astype(int) + seed_number) % 3)
    )
    return _make_feasible_seed(centers, width, height, weights)


def _lp_refine(seed, linprog):
    """Repeatedly optimize radii with fixed pair-separation directions."""
    initial, width, height = seed
    count = len(initial)
    pair_count = count * (count - 1) // 2
    variable_count = 3 * count + 2
    width_index = 3 * count
    height_index = width_index + 1

    current = np.empty(variable_count, dtype=float)
    current[:count] = initial[:, 0]
    current[count : 2 * count] = initial[:, 1]
    current[2 * count : 3 * count] = initial[:, 2]
    current[width_index] = width
    current[height_index] = height

    objective = np.zeros(variable_count, dtype=float)
    objective[2 * count : 3 * count] = -1.0
    bounds = (
        [(0.0, 2.0)] * (2 * count)
        + [(1.0e-8, 1.0)] * count
        + [(0.0, 2.0), (0.0, 2.0)]
    )

    previous_value = np.sum(initial[:, 2])
    for _ in range(40):
        rows = 4 * count + 1 + pair_count
        a_ub = np.zeros((rows, variable_count), dtype=float)
        b_ub = np.zeros(rows, dtype=float)
        row = 0

        # Circle-to-rectangle containment constraints.
        for i in range(count):
            radius_index = 2 * count + i

            # r_i - x_i <= 0
            a_ub[row, i] = -1.0
            a_ub[row, radius_index] = 1.0
            row += 1

            # x_i + r_i - W <= 0
            a_ub[row, i] = 1.0
            a_ub[row, radius_index] = 1.0
            a_ub[row, width_index] = -1.0
            row += 1

            # r_i - y_i <= 0
            a_ub[row, count + i] = -1.0
            a_ub[row, radius_index] = 1.0
            row += 1

            # y_i + r_i - H <= 0
            a_ub[row, count + i] = 1.0
            a_ub[row, radius_index] = 1.0
            a_ub[row, height_index] = -1.0
            row += 1

        # W + H <= 2 is exactly the perimeter-at-most-four constraint.
        a_ub[row, width_index] = 1.0
        a_ub[row, height_index] = 1.0
        b_ub[row] = 2.0
        row += 1

        x = current[:count]
        y = current[count : 2 * count]

        # A projected separation is conservative: Euclidean distance is at
        # least its projection on any unit direction.
        for i in range(count):
            for j in range(i + 1, count):
                dx = x[i] - x[j]
                dy = y[i] - y[j]
                distance = np.hypot(dx, dy)
                if distance < 1.0e-14:
                    angle = (i * count + j + 1) * 2.399963229728653
                    nx, ny = np.cos(angle), np.sin(angle)
                else:
                    nx, ny = dx / distance, dy / distance

                # -n.(center_i-center_j) + r_i+r_j <= 0
                a_ub[row, i] = -nx
                a_ub[row, count + i] = -ny
                a_ub[row, j] = nx
                a_ub[row, count + j] = ny
                a_ub[row, 2 * count + i] = 1.0
                a_ub[row, 2 * count + j] = 1.0
                row += 1

        result = linprog(
            objective,
            A_ub=a_ub,
            b_ub=b_ub,
            bounds=bounds,
            method="highs",
            options={
                "primal_feasibility_tolerance": 1.0e-9,
                "dual_feasibility_tolerance": 1.0e-9,
            },
        )
        if not result.success or not np.all(np.isfinite(result.x)):
            break

        candidate = np.column_stack(
            (
                result.x[:count],
                result.x[count : 2 * count],
                result.x[2 * count : 3 * count],
            )
        )
        value = float(np.sum(candidate[:, 2]))

        # Reject meaningful numerical regressions or invalid solver output.
        if value + 2.0e-8 < previous_value:
            break
        if not _validate(candidate, tolerance=3.0e-7):
            break

        improvement = value - previous_value
        displacement = np.max(np.abs(result.x - current))
        current = result.x
        previous_value = value

        if improvement <= 2.0e-10 and displacement <= 2.0e-7:
            break
        if improvement <= 5.0e-11:
            break

    circles = np.column_stack(
        (
            current[:count],
            current[count : 2 * count],
            current[2 * count : 3 * count],
        )
    )
    return circles, current


def _nonlinear_polish(state, minimize):
    """Polish the best LP solution using exact squared-distance constraints."""
    count = 21
    variable_count = 3 * count + 2
    width_index = 3 * count
    height_index = width_index + 1

    def objective(z):
        return -float(np.sum(z[2 * count : 3 * count]))

    def objective_jacobian(z):
        jacobian = np.zeros(variable_count, dtype=float)
        jacobian[2 * count : 3 * count] = -1.0
        return jacobian

    pair_count = count * (count - 1) // 2
    constraint_count = 4 * count + 1 + pair_count

    def constraints(z):
        x = z[:count]
        y = z[count : 2 * count]
        radii = z[2 * count : 3 * count]
        width = z[width_index]
        height = z[height_index]

        values = np.empty(constraint_count, dtype=float)
        values[:count] = x - radii
        values[count : 2 * count] = width - x - radii
        values[2 * count : 3 * count] = y - radii
        values[3 * count : 4 * count] = height - y - radii
        values[4 * count] = 2.0 - width - height

        row = 4 * count + 1
        for i in range(count):
            for j in range(i + 1, count):
                dx = x[i] - x[j]
                dy = y[i] - y[j]
                radius_sum = radii[i] + radii[j]
                values[row] = dx * dx + dy * dy - radius_sum * radius_sum
                row += 1
        return values

    def constraint_jacobian(z):
        x = z[:count]
        y = z[count : 2 * count]
        radii = z[2 * count : 3 * count]
        jacobian = np.zeros((constraint_count, variable_count), dtype=float)

        for i in range(count):
            radius_index = 2 * count + i

            jacobian[i, i] = 1.0
            jacobian[i, radius_index] = -1.0

            jacobian[count + i, i] = -1.0
            jacobian[count + i, radius_index] = -1.0
            jacobian[count + i, width_index] = 1.0

            jacobian[2 * count + i, count + i] = 1.0
            jacobian[2 * count + i, radius_index] = -1.0

            jacobian[3 * count + i, count + i] = -1.0
            jacobian[3 * count + i, radius_index] = -1.0
            jacobian[3 * count + i, height_index] = 1.0

        jacobian[4 * count, width_index] = -1.0
        jacobian[4 * count, height_index] = -1.0

        row = 4 * count + 1
        for i in range(count):
            for j in range(i + 1, count):
                dx = x[i] - x[j]
                dy = y[i] - y[j]
                radius_sum = radii[i] + radii[j]

                jacobian[row, i] = 2.0 * dx
                jacobian[row, j] = -2.0 * dx
                jacobian[row, count + i] = 2.0 * dy
                jacobian[row, count + j] = -2.0 * dy
                jacobian[row, 2 * count + i] = -2.0 * radius_sum
                jacobian[row, 2 * count + j] = -2.0 * radius_sum
                row += 1
        return jacobian

    bounds = (
        [(0.0, 2.0)] * (2 * count)
        + [(1.0e-8, 1.0)] * count
        + [(0.0, 2.0), (0.0, 2.0)]
    )
    result = minimize(
        objective,
        state,
        jac=objective_jacobian,
        method="SLSQP",
        bounds=bounds,
        constraints={
            "type": "ineq",
            "fun": constraints,
            "jac": constraint_jacobian,
        },
        options={"maxiter": 300, "ftol": 2.0e-12, "disp": False},
    )

    if not np.all(np.isfinite(result.x)):
        return None, None

    circles = np.column_stack(
        (
            result.x[:count],
            result.x[count : 2 * count],
            result.x[2 * count : 3 * count],
        )
    )
    return circles, result.x


def _finalize(circles):
    """Recompute minimum bounds and apply a tiny uniform safety reduction."""
    result = np.asarray(circles, dtype=float).copy()
    centers = result[:, :2]
    radii = result[:, 2]

    maximum_overlap = 0.0
    for i in range(21):
        differences = centers[i + 1 :] - centers[i]
        if len(differences):
            distances = np.sqrt(np.sum(differences * differences, axis=1))
            overlaps = radii[i] + radii[i + 1 :] - distances
            maximum_overlap = max(maximum_overlap, float(np.max(overlaps)))

    width = np.max(centers[:, 0] + radii) - np.min(centers[:, 0] - radii)
    height = np.max(centers[:, 1] + radii) - np.min(centers[:, 1] - radii)
    perimeter_excess = width + height - 2.0

    reduction = max(0.0, maximum_overlap / 2.0, perimeter_excess / 4.0)
    reduction += 3.0e-9
    result[:, 2] -= reduction

    # Translate the actual minimum rectangle to start at the origin.
    result[:, 0] -= np.min(result[:, 0] - result[:, 2])
    result[:, 1] -= np.min(result[:, 1] - result[:, 2])

    # A second correction is normally unnecessary, but protects against
    # unusual platform-dependent floating-point behavior.
    if not _validate(result, tolerance=1.0e-10):
        result[:, 2] -= 2.0e-8
        result[:, 0] -= np.min(result[:, 0] - result[:, 2])
        result[:, 1] -= np.min(result[:, 1] - result[:, 2])

    return result


def construct_packing(num_circles: int = 21):
    """Construct an optimized packing of exactly 21 disjoint circles."""
    if num_circles != 21:
        raise ValueError("This construction is specialized for exactly 21 circles")

    # Keep the original grid as a guaranteed feasible incumbent and fallback.
    baseline_radius = 0.099999
    baseline_centers = np.array(
        [
            [(column + 0.5) / 5.0, (row + 0.5) / 5.0]
            for row in range(5)
            for column in range(5)
        ][:21],
        dtype=float,
    )
    baseline = np.column_stack(
        (baseline_centers, np.full(21, baseline_radius, dtype=float))
    )
    baseline_value = float(np.sum(baseline[:, 2]))

    try:
        from scipy.optimize import linprog, minimize
    except ImportError:
        return baseline

    seeds = [(baseline.copy(), 1.0, 1.0)]
    row_specs = (
        ([5, 4, 4, 4, 4], 0),
        ([4, 5, 4, 4, 4], 1),
        ([6, 5, 5, 5], 0),
        ([4, 4, 4, 3, 3, 3], 1),
    )

    structured = []
    for row_counts, phase in row_specs:
        normal = _row_seed(row_counts, transpose=False, phase=phase)
        transposed = _row_seed(row_counts, transpose=True, phase=phase)
        structured.extend((normal, transposed))
    seeds.extend(structured)

    # Add several unequal-radius, asymmetrically perturbed starts.
    for seed_number, source in enumerate(structured[:3]):
        seeds.append(_jittered_seed(source, seed_number))

    best_circles = baseline
    best_state = None
    best_value = baseline_value

    for seed in seeds:
        try:
            circles, state = _lp_refine(seed, linprog)
        except Exception:
            # A single failed optimization start must not lose the incumbent.
            continue

        value = float(np.sum(circles[:, 2]))
        if _validate(circles, tolerance=3.0e-7) and value > best_value + 1.0e-9:
            best_circles = circles
            best_state = state
            best_value = value

    # Exact nonlinear polishing can improve contacts that fixed projections
    # cannot rotate through. It is accepted only after independent validation.
    if best_state is not None:
        try:
            polished, polished_state = _nonlinear_polish(best_state, minimize)
        except Exception:
            polished = polished_state = None

        if polished is not None:
            polished_value = float(np.sum(polished[:, 2]))
            if (
                polished_value > best_value + 1.0e-10
                and _validate(polished, tolerance=2.0e-7)
            ):
                best_circles = polished
                best_state = polished_state
                best_value = polished_value

    # If optimization did not improve on the guaranteed incumbent, preserve it
    # exactly rather than needlessly applying an additional safety shrink.
    if best_value <= baseline_value + 1.0e-9:
        return baseline

    finalized = _finalize(best_circles)
    if (
        _validate(finalized, tolerance=1.0e-10)
        and np.sum(finalized[:, 2]) > baseline_value
    ):
        return finalized
    return baseline


# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```
