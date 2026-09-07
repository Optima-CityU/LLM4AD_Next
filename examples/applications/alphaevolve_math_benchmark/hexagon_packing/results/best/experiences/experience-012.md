Insight on design choices and reusable strategies that yielded a valid, tight packing under SAT-based verification.

- SAT-guided honeycomb island search for 11-unit hex packing: Start from a compact honeycomb seed (rows at y ∈ {0, 1.5, 3.0} with alternating x offsets 0 and √3/2) and repair overlaps using SAT-guided minimal-translation vectors; this narrows the search and quickly produces valid configurations (validity 1.0 in 0.2168321760254912 s). For each candidate, analytically minimize the outer bound via directional supports and the Chebyshev center, using R_out = t/(√3/2), and refine outer orientation Φ with 60° symmetry scanning plus golden-section search to tighten the container beyond box-like bounds. The observed outer_hex_side_length of 4.015000000000002 and score 0.979078455790784 support this hybrid’s effectiveness; future designs should reuse the honeycomb seed + SAT repair + support-function outer minimization trio under disjointness-and-containment constraints, with small epsilon padding to prevent re-contact.
- SAT-guided (Separating Axis Theorem) feasibility solver: Using SAT normals and penetration depths to drive minimal, geometry-aware position and rotation corrections removed overlaps and enforced boundary containment with tighter clearances than center-distance heuristics.
- Bisection on the outer side length: Nesting the local feasibility solver inside a binary search on the outer hexagon side length s systematically pushed toward the smallest feasible s while verify_construction served as a strict feasibility gate.
- 4-4-3, 3-4-4, and 4-3-4 honeycomb seeds with deterministic tightening and annealed jitters: Seeding multiple honeycomb layouts at outer orientations in {0°, ~30°} and inner rotations in {0°, ±10°, ±30°}, then alternating deterministic tightening with small random jitters, broke symmetry plateaus and enabled feasible packings reported below 3.931 (around 3.90–3.92 depending on seeds).
- Honeycomb-Feasible-Search with SAT-guided Bisection: Success was driven by casting minimization as feasibility at a fixed outer side length S and embedding a Separating Axis Theorem–guided local repair loop inside a bisection over S, which rapidly tightens S once any feasible placement is found. The repair loop leverages polygons_intersect and get_normals to compute separating axes, applies minimal opposing translations to resolve overlaps, and nudges only boundary hexagons with small rotations (clipped to about ±8°) to better align with the outer boundary while preserving honeycomb adjacency. Multiple honeycomb seed templates plus slight jitters (≈0.02 units/0.5°) diversify starts, and small soft margins (≈1e-5–5e-4) stabilize containment and overlap checks before final verification. In this run the construction passed verification (validity 1.0) with S ≈ 3.9943 and score ≈ 0.9842, indicating the feasibility-first plus SAT-guided adjustments pattern is robust and worth reusing.

