Hybrid strategy that couples SAT-validated global spacing control with orientation-aware local adjustments and repeated outer-fit refinement.

- SAT-MinInflate with Active Micro-Rotation and Reverse-Deflation: Apply SAT-validated minimal uniform radial inflation about the centers’ centroid via bisection (start [1.0, 1.003], widen to ≈1.01 if needed) to remove tangencies with near-zero slack, then target only hexagons supporting the current maximal apothem with micro-rotation probes s ∈ {−0.6°, −0.3°, 0°, +0.3°, +0.6°} and guarded inward translations along active normals; after successful local nudges, run reverse radial deflation by bisection over s' ∈ [1, s] to uniformly contract while keeping SAT disjointness, and refit the outer hex via coarse sweep + golden-section + micro-sweep (±0.5° at 0.02°) with active-normal Chebyshev center correction. Maintain strict validity using SAT checks, backtracking, and a tiny robustness inflation (~1e−9) on the returned side length, and finalize in the positive quadrant (x>0, y>0); in this run the construction achieved validity = 1.0, target_ratio = 0.9827499993287605, and outer_hex_side_length = 4.000000002732087. Reuse this pattern of globally controlled inflation/deflation coupled with orientation-aware micro-rotations and iterative outer-fit refinement to reliably reduce the limiting apothem without violating constraints.
- SAT-Tight 4–3–4 with Adaptive Active-Normal Rotations, Inward Translations, and Reverse Deflation: Under strict SAT guards, first target the currently active limiting normals by running adaptive micro-rotation line searches (with face-alignment rescue when needed) and tiny inward translations on the hexagons supporting the maximal apothem to directly lower the outer support, then immediately apply reverse radial deflation to uniformly reclaim global slack; reuse this sequence inside an outer-fit loop that re-optimizes the center via active-normal Chebyshev correction and refines the hull orientation with coarse→golden→micro search, as it maintained a tight hull and produced a valid construction (validity 1.0) with a strong score of 0.9827499993287679 in this task.
- Reverse-Deflation After Active-Normal Tightening: After support-active equalization and micro-rotations, uniformly contract all hexagon centers about the centroid by a scale s in [s_lo, 1] with s_lo ≈ 0.985–0.99, and use SAT-guarded bisection against hexagons_are_disjoint to find the smallest s that preserves strict non-overlap for the entire layout.
- Reverse-Deflation After Active-Normal Tightening: Rebuild inner vertices and re-run the outer shrink-wrap (least-squares center, active-normal center correction, and φ coarse→golden→micro refinement) on the contracted layout, then perform the final verify-driven trimming to update the outer center, rotation, and side length.
- Reverse-Deflation After Active-Normal Tightening: This conservative, SAT-bisected reverse deflation reliably reclaims global hull slack left by local nudges and typically trims 1e−3–1e−2 off the required outer side length while maintaining strict feasibility, with minimal integration changes (a single helper invoked once and a 32-iteration bisection).

