Insight on why the run achieved valid tight packing and strong score, and what to reuse.

- Dual‑Guided SAT‑Shrinkwrap with MTV Repair and Facet‑Aligned Boundary Polishing: Use SAT-based minimum-translation repairs along true separating axes (avoiding uniform inflation) and immediately re-fit the outer hexagon via support-function shrink-wrap by re-optimizing the rotation φ and center C after each accepted inner change; this feasibility-first, co-moving outer fit preserved boundary tightness and produced a valid packing (validity 1.0) with outer_hex_side_length ≈ 4.000008 and score 0.9827 in ≈25.38s. Reuse this pattern together with dual-guided boundary equalization that targets active outer-facet contributors to reduce the maximal apothem without introducing global slack.
- SAT-Guided Honeycomb MTV Repair with Active-Normal Shrink-Wrap: Replacing global radial inflation with SAT-based Minimum Translation Vector micro-repairs kept overlap resolution local and minimized slack, then scanning outer rotations from 0–60 degrees with active-normal subgradient shrink-wrap and a verification-guided bisection on the outer side length yielded a valid packing (validity = 1.0) with outer_hex_side_length ≈ 4.00002073 in ≈ 171.03 s; reuse this SAT-MTV micro-repair plus active-normal shrink-wrap and verifier-driven bisection pattern when tightly packing 11 unit hexagons within a regular hexagon under strict non-overlap and floating-point tolerance constraints.
- SAT-MTV with Free Center Drift, Decay, and Angle Micro-Sweep: In the SAT-verified hexagon packing setting, removing forced centroid re-centering allowed the 11 unit hexagons to drift as a group, reducing boundary slack while maintaining non-overlap. Coupling MTV corrections with a decaying learning rate (from ~0.5 down to ~0.05) suppressed oscillations at tight tolerances, and a fine outer-angle micro-sweep (±2° at 0.5° increments) around the coarse 5° sweep’s best angle extracted marginal gains. This combination produced validity = 1.0 and target_ratio = 0.9853840996471558 with outer_hex_side_length = 3.989307318240272, evidencing a tightly feasible configuration. Future designs should reuse free-center drift under SAT constraints, apply scheduled learning-rate decay to stabilize MTV steps, and perform local angle micro-sweeps around a coarse optimum to capture residual slack.
- Gauss-Seidel Constraint Resolution: Replacing simultaneous Jacobi updates with immediate, in-place Gauss-Seidel center updates and recomputing vertices inside pairwise checks ensures each collision test uses up-to-date states, reducing stale-interaction oscillations and speeding convergence in MTV-based packing.
- Precomputed Vertex Offsets: Precomputing the six relative vertex offsets for the fixed inner hexagon angle outside the optimization loop replaces per-iteration trigonometric calls with coordinate additions, maintaining high performance when combined with Gauss-Seidel updates.
- Cosine Annealing Learning Rate Schedule: Applying lr = lr_end + 0.5*(lr_start - lr_end)*(1 + cos(pi*it/max_iters)) enables large structural moves early and smooth fine-tuning later, helping the optimizer escape packing plateaus before stabilizing.
- Precision Bisection and Angle Micro-sweep: Increasing binary search depth from 15 to 25, raising max_iters to 1200, and scanning the outer-hexagon angle at 0.25-degree increments provides the resolution needed to capture small geometric slack near feasibility boundaries.
- Accelerated Gauss-Seidel MTV with Cosine Annealing: In this run, the method achieved target_ratio 0.99211733348212 with validity 1.0 at outer_hex_side_length 3.9622329610984917 in 752.9109057569876 seconds, evidencing stable convergence to a high-quality valid configuration.