```python
#!/usr/bin/env python3
"""Hybrid honeycomb + support-function optimizer for packing 11 unit hexagons.

This script constructs a dense packing of 11 unit regular hexagons (side length = 1)
inside a minimal regular hexagonal container. It uses:
- A compact honeycomb seed layout (several variants).
- A support-function based analytical minimization of the outer hex apothem for
  each candidate inner configuration and outer orientation.
- A small safety spacing between inner hexagons to ensure disjointness under SAT.

Notes:
- We keep all inner centers in the positive quadrant (x > 0, y > 0).
- We do not call verify_construction here; the harness will, using the provided utilities.
- We avoid modifying any verification utilities and rely on the geometric reasoning to satisfy them.

Returned format:
- inner_hexagons: list of [x, y, angle_degrees] for each of the 11 unit hexagons (side length fixed as 1).
- outer_center: [x, y] of the enclosing hexagon center.
- outer_side_length: side length of the enclosing hexagon (this is also its circumradius).
- outer_angle_degrees: rotation angle of the enclosing hexagon.
"""

import json
import math
from typing import List, Tuple


# EVOLVE_START
def optimize_construct():
    # -------------------------------
    # Geometry helpers (local only)
    # -------------------------------
    def hexagon_vertices(center_x: float, center_y: float, side_length: float, angle_degrees: float):
        """Compute vertices of a regular hexagon. Here side_length is the true side length
        (for a regular hex, side length equals its circumradius)."""
        vertices = []
        angle_radians = math.radians(angle_degrees)
        for i in range(6):
            ang = angle_radians + 2.0 * math.pi * i / 6.0
            x = center_x + side_length * math.cos(ang)
            y = center_y + side_length * math.sin(ang)
            vertices.append((x, y))
        return vertices

    def outer_normals_for_phi(phi_deg: float) -> List[Tuple[float, float]]:
        """Return the six outward unit normals of a regular hexagon rotated by phi_deg.

        If the hexagon's vertices are at angles (phi + k*60°), then the edge normals are at
        angles (phi + 30° + k*60°), k=0..5.
        """
        phi = math.radians(phi_deg + 30.0)
        normals = []
        for k in range(6):
            ang = phi + k * (math.pi / 3.0)  # 60° step
            normals.append((math.cos(ang), math.sin(ang)))
        return normals

    def support_extents(vertices: List[Tuple[float, float]], normals: List[Tuple[float, float]]):
        """For each normal n_k, compute min and max support of vertices along n_k."""
        m = []
        M = []
        for nx, ny in normals:
            mn = float('inf')
            mx = float('-inf')
            for (x, y) in vertices:
                proj = x * nx + y * ny
                if proj < mn:
                    mn = proj
                if proj > mx:
                    mx = proj
            m.append(mn)
            M.append(mx)
        return m, M

    def minimize_apothem_for_phi(vertices: List[Tuple[float, float]], phi_deg: float):
        """Given a fixed inner-hex vertices set and outer orientation phi,
        find the best center C that minimizes the apothem t.
        Returns (best_t, best_center_x, best_center_y).

        We solve min_C t(C) where
            t(C) = max_k max(M_k - n_k·C, n_k·C - m_k).
        Using a simple subgradient descent on C.
        """
        normals = outer_normals_for_phi(phi_deg)
        m_list, M_list = support_extents(vertices, normals)

        # Initialize center at the centroid of all inner vertices
        cx = sum(v[0] for v in vertices) / len(vertices)
        cy = sum(v[1] for v in vertices) / len(vertices)

        def t_and_subgrad(cx: float, cy: float):
            """Compute t(C) and a subgradient wrt C."""
            worst_val = float('-inf')
            worst_grad = (0.0, 0.0)

            for k, (n) in enumerate(normals):
                nx, ny = n
                dot = cx * nx + cy * ny
                # Two half-plane constraints around the strip [m_k, M_k]
                a = M_list[k] - dot  # needs to be <= t
                b = dot - m_list[k]  # needs to be <= t
                if a >= b:
                    val = a
                    grad = (-nx, -ny)  # gradient of a = -(n)
                else:
                    val = b
                    grad = (nx, ny)    # gradient of b = +n

                if val > worst_val:
                    worst_val = val
                    worst_grad = grad
            return worst_val, worst_grad

        # Subgradient descent parameters
        best_cx, best_cy = cx, cy
        best_t, _ = t_and_subgrad(cx, cy)
        step0 = 0.25  # initial step
        for it in range(250):
            t_val, grad = t_and_subgrad(cx, cy)
            if t_val < best_t:
                best_t = t_val
                best_cx, best_cy = cx, cy
            # Diminishing step
            alpha = step0 / math.sqrt(1.0 + it)
            gx, gy = grad
            # Update center
            cx -= alpha * gx
            cy -= alpha * gy

        return best_t, best_cx, best_cy

    def minimal_outer_for_config(inner_hexes: List[Tuple[float, float, float]]):
        """For a fixed set of inner hexagons (each as (x,y,angle_deg)), compute the
        enclosing outer hexagon with minimal side length by scanning outer orientations.

        Returns (outer_side_length, outer_center_x, outer_center_y, outer_angle_deg).
        """
        # Collect all inner vertices
        verts = []
        for (x, y, ang) in inner_hexes:
            verts.extend(hexagon_vertices(x, y, 1.0, ang))

        # Search outer orientation phi in [0, 60) degrees
        best = None
        # Coarse scan 0.5° steps for better orientation alignment
        for phi in [i * 0.5 for i in range(0, 120)]:  # 0..59.5 step 0.5
            t, cx, cy = minimize_apothem_for_phi(verts, phi)
            # Convert apothem to side length (circumradius): R = t / cos(30°) = 2t / sqrt(3)
            R = 2.0 * t / math.sqrt(3.0)
            if (best is None) or (R < best[0]):
                best = (R, cx, cy, phi)

        return best  # type: ignore

    # -------------------------------
    # Honeycomb seed generation
    # -------------------------------
    # We place 11 unit hexagons (side length = 1) in a compact honeycomb:
    # Use pointy-top orientation for inner hexes (angle ≈ 90 degrees), which yields a standard grid:
    #   horizontal step = sqrt(3) * (1 + eps)
    #   vertical step   = 1.5 * (1 + eps)
    # We add a small spacing epsilon to ensure "disjoint" (non-overlapping) for the SAT test.
    eps_spacing = 5e-3
    step_x = math.sqrt(3.0) * (1.0 + eps_spacing)
    step_y = 1.5 * (1.0 + eps_spacing)

    def build_rows(counts: List[int], base_x: float, base_y: float, angle_deg: float) -> List[Tuple[float, float, float]]:
        """Build 3 honeycomb rows with alternating horizontal offsets.
        counts = [c0, c1, c2] is number of hexes per row.
        Row 0 offset = 0, Row 1 offset = step_x/2, Row 2 offset = 0 (alternating).
        """
        inner = []
        for row in range(3):
            c = counts[row]
            y = base_y + row * step_y
            offset = (step_x / 2.0) if (row % 2 == 1) else 0.0
            # Place columns to the right from base_x
            for i in range(c):
                x = base_x + offset + i * step_x
                # Ensure positive quadrant by using positive base_x/base_y
                inner.append((x, y, angle_deg))
        return inner

    # Candidate seeds (3-row honeycomb variants):
    # - variant A: 4-3-4 (balanced top/bottom)
    # - variant B: 3-4-4
    # - variant C: 4-4-3
    # Also try two inner orientations: 90° (pointy top) and 30° (flat top-ish).
    seeds = []
    base_x = 1.0
    base_y = 1.0
    for counts in ([4, 3, 4], [3, 4, 4], [4, 4, 3]):
        for inner_ang in (90.0, 30.0):
            seeds.append(build_rows(counts, base_x, base_y, inner_ang))

    # Among seeds, compute the minimal enclosing outer hex.
    best_outer = None
    best_inner = None
    for inner in seeds:
        out = minimal_outer_for_config(inner)
        if (best_outer is None) or (out[0] < best_outer[0]):
            best_outer = out
            best_inner = inner

    # Prepare return in required format
    # inner_hexagons: list of [x, y, angle_degrees]
    inner_hexagons = [[x, y, ang] for (x, y, ang) in best_inner]  # type: ignore
    outer_side_length, ocx, ocy, oang = best_outer  # type: ignore
    outer_center = [ocx, ocy]
    outer_angle_degrees = oang

    # Return construction
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

```python
#!/usr/bin/env python3
"""Algorithmic construction for packing 11 unit hexagons inside a minimal regular hexagon.

This implementation builds a tightly staggered (triangular-lattice) arrangement of 11 unit
hexagons in 3 rows (4-3-4 and 3-4-4 variants), then searches the outer hexagon orientation
(angle) that minimizes the required side length via bisection. The arrangement ensures a tiny
positive clearance between inner hexagons (so they are disjoint, not just touching) and uses
precise polygon geometry (SAT primitives replicated locally) to compute the smallest outer
side that still contains all inner hexagons' vertices. The result returned is guaranteed to
pass the external verify_construction function (provided by the harness).
"""

