Key design choices that maintained disjointness with minimal slack and tightened the final outer hexagon size using existing SAT primitives and verification.

- SAT-MTV replacement for seed disjointness: The algorithm replaces uniform radial scaling with an iterative SAT-based minimal translation vector repair that moves only intersecting pairs via evenly split, damped steps (factor 0.6–0.8) under an iteration cap of roughly 60–120, recentroids to prevent drift, enforces x>0,y>0 via a final positive shift, and falls back to a tiny radial bisection in [1.0, 1.002] only if overlaps persist.
- Boundary-focused rotation line-search: For boundary-contributing hexagons, the method performs a deterministic 1D rotation search testing θ_i ± δ with δ decaying from about 4–6° to 0.5–1°, using backtracking and committing only rotations that keep layouts disjoint and strictly reduce the required outer side length.
- Verify-driven bisection on outer side length: After analytical sizing, the method bisects the outer hexagon side length using verify_construction, starting slightly below the estimate, shrinking until verification fails, and returning the smallest passing value with a ~2e−7 cushion, typically trimming 1e−3–1e−2 compared to fixed safety padding.
- SAT-MTV Separation with Verify-Bisected Outer Shrink: On the reported run, the method achieved validity 1.0 with outer_hex_side_length 4.000000891552754, score 0.9827497809566815, and eval_time 4.443089095002506 seconds.
- SAT-MTV Separation with Verify-Bisected Outer Trim: Replacing the naive center–center push in relax_layout with a SAT-derived minimum-translation-vector separation that projects both polygons onto the union of their edge-normal axes and pushes along the minimal-overlap axis—splitting the displacement equally and adding a ~1e−7 cushion—minimized pairwise displacements and prevented unnecessary radial spread; pairing this with a verification-guided 1D bisection that trims the outer side length over [max(s_fit−0.01, 0.1), s_fit] while keeping the center and angle fixed removed conservative padding with a ~1e−6 safety margin. In this run, the method maintained validity 1.0 and returned an outer_hex_side_length of 4.000001086313868 in 4.165820572001394 seconds (score 0.982749733106334) using verify_construction as the acceptance oracle; reuse this MTV-based repair plus verify-driven trim whenever collision fixes and container-size minimization are gated by a deterministic verifier.
- After group-angle and active-individual-angle polishing, Verifier-Gated Outer-Side Trimming keeps the inner centers and orientations, the outer center, and the outer angle fixed, then binary-searches the outer side length between a conservative lower bound such as the LP-reported side minus a small margin and the current padded feasible side. Each midpoint is accepted only when both the internal validity test and the unchanged verify_construction function pass; a failed midpoint restores the previous feasible side, while a passing midpoint continues shrinking the side, with approximately 30 bisection iterations.
- Verifier-Gated Outer-Side Trimming: For fixed inner hexagon centers and orientations and a fixed outer angle and center, containment is monotone in the outer side length, so replacing the fixed 2e-5 safety margin with a tiny numerical cushion of approximately 2e-7 to 1e-6 can remove unused boundary slack without changing the LP, SAT logic, orientation search, or verification utilities.
- Verifier-Gated Outer-Side Trimming: The observed result had an outer_hex_side_length of 3.9290461148569333, validity of 1.0, eval_time of 177.6303127608262, target_ratio of 1.0004972924943991, and score of 1.0004972924943991, with no reported error.

