Use an exact support-function-based outer-hex fitting to co-move the container during relaxation and as a final post-fit, integrated within a binary-search feasibility loop and diversified honeycomb seeding.

- Binary-Search Compaction with Co-Moving Outer Hex Fit: Injecting an exact support-function-based outer-hex fit that optimizes rotation φ and center C both intermittently during relaxation and as a final step eliminates container misalignment slack and reduces the required side length s_req for any fixed inner layout.
- Co-moving outer-container fitting: Updating (C, φ) via periodic outer fitting during the feasibility oracle aligns boundary normals with the current layout, steering boundary corrections toward true tangencies and making smaller S values feasible earlier in the binary search.
- Structured honeycomb seeding for 11-unit-hex packing: Initializing with diverse 1+6+4 seeds using global lattice rotations, ring‑2 orientation patterns (±30°), and mild radial scaling expands contact‑graph coverage and increases the likelihood that relaxation reaches tight, feasible configurations.
- Binary-Search Compaction with Co-Moving Outer Hex Fit (Generation 8): In this run it achieved validity 1.0, outer_hex_side_length 3.9565991435319905, and score 0.9935300133768066, outperforming parent scores 0.9826916339657373 and 0.9825578485307467, indicating the co-moving outer fit contributed to better packing efficiency.
- Co‑Moving Belt–Honeycomb Shrink‑Wrap with Edge‑Alignment and Side‑Normal Relaxation: Aligning the container’s rotation φ and center C via a co‑moving support‑function shrink‑wrap, and making only SAT‑checked boundary micro‑moves—edge‑alignment micro‑rotations and side‑normal boundary pushes—within a feasibility‑bisected search on S consistently reduced the maximal apothem and yielded a verified solution with outer_hex_side_length 4.000000783227126, validity 1.0, and target_ratio 0.9827498075709232. To reuse, periodically re‑fit (C, φ) during relaxation, run SAT‑first pair separation before any push, and accept a step only if the re‑fitted S strictly decreases; keep narrow fallback radial inflation in [1.000, 1.010] (extending to about 1.015–1.020 only if needed) to clear borderline contacts without adding slack. Use a deterministic multi‑start seed bank mixing honeycomb rows, axial rings, and 6+5 belts so contact graphs diversify and boundary hexes arrive pre‑aligned with likely outer‑side normals.

