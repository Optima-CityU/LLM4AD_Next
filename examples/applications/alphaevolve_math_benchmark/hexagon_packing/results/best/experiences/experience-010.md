This run combined SAT-first micro-repair, support-function shrink-wrap with orientation search, and a final verify-driven bisection of the outer side length, achieving validity 1.0 with outer_hex_side_length ≈ 4.000000003445561.

- SAT-Equalized Micro-Repair Shrink-Wrap with Verify-Bisected Outer Hex: The algorithm first enforces disjointness via SAT-guided micro-repair and uses only tightly bounded global inflation by SAT-bisection if repairs stall, then shrink-wraps the outer hexagon with a support-function optimizer that refines the outer orientation and center, and finally bisects the outer side length against verify_construction to remove residual padding; this sequence yielded a valid construction (validity 1.0) with outer_hex_side_length ≈ 4.000000003445561 in this run, indicating that verify-bisected sizing is an effective last step to convert analytical fits into minimal feasible containers worth reusing in future designs.

```python
#!/usr/bin/env python3
"""Optimized packing of 11 unit regular hexagons inside a minimal regular hexagon.

This implementation fuses compact honeycomb and axial-ring seeds with a precise
support-function shrink-wrap of the outer hexagon. It uses SAT-based micro-repair
prior to any global expansion, and then minimal radial inflation (bisection) only
if needed to ensure strict non-overlap of inner hexagons. A coarse-to-fine outer-
rotation minimization is performed with an active-normal (subgradient) center
correction. A new support-aware equalization stage directly targets the maximal
directional supports by tiny inward nudges of the contributing boundary hexagons,
and focused per-hex micro-rotations further polish the silhouette.

Key elements:
- Seeds:
  - Honeycomb rows: 4–3–4, 3–4–4, and 4–4–3 (pointy-top spacing).
  - Axial-ring: center + 6 ring-1 neighbors + 4 ring-2 positions from curated
    balanced 4-of-6 subsets (3 rotated variants).
  - For each seed shape, two inner-orientation patterns are tried: 90° (pointy-top)
    and 30° (flat-top), to diversify boundary silhouettes.
- Disjointness enforcement:
  - SAT micro-repair first: minimally split offending pairs along their center axis
    via equal-and-opposite, backtracked nudges, guarded against creating new overlaps.
  - Minimal radial inflation (tight bisection about the centroid) applied only
    if micro-repair leaves residual intersections. Window expands conservatively.
- Outer shrink-wrap:
  - For a rotation φ ∈ [0,60°), fit the center via opposite-normal LS, then polish
    with active-normal subgradient steps. Map apothem to side length precisely.
  - Coarse φ sweep, golden-section refinement, and a micro-sweep around the best φ.
- Support-aware equalization (new):
  - Identify active outward normals and the hexes whose vertices realize the maximal
    apothem. Apply tiny, SAT-verified inward nudges along those normals using a
    backtracking line search. Re-optimize φ locally after successful moves.
- Per-hex micro-rotations:
  - For the top boundary contributors (from active normals), try small ±Δ° rotations
    (decaying schedule), accepting only if they strictly reduce the container and
    preserve SAT-disjointness.
- Verify-bisected outer side length (new):
  - After analytical shrink-wrap returns a feasible outer hexagon, run a short
    bisection directly against verify_construction to trim residual padding and
    return the smallest passing side length with a tiny cushion.

Conservative numeric padding and final shift to x>0, y>0 are applied.

The construction uses the provided SAT helper utilities unchanged and returns
a solution that is verified by verify_construction.
"""

import json
import math

# Bring in the utility functions as specified (kept unchanged in behavior).
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

    magnitude = math.sqrt(v[0] ** 2 + v[1] ** 2)
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
        vertices: List of (x, y) tuples representing polygon vertices
        axis: (x, y) tuple representing the projection axis

    Returns:
        tuple: (min_projection, max_projection) projection range
    """

    min_proj = float("inf")
    max_proj = float("-inf")
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


# EVOLVE_START

def optimize_construct():
    """
    Compute positions and orientations for 11 unit hexagons and the enclosing regular hexagon.

    Returns:
        inner_hexagons: list of [x, y, angle_degrees] for 11 unit hexagons
        outer_center: [cx, cy]
        outer_side_length: side length L of the outer regular hexagon
        outer_angle_degrees: rotation angle phi of the outer hexagon in degrees
    """
    # Geometry constants for unit hexagons (side length = 1)
    size = 1.0
    sqrt3 = math.sqrt(3.0)

    # Local vertex generator (for speed). Verification will use the global one.
    def hexagon_vertices_local(cx, cy, side_length, angle_degrees):
        verts = []
        base = math.radians(angle_degrees)
        for i in range(6):
            ang = base + 2.0 * math.pi * i / 6.0
            x = cx + side_length * math.cos(ang)
            y = cy + side_length * math.sin(ang)
            verts.append((x, y))
        return verts

    # Build honeycomb row seeds (exact contact centers for pointy-top hexes).
    # Within-row spacing: step_x = sqrt(3), vertical row spacing: step_y = 1.5
    step_x = sqrt3
    step_y = 1.5

    def build_honeycomb(seed_kind):
        centers = []
        if seed_kind == "434":
            # y rows: +step_y (4), 0 (3), -step_y (4)
            for k in (-1.5, -0.5, 0.5, 1.5):
                centers.append((k * step_x, +step_y))
            for k in (-1.0, 0.0, 1.0):
                centers.append((k * step_x, 0.0))
            for k in (-1.5, -0.5, 0.5, 1.5):
                centers.append((k * step_x, -step_y))
        elif seed_kind == "344":
            for k in (-1.0, 0.0, 1.0):
                centers.append((k * step_x, +step_y))
            for k in (-1.5, -0.5, 0.5, 1.5):
                centers.append((k * step_x, 0.0))
            for k in (-1.5, -0.5, 0.5, 1.5):
                centers.append((k * step_x, -step_y))
        elif seed_kind == "443":
            # y rows: +step_y (4), 0 (4), -step_y (3)
            for k in (-1.5, -0.5, 0.5, 1.5):
                centers.append((k * step_x, +step_y))
            for k in (-1.5, -0.5, 0.5, 1.5):
                centers.append((k * step_x, 0.0))
            for k in (-1.0, 0.0, 1.0):
                centers.append((k * step_x, -step_y))
        else:
            raise ValueError("Unknown honeycomb seed kind")
        return centers

    # Axial ring seeds using axial coordinates (q, r) -> 2D coordinates
    # For pointy-top grid: basis vectors
    b1 = (sqrt3, 0.0)
    b2 = (sqrt3 / 2.0, 1.5)

    def axial_to_xy(q, r):
        return (q * b1[0] + r * b2[0], q * b1[1] + r * b2[1])

    # Ring-1 axial offsets (distance 1)
    ring1_axial = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
    # Ring-2 "axis" positions (distance 2 along axial axes)
    ring2_axes = [(2, 0), (2, -2), (0, -2), (-2, 0), (-2, 2), (0, 2)]

    # Curated 4-of-6 subsets for ring2 (omit two opposite). Provide 3 rotations.
    ring2_subsets = [
        [0, 1, 3, 4],
        [1, 2, 4, 5],
        [2, 3, 5, 0],
    ]

    def build_axial_ring(subset_idx):
        centers = []
        # center
        centers.append((0.0, 0.0))
        # ring1
        for (q, r) in ring1_axial:
            centers.append(axial_to_xy(q, r))
        # ring2 subset
        subset = ring2_subsets[subset_idx % len(ring2_subsets)]
        for idx in subset:
            q, r = ring2_axes[idx]
            centers.append(axial_to_xy(q, r))
        # Should be 1 + 6 + 4 = 11
        return centers

    # Layout representation: list of (cx, cy, angle_deg)
    def layout_from_centers_and_angle(centers, angle_deg):
        return [(cx, cy, angle_deg) for (cx, cy) in centers]

    # SAT-disjointness check for a layout
    def layout_is_disjoint(layout):
        n = len(layout)
        for i in range(n):
            for j in range(i + 1, n):
                if not hexagons_are_disjoint(
                    (layout[i][0], layout[i][1], 1.0, layout[i][2]),
                    (layout[j][0], layout[j][1], 1.0, layout[j][2]),
                ):
                    return False
        return True

    # Minimal radial inflation about the centroid using bisection and SAT check.
    def find_minimal_radial_scale_layout(layout):
        mx = sum(p[0] for p in layout) / len(layout)
        my = sum(p[1] for p in layout) / len(layout)

        def scaled_layout(s):
            return [(mx + s * (x - mx), my + s * (y - my), ang) for (x, y, ang) in layout]

        lo = 1.0
        hi = 1.003  # very tight inflation window
        max_hi = 1.02
        it_guard = 0
        while not layout_is_disjoint(scaled_layout(hi)) and hi < max_hi and it_guard < 12:
            hi *= 1.5
            it_guard += 1

        # If even at hi it's not disjoint, return hi as best effort (rare)
        if not layout_is_disjoint(scaled_layout(hi)):
            return scaled_layout(hi)

        # Bisection
        for _ in range(32):
            mid = 0.5 * (lo + hi)
            if layout_is_disjoint(scaled_layout(mid)):
                hi = mid
            else:
                lo = mid
        return scaled_layout(hi)

    # SAT-guided micro-repair: small, equal-and-opposite nudges along the line
    # between offending centers (feasible-only; capped iterations).
    def micro_repair_layout(layout):
        layout = layout[:]  # copy
        n = len(layout)
        max_outer_its = 8
        step0 = 1e-4
        for _ in range(max_outer_its):
            changed = False
            for i in range(n):
                for j in range(i + 1, n):
                    hi = (layout[i][0], layout[i][1], 1.0, layout[i][2])
                    hj = (layout[j][0], layout[j][1], 1.0, layout[j][2])
                    if hexagons_are_disjoint(hi, hj):
                        continue
                    # Move apart along center-center vector
                    dx = layout[j][0] - layout[i][0]
                    dy = layout[j][1] - layout[i][1]
                    dn = math.hypot(dx, dy)
                    if dn < 1e-12:
                        dx, dy = 1.0, 0.0
                        dn = 1.0
                    ux, uy = dx / dn, dy / dn
                    step = step0
                    moved = False
                    for _try in range(7):
                        ci_new = (layout[i][0] - step * ux, layout[i][1] - step * uy, layout[i][2])
                        cj_new = (layout[j][0] + step * ux, layout[j][1] + step * uy, layout[j][2])
                        old_i, old_j = layout[i], layout[j]
                        layout[i], layout[j] = ci_new, cj_new
                        ok_pair = hexagons_are_disjoint(
                            (ci_new[0], ci_new[1], 1.0, ci_new[2]),
                            (cj_new[0], cj_new[1], 1.0, cj_new[2]),
                        )
                        ok_global = True
                        if ok_pair:
                            for k in range(n):
                                if k == i or k == j:
                                    continue
                                if not hexagons_are_disjoint(
                                    (layout[i][0], layout[i][1], 1.0, layout[i][2]),
                                    (layout[k][0], layout[k][1], 1.0, layout[k][2]),
                                ):
                                    ok_global = False
                                    break
                                if not hexagons_are_disjoint(
                                    (layout[j][0], layout[j][1], 1.0, layout[j][2]),
                                    (layout[k][0], layout[k][1], 1.0, layout[k][2]),
                                ):
                                    ok_global = False
                                    break
                        if ok_pair and ok_global:
                            changed = True
                            moved = True
                            break
                        else:
                            layout[i], layout[j] = old_i, old_j
                            step *= 0.5
                    if not moved:
                        pass
            if not changed:
                break
        return layout

    # Enforce strict disjointness: micro-repair first, then minimal radial inflation if needed.
    def enforce_disjointness(layout):
        lay = micro_repair_layout(layout)
        if not layout_is_disjoint(lay):
            lay = find_minimal_radial_scale_layout(lay)
        return lay

    # Outward unit normals for a regular hexagon rotated by phi degrees.
    def outer_normals(phi_deg):
        base = math.radians(phi_deg + 30.0)
        ns = []
        for k in range(6):
            a = base + k * (math.pi / 3.0)
            ns.append((math.cos(a), math.sin(a)))
        return ns

    # Compute maximum support along a given unit normal for a set of points (vertices).
    def max_support_along_n(verts, n):
        nx, ny = n
        m = -1e30
        for (vx, vy) in verts:
            s = vx * nx + vy * ny
            if s > m:
                m = s
        return m

    # For fixed center C and rotation phi, compute minimal outer side length L.
    def minimal_L_for_phi_C(verts, Cx, Cy, phi_deg):
        ns = outer_normals(phi_deg)
        A = -1e30
        for (nx, ny) in ns:
            m = -1e30
            for (vx, vy) in verts:
                s = vx * nx + vy * ny
                if s > m:
                    m = s
            need = m - (nx * Cx + ny * Cy)
            if need > A:
                A = need
        # Apothem = A; side length L = 2/sqrt(3) * apothem
        return (2.0 / sqrt3) * A

    # LS center for fixed phi via opposite-normal pair equalization.
    def ls_center_for_phi(verts, phi_deg):
        ns = outer_normals(phi_deg)
        pair_indices = [(0, 3), (1, 4), (2, 5)]
        w = []
        basis_ns = []
        for (i, j) in pair_indices:
            ni = ns[i]
            nj = ns[j]
            mi = max_support_along_n(verts, ni)
            mj = max_support_along_n(verts, nj)
            wj = 0.5 * (mi - mj)
            w.append(wj)
            basis_ns.append(ni)
        # Solve (sum n n^T) C = sum w n
        a11 = a12 = a22 = 0.0
        b1 = b2 = 0.0
        for k in range(3):
            nx, ny = basis_ns[k]
            a11 += nx * nx
            a12 += nx * ny
            a22 += ny * ny
            b1 += w[k] * nx
            b2 += w[k] * ny
        det = a11 * a22 - a12 * a12
        if abs(det) < 1e-18:
            return 0.0, 0.0
        inv11 = a22 / det
        inv12 = -a12 / det
        inv22 = a11 / det
        Cx = inv11 * b1 + inv12 * b2
        Cy = inv12 * b1 + inv22 * b2
        return Cx, Cy

    # Active-normal subgradient correction on C with short backtracking line search.
    def active_axis_correction(verts, Cx0, Cy0, phi_deg, micro_steps=5, eta0=0.25):
        Cx, Cy = Cx0, Cy0
        ns = outer_normals(phi_deg)

        def compute_needs_and_A(Cx, Cy):
            needs = []
            A = -1e30
            for (nx, ny) in ns:
                m = -1e30
                for (vx, vy) in verts:
                    s = vx * nx + vy * ny
                    if s > m:
                        m = s
                need = m - (nx * Cx + ny * Cy)
                needs.append(need)
                if need > A:
                    A = need
            return needs, A

        needs, A = compute_needs_and_A(Cx, Cy)
        for _ in range(micro_steps):
            tolA = 1e-12
            active = []
            for idx, need in enumerate(needs):
                if A - need <= tolA:
                    active.append(ns[idx])
            if not active:
                break
            gx = sum(n[0] for n in active)
            gy = sum(n[1] for n in active)
            gnorm = math.hypot(gx, gy)
            if gnorm < 1e-18:
                break
            gx /= gnorm
            gy /= gnorm
            eta = eta0
            accepted = False
            while eta > 1e-6:
                Cx_try = Cx + eta * gx
                Cy_try = Cy + eta * gy
                _, A_try = compute_needs_and_A(Cx_try, Cy_try)
                if A_try < A - 1e-12:
                    Cx, Cy = Cx_try, Cy_try
                    needs, A = compute_needs_and_A(Cx, Cy)
                    accepted = True
                    break
                eta *= 0.5
            if not accepted:
                break
        return Cx, Cy

    # Helpers to work with layout
    def build_inner_vertices(layout):
        flat = []
        per_hex = []
        for (cx, cy, ang) in layout:
            verts = hexagon_vertices_local(cx, cy, size, ang)
            per_hex.append(verts)
            flat.extend(verts)
        return flat, per_hex

    # Active normals and contributing hex indices at (C, phi)
    def active_normals_and_hexes(Cx, Cy, phi_deg, per_hex):
        ns = outer_normals(phi_deg)
        needs = []
        contrib_hex = []
        A = -1e30
        for (nx, ny) in ns:
            best_s = -1e30
            best_h = -1
            for h_idx, verts in enumerate(per_hex):
                for (vx, vy) in verts:
                    s = vx * nx + vy * ny
                    if s > best_s:
                        best_s = s
                        best_h = h_idx
            need = best_s - (nx * Cx + ny * Cy)
            needs.append(need)
            contrib_hex.append(best_h)
            if need > A:
                A = need
        tolA = 1e-12
        act = []
        for i, need in enumerate(needs):
            if A - need <= tolA:
                act.append((ns[i], contrib_hex[i]))
        return act

    # Equalize-max stage: tiny inward nudges for contributors on active normals, local φ re-fit.
    def equalize_max(layout, best_phi, best_Cx, best_Cy, best_L):
        inner_verts, per_hex_verts = build_inner_vertices(layout)

        # Attempt several equalization passes
        passes = 3
        improved_global = False
        for _ in range(passes):
            improved = False
            act = active_normals_and_hexes(best_Cx, best_Cy, best_phi, per_hex_verts)
            # Unique contributors with ordering
            seen = set()
            ordered = []
            for (nvec, hidx) in act:
                if hidx not in seen:
                    seen.add(hidx)
                    ordered.append((nvec, hidx))
            # Try tiny inward moves for these contributors
            for (nvec, hidx) in ordered:
                nx, ny = nvec
                delta = 8e-4
                accept_move = False
                for _try in range(6):
                    cx_old, cy_old, ang_old = layout[hidx]
                    cx_new = cx_old - delta * nx
                    cy_new = cy_old - delta * ny
                    layout[hidx] = (cx_new, cy_new, ang_old)
                    # Check global disjointness after move
                    if layout_is_disjoint(layout):
                        # Rebuild vertices and re-fit around best_phi neighborhood
                        inner_verts_try, per_hex_verts_try = build_inner_vertices(layout)

                        def wrap_phi(p):
                            p_mod = p % 60.0
                            if p_mod < 0.0:
                                p_mod += 60.0
                            return p_mod

                        best_local_L = float("inf")
                        best_local_phi = best_phi
                        best_local_Cx = best_Cx
                        best_local_Cy = best_Cy
                        # Small micro-sweep ±0.25° with 0.05° steps
                        for ii in range(-5, 6):
                            phi_try = wrap_phi(best_phi + ii * 0.05)
                            Cx_ls, Cy_ls = ls_center_for_phi(inner_verts_try, phi_try)
                            Cx_opt, Cy_opt = active_axis_correction(inner_verts_try, Cx_ls, Cy_ls, phi_deg=phi_try, micro_steps=5, eta0=0.3)
                            L_try = minimal_L_for_phi_C(inner_verts_try, Cx_opt, Cy_opt, phi_try)
                            if L_try < best_local_L:
                                best_local_L = L_try
                                best_local_phi = phi_try
                                best_local_Cx = Cx_opt
                                best_local_Cy = Cy_opt
                        # Accept only if improves global best
                        if best_local_L + 1e-12 < best_L:
                            best_L = best_local_L
                            best_phi = best_local_phi
                            best_Cx = best_local_Cx
                            best_Cy = best_local_Cy
                            inner_verts, per_hex_verts = inner_verts_try, per_hex_verts_try
                            accept_move = True
                            improved = True
                            improved_global = True
                            break
                    # Revert and try smaller delta
                    layout[hidx] = (cx_old, cy_old, ang_old)
                    delta *= 0.5
                if not accept_move:
                    # No change for this hex
                    pass
            if not improved:
                break
        return layout, best_phi, best_Cx, best_Cy, best_L, improved_global

    # Per-hex micro-rotations for top contributors; accept if strictly improves.
    def micro_rotations(layout, best_phi, best_Cx, best_Cy, best_L):
        inner_verts, per_hex_verts = build_inner_vertices(layout)
        act = active_normals_and_hexes(best_Cx, best_Cy, best_phi, per_hex_verts)
        # Rank contributors by frequency (appearances across active normals)
        freq = {}
        for (_, hidx) in act:
            freq[hidx] = freq.get(hidx, 0) + 1
        # Order by decreasing frequency
        ordered = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
        # Try small rotations for top contributors
        max_targets = min(5, len(ordered))
        improved_any = False

        def wrap_phi(p):
            p_mod = p % 60.0
            if p_mod < 0.0:
                p_mod += 60.0
            return p_mod

        for t in range(max_targets):
            hidx = ordered[t][0]
            cx, cy, ang = layout[hidx]
            for delta_ang in (0.25, -0.25, 0.15, -0.15, 0.08, -0.08):
                ang_new = ang + delta_ang
                old = layout[hidx]
                layout[hidx] = (cx, cy, ang_new)
                if layout_is_disjoint(layout):
                    inner_verts_try, _ = build_inner_vertices(layout)
                    # Tight local φ refinement
                    best_local_L = float("inf")
                    best_local_phi = best_phi
                    best_local_Cx = best_Cx
                    best_local_Cy = best_Cy
                    for ii in range(-5, 6):  # ±0.25° with 0.05° steps
                        phi_try = wrap_phi(best_phi + ii * 0.05)
                        Cx_ls, Cy_ls = ls_center_for_phi(inner_verts_try, phi_try)
                        Cx_opt, Cy_opt = active_axis_correction(inner_verts_try, Cx_ls, Cy_ls, phi_deg=phi_try, micro_steps=5, eta0=0.3)
                        L_try = minimal_L_for_phi_C(inner_verts_try, Cx_opt, Cy_opt, phi_try)
                        if L_try < best_local_L:
                            best_local_L = L_try
                            best_local_phi = phi_try
                            best_local_Cx = Cx_opt
                            best_local_Cy = Cy_opt
                    if best_local_L + 1e-12 < best_L:
                        best_L = best_local_L
                        best_phi = best_local_phi
                        best_Cx = best_local_Cx
                        best_Cy = best_local_Cy
                        improved_any = True
                        # Keep the rotation and continue to next target
                        break
                # Revert rotation
                layout[hidx] = old
        return layout, best_phi, best_Cx, best_Cy, best_L, improved_any

    # Evaluate one seed: enforce disjointness, shrink-wrap outer hex, equalize-max and micro-rotations.
    def evaluate_seed(initial_layout):
        # 1) Strict disjointness: micro-repair first, then minimal radial inflation if needed
        layout = enforce_disjointness(initial_layout)

        # 2) Build vertices
        inner_verts, per_hex_verts = build_inner_vertices(layout)

        # 3) Outer shrink-wrap: φ sweep + golden refine + micro-sweep
        best_phi = None
        best_Cx = None
        best_Cy = None
        best_L = float("inf")

        # Coarse sweep over φ ∈ [0, 60)
        dphi = 0.5
        num_steps = int(60.0 / dphi + 1e-9)

        def wrap_phi(p):
            p_mod = p % 60.0
            if p_mod < 0.0:
                p_mod += 60.0
            return p_mod

        for i in range(num_steps):
            phi = i * dphi
            Cx_ls, Cy_ls = ls_center_for_phi(inner_verts, phi)
            Cx_opt, Cy_opt = active_axis_correction(inner_verts, Cx_ls, Cy_ls, phi_deg=phi, micro_steps=6, eta0=0.3)
            L = minimal_L_for_phi_C(inner_verts, Cx_opt, Cy_opt, phi)
            if L < best_L:
                best_L = L
                best_phi = phi
                best_Cx = Cx_opt
                best_Cy = Cy_opt

        # Golden-section refinement within [best-dphi, best+dphi], with wrapping
        def eval_phi(phi_val):
            phi_wrapped = wrap_phi(phi_val)
            Cx_ls, Cy_ls = ls_center_for_phi(inner_verts, phi_wrapped)
            Cx_opt, Cy_opt = active_axis_correction(inner_verts, Cx_ls, Cy_ls, phi_deg=phi_wrapped, micro_steps=6, eta0=0.3)
            L = minimal_L_for_phi_C(inner_verts, Cx_opt, Cy_opt, phi_wrapped)
            return L, phi_wrapped, Cx_opt, Cy_opt

        left = best_phi - dphi
        right = best_phi + dphi
        gr = (math.sqrt(5.0) - 1.0) / 2.0
        c = right - gr * (right - left)
        d = left + gr * (right - left)

        Lc, phic, Cxc, Cyc = eval_phi(c)
        Ld, phid, Cxd, Cyd = eval_phi(d)
        if Lc < best_L:
            best_L, best_phi, best_Cx, best_Cy = Lc, phic, Cxc, Cyc
        if Ld < best_L:
            best_L, best_phi, best_Cx, best_Cy = Ld, phid, Cxd, Cyd

        for _ in range(12):
            if Lc < Ld:
                right = d
                d = c
                Ld, phid, Cxd, Cyd = Lc, phic, Cxc, Cyc
                c = right - gr * (right - left)
                Lc, phic, Cxc, Cyc = eval_phi(c)
                if Lc < best_L:
                    best_L, best_phi, best_Cx, best_Cy = Lc, phic, Cxc, Cyc
            else:
                left = c
                c = d
                Lc, phic, Cxc, Cyc = Ld, phid, Cxd, Cyd
                d = left + gr * (right - left)
                Ld, phid, Cxd, Cyd = eval_phi(d)
                if Ld < best_L:
                    best_L, best_phi, best_Cx, best_Cy = Ld, phid, Cxd, Cyd

        # Micro-sweep polishing around best_phi
        sweep_span = 0.6
        sweep_step = 0.05
        i_min = int(math.floor((-sweep_span) / sweep_step))
        i_max = int(math.ceil((sweep_span) / sweep_step))
        for ii in range(i_min, i_max + 1):
            phi_try = best_phi + ii * sweep_step
            phi_wrap = wrap_phi(phi_try)
            Cx_ls, Cy_ls = ls_center_for_phi(inner_verts, phi_wrap)
            Cx_opt, Cy_opt = active_axis_correction(inner_verts, Cx_ls, Cy_ls, phi_deg=phi_wrap, micro_steps=6, eta0=0.3)
            L = minimal_L_for_phi_C(inner_verts, Cx_opt, Cy_opt, phi_wrap)
            if L < best_L:
                best_L = L
                best_phi = phi_wrap
                best_Cx = Cx_opt
                best_Cy = Cy_opt

        # 4) Support-aware equalization and focused micro-rotations
        # Equalize-max passes
        layout, best_phi, best_Cx, best_Cy, best_L, _ = equalize_max(layout, best_phi, best_Cx, best_Cy, best_L)
        # Micro-rotations for top contributors
        layout, best_phi, best_Cx, best_Cy, best_L, _ = micro_rotations(layout, best_phi, best_Cx, best_Cy, best_L)

        return {
            "layout": layout,
            "best_phi": best_phi,
            "best_Cx": best_Cx,
            "best_Cy": best_Cy,
            "best_L": best_L,
        }

    # Build a diversified seed list with orientations {90°, 30°}
    seeds = []
    for kind in ("434", "344", "443"):
        centers = build_honeycomb(kind)
        for ang in (90.0, 30.0):
            seeds.append(layout_from_centers_and_angle(centers, ang))
    for subset_idx in range(3):
        centers = build_axial_ring(subset_idx)
        for ang in (90.0, 30.0):
            seeds.append(layout_from_centers_and_angle(centers, ang))

    # Evaluate all seeds, pick the best configuration
    best_result = None
    for layout in seeds:
        res = evaluate_seed(layout)
        if (best_result is None) or (res["best_L"] < best_result["best_L"]):
            best_result = res

    layout = best_result["layout"]
    best_phi = best_result["best_phi"]
    best_Cx = best_result["best_Cx"]
    best_Cy = best_result["best_Cy"]
    best_L = best_result["best_L"]

    # Verify-driven bisection on the outer side length to remove residual padding.
    # We keep layout, center, and outer orientation fixed and search the smallest L
    # that passes verify_construction.
    def minimize_L_via_verify(layout_fixed, Cx, Cy, phi_deg, L_start):
        inner_hex_data = [[x, y, ang] for (x, y, ang) in layout_fixed]
        # Ensure high bound passes; if not, gently inflate until it does (very rare).
        hi = max(L_start, 1e-6)
        outer_center = [Cx, Cy]
        if not verify_construction(inner_hex_data, outer_center, hi, phi_deg):
            # Inflate very slightly until verification passes or cap iterations.
            it = 0
            while it < 30 and not verify_construction(inner_hex_data, outer_center, hi, phi_deg):
                hi *= 1.0005
                it += 1
            if not verify_construction(inner_hex_data, outer_center, hi, phi_deg):
                # Fallback: small absolute bump
                hi += 5e-6
        # Lower bound that surely fails (0 side length)
        lo = 0.0
        # Bisection to minimize L
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            if verify_construction(inner_hex_data, outer_center, mid, phi_deg):
                hi = mid
            else:
                lo = mid
        # Tiny cushion to protect against very borderline numeric changes
        return hi + 2e-9

    # Apply verify-bisection on unshifted coordinates
    best_L = minimize_L_via_verify(layout, best_Cx, best_Cy, best_phi, best_L)

    # Shift all coordinates into x>0, y>0 space with a small margin.
    min_cx = min(x for (x, y, ang) in layout)
    min_cy = min(y for (x, y, ang) in layout)
    min_all_x = min(min_cx, best_Cx)
    min_all_y = min(min_cy, best_Cy)
    shift_x = (-min_all_x + 0.3) if min_all_x <= 0.0 else 0.0
    shift_y = (-min_all_y + 0.3) if min_all_y <= 0.0 else 0.0

    layout_shifted = [(x + shift_x, y + shift_y, ang) for (x, y, ang) in layout]
    Cx_shifted = best_Cx + shift_x
    Cy_shifted = best_Cy + shift_y

    # Assemble return values
    inner_hexagons = [[x, y, ang] for (x, y, ang) in layout_shifted]
    outer_center = [Cx_shifted, Cy_shifted]
    outer_side_length = best_L
    outer_angle_degrees = best_phi

    # Final sanity with the provided verifier (defensive; not strictly required by interface)
    # If verification fails due to extreme numeric sensitivity, slightly increase safety margin.
    if not verify_construction(inner_hexagons, outer_center, outer_side_length, outer_angle_degrees):
        outer_side_length += 5e-6

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