import json


# EVOLVE_START
def optimize_construct():
    import math

    # Local geometry utilities (mirror the provided ones) with a conservative epsilon.
    EPSILON = 1e-9

    def hexagon_vertices(center_x, center_y, side_length, angle_degrees):
        vertices = []
        angle_radians = math.radians(angle_degrees)
        for i in range(6):
            angle = angle_radians + 2 * math.pi * i / 6.0
            x = center_x + side_length * math.cos(angle)
            y = center_y + side_length * math.sin(angle)
            vertices.append((x, y))
        return vertices

    def normalize_vector(v):
        magnitude = math.hypot(v[0], v[1])
        return (v[0] / magnitude, v[1] / magnitude) if magnitude > EPSILON else (0.0, 0.0)

    def get_normals(vertices):
        normals = []
        for i in range(len(vertices)):
            p1 = vertices[i]
            p2 = vertices[(i + 1) % len(vertices)]
            edge = (p2[0] - p1[0], p2[1] - p1[1])
            normal = normalize_vector((-edge[1], edge[0]))
            normals.append(normal)
        return normals

    def project_polygon(vertices, axis):
        min_proj = float('inf')
        max_proj = float('-inf')
        for vertex in vertices:
            projection = vertex[0] * axis[0] + vertex[1] * axis[1]
            min_proj = min(min_proj, projection)
            max_proj = max(max_proj, projection)
        return min_proj, max_proj

    def overlap_1d(min1, max1, min2, max2):
        return max1 >= (min2 - EPSILON) and max2 >= (min1 - EPSILON)

    def polygons_intersect(vertices1, vertices2):
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
        hex1_vertices = hexagon_vertices(*hex1_params)
        hex2_vertices = hexagon_vertices(*hex2_params)
        return not polygons_intersect(hex1_vertices, hex2_vertices)

    def is_inside_hexagon(point, hex_params):
        hex_vertices = hexagon_vertices(*hex_params)
        # Assumes vertices are CCW. Point-in-convex-polygon via edge cross-products.
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
        for inner_hex_params in inner_hex_params_list:
            inner_hex_vertices = hexagon_vertices(*inner_hex_params)
            for vertex in inner_hex_vertices:
                if not is_inside_hexagon(vertex, outer_hex_params):
                    return False
        return True

    # Build staggered-row seeds on a triangular lattice with a tiny clearance factor > 1
    # to ensure disjointness (no touching).
    def build_seed_positions(pattern="4-3-4", scale_factor=1.00002):
        # Triangular lattice parameters for unit hex tiling (pointy-top orientation):
        # Horizontal neighbor distance = sqrt(3), vertical row spacing = 1.5,
        # diagonal neighbor vector = (sqrt(3)/2, 1.5). We scale both axes by scale_factor
        # to open a tiny non-overlap gap.
        dx_base = math.sqrt(3.0)
        dy_base = 1.5
        dx = dx_base * scale_factor
        dy = dy_base * scale_factor

        positions = []
        if pattern == "4-3-4":
            # Row y = +dy: 4 centers
            y = dy
            xs = [-1.5 * dx, -0.5 * dx, 0.5 * dx, 1.5 * dx]
            positions += [(x, y) for x in xs]
            # Row y = 0: 3 centers
            y = 0.0
            xs = [-dx, 0.0, dx]
            positions += [(x, y) for x in xs]
            # Row y = -dy: 4 centers
            y = -dy
            xs = [-1.5 * dx, -0.5 * dx, 0.5 * dx, 1.5 * dx]
            positions += [(x, y) for x in xs]
        elif pattern == "3-4-4":
            # Row y = +dy: 3 centers
            y = dy
            xs = [-dx, 0.0, dx]
            positions += [(x, y) for x in xs]
            # Row y = 0: 4 centers
            y = 0.0
            xs = [-1.5 * dx, -0.5 * dx, 0.5 * dx, 1.5 * dx]
            positions += [(x, y) for x in xs]
            # Row y = -dy: 4 centers
            y = -dy
            xs = [-1.5 * dx, -0.5 * dx, 0.5 * dx, 1.5 * dx]
            positions += [(x, y) for x in xs]
        elif pattern == "4-4-3":
            # Row y = +dy: 4 centers
            y = dy
            xs = [-1.5 * dx, -0.5 * dx, 0.5 * dx, 1.5 * dx]
            positions += [(x, y) for x in xs]
            # Row y = 0: 4 centers
            y = 0.0
            xs = [-1.5 * dx, -0.5 * dx, 0.5 * dx, 1.5 * dx]
            positions += [(x, y) for x in xs]
            # Row y = -dy: 3 centers
            y = -dy
            xs = [-dx, 0.0, dx]
            positions += [(x, y) for x in xs]
        else:
            raise ValueError("Unknown pattern")
        return positions

    # Check pairwise disjointness among unit hexes with given centers and angles
    def arrangement_is_disjoint(inner_hex_data):
        n = len(inner_hex_data)
        params = [(x, y, 1.0, ang) for (x, y, ang) in inner_hex_data]
        for i in range(n):
            for j in range(i + 1, n):
                if not hexagons_are_disjoint(params[i], params[j]):
                    return False
        return True

    # For fixed inner arrangement and outer (center, angle), find minimal side length s
    # via bisection so that all vertices lie inside the outer hexagon.
    def minimal_side_length(inner_hex_data, outer_center, outer_angle_deg):
        # Helper to test containment at side length s
        def contained(s):
            if s <= 0:
                return False
            outer_params = (outer_center[0], outer_center[1], s, outer_angle_deg)
            params_list = [(x, y, 1.0, ang) for (x, y, ang) in inner_hex_data]
            return all_hexagons_contained(params_list, outer_params)

        # Establish a safe upper bound
        lo = 0.0
        hi = 4.5  # A conservative starting upper bound
        # Increase hi if needed
        outer_center_tuple = (outer_center[0], outer_center[1])
        while not contained(hi):
            hi *= 1.05
            if hi > 10.0:
                # Should not happen; guard to avoid infinite loop
                break

        # Bisection
        for _ in range(64):
            mid = 0.5 * (lo + hi)
            if contained(mid):
                hi = mid
            else:
                lo = mid
        # Add a minuscule safety margin
        return hi + 1e-9

    # Build multiple seeds and scan outer angles to pick the tightest one.
    # Inner hexagons can be rotated; we include a small set of candidate rotations.
    scale_factor = 1.00002  # tiny clearance for disjointness
    patterns = ["4-3-4", "3-4-4", "4-4-3"]
    inner_angles = [0.0, 10.0, 20.0, 30.0]  # candidate inner rotations (degrees)
    # Scan outer angles between 0 and 30 degrees (symmetry every 60 degrees)
    outer_angle_candidates = [i * 0.25 for i in range(0, int(30.0 / 0.25) + 1)]

    best_solution = None  # (s, inner_hex_data, outer_center, outer_angle)

    for pattern in patterns:
        base_positions = build_seed_positions(pattern=pattern, scale_factor=scale_factor)

        # Compute the centroid (should be near origin) to center the cluster at (0,0)
        cx = sum(p[0] for p in base_positions) / len(base_positions)
        cy = sum(p[1] for p in base_positions) / len(base_positions)
        centered_positions = [(x - cx, y - cy) for (x, y) in base_positions]

        for inner_angle in inner_angles:
            inner_hex_data = [[x, y, inner_angle] for (x, y) in centered_positions]
            # Ensure disjointness; if not, skip this configuration
            if not arrangement_is_disjoint(inner_hex_data):
                continue

            # Outer center at the centroid
            outer_center = [0.0, 0.0]

            # Scan outer angles to pick the minimal side length
            for outer_angle in outer_angle_candidates:
                s = minimal_side_length(inner_hex_data, outer_center, outer_angle)
                if best_solution is None or s < best_solution[0]:
                    best_solution = (s, [row[:] for row in inner_hex_data], outer_center[:], outer_angle)

    # Fallback: in extremely unlikely case no pattern yielded disjointness (should not happen)
    if best_solution is None:
        # Use a conservative, trivially valid configuration
        positions = [
            (0.0, 0.0), (3.0, 0.0), (6.0, 0.0), (9.0, 0.0),
            (1.5, 2.6), (4.5, 2.6), (7.5, 2.6),
            (3.0, 5.2), (6.0, 5.2),
            (1.5, 7.8), (4.5, 7.8),
        ]
        inner_hexagons = [[x, y, 0.0] for x, y in positions]
        return inner_hexagons, [5.0, 5.0], 12.0, 0.0

    # Retrieve best found
    s_best, inner_best, outer_center_best, outer_angle_best = best_solution

    # Shift to first quadrant (x>0, y>0) for cleanliness (translationally invariant)
    min_x = min(x for (x, y, ang) in inner_best)
    min_y = min(y for (x, y, ang) in inner_best)
    shift_x = (-min_x) + 2.0 if min_x <= 0 else 0.0
    shift_y = (-min_y) + 2.0 if min_y <= 0 else 0.0

    inner_hexagons = []
    for (x, y, ang) in inner_best:
        inner_hexagons.append([x + shift_x, y + shift_y, ang])

    outer_center = [outer_center_best[0] + shift_x, outer_center_best[1] + shift_y]
    outer_side_length = s_best
    outer_angle_degrees = outer_angle_best

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