```python
#!/usr/bin/env python3
"""Packing 11 unit hexagons inside the smallest possible regular hexagon.

This implementation follows a hybrid approach:
- Exact polygon geometry (provided utility functions) for intersection and containment checks
- Structured honeycomb seeding (1+6+4 layout variants)
- A feasibility oracle using relaxation: separate intersecting pairs and push boundary-outside vertices inwards
- An outer binary search on the outer hexagon side length S
- New: Co-moving outer-container fitting during relaxation, and a final exact outer fitting
  (rotation + center via support functions with least-squares center and golden-section angle refinement)
"""

import json
import math
import random
from typing import List, Tuple

# EVOLVE_START

# Numerical tolerance
EPSILON = 1e-9


def hexagon_vertices(center_x, center_y, side_length, angle_degrees):
    """
    Calculate the vertices of a hexagon.

    Args:
        center_x: x-coordinate of hexagon center
        center_y: y-coordinate of hexagon center
        side_length: Length of hexagon side (also the circumradius for a regular hexagon)
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
    return (v[0] / magnitude, v[1] / magnitude) if magnitude > EPSILON else (0.0, 0.0)


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
        vertices: List of (x, y) tuples representing the polygon vertices
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


# ---------------------------
# Optimization implementation
# ---------------------------

def rotate_point(x: float, y: float, angle_deg: float) -> Tuple[float, float]:
    """Rotate point (x, y) around origin by angle_deg degrees."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return (x * ca - y * sa, x * sa + y * ca)


def clamp_angle_deg(a: float) -> float:
    """Clamp angle to [0, 360)."""
    a = a % 360.0
    if a < 0:
        a += 360.0
    return a


def build_outer_hex_params(S: float) -> Tuple[float, float, float, float]:
    """Outer hex centered at (S, S), side length S, angle 0."""
    return (S, S, S, 0.0)


def current_vertices(inner: List[List[float]]) -> List[List[Tuple[float, float]]]:
    """Compute vertices for all inner hexagons (side=1)."""
    verts = []
    for x, y, ang in inner:
        verts.append(hexagon_vertices(x, y, 1.0, ang))
    return verts


def verify_list(inner: List[List[float]], S: float) -> bool:
    """Quick wrapper using provided verification utility at center (S,S), angle 0."""
    return verify_construction(inner, [S, S], S, 0.0)


def gen_honeycomb_seed_variants() -> List[List[Tuple[float, float, float]]]:
    """Generate multiple seed layouts (relative to origin) using 1+6+4 honeycomb motifs."""
    seeds = []

    r1 = math.sqrt(3.0)  # center distance for edge-edge neighbors
    r2 = 2.0 * r1

    # Base angles for ring-1 (6 neighbors)
    ring1_dirs = [0, 60, 120, 180, 240, 300]

    # Several choices for the four ring-2 placements (out of 12 possibilities)
    ring2_sets = [
        [0, 60, 180, 240],     # aligned with sides (favoring side contacts)
        [30, 90, 210, 270],    # mid-sides directions
        [0, 120, 180, 300],    # alternate
        [60, 180, 240, 0],     # reordered (equivalent but helps search)
    ]

    # Orientation patterns for ring-2 hexes
    ring2_orient_variants = [
        [0, 0, 0, 0],          # same as center
        [30, 30, 30, 30],      # all +30 deg
        [-30, -30, -30, -30],  # all -30 deg
        [0, 30, 0, 30],        # alternating
        [15, -15, 15, -15],    # mild alternating
    ]

    # Global rotation variants for the entire honeycomb scaffold
    global_rot_variants = [0, 10, 20, 30, 40, 50, 60]

    # Slight radial scaling for ring-2 to explore different compactness
    ring2_scale_variants = [0.94, 0.97, 1.00, 1.03]

    for gro in global_rot_variants:
        for r2_angles in ring2_sets:
            for r2_orients in ring2_orient_variants:
                for r2_scale in ring2_scale_variants:
                    layout = []
                    # Center hex
                    layout.append((0.0, 0.0, 0.0))

                    # Ring-1 hexes
                    for a in ring1_dirs:
                        x, y = rotate_point(r1, 0.0, a + gro)
                        layout.append((x, y, 0.0))

                    # Ring-2 hexes
                    for idx, a in enumerate(r2_angles):
                        rr = r2 * r2_scale
                        x, y = rotate_point(rr, 0.0, a + gro)
                        layout.append((x, y, float(r2_orients[idx])))

                    # Ensure 11 items
                    if len(layout) == 11:
                        seeds.append(layout)

    # Deduplicate simple duplicates by rounding
    uniq = {}
    for L in seeds:
        key = tuple((round(x, 3), round(y, 3), round(a, 1)) for x, y, a in L)
        uniq[key] = L
    return list(uniq.values())


def translate_seed_to_center(seed_layout: List[Tuple[float, float, float]], S: float) -> List[List[float]]:
    """Translate a seed layout so that it's centered at (S, S)."""
    result = []
    for x, y, a in seed_layout:
        result.append([x + S, y + S, clamp_angle_deg(a)])
    return result


# ---------------------------
# Container fitting utilities
# ---------------------------

def _outer_normals(angle_deg: float) -> List[Tuple[float, float]]:
    """
    Compute outward unit normals for a regular hexagon rotated by angle_deg.

    We use a unit-side-length hex at the origin and call get_normals, which returns
    outward normals for a CCW vertex order polygon.
    """
    verts = hexagon_vertices(0.0, 0.0, 1.0, angle_deg)
    return get_normals(verts)


def _projections_bounds(all_vertices: List[Tuple[float, float]], axis: Tuple[float, float]) -> Tuple[float, float]:
    """Return (min_proj, max_proj) of all_vertices onto axis."""
    min_p = float('inf')
    max_p = float('-inf')
    ax, ay = axis
    for (x, y) in all_vertices:
        p = x * ax + y * ay
        if p < min_p:
            min_p = p
        if p > max_p:
            max_p = p
    return min_p, max_p


def _compute_center_and_s_for_phi(inner: List[List[float]], phi: float) -> Tuple[List[float], float]:
    """
    Given an inner layout and a fixed outer rotation phi, compute:
    - center C by least squares from constraints n_k · C = m_k with m_k midrange projections
    - minimal required side length s for that C and phi

    Returns (center, s)
    """
    verts_list = current_vertices(inner)
    all_verts = [v for poly in verts_list for v in poly]
    normals = _outer_normals(phi)

    # Build least-squares A^T A and A^T m for Ax ≈ m, with rows n_k and m_k midranges
    ata_00 = ata_01 = ata_11 = 0.0
    atm_0 = atm_1 = 0.0

    midranges = []
    for k in range(6):
        nx, ny = normals[k]
        mk_min, mk_max = _projections_bounds(all_verts, (nx, ny))
        mk = 0.5 * (mk_min + mk_max)
        midranges.append(mk)
        ata_00 += nx * nx
        ata_01 += nx * ny
        ata_11 += ny * ny
        atm_0 += nx * mk
        atm_1 += ny * mk

    # Solve 2x2 system (A^T A) C = A^T m
    det = ata_00 * ata_11 - ata_01 * ata_01
    if abs(det) < 1e-12:
        # Fallback: use two normals (0,1)
        n0x, n0y = normals[0]
        n1x, n1y = normals[1]
        m0 = midranges[0]
        m1 = midranges[1]
        det2 = n0x * n1y - n0y * n1x
        if abs(det2) < 1e-12:
            # Degenerate; put center near average of vertex positions
            sx = sum(v[0] for v in all_verts) / len(all_verts)
            sy = sum(v[1] for v in all_verts) / len(all_verts)
            Cx, Cy = sx, sy
        else:
            inv2 = (n1y / det2, -n0y / det2, -n1x / det2, n0x / det2)
            Cx = inv2[0] * m0 + inv2[1] * m1
            Cy = inv2[2] * m0 + inv2[3] * m1
    else:
        inv00 = ata_11 / det
        inv01 = -ata_01 / det
        inv11 = ata_00 / det
        Cx = inv00 * atm_0 + inv01 * atm_1
        Cy = inv01 * atm_0 + inv11 * atm_1

    # Compute apothem requirement across all 6 normals
    a_req = 0.0
    for k in range(6):
        nx, ny = normals[k]
        cproj = Cx * nx + Cy * ny
        mk_min, mk_max = _projections_bounds(all_verts, (nx, ny))
        a_k = max(mk_max - cproj, cproj - mk_min)
        if a_k > a_req:
            a_req = a_k

    s_req = (2.0 * a_req) / math.sqrt(3.0)
    return [Cx, Cy], s_req


def _wrap60(phi: float) -> float:
    """Wrap angle to [0, 60) due to hexagon symmetry."""
    phi = phi % 60.0
    if phi < 0:
        phi += 60.0
    return phi


def fit_outer_container(inner: List[List[float]]) -> Tuple[List[float], float, float]:
    """
    Given a fixed inner layout (side length 1), compute the minimal enclosing
    regular hexagon by optimizing its rotation angle and center.

    - Coarse sweep of phi over [0,60) in 5° steps
    - Local golden-section refinement within ±5° of best coarse phi
    - Center via least-squares fit to midranges along six normals
    - Side length from support functions

    Returns:
        center: [cx, cy]
        side_length: s (slightly inflated for robustness)
        angle_deg: φ
    """
    # Coarse scan
    best_phi = 0.0
    best_s = float('inf')
    best_center = [0.0, 0.0]

    for phi in [i * 5.0 for i in range(12)]:  # 0..55 step 5
        c, s = _compute_center_and_s_for_phi(inner, phi)
        if s < best_s:
            best_s = s
            best_center = c
            best_phi = phi

    # Golden-section refinement around best_phi within ±5°
    a = _wrap60(best_phi - 5.0)
    b = _wrap60(best_phi + 5.0)
    # Handle potential wraparound by sampling on unfolded domain
    # We'll refine on an interval in R by choosing base at best_phi-5 and evaluate phi%60 inside
    A = best_phi - 5.0
    B = best_phi + 5.0
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    x1 = B - gr * (B - A)
    x2 = A + gr * (B - A)

    def f(phi_val: float) -> float:
        _, s_local = _compute_center_and_s_for_phi(inner, _wrap60(phi_val))
        return s_local

    f1 = f(x1)
    f2 = f(x2)
    for _ in range(18):  # ~precision within ~1e-3 degrees
        if f1 > f2:
            A = x1
            x1 = x2
            f1 = f2
            x2 = A + gr * (B - A)
            f2 = f(x2)
        else:
            B = x2
            x2 = x1
            f2 = f1
            x1 = B - gr * (B - A)
            f1 = f(x1)

    phi_refined = _wrap60(0.5 * (A + B))
    center_ref, s_ref = _compute_center_and_s_for_phi(inner, phi_refined)

    # Minimal inflation for robustness
    s_ref += 1e-6
    return center_ref, s_ref, phi_refined


def relax_layout(inner: List[List[float]], S: float, max_iters: int = 700, seed_jitter: float = 0.25) -> Tuple[bool, List[List[float]], float, List[float], float]:
    """
    Attempt to make a given layout feasible inside outer hex side length S by relaxation.

    Strategy:
    - Separate intersecting pairs by pushing centers apart along their connecting line
    - For any vertex outside the current working outer hex, push the hex center inward
    - Periodically fit the outer container (angle + center) for current inner layout
    - Early feasibility check uses fitted center/angle with side length S
    - Decaying step sizes and occasional jitter

    Returns:
        ok: feasibility at target S (with fitted center/angle)
        inner: layout
        s_fit: minimal fitted side length for this layout (best seen during relax)
        fit_center: corresponding center (final fit at end)
        fit_angle: corresponding angle (final fit at end)
    """
    random.seed(7)  # deterministic for reproducibility

    # Initial working outer parameters
    work_center = [S, S]
    work_angle = 0.0
    work_side = S

    # Initial small jitter on positions/angles to avoid symmetric stalls
    for k in range(len(inner)):
        inner[k][0] += (random.random() - 0.5) * seed_jitter
        inner[k][1] += (random.random() - 0.5) * seed_jitter
        inner[k][2] = clamp_angle_deg(inner[k][2] + (random.random() - 0.5) * 2.0)

    step_sep = 0.15
    step_wall = 0.12
    ang_step = 0.4  # degrees
    decay = 0.992

    best_s_fit = float('inf')
    best_center_fit = work_center[:]
    best_angle_fit = work_angle

    for it in range(max_iters):
        changed = False

        # Precompute vertices
        verts = current_vertices(inner)

        # Pairwise separation for intersecting hexagons (simple centroid push)
        n = len(inner)
        for i in range(n):
            for j in range(i + 1, n):
                if polygons_intersect(verts[i], verts[j]):
                    xi, yi, ai = inner[i]
                    xj, yj, aj = inner[j]
                    dx = xi - xj
                    dy = yi - yj
                    nx, ny = normalize_vector((dx, dy))
                    if abs(nx) < 1e-12 and abs(ny) < 1e-12:
                        nx, ny = normalize_vector((random.random() - 0.5, random.random() - 0.5))
                    inner[i][0] += nx * step_sep
                    inner[i][1] += ny * step_sep
                    inner[j][0] -= nx * step_sep
                    inner[j][1] -= ny * step_sep
                    inner[i][2] = clamp_angle_deg(ai + ang_step * (random.random() - 0.5))
                    inner[j][2] = clamp_angle_deg(aj + ang_step * (random.random() - 0.5))
                    changed = True

        # Boundary containment push: for any vertex outside, push inward toward working center
        outer_params = (work_center[0], work_center[1], work_side, work_angle)

        # Recompute vertices after separation
        verts = current_vertices(inner)
        for idx in range(n):
            vx_total = 0.0
            vy_total = 0.0
            outside_count = 0
            for v in verts[idx]:
                if not is_inside_hexagon(v, outer_params):
                    outside_count += 1
                    # Push towards working center
                    dirx = work_center[0] - v[0]
                    diry = work_center[1] - v[1]
                    nx, ny = normalize_vector((dirx, diry))
                    vx_total += nx
                    vy_total += ny
            if outside_count > 0:
                inner[idx][0] += (vx_total / max(1, outside_count)) * step_wall
                inner[idx][1] += (vy_total / max(1, outside_count)) * step_wall
                # Slight angular correction
                inner[idx][2] = clamp_angle_deg(inner[idx][2] + (random.random() - 0.5) * ang_step)
                changed = True

        # Periodically re-fit the outer container and use it as co-moving boundary
        if it % 20 == 0 or it == max_iters - 1:
            c_fit, s_fit, phi_fit = fit_outer_container(inner)
            # Update the best fitted stats
            if s_fit < best_s_fit:
                best_s_fit = s_fit
                best_center_fit = c_fit[:]
                best_angle_fit = phi_fit
            # Use the fitted orientation and center as working container
            work_center = c_fit[:]
            work_angle = phi_fit
            # Use a slightly tighter container for boundary feedback
            work_side = min(S, s_fit)

            # Early feasibility check: if it fits in s_fit, then it fits in S with same center/angle
            if verify_construction(inner, work_center, S, work_angle):
                return True, inner, best_s_fit, best_center_fit, best_angle_fit

        # Occasional simulated annealing jitter
        if it % 40 == 0 and it > 0:
            for k in range(n):
                inner[k][0] += (random.random() - 0.5) * (0.10 * step_sep)
                inner[k][1] += (random.random() - 0.5) * (0.10 * step_sep)
                inner[k][2] = clamp_angle_deg(inner[k][2] + (random.random() - 0.5) * (ang_step * 1.5))

        # Decay step sizes
        step_sep *= decay
        step_wall *= decay
        ang_step *= decay

        # If nothing changed and still infeasible, add a tiny jitter
        if not changed and it % 25 == 0:
            for k in range(n):
                inner[k][0] += (random.random() - 0.5) * 0.01
                inner[k][1] += (random.random() - 0.5) * 0.01
                inner[k][2] = clamp_angle_deg(inner[k][2] + (random.random() - 0.5) * 0.05)

    # Final check with last fitted parameters
    if verify_construction(inner, best_center_fit, S, best_angle_fit):
        return True, inner, best_s_fit, best_center_fit, best_angle_fit

    # As a last resort, try the trivial container (S,S,0)
    ok_plain = verify_list(inner, S)
    return ok_plain, inner, best_s_fit, best_center_fit, best_angle_fit


def try_feasible_at_S(S: float, seeds: List[List[Tuple[float, float, float]]]) -> Tuple[bool, List[List[float]], float, List[float], float]:
    """Try to find a feasible layout at a given S by relaxing from multiple seeds.

    Returns:
        ok, layout, s_fit_best, fitted_center, fitted_angle
    """
    seeds_idx = list(range(len(seeds)))
    random.Random(123).shuffle(seeds_idx)

    # Limit to a manageable number of attempts
    attempt_limit = min(80, len(seeds_idx))
    best_s_fit = float('inf')
    best_layout = []
    best_center = [S, S]
    best_angle = 0.0

    for t in range(attempt_limit):
        seed_layout = seeds[seeds_idx[t]]
        # Translate to (S, S)
        inner = translate_seed_to_center(seed_layout, S)
        ok, result, s_fit, c_fit, a_fit = relax_layout(inner, S, max_iters=700, seed_jitter=0.2)
        if s_fit < best_s_fit:
            best_s_fit = s_fit
            best_layout = [row[:] for row in result]
            best_center = c_fit[:]
            best_angle = a_fit
        if ok:
            return True, result, s_fit, c_fit, a_fit

    return False, best_layout, best_s_fit, best_center, best_angle


def binary_search_S(seeds: List[List[Tuple[float, float, float]]], S_low: float, S_high: float, tol: float = 1e-3) -> Tuple[float, List[List[float]]]:
    """
    Monotone binary search on S using feasibility oracle at each S.

    We also use the fitted minimal side length s_fit returned by the oracle to tighten S_high.
    Returns the best feasible S and the corresponding inner layout.
    """
    best_S = None
    best_layout = None

    # Ensure we have a feasible upper bound
    ok_high, layout_high, sfit_high, _, _ = try_feasible_at_S(S_high, seeds)
    if not ok_high:
        # If not feasible even at S_high, increase S_high gradually up to a cap
        grow = S_high
        for _ in range(6):
            grow += 0.35
            ok_high, layout_high, sfit_high, _, _ = try_feasible_at_S(grow, seeds)
            if ok_high:
                S_high = grow
                break
        if not ok_high:
            # Fallback; return current
            return S_high, layout_high

    # Tighten upper bound by the best fit we saw
    if sfit_high < S_high:
        S_high = sfit_high

    best_S = S_high
    best_layout = layout_high

    # If S_low is feasible, update best and lower bound
    ok_low, layout_low, sfit_low, _, _ = try_feasible_at_S(S_low, seeds)
    if ok_low:
        best_S = sfit_low if sfit_low < S_low else S_low
        best_layout = layout_low
        S_high = best_S

    # Binary search loop
    while S_high - S_low > tol:
        S_mid = 0.5 * (S_low + S_high)
        ok_mid, layout_mid, sfit_mid, _, _ = try_feasible_at_S(S_mid, seeds)
        if ok_mid:
            # Tight upper bound by fitted size as well
            S_high = min(S_mid, sfit_mid)
            best_S = S_high
            best_layout = layout_mid
        else:
            S_low = S_mid

    return best_S, best_layout


def final_compaction(S: float, inner: List[List[float]], seeds: List[List[Tuple[float, float, float]]], steps: int = 6) -> Tuple[float, List[List[float]]]:
    """Small greedy compaction: decrement S in tiny steps with quick relaxation."""
    best_S = S
    best_layout = [row[:] for row in inner]
    delta = 0.008
    for _ in range(steps):
        cand_S = max(0.1, best_S - delta)
        # Re-center to new (cand_S, cand_S) by shifting the whole pattern
        shift_x = cand_S - best_S
        shift_y = cand_S - best_S
        cand_layout = []
        for x, y, a in best_layout:
            cand_layout.append([x + shift_x, y + shift_y, a])
        ok, relaxed, _, _, _ = relax_layout(cand_layout, cand_S, max_iters=450, seed_jitter=0.05)
        if ok:
            best_S = cand_S
            best_layout = relaxed
        else:
            break
    return best_S, best_layout


def optimize_construct():
    """
    Main entry: search for a tight packing under the verification constraints.

    Returns:
        inner_hexagons: list of [x, y, angle_degrees] for 11 unit hexagons
        outer_center: [x, y] for outer hexagon center (fitted)
        outer_side_length: side length of the outer hexagon (fitted)
        outer_angle_degrees: rotation angle of the outer hexagon (fitted)
    """
    # Generate structured seeds
    seeds = gen_honeycomb_seed_variants()

    # Outer search bounds for S
    # Start with an ambitious low bound and a conservative high bound
    S_low = 3.80
    S_high = 4.10

    # Binary search for minimal feasible S with co-moving container fitting
    S_best, layout_best = binary_search_S(seeds, S_low, S_high, tol=1e-3)

    # If nothing feasible found (very unlikely), provide a safe fallback
    if not layout_best:
        # Simple grid-like layout far inside a large container to ensure validity
        positions = [
            (0.0, 0.0), (math.sqrt(3), 0.0), (-math.sqrt(3), 0.0),
            (0.0, 2.0), (math.sqrt(3), 2.0), (-math.sqrt(3), 2.0),
            (0.0, -2.0), (math.sqrt(3), -2.0), (-math.sqrt(3), -2.0),
            (2 * math.sqrt(3), 0.0), (-2 * math.sqrt(3), 0.0),
        ]
        S_fallback = 6.0
        inner = [[x + S_fallback, y + S_fallback, 0.0] for (x, y) in positions]
        return inner, [S_fallback, S_fallback], S_fallback, 0.0

    # Final small compaction to squeeze extra margin
    S_final, layout_final = final_compaction(S_best, layout_best, seeds, steps=6)

    # Exact outer fit (rotation + center) for the fixed inner layout with refinement
    center_fit, s_fit, phi_fit = fit_outer_container(layout_final)

    # Numerical robustness: inflate tiny amount if needed, and ensure first-quadrant coordinates
    inflate_attempts = 0
    while not verify_construction(layout_final, center_fit, s_fit, phi_fit) and inflate_attempts < 10:
        s_fit += 5e-6
        inflate_attempts += 1

    # Guarantee positive coordinates (shift entire configuration if needed)
    min_x = min(x for x, _, _ in layout_final)
    min_y = min(y for _, y, _ in layout_final)
    shift_dx = 0.0
    shift_dy = 0.0
    if min_x <= 0.05:
        shift_dx = 0.10 - min_x
    if min_y <= 0.05:
        shift_dy = 0.10 - min_y
    if shift_dx != 0.0 or shift_dy != 0.0:
        for k in range(len(layout_final)):
            layout_final[k][0] += shift_dx
            layout_final[k][1] += shift_dy
        center_fit = [center_fit[0] + shift_dx, center_fit[1] + shift_dy]

    # Verify again after shift
    inflate_attempts2 = 0
    while not verify_construction(layout_final, center_fit, s_fit, phi_fit) and inflate_attempts2 < 10:
        s_fit += 5e-6
        inflate_attempts2 += 1

    # If verification somehow still fails, fallback to the search result container (axis-aligned)
    if not verify_construction(layout_final, center_fit, s_fit, phi_fit):
        robustness = 1e-6
        S_out = S_final + robustness
        inflate_attempts = 0
        while not verify_construction(layout_final, [S_out, S_out], S_out, 0.0) and inflate_attempts < 10:
            S_out += 5e-6
            inflate_attempts += 1
        return layout_final, [S_out, S_out], S_out, 0.0

    # Return fitted container parameters
    return layout_final, center_fit, s_fit, phi_fit

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
"""Optimized candidate for packing 11 unit hexagons in a regular hexagon.

This implementation follows the described algorithmic approach:
- Multiple compact seed layouts (row patterns, axial ring variants, and 6+5 belts).
- For each seed, co-optimizes the outer hexagon's rotation and center by
  exact support-function shrink-wrapping along outer normals.
- Active-normal equalization: tiny inward nudges of boundary-contributing
  hexagons with accept-if-improves checks while preserving strict non-overlap.
- Per-hex micro-rotations for top boundary contributors with accept-if-improves
  and strict non-overlap, including targeted edge-normal alignment.
- Minimal radial inflation fallback using a short bisection to eliminate
  borderline contacts while preserving compactness.
- Selects the best seed, shifts the entire construction to the positive quadrant,
  trims the outer side length with a verify-driven micro-bisection, and returns
  the final inner hexagons and the outer hexagon parameters.

Notes and constraints:
- All inner hexagons have unit side length and may be rotated freely.
- This code uses self-contained geometry operations for internal checks and
  does not modify external verification utilities. The returned result is
  expected to pass the provided verify_construction function, if present.
"""

import json
import math


# EVOLVE_START
def optimize_construct():
    # Geometry constants
    SIDE = 1.0
    SQRT3 = math.sqrt(3.0)
    # Honeycomb spacing for pointy-top orientation (angle 90°):
    STEP_X = SQRT3           # edge-touch horizontal center spacing
    STEP_Y = 1.5             # edge-touch vertical center spacing
    # Numerical cushions
    SMALL = 1e-12
    APOTHEM_TO_SIDE = 2.0 / SQRT3

    # ----------------------------------------------------------------------
    # Local helpers (self-contained; do not modify external utilities)
    # ----------------------------------------------------------------------
    def hex_vertices(center_x, center_y, side_length, angle_deg):
        """Compute the 6 vertices of a regular hexagon (CCW order)."""
        verts = []
        ang = math.radians(angle_deg)
        for i in range(6):
            a = ang + 2.0 * math.pi * i / 6.0
            x = center_x + side_length * math.cos(a)
            y = center_y + side_length * math.sin(a)
            verts.append((x, y))
        return verts

    def normalize(vx, vy):
        """Normalize a 2D vector."""
        m = math.hypot(vx, vy)
        if m <= SMALL:
            return (0.0, 0.0)
        return (vx / m, vy / m)

    def polygon_normals(vertices):
        """Outward normals from a CCW polygon vertex list."""
        normals = []
        n = len(vertices)
        for i in range(n):
            x1, y1 = vertices[i]
            x2, y2 = vertices[(i + 1) % n]
            ex = x2 - x1
            ey = y2 - y1
            # Outward normal for CCW polygon is (-ey, ex)
            nx, ny = normalize(-ey, ex)
            normals.append((nx, ny))
        return normals

    def project_polygon(vertices, axis):
        """Project a polygon onto an axis and return min and max scalar projections."""
        ax, ay = axis
        min_p = float('inf')
        max_p = float('-inf')
        for x, y in vertices:
            p = x * ax + y * ay
            if p < min_p:
                min_p = p
            if p > max_p:
                max_p = p
        return min_p, max_p

    def overlap_1d(min1, max1, min2, max2):
        """Check if two 1D intervals overlap (strict; touching counts as overlap)."""
        eps = 0.0
        return (max1 >= min2 - eps) and (max2 >= min1 - eps)

    def polygons_intersect(vertices1, vertices2):
        """Check if two convex polygons intersect using the Separating Axis Theorem."""
        normals1 = polygon_normals(vertices1)
        normals2 = polygon_normals(vertices2)
        axes = normals1 + normals2

        for axis in axes:
            min1, max1 = project_polygon(vertices1, axis)
            min2, max2 = project_polygon(vertices2, axis)
            if not overlap_1d(min1, max1, min2, max2):
                return False
        return True

    def hexagons_intersect(h1, h2):
        """Check if two unit hexagons (center_x, center_y, angle_deg) intersect."""
        v1 = hex_vertices(h1[0], h1[1], SIDE, h1[2])
        v2 = hex_vertices(h2[0], h2[1], SIDE, h2[2])
        return polygons_intersect(v1, v2)

    def layout_is_disjoint(inner_hexes):
        """Check pairwise non-overlap for a layout."""
        n = len(inner_hexes)
        verts = [hex_vertices(h[0], h[1], SIDE, h[2]) for h in inner_hexes]
        for i in range(n):
            for j in range(i + 1, n):
                if polygons_intersect(verts[i], verts[j]):
                    return False
        return True

    def moved_hex_disjoint(inner_hexes, moved_idx):
        """Check the moved hexagon remains disjoint from others."""
        n = len(inner_hexes)
        vi = hex_vertices(inner_hexes[moved_idx][0], inner_hexes[moved_idx][1], SIDE, inner_hexes[moved_idx][2])
        for j in range(n):
            if j == moved_idx:
                continue
            vj = hex_vertices(inner_hexes[j][0], inner_hexes[j][1], SIDE, inner_hexes[j][2])
            if polygons_intersect(vi, vj):
                return False
        return True

    def outer_normals(angle_deg):
        """Normals of a unit regular hexagon rotated by angle_deg."""
        hv = hex_vertices(0.0, 0.0, 1.0, angle_deg)
        return polygon_normals(hv)

    def all_inner_vertices(inner_hexes):
        """Collect all vertices of inner hexagons and map vertex -> hex index."""
        pts = []
        pt_hex_idx = []
        for hi, (cx, cy, ang) in enumerate(inner_hexes):
            v = hex_vertices(cx, cy, SIDE, ang)
            for p in v:
                pts.append(p)
                pt_hex_idx.append(hi)
        return pts, pt_hex_idx

    def shrinkwrap_apothem(inner_hexes, angle_deg, center_init=None, max_iter=80):
        """Compute minimal apothem A and center for outer hex at angle_deg
        so all inner vertices are inside the outer hex.

        Uses exact support-function via outer normals and subgradient-based
        center adjustment (active-normal equalization).
        """
        normals = outer_normals(angle_deg)
        verts, _ = all_inner_vertices(inner_hexes)

        # Initial center: centroid of inner vertices
        if center_init is None:
            sx = sum(p[0] for p in verts)
            sy = sum(p[1] for p in verts)
            Cx = sx / len(verts)
            Cy = sy / len(verts)
        else:
            Cx, Cy = center_init

        # Helper to compute projections and active set
        def projections(center_x, center_y):
            # s_k = max_v dot(v, n_k) - dot(C, n_k)
            s_list = []
            contributors = []  # store (k, max_vertex_index)
            for k, (nx, ny) in enumerate(normals):
                max_val = -1e100
                max_idx = -1
                cproj = center_x * nx + center_y * ny
                for vi, (vx, vy) in enumerate(verts):
                    val = vx * nx + vy * ny - cproj
                    if val > max_val:
                        max_val = val
                        max_idx = vi
                s_list.append(max_val)
                contributors.append((k, max_idx))
            A = max(s_list)
            # Active normals within tolerance from max
            tol = 1e-12
            active = [i for i, sk in enumerate(s_list) if A - sk <= tol]
            return A, s_list, active, contributors

        # Iterative equalization of center
        A_best, _, active_best, contrib_best = projections(Cx, Cy)
        for _ in range(max_iter):
            A, s_list, active, contrib = projections(Cx, Cy)
            dx = 0.0
            dy = 0.0
            for idx in active:
                nx, ny = normals[idx]
                dx += nx
                dy += ny
            mag = math.hypot(dx, dy)
            if mag <= SMALL:
                break
            dx /= mag
            dy /= mag
            # Backtracking line search
            improved = False
            step = 0.35
            for _ls in range(24):
                nCx = Cx + step * dx
                nCy = Cy + step * dy
                A_try, _, _, _ = projections(nCx, nCy)
                if A_try < A - 1e-14:
                    Cx, Cy = nCx, nCy
                    A_best, _, active_best, contrib_best = A_try, s_list, active, contrib
                    improved = True
                    break
                step *= 0.5
            if not improved:
                break

        # Final check
        A_final, _, _, contributors = projections(Cx, Cy)
        return A_final, (Cx, Cy), contributors, normals

    def adjust_boundary_inward(inner_hexes, angle_deg, passes=4):
        """Active-normal equalization: tiny inward translations of hexagons
        contributing to maximal apothem, accept only if apothem reduces and
        global non-overlap is preserved."""
        best_hexes = [h[:] for h in inner_hexes]
        A0, C0, contrib, normals = shrinkwrap_apothem(best_hexes, angle_deg)
        # Try multiple passes with decaying steps
        step_schedule = [1.5e-3, 9e-4, 6e-4, 3e-4]
        for _pass in range(passes):
            improved_any = False
            # Collect contributing hex indices for current A
            active_hex_ids = set()
            for k, max_vi in contrib:
                active_hex_ids.add(max_vi // 6)  # vertex index to hex index
            for step in step_schedule:
                for hid in list(active_hex_ids):
                    # Aggregate direction: sum of normals that this hex contributes to
                    dirx, diry = 0.0, 0.0
                    count = 0
                    for k, max_vi in contrib:
                        if (max_vi // 6) == hid:
                            nx, ny = normals[k]
                            dirx += nx
                            diry += ny
                            count += 1
                    if count == 0:
                        continue
                    dx, dy = normalize(dirx, diry)
                    if dx == 0.0 and dy == 0.0:
                        continue
                    # Try a short backtracking line-search on this move
                    alpha = step
                    accepted = False
                    for _ls in range(8):
                        trial = [h[:] for h in best_hexes]
                        trial[hid][0] -= alpha * dx
                        trial[hid][1] -= alpha * dy
                        # Preserve strict non-overlap
                        if not moved_hex_disjoint(trial, hid):
                            alpha *= 0.5
                            continue
                        A_try, _, contrib_try, normals_try = shrinkwrap_apothem(trial, angle_deg, center_init=C0)
                        if A_try + 1e-12 < A0:
                            best_hexes = trial
                            A0 = A_try
                            contrib = contrib_try
                            normals = normals_try
                            improved_any = True
                            accepted = True
                            break
                        alpha *= 0.5
                    if accepted:
                        # refresh center estimate
                        _, C0, _, _ = shrinkwrap_apothem(best_hexes, angle_deg, center_init=C0)
            if not improved_any:
                break
        return best_hexes

    def micro_rotate_top_contributors(inner_hexes, angle_deg, attempts=3):
        """Try tiny per-hex micro-rotations on boundary contributors; ensure non-overlap."""
        best_hexes = [h[:] for h in inner_hexes]
        A0, _, contrib, _ = shrinkwrap_apothem(best_hexes, angle_deg)
        # Count contributions per hex
        count = {}
        for k, v_idx in contrib:
            hid = v_idx // 6
            count[hid] = count.get(hid, 0) + 1
        # Sort hexes by contribution count
        candidates = sorted(count.keys(), key=lambda h: -count[h])
        # Rotation deltas to try (small first to avoid breaking contacts)
        deltas = [0.25, -0.25, 0.18, -0.18, 0.12, -0.12, 0.08, -0.08]
        for _ in range(attempts):
            improved_any = False
            for hid in candidates[:6]:
                for d in deltas:
                    trial = [h[:] for h in best_hexes]
                    trial[hid][2] += d
                    # Preserve strict non-overlap
                    if not moved_hex_disjoint(trial, hid):
                        continue
                    A_try, _, _, _ = shrinkwrap_apothem(trial, angle_deg)
                    if A_try + 1e-12 < A0:
                        best_hexes = trial
                        A0 = A_try
                        improved_any = True
                        break
            if not improved_any:
                break
            # Re-rank after improvements
            A0, _, contrib, _ = shrinkwrap_apothem(best_hexes, angle_deg)
            count = {}
            for k, v_idx in contrib:
                hh = v_idx // 6
                count[hh] = count.get(hh, 0) + 1
            candidates = sorted(count.keys(), key=lambda h: -count[h])
        return best_hexes

    def micro_rotate_alignment(inner_hexes, angle_deg, rounds=2, max_step_deg=0.5):
        """Targeted alignment: rotate boundary hexes so an edge outward normal
        aligns with active outer normal(s). Accept only if apothem decreases."""
        best = [h[:] for h in inner_hexes]
        A0, _, contrib, onormals = shrinkwrap_apothem(best, angle_deg)
        for _ in range(rounds):
            improved_any = False
            # Build per-hex target rotation based on active contacts
            per_hex_targets = {}
            per_hex_weights = {}
            # Map from vertex index to hex index
            for k, v_idx in contrib:
                hid = v_idx // 6
                # For this hex, compute its edge normals
                hv = hex_vertices(best[hid][0], best[hid][1], SIDE, best[hid][2])
                hnorms = polygon_normals(hv)
                # Active outer normal
                onx, ony = onormals[k]
                # Choose hex edge normal closest to outer normal
                best_dot = -1e100
                best_hn = (0.0, 0.0)
                for hn in hnorms:
                    dot = hn[0] * onx + hn[1] * ony
                    if dot > best_dot:
                        best_dot = dot
                        best_hn = hn
                # Rotation angle to align best_hn to outer normal
                dot = max(-1.0, min(1.0, best_hn[0] * onx + best_hn[1] * ony))
                cross = best_hn[0] * ony - best_hn[1] * onx
                delta_rad = math.atan2(cross, dot)
                delta_deg = math.degrees(delta_rad)
                # Accumulate per hex
                per_hex_targets[hid] = per_hex_targets.get(hid, 0.0) + delta_deg
                per_hex_weights[hid] = per_hex_weights.get(hid, 0.0) + 1.0

            # Try rotating hexes toward their average target by a capped fraction
            # Prioritize hexes with more contacts
            order = sorted(per_hex_weights.keys(), key=lambda h: -per_hex_weights[h])
            for hid in order[:7]:
                avg_target = per_hex_targets[hid] / max(1.0, per_hex_weights[hid])
                # Cap rotation step
                step = max(-max_step_deg, min(max_step_deg, 0.6 * avg_target))
                if abs(step) < 1e-6:
                    continue
                # Backtracking rotation
                alpha = step
                for _ls in range(6):
                    trial = [h[:] for h in best]
                    trial[hid][2] += alpha
                    if not moved_hex_disjoint(trial, hid):
                        alpha *= 0.5
                        continue
                    A_try, _, _, _ = shrinkwrap_apothem(trial, angle_deg)
                    if A_try + 1e-12 < A0:
                        best = trial
                        A0 = A_try
                        improved_any = True
                        break
                    alpha *= 0.5
            if not improved_any:
                break
            # Refresh contributors for next round
            A0, _, contrib, onormals = shrinkwrap_apothem(best, angle_deg)
        return best

    def sweep_outer_angle(inner_hexes):
        """Coarse-to-fine sweep of outer rotation angle in [0°, 60°) with golden refinement."""
        def eval_phi(phi, center_hint=None):
            while phi < 0.0:
                phi += 60.0
            while phi >= 60.0:
                phi -= 60.0
            A, C, _, _ = shrinkwrap_apothem(inner_hexes, phi, center_init=center_hint)
            return A, C

        # Coarse sweep at 0.5° to locate a good bracket
        best_phi = 0.0
        best_A = 1e100
        best_C = (0.0, 0.0)
        for i in range(121):
            phi = float(i) * 0.5
            A, C = eval_phi(phi)
            if A < best_A:
                best_A = A
                best_phi = phi
                best_C = C

        # Golden-section refinement within +/- 2.0° around best
        left = best_phi - 2.0
        right = best_phi + 2.0
        gr = (math.sqrt(5.0) - 1.0) / 2.0
        c = right - gr * (right - left)
        d = left + gr * (right - left)
        Ac, Cc = eval_phi(c, center_hint=best_C)
        Ad, Cd = eval_phi(d, center_hint=best_C)
        for _ in range(22):
            if Ac < Ad:
                right = d
                d = c
                Ad, Cd = Ac, Cc
                c = right - gr * (right - left)
                Ac, Cc = eval_phi(c, center_hint=best_C)
            else:
                left = c
                c = d
                Ac, Cc = Ad, Cd
                d = left + gr * (right - left)
                Ad, Cd = eval_phi(d, center_hint=best_C)

        # Pick better of c, d
        if Ac < Ad:
            best_phi_local = c
            best_A_local = Ac
            best_C_local = Cc
        else:
            best_phi_local = d
            best_A_local = Ad
            best_C_local = Cd

        # Micro polish around best at fine steps
        fine_candidates = [best_phi_local + dd for dd in [-0.8, -0.6, -0.4, -0.2, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2, 0.4, 0.6, 0.8]]
        for phi in fine_candidates:
            A, C = eval_phi(phi, center_hint=best_C_local)
            if A < best_A_local:
                best_A_local = A
                best_phi_local = phi
                best_C_local = C

        # Wrap phi into [0, 60)
        while best_phi_local < 0.0:
            best_phi_local += 60.0
        while best_phi_local >= 60.0:
            best_phi_local -= 60.0

        return best_phi_local, best_C_local, best_A_local

    def inflate_layout(layout, factor=1.001, about_centroid=True):
        """Radially inflate layout to convert edge-contacts to tiny gaps."""
        if about_centroid and layout:
            cx = sum(p[0] for p in layout) / len(layout)
            cy = sum(p[1] for p in layout) / len(layout)
        else:
            cx, cy = 0.0, 0.0
        inflated = []
        for x, y, ang in layout:
            dx = x - cx
            dy = y - cy
            inflated.append([cx + dx * factor, cy + dy * factor, ang])
        return inflated

    def ensure_disjoint(layout):
        """Ensure strict non-overlap via minimal radial inflation using short bisection."""
        if layout_is_disjoint(layout):
            return [h[:] for h in layout]
        # Find an upper bound factor
        lo = 1.0
        hi = 1.008
        inflated = inflate_layout(layout, factor=hi, about_centroid=True)
        tries = 0
        while not layout_is_disjoint(inflated) and tries < 24:
            hi *= 1.015
            inflated = inflate_layout(layout, factor=hi, about_centroid=True)
            tries += 1
        # Bisection to find smallest passing factor
        for _ in range(22):
            mid = 0.5 * (lo + hi)
            inflated = inflate_layout(layout, factor=mid, about_centroid=True)
            if layout_is_disjoint(inflated):
                hi = mid
            else:
                lo = mid
        return inflate_layout(layout, factor=hi, about_centroid=True)

    def rotate_point(x, y, angle_deg):
        """Rotate a point (x,y) around origin by angle_deg."""
        a = math.radians(angle_deg)
        ca, sa = math.cos(a), math.sin(a)
        return (x * ca - y * sa, x * sa + y * ca)

    # ----------------------------------------------------------------------
    # Seed generation
    # ----------------------------------------------------------------------
    def row_seed(row_counts, angle_deg, pre_rot=0.0):
        """Generate a honeycomb row seed (pointy-top spacing) with optional global pre-rotation."""
        rows = len(row_counts)
        mid = (rows - 1) / 2.0
        centers = []
        for i, cnt in enumerate(row_counts):
            y = (i - mid) * STEP_Y
            if cnt == 4:
                xs = [(-1.5 * STEP_X), (-0.5 * STEP_X), (0.5 * STEP_X), (1.5 * STEP_X)]
            elif cnt == 3:
                xs = [(-1.0 * STEP_X), 0.0, (1.0 * STEP_X)]
            else:
                xs = [STEP_X * (j - (cnt - 1) / 2.0) for j in range(cnt)]
            for x in xs:
                rx, ry = rotate_point(x, y, pre_rot)
                centers.append([rx, ry, angle_deg + pre_rot])
        return centers

    def ring_seed(angle_deg, variant=0):
        """Generate axial ring seed: center + 6 neighbors + 4 second ring."""
        centers = []
        # Center
        centers.append([0.0, 0.0, angle_deg])
        # First ring at radius r1 (grid neighbor distance)
        r1 = SQRT3
        first_angles = [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]
        for a in first_angles:
            rad = math.radians(a)
            centers.append([r1 * math.cos(rad), r1 * math.sin(rad), angle_deg])
        # Second ring subset of 6 at radius r2
        r2 = 2.0 * SQRT3
        variants = [
            [0, 120, 240, 300],
            [60, 180, 300, 0],
            [30, 90, 210, 330],
        ]
        sel = variants[variant % len(variants)]
        for a in sel:
            rad = math.radians(a)
            centers.append([r2 * math.cos(rad), r2 * math.sin(rad), angle_deg])
        return centers[:11]

    def belt_seed(angle_deg, variant=0):
        """Generate a 6+5 belt: two interleaving rows of 6 and 5 centers.

        Variants:
          - variant%2 selects which row is on top (6 on top vs 5 on top)
          - variant//2 selects a small global pre-rotation for the belt: Γ ∈ {0, ±10°, ±15°}
        """
        centers = []
        six_on_top = (variant % 2 == 0)
        rot_idx = (variant // 2) % 5
        rot_angles = [0.0, 10.0, -10.0, 15.0, -15.0]
        gamma = rot_angles[rot_idx]

        # Base y positions for the two rows (symmetric about origin)
        y_top = +0.75 * STEP_Y
        y_bot = -0.75 * STEP_Y

        # X positions for rows
        xs6 = [(-2.5 * STEP_X), (-1.5 * STEP_X), (-0.5 * STEP_X), (0.5 * STEP_X), (1.5 * STEP_X), (2.5 * STEP_X)]
        xs5 = [(-2.0 * STEP_X), (-1.0 * STEP_X), (0.0 * STEP_X), (1.0 * STEP_X), (2.0 * STEP_X)]

        # Build rows before rotation
        if six_on_top:
            rowA = [(x, y_top) for x in xs6]
            rowB = [(x, y_bot) for x in xs5]
        else:
            rowA = [(x, y_top) for x in xs5]
            rowB = [(x, y_bot) for x in xs6]

        # Apply global rotation gamma to centers and add small per-hex orientation tweaks
        tweak = [0.0, 3.0, -3.0, 4.0, -4.0, 0.0]
        for idx, (x, y) in enumerate(rowA):
            rx, ry = rotate_point(x, y, gamma)
            t = tweak[idx % len(tweak)]
            centers.append([rx, ry, angle_deg + gamma + t * 0.2])
        for idx, (x, y) in enumerate(rowB):
            rx, ry = rotate_point(x, y, gamma)
            t = tweak[idx % len(tweak)]
            centers.append([rx, ry, angle_deg + gamma - t * 0.2])

        return centers[:11]

    # Build seed bank
    seeds = []
    # Row seeds with diversified orders and orientations and pre-rotations
    for pre in [0.0, 2.0, -2.0]:
        seeds.append(row_seed([4, 3, 4], 90.0, pre_rot=pre))
        seeds.append(row_seed([3, 4, 4], 90.0, pre_rot=pre))
        seeds.append(row_seed([4, 4, 3], 90.0, pre_rot=pre))
        seeds.append(row_seed([4, 3, 4], 30.0, pre_rot=pre))
        seeds.append(row_seed([3, 4, 4], 30.0, pre_rot=pre))
        seeds.append(row_seed([4, 4, 3], 30.0, pre_rot=pre))
    # Ring seeds (three variants), two orientations, with tiny pre-rotations
    for v in range(3):
        for pre in [0.0, 2.0, -2.0]:
            base = ring_seed(90.0, variant=v)
            base2 = ring_seed(30.0, variant=v)
            # Apply pre-rotation to centers and orientations
            def apply_pre(layout, pre_rot):
                out = []
                for x, y, ang in layout:
                    rx, ry = rotate_point(x, y, pre_rot)
                    out.append([rx, ry, ang + pre_rot])
                return out
            seeds.append(apply_pre(base, pre))
            seeds.append(apply_pre(base2, pre))
    # Belt seeds: multiple variants, two orientations
    for v in range(10):
        seeds.append(belt_seed(90.0, variant=v))
        seeds.append(belt_seed(30.0, variant=v))

    # ----------------------------------------------------------------------
    # Evaluate seeds with shrinkwrap + equalization + micro-rotation; pick best
    # ----------------------------------------------------------------------
    best_pack = None
    best_phi = 0.0
    best_center = (0.0, 0.0)
    best_A = 1e100

    for raw_seed in seeds:
        # Ensure disjointness: minimal inflation by bisection only if needed
        seed = ensure_disjoint(raw_seed)
        if not layout_is_disjoint(seed):
            continue

        # Initial shrinkwrap fit on seed
        phi, C, A = sweep_outer_angle(seed)

        # Iterative co-moving boundary shaping and micro-rotations with re-fit
        current = [h[:] for h in seed]
        cur_phi, cur_C, cur_A = phi, C, A
        for _cycle in range(4):
            improved = False
            # Translation shaping passes (side-normal inward pushes)
            shaped = adjust_boundary_inward(current, cur_phi, passes=5)
            shaped = ensure_disjoint(shaped)
            # Alignment micro-rotations (edge-normal alignment)
            shaped = micro_rotate_alignment(shaped, cur_phi, rounds=2, max_step_deg=0.6)
            shaped = ensure_disjoint(shaped)
            # Additional tiny randomized rotations for top contributors
            shaped = micro_rotate_top_contributors(shaped, cur_phi, attempts=2)
            shaped = ensure_disjoint(shaped)
            # Refit outer parameters on improved configuration
            nphi, nC, nA = sweep_outer_angle(shaped)
            if nA + 1e-12 < cur_A:
                current, cur_phi, cur_C, cur_A = shaped, nphi, nC, nA
                improved = True
            if not improved:
                break

        # Keep the best feasible
        if cur_A < best_A and layout_is_disjoint(current):
            best_A = cur_A
            best_pack = current
            best_phi = cur_phi
            best_center = cur_C

    # Fallback if no seed succeeded (should not happen)
    if best_pack is None:
        best_pack = row_seed([4, 3, 4], 90.0)
        best_phi, best_center, best_A = sweep_outer_angle(best_pack)

    # Compute minimal side length from apothem
    s0 = APOTHEM_TO_SIDE * best_A

    # Shift to positive quadrant with a cushion
    min_cx = min(h[0] for h in best_pack)
    min_cy = min(h[1] for h in best_pack)
    margin = 0.75
    shift_x = (margin - min_cx) if min_cx < margin else 0.0
    shift_y = (margin - min_cy) if min_cy < margin else 0.0
    inner_hexagons = []
    for (x, y, ang) in best_pack:
        inner_hexagons.append([x + shift_x, y + shift_y, ang])
    outer_center = [best_center[0] + shift_x, best_center[1] + shift_y]
    outer_angle_degrees = best_phi

    # Determine if external verifier is available in this runtime
    verifier_available = ('verify_construction' in globals()
                          and callable(globals().get('verify_construction')))  # type: ignore

    if verifier_available:
        # Use verification-based small bisection around s0 to remove slack
        def try_verify(inner_hex_data, outer_c, side_len, outer_angle):
            # Call the provided verifier directly
            return verify_construction(inner_hex_data, outer_c, side_len, outer_angle)  # type: ignore

        s_low = max(0.99 * s0, 0.0)
        s_high = s0
        # Ensure we have a passing high bound; if not, slightly inflate until it passes
        if not try_verify(inner_hexagons, outer_center, s_high, outer_angle_degrees):
            mul = 1.0002
            tries = 0
            while not try_verify(inner_hexagons, outer_center, s_high, outer_angle_degrees) and tries < 50:
                s_high *= mul
                mul *= 1.002
                tries += 1
            s_low = 0.995 * s_high
        else:
            # Try to shrink below s0 a bit, in case of numerical slack
            if try_verify(inner_hexagons, outer_center, 0.999999 * s0, outer_angle_degrees):
                s_low = 0.999999 * s0

        # Bisection to find smallest passing side length within a narrow band
        for _ in range(36):
            mid = 0.5 * (s_low + s_high)
            if try_verify(inner_hexagons, outer_center, mid, outer_angle_degrees):
                s_high = mid
            else:
                s_low = mid

        outer_side_length = s_high * (1.0 + 2e-8)
    else:
        # If no verifier is available here, return the analytically minimal side length s0
        # computed from shrink-wrap apothem, which guarantees containment.
        outer_side_length = s0

    return inner_hexagons, outer_center, outer_side_length, outer_angle_degrees
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