```python
#!/usr/bin/env python3
"""Construct a tight packing of 11 unit hexagons inside a regular hexagon.

HALO-TUCK+MTV: Multi-seed exact outer minimization with SAT-guided minimal
translation vector separation and deterministic boundary rotation line-search.

Overview changes (mutation summary):
- SAT-MTV separation for seed disjointness: replace initial uniform radial inflation
  with an iterative, SAT-guided minimal translation vector repair that only moves
  the overlapping pairs by the minimum required distances. Fall back to a very
  small uniform radial bisection only if MTV fails.
- Boundary-focused rotation line-search: deterministic 1D rotation search per
  active boundary hexagon with backtracking and decaying angle; commit strictly
  S-reducing rotations; no random jitter.
- Verify-driven bisection to finalize outer side length: keep existing function,
  but use a very small safety cushion.

Returned result is designed to satisfy verify_construction:
- Inner hexagons are strictly disjoint (SAT).
- All inner vertices lie inside the outer hexagon (guaranteed by the half-plane outer
  solution and a tiny safety margin on S).
- Coordinates shifted to x>0, y>0 quadrant.

"""

import json
import math
import random
from typing import List, Tuple, Optional


# EVOLVE_START
EPS = 1e-9


def hexagon_vertices(center_x, center_y, side_length, angle_degrees):
    """Compute CCW vertices of a regular hexagon (circumradius = side_length)."""
    vertices = []
    ang0 = math.radians(angle_degrees)
    for i in range(6):
        ang = ang0 + 2.0 * math.pi * i / 6.0
        x = center_x + side_length * math.cos(ang)
        y = center_y + side_length * math.sin(ang)
        vertices.append((x, y))
    return vertices


def unit_vec_from_angle(deg: float) -> Tuple[float, float]:
    a = math.radians(deg)
    return (math.cos(a), math.sin(a))


def dot(u: Tuple[float, float], v: Tuple[float, float]) -> float:
    return u[0] * v[0] + u[1] * v[1]


def add(u: Tuple[float, float], v: Tuple[float, float]) -> Tuple[float, float]:
    return (u[0] + v[0], u[1] + v[1])


def sub(u: Tuple[float, float], v: Tuple[float, float]) -> Tuple[float, float]:
    return (u[0] - v[0], u[1] - v[1])


def scale(k: float, u: Tuple[float, float]) -> Tuple[float, float]:
    return (k * u[0], k * u[1])


def project_polygon(vertices: List[Tuple[float, float]], axis: Tuple[float, float]) -> Tuple[float, float]:
    min_proj = float('inf')
    max_proj = float('-inf')
    for (x, y) in vertices:
        p = x * axis[0] + y * axis[1]
        if p < min_proj:
            min_proj = p
        if p > max_proj:
            max_proj = p
    return (min_proj, max_proj)


def get_normals(vertices: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    normals: List[Tuple[float, float]] = []
    for i in range(len(vertices)):
        p1 = vertices[i]
        p2 = vertices[(i + 1) % len(vertices)]
        edge = (p2[0] - p1[0], p2[1] - p1[1])
        # outward normal (perp)
        n = (-edge[1], edge[0])
        ln = math.hypot(n[0], n[1])
        if ln > EPS:
            normals.append((n[0] / ln, n[1] / ln))
    return normals


def overlap_1d(min1, max1, min2, max2) -> bool:
    return max1 >= min2 - 1e-12 and max2 >= min1 - 1e-12


def polygons_intersect(vertices1: List[Tuple[float, float]], vertices2: List[Tuple[float, float]]) -> bool:
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
    v1 = hexagon_vertices(*hex1_params)
    v2 = hexagon_vertices(*hex2_params)
    return not polygons_intersect(v1, v2)


def all_disjoint(inner: List[Tuple[float, float, float]]) -> bool:
    n = len(inner)
    for i in range(n):
        (x1, y1, a1) = inner[i]
        for j in range(i + 1, n):
            (x2, y2, a2) = inner[j]
            if not hexagons_are_disjoint((x1, y1, 1.0, a1), (x2, y2, 1.0, a2)):
                return False
    return True


def vertices_of_all(inner: List[Tuple[float, float, float]]) -> List[Tuple[float, float]]:
    V = []
    for (x, y, ang) in inner:
        V.extend(hexagon_vertices(x, y, 1.0, ang))
    return V


def outer_normals(theta_deg: float) -> List[Tuple[float, float]]:
    # Six outward normals of the regular hexagon with rotation theta
    # Start at theta+30°, then every 60°
    return [unit_vec_from_angle(theta_deg + 30.0 + 60.0 * k) for k in range(6)]


def compute_support_extrema(inner: List[Tuple[float, float, float]], theta_deg: float):
    """Return (a_k, argmax indices/vertices) for k=0..5 where a_k = max_v dot(n_k, v)."""
    normals6 = outer_normals(theta_deg)
    # Build vertices with hex index mapping
    verts: List[Tuple[float, float]] = []
    owner: List[int] = []
    for idx, (x, y, ang) in enumerate(inner):
        vs = hexagon_vertices(x, y, 1.0, ang)
        for v in vs:
            verts.append(v)
            owner.append(idx)
    a = []
    arg_idx = []
    arg_v = []
    for k in range(6):
        nk = normals6[k]
        maxp = float('-inf')
        max_i = -1
        max_v = (0.0, 0.0)
        for i, v in enumerate(verts):
            p = dot(nk, v)
            if p > maxp:
                maxp = p
                max_i = owner[i]
                max_v = v
        a.append(maxp)
        arg_idx.append(max_i)
        arg_v.append(max_v)
    return a, arg_idx, arg_v, normals6, verts, owner


def clip_polygon_with_halfplane(poly: List[Tuple[float, float]], n: Tuple[float, float], b: float) -> List[Tuple[float, float]]:
    """Clip convex polygon poly by half-plane {x | n·x >= b}. Return clipped polygon (possibly empty)."""
    if not poly:
        return []
    res: List[Tuple[float, float]] = []
    m = len(poly)
    for i in range(m):
        P = poly[i]
        Q = poly[(i + 1) % m]
        vp = dot(n, P) - b
        vq = dot(n, Q) - b
        pin = vp >= -1e-12
        qin = vq >= -1e-12
        if pin and qin:
            # both inside: keep Q
            res.append(Q)
        elif pin and not qin:
            # leaving: add intersection
            denom = dot(n, (Q[0] - P[0], Q[1] - P[1]))
            if abs(denom) > 1e-18:
                t = (b - dot(n, P)) / denom
                I = (P[0] + t * (Q[0] - P[0]), P[1] + t * (Q[1] - P[1]))
                res.append(I)
        elif (not pin) and qin:
            # entering: add intersection then Q
            denom = dot(n, (Q[0] - P[0], Q[1] - P[1]))
            if abs(denom) > 1e-18:
                t = (b - dot(n, P)) / denom
                I = (P[0] + t * (Q[0] - P[0]), P[1] + t * (Q[1] - P[1]))
                res.append(I)
            res.append(Q)
        else:
            # both outside: add nothing
            pass
    return res


def polygon_centroid(poly: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Return centroid of a polygon; if degenerate, return average of points."""
    if not poly:
        return (0.0, 0.0)
    A = 0.0
    Cx = 0.0
    Cy = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        A += cross
        Cx += (x1 + x2) * cross
        Cy += (y1 + y2) * cross
    if abs(A) < 1e-16:
        sx = sum(p[0] for p in poly) / len(poly)
        sy = sum(p[1] for p in poly) / len(poly)
        return (sx, sy)
    A *= 0.5
    Cx /= (6.0 * A)
    Cy /= (6.0 * A)
    return (Cx, Cy)


def quick_upper_bound_t(inner: List[Tuple[float, float, float]], theta_deg: float) -> Tuple[float, Tuple[float, float]]:
    """Compute a feasible upper bound on apothem t by placing center C on mid-planes for two axes."""
    V = vertices_of_all(inner)
    n_angles = [theta_deg + 30.0, theta_deg + 90.0, theta_deg + 150.0]
    n = [unit_vec_from_angle(a) for a in n_angles]
    mins = []
    maxs = []
    mids = []
    for i in range(3):
        projs = [dot(n[i], v) for v in V]
        mins.append(min(projs))
        maxs.append(max(projs))
        mids.append(0.5 * (mins[-1] + maxs[-1]))
    # Solve for C: dot(n0,C) = mid0, dot(n1,C)=mid1
    (a, b) = n[0]
    (c, d) = n[1]
    det = a * d - b * c
    if abs(det) < 1e-12:
        C = (0.0, 0.0)
    else:
        inv = (d / det, -b / det, -c / det, a / det)
        cx = inv[0] * mids[0] + inv[1] * mids[1]
        cy = inv[2] * mids[0] + inv[3] * mids[1]
        C = (cx, cy)
    normals6 = outer_normals(theta_deg)
    t = 0.0
    for nk in normals6:
        m = 0.0
        for v in V:
            val = dot(nk, v) - dot(nk, C)
            if val > m:
                m = val
        if m > t:
            t = m
    return t, C


def min_apothem_via_halfplanes(inner: List[Tuple[float, float, float]], theta_deg: float, tol: float = 1e-8) -> Tuple[float, Tuple[float, float]]:
    """Exact minimal apothem t* and a feasible center C* for fixed inner and outer angle theta, via half-plane intersection."""
    a, _, _, normals6, V, _ = compute_support_extrema(inner, theta_deg)

    max_abs = 0.0
    for (x, y) in V:
        max_abs = max(max_abs, abs(x), abs(y))
    M = max(10.0, 4.0 * max_abs + 10.0)
    base_poly = [(-M, -M), (M, -M), (M, M), (-M, M)]

    def feasible_poly_for_t(t: float) -> List[Tuple[float, float]]:
        poly = base_poly
        for k in range(6):
            n = normals6[k]
            b = a[k] - t
            poly = clip_polygon_with_halfplane(poly, n, b)
            if not poly:
                return []
        return poly

    t_hi, C_hi = quick_upper_bound_t(inner, theta_deg)
    t_lo = 0.0
    poly = feasible_poly_for_t(t_hi)
    if not poly:
        t_hi *= 1.1
        for _ in range(10):
            poly = feasible_poly_for_t(t_hi)
            if poly:
                break
            t_hi *= 1.5
        if not poly:
            t_hi = (2.0 / math.sqrt(3.0)) * t_hi
            poly = feasible_poly_for_t(t_hi)

    best_poly = poly if poly else []
    for _ in range(50):
        mid = 0.5 * (t_lo + t_hi)
        poly_mid = feasible_poly_for_t(mid)
        if poly_mid:
            best_poly = poly_mid
            t_hi = mid
        else:
            t_lo = mid
        if t_hi - t_lo < tol:
            break

    t_star = t_hi
    C_star = polygon_centroid(best_poly) if best_poly else C_hi
    return t_star, C_star


def outer_size_for_theta(inner: List[Tuple[float, float, float]], theta_deg: float) -> Tuple[float, Tuple[float, float]]:
    """Compute S and center C for given theta: exact minimal apothem, then side length."""
    t_star, C_star = min_apothem_via_halfplanes(inner, theta_deg)
    S = (2.0 / math.sqrt(3.0)) * t_star
    return S, C_star


def scan_theta_grid(inner: List[Tuple[float, float, float]], deg_start: float, deg_end: float, step: float) -> Tuple[float, float, Tuple[float, float]]:
    """Return best (S, theta, C) over a grid of theta values [deg_start, deg_end) in given step size."""
    best_S = float('inf')
    best_theta = deg_start
    best_C = (0.0, 0.0)
    th = deg_start
    while th < deg_end - 1e-12:
        S, C = outer_size_for_theta(inner, th)
        if S < best_S:
            best_S = S
            best_theta = th
            best_C = C
        th += step
    return best_S, best_theta, best_C


def micro_sweep_theta(inner: List[Tuple[float, float, float]], theta_best: float, span: float = 0.6, step: float = 0.02) -> Tuple[float, float, Tuple[float, float]]:
    """High-resolution sweep around theta_best in ±span with 'step' increments, with wrap to [0,60)."""
    cands = []
    base = theta_best % 60.0
    n_steps = int(round((2.0 * span) / step)) + 1
    start = -span
    for i in range(n_steps):
        d = start + i * step
        th = (base + d) % 60.0
        cands.append(round(th, 6))
    cands = sorted(set(cands))
    best_S = float('inf')
    best_theta = base
    best_C = (0.0, 0.0)
    for th in cands:
        S, C = outer_size_for_theta(inner, th)
        if S < best_S:
            best_S = S
            best_theta = th
            best_C = C
    return best_S, best_theta, best_C


def seed_centroid(inner: List[Tuple[float, float, float]]) -> Tuple[float, float]:
    sx = sum(x for (x, _, _) in inner) / len(inner)
    sy = sum(y for (_, y, _) in inner) / len(inner)
    return (sx, sy)


def _pair_mtv(hex1: Tuple[float, float, float], hex2: Tuple[float, float, float]) -> Tuple[float, float]:
    """Compute minimal translation vector (MTV) to separate two overlapping unit hexagons using SAT axes.
    Returns a vector 'mtv' such that translating hex1 by -0.5*mtv and hex2 by +0.5*mtv reduces penetration.
    If they are not overlapping or overlap depth is degenerate, returns (0,0).
    """
    (x1, y1, a1) = hex1
    (x2, y2, a2) = hex2
    v1 = hexagon_vertices(x1, y1, 1.0, a1)
    v2 = hexagon_vertices(x2, y2, 1.0, a2)

    # Quick non-overlap check
    if not polygons_intersect(v1, v2):
        return (0.0, 0.0)

    axes = get_normals(v1) + get_normals(v2)
    # Center projections along axes to determine MTV orientation
    C1 = (x1, y1)
    C2 = (x2, y2)

    min_depth = float('inf')
    best_axis = (0.0, 0.0)
    best_sign = 0.0

    for axis in axes:
        min1, max1 = project_polygon(v1, axis)
        min2, max2 = project_polygon(v2, axis)
        # If there's a separating axis (rare due to polygons_intersect True), skip
        if not overlap_1d(min1, max1, min2, max2):
            return (0.0, 0.0)
        # Depth of overlap along this axis
        depth = min(max1 - min2, max2 - min1)
        if depth < min_depth:
            min_depth = depth
            # Orientation: push along axis from C1 to C2 direction
            c1 = dot(axis, C1)
            c2 = dot(axis, C2)
            sgn = 1.0 if (c1 < c2) else -1.0
            best_axis = axis
            best_sign = sgn

    if not math.isfinite(min_depth) or min_depth <= 0.0:
        return (0.0, 0.0)
    # MTV direction with minimal magnitude
    mtv = (best_sign * min_depth * best_axis[0], best_sign * min_depth * best_axis[1])
    return mtv


def separate_with_mtv(inner: List[Tuple[float, float, float]], iter_cap: int = 120, damping: float = 0.75) -> List[Tuple[float, float, float]]:
    """Iteratively resolve overlaps using SAT-derived minimal translation vectors (MTV).
    Only overlapping pairs are moved, split evenly with damping to avoid overshoot.
    If separation fails within iteration cap, returns the current configuration (for fallback scaling).
    """
    n = len(inner)
    cfg = inner[:]
    # Preserve original centroid to prevent global drift
    cx0, cy0 = seed_centroid(cfg)

    def recenter():
        cx, cy = seed_centroid(cfg)
        shift = (cx0 - cx, cy0 - cy)
        for i in range(n):
            x, y, a = cfg[i]
            cfg[i] = (x + shift[0], y + shift[1], a)

    # If already disjoint, return as-is (no micro inflation to preserve tightness)
    if all_disjoint(cfg):
        recenter()
        return cfg

    for it in range(iter_cap):
        any_move = False
        # Sweep all intersecting pairs and apply damped half-and-half MTVs
        for i in range(n):
            xi, yi, ai = cfg[i]
            for j in range(i + 1, n):
                xj, yj, aj = cfg[j]
                if hexagons_are_disjoint((xi, yi, 1.0, ai), (xj, yj, 1.0, aj)):
                    continue
                mtv = _pair_mtv((xi, yi, ai), (xj, yj, aj))
                dmx, dmy = mtv
                depth = math.hypot(dmx, dmy)
                if depth <= 0.0:
                    continue
                any_move = True
                # Damped split move
                sx = 0.5 * damping * dmx
                sy = 0.5 * damping * dmy
                cfg[i] = (xi - sx, yi - sy, ai)
                cfg[j] = (xj + sx, yj + sy, aj)
                # Update local copies used for subsequent pair checks
                xi, yi, ai = cfg[i]
                xj, yj, aj = cfg[j]
        # Recentroid to original to avoid drift accumulation
        recenter()

        # Stop when no overlaps remain
        if all_disjoint(cfg):
            return cfg
        # If no move happened but still overlapping, break for fallback
        if not any_move:
            break

    # Return current cfg; caller will perform tiny uniform radial fallback if needed
    return cfg


def radial_inflate_disjoint(inner: List[Tuple[float, float, float]]) -> List[Tuple[float, float, float]]:
    """First attempt SAT-MTV separation; if overlaps persist, perform tiny uniform radial scaling via bisection."""
    # MTV separation
    separated = separate_with_mtv(inner, iter_cap=120, damping=0.8)
    if all_disjoint(separated):
        return separated

    # Fallback: small uniform radial scaling about centroid via bisection in [1.0, 1.002]
    cx, cy = seed_centroid(separated)

    def scale_seed(s: float) -> List[Tuple[float, float, float]]:
        out = []
        for (x, y, ang) in separated:
            dx = x - cx
            dy = y - cy
            out.append((cx + s * dx, cy + s * dy, ang))
        return out

    lo, hi = 1.0, 1.002
    cand = scale_seed(hi)
    # Expand slightly if still overlapping
    attempts = 0
    while not all_disjoint(cand) and attempts < 20 and hi < 1.01:
        hi *= 1.01
        cand = scale_seed(hi)
        attempts += 1

    # Bisection
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        c = scale_seed(mid)
        if all_disjoint(c):
            hi = mid
        else:
            lo = mid
        if hi - lo < 5e-8:
            break
    return scale_seed(hi)


def honeycomb_seed(rows: Tuple[int, int, int], t_scale: float = 1.0, angle_deg: float = 30.0) -> List[Tuple[float, float, float]]:
    """Build a 3-row honeycomb seed with counts given by 'rows' (top, mid, bot).
    Lattice spacings for pointy-top style arrangement: dx = √3 * t_scale, dy = 1.5 * t_scale.
    """
    dx = math.sqrt(3.0) * t_scale
    dy = 1.5 * t_scale
    top_count, mid_count, bot_count = rows

    def xs_for_count(c: int) -> List[float]:
        if c == 4:
            return [(-1.5 + i) * dx for i in range(4)]
        elif c == 3:
            return [(-1.0 + i) * dx for i in range(3)]
        else:
            start = -0.5 * (c - 1)
            return [(start + i) * dx for i in range(c)]

    inner: List[Tuple[float, float, float]] = []
    for x in xs_for_count(top_count):
        inner.append((x, dy, angle_deg))
    for x in xs_for_count(mid_count):
        inner.append((x, 0.0, angle_deg))
    for x in xs_for_count(bot_count):
        inner.append((x, -dy, angle_deg))
    assert len(inner) == 11, "Seed must contain 11 hexagons"
    return inner


def axial_to_xy(q: int, r: int, t_scale: float = 1.0) -> Tuple[float, float]:
    """Axial coordinate to 2D for pointy-top lattice (unit hex side length=1)."""
    dx = math.sqrt(3.0) * t_scale
    dy = 1.5 * t_scale
    x = dx * (q + r / 2.0)
    y = dy * r
    return (x, y)


def axial_ring_seed(angle_deg: float = 30.0, t_scale: float = 1.0, second_ring_indices: List[int] = None) -> List[Tuple[float, float, float]]:
    """Construct seed: center + 6 first-ring + 4 second-ring from 6 options (given indices)."""
    # First ring axial directions radius=1
    dirs = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
    centers = []
    # center
    centers.append((0.0, 0.0, angle_deg))
    # first ring
    for (q, r) in dirs:
        x, y = axial_to_xy(q, r, t_scale)
        centers.append((x, y, angle_deg))
    # second ring candidates radius=2 along same dirs
    sec_cands = []
    for (q, r) in dirs:
        x, y = axial_to_xy(2 * q, 2 * r, t_scale)
        sec_cands.append((x, y, angle_deg))
    # Choose 4 indices from 0..5, either provided or a default set
    if second_ring_indices is None:
        second_ring_indices = [0, 2, 3, 5]  # default balanced set
    for idx in second_ring_indices:
        centers.append(sec_cands[idx])
    assert len(centers) == 11
    return centers


def post_fit_center_polish(inner: List[Tuple[float, float, float]], theta_deg: float, C_init: Tuple[float, float]) -> Tuple[float, float, Tuple[float, float]]:
    """Polish C via small nudges along average of near-active outer normals; return (S, theta, C_polished)."""
    S_init, C_best = outer_size_for_theta(inner, theta_deg)
    t_star = (math.sqrt(3.0) / 2.0) * S_init
    # Compute slacks
    a, _, _, normals6, _, _ = compute_support_extrema(inner, theta_deg)
    C = C_best
    # Tightness threshold
    thresh = 1e-9
    for it in range(4):
        slacks = []
        for k in range(6):
            s = dot(normals6[k], C) - (a[k] - t_star)
            slacks.append(s)
        # Near-active normals: small positive slacks
        active = [k for k in range(6) if slacks[k] < thresh]
        if not active:
            break
        # Nudge along average of active normals
        avg_n = (0.0, 0.0)
        for k in active:
            avg_n = add(avg_n, normals6[k])
        ln = math.hypot(avg_n[0], avg_n[1])
        if ln < 1e-12:
            break
        avg_n = (avg_n[0] / ln, avg_n[1] / ln)
        step = 2e-3 / (1 + it)
        C = (C[0] + step * avg_n[0], C[1] + step * avg_n[1])
        # Recompute exact t/S for updated C by resolving t as max_k(a_k - n_k·C)
        t_candidate = max(a[k] - dot(normals6[k], C) for k in range(6))
        S_candidate = (2.0 / math.sqrt(3.0)) * t_candidate
        # Keep if improves
        if S_candidate + 1e-12 < S_init:
            S_init = S_candidate
            t_star = (math.sqrt(3.0) / 2.0) * S_init
        else:
            # Backtrack
            C = C_best
            break
        C_best = C
    return S_init, theta_deg, C_best


def active_boundary_indices(inner: List[Tuple[float, float, float]], theta_deg: float, C: Tuple[float, float]) -> List[int]:
    """Identify indices of hexagons contributing to maximal support at active outer normals."""
    a, arg_idx, _, normals6, _, _ = compute_support_extrema(inner, theta_deg)
    # Compute m_k(C) = a_k - n_k·C. Active if near maximal among k.
    mk = [a[k] - dot(normals6[k], C) for k in range(6)]
    max_m = max(mk)
    tol = max(1e-6, 1e-5 * max_m)
    boundary_set = set()
    for k in range(6):
        if max_m - mk[k] <= tol:
            if arg_idx[k] >= 0:
                boundary_set.add(arg_idx[k])
    return sorted(boundary_set)


def move_hex(inner: List[Tuple[float, float, float]], idx: int, dx: float, dy: float, dtheta: float) -> List[Tuple[float, float, float]]:
    out = inner[:]
    x, y, ang = out[idx]
    out[idx] = (x + dx, y + dy, ang + dtheta)
    return out


def deterministic_rotation_line_search(inner: List[Tuple[float, float, float]], theta_deg: float, C: Tuple[float, float], max_accepts: int = 24) -> Tuple[List[Tuple[float, float, float]], float, Tuple[float, float]]:
    """Deterministic boundary rotation line-search.
    For each boundary-contributing hexagon, evaluate S at angles ±δ with backtracking until improvement.
    Optionally apply a tiny inward translation along the sum of active normals to tuck.
    """
    # Current best
    S_best, C_best = outer_size_for_theta(inner, theta_deg)
    inner_best = inner[:]
    accepts = 0

    # Precompute inward direction from active normals
    a, _, _, normals6, _, _ = compute_support_extrema(inner_best, theta_deg)
    mk = [a[k] - dot(normals6[k], C_best) for k in range(6)]
    max_m = max(mk)
    tol = max(1e-6, 1e-5 * max_m)
    active_normals = [normals6[k] for k in range(6) if (max_m - mk[k] <= tol)]
    sum_n = (0.0, 0.0)
    for n in active_normals:
        sum_n = add(sum_n, n)
    ln = math.hypot(sum_n[0], sum_n[1])
    inward = (0.0, 0.0) if ln < 1e-12 else (-sum_n[0] / ln, -sum_n[1] / ln)

    # Rotation search parameters
    base_beta = 6.0      # degrees
    min_beta = 0.6       # degrees
    base_delta = 0.012   # translation magnitude
    min_delta = 0.003

    while accepts < max_accepts:
        boundary = active_boundary_indices(inner_best, theta_deg, C_best)
        improved_any = False
        for idx in boundary:
            beta = base_beta
            best_local = None  # tuple (S_cand, C_cand, cand_inner)
            # Backtracking line-search on rotation for this hex
            while beta >= min_beta:
                # Translate magnitude coupled to beta
                delta = max(min_delta, base_delta * (beta / base_beta))
                # Evaluate both directions
                candidates = []
                for rot in (+beta, -beta):
                    dx = delta * inward[0]
                    dy = delta * inward[1]
                    cand = move_hex(inner_best, idx, dx, dy, rot)
                    if not all_disjoint(cand):
                        continue
                    S_cand, C_cand = outer_size_for_theta(cand, theta_deg)
                    candidates.append((S_cand, C_cand, cand))
                if candidates:
                    # Pick best S among candidates
                    candidates.sort(key=lambda t: t[0])
                    (S_cand, C_cand, cand_inner) = candidates[0]
                    if S_cand + 1e-6 < S_best:
                        best_local = (S_cand, C_cand, cand_inner)
                        break
                # No improvement at this beta: backtrack
                beta *= 0.5

            if best_local is not None:
                S_best, C_best, inner_best = best_local
                accepts += 1
                improved_any = True
                # Recompute inward direction based on updated best
                a2, _, _, normals6b, _, _ = compute_support_extrema(inner_best, theta_deg)
                mk2 = [a2[k] - dot(normals6b[k], C_best) for k in range(6)]
                max_m2 = max(mk2)
                tol2 = max(1e-6, 1e-5 * max_m2)
                active_normals2 = [normals6b[k] for k in range(6) if (max_m2 - mk2[k] <= tol2)]
                sum_n2 = (0.0, 0.0)
                for n in active_normals2:
                    sum_n2 = add(sum_n2, n)
                ln2 = math.hypot(sum_n2[0], sum_n2[1])
                inward = (0.0, 0.0) if ln2 < 1e-12 else (-sum_n2[0] / ln2, -sum_n2[1] / ln2)
            # Move to next boundary hexagon regardless
        if not improved_any:
            break

    return inner_best, S_best, C_best


def try_tucking_once(inner: List[Tuple[float, float, float]], theta_deg: float, C: Tuple[float, float], beta_deg: float, delta: float) -> Tuple[List[Tuple[float, float, float]], float, Tuple[float, float]]:
    """Legacy randomized tucking (kept for reference, not used)."""
    a, _, _, normals6, _, _ = compute_support_extrema(inner, theta_deg)
    mk = [a[k] - dot(normals6[k], C) for k in range(6)]
    max_m = max(mk)
    tol = max(1e-6, 1e-5 * max_m)
    active_normals = [normals6[k] for k in range(6) if (max_m - mk[k] <= tol)]
    sum_n = (0.0, 0.0)
    for n in active_normals:
        sum_n = add(sum_n, n)
    # Inward translation opposite to sum of active normals
    ln = math.hypot(sum_n[0], sum_n[1])
    inward = (0.0, 0.0) if ln < 1e-12 else (-sum_n[0] / ln, -sum_n[1] / ln)

    boundary = active_boundary_indices(inner, theta_deg, C)
    best_S, best_C = outer_size_for_theta(inner, theta_deg)
    best_inner = inner
    improved = False

    # Candidate moves per boundary hex
    for idx in boundary:
        for rot in (+beta_deg, -beta_deg):
            dx = delta * inward[0]
            dy = delta * inward[1]
            cand = move_hex(inner, idx, dx, dy, rot)
            if not all_disjoint(cand):
                continue
            S_cand, C_cand = outer_size_for_theta(cand, theta_deg)
            if S_cand + 1e-6 < best_S:
                best_S = S_cand
                best_C = C_cand
                best_inner = cand
                improved = True

    return (best_inner, best_S, best_C) if improved else (inner, best_S, C)


def tucking_refinement(inner: List[Tuple[float, float, float]], theta_deg: float, C: Tuple[float, float], max_accepts: int = 20) -> Tuple[List[Tuple[float, float, float]], float, Tuple[float, float]]:
    """Active boundary tucking using deterministic rotation line-search."""
    return deterministic_rotation_line_search(inner, theta_deg, C, max_accepts=max_accepts)


def best_outer_for_seed(inner: List[Tuple[float, float, float]]) -> Tuple[float, float, Tuple[float, float]]:
    """Coarse scan φ ∈ [0,60) with 0.25° steps, then micro-sweep ±0.6° at 0.02° with center polish."""
    S0, th0, C0 = scan_theta_grid(inner, 0.0, 60.0, 0.25)
    S1, th1, C1 = micro_sweep_theta(inner, th0, span=0.6, step=0.02)
    # Post-fit polish on center
    S1p, th1p, C1p = post_fit_center_polish(inner, th1, C1)
    if S1p < S1 + 1e-12:
        return S1p, th1p, C1p
    return S1, th1, C1


def generate_seeds() -> List[List[Tuple[float, float, float]]]:
    """Create diversified seed set and apply MTV separation for disjointness (fallback to tiny radial inflation)."""
    seeds: List[List[Tuple[float, float, float]]] = []
    # Honeycomb families rows with angles
    row_patterns = [(4, 3, 4), (3, 4, 4), (4, 4, 3)]
    angles = [30.0, 90.0]
    for rows in row_patterns:
        for ang in angles:
            base = honeycomb_seed(rows, t_scale=1.0, angle_deg=ang)
            seeds.append(radial_inflate_disjoint(base))
    # Axial ring: evaluate combinations of 4 second-ring selections from 6
    inds = [0, 1, 2, 3, 4, 5]
    subsets = []
    for i in range(6):
        for j in range(i + 1, 6):
            for k in range(j + 1, 6):
                for l in range(k + 1, 6):
                    subsets.append([inds[i], inds[j], inds[k], inds[l]])
    # Build angle variants
    axial_candidates: List[Tuple[List[Tuple[float, float, float]], float]] = []
    test_theta = 30.0
    for ang in angles:
        for sel in subsets:
            seed = axial_ring_seed(angle_deg=ang, t_scale=1.0, second_ring_indices=sel)
            # quick bound S via quick_upper_bound_t
            t_bound, _ = quick_upper_bound_t(seed, test_theta)
            S_bound = (2.0 / math.sqrt(3.0)) * t_bound
            axial_candidates.append((seed, S_bound))
    # Keep best few by bound
    axial_candidates.sort(key=lambda p: p[1])
    keep = min(10, len(axial_candidates))
    for i in range(keep):
        base = axial_candidates[i][0]
        seeds.append(radial_inflate_disjoint(base))
    return seeds


def shift_positive(inner: List[Tuple[float, float, float]], outer_C: Tuple[float, float]) -> Tuple[List[List[float]], List[float]]:
    """Shift all coordinates to x>0, y>0 while preserving geometry."""
    Cx, Cy = outer_C
    min_cx = min([x for (x, _, _) in inner] + [Cx])
    min_cy = min([y for (_, y, _) in inner] + [Cy])
    shift_x = 0.5 - min_cx if min_cx <= 0.5 else 0.0
    shift_y = 0.5 - min_cy if min_cy <= 0.5 else 0.0
    inner_shifted = [[x + shift_x, y + shift_y, ang] for (x, y, ang) in inner]
    outer_center_shifted = [Cx + shift_x, Cy + shift_y]
    return inner_shifted, outer_center_shifted


def verify_bisect_if_available(inner: List[List[float]], outer_center: List[float], S: float, theta: float) -> float:
    """Optional final bisection to minimize S using external verify_construction if available."""
    # Try to import verify_construction (if provided by environment)
    verify_fn = None
    try:
        from verify_construction import verify_construction as _vf  # hypothetical module
        verify_fn = _vf
    except Exception:
        try:
            # Sometimes it's available in main scope
            from __main__ import verify_construction as _vf2
            verify_fn = _vf2
        except Exception:
            verify_fn = None

    if verify_fn is None:
        # Fallback: add microscopic safety and return
        return S * (1.0 + 2e-7)

    # Bisection on S downwards until verify fails
    lo = 0.0
    hi = S
    best = S
    for _ in range(42):
        mid = 0.5 * (lo + hi)
        # Construct params for verify: inner data is [x, y, angle]
        if verify_fn(inner, outer_center, mid, theta):
            best = mid
            hi = mid
        else:
            lo = mid
        if hi - lo < 5e-9:
            break
    return best * (1.0 + 2e-7)


def find_best_construction():
    """Run HALO-TUCK+MTV across multiple seeds, refine, and select the best configuration."""
    seeds = generate_seeds()
    global_best = {
        "S": float("inf"),
        "theta": 0.0,
        "C": (0.0, 0.0),
        "inner": None,
    }

    for seed in seeds:
        # Outer fit and θ micro-sweep
        S0, theta0, C0 = best_outer_for_seed(seed)
        # Boundary-driven deterministic tucking refinement
        seed_ref, S_ref, C_ref = tucking_refinement(seed, theta0, C0, max_accepts=20)
        # Re-run micro-sweep around refined θ to capture slight angle improvements
        S1, theta1, C1 = micro_sweep_theta(seed_ref, theta0, span=0.6, step=0.02)
        # Post-fit polish
        S1p, theta1p, C1p = post_fit_center_polish(seed_ref, theta1, C1)
        # Keep the best per seed
        S_seed = min(S0, S_ref, S1, S1p)
        if S1p <= S_seed + 1e-12:
            S_seed = S1p
            theta_seed = theta1p
            C_seed = C1p
            inner_seed = seed_ref
        elif S1 <= S_seed + 1e-12:
            S_seed = S1
            theta_seed = theta1
            C_seed = C1
            inner_seed = seed_ref
        elif S_ref <= S_seed + 1e-12:
            S_seed = S_ref
            theta_seed = theta0
            C_seed = C_ref
            inner_seed = seed_ref
        else:
            S_seed = S0
            theta_seed = theta0
            C_seed = C0
            inner_seed = seed

        if S_seed < global_best["S"]:
            global_best["S"] = S_seed
            global_best["theta"] = theta_seed
            global_best["C"] = C_seed
            global_best["inner"] = inner_seed

    # Finalize, optional verify-bisection, then shift to positive quadrant
    inner_final = global_best["inner"]
    theta_final = global_best["theta"]
    Cx, Cy = global_best["C"]

    # Convert inner to list-of-lists for verify function signature
    inner_ll = [[x, y, ang] for (x, y, ang) in inner_final]
    outer_center_list = [Cx, Cy]
    S_bisected = verify_bisect_if_available(inner_ll, outer_center_list, global_best["S"], theta_final)

    inner_shifted, outer_center_shifted = shift_positive(inner_final, (Cx, Cy))
    return inner_shifted, outer_center_shifted, S_bisected, theta_final


def optimize_construct():
    """
    Return:
        inner_hexagons: list of [x, y, angle_degrees] for the 11 unit hexagons
        outer_center: [x, y] center of outer regular hexagon
        outer_side_length: side length of outer hexagon
        outer_angle_degrees: rotation angle of outer hexagon in degrees
    """
    inner, center, side, angle = find_best_construction()
    return inner, center, side, angle
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
"""Optimized packing for 11 unit hexagons inside a regular hexagon.

This implementation follows a hybrid approach:
- Starts from compact lattice seeds (4-3-4 honeycomb layouts) with different inner orientations.
- Ensures strict non-overlap using a SAT-based minimum-translation-vector (MTV) micro-repair with a tiny separation cushion.
- Computes a near-minimal enclosing regular hexagon via convex support functions:
  * Scans outer rotation φ over [0°, 60°) and, for each φ, analytically fits the center by equalizing opposite supports in least-squares sense.
  * Evaluates the maximal apothem A over the six outer facet normals and returns the minimal side length L = (2/√3) * A for that φ.
- Applies a small boundary-shaping loop: nudges and micro-rotates only the hexagons whose vertices are active on the current support directions, accepting moves that strictly reduce L while preserving disjointness.
- Selects the best configuration across seeds and adjustments, then translates everything to the positive quadrant.
- Finally, trims the outer side length by a verification-guided 1D bisection to remove residual slack, adding a tiny safety cushion.

The result is intended to surpass the 3.931 side-length plateau while remaining robust under the provided verifier.
"""

import json
import math
import random
from copy import deepcopy

# -----------------------------------------------------------------------------
# Local geometry utilities. These mirror the semantics of the provided helpers.
# We keep them consistent with the described verification utilities.
# -----------------------------------------------------------------------------

EPSILON = 1e-9

def hexagon_vertices(center_x, center_y, side_length, angle_degrees):
    vertices = []
    angle_radians = math.radians(angle_degrees)
    for i in range(6):
        angle = angle_radians + 2.0 * math.pi * i / 6.0
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
        # outward normal (per provided semantics)
        normal = normalize_vector((-edge[1], edge[0]))
        normals.append(normal)
    return normals

def project_polygon(vertices, axis):
    min_proj = float('inf')
    max_proj = float('-inf')
    for vertex in vertices:
        projection = vertex[0] * axis[0] + vertex[1] * axis[1]
        if projection < min_proj:
            min_proj = projection
        if projection > max_proj:
            max_proj = projection
    return min_proj, max_proj

def overlap_1d(min1, max1, min2, max2):
    # Overlap if intervals touch or intersect (with epsilon tolerance)
    return max1 >= min2 - EPSILON and max2 >= min1 - EPSILON

def polygons_intersect(vertices1, vertices2):
    # Separating Axis Theorem
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
    for i in range(len(hex_vertices)):
        p1 = hex_vertices[i]
        p2 = hex_vertices[(i + 1) % len(hex_vertices)]
        edge_vector = (p2[0] - p1[0], p2[1] - p1[1])
        point_vector = (point[0] - p1[0], point[1] - p1[1])
        cross_product = (edge_vector[0] * point_vector[1] - edge_vector[1] * point_vector[0])
        # Strictly inside or on boundary (per verify). We need inside-or-on tolerance.
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

# -----------------------------------------------------------------------------
# Packing/optimization helpers
# -----------------------------------------------------------------------------

def layout_to_params(inner_hex_data):
    # inner_hex_data elements: [x, y, angle_degrees], side length always 1
    return [(x, y, 1.0, ang) for (x, y, ang) in inner_hex_data]

def any_overlap(inner_hex_data):
    params = layout_to_params(inner_hex_data)
    n = len(params)
    for i in range(n):
        for j in range(i + 1, n):
            if not hexagons_are_disjoint(params[i], params[j]):
                return True
    return False

def all_disjoint(inner_hex_data):
    return not any_overlap(inner_hex_data)

def compute_mtv_for_pair(h1_params, h2_params, sep_eps=1e-7):
    # Returns translation vectors (dx1, dy1), (dx2, dy2) to separate the polygons minimally
    v1 = hexagon_vertices(*h1_params)
    v2 = hexagon_vertices(*h2_params)
    c1 = (h1_params[0], h1_params[1])
    c2 = (h2_params[0], h2_params[1])

    axes = get_normals(v1) + get_normals(v2)
    min_overlap = float('inf')
    best_axis = (0.0, 0.0)
    # Compute the minimum overlap along candidate axes
    for axis in axes:
        min1, max1 = project_polygon(v1, axis)
        min2, max2 = project_polygon(v2, axis)
        # If they are separated along this axis, there's no intersection; but since caller
        # detected intersection, proceed; for touching, overlap ~ 0.
        overlap = min(max1, max2) - max(min1, min2)
        if overlap < min_overlap:
            min_overlap = overlap
            best_axis = axis

    # Ensure a tiny positive separation even for flush contact
    total_push = min_overlap + sep_eps
    if total_push < sep_eps * 0.5:
        total_push = sep_eps

    # Direction from c1 to c2 projected onto best_axis determines push direction
    diff = (c2[0] - c1[0], c2[1] - c1[1])
    sign = 1.0 if (best_axis[0] * diff[0] + best_axis[1] * diff[1]) >= 0.0 else -1.0
    push_vec = (best_axis[0] * sign * total_push, best_axis[1] * sign * total_push)
    # Split equally
    return (-0.5 * push_vec[0], -0.5 * push_vec[1]), (0.5 * push_vec[0], 0.5 * push_vec[1])

def micro_repair_disjoint(inner_hex_data, max_iters=200, sep_eps=1e-7):
    # Iteratively separate any intersecting pairs by MTV
    n = len(inner_hex_data)
    for _ in range(max_iters):
        moved = False
        params = layout_to_params(inner_hex_data)
        for i in range(n):
            for j in range(i + 1, n):
                h1 = params[i]
                h2 = params[j]
                v1 = hexagon_vertices(*h1)
                v2 = hexagon_vertices(*h2)
                if polygons_intersect(v1, v2):
                    d1, d2 = compute_mtv_for_pair(h1, h2, sep_eps=sep_eps)
                    inner_hex_data[i][0] += d1[0]
                    inner_hex_data[i][1] += d1[1]
                    inner_hex_data[j][0] += d2[0]
                    inner_hex_data[j][1] += d2[1]
                    moved = True
        if not moved:
            break
    return inner_hex_data

def all_vertices(inner_hex_data):
    verts = []
    for (x, y, ang) in inner_hex_data:
        verts.extend(hexagon_vertices(x, y, 1.0, ang))
    return verts

def outer_normals(angle_deg):
    # Six unit normals spaced by 60°, rotated by angle_deg
    a0 = math.radians(angle_deg)
    normals = []
    for k in range(6):
        ang = a0 + (math.pi / 3.0) * k
        normals.append((math.cos(ang), math.sin(ang)))
    return normals

def support_along(norm, vertices):
    # Max dot product over all vertices
    nx, ny = norm
    m = float('-inf')
    for (x, y) in vertices:
        d = nx * x + ny * y
        if d > m:
            m = d
    return m

def best_center_for_phi(vertices, phi_deg):
    # Equalize opposite supports in least squares sense:
    # For base normals i=0,1,2 with n_{i+3} = -n_i, impose n_i·C = (s_i - s_{i+3})/2.
    norms = outer_normals(phi_deg)
    s = [support_along(n, vertices) for n in norms]
    A = []  # rows are n_i for i=0..2
    b = []
    for i in range(3):
        ni = norms[i]
        # s_{i+3} = support along -ni (computed separately)
        bi = 0.5 * (s[i] - s[i + 3])
        A.append(ni)
        b.append(bi)
    # Solve least squares for C in R^2: minimize ||A C - b||^2
    # Normal equations: (A^T A) C = A^T b
    a00 = sum(A[i][0] * A[i][0] for i in range(3))
    a01 = sum(A[i][0] * A[i][1] for i in range(3))
    a11 = sum(A[i][1] * A[i][1] for i in range(3))
    bt0 = sum(A[i][0] * b[i] for i in range(3))
    bt1 = sum(A[i][1] * b[i] for i in range(3))
    det = a00 * a11 - a01 * a01
    if abs(det) < 1e-15:
        # Degenerate, fallback to origin
        return (0.0, 0.0)
    inv00 = a11 / det
    inv01 = -a01 / det
    inv11 = a00 / det
    cx = inv00 * bt0 + inv01 * bt1
    cy = inv01 * bt0 + inv11 * bt1
    return (cx, cy)

def apothem_and_active(vertices, center, phi_deg):
    # For given center and outer rotation phi, compute:
    # - required apothem A = max_k (s_k - n_k·C)
    # - active indices achieving this max
    norms = outer_normals(phi_deg)
    cx, cy = center
    Ak = []
    for k in range(6):
        sk = support_along(norms[k], vertices)
        ak = sk - (norms[k][0] * cx + norms[k][1] * cy)
        Ak.append(ak)
    A = max(Ak)
    active = [k for k, val in enumerate(Ak) if A - val <= 1e-9]
    return A, active, Ak

def minimal_outer_for_layout(inner_hex_data):
    # Returns (center (tuple), side_length, outer_angle_deg, details)
    verts = all_vertices(inner_hex_data)
    # Coarse scan over phi in [0, 60)
    best = None
    best_detail = None
    for step in [0.5, 0.1, 0.02]:
        # Build scan set depending on step refinement
        if best is None:
            phi_candidates = [i * step for i in range(int(60.0 / step))]
        else:
            phi0 = best[2]
            lo = max(0.0, phi0 - 1.0)
            hi = min(60.0, phi0 + 1.0)
            cnt = int((hi - lo) / step) + 1
            phi_candidates = [lo + i * step for i in range(cnt)]
        # Evaluate each candidate
        curr_best = best
        curr_detail = best_detail
        for phi in phi_candidates:
            C = best_center_for_phi(verts, phi)
            A, active, Ak = apothem_and_active(verts, C, phi)
            # Side length L relates to apothem by A = L * sqrt(3) / 2
            L = (2.0 / math.sqrt(3.0)) * A
            if (curr_best is None) or (L < curr_best[1] - 1e-12):
                curr_best = (C, L, phi)
                curr_detail = (A, active, Ak)
        best = curr_best
        best_detail = curr_detail
    return best[0], best[1], best[2], best_detail

def nudge_hexagon(layout, idx, direction, amount):
    # Shift one hex center by amount * direction, return new layout
    nx, ny = direction
    new_layout = deepcopy(layout)
    new_layout[idx][0] += nx * amount
    new_layout[idx][1] += ny * amount
    return new_layout

def rotate_hexagon(layout, idx, delta_deg):
    new_layout = deepcopy(layout)
    new_layout[idx][2] += delta_deg
    return new_layout

def active_hex_indices_and_dirs(inner_hex_data, center, phi_deg, active_ks):
    # Identify which hexagons have the vertices realizing the active supports,
    # and return a set of (hex_index, normal_direction) pairs for nudging.
    norms = outer_normals(phi_deg)
    verts_by_hex = []
    for (x, y, ang) in inner_hex_data:
        verts_by_hex.append(hexagon_vertices(x, y, 1.0, ang))
    # For each active normal k, find the hex and vertex that maximizes dot(nk, v)
    contributors = []
    for k in active_ks:
        nk = norms[k]
        best = None
        best_hex = None
        for hi, verts in enumerate(verts_by_hex):
            for v in verts:
                s = nk[0] * v[0] + nk[1] * v[1]
                if (best is None) or (s > best):
                    best = s
                    best_hex = hi
        if best_hex is not None:
            contributors.append((best_hex, nk))
    # Deduplicate by hex index, average normals if multiple
    by_hex = {}
    for hi, nk in contributors:
        if hi not in by_hex:
            by_hex[hi] = [nk]
        else:
            by_hex[hi].append(nk)
    result = []
    for hi, arr in by_hex.items():
        # Average direction
        sx = sum(n[0] for n in arr)
        sy = sum(n[1] for n in arr)
        n = normalize_vector((sx, sy))
        result.append((hi, n))
    return result

def try_boundary_shaping(inner_hex_data, max_outer_improve_iters=40):
    # Perform a limited number of nudges/rotations on active contributors to reduce L.
    layout = deepcopy(inner_hex_data)
    # Initial disjoint repair
    layout = micro_repair_disjoint(layout, max_iters=150, sep_eps=1e-7)
    C, L, phi, detail = minimal_outer_for_layout(layout)
    best = (layout, C, L, phi)
    A, active, Ak = detail

    for _ in range(max_outer_improve_iters):
        improved = False
        # Identify active contributors at current boundary
        contributors = active_hex_indices_and_dirs(layout, C, phi, active)
        # Try inward nudges for each contributor
        for (hi, nk) in contributors:
            # Try multiple step sizes with backtracking
            for step in [0.04, 0.02, 0.01, 0.006, 0.003]:
                candidate = nudge_hexagon(layout, hi, (-nk[0], -nk[1]), step)
                candidate = micro_repair_disjoint(candidate, max_iters=80, sep_eps=1e-7)
                C2, L2, phi2, detail2 = minimal_outer_for_layout(candidate)
                if L2 < best[2] - 1e-5 and all_disjoint(candidate):
                    layout = candidate
                    C, L, phi = C2, L2, phi2
                    A, active, Ak = detail2
                    best = (layout, C, L, phi)
                    improved = True
                    break
            if improved:
                break
        if improved:
            continue
        # Try micro-rotations on most frequent contributors
        for (hi, nk) in contributors:
            for delta in [0.25, -0.25, 0.15, -0.15, 0.08, -0.08]:
                candidate = rotate_hexagon(layout, hi, delta)
                candidate = micro_repair_disjoint(candidate, max_iters=60, sep_eps=1e-7)
                C2, L2, phi2, detail2 = minimal_outer_for_layout(candidate)
                if L2 < best[2] - 1e-5 and all_disjoint(candidate):
                    layout = candidate
                    C, L, phi = C2, L2, phi2
                    A, active, Ak = detail2
                    best = (layout, C, L, phi)
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    return best

def honeycomb_seed_434(angle_deg=0.0, scale=1.0):
    # pointy-top lattice spacing: horizontal = sqrt(3), vertical = 1.5
    hx = math.sqrt(3.0) * scale
    hy = 1.5 * scale
    rows = [4, 3, 4]
    yoffs = [-(len(rows) - 1) * 0.5 * hy + i * hy for i in range(len(rows))]
    inner = []
    for ri, cnt in enumerate(rows):
        if cnt % 2 == 0:
            # even count: symmetric positions around 0 at offsets (-(cnt/2 - 0.5), ..., +(cnt/2 - 0.5)) * hx
            start = -(cnt / 2.0 - 0.5) * hx
            xs = [start + j * hx for j in range(cnt)]
        else:
            # odd count: symmetric positions around 0 at offsets (-(cnt//2), ..., +(cnt//2)) * hx
            start = - (cnt // 2) * hx
            xs = [start + j * hx for j in range(cnt)]
        for x in xs:
            inner.append([x, yoffs[ri], float(angle_deg)])
    return inner

def translate_to_positive(inner_hex_data, center):
    # Shift all coordinates so that all inner centers and vertices are in x>0, y>0,
    # and the outer center as well. Add a small margin.
    margin = 0.5
    # Compute mins over all vertices and the center
    verts = all_vertices(inner_hex_data)
    minx = min(v[0] for v in verts)
    miny = min(v[1] for v in verts)
    minx = min(minx, center[0])
    miny = min(miny, center[1])
    dx = -minx + margin if minx < margin else 0.0
    dy = -miny + margin if miny < margin else 0.0
    shifted = []
    for (x, y, ang) in inner_hex_data:
        shifted.append([x + dx, y + dy, ang])
    return shifted, [center[0] + dx, center[1] + dy]

# -----------------------------------------------------------------------------
# Main optimizer
# -----------------------------------------------------------------------------

# EVOLVE_START
def optimize_construct():
    random.seed(0xC0FFEE)

    # Helper: verify containment for a given outer container using our utilities.
    def verify_containment(inner_hex_data, outer_center, outer_side, outer_angle_deg):
        inner_params_list = [(x, y, 1.0, ang) for (x, y, ang) in inner_hex_data]
        outer_params = (outer_center[0], outer_center[1], outer_side, outer_angle_deg)
        return all_hexagons_contained(inner_params_list, outer_params)

    # Helper: trim the outer side length by bisection while keeping center/angle fixed.
    def bisection_trim_side(inner_hex_data, outer_center, side_initial, outer_angle_deg):
        # Ensure the initial side length is valid
        assert verify_containment(inner_hex_data, outer_center, side_initial, outer_angle_deg)
        lo = max(side_initial - 0.01, 0.1)
        hi = side_initial
        # If lower bound already invalid, start shrinking from hi
        if verify_containment(inner_hex_data, outer_center, lo, outer_angle_deg):
            # Expand search lower if still valid, maintain robustness
            lo2 = max(lo - 0.02, 0.1)
            if verify_containment(inner_hex_data, outer_center, lo2, outer_angle_deg):
                lo = lo2
        # Standard bisection to find minimal valid side length
        for _ in range(50):
            mid = 0.5 * (lo + hi)
            if verify_containment(inner_hex_data, outer_center, mid, outer_angle_deg):
                hi = mid
            else:
                lo = mid
        # Add a tiny cushion to protect against floating-point roundoff in external verify
        return hi + 1e-6

    seeds = []
    # Honeycomb 4-3-4 seeds with slight scale to prevent exact edge contact
    for ang in [0.0, 30.0]:
        for scl in [1.0000, 1.0010]:
            seeds.append(honeycomb_seed_434(angle_deg=ang, scale=scl))

    best_global = None  # (inner_layout, outer_center, outer_side_length, outer_angle_deg)

    for seed in seeds:
        # Enforce strict disjointness with tiny gaps using SAT-based MTV separation
        layout = micro_repair_disjoint(deepcopy(seed), max_iters=200, sep_eps=1e-7)
        # Compute minimal outer for this layout
        C, L, phi, _ = minimal_outer_for_layout(layout)

        # Boundary shaping loop to reduce L further
        layout2, C2, L2, phi2 = try_boundary_shaping(layout, max_outer_improve_iters=40)
        if L2 < L:
            layout, C, L, phi = layout2, C2, L2, phi2

        # Track best across seeds
        if (best_global is None) or (L < best_global[2] - 1e-12):
            best_global = (deepcopy(layout), (C[0], C[1]), L, phi)

    if best_global is None:
        # Fallback (should not happen)
        layout = honeycomb_seed_434(angle_deg=0.0, scale=1.001)
        layout = micro_repair_disjoint(layout, max_iters=200, sep_eps=1e-7)
        C, L, phi, _ = minimal_outer_for_layout(layout)
        best_global = (layout, C, L, phi)

    # Translate to positive quadrant
    layout, C = translate_to_positive(best_global[0], best_global[1])
    L = best_global[2]
    phi_normals = best_global[3]

    # Convert normals-angle to vertices-angle by subtracting 30 degrees
    phi_vertices = (phi_normals - 30.0) % 360.0

    # Verification-guided bisection trim of the side length, keeping center and angle fixed
    # Ensure that the configuration is disjoint before trimming
    layout = micro_repair_disjoint(deepcopy(layout), max_iters=60, sep_eps=1e-7)
    L_out = bisection_trim_side(layout, C, L, phi_vertices)

    inner_hexagons = [[x, y, ang] for (x, y, ang) in layout]
    return inner_hexagons, [C[0], C[1]], float(L_out), float(phi_vertices)
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
                return bool(verifier(data, list(outer_center), float(side),
                                     float(outer_angle)))
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

    # The LP result carries a fixed 2e-5 padding.  With the configuration
    # fixed, containment is monotone in the outer side, so remove that
    # unused slack by bisection against the actual verifier.
    padded_side = float(side)
    lp_side = max(0.0, padded_side - 2.0e-5)
    lower = max(0.0, lp_side - 2.0e-6)
    feasible_side = padded_side

    for _ in range(30):
        midpoint = 0.5 * (lower + feasible_side)
        if verifier_valid(centers, angles, outer_center, midpoint,
                          outer_angle):
            feasible_side = midpoint
        else:
            lower = midpoint

    # Keep a small positive cushion for independent floating-point
    # evaluation by the adapter's verifier.
    trimmed_side = feasible_side + 3.0e-7
    if verifier_valid(centers, angles, outer_center, trimmed_side,
                      outer_angle):
        side = trimmed_side
    else:
        side = feasible_side

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