```python
#!/usr/bin/env python3
"""Algorithmic construction to pack 11 unit regular hexagons into a minimal regular hexagonal container.

Notes:
- We generate compact seeds based on honeycomb axial coordinates: a 4–3–4 row seed and
  a center+first ring (6) + four from the second ring (out of 12 candidates) seed.
- We strictly enforce non-overlap by a SAT-based minimum-translation-vector (MTV) micro-repair:
  for any intersecting pair, we nudge them apart along the true separating axis with the least
  overlap and add a tiny safety gap. This avoids global radial inflation and preserves tightness.
- For each feasible seed and for several inner orientation angles, we perform a shrink-wrap
  fit of the outer hexagon:
  * For an outer rotation φ in [0°, 60°), compute the support along the six facet normals.
  * Solve for the outer center C by equalizing opposite supports in a least-squares sense.
  * The required apothem a(φ, C) is the max directional support; side length L = 2a / √3.
  * We sweep φ on a coarse grid then do a fine micro-sweep about the best φ.
- We keep the best candidate and finally shift all coordinates into the first quadrant with
  a small margin and return the result.

This construction is deterministic, fast, and produces a tightly packed layout that
passes the provided verify_construction without modifying it.

Implementation enhancements (over the baseline parent):
- Remove radial inflation; replace with SAT-MTV micro-repair to keep layouts strictly disjoint with
  minimal slack.
- Coarser-to-finer φ sweep to reduce outer side length.
"""

import json
import math
from itertools import combinations

# ==============================
# Core construction entry point
# ==============================

# EVOLVE_START
def optimize_construct():
    # Configuration parameters
    # Candidate inner hexagon orientations (degrees). Hex symmetry repeats every 60°.
    inner_angles = [0.0, 12.0, 15.0, 18.0, 24.0, 30.0]
    # Outer rotation sweep grid (degrees)
    phi_coarse_step = 0.25  # coarse sweep step size
    phi_fine_halfspan = 0.8  # half-width of local fine sweep window around the best φ (degrees)
    phi_fine_step = 0.01     # fine sweep step size

    # Numeric cushions
    l_margin = 1.0e-8  # small cushion added to side length to ensure strict containment
    shift_margin = 1.0e-3  # shift to ensure strictly positive coordinates

    # SAT/MTV separation parameters
    sep_eps = 1.0e-5  # minimal separation gap to ensure "strictly disjoint" under SAT with tolerance
    max_mtv_iters = 2000

    # Build seeds
    seeds = []
    seeds.extend(generate_row_seeds())
    seeds.extend(generate_ring_seeds())

    # Best result accumulator: (L, [hexes], [Cx, Cy], phi)
    # hexes is a list of dicts: {"x":..., "y":..., "angle":...}
    best = None

    for centers in seeds:
        for inner_angle in inner_angles:
            # Initialize hexes with given common orientation
            hexes = [{"x": x, "y": y, "angle": inner_angle} for (x, y) in centers]

            # Ensure non-overlap using SAT-MTV micro-repair with minimal movement
            separate_by_mtv(hexes, sep_eps=sep_eps, max_iters=max_mtv_iters)

            # Compute minimal L using shrink-wrap (coarse sweep)
            Lc, Cx, Cy, phi = shrinkwrap_outer_from_hexes(hexes, phi_step=phi_coarse_step)
            if Lc is None:
                continue

            # Fine local sweep around the best φ to refine further
            L, Cx_ref, Cy_ref, phi_ref = fine_refine_phi(hexes, phi_best=phi,
                                                         halfspan=phi_fine_halfspan,
                                                         fine_step=phi_fine_step)
            if L is None:
                # fallback to coarse if refine failed for any reason
                L, Cx_ref, Cy_ref, phi_ref = Lc, Cx, Cy, phi

            # Keep best
            if (best is None) or (L < best[0]):
                # Deep copy hex list
                best_hexes = [{"x": h["x"], "y": h["y"], "angle": h["angle"]} for h in hexes]
                best = (L, best_hexes, Cx_ref, Cy_ref, phi_ref)

    if best is None:
        # Fallback: extremely conservative box (should always verify but large L)
        positions = [
            (-3.3, -2.0), (-1.1, -2.0), (1.1, -2.0), (3.3, -2.0),
            (-3.3, 0.0), (-1.1, 0.0), (1.1, 0.0), (3.3, 0.0),
            (-2.2, 2.0), (0.0, 2.0), (2.2, 2.0),
        ]
        inner_hexagons = [[x + 10.0, y + 10.0, 0.0] for x, y in positions]
        return inner_hexagons, [10.0, 10.0], 8.0, 0.0

    L_best, hexes_best, Cx_best, Cy_best, phi_best = best

    # Add tiny cushion to L for safety under verification
    L_best += l_margin

    # Shift all coordinates to strictly positive quadrant with a small margin
    min_x = min([h["x"] for h in hexes_best] + [Cx_best])
    min_y = min([h["y"] for h in hexes_best] + [Cy_best])
    dx = (-min_x + shift_margin) if min_x <= 0 else 0.0
    dy = (-min_y + shift_margin) if min_y <= 0 else 0.0

    # Construct final data
    inner_hexagons = []
    for h in hexes_best:
        inner_hexagons.append([h["x"] + dx, h["y"] + dy, h["angle"]])

    outer_center = [Cx_best + dx, Cy_best + dy]
    outer_side_length = L_best
    outer_angle_degrees = phi_best

    return inner_hexagons, outer_center, outer_side_length, outer_angle_degrees


# ================
# Optimizer utils
# ================

def separate_by_mtv(hexes, sep_eps=1e-5, max_iters=2000):
    """Resolve intersections by applying equal-and-opposite MTV nudges with a tiny gap.
    hexes: list of {"x","y","angle"}.

    Important: sat_mtv_convex returns the MTV that, when applied to polygon 1, separates it from polygon 2.
    We split this MTV equally: move hex i by +0.5*(overlap + gap) along the MTV direction and
    hex j by -0.5*(overlap + gap) along the same direction. If the MTV magnitude is zero (touching),
    we still separate by sep_eps along the center-to-center direction.
    """
    # Repeatedly scan for a colliding pair and nudge them apart.
    n = len(hexes)
    iters = 0
    while iters < max_iters:
        iters += 1
        any_fix = False
        for i in range(n):
            vi = hexagon_vertices_local(hexes[i]["x"], hexes[i]["y"], 1.0, hexes[i]["angle"])
            for j in range(i + 1, n):
                vj = hexagon_vertices_local(hexes[j]["x"], hexes[j]["y"], 1.0, hexes[j]["angle"])
                intersect, mtv = sat_mtv_convex(vi, vj)
                if intersect:
                    # Compute movement vectors
                    mv_len = math.hypot(mtv[0], mtv[1])
                    if mv_len > 0.0:
                        ux = mtv[0] / mv_len
                        uy = mtv[1] / mv_len
                        # Split the overlap and add a tiny separation gap
                        move_mag = 0.5 * mv_len + 0.5 * sep_eps
                        dx = ux * move_mag
                        dy = uy * move_mag
                    else:
                        # Touching without overlap along the SAT axis; use center-to-center direction
                        dx_raw = hexes[j]["x"] - hexes[i]["x"]
                        dy_raw = hexes[j]["y"] - hexes[i]["y"]
                        norm = math.hypot(dx_raw, dy_raw)
                        if norm == 0.0:
                            ux, uy = 1.0, 0.0
                        else:
                            ux, uy = dx_raw / norm, dy_raw / norm
                        dx = ux * (0.5 * sep_eps)
                        dy = uy * (0.5 * sep_eps)

                    # Apply equal and opposite nudge: move hex i along +dir, hex j along -dir
                    hexes[i]["x"] += dx
                    hexes[i]["y"] += dy
                    hexes[j]["x"] -= dx
                    hexes[j]["y"] -= dy

                    any_fix = True
                    # Break to recompute vertices freshly after each fix
                    break
            if any_fix:
                break
        if not any_fix:
            break
    return


def fine_refine_phi(hexes, phi_best, halfspan=0.8, fine_step=0.01):
    """Refine the best φ locally by a fine grid sweep in [phi_best - halfspan, phi_best + halfspan]."""
    # Clamp sweep to [0, 60)
    start = max(0.0, phi_best - halfspan)
    end = min(60.0, phi_best + halfspan)
    if end <= start:
        return shrinkwrap_outer_from_hexes(hexes, phi_step=fine_step)

    best = None
    phi = start
    while phi <= end + 1e-12:
        candidate = shrinkwrap_with_fixed_phi(hexes, phi)
        if candidate is not None:
            L, Cx, Cy = candidate
            if (best is None) or (L < best[0]):
                best = (L, Cx, Cy, phi)
        phi += fine_step
    if best is None:
        return None, None, None, None
    return best


def shrinkwrap_outer_from_hexes(hexes, phi_step=0.25):
    """Compute minimal outer side length L for a given set of hexes by sweeping outer rotation φ."""
    all_vertices = []
    for h in hexes:
        all_vertices.extend(hexagon_vertices_local(h["x"], h["y"], 1.0, h["angle"]))

    best = None  # (L, Cx, Cy, phi)
    phi = 0.0
    while phi < 60.0 - 1e-12:
        normals3 = facet_normals_from_phi(phi)
        # Compute supports
        S_max, S_min = support_extrema(all_vertices, normals3)
        # Solve for center
        targets = [0.5 * (S_max[k] + S_min[k]) for k in range(3)]
        Cx, Cy = least_squares_center(normals3, targets)
        # Compute apothem needed
        a_needed = 0.0
        for k in range(3):
            n = normals3[k]
            n_dot_C = n[0] * Cx + n[1] * Cy
            need_plus = S_max[k] - n_dot_C
            need_minus = (-S_min[k]) + n_dot_C
            if need_plus > a_needed:
                a_needed = need_plus
            if need_minus > a_needed:
                a_needed = need_minus
        L = (2.0 * a_needed) / math.sqrt(3.0)
        if (best is None) or (L < best[0]):
            best = (L, Cx, Cy, phi)
        phi += phi_step
    return best


def shrinkwrap_with_fixed_phi(hexes, phi):
    """Shrink-wrap with fixed φ; return (L, Cx, Cy) or None."""
    all_vertices = []
    for h in hexes:
        all_vertices.extend(hexagon_vertices_local(h["x"], h["y"], 1.0, h["angle"]))
    normals3 = facet_normals_from_phi(phi)
    S_max, S_min = support_extrema(all_vertices, normals3)
    targets = [0.5 * (S_max[k] + S_min[k]) for k in range(3)]
    Cx, Cy = least_squares_center(normals3, targets)
    a_needed = 0.0
    for k in range(3):
        n = normals3[k]
        n_dot_C = n[0] * Cx + n[1] * Cy
        need_plus = S_max[k] - n_dot_C
        need_minus = (-S_min[k]) + n_dot_C
        if need_plus > a_needed:
            a_needed = need_plus
        if need_minus > a_needed:
            a_needed = need_minus
    L = (2.0 * a_needed) / math.sqrt(3.0)
    return (L, Cx, Cy)


def facet_normals_from_phi(phi):
    """Return the three unique outward facet normals of a regular hexagon rotated by φ degrees."""
    return [
        (math.cos(math.radians(phi + 30.0 + k * 60.0)), math.sin(math.radians(phi + 30.0 + k * 60.0)))
        for k in range(3)
    ]


def support_extrema(points, normals3):
    """Compute max and min support values along the three normals for a point set."""
    S_max = []
    S_min = []
    for n in normals3:
        ps = [p[0] * n[0] + p[1] * n[1] for p in points]
        S_max.append(max(ps))
        S_min.append(min(ps))
    return S_max, S_min
# EVOLVE_END


# ============================
# Seed generators
# ============================

def generate_row_seeds():
    """Generate row-based seeds: 4–3–4, 3–4–4, 4–4–3 on pointy-top lattice (Δx=√3, Δy=1.5)."""
    seeds = []
    rt3 = math.sqrt(3.0)

    # 4–3–4
    y_top = 1.5
    y_mid = 0.0
    y_bot = -1.5
    row_top = [(-1.5 * rt3, y_top), (-0.5 * rt3, y_top), (0.5 * rt3, y_top), (1.5 * rt3, y_top)]
    row_mid = [(-1.0 * rt3, y_mid), (0.0, y_mid), (1.0 * rt3, y_mid)]
    row_bot = [(-1.5 * rt3, y_bot), (-0.5 * rt3, y_bot), (0.5 * rt3, y_bot), (1.5 * rt3, y_bot)]
    centers_434 = row_top + row_mid + row_bot
    # Add a small centroid shift towards origin for symmetry
    centers_434 = recenter_to_origin(centers_434)
    seeds.append(centers_434)

    # 3–4–4 (shift one row order)
    row_top = [(-1.0 * rt3, y_top), (0.0, y_top), (1.0 * rt3, y_top)]
    row_mid = [(-1.5 * rt3, y_mid), (-0.5 * rt3, y_mid), (0.5 * rt3, y_mid), (1.5 * rt3, y_mid)]
    row_bot = [(-1.5 * rt3, y_bot), (-0.5 * rt3, y_bot), (0.5 * rt3, y_bot), (1.5 * rt3, y_bot)]
    centers_344 = row_top + row_mid + row_bot
    centers_344 = recenter_to_origin(centers_344)
    seeds.append(centers_344)

    # 4–4–3
    row_top = [(-1.5 * rt3, y_top), (-0.5 * rt3, y_top), (0.5 * rt3, y_top), (1.5 * rt3, y_top)]
    row_mid = [(-1.5 * rt3, y_mid), (-0.5 * rt3, y_mid), (0.5 * rt3, y_mid), (1.5 * rt3, y_mid)]
    row_bot = [(-1.0 * rt3, y_bot), (0.0, y_bot), (1.0 * rt3, y_bot)]
    centers_443 = row_top + row_mid + row_bot
    centers_443 = recenter_to_origin(centers_443)
    seeds.append(centers_443)

    return seeds


def generate_ring_seeds():
    """Generate center + 6 first ring + 4 of second ring candidates (multiple combinations)."""
    seeds = []
    rt3 = math.sqrt(3.0)

    # First-ring neighbor vectors for pointy-top lattice
    v = [
        (rt3, 0.0),
        (0.5 * rt3, 1.5),
        (-0.5 * rt3, 1.5),
        (-rt3, 0.0),
        (-0.5 * rt3, -1.5),
        (0.5 * rt3, -1.5),
    ]

    # Build base: center + first ring
    base = [(0.0, 0.0)]
    base.extend(v)

    # Second ring candidates: 12 positions (six doubles, six mixed sums)
    second_ring = []
    for i in range(6):
        # 2*vi
        second_ring.append((2.0 * v[i][0], 2.0 * v[i][1]))
    for i in range(6):
        j = (i + 1) % 6
        second_ring.append((v[i][0] + v[j][0], v[i][1] + v[j][1]))

    # We will choose 4 out of 12 with an emphasis on balanced shapes.
    # To reduce combinatorics, we primarily use the mixed-sum half (indices 6..11),
    # which are typically closer angularly and give rounded silhouettes.
    mixed_indices = list(range(6, 12))
    for combo in combinations(mixed_indices, 4):
        centers = list(base)
        for idx in combo:
            centers.append(second_ring[idx])
        centers = recenter_to_origin(centers)
        seeds.append(centers)

    # Additionally, include a couple of hand-picked balanced combos mixing doubles and mixed sums.
    # This broadens the candidate shapes.
    handpicked = [
        (0, 2, 7, 10),  # 2*vi combo with some mixed
        (1, 3, 8, 11),
        (2, 4, 6, 9),
    ]
    for c in handpicked:
        centers = list(base)
        for idx in c:
            centers.append(second_ring[idx])
        centers = recenter_to_origin(centers)
        seeds.append(centers)

    # All seeds currently have 1 + 6 + 4 = 11 centers
    # Filter out any duplicates (by rounded coordinates) to keep variety but avoid redundancy
    uniq = []
    seen = set()
    for centers in seeds:
        key = tuple(round(x, 6) for p in centers for x in p)
        if key not in seen and len(centers) == 11:
            seen.add(key)
            uniq.append(centers)
    return uniq


# ============================
# Shrink-wrap outer hexagon
# ============================

def shrinkwrap_outer(centers, inner_angle_deg, phi_step):
    """For given centers and common inner angle, compute minimal outer L via rotation sweep."""
    # Precompute all inner hex vertices
    all_vertices = []
    for (x, y) in centers:
        verts = hexagon_vertices_local(x, y, 1.0, inner_angle_deg)
        all_vertices.extend(verts)

    best = None  # (L, Cx, Cy, phi)

    # Sweep φ within [0, 60) due to hex symmetry; φ is the vertex rotation of the outer hexagon
    phi = 0.0
    while phi < 60.0 - 1e-12:
        # For a regular hexagon with vertex rotation φ, the outward facet normals are at angles:
        # φ + 30°, φ + 90°, φ + 150° (and their negatives).
        normals3 = [
            (math.cos(math.radians(phi + 30.0 + k * 60.0)), math.sin(math.radians(phi + 30.0 + k * 60.0)))
            for k in range(3)
        ]

        # For each normal, compute S_max= max n⋅p, S_min= min n⋅p across all vertices
        S_max = []
        S_min = []
        for n in normals3:
            proj = [p[0] * n[0] + p[1] * n[1] for p in all_vertices]
            S_max.append(max(proj))
            S_min.append(min(proj))

        # Solve for center C to equalize opposite supports in least squares:
        # For each k, target t_k satisfies n_k ⋅ C = (S_max_k + S_min_k)/2
        t = [0.5 * (S_max[k] + S_min[k]) for k in range(3)]
        Cx, Cy = least_squares_center(normals3, t)

        # Compute necessary apothem a = max over all 6 facets
        a_needed = 0.0
        for k in range(3):
            n = normals3[k]
            n_dot_C = n[0] * Cx + n[1] * Cy
            need_plus = S_max[k] - n_dot_C
            need_minus = (-S_min[k]) + n_dot_C
            a_needed = max(a_needed, need_plus, need_minus)

        # Side length L = 2*a / √3
        L = (2.0 * a_needed) / math.sqrt(3.0)

        if (best is None) or (L < best[0]):
            best = (L, Cx, Cy, phi)

        phi += phi_step

    return best


# ============================
# Utilities
# ============================

def recenter_to_origin(centers):
    """Shift centers so that their average is near origin (centroid -> 0)."""
    if not centers:
        return centers
    sx = sum(x for (x, y) in centers)
    sy = sum(y for (x, y) in centers)
    n = float(len(centers))
    cx = sx / n
    cy = sy / n
    return [(x - cx, y - cy) for (x, y) in centers]


def hexagon_vertices_local(center_x, center_y, side_length, angle_degrees):
    """Compute vertices of a unit hexagon centered at (center_x, center_y) rotated by angle_degrees.
    We replicate the logic of the provided utility but locally for shrink-wrap calculations.
    """
    verts = []
    angle_radians = math.radians(angle_degrees)
    for i in range(6):
        ang = angle_radians + 2.0 * math.pi * i / 6.0
        x = center_x + side_length * math.cos(ang)
        y = center_y + side_length * math.sin(ang)
        verts.append((x, y))
    return verts


def least_squares_center(normals3, targets):
    """Solve min_C sum_k (n_k ⋅ C - t_k)^2 for k=0..2; returns Cx, Cy."""
    # A is 3x2 with rows n_k; solve (A^T A) C = A^T t
    n0, n1, n2 = normals3
    A11 = n0[0] * n0[0] + n1[0] * n1[0] + n2[0] * n2[0]
    A12 = n0[0] * n0[1] + n1[0] * n1[1] + n2[0] * n2[1]
    A22 = n0[1] * n0[1] + n1[1] * n1[1] + n2[1] * n2[1]

    b1 = n0[0] * targets[0] + n1[0] * targets[1] + n2[0] * targets[2]
    b2 = n0[1] * targets[0] + n1[1] * targets[1] + n2[1] * targets[2]

    # Solve 2x2 system:
    det = A11 * A22 - A12 * A12
    if abs(det) < 1e-12:
        # Fallback: take origin
        return 0.0, 0.0
    inv11 = A22 / det
    inv12 = -A12 / det
    inv22 = A11 / det

    Cx = inv11 * b1 + inv12 * b2
    Cy = inv12 * b1 + inv22 * b2
    return Cx, Cy


# -------------
# Local SAT-MTV
# -------------

def get_normals_polygon(vertices):
    """Compute outward normals for each edge of a convex polygon (normalized)."""
    normals = []
    m = len(vertices)
    for i in range(m):
        p1 = vertices[i]
        p2 = vertices[(i + 1) % m]
        ex = p2[0] - p1[0]
        ey = p2[1] - p1[1]
        nx = -ey
        ny = ex
        norm = math.hypot(nx, ny)
        if norm > 0:
            nx /= norm
            ny /= norm
        normals.append((nx, ny))
    return normals


def project_polygon(vertices, axis):
    """Project polygon onto axis; return min and max scalar projections."""
    min_proj = float('inf')
    max_proj = float('-inf')
    for v in vertices:
        p = v[0] * axis[0] + v[1] * axis[1]
        if p < min_proj:
            min_proj = p
        if p > max_proj:
            max_proj = p
    return min_proj, max_proj


def interval_overlap(a_min, a_max, b_min, b_max):
    """Return overlap depth for two 1D intervals (>=0 if touching/overlapping, <0 if separated)."""
    return min(a_max, b_max) - max(a_min, b_min)


def polygon_centroid(vertices):
    """Compute centroid for convex polygon (simple average of vertices)."""
    cx = sum(p[0] for p in vertices) / float(len(vertices))
    cy = sum(p[1] for p in vertices) / float(len(vertices))
    return cx, cy


def sat_mtv_convex(vertices1, vertices2):
    """SAT with MTV: returns (intersect: bool, mtv_for_1: (dx,dy)) for convex polygons.
    If no intersection, returns (False, (0,0)).
    If intersect (including touching), returns (True, MTV) where MTV is the minimal translation
    vector to move polygon 1 away from polygon 2 along the axis of smallest penetration.
    """
    axes = []
    axes.extend(get_normals_polygon(vertices1))
    axes.extend(get_normals_polygon(vertices2))

    min_overlap = float('inf')
    smallest_axis = None

    a_cx, a_cy = polygon_centroid(vertices1)
    b_cx, b_cy = polygon_centroid(vertices2)

    for axis in axes:
        a_min, a_max = project_polygon(vertices1, axis)
        b_min, b_max = project_polygon(vertices2, axis)
        ov = interval_overlap(a_min, a_max, b_min, b_max)
        # If intervals are separated (ov < 0), polygons are disjoint
        if ov < 0.0:
            return False, (0.0, 0.0)
        # Keep the axis with the smallest overlap
        if ov < min_overlap:
            min_overlap = ov
            smallest_axis = axis

    # They intersect or touch. Build MTV direction:
    # Ensure MTV points from polygon 1 towards outside of polygon 2.
    # Use center-to-center vector to set direction.
    axis = smallest_axis
    dir_sign = 1.0
    rel_c = (b_cx - a_cx) * axis[0] + (b_cy - a_cy) * axis[1]
    if rel_c > 0:
        dir_sign = -1.0  # move poly1 opposite axis to separate from poly2
    else:
        dir_sign = 1.0
    mtv = (axis[0] * min_overlap * dir_sign, axis[1] * min_overlap * dir_sign)
    return True, mtv


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
import math

EPSILON = 1e-9

def hexagon_vertices(center_x, center_y, side_length, angle_degrees):
    """
    Calculate the vertices of a hexagon.

    Args:
        center_x: x-coordinate of hexagon center
        center_y: y-coordinate of hexagon center
        side_length: Length of hexagon side
        angle_degrees: Rotation angle in degrees

    Returns:
        list: List of (x, y) tuples representing the hexagon vertices
    """
    vertices = []
    angle_radians = math.radians(angle_degrees)
    for i in range(6):
        angle = angle_radians + 2 * math.pi * i / 6
        x = center_x + side_length * math.cos(angle)
        y = center_y + side_length * math.sin(angle)
        vertices.append((x, y))
    return vertices

def normalize_vector(v):
    """
    Normalize a 2D vector to unit length.

    Args:
        v: (x, y) tuple representing the vector

    Returns:
        tuple: Normalized (x, y) vector
    """
    magnitude = math.sqrt(v[0]**2 + v[1]**2)
    return (v[0] / magnitude, v[1] / magnitude) if magnitude > EPSILON else (0., 0.)

def get_normals(vertices):
    """
    Calculate normal vectors for all edges of a polygon.

    Args:
        vertices: List of (x, y) tuples representing polygon vertices

    Returns:
        list: List of (x, y) normal vectors
    """
    normals = []
    for i in range(len(vertices)):
        p1 = vertices[i]
        p2 = vertices[(i + 1) % len(vertices)]
        edge = (p2[0] - p1[0], p2[1] - p1[1])
        normal = normalize_vector((-edge[1], edge[0]))
        normals.append(normal)
    return normals

def project_polygon(vertices, axis):
    """
    Project a polygon onto an axis and return the min and max projection values.

    Args:
        vertices: List of (x, y) tuples representing polygon vertices
        axis: (x, y) tuple representing the projection axis

    Returns:
        tuple: (min_projection, max_projection) projection range
    """
    min_proj = float('inf')
    max_proj = float('-inf')
    for vertex in vertices:
        projection = vertex[0] * axis[0] + vertex[1] * axis[1]
        min_proj = min(min_proj, projection)
        max_proj = max(max_proj, projection)
    return min_proj, max_proj

def overlap_1d(min1, max1, min2, max2):
    """
    Check if two 1D intervals overlap (with epsilon tolerance).

    Args:
        min1, max1: Start and end of first interval
        min2, max2: Start and end of second interval

    Returns:
        bool: True if intervals overlap, False otherwise
    """
    return max1 >= min2 - EPSILON and max2 >= min1 - EPSILON

def polygons_intersect(vertices1, vertices2):
    """
    Check if two convex polygons intersect using the Separating Axis Theorem.

    Args:
        vertices1: List of (x, y) tuples for first polygon
        vertices2: List of (x, y) tuples for second polygon

    Returns:
        bool: True if polygons intersect, False otherwise
    """
    normals1 = get_normals(vertices1)
    normals2 = get_normals(vertices2)
    axes = normals1 + normals2

    for axis in axes:
        min1, max1 = project_polygon(vertices1, axis)
        min2, max2 = project_polygon(vertices2, axis)
        if not overlap_1d(min1, max1, min2, max2):
            return False
    return True

def hexagons_are_disjoint(hex1_params, hex2_params):
    """
    Check if two hexagons are disjoint (non-overlapping).

    Args:
        hex1_params: (center_x, center_y, side_length, angle_degrees) for first hexagon
        hex2_params: (center_x, center_y, side_length, angle_degrees) for second hexagon

    Returns:
        bool: True if hexagons are disjoint, False if they intersect
    """   
    hex1_vertices = hexagon_vertices(*hex1_params)
    hex2_vertices = hexagon_vertices(*hex2_params)
    return not polygons_intersect(hex1_vertices, hex2_vertices)

def is_inside_hexagon(point, hex_params):
    """
    Check if a point is inside a hexagon.

    Args:
        point: (x, y) tuple representing the point
        hex_params: (center_x, center_y, side_length, angle_degrees) for the hexagon

    Returns:
        bool: True if point is inside hexagon, False otherwise
    """
    hex_vertices = hexagon_vertices(*hex_params)
    for i in range(len(hex_vertices)):
        p1 = hex_vertices[i]
        p2 = hex_vertices[(i + 1) % len(hex_vertices)]
        edge_vector = (p2[0] - p1[0], p2[1] - p1[1])
        point_vector = (point[0] - p1[0], point[1] - p1[1])
        cross_product = (edge_vector[0] * point_vector[1] - edge_vector[1] * point_vector[0])

        if cross_product < -EPSILON:
            return False

    return True

def all_hexagons_contained(inner_hex_params_list, outer_hex_params):
    """
    Check if all inner hexagons are completely contained within the outer hexagon.

    Args:
        inner_hex_params_list: List of (x, y, side_length, angle_degrees) for inner hexagons
        outer_hex_params: (center_x, center_y, side_length, angle_degrees) for outer hexagon

    Returns:
        bool: True if all inner hexagons are contained, False otherwise
    """
    for inner_hex_params in inner_hex_params_list:
        inner_hex_vertices = hexagon_vertices(*inner_hex_params)
        for vertex in inner_hex_vertices:
            if not is_inside_hexagon(vertex, outer_hex_params):
                return False
    return True    

def verify_construction(inner_hex_data, outer_hex_center, outer_hex_side_length, outer_hex_angle_degrees):
    """
    Verify the complete construction meets all requirements.

    Args:
        inner_hex_data: List of [x, y, angle_degrees] for inner hexagons
        outer_hex_center: [x, y] for outer hexagon center
        outer_hex_side_length: Side length of outer hexagon
        outer_hex_angle_degrees: Rotation angle of outer hexagon in degrees

    Returns:
        bool: True if construction is valid, False otherwise
    """
    inner_hex_params_list = [
        (x, y, 1, angle) for x, y, angle in inner_hex_data
    ]
    outer_hex_params = (
        outer_hex_center[0], outer_hex_center[1],
        outer_hex_side_length, outer_hex_angle_degrees
    )

    if outer_hex_side_length < EPSILON:
        return False

    for i in range(len(inner_hex_params_list)):
        for j in range(i + 1, len(inner_hex_params_list)):
            if not hexagons_are_disjoint(inner_hex_params_list[i], inner_hex_params_list[j]):
                return False

    if not all_hexagons_contained(inner_hex_params_list, outer_hex_params):
        return False

    return True

def optimize_construct():
    def get_mtv(vertices1, vertices2):
        """Calculate Minimum Translation Vector to separate two convex polygons."""
        normals1 = get_normals(vertices1)
        normals2 = get_normals(vertices2)
        axes = normals1 + normals2
        
        min_overlap = float('inf')
        mtv_axis = None
        
        for axis in axes:
            min1, max1 = project_polygon(vertices1, axis)
            min2, max2 = project_polygon(vertices2, axis)
            
            # If there's a separating axis, they don't intersect
            if max1 <= min2 + 1e-9 or max2 <= min1 + 1e-9:
                return None 
                
            overlap = min(max1 - min2, max2 - min1)
            
            if overlap < min_overlap:
                min_overlap = overlap
                
                # Determine direction: should point from polygon 2 to polygon 1
                c1x = sum(v[0] for v in vertices1) / len(vertices1)
                c1y = sum(v[1] for v in vertices1) / len(vertices1)
                c2x = sum(v[0] for v in vertices2) / len(vertices2)
                c2y = sum(v[1] for v in vertices2) / len(vertices2)
                
                if (c1x - c2x) * axis[0] + (c1y - c2y) * axis[1] < 0:
                    mtv_axis = (-axis[0], -axis[1])
                else:
                    mtv_axis = axis
                    
        if mtv_axis is None:
            return None
            
        return (mtv_axis[0] * min_overlap, mtv_axis[1] * min_overlap)

    def get_mtv_inside_hex(inner_vertices, outer_center, outer_side, outer_angle):
        """Calculate MTV to push an inner polygon entirely inside the outer hexagon."""
        A = outer_side * math.sqrt(3) / 2
        normals = []
        outer_angle_rad = math.radians(outer_angle)
        
        # Calculate inward normals for the 6 edges of the outer hexagon
        for i in range(6):
            n_out_angle = outer_angle_rad + 2 * math.pi * i / 6 + math.pi / 6
            nx = -math.cos(n_out_angle)
            ny = -math.sin(n_out_angle)
            normals.append((nx, ny))
            
        total_tx = 0
        total_ty = 0
        
        for nx, ny in normals:
            # Project inner vertices onto the inward normal
            min_proj = min((v[0] - outer_center[0]) * nx + (v[1] - outer_center[1]) * ny for v in inner_vertices)
            # The boundary is at -A along the inward normal
            if min_proj < -A:
                depth = -A - min_proj
                total_tx += nx * depth
                total_ty += ny * depth
                
        return (total_tx, total_ty)

    def optimize_layout(seed_centers, inner_angle, outer_angle, S, max_iters=800):
        """Physics-based SAT MTV optimization to pack hexagons tightly."""
        centers = [list(c) for c in seed_centers]
        
        # Center the configuration at the origin
        cx = sum(c[0] for c in centers) / len(centers)
        cy = sum(c[1] for c in centers) / len(centers)
        for c in centers:
            c[0] -= cx
            c[1] -= cy
        outer_center = [0.0, 0.0]
        
        lr_inner = 0.4
        lr_outer = 0.4
        # Add padding to ensure strict disjointness for the verifier
        pad = 1e-5
        
        for it in range(max_iters):
            max_overlap = 0.0
            
            # 1. Resolve overlaps between inner hexagons
            inner_moves = [[0.0, 0.0] for _ in range(11)]
            for i in range(11):
                v1 = hexagon_vertices(centers[i][0], centers[i][1], 1.0 + pad, inner_angle)
                for j in range(i+1, 11):
                    v2 = hexagon_vertices(centers[j][0], centers[j][1], 1.0 + pad, inner_angle)
                    mtv = get_mtv(v1, v2)
                    if mtv is not None:
                        mag = math.hypot(mtv[0], mtv[1])
                        if mag > max_overlap:
                            max_overlap = mag
                        inner_moves[i][0] += mtv[0] * lr_inner
                        inner_moves[i][1] += mtv[1] * lr_inner
                        inner_moves[j][0] -= mtv[0] * lr_inner
                        inner_moves[j][1] -= mtv[1] * lr_inner
                        
            for i in range(11):
                centers[i][0] += inner_moves[i][0]
                centers[i][1] += inner_moves[i][1]
                
            # 2. Resolve boundary violations with the outer hexagon
            outer_moves = [[0.0, 0.0] for _ in range(11)]
            for i in range(11):
                v1 = hexagon_vertices(centers[i][0], centers[i][1], 1.0 + pad, inner_angle)
                mtv_out = get_mtv_inside_hex(v1, outer_center, S - pad, outer_angle)
                mag = math.hypot(mtv_out[0], mtv_out[1])
                if mag > 0:
                    if mag > max_overlap:
                        max_overlap = mag
                    outer_moves[i][0] += mtv_out[0] * lr_outer
                    outer_moves[i][1] += mtv_out[1] * lr_outer
                    
            for i in range(11):
                centers[i][0] += outer_moves[i][0]
                centers[i][1] += outer_moves[i][1]
                
            # Keep configuration centered
            cx = sum(c[0] for c in centers) / len(centers)
            cy = sum(c[1] for c in centers) / len(centers)
            for c in centers:
                c[0] -= cx
                c[1] -= cy
                
            if max_overlap < 1e-7:
                break
                
        # Final strict verification
        inner_data = [[c[0], c[1], inner_angle] for c in centers]
        if verify_construction(inner_data, outer_center, S, outer_angle):
            return centers, outer_center, True
            
        return centers, outer_center, False

    def generate_seeds():
        """Generate dense honeycomb lattice subsets (4-3-4, 3-4-4, 4-4-3)."""
        seeds = []
        R = math.sqrt(3)
        
        c_434 = []
        for i in range(4): c_434.append((i*R, 0))
        for i in range(3): c_434.append((R/2 + i*R, 1.5))
        for i in range(4): c_434.append((i*R, 3.0))
        
        c_344 = []
        for i in range(3): c_344.append((R/2 + i*R, 0))
        for i in range(4): c_344.append((i*R, 1.5))
        for i in range(4): c_344.append((R/2 + i*R, 3.0))
        
        c_443 = []
        for i in range(4): c_443.append((i*R, 0))
        for i in range(4): c_443.append((R/2 + i*R, 1.5))
        for i in range(3): c_443.append((i*R, 3.0))
        
        base_seeds = [c_434, c_344, c_443]
        
        final_seeds = []
        for centers in base_seeds:
            # Orientation 30 (pointy top)
            final_seeds.append((centers, 30.0))
            # Orientation 0 (flat top)
            flat_centers = [(-y, x) for x, y in centers]
            final_seeds.append((flat_centers, 0.0))
            
        return final_seeds

    # Verification-Guided Bisection Search
    seeds = generate_seeds()
    outer_angles = [float(a) for a in range(0, 60, 5)]
    
    best_S = 4.2
    best_config = None
    
    for seed_centers, inner_angle in seeds:
        for outer_angle in outer_angles:
            low = 3.7
            high = best_S
            
            # Quick check to prune unpromising configurations
            current_centers, current_oc, success = optimize_layout(
                seed_centers, inner_angle, outer_angle, high, max_iters=600)
            
            if not success:
                continue
                
            valid_centers = current_centers
            valid_oc = current_oc
            
            # Binary search to find the minimum feasible side length S
            for step in range(15):
                mid = (low + high) / 2.0
                test_centers, test_oc, success = optimize_layout(
                    current_centers, inner_angle, outer_angle, mid, max_iters=800)
                
                if success:
                    high = mid
                    valid_centers = test_centers
                    valid_oc = test_oc
                    # Warm start for the next iteration
                    current_centers = test_centers
                else:
                    low = mid
                    
            # Update best configuration found so far
            if high < best_S:
                best_S = high
                best_config = (valid_centers, inner_angle, outer_angle, high, valid_oc)
                
    if best_config is None:
        # Fallback (should not be reached)
        seed_centers, inner_angle = seeds[0]
        return [[c[0]+10, c[1]+10, inner_angle] for c in seed_centers], [10.0, 10.0], 4.2, 0.0
        
    centers, inner_angle, outer_angle, S, oc = best_config
    
    # Normalize coordinates to ensure x > 0 and y > 0
    min_x = min(c[0] for c in centers) - S
    min_y = min(c[1] for c in centers) - S
    
    shift_x = -min_x + 1.0
    shift_y = -min_y + 1.0
    
    final_inner = [[c[0] + shift_x, c[1] + shift_y, inner_angle] for c in centers]
    final_oc = [oc[0] + shift_x, oc[1] + shift_y]
    
    return final_inner, final_oc, S, outer_angle
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
import math

EPSILON = 1e-9

def hexagon_vertices(center_x, center_y, side_length, angle_degrees):
    """
    Calculate the vertices of a hexagon.

    Args:
        center_x: x-coordinate of hexagon center
        center_y: y-coordinate of hexagon center
        side_length: Length of hexagon side
        angle_degrees: Rotation angle in degrees

    Returns:
        list: List of (x, y) tuples representing the hexagon vertices
    """
    vertices = []
    angle_radians = math.radians(angle_degrees)
    for i in range(6):
        angle = angle_radians + 2 * math.pi * i / 6
        x = center_x + side_length * math.cos(angle)
        y = center_y + side_length * math.sin(angle)
        vertices.append((x, y))
    return vertices

def normalize_vector(v):
    """
    Normalize a 2D vector to unit length.

    Args:
        v: (x, y) tuple representing the vector

    Returns:
        tuple: Normalized (x, y) vector
    """
    magnitude = math.sqrt(v[0]**2 + v[1]**2)
    return (v[0] / magnitude, v[1] / magnitude) if magnitude > EPSILON else (0., 0.)

def get_normals(vertices):
    """
    Calculate normal vectors for all edges of a polygon.

    Args:
        vertices: List of (x, y) tuples representing polygon vertices

    Returns:
        list: List of (x, y) normal vectors
    """
    normals = []
    for i in range(len(vertices)):
        p1 = vertices[i]
        p2 = vertices[(i + 1) % len(vertices)]
        edge = (p2[0] - p1[0], p2[1] - p1[1])
        normal = normalize_vector((-edge[1], edge[0]))
        normals.append(normal)
    return normals

def project_polygon(vertices, axis):
    """
    Project a polygon onto an axis and return the min and max projection values.

    Args:
        vertices: List of (x, y) tuples representing polygon vertices
        axis: (x, y) tuple representing the projection axis

    Returns:
        tuple: (min_projection, max_projection) projection range
    """
    min_proj = float('inf')
    max_proj = float('-inf')
    for vertex in vertices:
        projection = vertex[0] * axis[0] + vertex[1] * axis[1]
        min_proj = min(min_proj, projection)
        max_proj = max(max_proj, projection)
    return min_proj, max_proj

def overlap_1d(min1, max1, min2, max2):
    """
    Check if two 1D intervals overlap (with epsilon tolerance).

    Args:
        min1, max1: Start and end of first interval
        min2, max2: Start and end of second interval

    Returns:
        bool: True if intervals overlap, False otherwise
    """
    return max1 >= min2 - EPSILON and max2 >= min1 - EPSILON

def polygons_intersect(vertices1, vertices2):
    """
    Check if two convex polygons intersect using the Separating Axis Theorem.

    Args:
        vertices1: List of (x, y) tuples for first polygon
        vertices2: List of (x, y) tuples for second polygon

    Returns:
        bool: True if polygons intersect, False otherwise
    """
    normals1 = get_normals(vertices1)
    normals2 = get_normals(vertices2)
    axes = normals1 + normals2

    for axis in axes:
        min1, max1 = project_polygon(vertices1, axis)
        min2, max2 = project_polygon(vertices2, axis)
        if not overlap_1d(min1, max1, min2, max2):
            return False
    return True

def hexagons_are_disjoint(hex1_params, hex2_params):
    """
    Check if two hexagons are disjoint (non-overlapping).

    Args:
        hex1_params: (center_x, center_y, side_length, angle_degrees) for first hexagon
        hex2_params: (center_x, center_y, side_length, angle_degrees) for second hexagon

    Returns:
        bool: True if hexagons are disjoint, False if they intersect
    """   
    hex1_vertices = hexagon_vertices(*hex1_params)
    hex2_vertices = hexagon_vertices(*hex2_params)
    return not polygons_intersect(hex1_vertices, hex2_vertices)

def is_inside_hexagon(point, hex_params):
    """
    Check if a point is inside a hexagon.

    Args:
        point: (x, y) tuple representing the point
        hex_params: (center_x, center_y, side_length, angle_degrees) for the hexagon

    Returns:
        bool: True if point is inside hexagon, False otherwise
    """
    hex_vertices = hexagon_vertices(*hex_params)
    for i in range(len(hex_vertices)):
        p1 = hex_vertices[i]
        p2 = hex_vertices[(i + 1) % len(hex_vertices)]
        edge_vector = (p2[0] - p1[0], p2[1] - p1[1])
        point_vector = (point[0] - p1[0], point[1] - p1[1])
        cross_product = (edge_vector[0] * point_vector[1] - edge_vector[1] * point_vector[0])

        if cross_product < -EPSILON:
            return False

    return True

def all_hexagons_contained(inner_hex_params_list, outer_hex_params):
    """
    Check if all inner hexagons are completely contained within the outer hexagon.

    Args:
        inner_hex_params_list: List of (x, y, side_length, angle_degrees) for inner hexagons
        outer_hex_params: (center_x, center_y, side_length, angle_degrees) for outer hexagon

    Returns:
        bool: True if all inner hexagons are contained, False otherwise
    """
    for inner_hex_params in inner_hex_params_list:
        inner_hex_vertices = hexagon_vertices(*inner_hex_params)
        for vertex in inner_hex_vertices:
            if not is_inside_hexagon(vertex, outer_hex_params):
                return False
    return True    

def verify_construction(inner_hex_data, outer_hex_center, outer_hex_side_length, outer_hex_angle_degrees):
    """
    Verify the complete construction meets all requirements.

    Args:
        inner_hex_data: List of [x, y, angle_degrees] for inner hexagons
        outer_hex_center: [x, y] for outer hexagon center
        outer_hex_side_length: Side length of outer hexagon
        outer_hex_angle_degrees: Rotation angle of outer hexagon in degrees

    Returns:
        bool: True if construction is valid, False otherwise
    """
    inner_hex_params_list = [
        (x, y, 1, angle) for x, y, angle in inner_hex_data
    ]
    outer_hex_params = (
        outer_hex_center[0], outer_hex_center[1],
        outer_hex_side_length, outer_hex_angle_degrees
    )

    if outer_hex_side_length < EPSILON:
        return False

    for i in range(len(inner_hex_params_list)):
        for j in range(i + 1, len(inner_hex_params_list)):
            if not hexagons_are_disjoint(inner_hex_params_list[i], inner_hex_params_list[j]):
                return False

    if not all_hexagons_contained(inner_hex_params_list, outer_hex_params):
        return False

    return True

def optimize_construct():
    def get_mtv(vertices1, vertices2):
        """Calculate Minimum Translation Vector to separate two convex polygons."""
        normals1 = get_normals(vertices1)
        normals2 = get_normals(vertices2)
        axes = normals1 + normals2
        
        min_overlap = float('inf')
        mtv_axis = None
        
        for axis in axes:
            min1, max1 = project_polygon(vertices1, axis)
            min2, max2 = project_polygon(vertices2, axis)
            
            # If there's a separating axis, they don't intersect
            if max1 <= min2 + 1e-9 or max2 <= min1 + 1e-9:
                return None 
                
            overlap = min(max1 - min2, max2 - min1)
            
            if overlap < min_overlap:
                min_overlap = overlap
                
                # Determine direction: should point from polygon 2 to polygon 1
                c1x = sum(v[0] for v in vertices1) / len(vertices1)
                c1y = sum(v[1] for v in vertices1) / len(vertices1)
                c2x = sum(v[0] for v in vertices2) / len(vertices2)
                c2y = sum(v[1] for v in vertices2) / len(vertices2)
                
                if (c1x - c2x) * axis[0] + (c1y - c2y) * axis[1] < 0:
                    mtv_axis = (-axis[0], -axis[1])
                else:
                    mtv_axis = axis
                    
        if mtv_axis is None:
            return None
            
        return (mtv_axis[0] * min_overlap, mtv_axis[1] * min_overlap)

    def get_mtv_inside_hex(inner_vertices, outer_center, outer_side, outer_angle):
        """Calculate MTV to push an inner polygon entirely inside the outer hexagon."""
        A = outer_side * math.sqrt(3) / 2
        normals = []
        outer_angle_rad = math.radians(outer_angle)
        
        # Calculate inward normals for the 6 edges of the outer hexagon
        for i in range(6):
            n_out_angle = outer_angle_rad + 2 * math.pi * i / 6 + math.pi / 6
            nx = -math.cos(n_out_angle)
            ny = -math.sin(n_out_angle)
            normals.append((nx, ny))
            
        total_tx = 0
        total_ty = 0
        
        for nx, ny in normals:
            # Project inner vertices onto the inward normal
            min_proj = min((v[0] - outer_center[0]) * nx + (v[1] - outer_center[1]) * ny for v in inner_vertices)
            # The boundary is at -A along the inward normal
            if min_proj < -A:
                depth = -A - min_proj
                total_tx += nx * depth
                total_ty += ny * depth
                
        return (total_tx, total_ty)

    def optimize_layout(seed_centers, inner_angle, outer_angle, S, max_iters=800):
        """Physics-based SAT MTV optimization to pack hexagons tightly."""
        centers = [list(c) for c in seed_centers]
        
        # Center the configuration at the origin initially
        cx = sum(c[0] for c in centers) / len(centers)
        cy = sum(c[1] for c in centers) / len(centers)
        for c in centers:
            c[0] -= cx
            c[1] -= cy
        outer_center = [0.0, 0.0]
        
        lr_start = 0.5
        lr_end = 0.05
        # Add padding to ensure strict disjointness for the verifier
        pad = 1e-5
        
        for it in range(max_iters):
            lr = lr_start - (lr_start - lr_end) * (it / max_iters)
            max_overlap = 0.0
            
            # 1. Resolve overlaps between inner hexagons
            inner_moves = [[0.0, 0.0] for _ in range(11)]
            for i in range(11):
                v1 = hexagon_vertices(centers[i][0], centers[i][1], 1.0 + pad, inner_angle)
                for j in range(i+1, 11):
                    v2 = hexagon_vertices(centers[j][0], centers[j][1], 1.0 + pad, inner_angle)
                    mtv = get_mtv(v1, v2)
                    if mtv is not None:
                        mag = math.hypot(mtv[0], mtv[1])
                        if mag > max_overlap:
                            max_overlap = mag
                        inner_moves[i][0] += mtv[0] * lr
                        inner_moves[i][1] += mtv[1] * lr
                        inner_moves[j][0] -= mtv[0] * lr
                        inner_moves[j][1] -= mtv[1] * lr
                        
            for i in range(11):
                centers[i][0] += inner_moves[i][0]
                centers[i][1] += inner_moves[i][1]
                
            # 2. Resolve boundary violations with the outer hexagon
            outer_moves = [[0.0, 0.0] for _ in range(11)]
            for i in range(11):
                v1 = hexagon_vertices(centers[i][0], centers[i][1], 1.0 + pad, inner_angle)
                mtv_out = get_mtv_inside_hex(v1, outer_center, S - pad, outer_angle)
                mag = math.hypot(mtv_out[0], mtv_out[1])
                if mag > 0:
                    if mag > max_overlap:
                        max_overlap = mag
                    outer_moves[i][0] += mtv_out[0] * lr
                    outer_moves[i][1] += mtv_out[1] * lr
                    
            for i in range(11):
                centers[i][0] += outer_moves[i][0]
                centers[i][1] += outer_moves[i][1]
                
            if max_overlap < 1e-7:
                break
                
        # Final strict verification
        inner_data = [[c[0], c[1], inner_angle] for c in centers]
        if verify_construction(inner_data, outer_center, S, outer_angle):
            return centers, outer_center, True
            
        return centers, outer_center, False

    def generate_seeds():
        """Generate dense honeycomb lattice subsets (4-3-4, 3-4-4, 4-4-3)."""
        seeds = []
        R = math.sqrt(3)
        
        c_434 = []
        for i in range(4): c_434.append((i*R, 0))
        for i in range(3): c_434.append((R/2 + i*R, 1.5))
        for i in range(4): c_434.append((i*R, 3.0))
        
        c_344 = []
        for i in range(3): c_344.append((R/2 + i*R, 0))
        for i in range(4): c_344.append((i*R, 1.5))
        for i in range(4): c_344.append((R/2 + i*R, 3.0))
        
        c_443 = []
        for i in range(4): c_443.append((i*R, 0))
        for i in range(4): c_443.append((R/2 + i*R, 1.5))
        for i in range(3): c_443.append((i*R, 3.0))
        
        base_seeds = [c_434, c_344, c_443]
        
        final_seeds = []
        for centers in base_seeds:
            # Orientation 30 (pointy top)
            final_seeds.append((centers, 30.0))
            # Orientation 0 (flat top)
            flat_centers = [(-y, x) for x, y in centers]
            final_seeds.append((flat_centers, 0.0))
            
        return final_seeds

    # Verification-Guided Bisection Search
    seeds = generate_seeds()
    outer_angles = [float(a) for a in range(0, 60, 5)]
    
    best_S = 4.2
    best_config = None
    
    # Coarse search
    for seed_centers, inner_angle in seeds:
        for outer_angle in outer_angles:
            low = 3.7
            high = best_S
            
            # Quick check to prune unpromising configurations
            current_centers, current_oc, success = optimize_layout(
                seed_centers, inner_angle, outer_angle, high, max_iters=600)
            
            if not success:
                continue
                
            valid_centers = current_centers
            valid_oc = current_oc
            
            # Binary search to find the minimum feasible side length S
            for step in range(15):
                mid = (low + high) / 2.0
                test_centers, test_oc, success = optimize_layout(
                    current_centers, inner_angle, outer_angle, mid, max_iters=800)
                
                if success:
                    high = mid
                    valid_centers = test_centers
                    valid_oc = test_oc
                    # Warm start for the next iteration
                    current_centers = test_centers
                else:
                    low = mid
                    
            # Update best configuration found so far
            if high < best_S:
                best_S = high
                best_config = (valid_centers, inner_angle, outer_angle, high, valid_oc, seed_centers)
                
    if best_config is not None:
        # Micro-Sweep around best outer angle
        _, best_inner_angle, best_outer_angle, _, _, best_seed_centers = best_config
        micro_angles = [best_outer_angle + d * 0.5 for d in range(-4, 5) if d != 0]
        
        for outer_angle in micro_angles:
            low = 3.7
            high = best_S
            
            current_centers, current_oc, success = optimize_layout(
                best_seed_centers, best_inner_angle, outer_angle, high, max_iters=600)
            
            if not success:
                continue
                
            valid_centers = current_centers
            valid_oc = current_oc
            
            for step in range(15):
                mid = (low + high) / 2.0
                test_centers, test_oc, success = optimize_layout(
                    current_centers, best_inner_angle, outer_angle, mid, max_iters=800)
                
                if success:
                    high = mid
                    valid_centers = test_centers
                    valid_oc = test_oc
                    current_centers = test_centers
                else:
                    low = mid
                    
            if high < best_S:
                best_S = high
                best_config = (valid_centers, best_inner_angle, outer_angle, high, valid_oc, best_seed_centers)
                
    if best_config is None:
        # Fallback (should not be reached)
        seed_centers, inner_angle = seeds[0]
        return [[c[0]+10, c[1]+10, inner_angle] for c in seed_centers], [10.0, 10.0], 4.2, 0.0
        
    centers, inner_angle, outer_angle, S, oc, _ = best_config
    
    # Normalize coordinates to ensure x > 0 and y > 0
    min_x = min(c[0] for c in centers) - S
    min_y = min(c[1] for c in centers) - S
    
    shift_x = -min_x + 1.0
    shift_y = -min_y + 1.0
    
    final_inner = [[c[0] + shift_x, c[1] + shift_y, inner_angle] for c in centers]
    final_oc = [oc[0] + shift_x, oc[1] + shift_y]
    
    return final_inner, final_oc, S, outer_angle
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
import math

EPSILON = 1e-9

def hexagon_vertices(center_x, center_y, side_length, angle_degrees):
    """
    Calculate the vertices of a hexagon.

    Args:
        center_x: x-coordinate of hexagon center
        center_y: y-coordinate of hexagon center
        side_length: Length of hexagon side
        angle_degrees: Rotation angle in degrees

    Returns:
        list: List of (x, y) tuples representing the hexagon vertices
    """
    vertices = []
    angle_radians = math.radians(angle_degrees)
    for i in range(6):
        angle = angle_radians + 2 * math.pi * i / 6
        x = center_x + side_length * math.cos(angle)
        y = center_y + side_length * math.sin(angle)
        vertices.append((x, y))
    return vertices

def normalize_vector(v):
    """
    Normalize a 2D vector to unit length.

    Args:
        v: (x, y) tuple representing the vector

    Returns:
        tuple: Normalized (x, y) vector
    """
    magnitude = math.sqrt(v[0]**2 + v[1]**2)
    return (v[0] / magnitude, v[1] / magnitude) if magnitude > EPSILON else (0., 0.)

def get_normals(vertices):
    """
    Calculate normal vectors for all edges of a polygon.

    Args:
        vertices: List of (x, y) tuples representing polygon vertices

    Returns:
        list: List of (x, y) normal vectors
    """
    normals = []
    for i in range(len(vertices)):
        p1 = vertices[i]
        p2 = vertices[(i + 1) % len(vertices)]
        edge = (p2[0] - p1[0], p2[1] - p1[1])
        normal = normalize_vector((-edge[1], edge[0]))
        normals.append(normal)
    return normals

def project_polygon(vertices, axis):
    """
    Project a polygon onto an axis and return the min and max projection values.

    Args:
        vertices: List of (x, y) tuples representing polygon vertices
        axis: (x, y) tuple representing the projection axis

    Returns:
        tuple: (min_projection, max_projection) projection range
    """
    min_proj = float('inf')
    max_proj = float('-inf')
    for vertex in vertices:
        projection = vertex[0] * axis[0] + vertex[1] * axis[1]
        min_proj = min(min_proj, projection)
        max_proj = max(max_proj, projection)
    return min_proj, max_proj

def overlap_1d(min1, max1, min2, max2):
    """
    Check if two 1D intervals overlap (with epsilon tolerance).

    Args:
        min1, max1: Start and end of first interval
        min2, max2: Start and end of second interval

    Returns:
        bool: True if intervals overlap, False otherwise
    """
    return max1 >= min2 - EPSILON and max2 >= min1 - EPSILON

def polygons_intersect(vertices1, vertices2):
    """
    Check if two convex polygons intersect using the Separating Axis Theorem.

    Args:
        vertices1: List of (x, y) tuples for first polygon
        vertices2: List of (x, y) tuples for second polygon

    Returns:
        bool: True if polygons intersect, False otherwise
    """
    normals1 = get_normals(vertices1)
    normals2 = get_normals(vertices2)
    axes = normals1 + normals2

    for axis in axes:
        min1, max1 = project_polygon(vertices1, axis)
        min2, max2 = project_polygon(vertices2, axis)
        if not overlap_1d(min1, max1, min2, max2):
            return False
    return True

def hexagons_are_disjoint(hex1_params, hex2_params):
    """
    Check if two hexagons are disjoint (non-overlapping).

    Args:
        hex1_params: (center_x, center_y, side_length, angle_degrees) for first hexagon
        hex2_params: (center_x, center_y, side_length, angle_degrees) for second hexagon

    Returns:
        bool: True if hexagons are disjoint, False if they intersect
    """   
    hex1_vertices = hexagon_vertices(*hex1_params)
    hex2_vertices = hexagon_vertices(*hex2_params)
    return not polygons_intersect(hex1_vertices, hex2_vertices)

def is_inside_hexagon(point, hex_params):
    """
    Check if a point is inside a hexagon.

    Args:
        point: (x, y) tuple representing the point
        hex_params: (center_x, center_y, side_length, angle_degrees) for the hexagon

    Returns:
        bool: True if point is inside hexagon, False otherwise
    """
    hex_vertices = hexagon_vertices(*hex_params)
    for i in range(len(hex_vertices)):
        p1 = hex_vertices[i]
        p2 = hex_vertices[(i + 1) % len(hex_vertices)]
        edge_vector = (p2[0] - p1[0], p2[1] - p1[1])
        point_vector = (point[0] - p1[0], point[1] - p1[1])
        cross_product = (edge_vector[0] * point_vector[1] - edge_vector[1] * point_vector[0])

        if cross_product < -EPSILON:
            return False

    return True

def all_hexagons_contained(inner_hex_params_list, outer_hex_params):
    """
    Check if all inner hexagons are completely contained within the outer hexagon.

    Args:
        inner_hex_params_list: List of (x, y, side_length, angle_degrees) for inner hexagons
        outer_hex_params: (center_x, center_y, side_length, angle_degrees) for outer hexagon

    Returns:
        bool: True if all inner hexagons are contained, False otherwise
    """
    for inner_hex_params in inner_hex_params_list:
        inner_hex_vertices = hexagon_vertices(*inner_hex_params)
        for vertex in inner_hex_vertices:
            if not is_inside_hexagon(vertex, outer_hex_params):
                return False
    return True    

def verify_construction(inner_hex_data, outer_hex_center, outer_hex_side_length, outer_hex_angle_degrees):
    """
    Verify the complete construction meets all requirements.

    Args:
        inner_hex_data: List of [x, y, angle_degrees] for inner hexagons
        outer_hex_center: [x, y] for outer hexagon center
        outer_hex_side_length: Side length of outer hexagon
        outer_hex_angle_degrees: Rotation angle of outer hexagon in degrees

    Returns:
        bool: True if construction is valid, False otherwise
    """
    inner_hex_params_list = [
        (x, y, 1, angle) for x, y, angle in inner_hex_data
    ]
    outer_hex_params = (
        outer_hex_center[0], outer_hex_center[1],
        outer_hex_side_length, outer_hex_angle_degrees
    )

    if outer_hex_side_length < EPSILON:
        return False

    for i in range(len(inner_hex_params_list)):
        for j in range(i + 1, len(inner_hex_params_list)):
            if not hexagons_are_disjoint(inner_hex_params_list[i], inner_hex_params_list[j]):
                return False

    if not all_hexagons_contained(inner_hex_params_list, outer_hex_params):
        return False

    return True

def optimize_construct():
    def get_mtv(vertices1, vertices2):
        """Calculate Minimum Translation Vector to separate two convex polygons."""
        normals1 = get_normals(vertices1)
        normals2 = get_normals(vertices2)
        axes = normals1 + normals2
        
        min_overlap = float('inf')
        mtv_axis = None
        
        for axis in axes:
            min1, max1 = project_polygon(vertices1, axis)
            min2, max2 = project_polygon(vertices2, axis)
            
            # If there's a separating axis, they don't intersect
            if max1 <= min2 + 1e-9 or max2 <= min1 + 1e-9:
                return None 
                
            overlap = min(max1 - min2, max2 - min1)
            
            if overlap < min_overlap:
                min_overlap = overlap
                
                # Determine direction: should point from polygon 2 to polygon 1
                c1x = sum(v[0] for v in vertices1) / len(vertices1)
                c1y = sum(v[1] for v in vertices1) / len(vertices1)
                c2x = sum(v[0] for v in vertices2) / len(vertices2)
                c2y = sum(v[1] for v in vertices2) / len(vertices2)
                
                if (c1x - c2x) * axis[0] + (c1y - c2y) * axis[1] < 0:
                    mtv_axis = (-axis[0], -axis[1])
                else:
                    mtv_axis = axis
                    
        if mtv_axis is None:
            return None
            
        return (mtv_axis[0] * min_overlap, mtv_axis[1] * min_overlap)

    def get_mtv_inside_hex(inner_vertices, outer_center, outer_side, outer_angle):
        """Calculate MTV to push an inner polygon entirely inside the outer hexagon."""
        A = outer_side * math.sqrt(3) / 2
        normals = []
        outer_angle_rad = math.radians(outer_angle)
        
        # Calculate inward normals for the 6 edges of the outer hexagon
        for i in range(6):
            n_out_angle = outer_angle_rad + 2 * math.pi * i / 6 + math.pi / 6
            nx = -math.cos(n_out_angle)
            ny = -math.sin(n_out_angle)
            normals.append((nx, ny))
            
        total_tx = 0
        total_ty = 0
        
        for nx, ny in normals:
            # Project inner vertices onto the inward normal
            min_proj = min((v[0] - outer_center[0]) * nx + (v[1] - outer_center[1]) * ny for v in inner_vertices)
            # The boundary is at -A along the inward normal
            if min_proj < -A:
                depth = -A - min_proj
                total_tx += nx * depth
                total_ty += ny * depth
                
        return (total_tx, total_ty)

    def optimize_layout(seed_centers, inner_angle, outer_angle, S, max_iters=1200):
        """Physics-based SAT MTV optimization to pack hexagons tightly."""
        centers = [list(c) for c in seed_centers]
        
        # Center the configuration at the origin initially
        cx = sum(c[0] for c in centers) / len(centers)
        cy = sum(c[1] for c in centers) / len(centers)
        for c in centers:
            c[0] -= cx
            c[1] -= cy
        outer_center = [0.0, 0.0]
        
        lr_start = 0.5
        lr_end = 0.05
        # Add padding to ensure strict disjointness for the verifier
        pad = 1e-5
        
        # Precompute vertex offsets to avoid costly trigonometric functions inside the loop
        inner_angle_rad = math.radians(inner_angle)
        v_offsets = []
        for i in range(6):
            angle = inner_angle_rad + 2 * math.pi * i / 6
            v_offsets.append((math.cos(angle) * (1.0 + pad), math.sin(angle) * (1.0 + pad)))
            
        for it in range(max_iters):
            # Cosine annealing learning rate schedule
            lr = lr_end + 0.5 * (lr_start - lr_end) * (1 + math.cos(math.pi * it / max_iters))
            max_overlap = 0.0
            
            # 1. Resolve overlaps between inner hexagons (Gauss-Seidel Constraint Resolution)
            for i in range(11):
                for j in range(i+1, 11):
                    # Dynamically evaluate vertices using precomputed offsets
                    v1 = [(centers[i][0] + dx, centers[i][1] + dy) for dx, dy in v_offsets]
                    v2 = [(centers[j][0] + dx, centers[j][1] + dy) for dx, dy in v_offsets]
                    mtv = get_mtv(v1, v2)
                    if mtv is not None:
                        mag = math.hypot(mtv[0], mtv[1])
                        if mag > max_overlap:
                            max_overlap = mag
                        # In-place, immediate updates applied directly to centers
                        centers[i][0] += mtv[0] * lr
                        centers[i][1] += mtv[1] * lr
                        centers[j][0] -= mtv[0] * lr
                        centers[j][1] -= mtv[1] * lr
                        
            # 2. Resolve boundary violations with the outer hexagon
            for i in range(11):
                v1 = [(centers[i][0] + dx, centers[i][1] + dy) for dx, dy in v_offsets]
                mtv_out = get_mtv_inside_hex(v1, outer_center, S - pad, outer_angle)
                mag = math.hypot(mtv_out[0], mtv_out[1])
                if mag > 0:
                    if mag > max_overlap:
                        max_overlap = mag
                    centers[i][0] += mtv_out[0] * lr
                    centers[i][1] += mtv_out[1] * lr
                    
            if max_overlap < 1e-7:
                break
                
        # Final strict verification
        inner_data = [[c[0], c[1], inner_angle] for c in centers]
        if verify_construction(inner_data, outer_center, S, outer_angle):
            return centers, outer_center, True
            
        return centers, outer_center, False

    def generate_seeds():
        """Generate dense honeycomb lattice subsets (4-3-4, 3-4-4, 4-4-3)."""
        seeds = []
        R = math.sqrt(3)
        
        c_434 = []
        for i in range(4): c_434.append((i*R, 0))
        for i in range(3): c_434.append((R/2 + i*R, 1.5))
        for i in range(4): c_434.append((i*R, 3.0))
        
        c_344 = []
        for i in range(3): c_344.append((R/2 + i*R, 0))
        for i in range(4): c_344.append((i*R, 1.5))
        for i in range(4): c_344.append((R/2 + i*R, 3.0))
        
        c_443 = []
        for i in range(4): c_443.append((i*R, 0))
        for i in range(4): c_443.append((R/2 + i*R, 1.5))
        for i in range(3): c_443.append((i*R, 3.0))
        
        base_seeds = [c_434, c_344, c_443]
        
        final_seeds = []
        for centers in base_seeds:
            # Orientation 30 (pointy top)
            final_seeds.append((centers, 30.0))
            # Orientation 0 (flat top)
            flat_centers = [(-y, x) for x, y in centers]
            final_seeds.append((flat_centers, 0.0))
            
        return final_seeds

    # Verification-Guided Bisection Search
    seeds = generate_seeds()
    outer_angles = [float(a) for a in range(0, 60, 5)]
    
    best_S = 4.2
    best_config = None
    
    # Coarse search
    for seed_centers, inner_angle in seeds:
        for outer_angle in outer_angles:
            low = 3.7
            high = best_S
            
            # Quick check to prune unpromising configurations
            current_centers, current_oc, success = optimize_layout(
                seed_centers, inner_angle, outer_angle, high, max_iters=1200)
            
            if not success:
                continue
                
            valid_centers = current_centers
            valid_oc = current_oc
            
            # Binary search to find the minimum feasible side length S
            for step in range(25):
                mid = (low + high) / 2.0
                test_centers, test_oc, success = optimize_layout(
                    current_centers, inner_angle, outer_angle, mid, max_iters=1200)
                
                if success:
                    high = mid
                    valid_centers = test_centers
                    valid_oc = test_oc
                    # Warm start for the next iteration
                    current_centers = test_centers
                else:
                    low = mid
                    
            # Update best configuration found so far
            if high < best_S:
                best_S = high
                best_config = (valid_centers, inner_angle, outer_angle, high, valid_oc, seed_centers)
                
    if best_config is not None:
        # Micro-Sweep around best outer angle
        _, best_inner_angle, best_outer_angle, _, _, best_seed_centers = best_config
        micro_angles = [best_outer_angle + d * 0.25 for d in range(-8, 9) if d != 0]
        
        for outer_angle in micro_angles:
            low = 3.7
            high = best_S
            
            current_centers, current_oc, success = optimize_layout(
                best_seed_centers, best_inner_angle, outer_angle, high, max_iters=1200)
            
            if not success:
                continue
                
            valid_centers = current_centers
            valid_oc = current_oc
            
            for step in range(25):
                mid = (low + high) / 2.0
                test_centers, test_oc, success = optimize_layout(
                    current_centers, best_inner_angle, outer_angle, mid, max_iters=1200)
                
                if success:
                    high = mid
                    valid_centers = test_centers
                    valid_oc = test_oc
                    current_centers = test_centers
                else:
                    low = mid
                    
            if high < best_S:
                best_S = high
                best_config = (valid_centers, best_inner_angle, outer_angle, high, valid_oc, best_seed_centers)
                
    if best_config is None:
        # Fallback (should not be reached)
        seed_centers, inner_angle = seeds[0]
        return [[c[0]+10, c[1]+10, inner_angle] for c in seed_centers], [10.0, 10.0], 4.2, 0.0
        
    centers, inner_angle, outer_angle, S, oc, _ = best_config
    
    # Normalize coordinates to ensure x > 0 and y > 0
    min_x = min(c[0] for c in centers) - S
    min_y = min(c[1] for c in centers) - S
    
    shift_x = -min_x + 1.0
    shift_y = -min_y + 1.0
    
    final_inner = [[c[0] + shift_x, c[1] + shift_y, inner_angle] for c in centers]
    final_oc = [oc[0] + shift_x, oc[1] + shift_y]
    
    return final_inner, final_oc, S, outer_angle
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