```python
#!/usr/bin/env python3
"""Hybrid SAT-tight 4–3–4 packing with radial inflation/deflation, active-normal
micro-rotation nudges, Chebyshev-style center correction, and golden + micro
outer-orientation search.

This implements the requested crossover/mutation:

- Dual seeds (4–3–4 honeycomb centers):
  Seed A: all inner hexagons at 30° (pointy-top).
  Seed B: mixed rows: outer rows 0°, middle row 30°.

- Exact honeycomb spacing (H = √3, V = 1.5) with zero built-in slack.
  A minimal SAT-clean uniform radial inflation is found by bisection and applied
  to eliminate tangencies safely with near-zero slack.

- Outer hex fit pipeline:
  • Coarse sweep of the normals orientation α ∈ [0°, 60°) in 1° steps.
  • Golden-section refinement around the best α.
  • Active-normal Chebyshev-style center correction to reduce the apothem.
  • Final micro-sweep in ±0.5° around the refined α at 0.02° step.

- Support-aware per-hex micro-rotation nudges on the currently active limiting axes:
  Identify the active normals and the hexagons supporting those extremes. Probe small
  rotations s ∈ {−0.6°, −0.3°, 0°, +0.3°, +0.6°}; accept if it decreases the active apothem
  proxy and preserves SAT disjointness. Then try a tiny inward translation along the
  inward normal with backtracking and SAT guards.

- Reverse radial deflation:
  After micro-rotations/translations create new clearances, uniformly contract the centers
  toward their centroid via bisection while preserving SAT disjointness, recovering the
  initial inflation and further tightening the hull.

- Short multi-pass loop (2–3 passes) of nudges + deflation + refit, accepting a pass only
  on measurable improvement in the outer side length.

- Best-of-two seeds, shift to first quadrant, and return with a microscopic outer side
  length inflation for robustness.

All SAT checks and containment in external verification use the provided utilities.
This constructor maintains strict non-overlap and uses robust tolerances to pass
the verify_construction checks without modifying them.
"""

import json
import math
from typing import List, Tuple


# EVOLVE_START
def optimize_construct():
    """
    Construct a compact packing of 11 unit hexagons inside a minimal outer regular hexagon.

    Returns:
        inner_hexagons: List of [x, y, angle_degrees] for the 11 inner hexagons (side length fixed at 1).
        outer_center: [x, y] center of the outer hexagon.
        outer_side_length: Side length of the outer hexagon.
        outer_angle_degrees: Rotation angle (vertex-phase) of the outer hexagon in degrees.
    """
    # -------- Constants and basic geometry helpers ----------
    EPS = 1e-9
    sqrt3 = math.sqrt(3.0)
    unit_side = 1.0

    # Local utilities (mirroring the provided verification ones, used only internally)
    def hexagon_vertices(center_x: float, center_y: float, side_length: float, angle_degrees: float):
        verts = []
        angle_radians = math.radians(angle_degrees)
        for i in range(6):
            ang = angle_radians + 2.0 * math.pi * i / 6.0
            x = center_x + side_length * math.cos(ang)
            y = center_y + side_length * math.sin(ang)
            verts.append((x, y))
        return verts

    def normalize_vector(v):
        mag = math.hypot(v[0], v[1])
        return (v[0] / mag, v[1] / mag) if mag > EPS else (0.0, 0.0)

    def get_normals(vertices):
        normals = []
        n = len(vertices)
        for i in range(n):
            p1 = vertices[i]
            p2 = vertices[(i + 1) % n]
            edge = (p2[0] - p1[0], p2[1] - p1[1])
            normal = normalize_vector((-edge[1], edge[0]))
            normals.append(normal)
        return normals

    def project_polygon(vertices, axis):
        min_proj = float('inf')
        max_proj = float('-inf')
        ax, ay = axis
        for (x, y) in vertices:
            p = x * ax + y * ay
            if p < min_proj:
                min_proj = p
            if p > max_proj:
                max_proj = p
        return min_proj, max_proj

    def overlap_1d(min1, max1, min2, max2):
        # Return True if intervals overlap (touching counts as overlap)
        return max1 >= min2 - EPS and max2 >= min1 - EPS

    def polygons_intersect(vertices1, vertices2):
        # Separating Axis Theorem for convex polygons
        normals = get_normals(vertices1) + get_normals(vertices2)
        for axis in normals:
            min1, max1 = project_polygon(vertices1, axis)
            min2, max2 = project_polygon(vertices2, axis)
            if not overlap_1d(min1, max1, min2, max2):
                return False
        return True

    def hexagons_are_disjoint(h1, h2):
        # h = (x, y, side=1, angle)
        v1 = hexagon_vertices(h1[0], h1[1], unit_side, h1[3])
        v2 = hexagon_vertices(h2[0], h2[1], unit_side, h2[3])
        return not polygons_intersect(v1, v2)

    # Layout helpers
    def layout_to_params(layout):
        # Converts [x, y, ang] to params (x, y, 1.0, ang) for SAT
        return [(x, y, unit_side, ang) for (x, y, ang) in layout]

    def all_disjoint(layout):
        params = layout_to_params(layout)
        n = len(params)
        for i in range(n):
            for j in range(i + 1, n):
                if not hexagons_are_disjoint(params[i], params[j]):
                    return False
        return True

    def all_vertices(layout):
        # Collect all vertices across all inner hexes; also return per-hex vertices
        verts = []
        per_hex = []
        for (x, y, ang) in layout:
            v = hexagon_vertices(x, y, unit_side, ang)
            verts.extend(v)
            per_hex.append(v)
        return verts, per_hex

    # --------- Seed generation (4–3–4 honeycomb) ----------
    H = sqrt3   # exact honeycomb horizontal step (no slack)
    V = 1.5     # exact honeycomb vertical step (no slack)

    def seed_uniform_30() -> List[List[float]]:
        inner = []
        # Row 0 (y=0), 4 hexes
        y0 = 0.0
        for j in range(4):
            inner.append([j * H, y0, 30.0])
        # Row 1 (y=V), 3 hexes, offset by 0.5 H
        y1 = V
        for j in range(3):
            inner.append([0.5 * H + j * H, y1, 30.0])
        # Row 2 (y=2V), 4 hexes
        y2 = 2.0 * V
        for j in range(4):
            inner.append([j * H, y2, 30.0])
        return inner

    def seed_mixed_rows() -> List[List[float]]:
        inner = []
        # Row 0 at 0°
        y0 = 0.0
        for j in range(4):
            inner.append([j * H, y0, 0.0])
        # Middle row at 30°
        y1 = V
        for j in range(3):
            inner.append([0.5 * H + j * H, y1, 30.0])
        # Row 2 at 0°
        y2 = 2.0 * V
        for j in range(4):
            inner.append([j * H, y2, 0.0])
        return inner

    # ---------- Minimal SAT-clean radial inflation ----------
    def centroid(layout):
        sx = sum(p[0] for p in layout)
        sy = sum(p[1] for p in layout)
        n = len(layout)
        return (sx / n, sy / n)

    def scale_about_C(layout, C, s):
        cx, cy = C
        new = []
        for (x, y, ang) in layout:
            nx = cx + s * (x - cx)
            ny = cy + s * (y - cy)
            new.append([nx, ny, ang])
        return new

    def minimal_radial_inflation(layout):
        # Ensure disjointness by scaling outward about centroid by minimal s>=1
        C = centroid(layout)
        s_lo = 1.0
        s_hi = 1.0005
        # Increase s_hi until disjoint
        scaled = scale_about_C(layout, C, s_hi)
        tries = 0
        while not all_disjoint(scaled) and tries < 40:
            s_hi *= 1.01
            scaled = scale_about_C(layout, C, s_hi)
            tries += 1
        # Now bisection between [s_lo, s_hi]
        for _ in range(35):
            s_mid = 0.5 * (s_lo + s_hi)
            test = scale_about_C(layout, C, s_mid)
            if all_disjoint(test):
                s_hi = s_mid
                scaled = test
            else:
                s_lo = s_mid
        return scaled, C, s_hi

    # ---------- Outer hex fit and refinements ----------
    # Compute vertices once per layout when needed
    def fit_outer_hex(layout, alpha_deg):
        # alpha_deg is normals phase. Outward normals at alpha, alpha+60, alpha+120
        verts_all, _ = all_vertices(layout)
        normals = []
        for k in range(3):
            ang = math.radians(alpha_deg + 60.0 * k)
            normals.append((math.cos(ang), math.sin(ang)))

        proj_min = []
        proj_max = []
        for nvec in normals:
            dots = [vx * nvec[0] + vy * nvec[1] for (vx, vy) in verts_all]
            proj_min.append(min(dots))
            proj_max.append(max(dots))

        # Least-squares center: solve dot(n_k, C) ≈ (max_k + min_k)/2
        c_vec = [(proj_max[k] + proj_min[k]) * 0.5 for k in range(3)]
        A = normals
        ata00 = sum(A[i][0] * A[i][0] for i in range(3))
        ata01 = sum(A[i][0] * A[i][1] for i in range(3))
        ata11 = sum(A[i][1] * A[i][1] for i in range(3))
        atc0 = sum(A[i][0] * c_vec[i] for i in range(3))
        atc1 = sum(A[i][1] * c_vec[i] for i in range(3))
        det = ata00 * ata11 - ata01 * ata01
        if abs(det) < 1e-14:
            cx, cy = 0.0, 0.0
        else:
            inv00 = ata11 / det
            inv01 = -ata01 / det
            inv11 = ata00 / det
            cx = inv00 * atc0 + inv01 * atc1
            cy = inv01 * atc0 + inv11 * atc1

        # Apothem at LS center
        a_vals = []
        for k in range(3):
            nC = cx * normals[k][0] + cy * normals[k][1]
            a_k = max(proj_max[k] - nC, nC - proj_min[k])
            a_vals.append(a_k)
        a_ls = max(a_vals)

        # Active-normal Chebyshev-style center correction
        def refine_center(C0):
            C = (C0[0], C0[1])

            def apothem_and_details(C):
                cx, cy = C
                a_list = []
                gp = []
                gn = []
                for k in range(3):
                    nC = cx * normals[k][0] + cy * normals[k][1]
                    gpos = proj_max[k] - nC
                    gneg = nC - proj_min[k]
                    gp.append(gpos)
                    gn.append(gneg)
                    a_list.append(max(gpos, gneg))
                return max(a_list), a_list, gp, gn

            a_cur, a_list, gp, gn = refine_center_cache = apothem_and_details(C)
            for _ in range(5):
                K = [k for k in range(3) if a_list[k] >= a_cur - 1e-12]
                dx = 0.0
                dy = 0.0
                for k in K:
                    sgn = 1.0 if gp[k] >= gn[k] else -1.0
                    dx += sgn * normals[k][0]
                    dy += sgn * normals[k][1]
                dnorm = math.hypot(dx, dy)
                if dnorm < 1e-14:
                    break
                dx /= dnorm
                dy /= dnorm
                tau = 0.15 * a_cur
                improved = False
                while tau > 1e-12:
                    Cn = (C[0] - tau * dx, C[1] - tau * dy)
                    a_new, a_list_new, gp_new, gn_new = apothem_and_details(Cn)
                    if a_new < a_cur - 1e-12:
                        C = Cn
                        a_cur = a_new
                        a_list = a_list_new
                        gp = gp_new
                        gn = gn_new
                        improved = True
                        break
                    tau *= 0.5
                if not improved:
                    break
            return C, a_cur

        C_ref, a_ref = refine_center((cx, cy))
        a_final = min(a_ls, a_ref)
        S = (2.0 * a_final) / sqrt3
        theta_deg = alpha_deg - 30.0
        if a_ref <= a_ls:
            return S, (C_ref[0], C_ref[1]), theta_deg
        else:
            return S, (cx, cy), theta_deg

    def norm_alpha(a):
        r = a % 60.0
        return r if r >= 0.0 else (r + 60.0)

    def eval_alpha(layout, alpha):
        return fit_outer_hex(layout, norm_alpha(alpha))

    def golden_refine(layout, alpha0, span=3.0, iterations=20):
        lo = alpha0 - span
        hi = alpha0 + span
        invphi = (math.sqrt(5.0) - 1.0) / 2.0
        x1 = hi - invphi * (hi - lo)
        x2 = lo + invphi * (hi - lo)
        f1 = eval_alpha(layout, x1)
        f2 = eval_alpha(layout, x2)
        best = f1 if f1[0] <= f2[0] else f2
        for _ in range(iterations):
            if f1[0] < f2[0]:
                hi = x2
                x2 = x1
                f2 = f1
                x1 = hi - invphi * (hi - lo)
                f1 = eval_alpha(layout, x1)
            else:
                lo = x1
                x1 = x2
                f1 = f2
                x2 = lo + invphi * (hi - lo)
                f2 = eval_alpha(layout, x2)
            if f1[0] < best[0]:
                best = f1
            if f2[0] < best[0]:
                best = f2
        return best

    def micro_sweep(layout, alpha_center, half_width=0.5, step=0.02):
        best_S = float('inf')
        best_C = (0.0, 0.0)
        best_theta = 0.0
        a0 = alpha_center - half_width
        a1 = alpha_center + half_width
        nsteps = int(round((a1 - a0) / step)) + 1
        for i in range(nsteps):
            a = a0 + i * step
            S, C, theta = eval_alpha(layout, a)
            if S < best_S:
                best_S, best_C, best_theta = S, C, theta
        return best_S, best_C, best_theta

    def outer_fit_pipeline(layout):
        # Coarse sweep
        coarse_S = float('inf')
        coarse_C = (0.0, 0.0)
        coarse_theta = 0.0
        coarse_alpha = 0.0
        for alpha in range(60):
            S, C, theta = fit_outer_hex(layout, float(alpha))
            if S < coarse_S:
                coarse_S, coarse_C, coarse_theta, coarse_alpha = S, C, theta, float(alpha)
        # Golden refine
        ref_S, ref_C, ref_theta = golden_refine(layout, coarse_alpha, span=3.0, iterations=20)
        # Micro sweep around refined α ≈ ref_theta + 30°
        micro_S, micro_C, micro_theta = micro_sweep(layout, norm_alpha(ref_theta + 30.0), half_width=0.5, step=0.02)
        # Best across stages
        best_S = coarse_S
        best_C = coarse_C
        best_theta = coarse_theta
        if ref_S < best_S:
            best_S, best_C, best_theta = ref_S, ref_C, ref_theta
        if micro_S < best_S:
            best_S, best_C, best_theta = micro_S, micro_C, micro_theta
        return best_S, best_C, best_theta

    # ---------- Support computation and active set ----------
    def apothem_proxy_for(layout, center, alpha_normals_deg):
        verts_all, _ = all_vertices(layout)
        normals = []
        for k in range(3):
            ang = math.radians(alpha_normals_deg + 60.0 * k)
            normals.append((math.cos(ang), math.sin(ang)))

        proj_min = []
        proj_max = []
        for nvec in normals:
            dots = [vx * nvec[0] + vy * nvec[1] for (vx, vy) in verts_all]
            proj_min.append(min(dots))
            proj_max.append(max(dots))

        cx, cy = center
        a_vals = []
        gap_pos = []
        gap_neg = []
        for k in range(3):
            nC = cx * normals[k][0] + cy * normals[k][1]
            gp = proj_max[k] - nC
            gn = nC - proj_min[k]
            gap_pos.append(gp)
            gap_neg.append(gn)
            a_vals.append(max(gp, gn))
        a = max(a_vals)
        return a, a_vals, gap_pos, gap_neg, normals, proj_min, proj_max

    def active_hex_ids(layout, center, alpha_normals_deg, tol=1e-10):
        # Returns the set of hex indices contributing to extreme supports on active normals
        _, per_hex = all_vertices(layout)
        a, a_vals, gp, gn, normals, proj_min, proj_max = apothem_proxy_for(layout, center, alpha_normals_deg)

        # Identify active normals indices
        K = [k for k in range(3) if a_vals[k] >= a - 1e-12]
        active_ids = set()
        # For each active normal, find hexes with vertices at extremes
        for k in K:
            n = normals[k]
            # Find max and min contributors
            max_val = proj_max[k]
            min_val = proj_min[k]
            for hid, verts in enumerate(per_hex):
                # Compute this hex's min/max along normal
                local_dots = [vx * n[0] + vy * n[1] for (vx, vy) in verts]
                if max(local_dots) >= max_val - tol:
                    active_ids.add(hid)
                if min(local_dots) <= min_val + tol:
                    active_ids.add(hid)
        return active_ids

    # ---------- Micro-rotation and inward translation on active hexes ----------
    def try_micro_rotation_and_translation(layout, center, alpha_normals_deg):
        # Returns a new layout after per-hex nudges and whether any changes applied
        # Keep SAT disjointness at all times
        base_layout = [p[:] for p in layout]
        changed = False
        # Rotation candidates in degrees
        rot_candidates = [-0.6, -0.3, 0.0, 0.3, 0.6]

        # Current apothem proxy
        a_cur, a_vals, gp, gn, normals, proj_min, proj_max = apothem_proxy_for(layout, center, alpha_normals_deg)

        # Determine active hexids
        A = active_hex_ids(layout, center, alpha_normals_deg, tol=1e-10)
        if not A:
            return base_layout, False

        def apothem_proxy_only(lay):
            ac, _, _, _, _, _, _ = apothem_proxy_for(lay, center, alpha_normals_deg)
            return ac

        # Try per-hex micro-rotations
        for hid in A:
            best_delta = 0.0
            best_a = a_cur
            # test rotations
            for delta in rot_candidates:
                if abs(delta) < 1e-16:
                    # Always valid (no change)
                    continue
                trial = [p[:] for p in layout]
                trial[hid][2] = trial[hid][2] + delta
                if not all_disjoint(trial):
                    continue
                a_try = apothem_proxy_only(trial)
                if a_try < best_a - 1e-12:
                    best_a = a_try
                    best_delta = delta
            if abs(best_delta) > 1e-16:
                layout[hid][2] += best_delta
                a_cur = best_a
                changed = True

            # Try a tiny inward translation along the most limiting active axis
            # Recompute details after rotation
            a_cur, a_vals, gp, gn, normals, proj_min, proj_max = apothem_proxy_for(layout, center, alpha_normals_deg)
            k_star = max(range(3), key=lambda k: a_vals[k])
            n = normals[k_star]
            # For this hex, decide direction using its local contribution
            # Compute local min/max projections
            hx, hy, hang = layout[hid]
            vhex = hexagon_vertices(hx, hy, unit_side, hang)
            dots = [vx * n[0] + vy * n[1] for (vx, vy) in vhex]
            local_max = max(dots)
            local_min = min(dots)
            nC = center[0] * n[0] + center[1] * n[1]
            gp_local = local_max - nC
            gn_local = nC - local_min
            # If gp dominates, move inward along -n; else along +n
            dir_sign = -1.0 if gp_local >= gn_local else 1.0
            tdir = (dir_sign * n[0], dir_sign * n[1])
            # Backtracking translation with SAT guards
            tau = min(0.04, 0.1 * a_cur)
            improved = False
            while tau > 5e-7:
                trial = [p[:] for p in layout]
                trial[hid][0] = trial[hid][0] + tdir[0] * tau
                trial[hid][1] = trial[hid][1] + tdir[1] * tau
                if not all_disjoint(trial):
                    tau *= 0.5
                    continue
                a_try = apothem_proxy_only(trial)
                if a_try < a_cur - 1e-12:
                    layout = trial
                    a_cur = a_try
                    changed = True
                    improved = True
                    break
                tau *= 0.5
            # continue to next hex
        return layout, changed

    # ---------- Reverse radial deflation ----------
    def reverse_radial_deflation(layout):
        # Uniformly contract centers toward centroid by the largest factor λ ≤ 1
        # that preserves SAT disjointness. Use bisection with an initial bracket.
        C = centroid(layout)
        # Establish bracket [lo (not disjoint), hi (disjoint)]
        hi = 1.0
        lo = 0.95
        def apply_lambda(lmbd):
            return scale_about_C(layout, C, lmbd)
        # Ensure lo is feasible bracket
        if all_disjoint(apply_lambda(lo)):
            # Can contract to lo; try to push even more conservatively but keep 0.95 as limit
            pass
        else:
            # Increase lo until it becomes disjoint or reaches hi
            # Binary search to find initial bracket
            a = lo
            b = hi
            for _ in range(10):
                mid = 0.5 * (a + b)
                if all_disjoint(apply_lambda(mid)):
                    b = mid
                else:
                    a = mid
            lo = a
            hi = b
        # Now refine within [lo, hi] keeping hi disjoint
        for _ in range(24):
            mid = 0.5 * (lo + hi)
            if all_disjoint(apply_lambda(mid)):
                hi = mid
            else:
                lo = mid
        return apply_lambda(hi)

    # ---------- Seed processing pipeline ----------
    def process_seed(seed_layout):
        # Step 1: Minimal SAT inflation
        inflated_layout, C0, s_init = minimal_radial_inflation(seed_layout)

        # Step 2: Outer fit
        S_best, C_best, theta_best = outer_fit_pipeline(inflated_layout)

        # Short multi-pass loop: active nudges + reverse deflation + refit
        passes = 3
        improve_tol = 3e-6
        cur_layout = [p[:] for p in inflated_layout]
        cur_S = S_best
        cur_C = C_best
        cur_theta = theta_best

        for _ in range(passes):
            before_layout = [p[:] for p in cur_layout]
            before_S = cur_S
            before_C = cur_C
            before_theta = cur_theta

            # Use normals phase alpha = theta + 30°
            alpha_norm = cur_theta + 30.0

            # Active micro-rotation nudges + inward translations
            nudged_layout, changed = try_micro_rotation_and_translation(cur_layout, cur_C, alpha_norm)

            if changed:
                cur_layout = nudged_layout

            # Reverse radial deflation to recover initial inflation if possible
            cur_layout = reverse_radial_deflation(cur_layout)

            # Refit outer hex after adjustments
            S_new, C_new, theta_new = outer_fit_pipeline(cur_layout)

            if S_new < before_S - improve_tol:
                # Accept pass
                cur_S, cur_C, cur_theta = S_new, C_new, theta_new
            else:
                # Revert if no measurable improvement
                cur_layout = before_layout
                cur_S, cur_C, cur_theta = before_S, before_C, before_theta
                break  # no further progress likely

        return cur_layout, cur_C, cur_S, cur_theta

    # ---------- Evaluate both seeds and choose the best ----------
    seeds = [seed_uniform_30(), seed_mixed_rows()]
    best_overall = None  # (layout, center, S, theta)

    for s in seeds:
        layout, C, S, theta = process_seed(s)
        if best_overall is None or S < best_overall[2]:
            best_overall = (layout, C, S, theta)

    best_layout, best_center, best_S, best_theta = best_overall

    # ---------- Shift to positive quadrant with a small margin ----------
    def collect_min_xy(layout, center):
        verts, _ = all_vertices(layout)
        min_x = min([vx for (vx, _) in verts] + [center[0]])
        min_y = min([vy for (_, vy) in verts] + [center[1]])
        return min_x, min_y

    margin = 0.1
    min_x, min_y = collect_min_xy(best_layout, best_center)
    dx = (margin - min_x) if min_x < margin else 0.0
    dy = (margin - min_y) if min_y < margin else 0.0

    inner_shifted = [[x + dx, y + dy, ang] for (x, y, ang) in best_layout]
    outer_center = [best_center[0] + dx, best_center[1] + dy]
    outer_side_length = float(best_S + 1e-9)  # tiny inflation for robustness
    outer_angle_degrees = float(best_theta)

    return inner_shifted, outer_center, outer_side_length, outer_angle_degrees
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
"""Hybrid SAT-tight 4–3–4 packing with radial inflation/deflation, active-normal
adaptive orientation line search + face-alignment rescue, Chebyshev-style center
correction, and golden + micro outer-orientation search.

This implements the requested crossover/mutation:

- Dual seeds (4–3–4 honeycomb centers):
  Seed A: all inner hexagons at 30° (pointy-top).
  Seed B: mixed rows: outer rows 0°, middle row 30°.

- Exact honeycomb spacing (H = √3, V = 1.5) with zero built-in slack.
  A minimal SAT-clean uniform radial inflation is found by bisection and applied
  to eliminate tangencies safely with near-zero slack.

- Outer hex fit pipeline:
  • Coarse sweep of the normals orientation α ∈ [0°, 60°) in 1° steps.
  • Golden-section refinement around the best α.
  • Active-normal Chebyshev-style center correction to reduce the apothem.
  • Final micro-sweep in ±0.5° around the refined α at 0.02° step.

- Support-aware per-hex adaptive micro-rotation nudges on the currently active limiting axes:
  Identify the active normals and the hexagons supporting those extremes. For each active
  hex, estimate the slope d a / d θ via symmetric finite differences at ±0.15°. Perform a
  monotone backtracking line search starting at ≈0.8° (halving on non-improvement or SAT
  violation) down to ≈0.02°. If slope-based search stalls, rotate towards the closest alignment
  of a face normal to the limiting outer-normal (up to ≈1.2°), using the same backtracking.
  After a successful rotation, attempt a tiny inward translation along the most limiting axis
  with backtracking and SAT guards.

- Reverse radial deflation:
  After micro-rotations/translations create new clearances, uniformly contract the centers
  toward their centroid via bisection while preserving SAT disjointness, recovering the
  initial inflation and further tightening the hull.

- Short multi-pass loop (2–3 passes) of nudges + deflation + refit, accepting a pass only
  on measurable improvement in the outer side length.

- Best-of-two seeds, shift to first quadrant, and return with a microscopic outer side
  length inflation for robustness.

All SAT checks and containment in external verification use the provided utilities.
This constructor maintains strict non-overlap and uses robust tolerances to pass
the verify_construction checks without modifying them.
"""

import json
import math
from typing import List, Tuple


# EVOLVE_START
def optimize_construct():
    """
    Construct a compact packing of 11 unit hexagons inside a minimal outer regular hexagon.

    Returns:
        inner_hexagons: List of [x, y, angle_degrees] for the 11 inner hexagons (side length fixed at 1).
        outer_center: [x, y] center of the outer hexagon.
        outer_side_length: Side length of the outer hexagon.
        outer_angle_degrees: Rotation angle (vertex-phase) of the outer hexagon in degrees.
    """
    # -------- Constants and basic geometry helpers ----------
    EPS = 1e-9
    sqrt3 = math.sqrt(3.0)
    unit_side = 1.0

    # Local utilities (mirroring the provided verification ones, used only internally)
    def hexagon_vertices(center_x: float, center_y: float, side_length: float, angle_degrees: float):
        verts = []
        angle_radians = math.radians(angle_degrees)
        for i in range(6):
            ang = angle_radians + 2.0 * math.pi * i / 6.0
            x = center_x + side_length * math.cos(ang)
            y = center_y + side_length * math.sin(ang)
            verts.append((x, y))
        return verts

    def normalize_vector(v):
        mag = math.hypot(v[0], v[1])
        return (v[0] / mag, v[1] / mag) if mag > EPS else (0.0, 0.0)

    def get_normals(vertices):
        normals = []
        n = len(vertices)
        for i in range(n):
            p1 = vertices[i]
            p2 = vertices[(i + 1) % n]
            edge = (p2[0] - p1[0], p2[1] - p1[1])
            normal = normalize_vector((-edge[1], edge[0]))
            normals.append(normal)
        return normals

    def project_polygon(vertices, axis):
        min_proj = float('inf')
        max_proj = float('-inf')
        ax, ay = axis
        for (x, y) in vertices:
            p = x * ax + y * ay
            if p < min_proj:
                min_proj = p
            if p > max_proj:
                max_proj = p
        return min_proj, max_proj

    def overlap_1d(min1, max1, min2, max2):
        # Return True if intervals overlap (touching counts as overlap)
        return max1 >= min2 - EPS and max2 >= min1 - EPS

    def polygons_intersect(vertices1, vertices2):
        # Separating Axis Theorem for convex polygons
        normals = get_normals(vertices1) + get_normals(vertices2)
        for axis in normals:
            min1, max1 = project_polygon(vertices1, axis)
            min2, max2 = project_polygon(vertices2, axis)
            if not overlap_1d(min1, max1, min2, max2):
                return False
        return True

    def hexagons_are_disjoint(h1, h2):
        # h = (x, y, side=1, angle)
        v1 = hexagon_vertices(h1[0], h1[1], unit_side, h1[3])
        v2 = hexagon_vertices(h2[0], h2[1], unit_side, h2[3])
        return not polygons_intersect(v1, v2)

    # Layout helpers
    def layout_to_params(layout):
        # Converts [x, y, ang] to params (x, y, 1.0, ang) for SAT
        return [(x, y, unit_side, ang) for (x, y, ang) in layout]

    def all_disjoint(layout):
        params = layout_to_params(layout)
        n = len(params)
        for i in range(n):
            for j in range(i + 1, n):
                if not hexagons_are_disjoint(params[i], params[j]):
                    return False
        return True

    def all_vertices(layout):
        # Collect all vertices across all inner hexes; also return per-hex vertices
        verts = []
        per_hex = []
        for (x, y, ang) in layout:
            v = hexagon_vertices(x, y, unit_side, ang)
            verts.extend(v)
            per_hex.append(v)
        return verts, per_hex

    # --------- Seed generation (4–3–4 honeycomb) ----------
    H = sqrt3   # exact honeycomb horizontal step (no slack)
    V = 1.5     # exact honeycomb vertical step (no slack)

    def seed_uniform_30() -> List[List[float]]:
        inner = []
        # Row 0 (y=0), 4 hexes
        y0 = 0.0
        for j in range(4):
            inner.append([j * H, y0, 30.0])
        # Row 1 (y=V), 3 hexes, offset by 0.5 H
        y1 = V
        for j in range(3):
            inner.append([0.5 * H + j * H, y1, 30.0])
        # Row 2 (y=2V), 4 hexes
        y2 = 2.0 * V
        for j in range(4):
            inner.append([j * H, y2, 30.0])
        return inner

    def seed_mixed_rows() -> List[List[float]]:
        inner = []
        # Row 0 at 0°
        y0 = 0.0
        for j in range(4):
            inner.append([j * H, y0, 0.0])
        # Middle row at 30°
        y1 = V
        for j in range(3):
            inner.append([0.5 * H + j * H, y1, 30.0])
        # Row 2 at 0°
        y2 = 2.0 * V
        for j in range(4):
            inner.append([j * H, y2, 0.0])
        return inner

    # ---------- Minimal SAT-clean radial inflation ----------
    def centroid(layout):
        sx = sum(p[0] for p in layout)
        sy = sum(p[1] for p in layout)
        n = len(layout)
        return (sx / n, sy / n)

    def scale_about_C(layout, C, s):
        cx, cy = C
        new = []
        for (x, y, ang) in layout:
            nx = cx + s * (x - cx)
            ny = cy + s * (y - cy)
            new.append([nx, ny, ang])
        return new

    def minimal_radial_inflation(layout):
        # Ensure disjointness by scaling outward about centroid by minimal s>=1
        C = centroid(layout)
        s_lo = 1.0
        s_hi = 1.0005
        # Increase s_hi until disjoint
        scaled = scale_about_C(layout, C, s_hi)
        tries = 0
        while not all_disjoint(scaled) and tries < 60:
            s_hi *= 1.01
            scaled = scale_about_C(layout, C, s_hi)
            tries += 1
        # Now bisection between [s_lo, s_hi]
        for _ in range(40):
            s_mid = 0.5 * (s_lo + s_hi)
            test = scale_about_C(layout, C, s_mid)
            if all_disjoint(test):
                s_hi = s_mid
                scaled = test
            else:
                s_lo = s_mid
        return scaled, C, s_hi

    # ---------- Outer hex fit and refinements ----------
    # Compute vertices once per layout when needed
    def fit_outer_hex(layout, alpha_deg):
        # alpha_deg is normals phase. Outward normals at alpha, alpha+60, alpha+120
        verts_all, _ = all_vertices(layout)
        normals = []
        for k in range(3):
            ang = math.radians(alpha_deg + 60.0 * k)
            normals.append((math.cos(ang), math.sin(ang)))

        proj_min = []
        proj_max = []
        for nvec in normals:
            dots = [vx * nvec[0] + vy * nvec[1] for (vx, vy) in verts_all]
            proj_min.append(min(dots))
            proj_max.append(max(dots))

        # Least-squares center: solve dot(n_k, C) ≈ (max_k + min_k)/2
        c_vec = [(proj_max[k] + proj_min[k]) * 0.5 for k in range(3)]
        A = normals
        ata00 = sum(A[i][0] * A[i][0] for i in range(3))
        ata01 = sum(A[i][0] * A[i][1] for i in range(3))
        ata11 = sum(A[i][1] * A[i][1] for i in range(3))
        atc0 = sum(A[i][0] * c_vec[i] for i in range(3))
        atc1 = sum(A[i][1] * c_vec[i] for i in range(3))
        det = ata00 * ata11 - ata01 * ata01
        if abs(det) < 1e-14:
            cx, cy = 0.0, 0.0
        else:
            inv00 = ata11 / det
            inv01 = -ata01 / det
            inv11 = ata00 / det
            cx = inv00 * atc0 + inv01 * atc1
            cy = inv01 * atc0 + inv11 * atc1

        # Apothem at LS center
        a_vals = []
        for k in range(3):
            nC = cx * normals[k][0] + cy * normals[k][1]
            a_k = max(proj_max[k] - nC, nC - proj_min[k])
            a_vals.append(a_k)
        a_ls = max(a_vals)

        # Active-normal Chebyshev-style center correction
        def refine_center(C0):
            C = (C0[0], C0[1])

            def apothem_and_details(C):
                cx, cy = C
                a_list = []
                gp = []
                gn = []
                for k in range(3):
                    nC = cx * normals[k][0] + cy * normals[k][1]
                    gpos = proj_max[k] - nC
                    gneg = nC - proj_min[k]
                    gp.append(gpos)
                    gn.append(gneg)
                    a_list.append(max(gpos, gneg))
                return max(a_list), a_list, gp, gn

            a_cur, a_list, gp, gn = apothem_and_details(C)
            for _ in range(6):
                K = [k for k in range(3) if a_list[k] >= a_cur - 1e-12]
                dx = 0.0
                dy = 0.0
                for k in K:
                    sgn = 1.0 if gp[k] >= gn[k] else -1.0
                    dx += sgn * normals[k][0]
                    dy += sgn * normals[k][1]
                dnorm = math.hypot(dx, dy)
                if dnorm < 1e-14:
                    break
                dx /= dnorm
                dy /= dnorm
                tau = 0.15 * a_cur
                improved = False
                while tau > 1e-12:
                    Cn = (C[0] - tau * dx, C[1] - tau * dy)
                    a_new, a_list_new, gp_new, gn_new = apothem_and_details(Cn)
                    if a_new < a_cur - 1e-12:
                        C = Cn
                        a_cur = a_new
                        a_list = a_list_new
                        gp = gp_new
                        gn = gn_new
                        improved = True
                        break
                    tau *= 0.5
                if not improved:
                    break
            return C, a_cur

        C_ref, a_ref = refine_center((cx, cy))
        a_final = min(a_ls, a_ref)
        S = (2.0 * a_final) / sqrt3
        theta_deg = alpha_deg - 30.0
        if a_ref <= a_ls:
            return S, (C_ref[0], C_ref[1]), theta_deg
        else:
            return S, (cx, cy), theta_deg

    def norm_alpha(a):
        r = a % 60.0
        return r if r >= 0.0 else (r + 60.0)

    def eval_alpha(layout, alpha):
        return fit_outer_hex(layout, norm_alpha(alpha))

    def golden_refine(layout, alpha0, span=3.0, iterations=20):
        lo = alpha0 - span
        hi = alpha0 + span
        invphi = (math.sqrt(5.0) - 1.0) / 2.0
        x1 = hi - invphi * (hi - lo)
        x2 = lo + invphi * (hi - lo)
        f1 = eval_alpha(layout, x1)
        f2 = eval_alpha(layout, x2)
        best = f1 if f1[0] <= f2[0] else f2
        for _ in range(iterations):
            if f1[0] < f2[0]:
                hi = x2
                x2 = x1
                f2 = f1
                x1 = hi - invphi * (hi - lo)
                f1 = eval_alpha(layout, x1)
            else:
                lo = x1
                x1 = x2
                f1 = f2
                x2 = lo + invphi * (hi - lo)
                f2 = eval_alpha(layout, x2)
            if f1[0] < best[0]:
                best = f1
            if f2[0] < best[0]:
                best = f2
        return best

    def micro_sweep(layout, alpha_center, half_width=0.5, step=0.02):
        best_S = float('inf')
        best_C = (0.0, 0.0)
        best_theta = 0.0
        a0 = alpha_center - half_width
        a1 = alpha_center + half_width
        nsteps = int(round((a1 - a0) / step)) + 1
        for i in range(nsteps):
            a = a0 + i * step
            S, C, theta = eval_alpha(layout, a)
            if S < best_S:
                best_S, best_C, best_theta = S, C, theta
        return best_S, best_C, best_theta

    def outer_fit_pipeline(layout):
        # Coarse sweep
        coarse_S = float('inf')
        coarse_C = (0.0, 0.0)
        coarse_theta = 0.0
        coarse_alpha = 0.0
        for alpha in range(60):
            S, C, theta = fit_outer_hex(layout, float(alpha))
            if S < coarse_S:
                coarse_S, coarse_C, coarse_theta, coarse_alpha = S, C, theta, float(alpha)
        # Golden refine
        ref_S, ref_C, ref_theta = golden_refine(layout, coarse_alpha, span=3.0, iterations=20)
        # Micro sweep around refined α ≈ ref_theta + 30°
        micro_S, micro_C, micro_theta = micro_sweep(layout, norm_alpha(ref_theta + 30.0), half_width=0.5, step=0.02)
        # Best across stages
        best_S = coarse_S
        best_C = coarse_C
        best_theta = coarse_theta
        if ref_S < best_S:
            best_S, best_C, best_theta = ref_S, ref_C, ref_theta
        if micro_S < best_S:
            best_S, best_C, best_theta = micro_S, micro_C, micro_theta
        return best_S, best_C, best_theta

    # ---------- Support computation and active set ----------
    def apothem_proxy_for(layout, center, alpha_normals_deg):
        verts_all, _ = all_vertices(layout)
        normals = []
        for k in range(3):
            ang = math.radians(alpha_normals_deg + 60.0 * k)
            normals.append((math.cos(ang), math.sin(ang)))

        proj_min = []
        proj_max = []
        for nvec in normals:
            dots = [vx * nvec[0] + vy * nvec[1] for (vx, vy) in verts_all]
            proj_min.append(min(dots))
            proj_max.append(max(dots))

        cx, cy = center
        a_vals = []
        gap_pos = []
        gap_neg = []
        for k in range(3):
            nC = cx * normals[k][0] + cy * normals[k][1]
            gp = proj_max[k] - nC
            gn = nC - proj_min[k]
            gap_pos.append(gp)
            gap_neg.append(gn)
            a_vals.append(max(gp, gn))
        a = max(a_vals)
        return a, a_vals, gap_pos, gap_neg, normals, proj_min, proj_max

    def active_hex_ids(layout, center, alpha_normals_deg, tol=1e-10):
        # Returns the set of hex indices contributing to extreme supports on active normals
        _, per_hex = all_vertices(layout)
        a, a_vals, gp, gn, normals, proj_min, proj_max = apothem_proxy_for(layout, center, alpha_normals_deg)

        # Identify active normals indices
        K = [k for k in range(3) if a_vals[k] >= a - 1e-12]
        active_ids = set()
        # For each active normal, find hexes with vertices at extremes
        for k in K:
            n = normals[k]
            # Find max and min contributors
            max_val = proj_max[k]
            min_val = proj_min[k]
            for hid, verts in enumerate(per_hex):
                # Compute this hex's min/max along normal
                local_dots = [vx * n[0] + vy * n[1] for (vx, vy) in verts]
                if max(local_dots) >= max_val - tol:
                    active_ids.add(hid)
                if min(local_dots) <= min_val + tol:
                    active_ids.add(hid)
        return active_ids

    # ---------- Adaptive micro-rotation and inward translation on active hexes ----------
    def try_micro_rotation_and_translation(layout, center, alpha_normals_deg):
        # Returns a new layout after per-hex nudges and whether any changes applied
        base_layout = [p[:] for p in layout]
        changed = False

        # Current apothem proxy and limiting axis
        a_cur, a_vals, gp, gn, normals, proj_min, proj_max = apothem_proxy_for(layout, center, alpha_normals_deg)
        k_star = max(range(3), key=lambda k: a_vals[k])
        limiting_normal = normals[k_star]
        limiting_angle_deg = alpha_normals_deg + 60.0 * k_star  # direction of outward normal realizing max apothem

        # Determine active hexids
        A = active_hex_ids(layout, center, alpha_normals_deg, tol=1e-10)
        if not A:
            return base_layout, False

        def apothem_proxy_only(lay):
            ac, _, _, _, _, _, _ = apothem_proxy_for(lay, center, alpha_normals_deg)
            return ac

        # Helper: check and accept candidate layout if improves apothem and keeps SAT
        def maybe_accept(trial, current_best):
            if not all_disjoint(trial):
                return None
            a_try = apothem_proxy_only(trial)
            if a_try < current_best - 1e-12:
                return a_try
            return None

        # For each active hex: adaptive slope-based rotation, then face-alignment rescue if needed
        for hid in A:
            # Slope estimate at ±delta
            delta_probe = 0.15  # degrees
            base_ang = layout[hid][2]
            # Symmetric difference
            trial_minus = [p[:] for p in layout]
            trial_minus[hid][2] = base_ang - delta_probe
            if not all_disjoint(trial_minus):
                a_minus = float('inf')
            else:
                a_minus = apothem_proxy_only(trial_minus)

            trial_plus = [p[:] for p in layout]
            trial_plus[hid][2] = base_ang + delta_probe
            if not all_disjoint(trial_plus):
                a_plus = float('inf')
            else:
                a_plus = apothem_proxy_only(trial_plus)

            slope = None
            if math.isfinite(a_minus) and math.isfinite(a_plus):
                slope = (a_plus - a_minus) / (2.0 * delta_probe)

            improved_this_hex = False

            # Backtracking line search along negative slope direction
            if slope is not None and abs(slope) > 1e-16:
                direction = -1.0 if slope > 0.0 else 1.0
                step = 0.8  # degrees
                min_step = 0.02
                best_a_local = a_cur
                best_layout_local = None
                best_angle = base_ang
                while step >= min_step - 1e-12:
                    trial = [p[:] for p in layout]
                    trial[hid][2] = base_ang + direction * step
                    a_try = maybe_accept(trial, a_cur)
                    if a_try is not None:
                        best_a_local = a_try
                        best_layout_local = trial
                        best_angle = trial[hid][2]
                        break
                    step *= 0.5
                if best_layout_local is not None:
                    layout = best_layout_local
                    a_cur = best_a_local
                    improved_this_hex = True
                    changed = True

            # Face-alignment rescue if slope-based failed
            if not improved_this_hex:
                # Rotate to bring a face-normal of the inner hex close to limiting_normal
                # Face normals of inner hex occur at angles hang + 30 + 60*m
                hang = layout[hid][2]
                # Bring limiting_angle into same modulo-60 frame
                # Compute target rotation delta so that hang + delta + 30 ≡ limiting_angle (mod 60)
                # Reduce to nearest representative in [-30, 30]
                raw_delta = (limiting_angle_deg - (hang + 30.0)) % 60.0
                if raw_delta > 30.0:
                    raw_delta -= 60.0
                # Cap magnitude to 1.2°
                cap = 1.2
                if abs(raw_delta) > cap:
                    raw_delta = cap if raw_delta > 0 else -cap
                # Backtracking along this target
                step = abs(raw_delta)
                direction = 1.0 if raw_delta >= 0 else -1.0
                min_step = 0.02
                best_a_local = a_cur
                best_layout_local = None
                while step >= min_step - 1e-12 and step > 1e-12:
                    trial = [p[:] for p in layout]
                    trial[hid][2] = layout[hid][2] + direction * step
                    a_try = maybe_accept(trial, a_cur)
                    if a_try is not None:
                        best_a_local = a_try
                        best_layout_local = trial
                        break
                    step *= 0.5
                if best_layout_local is not None:
                    layout = best_layout_local
                    a_cur = best_a_local
                    improved_this_hex = True
                    changed = True

            # Inward translation attempt along most limiting axis
            # Recompute details after rotation
            a_cur, a_vals, gp, gn, normals, proj_min, proj_max = apothem_proxy_for(layout, center, alpha_normals_deg)
            k_star = max(range(3), key=lambda k: a_vals[k])
            n = normals[k_star]
            # For this hex, decide direction using its local contribution
            hx, hy, hang = layout[hid]
            vhex = hexagon_vertices(hx, hy, unit_side, hang)
            dots = [vx * n[0] + vy * n[1] for (vx, vy) in vhex]
            local_max = max(dots)
            local_min = min(dots)
            nC = center[0] * n[0] + center[1] * n[1]
            gp_local = local_max - nC
            gn_local = nC - local_min
            dir_sign = -1.0 if gp_local >= gn_local else 1.0  # inward direction
            tdir = (dir_sign * n[0], dir_sign * n[1])
            # Backtracking translation with SAT guards
            tau = min(0.04, 0.1 * a_cur)
            while tau > 5e-7:
                trial = [p[:] for p in layout]
                trial[hid][0] = trial[hid][0] + tdir[0] * tau
                trial[hid][1] = trial[hid][1] + tdir[1] * tau
                a_try = maybe_accept(trial, a_cur)
                if a_try is not None:
                    layout = trial
                    a_cur = a_try
                    changed = True
                    break
                tau *= 0.5

        return layout, changed

    # ---------- Reverse radial deflation ----------
    def reverse_radial_deflation(layout):
        # Uniformly contract centers toward centroid by the largest factor λ ≤ 1
        # that preserves SAT disjointness. Use bisection.
        C = centroid(layout)
        def apply_lambda(lmbd):
            return scale_about_C(layout, C, lmbd)

        # Establish bracket: hi is known feasible (1.0), find lo infeasible or low bound
        lo = 0.9
        hi = 1.0
        # Ensure hi disjoint (should be)
        assert all_disjoint(apply_lambda(hi))
        # If lo already disjoint, we'll bisection anyway to push lower
        # Bisection on [lo, hi] keeping hi disjoint
        for _ in range(28):
            mid = 0.5 * (lo + hi)
            if all_disjoint(apply_lambda(mid)):
                hi = mid
            else:
                lo = mid
        return apply_lambda(hi)

    # ---------- Seed processing pipeline ----------
    def process_seed(seed_layout):
        # Step 1: Minimal SAT inflation
        inflated_layout, C0, s_init = minimal_radial_inflation(seed_layout)

        # Step 2: Outer fit
        S_best, C_best, theta_best = outer_fit_pipeline(inflated_layout)

        # Short multi-pass loop: active nudges + reverse deflation + refit
        passes = 3
        improve_tol = 3e-6
        cur_layout = [p[:] for p in inflated_layout]
        cur_S = S_best
        cur_C = C_best
        cur_theta = theta_best

        for _ in range(passes):
            before_layout = [p[:] for p in cur_layout]
            before_S = cur_S
            before_C = cur_C
            before_theta = cur_theta

            # Use normals phase alpha = theta + 30°
            alpha_norm = cur_theta + 30.0

            # Adaptive micro-rotation nudges + inward translations on active hexes
            nudged_layout, changed = try_micro_rotation_and_translation(cur_layout, cur_C, alpha_norm)

            if changed:
                cur_layout = nudged_layout

            # Reverse radial deflation to recover initial inflation if possible
            cur_layout = reverse_radial_deflation(cur_layout)

            # Refit outer hex after adjustments
            S_new, C_new, theta_new = outer_fit_pipeline(cur_layout)

            if S_new < before_S - improve_tol:
                # Accept pass
                cur_S, cur_C, cur_theta = S_new, C_new, theta_new
            else:
                # Revert if no measurable improvement
                cur_layout = before_layout
                cur_S, cur_C, cur_theta = before_S, before_C, before_theta
                break  # no further progress likely

        return cur_layout, cur_C, cur_S, cur_theta

    # ---------- Evaluate both seeds and choose the best ----------
    seeds = [seed_uniform_30(), seed_mixed_rows()]
    best_overall = None  # (layout, center, S, theta)

    for s in seeds:
        layout, C, S, theta = process_seed(s)
        if best_overall is None or S < best_overall[2]:
            best_overall = (layout, C, S, theta)

    best_layout, best_center, best_S, best_theta = best_overall

    # ---------- Shift to positive quadrant with a small margin ----------
    def collect_min_xy(layout, center):
        verts, _ = all_vertices(layout)
        min_x = min([vx for (vx, _) in verts] + [center[0]])
        min_y = min([vy for (_, vy) in verts] + [center[1]])
        return min_x, min_y

    margin = 0.1
    min_x, min_y = collect_min_xy(best_layout, best_center)
    dx = (margin - min_x) if min_x < margin else 0.0
    dy = (margin - min_y) if min_y < margin else 0.0

    inner_shifted = [[x + dx, y + dy, ang] for (x, y, ang) in best_layout]
    outer_center = [best_center[0] + dx, best_center[1] + dy]
    outer_side_length = float(best_S + 1e-9)  # tiny inflation for robustness
    outer_angle_degrees = float(best_theta)

    return inner_shifted, outer_center, outer_side_length, outer_angle_degrees
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
"""Optimization candidate for packing 11 unit hexagons into a minimal regular hexagon.

We implement a hybrid shrink-wrap based on directional supports (outer facet normals) and
multi-seed initialization. We keep feasibility by ensuring disjoint inner hexagons and use
an outer-center least-squares fit that balances opposite supports for a given rotation.
"""

import json
import math
from typing import List, Tuple

# Small epsilon for numerical robustness
EPSILON = 1e-9


# -----------------------------
# Geometry utility functions
# -----------------------------
def hexagon_vertices(center_x: float, center_y: float, side_length: float, angle_degrees: float):
    """
    Calculate the vertices of a hexagon.

    Args:
        center_x: x-coordinate of hexagon center
        center_y: y-coordinate of hexagon center
        side_length: Length of hexagon side (equals circumradius for regular hex)
        angle_degrees: Rotation angle in degrees (controls first vertex direction)

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


def normalize_vector(v: Tuple[float, float]) -> Tuple[float, float]:
    """
    Normalize a 2D vector to unit length.

    Args:
        v: (x, y) tuple representing the vector

    Returns:
        tuple: Normalized (x, y) vector
    """
    magnitude = math.sqrt(v[0] ** 2 + v[1] ** 2)
    return (v[0] / magnitude, v[1] / magnitude) if magnitude > EPSILON else (0.0, 0.0)


def get_normals(vertices: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
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


def project_polygon(vertices: List[Tuple[float, float]], axis: Tuple[float, float]) -> Tuple[float, float]:
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


def overlap_1d(min1: float, max1: float, min2: float, max2: float) -> bool:
    """
    Check if two 1D intervals overlap (with epsilon tolerance).

    Args:
        min1, max1: Start and end of first interval
        min2, max2: Start and end of second interval

    Returns:
        bool: True if intervals overlap, False otherwise
    """
    return max1 >= min2 - EPSILON and max2 >= min1 - EPSILON


def polygons_intersect(vertices1: List[Tuple[float, float]], vertices2: List[Tuple[float, float]]) -> bool:
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


def hexagons_are_disjoint(hex1_params, hex2_params) -> bool:
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


def is_inside_hexagon(point: Tuple[float, float], hex_params) -> bool:
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


def all_hexagons_contained(inner_hex_params_list, outer_hex_params) -> bool:
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


def verify_construction(inner_hex_data, outer_hex_center, outer_hex_side_length, outer_hex_angle_degrees) -> bool:
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
        (x, y, 1.0, angle) for x, y, angle in inner_hex_data
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


# -----------------------------
# Packing algorithm utilities
# -----------------------------
def make_seed_434() -> List[Tuple[float, float]]:
    """Build centers for a 4-3-4 honeycomb layout with pointy-top spacing."""
    xs = math.sqrt(3.0)
    row_step = 1.5
    # rows: y = -row_step, 0, +row_step
    yvals = [-row_step, 0.0, row_step]
    # x positions for rows of 4 and 3
    xs4 = [-1.5 * xs, -0.5 * xs, 0.5 * xs, 1.5 * xs]
    xs3 = [-1.0 * xs, 0.0, 1.0 * xs]
    centers = []
    # bottom 4
    for x in xs4:
        centers.append((x, yvals[0]))
    # middle 3
    for x in xs3:
        centers.append((x, yvals[1]))
    # top 4
    for x in xs4:
        centers.append((x, yvals[2]))
    return centers


def make_seed_344() -> List[Tuple[float, float]]:
    """Build centers for a 3-4-4 honeycomb layout."""
    xs = math.sqrt(3.0)
    row_step = 1.5
    yvals = [-row_step, 0.0, row_step]
    xs4 = [-1.5 * xs, -0.5 * xs, 0.5 * xs, 1.5 * xs]
    xs3 = [-1.0 * xs, 0.0, 1.0 * xs]
    centers = []
    # bottom 3
    for x in xs3:
        centers.append((x, yvals[0]))
    # middle 4
    for x in xs4:
        centers.append((x, yvals[1]))
    # top 4
    for x in xs4:
        centers.append((x, yvals[2]))
    return centers


def make_seed_443() -> List[Tuple[float, float]]:
    """Build centers for a 4-4-3 honeycomb layout."""
    xs = math.sqrt(3.0)
    row_step = 1.5
    yvals = [-row_step, 0.0, row_step]
    xs4 = [-1.5 * xs, -0.5 * xs, 0.5 * xs, 1.5 * xs]
    xs3 = [-1.0 * xs, 0.0, 1.0 * xs]
    centers = []
    # bottom 4
    for x in xs4:
        centers.append((x, yvals[0]))
    # middle 4
    for x in xs4:
        centers.append((x, yvals[1]))
    # top 3
    for x in xs3:
        centers.append((x, yvals[2]))
    return centers


def make_seed_ring() -> List[Tuple[float, float]]:
    """Build centers for a ring-based seed: center + 6 neighbors + 4 from second ring."""
    xs = math.sqrt(3.0)
    # Primary neighbor vectors for pointy-top grid
    v1 = (xs, 0.0)
    v2 = (0.5 * xs, 1.5)
    v3 = (-0.5 * xs, 1.5)
    v4 = (-xs, 0.0)
    v5 = (-0.5 * xs, -1.5)
    v6 = (0.5 * xs, -1.5)

    centers = [(0.0, 0.0)]
    centers += [v1, v2, v3, v4, v5, v6]

    # Candidate second ring (sum of consecutive neighbor vectors)
    s12 = (v1[0] + v2[0], v1[1] + v2[1])
    s23 = (v2[0] + v3[0], v2[1] + v3[1])
    s34 = (v3[0] + v4[0], v3[1] + v4[1])
    s45 = (v4[0] + v5[0], v4[1] + v5[1])
    s56 = (v5[0] + v6[0], v5[1] + v6[1])
    s61 = (v6[0] + v1[0], v6[1] + v1[1])

    # Choose four of these, skipping the ones aligned with principal axes to reduce width
    # We'll pick s23, s34, s56, s61 (roughly balancing)
    centers += [s23, s34, s56, s61]
    return centers


def ensure_disjoint(centers: List[Tuple[float, float]], angle_deg: float) -> List[Tuple[float, float]]:
    """Ensure all hexagons at given centers are disjoint by slight radial expansion if needed."""
    # Convert to params
    def params_from_centers(cs):
        return [(c[0], c[1], 1.0, angle_deg) for c in cs]

    params = params_from_centers(centers)

    # Quick check
    def all_disjoint(ps):
        n = len(ps)
        for i in range(n):
            for j in range(i + 1, n):
                if not hexagons_are_disjoint(ps[i], ps[j]):
                    return False
        return True

    if all_disjoint(params):
        return centers

    # Radial expansion from centroid
    cx = sum([c[0] for c in centers]) / len(centers)
    cy = sum([c[1] for c in centers]) / len(centers)

    scale = 1.0
    max_scale = 1.05  # cap
    # Incremental scale-up
    for _ in range(600):
        scale += 1e-4
        new_centers = []
        for (x, y) in centers:
            vx = x - cx
            vy = y - cy
            new_centers.append((cx + scale * vx, cy + scale * vy))
        params = params_from_centers(new_centers)
        if all_disjoint(params):
            return new_centers
        if scale > max_scale:
            break
    # If still not disjoint, do a more aggressive incremental bump along pair normals
    # Fallback: small jitter (rarely needed)
    return centers


def compute_support_extents(all_vertices: List[Tuple[float, float]], n: Tuple[float, float]) -> Tuple[float, float]:
    """Compute min and max projection of all vertices onto axis n."""
    min_proj = float('inf')
    max_proj = float('-inf')
    for (x, y) in all_vertices:
        p = x * n[0] + y * n[1]
        if p < min_proj:
            min_proj = p
        if p > max_proj:
            max_proj = p
    return min_proj, max_proj


def best_outer_for_layout(inner_hex_data: List[Tuple[float, float, float]]) -> Tuple[List[float], float, float, float]:
    """
    For given inner hex data (x, y, angle), compute a near-minimal outer hex:
    - scan rotation
    - solve least-squares center to equalize opposite supports
    - compute apothem and side length
    Returns: (outer_center [cx, cy], outer_side_length, outer_angle_degrees, apothem)
    """
    # Precompute all vertices of inner hexes (we will recompute per angle if needed)
    # Since angles may be the same for all, but keep generality
    all_vertices = []
    for (x, y, ang) in inner_hex_data:
        verts = hexagon_vertices(x, y, 1.0, ang)
        all_vertices.extend(verts)

    # Scan outer rotation phi in [0, 60) deg, because of 6-fold symmetry
    best = {
        "a": float('inf'),
        "L": float('inf'),
        "phi_deg": 0.0,
        "center": (0.0, 0.0),
    }

    # Helper to compute least-squares center for given normals
    def lsq_center(normals: List[Tuple[float, float]], ci_targets: List[float]) -> Tuple[float, float]:
        # Solve: minimize sum_i (n_i · C - c_i)^2
        # (sum n_i n_i^T) C = sum c_i n_i
        Axx = Axy = Ayy = 0.0
        bx = by = 0.0
        for k, n in enumerate(normals):
            nx, ny = n
            c = ci_targets[k]
            Axx += nx * nx
            Axy += nx * ny
            Ayy += ny * ny
            bx += c * nx
            by += c * ny
        # Solve 2x2 linear system [Axx Axy; Axy Ayy] [cx; cy] = [bx; by]
        det = Axx * Ayy - Axy * Axy
        if abs(det) < 1e-12:
            return 0.0, 0.0
        cx = (bx * Ayy - by * Axy) / det
        cy = (Axx * by - Axy * bx) / det
        return cx, cy

    # Coarse scan and micro-refinement
    def scan_range(start_deg: float, end_deg: float, step_deg: float, best_state):
        phi = start_deg
        while phi < end_deg - 0.5 * step_deg + 1e-12:
            # Facet normals are at angles beta = phi + 30°, spaced 60°
            # We'll compute only three unique normals (pairs are opposites)
            beta = math.radians(phi + 30.0)
            normals3 = []
            for k in range(3):
                ang = beta + k * math.pi / 3.0  # 60° step
                normals3.append((math.cos(ang), math.sin(ang)))

            # For each normal, compute min and max of all vertices
            M = []
            m = []
            ci = []
            for n in normals3:
                mn, mx = compute_support_extents(all_vertices, n)
                m.append(mn)
                M.append(mx)
                ci.append(0.5 * (mx + mn))  # desired C·n balance target

            cx, cy = lsq_center(normals3, ci)

            # Compute apothem required: max over both sides per normal
            # a must satisfy: a >= max(M_i - n_i·C, -m_i + n_i·C)
            a_req = 0.0
            for i in range(3):
                ndotc = normals3[i][0] * cx + normals3[i][1] * cy
                a1 = M[i] - ndotc
                a2 = -m[i] + ndotc
                a_req = max(a_req, a1, a2)

            # Side length: L = 2a / sqrt(3)
            L = 2.0 * a_req / math.sqrt(3.0)

            if a_req < best_state["a"] - 1e-12 or (abs(a_req - best_state["a"]) < 1e-12 and L < best_state["L"]):
                best_state["a"] = a_req
                best_state["L"] = L
                best_state["phi_deg"] = phi
                best_state["center"] = (cx, cy)
            phi += step_deg

    # First coarse scan and then refine around best
    scan_range(0.0, 60.0, 0.5, best)
    # refine around best with smaller steps
    span = 1.5
    scan_range(max(0.0, best["phi_deg"] - span), min(60.0, best["phi_deg"] + span), 0.05, best)

    # The outer hex angle parameter controls vertices angles; facet normals are at phi + 30°,
    # thus outer vertex angle = phi (since vertices at phi, phi+60, ...), so we return phi_deg.
    outer_center = [best["center"][0], best["center"][1]]
    outer_side_length = best["L"]
    outer_angle_degrees = best["phi_deg"]

    return outer_center, outer_side_length, outer_angle_degrees, best["a"]


def shift_to_positive(inner_hex_data: List[List[float]], outer_center: List[float], margin: float = 1.0):
    """Shift the entire construction so that all centers are in x>0,y>0 with a margin."""
    min_x = min([x for x, _, _ in inner_hex_data] + [outer_center[0]])
    min_y = min([y for _, y, _ in inner_hex_data] + [outer_center[1]])
    dx = 0.0
    dy = 0.0
    if min_x < margin:
        dx = margin - min_x
    if min_y < margin:
        dy = margin - min_y
    if abs(dx) > 0 or abs(dy) > 0:
        for i in range(len(inner_hex_data)):
            inner_hex_data[i][0] += dx
            inner_hex_data[i][1] += dy
        outer_center[0] += dx
        outer_center[1] += dy


def try_seed(centers: List[Tuple[float, float]], global_angle_deg: float) -> Tuple[List[List[float]], List[float], float, float]:
    """
    Build inner hex data from centers and a global inner angle, ensure disjointness,
    and compute best outer hex via shrink-wrap. Returns the full construction.
    """
    centers = ensure_disjoint(centers, global_angle_deg)

    inner_hex_data = [[x, y, global_angle_deg] for (x, y) in centers]
    outer_center, outer_side_length, outer_angle_degrees, _ = best_outer_for_layout(inner_hex_data)

    # Slight cushion to be robust
    outer_side_length += 1e-9

    return inner_hex_data, outer_center, outer_side_length, outer_angle_degrees


# EVOLVE_START
def optimize_construct():
    """
    Orchestrate multi-seed, multi-orientation search with a reverse-deflation stage and
    return the best verified construction.

    Algorithm additions:
    - After building a disjoint layout for each seed+angle, uniformly contract (reverse-deflate)
      all centers toward their centroid by a global scale s in [s_lo, 1], using SAT-guarded
      bisection to find the smallest feasible s that preserves strict non-overlap.
    - Re-run the outer shrink-wrap (least-squares center + rotation scan) on the deflated layout.
    - Perform a verify-driven bisection trim on the outer side length to ensure minimality.
    """
    # -----------------------
    # Local helpers for EVOLVE block
    # -----------------------
    def layout_is_disjoint(centers: List[Tuple[float, float]], angle_deg: float) -> bool:
        """Check pairwise disjointness for given centers and a common inner angle."""
        params = [(x, y, 1.0, angle_deg) for (x, y) in centers]
        n = len(params)
        for i in range(n):
            for j in range(i + 1, n):
                if not hexagons_are_disjoint(params[i], params[j]):
                    return False
        return True

    def reverse_deflate_layout(centers: List[Tuple[float, float]], angle_deg: float,
                               s_lo: float = 0.986, iters: int = 32) -> List[Tuple[float, float]]:
        """
        Uniformly contract all centers about the centroid by a scale s in [s_lo, 1],
        using SAT-guarded bisection to find the smallest feasible s that preserves non-overlap.
        """
        # If not disjoint already, skip deflation
        if not layout_is_disjoint(centers, angle_deg):
            return centers

        # Centroid
        cx = sum([x for x, _ in centers]) / len(centers)
        cy = sum([y for _, y in centers]) / len(centers)

        def apply_scale(s: float) -> List[Tuple[float, float]]:
            new_cs = []
            for (x, y) in centers:
                vx, vy = x - cx, y - cy
                new_cs.append((cx + s * vx, cy + s * vy))
            return new_cs

        # Bisection on s in [s_lo, 1.0]; we want the minimal s that is feasible
        lo = s_lo
        hi = 1.0
        # Ensure the upper bound is feasible (should be, since s=1 leaves layout unchanged)
        best_cs = centers
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            trial = apply_scale(mid)
            if layout_is_disjoint(trial, angle_deg):
                # Feasible; try smaller (more contraction)
                best_cs = trial
                hi = mid
            else:
                # Infeasible; reduce contraction (increase s)
                lo = mid
        return best_cs

    def trim_outer_length(inner_hex_data: List[List[float]],
                          outer_center: List[float],
                          outer_angle_deg: float,
                          L_init: float) -> float:
        """
        Verify-driven bisection to find the smallest outer side length that maintains feasibility.
        Starts from a passing L (inflate slightly if needed), then bisects down to the feasibility boundary.
        """
        L = L_init
        # Ensure we start from a passing length
        if not verify_construction(inner_hex_data, outer_center, L, outer_angle_deg):
            # Inflate until passing or small cap
            for _ in range(2000):
                L *= 1.000001
                if verify_construction(inner_hex_data, outer_center, L, outer_angle_deg):
                    break

        # If still not passing, return inflated length (rare; upstream should ensure feasibility)
        if not verify_construction(inner_hex_data, outer_center, L, outer_angle_deg):
            return L

        # Bisection on [0, L] to find minimal passing
        lo, hi = 0.0, L
        for _ in range(64):
            mid = 0.5 * (lo + hi)
            if verify_construction(inner_hex_data, outer_center, mid, outer_angle_deg):
                hi = mid
            else:
                lo = mid
        # Add tiny cushion for robustness
        return hi + 1e-9

    # -----------------------
    # Search over seeds and inner orientations
    # -----------------------
    seeds = [
        make_seed_434(),
        make_seed_344(),
        make_seed_443(),
        make_seed_ring(),
    ]
    # Use a micro-rotation sweep of the inner hexagons' common orientation (0..30) due to 60° symmetry
    inner_angles = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]

    best_solution = None
    best_L = float('inf')

    for centers in seeds:
        for ang in inner_angles:
            # 1) Ensure disjointness via gentle outward equalization
            centers0 = ensure_disjoint(centers, ang)
            if not layout_is_disjoint(centers0, ang):
                # Skip if still not disjoint (rare)
                continue

            # 2) Uniform reverse-deflation (contract uniformly while keeping non-overlap)
            centers_def = reverse_deflate_layout(centers0, ang, s_lo=0.986, iters=36)

            # 3) Outer shrink-wrap for the deflated layout
            inner_hex_data = [[x, y, ang] for (x, y) in centers_def]
            outer_center, outer_side_length, outer_angle_degrees, _ = best_outer_for_layout(inner_hex_data)

            # 4) Verify-driven trim on side length
            L_trim = trim_outer_length(inner_hex_data, outer_center, outer_angle_degrees, outer_side_length)

            # 5) Verify final candidate to be safe
            if verify_construction(inner_hex_data, outer_center, L_trim, outer_angle_degrees):
                if L_trim < best_L - 1e-12:
                    best_L = L_trim
                    best_solution = (inner_hex_data, outer_center, L_trim, outer_angle_degrees)

    # Fallback if something went wrong
    if best_solution is None:
        # Simple symmetric 4-3-4 as fallback with generous outer
        centers = make_seed_434()
        inner_hex_data = [[x, y, 0.0] for (x, y) in centers]
        outer_center = [0.0, 0.0]
        outer_side_length = 8.0
        outer_angle_degrees = 0.0
        shift_to_positive(inner_hex_data, outer_center, margin=1.0)
        return inner_hex_data, outer_center, outer_side_length, outer_angle_degrees

    # Shift everything to positive coordinates as required
    inner_hex_data, outer_center, L, phi_deg = best_solution
    shift_to_positive(inner_hex_data, outer_center, margin=1.0)

    # Tiny final verification cushion if needed (should already pass)
    L_final = L
    if not verify_construction(inner_hex_data, outer_center, L_final, phi_deg):
        for _ in range(1000):
            L_final *= 1.000001
            if verify_construction(inner_hex_data, outer_center, L_final, phi_deg):
                break

    return inner_hex_data, outer_center, L_final, phi_deg
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