```python
#!/usr/bin/env python3
"""Optimized construction for packing 11 unit hexagons inside the smallest regular hexagon.

This implementation follows a SAT-guided feasibility solver wrapped in a bisection
search on the outer hexagon side length S. We explore several near-honeycomb templates,
apply minimal separating translations and small controlled rotations on boundary hexagons,
and strictly enforce non-overlap and containment via internal geometric checks that
mirror the provided utilities (hexagon_vertices, polygons_intersect, etc.).

Note:
- We do not modify any external verification utilities; the final returned construction
  is intended to pass the external verify_construction used by the grader.
- Internally, we include equivalent geometric helpers to guide the optimization.
- The algorithm keeps all coordinates in the first quadrant at the end, as requested.
"""

import json
import math
import random
from typing import List, Tuple

# ------------- Internal geometric utilities (do not affect external verifier) -------------

EPSILON = 1e-9

def hexagon_vertices(center_x: float, center_y: float, side_length: float, angle_degrees: float):
    """Calculate the vertices of a regular hexagon (CCW order)."""
    vertices = []
    angle_radians = math.radians(angle_degrees)
    for i in range(6):
        angle = angle_radians + 2 * math.pi * i / 6.0
        x = center_x + side_length * math.cos(angle)
        y = center_y + side_length * math.sin(angle)
        vertices.append((x, y))
    return vertices

def normalize_vector(v: Tuple[float, float]):
    """Normalize a 2D vector."""
    magnitude = math.hypot(v[0], v[1])
    return (v[0] / magnitude, v[1] / magnitude) if magnitude > EPSILON else (0.0, 0.0)

def get_normals(vertices: List[Tuple[float, float]]):
    """Compute inward normals for a CCW polygon edges."""
    normals = []
    n = len(vertices)
    for i in range(n):
        p1 = vertices[i]
        p2 = vertices[(i + 1) % n]
        edge = (p2[0] - p1[0], p2[1] - p1[1])
        # For CCW polygons, (-ey, ex) is the inward normal
        normal = normalize_vector((-edge[1], edge[0]))
        normals.append(normal)
    return normals

def project_polygon(vertices: List[Tuple[float, float]], axis: Tuple[float, float]):
    """Project polygon onto an axis and return the min and max values."""
    min_proj = float('inf')
    max_proj = float('-inf')
    for (x, y) in vertices:
        p = x * axis[0] + y * axis[1]
        if p < min_proj:
            min_proj = p
        if p > max_proj:
            max_proj = p
    return min_proj, max_proj

def overlap_1d(min1: float, max1: float, min2: float, max2: float):
    """Check if two intervals overlap (treat touching as overlap)."""
    return max1 >= (min2 - EPSILON) and max2 >= (min1 - EPSILON)

def polygons_intersect(vertices1: List[Tuple[float, float]], vertices2: List[Tuple[float, float]]):
    """Separating Axis Theorem for convex polygons; returns True if they intersect or touch."""
    normals1 = get_normals(vertices1)
    normals2 = get_normals(vertices2)
    axes = normals1 + normals2
    for axis in axes:
        min1, max1 = project_polygon(vertices1, axis)
        min2, max2 = project_polygon(vertices2, axis)
        if not overlap_1d(min1, max1, min2, max2):
            return False
    return True

def hexagons_are_disjoint(h1: Tuple[float, float, float, float], h2: Tuple[float, float, float, float]):
    """Return True if two hexagons do not intersect (strictly disjoint)."""
    v1 = hexagon_vertices(*h1)
    v2 = hexagon_vertices(*h2)
    return not polygons_intersect(v1, v2)

def is_inside_hexagon(point: Tuple[float, float], hex_params: Tuple[float, float, float, float]) -> bool:
    """Check if a point is inside or on the boundary of a hexagon (CCW)."""
    verts = hexagon_vertices(*hex_params)
    n = len(verts)
    for i in range(n):
        p1 = verts[i]
        p2 = verts[(i + 1) % n]
        edge_vector = (p2[0] - p1[0], p2[1] - p1[1])
        point_vector = (point[0] - p1[0], point[1] - p1[1])
        cross_product = edge_vector[0] * point_vector[1] - edge_vector[1] * point_vector[0]
        # For CCW, interior is to the left: cross >= 0 (allow tiny negative with EPS)
        if cross_product < -1e-12:
            return False
    return True

def all_hexagons_contained(inner_hex_params_list: List[Tuple[float, float, float, float]],
                           outer_hex_params: Tuple[float, float, float, float]) -> bool:
    """Verify all inner hex vertices lie inside outer polygon."""
    for h in inner_hex_params_list:
        verts = hexagon_vertices(*h)
        for v in verts:
            if not is_inside_hexagon(v, outer_hex_params):
                return False
    return True

# ------------- Packing/solver helpers -------------

SQRT3 = math.sqrt(3.0)

def outer_hex_vertices(center: Tuple[float, float], S: float, angle_deg: float = 0.0):
    """Outer hexagon vertices CCW."""
    return hexagon_vertices(center[0], center[1], S, angle_deg)

def outer_hex_inward_normals(center: Tuple[float, float], S: float, angle_deg: float = 0.0):
    """Inward normals for outer hex edges."""
    verts = outer_hex_vertices(center, S, angle_deg)
    return verts, get_normals(verts)

def polygon_centroid(vertices: List[Tuple[float, float]]):
    """Centroid for a convex polygon (approx by arithmetic mean)."""
    sx, sy = 0.0, 0.0
    for (x, y) in vertices:
        sx += x
        sy += y
    n = len(vertices)
    return (sx / n, sy / n)

def min_distance_to_outer(verts: List[Tuple[float, float]], outer_verts: List[Tuple[float, float]], outer_normals: List[Tuple[float, float]]):
    """Compute the minimum signed distance of polygon vertices to outer polygon halfspaces.

    Returns:
      worst_d: minimum over all edges/vertices of signed (inward) distance (negative if outside),
      edge_index: index of most violated edge,
      vertex_index: index of most violated vertex.
    """
    worst_d = float('inf')
    worst_edge = -1
    worst_vi = -1
    n_edges = len(outer_verts)
    for vi, v in enumerate(verts):
        for ei in range(n_edges):
            p1 = outer_verts[ei]
            n = outer_normals[ei]  # inward normal
            d = (v[0] - p1[0]) * n[0] + (v[1] - p1[1]) * n[1]
            if d < worst_d:
                worst_d = d
                worst_edge = ei
                worst_vi = vi
    return worst_d, worst_edge, worst_vi

def mtv_for_polygons(v1: List[Tuple[float, float]], v2: List[Tuple[float, float]]):
    """Compute minimum translation vector (axis, overlap) for intersection via SAT.

    Returns:
      (axis_x, axis_y, overlap) where axis is unit vector direction from poly1 to poly2.
      If no intersection, returns (0, 0, 0).
    """
    normals = get_normals(v1) + get_normals(v2)
    min_overlap = float('inf')
    best_axis = (0.0, 0.0)

    for axis in normals:
        min1, max1 = project_polygon(v1, axis)
        min2, max2 = project_polygon(v2, axis)
        if not overlap_1d(min1, max1, min2, max2):
            return (0.0, 0.0, 0.0)  # No intersection along this axis
        overlap = min(max1, max2) - max(min1, min2)
        if overlap < min_overlap:
            min_overlap = overlap
            best_axis = axis

    if min_overlap == float('inf'):
        return (0.0, 0.0, 0.0)

    # Determine direction from poly1 to poly2 for consistent separation
    c1 = polygon_centroid(v1)
    c2 = polygon_centroid(v2)
    sep_dir = best_axis
    # If axis points opposite the vector from c1 to c2, flip it
    v12 = (c2[0] - c1[0], c2[1] - c1[1])
    if v12[0] * sep_dir[0] + v12[1] * sep_dir[1] < 0:
        sep_dir = (-sep_dir[0], -sep_dir[1])

    return (sep_dir[0], sep_dir[1], min_overlap)

def rotate_point(px: float, py: float, angle_deg: float):
    """Rotate point around origin by angle_deg CCW."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return (ca * px - sa * py, sa * px + ca * py)

def translate_to_positive(vertices: List[Tuple[float, float]]):
    """Compute translation to move all vertices into x>0,y>0."""
    min_x = min(x for (x, y) in vertices)
    min_y = min(y for (x, y) in vertices)
    tx = -min_x + 1e-6 if min_x < 0 else 0.0
    ty = -min_y + 1e-6 if min_y < 0 else 0.0
    return tx, ty

# ------------- Templates -------------

def template_axial_rows(row_counts: List[int]) -> List[Tuple[int, int]]:
    """Generate axial coordinates (q, r) for rows r=0..R-1 with specified counts per row.

    This uses pointy-top axial layout; within each row r, we place q = 0..(n-1).
    """
    axial = []
    for r, n in enumerate(row_counts):
        for q in range(n):
            axial.append((q, r))
    return axial

def axial_to_xy(q: int, r: int, s: float = 1.0):
    """Axial (q, r) to Euclidean coordinates for pointy-top hex grid of side 1."""
    x = SQRT3 * (q + r / 2.0) * s
    y = 1.5 * r * s
    return x, y

def axial_set_to_xy(axial: List[Tuple[int, int]], jitter: float = 0.0, spacing_scale: float = 1.001,
                    cluster_rot_deg: float = 0.0):
    """Map a set of axial coords to Euclidean centers with optional jitter, spacing scale, and rotation."""
    pts = []
    for (q, r) in axial:
        x, y = axial_to_xy(q, r, s=spacing_scale)
        if jitter > 0.0:
            x += random.uniform(-jitter, jitter)
            y += random.uniform(-jitter, jitter)
        if abs(cluster_rot_deg) > 1e-9:
            x, y = rotate_point(x, y, cluster_rot_deg)
        pts.append((x, y))
    # Center them around origin for stability
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    pts = [(p[0] - cx, p[1] - cy) for p in pts]
    return pts

def neighbors_axial(qr_list: List[Tuple[int, int]]):
    """Compute neighbor count for each axial cell to identify boundary cells."""
    sset = set(qr_list)
    dirs = [(1,0),(-1,0),(0,1),(-1,1),(1,-1),(0,-1)]
    counts = []
    for (q, r) in qr_list:
        c = 0
        for dq, dr in dirs:
            if (q + dq, r + dr) in sset:
                c += 1
        counts.append(c)
    return counts

# ------------- Feasibility solver for fixed S -------------

def solve_for_S(S: float,
                templates: List[List[Tuple[int, int]]],
                base_angle: float,
                outer_angle: float,
                max_iters: int = 500,
                seeds_per_template: int = 3,
                margin_sep: float = 1e-4) -> Tuple[bool, dict]:
    """Try to find a placement for the given S. Returns (success, data_dict)."""

    # Precompute cluster rotations to try
    cluster_rots = [0.0, 7.5, -7.5, 15.0, -15.0]

    best_found = None

    for axial in templates:
        # Boundary detection to allow rotations on boundary cells
        neigh_counts = neighbors_axial(axial)
        # Prebuild a map from index to boundary flag
        # boundary if neigh < 6
        # We'll allow small rotations for boundary to shave slack
        boundary_flags = [nc < 6 for nc in neigh_counts]

        for cluster_rot in cluster_rots:
            for seed_k in range(seeds_per_template):
                jitter = 0.01 if seed_k > 0 else 0.0
                spacing_scale = 1.001 + 0.0005 * seed_k  # create a tiny positive gap
                centers = axial_set_to_xy(axial, jitter=jitter, spacing_scale=spacing_scale, cluster_rot_deg=cluster_rot)
                n = len(centers)
                # Initialize angles
                angles = [base_angle + cluster_rot for _ in range(n)]
                # Allowable rotation bandwidth around base for boundary hexes
                rot_bounds = [(-12.0, 12.0) if boundary_flags[i] else (0.0, 0.0) for i in range(n)]

                # Outer center initialized to centroid
                ocx = sum(x for (x, y) in centers) / n
                ocy = sum(y for (x, y) in centers) / n
                outer_center = [ocx, ocy]

                # Iterative repair
                success = False
                for it in range(max_iters):
                    # Adjust outer center gently towards cluster centroid (helps stability)
                    ocx = sum(x for (x, y) in centers) / n
                    ocy = sum(y for (x, y) in centers) / n
                    # small smoothing
                    outer_center[0] = 0.7 * outer_center[0] + 0.3 * ocx
                    outer_center[1] = 0.7 * outer_center[1] + 0.3 * ocy

                    # Build outer polygon data
                    o_verts, o_normals = outer_hex_inward_normals((outer_center[0], outer_center[1]), S, outer_angle)

                    # Enforce containment with small rotations at boundary if needed
                    max_contain_violation = 0.0
                    for i in range(n):
                        v = hexagon_vertices(centers[i][0], centers[i][1], 1.0, angles[i])
                        dmin, edge_idx, vtx_idx = min_distance_to_outer(v, o_verts, o_normals)
                        if dmin < 0.0:
                            max_contain_violation = min(max_contain_violation, dmin)
                            # Push inward along the most violated edge inward normal
                            nvec = o_normals[edge_idx]
                            # Move center to reduce violation (scale factor tunes convergence)
                            step = -dmin * 0.75
                            centers[i] = (centers[i][0] + nvec[0] * step, centers[i][1] + nvec[1] * step)

                            # Optional small rotation for boundary hexes to reduce protrusion
                            if rot_bounds[i] != (0.0, 0.0):
                                base = base_angle + cluster_rot
                                lo, hi = rot_bounds[i]
                                # Try small angle tweaks
                                best_theta = angles[i]
                                best_improvement = dmin
                                for delta in (-1.2, -0.6, 0.0, 0.6, 1.2):
                                    cand = angles[i] + delta
                                    # clamp around base +/- bounds
                                    if cand < base + lo: continue
                                    if cand > base + hi: continue
                                    vv = hexagon_vertices(centers[i][0], centers[i][1], 1.0, cand)
                                    dmin2, _, _ = min_distance_to_outer(vv, o_verts, o_normals)
                                    if dmin2 > best_improvement:
                                        best_improvement = dmin2
                                        best_theta = cand
                                angles[i] = best_theta

                    # Resolve overlaps via MTVs
                    # Accumulate displacements to apply smoothly
                    disp = [(0.0, 0.0) for _ in range(n)]
                    overlap_count = 0
                    for i in range(n):
                        vi = hexagon_vertices(centers[i][0], centers[i][1], 1.0, angles[i])
                        for j in range(i + 1, n):
                            vj = hexagon_vertices(centers[j][0], centers[j][1], 1.0, angles[j])
                            mtv_x, mtv_y, ov = mtv_for_polygons(vi, vj)
                            if ov > 0.0:
                                overlap_count += 1
                                # Separate by ov + margin
                                sep = (ov + margin_sep) * 0.52  # move a tad more than half to ensure clearance
                                dx = mtv_x * sep
                                dy = mtv_y * sep
                                # Move i opposite, j along
                                di = disp[i]
                                dj = disp[j]
                                disp[i] = (di[0] - dx, di[1] - dy)
                                disp[j] = (dj[0] + dx, dj[1] + dy)

                    # Apply displacements with damping
                    if overlap_count > 0:
                        for i in range(n):
                            if disp[i] != (0.0, 0.0):
                                centers[i] = (centers[i][0] + disp[i][0], centers[i][1] + disp[i][1])

                    # Check feasibility with our internal verifier
                    inner_hex_params_list = [(centers[i][0], centers[i][1], 1.0, angles[i]) for i in range(n)]
                    outer_hex_params = (outer_center[0], outer_center[1], S, outer_angle)

                    # Quick checks: disjointness and containment
                    ok = True
                    # Check pairwise disjoint (strict)
                    for i in range(n):
                        for j in range(i + 1, n):
                            if not hexagons_are_disjoint(inner_hex_params_list[i], inner_hex_params_list[j]):
                                ok = False
                                break
                        if not ok:
                            break
                    if ok:
                        # Containment: all vertices inside
                        if all_hexagons_contained(inner_hex_params_list, outer_hex_params):
                            success = True
                            # Record and break
                            best_found = {
                                "centers": centers[:],
                                "angles": angles[:],
                                "outer_center": (outer_center[0], outer_center[1]),
                                "S": S,
                                "outer_angle": outer_angle
                            }
                            break

                    # If no overlaps and no containment violation (dmin >= 0), we should already have broken above.
                    # Otherwise continue iterating. If system stalls, minor diffusion jitter:
                    if overlap_count == 0 and max_contain_violation >= -1e-6:
                        # Near-feasible; try one last tiny relaxation
                        pass

                if best_found is not None:
                    # Found feasible for this S
                    break
            if best_found is not None:
                break
        if best_found is not None:
            break

    if best_found is None:
        return False, {}
    return True, best_found

# ------------- Main optimization with bisection on S -------------

def optimize_construct():
    """Return a construction that passes external verification.

    Output:
      - inner_hexagons: list of [x, y, angle_degrees] for the 11 inner hexagons (side length fixed to 1)
      - outer_center: [x, y] for the outer hexagon center
      - outer_side_length: minimal side length found
      - outer_angle_degrees: angle for the outer hexagon (keep 0 for stability)
    """
    random.seed(42)

    # Templates (axial rows) totaling 11 cells
    templates_axial = [
        template_axial_rows([3, 4, 4]),  # 3-4-4
        template_axial_rows([4, 3, 4]),  # 4-3-4
        template_axial_rows([4, 4, 3]),  # 4-4-3
        # Slightly taller variant to diversify outlines
        template_axial_rows([3, 4, 4]) + [(0, 3)],  # add one at top start, then prune one later dynamically
    ]
    # Ensure the last template remains 11 by pruning a boundary-most point if > 11
    fixed_templates = []
    for ax in templates_axial:
        temp = list(ax)
        if len(temp) > 11:
            # Remove the lexicographically last (likely boundary) to keep 11
            temp = temp[:11]
        elif len(temp) < 11:
            # Pad by mirroring the first to keep 11 (unlikely path)
            while len(temp) < 11:
                temp.append(temp[-1])
        fixed_templates.append(temp)

    # Fixed orientation: pointy-top interior, outer hex angle aligned with axes
    base_inner_angle = 90.0
    outer_angle = 0.0

    # Bisection bounds on S (outer hex side length)
    # Start with a safe upper bound and an ambitious lower bound
    S_lo = 3.60
    S_hi = 4.15

    best_solution = None
    # Bisection iterations
    for _ in range(15):
        S_mid = 0.5 * (S_lo + S_hi)
        ok, data = solve_for_S(
            S=S_mid,
            templates=fixed_templates,
            base_angle=base_inner_angle,
            outer_angle=outer_angle,
            max_iters=600,
            seeds_per_template=3,
            margin_sep=2e-4
        )
        if ok:
            best_solution = data
            S_hi = S_mid
        else:
            S_lo = S_mid

    if best_solution is None:
        # Fallback to a conservative placement if optimization fails (should not happen)
        positions = [
            (-3.3, -2.0), (-1.1, -2.0), (1.1, -2.0), (3.3, -2.0),
            (-3.3, 0.0), (-1.1, 0.0), (1.1, 0.0), (3.3, 0.0),
            (-2.2, 2.0), (0.0, 2.0), (2.2, 2.0),
        ]
        inner_hexagons = [[x + 10.0, y + 10.0, 0.0] for x, y in positions]
        return inner_hexagons, [12.0, 12.0], 8.0, 0.0

    # Shift to positive coordinates for output preference
    centers = best_solution["centers"]
    angles = best_solution["angles"]
    outer_center = list(best_solution["outer_center"])
    S = best_solution["S"]
    out_ang = best_solution["outer_angle"]

    # Compute full set of all vertices (inner + outer) to find min x,y
    all_verts = []
    for (c, a) in zip(centers, angles):
        all_verts.extend(hexagon_vertices(c[0], c[1], 1.0, a))
    all_verts.extend(outer_hex_vertices((outer_center[0], outer_center[1]), S, out_ang))
    tx, ty = translate_to_positive(all_verts)

    centers = [(c[0] + tx, c[1] + ty) for c in centers]
    outer_center = [outer_center[0] + tx, outer_center[1] + ty]

    inner_hexagons = [[centers[i][0], centers[i][1], angles[i]] for i in range(len(centers))]
    return inner_hexagons, outer_center, S, out_ang


if __name__ == "__main__":
    inner, center, side, angle = optimize_construct()
    print(json.dumps({
        "inner_hexagons": inner,
        "outer_center": center,
        "outer_side_length": side,
        "outer_angle_degrees": angle,
    }))
```
