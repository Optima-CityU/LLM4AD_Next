Design choices and patterns that produced robust, near-target packing verified by the provided SAT and containment checks.

- On-the-fly Micro-Refit (OMR): After each accepted local change, the algorithm immediately performs a micro outer-hex refit so the active normals and outer fit reflect the new geometry, preventing optimization against stale normals and turning small local gains into cumulative side-length reductions.
- Pairwise Tangent Sliding (TS): When a single active normal has two dominant contributors, the algorithm translates the +n contributor by −δ·t and the −n contributor by +δ·t, with t perpendicular to the limiting normal n, to reduce both extreme projections without shrinking separation along n, using SAT-guarded geometric backtracking.
- Dual Deflation (2D): Following local tightening, the algorithm applies reverse radial deflation and an axial deflation that uniformly shrinks centers’ components along the current most limiting normal n* by bisection while preserving disjointness, recovering targeted slack on the dominant slab that single deflation misses.
- SAT-tight 4–3–4 with OMR/POC, Tangent Sliding, and Dual Deflation (AAR-FR-RD-TS2D) metrics: This run recorded outer_hex_side_length 4.000000002732055, validity 1.0, target_ratio 0.9827499993287684, and no errors, indicating stable verification while operating close to the target side length.

```python
#!/usr/bin/env python3
"""OMR+POC+TS hybrid with dual deflation on a SAT-tight 4–3–4 backbone.

This implementation fuses:
- AAR-FR-RD pipeline (active-normal analysis, adaptive rotations with face-alignment
  rescue, guided inward translations, reverse radial deflation, robust outer fit),
- OMR: On-the-fly Micro-Refit after each accepted local change (small α sweep and
  center correction),
- POC: Opposed-Active Co-rotation when a dominant slab is limited by two sole
  contributors on opposite sides,
- TS: Pairwise Tangent Sliding along the tangent to the most limiting normal,
- Dual global deflation: reverse radial + axial (along the most limiting normal),

and augments seeding with a third staggered-tilt seed to break mirror plateaus.

All moves are SAT-guarded via local SAT checks identical in structure to the
verification utilities. The outer fit is an apothem minimization with Chebyshev
center correction, golden α refinement, and micro sweep.

The result is translated to the first quadrant and returned with a microscopic
outer-side slack for numerical robustness. The verify_construction function
used externally will confirm disjointness and containment.
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

    def seed_staggered_tilt() -> List[List[float]]:
        # New seed: outer rows gently tilted ±15°, middle row at 30°
        inner = []
        y0 = 0.0
        # First two "left" at +15°, last two "right" at -15°
        for j in range(4):
            ang = 15.0 if j < 2 else -15.0  # 345° == -15° effectively
            inner.append([j * H, y0, ang])
        y1 = V
        for j in range(3):
            inner.append([0.5 * H + j * H, y1, 30.0])
        y2 = 2.0 * V
        for j in range(4):
            ang = 15.0 if j < 2 else -15.0
            inner.append([j * H, y2, ang])
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
        while not all_disjoint(scaled) and tries < 80:
            s_hi *= 1.01
            scaled = scale_about_C(layout, C, s_hi)
            tries += 1
        # Now bisection between [s_lo, s_hi]
        for _ in range(42):
            s_mid = 0.5 * (s_lo + s_hi)
            test = scale_about_C(layout, C, s_mid)
            if all_disjoint(test):
                s_hi = s_mid
                scaled = test
            else:
                s_lo = s_mid
        return scaled, C, s_hi

    # ---------- Outer hex fit and refinements ----------
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
                tau = 0.18 * a_cur
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

    # ---------- Apothem proxy and active set ----------
    def apothem_proxy_for(layout, center, alpha_normals_deg):
        verts_all, per_hex = all_vertices(layout)
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
        # Active normals K: indices where a_vals at max
        K = [k for k in range(3) if a_vals[k] >= a - 1e-12]
        return a, a_vals, gap_pos, gap_neg, normals, proj_min, proj_max, K, per_hex

    def active_hex_contributors(layout, center, alpha_normals_deg, tol=1e-10):
        # Returns priority list of (hid, weight, limiting_normals)
        a, a_vals, gp, gn, normals, proj_min, proj_max, K, per_hex = apothem_proxy_for(layout, center, alpha_normals_deg)
        active_ids = {}
        cx, cy = center
        for k in K:
            n = normals[k]
            max_val = proj_max[k]
            min_val = proj_min[k]
            nC = cx * n[0] + cy * n[1]
            for hid, verts in enumerate(per_hex):
                local_dots = [vx * n[0] + vy * n[1] for (vx, vy) in verts]
                lmax = max(local_dots)
                lmin = min(local_dots)
                contributes = False
                w = 0.0
                if lmax >= max_val - tol:
                    gp_local = lmax - nC
                    w = max(w, gp_local)
                    contributes = True
                if lmin <= min_val + tol:
                    gn_local = nC - lmin
                    w = max(w, gn_local)
                    contributes = True
                if contributes:
                    if hid not in active_ids:
                        active_ids[hid] = {"weight": w, "normals": set()}
                    active_ids[hid]["weight"] = max(active_ids[hid]["weight"], w)
                    active_ids[hid]["normals"].add(k)
        # Build sorted list by weight descending
        items = [(hid, data["weight"], sorted(list(data["normals"]))) for hid, data in active_ids.items()]
        items.sort(key=lambda t: (-t[1], t[0]))
        return items, K

    def dominant_normal_and_contributors(layout, center, alpha_normals_deg, tol=1e-10):
        # For the most limiting normal k*, identify exactly the sets of contributors at + and - extremes.
        a, a_vals, gp, gn, normals, proj_min, proj_max, K, per_hex = apothem_proxy_for(layout, center, alpha_normals_deg)
        k_star = max(range(3), key=lambda k: a_vals[k])
        n = normals[k_star]
        min_val = proj_min[k_star]
        max_val = proj_max[k_star]
        pos = []
        neg = []
        # For each hex, check if its vertices touch the extreme within tol
        for hid, verts in enumerate(per_hex):
            vals = [vx * n[0] + vy * n[1] for (vx, vy) in verts]
            if max(vals) >= max_val - tol:
                pos.append(hid)
            if min(vals) <= min_val + tol:
                neg.append(hid)
        return k_star, n, pos, neg

    # ---------- OMR: On-the-fly Micro-Refit ----------
    def omr_refit(layout, prev_theta):
        # Use a very small sweep around previous alpha = prev_theta + 30°
        alpha_guess = norm_alpha(prev_theta + 30.0)
        S, C, theta = micro_sweep(layout, alpha_guess, half_width=0.3, step=0.02)
        return S, C, theta

    # ---------- Helpers for local moves ----------
    def principal_delta_period(delta, period=60.0):
        # Wrap delta into [-period/2, period/2]
        r = (delta + period / 2.0) % period - period / 2.0
        return r

    def apothem_proxy_only(layout, center, alpha_normals_deg):
        ac, _, _, _, _, _, _, _, _ = apothem_proxy_for(layout, center, alpha_normals_deg)
        return ac

    def rotate_hex(layout, hid, delta_deg):
        trial = [p[:] for p in layout]
        trial[hid][2] = trial[hid][2] + delta_deg
        if not all_disjoint(trial):
            return None
        return trial

    def translate_hex(layout, hid, tx, ty):
        trial = [p[:] for p in layout]
        trial[hid][0] = trial[hid][0] + tx
        trial[hid][1] = trial[hid][1] + ty
        if not all_disjoint(trial):
            return None
        return trial

    def orientation_line_search(layout, hid, center, alpha_normals_deg, limiting_normals, init_step=0.8, min_step=0.02):
        # Estimate slope via symmetric finite difference ±h (degrees)
        h = 0.15
        a0 = apothem_proxy_only(layout, center, alpha_normals_deg)

        # Try slope estimate around current angle
        trial_plus = rotate_hex(layout, hid, +h)
        trial_minus = rotate_hex(layout, hid, -h)
        if trial_plus is None and trial_minus is None:
            return layout, False, a0
        a_plus = apothem_proxy_only(trial_plus, center, alpha_normals_deg) if trial_plus is not None else float('inf')
        a_minus = apothem_proxy_only(trial_minus, center, alpha_normals_deg) if trial_minus is not None else float('inf')
        if math.isfinite(a_plus) and math.isfinite(a_minus):
            slope = (a_plus - a_minus) / (2.0 * h)
        elif math.isfinite(a_plus):
            slope = (a_plus - a0) / h
        elif math.isfinite(a_minus):
            slope = (a0 - a_minus) / h
        else:
            slope = 0.0

        # If slope is near zero, we'll try face-alignment rescue later
        if abs(slope) < 1e-10:
            return layout, False, a0

        # Monotone backtracking line search based on slope sign
        direction = -1.0 if slope > 0.0 else 1.0
        step = init_step
        best_layout = layout
        best_a = a0
        improved = False
        while step >= min_step - 1e-12:
            trial = rotate_hex(layout, hid, direction * step)
            if trial is None:
                step *= 0.5
                continue
            a_try = apothem_proxy_only(trial, center, alpha_normals_deg)
            if a_try < best_a - 1e-12:
                best_layout = trial
                best_a = a_try
                improved = True
                break  # accept first monotone improvement
            step *= 0.5
        return best_layout, improved, best_a

    def face_alignment_rescue(layout, hid, center, alpha_normals_deg, limiting_normals, cap_deg=1.2, min_step=0.02):
        # Rotate towards nearest face-normal alignment to the most limiting normal
        a0 = apothem_proxy_only(layout, center, alpha_normals_deg)
        # Pick the first limiting normal (most limiting direction)
        k_star = limiting_normals[0] if limiting_normals else 0
        target_alpha = alpha_normals_deg + 60.0 * k_star
        # Current hex angle
        hx, hy, hang = layout[hid]
        # Face normals angles are hang + 30 + 60*m; nearest alignment delta:
        base = hang + 30.0
        delta_to_align = principal_delta_period(target_alpha - base, 60.0)
        # Cap the magnitude
        move = max(-cap_deg, min(cap_deg, delta_to_align))
        # Backtracking on move
        step = abs(move)
        direction = 1.0 if move >= 0.0 else -1.0
        best_layout = layout
        best_a = a0
        improved = False
        while step >= min_step - 1e-12:
            trial = rotate_hex(layout, hid, direction * step)
            if trial is None:
                step *= 0.5
                continue
            a_try = apothem_proxy_only(trial, center, alpha_normals_deg)
            if a_try < best_a - 1e-12:
                best_layout = trial
                best_a = a_try
                improved = True
                break
            step *= 0.5
        return best_layout, improved, best_a

    def guided_inward_translation(layout, hid, center, alpha_normals_deg, current_a, max_tau=0.05):
        # Compute details to decide translation direction
        a_cur, a_vals, gp, gn, normals, proj_min, proj_max, K, per_hex = apothem_proxy_for(layout, center, alpha_normals_deg)
        # Most limiting normal index
        k_star = max(range(3), key=lambda k: a_vals[k])
        n = normals[k_star]
        # Local extreme for this hex along n
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
        tau = min(max_tau, 0.12 * a_cur)
        best_layout = layout
        best_a = current_a
        improved = False
        while tau > 5e-7:
            trial = translate_hex(layout, hid, tdir[0] * tau, tdir[1] * tau)
            if trial is None:
                tau *= 0.5
                continue
            a_try = apothem_proxy_only(trial, center, alpha_normals_deg)
            if a_try < best_a - 1e-12:
                best_layout = trial
                best_a = a_try
                improved = True
                break
            tau *= 0.5
        return best_layout, improved, best_a

    # ---------- Pairwise Tangent Sliding (new) ----------
    def tangent_slide_pair(layout, hid_pos, hid_neg, center, alpha_normals_deg, n_vec, init_delta=0.08, min_delta=0.003):
        # Tangent t is perpendicular to n: rotate by +90° (CCW)
        t = (-n_vec[1], n_vec[0])
        # Move +n contributor by -δ t, and -n contributor by +δ t
        a0 = apothem_proxy_only(layout, center, alpha_normals_deg)
        delta = init_delta
        best_layout = layout
        best_a = a0
        improved = False
        while delta >= min_delta - 1e-15:
            trial = [p[:] for p in layout]
            trial[hid_pos][0] -= t[0] * delta
            trial[hid_pos][1] -= t[1] * delta
            trial[hid_neg][0] += t[0] * delta
            trial[hid_neg][1] += t[1] * delta
            if not all_disjoint(trial):
                delta *= 0.5
                continue
            a_try = apothem_proxy_only(trial, center, alpha_normals_deg)
            if a_try < best_a - 1e-12:
                best_layout = trial
                best_a = a_try
                improved = True
                break
            delta *= 0.5
        return best_layout, improved, best_a

    # ---------- Reverse radial deflation ----------
    def reverse_radial_deflation(layout):
        # Uniformly contract centers toward centroid by the largest factor λ ≤ 1
        # that preserves SAT disjointness. Use bracket expansion then bisection.
        C = centroid(layout)

        def apply_lambda(lmbd):
            return scale_about_C(layout, C, lmbd)

        # Start with [lo, hi] where hi is disjoint (1.0), find lo not disjoint by expanding
        hi = 1.0
        lo = 0.9
        # If lo is still disjoint, push lower until overlap or floor
        floor = 0.7
        while lo > floor and all_disjoint(apply_lambda(lo)):
            lo *= 0.95
        # Ensure lo is not disjoint; if it is, set lo to floor for safety
        if all_disjoint(apply_lambda(lo)):
            lo = max(floor, lo * 0.95)
        # Guarantee hi disjoint
        for _ in range(28):
            mid = 0.5 * (lo + hi)
            if all_disjoint(apply_lambda(mid)):
                hi = mid
            else:
                lo = mid
        return apply_lambda(hi)

    # ---------- Axial deflation along a given normal (new) ----------
    def apply_axial(layout, C, n_vec, s):
        # For each center r, decompose d = r - C into d_n n + d_t, shrink only d_n -> s d_n
        nx, ny = n_vec
        new = []
        cx, cy = C
        for (x, y, ang) in layout:
            dx = x - cx
            dy = y - cy
            dn = dx * nx + dy * ny
            dtn_x = dx - dn * nx
            dtn_y = dy - dn * ny
            ndx = dtn_x + s * dn * nx
            ndy = dtn_y + s * dn * ny
            new.append([cx + ndx, cy + ndy, ang])
        return new

    def axial_deflation(layout, n_vec):
        # Bisection on s in (s_lo, 1] to find minimal s that keeps SAT disjointness.
        C = centroid(layout)
        hi = 1.0
        s = 0.9
        floor = 0.6
        # Bracket a non-disjoint lower bound
        while s > floor and all_disjoint(apply_axial(layout, C, n_vec, s)):
            s *= 0.95
        # Ensure s is non-disjoint; if still disjoint, clamp
        if all_disjoint(apply_axial(layout, C, n_vec, s)):
            s = max(floor, s * 0.95)
        lo = s
        # Now bisection between [lo, hi] for smallest disjoint s
        for _ in range(28):
            mid = 0.5 * (lo + hi)
            test = apply_axial(layout, C, n_vec, mid)
            if all_disjoint(test):
                hi = mid
            else:
                lo = mid
        return apply_axial(layout, C, n_vec, hi)

    # ---------- Opposed-Active Co-rotation (new) ----------
    def co_rotate_pair(layout, hid_pos, hid_neg, center, alpha_normals_deg, n_vec, init_delta=0.6, min_delta=0.02):
        # Rotate pos by +δ and neg by -δ (or the opposite) and accept if apothem reduces.
        # Try both signs to catch the beneficial alignment relative to n.
        a0 = apothem_proxy_only(layout, center, alpha_normals_deg)
        best_layout = layout
        best_a = a0
        improved = False

        def try_sign(sign):
            delta = init_delta
            while delta >= min_delta - 1e-15:
                trial = [p[:] for p in layout]
                trial[hid_pos][2] += sign * delta
                trial[hid_neg][2] -= sign * delta
                if not all_disjoint(trial):
                    delta *= 0.5
                    continue
                a_try = apothem_proxy_only(trial, center, alpha_normals_deg)
                if a_try < best_a - 1e-12:
                    return trial, True, a_try
                delta *= 0.5
            return layout, False, a0

        for sign in (+1.0, -1.0):
            trial, ok, a_try = try_sign(sign)
            if ok and a_try < best_a - 1e-12:
                best_layout = trial
                best_a = a_try
                improved = True
                break
        return best_layout, improved, best_a

    # ---------- Seed processing pipeline ----------
    def process_seed(seed_layout):
        # Step 1: Minimal SAT inflation
        inflated_layout, C0, s_init = minimal_radial_inflation(seed_layout)

        # Step 2: Outer fit
        S_best, C_best, theta_best = outer_fit_pipeline(inflated_layout)

        # Multi-pass loop: local ops + OMR + pairwise ops + dual deflation + refit
        max_passes = 4
        improve_tol = 3e-6
        cur_layout = [p[:] for p in inflated_layout]
        cur_S = S_best
        cur_C = C_best
        cur_theta = theta_best

        for _ in range(max_passes):
            before_layout = [p[:] for p in cur_layout]
            before_S = cur_S
            before_C = cur_C
            before_theta = cur_theta

            # Use normals phase alpha = theta + 30°
            alpha_norm = cur_theta + 30.0

            # Identify active contributors and priority
            contributors, K = active_hex_contributors(cur_layout, cur_C, alpha_norm, tol=1e-10)
            if not contributors:
                # No active contributors; run dual deflation and refit anyway
                cur_layout = reverse_radial_deflation(cur_layout)
                # Axial deflation along current most limiting normal
                k_star, n_vec, pos_list, neg_list = dominant_normal_and_contributors(cur_layout, cur_C, alpha_norm, tol=1e-10)
                cur_layout = axial_deflation(cur_layout, n_vec)
                S_new, C_new, theta_new = outer_fit_pipeline(cur_layout)
                if S_new < before_S - improve_tol:
                    cur_S, cur_C, cur_theta = S_new, C_new, theta_new
                else:
                    break
                continue

            # Process active hexes by descending exposure weight
            any_change = False
            # Current apothem proxy for guidance
            a_base = apothem_proxy_only(cur_layout, cur_C, alpha_norm)
            for hid, weight, limiting_normals in contributors:
                # Orientation line search
                new_layout, improved, a_after = orientation_line_search(cur_layout, hid, cur_C, alpha_norm, limiting_normals, init_step=0.8, min_step=0.02)
                if not improved:
                    # Face-alignment rescue
                    new_layout, improved, a_after = face_alignment_rescue(cur_layout, hid, cur_C, alpha_norm, limiting_normals, cap_deg=1.2, min_step=0.02)
                if improved:
                    cur_layout = new_layout
                    any_change = True
                    a_base = a_after
                    # OMR: small refit to propagate the local improvement
                    S_tmp, C_tmp, theta_tmp = omr_refit(cur_layout, cur_theta)
                    cur_S, cur_C, cur_theta = S_tmp, C_tmp, theta_tmp
                    alpha_norm = cur_theta + 30.0
                    # Guided inward translation along limiting normals
                    cur_layout, trans_improved, a_after2 = guided_inward_translation(cur_layout, hid, cur_C, alpha_norm, a_base, max_tau=0.06)
                    if trans_improved:
                        any_change = True
                        a_base = a_after2
                        # OMR again
                        S_tmp, C_tmp, theta_tmp = omr_refit(cur_layout, cur_theta)
                        cur_S, cur_C, cur_theta = S_tmp, C_tmp, theta_tmp
                        alpha_norm = cur_theta + 30.0
                # Next hex

            # Opposed-Active Co-rotation if exactly two sole contributors on top slab
            k_star, n_vec, pos_list, neg_list = dominant_normal_and_contributors(cur_layout, cur_C, alpha_norm, tol=1e-10)
            if len(pos_list) == 1 and len(neg_list) == 1:
                hid_pos = pos_list[0]
                hid_neg = neg_list[0]
                new_layout, corr_improved, a_after = co_rotate_pair(cur_layout, hid_pos, hid_neg, cur_C, alpha_norm, n_vec, init_delta=0.6, min_delta=0.02)
                if corr_improved:
                    cur_layout = new_layout
                    any_change = True
                    # OMR
                    S_tmp, C_tmp, theta_tmp = omr_refit(cur_layout, cur_theta)
                    cur_S, cur_C, cur_theta = S_tmp, C_tmp, theta_tmp
                    alpha_norm = cur_theta + 30.0
                    # Guided inward translations on both (with small tau)
                    cur_layout, imp1, _ = guided_inward_translation(cur_layout, hid_pos, cur_C, alpha_norm, apothem_proxy_only(cur_layout, cur_C, alpha_norm), max_tau=0.04)
                    if imp1:
                        S_tmp, C_tmp, theta_tmp = omr_refit(cur_layout, cur_theta)
                        cur_S, cur_C, cur_theta = S_tmp, C_tmp, theta_tmp
                        alpha_norm = cur_theta + 30.0
                    cur_layout, imp2, _ = guided_inward_translation(cur_layout, hid_neg, cur_C, alpha_norm, apothem_proxy_only(cur_layout, cur_C, alpha_norm), max_tau=0.04)
                    if imp2:
                        S_tmp, C_tmp, theta_tmp = omr_refit(cur_layout, cur_theta)
                        cur_S, cur_C, cur_theta = S_tmp, C_tmp, theta_tmp
                        alpha_norm = cur_theta + 30.0

            # Tangent sliding on dominant pair (use strongest pos/neg even if multiple)
            k_star, n_vec, pos_list, neg_list = dominant_normal_and_contributors(cur_layout, cur_C, alpha_norm, tol=1e-10)
            if pos_list and neg_list:
                # Heuristic: pick first ids (extreme contributors)
                hid_pos = pos_list[0]
                hid_neg = neg_list[0]
                new_layout, slide_improved, _ = tangent_slide_pair(cur_layout, hid_pos, hid_neg, cur_C, alpha_norm, n_vec, init_delta=0.08, min_delta=0.003)
                if slide_improved:
                    cur_layout = new_layout
                    any_change = True
                    # OMR
                    S_tmp, C_tmp, theta_tmp = omr_refit(cur_layout, cur_theta)
                    cur_S, cur_C, cur_theta = S_tmp, C_tmp, theta_tmp
                    alpha_norm = cur_theta + 30.0

            # After local + pairwise moves, dual deflation to recover slack
            if any_change:
                # Reverse radial deflation
                cur_layout = reverse_radial_deflation(cur_layout)
                # Axial deflation along the most limiting normal
                k_star, n_vec, pos_list, neg_list = dominant_normal_and_contributors(cur_layout, cur_C, alpha_norm, tol=1e-10)
                cur_layout = axial_deflation(cur_layout, n_vec)

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

    # ---------- Evaluate all seeds and choose the best ----------
    seeds = [seed_uniform_30(), seed_mixed_rows(), seed_staggered_tilt()]
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
